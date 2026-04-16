"""
SDN Controller – POX (OpenFlow 1.0)
=====================================
File:  sdn_controller.py
Place: pox/ext/sdn_controller.py

Features:
  - Layer-2 MAC learning switch
  - Dynamic flow rule installation
  - Topology Change Detector  (switch connect/disconnect + link events)
  - Live topology map display
  - Full event logging to /tmp/sdn_events.log
  - Topology snapshot to /tmp/topology_changes.json

Run:
    cd ~/pox
    python pox.py log.level --DEBUG openflow.discovery sdn_controller
"""

from pox.core import core
from pox.lib.util import dpid_to_str, str_to_dpid
from pox.lib.revent import EventMixin
import pox.openflow.libopenflow_01 as of
from pox.lib.packet import ethernet, arp, ipv4
from pox.openflow.discovery import Discovery
import pox.openflow.discovery

import logging
import datetime
import json
import os

# ── Logging setup ──────────────────────────────────────────────────────────── #
LOG_FILE = "/tmp/sdn_events.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("SDNController")

log = core.getLogger()   # POX native logger (also shown in terminal)


# ═══════════════════════════════════════════════════════════════════════════════
#  Per-switch handler
# ═══════════════════════════════════════════════════════════════════════════════
class SwitchHandler(object):
    """
    Attached to each connected switch (one instance per datapath).
    Handles PacketIn events, MAC learning, and flow rule installation.
    """

    def __init__(self, connection, controller):
        self.connection = connection
        self.dpid = connection.dpid
        self.controller = controller
        # MAC → port table for this switch
        self.mac_to_port = {}

        # Listen to events on this connection
        connection.addListeners(self)

        logger.info("=" * 60)
        logger.info(f"[SWITCH CONNECTED] DPID={dpid_to_str(self.dpid)}")
        logger.info("=" * 60)

        # Install table-miss: send all unmatched packets to controller
        self._install_table_miss()

    # ── Table-miss entry ──────────────────────────────────────────────────── #
    def _install_table_miss(self):
        msg = of.ofp_flow_mod()
        msg.priority = 0                        # lowest priority = catch-all
        msg.match = of.ofp_match()              # wildcard everything
        msg.actions.append(of.ofp_action_output(port=of.OFPP_CONTROLLER))
        self.connection.send(msg)
        logger.info(f"[FLOW RULE] Table-miss entry installed on DPID={dpid_to_str(self.dpid)}")

    # ── PacketIn handler ──────────────────────────────────────────────────── #
    def _handle_PacketIn(self, event):
        packet_data = event.parsed        # parsed Ethernet frame
        if not packet_data.parsed:
            log.warning("Ignoring incomplete packet")
            return

        in_port = event.port
        dpid    = self.dpid
        src_mac = str(packet_data.src)
        dst_mac = str(packet_data.dst)

        # ── MAC learning ─────────────────────────────────────────────────── #
        if src_mac not in self.mac_to_port:
            logger.info(f"[MAC LEARN] DPID={dpid_to_str(dpid)}  {src_mac} → port {in_port}")
        self.mac_to_port[src_mac] = in_port

        # ── Decide output port ───────────────────────────────────────────── #
        if dst_mac in self.mac_to_port:
            out_port = self.mac_to_port[dst_mac]
        else:
            out_port = of.OFPP_FLOOD          # unknown destination → flood

        # ── Install proactive flow rule (unicast only) ────────────────────── #
        if out_port != of.OFPP_FLOOD:
            msg = of.ofp_flow_mod()
            msg.priority   = 1
            msg.idle_timeout  = 30            # remove after 30 s of silence
            msg.hard_timeout  = 120           # remove after 2 min regardless
            msg.match.in_port = in_port
            msg.match.dl_src  = packet_data.src
            msg.match.dl_dst  = packet_data.dst
            msg.actions.append(of.ofp_action_output(port=out_port))
            # If buffer_id valid, let switch send buffered packet
            if event.ofp.buffer_id != of.NO_BUFFER:
                msg.buffer_id = event.ofp.buffer_id
                self.connection.send(msg)
                logger.info(
                    f"[FLOW RULE] Installed: DPID={dpid_to_str(dpid)} "
                    f"{src_mac}→{dst_mac} in={in_port} out={out_port}"
                )
                return          # switch handles forwarding via buffer
            else:
                self.connection.send(msg)
                logger.info(
                    f"[FLOW RULE] Installed: DPID={dpid_to_str(dpid)} "
                    f"{src_mac}→{dst_mac} in={in_port} out={out_port}"
                )

        # ── Send PacketOut ────────────────────────────────────────────────── #
        msg = of.ofp_packet_out()
        msg.actions.append(of.ofp_action_output(port=out_port))
        msg.data       = event.ofp          # re-use buffered or raw data
        msg.in_port    = in_port
        self.connection.send(msg)


# ═══════════════════════════════════════════════════════════════════════════════
#  Main controller component
# ═══════════════════════════════════════════════════════════════════════════════
class TopologyChangeDetector(object):
    """
    POX component that:
      1. Spawns a SwitchHandler for every connecting switch
      2. Detects topology changes via openflow.discovery events
      3. Maintains & displays a live topology map
      4. Logs all events to disk
    """

    def __init__(self):
        # topology_map: {dpid_str: {'ports': [...], 'neighbors': [...]}}
        self.topology_map  = {}
        # link_map: {(src_dpid, dst_dpid): (src_port, dst_port)}
        self.link_map      = {}
        # Active SwitchHandler objects: {dpid: SwitchHandler}
        self.switch_handlers = {}

        logger.info("=" * 60)
        logger.info("TopologyChangeDetector initialised")
        logger.info("=" * 60)

        # Listen for new switch connections
        core.openflow.addListeners(self)

        # Listen for topology discovery events (requires openflow.discovery)
        if core.hasComponent("openflow_discovery"):
            core.openflow_discovery.addListeners(self)
        else:
            # Discovery may register slightly later; use a delayed hook
            core.call_when_ready(self._attach_discovery, ["openflow_discovery"])

    def _attach_discovery(self):
        core.openflow_discovery.addListeners(self)
        logger.info("[TOPOLOGY] Discovery component attached")

    # ── Switch connect / disconnect ───────────────────────────────────────── #
    def _handle_ConnectionUp(self, event):
        dpid = event.dpid
        dpid_s = dpid_to_str(dpid)
        ports  = [p.port_no for p in event.connection.features.ports
                  if p.port_no < of.OFPP_MAX]

        self.switch_handlers[dpid] = SwitchHandler(event.connection, self)
        self._init_topo_entry(dpid_s, ports)
        self._log_topology_change("SWITCH_ENTER", dpid=dpid_s, ports=ports)
        self._display_topology()

    def _handle_ConnectionDown(self, event):
        dpid   = event.dpid
        dpid_s = dpid_to_str(dpid)

        self.switch_handlers.pop(dpid, None)
        self.topology_map.pop(dpid_s, None)
        # Remove any links that referenced this switch
        stale = [k for k in self.link_map if k[0] == dpid_s or k[1] == dpid_s]
        for k in stale:
            del self.link_map[k]

        self._log_topology_change("SWITCH_LEAVE", dpid=dpid_s)
        logger.info(f"[SWITCH DISCONNECTED] DPID={dpid_s}")
        self._display_topology()

    # ── Link discovery events (from openflow.discovery) ──────────────────── #
    def _handle_LinkEvent(self, event):
        link      = event.link
        src_dpid  = dpid_to_str(link.dpid1)
        src_port  = link.port1
        dst_dpid  = dpid_to_str(link.dpid2)
        dst_port  = link.port2
        key       = (src_dpid, dst_dpid)

        if event.added:
            self.link_map[key] = (src_port, dst_port)
            # Ensure both nodes exist in topology map
            self._init_topo_entry(src_dpid)
            self._init_topo_entry(dst_dpid)
            # Update neighbor list
            nbr = self.topology_map[src_dpid].setdefault("neighbors", [])
            if dst_dpid not in nbr:
                nbr.append(dst_dpid)

            self._log_topology_change(
                "LINK_ADD",
                src_dpid=src_dpid, src_port=src_port,
                dst_dpid=dst_dpid, dst_port=dst_port
            )
            logger.info(f"[LINK ADD] {src_dpid}:{src_port} ↔ {dst_dpid}:{dst_port}")

        elif event.removed:
            self.link_map.pop(key, None)
            if src_dpid in self.topology_map:
                nbr = self.topology_map[src_dpid].get("neighbors", [])
                if dst_dpid in nbr:
                    nbr.remove(dst_dpid)

            self._log_topology_change(
                "LINK_DELETE",
                src_dpid=src_dpid, dst_dpid=dst_dpid
            )
            logger.info(f"[LINK DELETE] {src_dpid} ↔ {dst_dpid}")

        self._display_topology()

    # ── Helpers ───────────────────────────────────────────────────────────── #
    def _init_topo_entry(self, dpid_s, ports=None):
        if dpid_s not in self.topology_map:
            self.topology_map[dpid_s] = {"ports": ports or [], "neighbors": []}
        elif ports:
            self.topology_map[dpid_s]["ports"] = ports

    def _log_topology_change(self, event_type, **kwargs):
        ts    = datetime.datetime.now().isoformat()
        entry = {"timestamp": ts, "event": event_type, **kwargs}
        logger.info(f"[TOPOLOGY CHANGE] {json.dumps(entry)}")

        log_path = "/tmp/topology_changes.json"
        changes  = []
        if os.path.exists(log_path):
            try:
                with open(log_path) as f:
                    changes = json.load(f)
            except Exception:
                pass
        changes.append(entry)
        with open(log_path, "w") as f:
            json.dump(changes, f, indent=2)

    def _display_topology(self):
        """Print current topology map to log/console."""
        logger.info("=" * 50)
        logger.info("[TOPOLOGY MAP] Current State:")
        for dpid_s, info in self.topology_map.items():
            logger.info(f"  Switch DPID={dpid_s}")
            logger.info(f"    Ports     : {info.get('ports', [])}")
            logger.info(f"    Neighbors : {info.get('neighbors', [])}")
        logger.info(f"  Total Links : {len(self.link_map)}")
        for (s, d), (sp, dp_) in self.link_map.items():
            logger.info(f"    {s}:{sp} ↔ {d}:{dp_}")
        logger.info("=" * 50)


# ═══════════════════════════════════════════════════════════════════════════════
#  POX entry point
# ═══════════════════════════════════════════════════════════════════════════════
def launch():
    """
    Called by POX when this module is loaded.
    Registers the TopologyChangeDetector component.
    """
    core.registerNew(TopologyChangeDetector)
    log.info("SDN Controller (POX) – TopologyChangeDetector registered")
