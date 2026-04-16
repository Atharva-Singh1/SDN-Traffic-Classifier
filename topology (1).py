#!/usr/bin/env python3
"""
Mininet Topology – POX SDN Demo
================================
Creates a tree topology: 3 switches + 4 hosts.
Connects to the POX controller on localhost:6633.

         Controller (POX)
              |
           [s1]  ← core switch
          /    \\
       [s2]   [s3]
       / \\     / \\
     h1  h2  h3  h4

Run:
    sudo python3 topology.py
"""

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.topo import Topo
from mininet.log import setLogLevel, info
from mininet.cli import CLI
from mininet.link import TCLink
import time


class SDNTreeTopo(Topo):
    """3-switch, 4-host tree topology for SDN demo."""

    def build(self, **opts):
        # Switches  – OpenFlow 1.0 for POX compatibility
        s1 = self.addSwitch("s1", protocols="OpenFlow10")
        s2 = self.addSwitch("s2", protocols="OpenFlow10")
        s3 = self.addSwitch("s3", protocols="OpenFlow10")

        # Hosts
        h1 = self.addHost("h1", ip="10.0.0.1/24", mac="00:00:00:00:00:01")
        h2 = self.addHost("h2", ip="10.0.0.2/24", mac="00:00:00:00:00:02")
        h3 = self.addHost("h3", ip="10.0.0.3/24", mac="00:00:00:00:00:03")
        h4 = self.addHost("h4", ip="10.0.0.4/24", mac="00:00:00:00:00:04")

        # Inter-switch links
        self.addLink(s1, s2, bw=100, delay="5ms")
        self.addLink(s1, s3, bw=100, delay="5ms")

        # Host links
        self.addLink(s2, h1, bw=10, delay="1ms")
        self.addLink(s2, h2, bw=10, delay="1ms")
        self.addLink(s3, h3, bw=10, delay="1ms")
        self.addLink(s3, h4, bw=10, delay="1ms")


def run_demo():
    setLogLevel("info")

    topo = SDNTreeTopo()
    net  = Mininet(
        topo       = topo,
        controller = None,
        switch     = OVSSwitch,
        link       = TCLink,
        autoSetMacs= False
    )

    # Point to POX controller (default port 6633)
    c0 = net.addController(
        "c0",
        controller = RemoteController,
        ip         = "127.0.0.1",
        port       = 6633
    )

    net.start()
    info("\n*** Network started – waiting for controller handshake...\n")
    time.sleep(3)

    # ── TEST 1: Full connectivity ─────────────────────────────────────────── #
    info("\n" + "=" * 55)
    info("\n*** TEST 1: pingAll (all-pairs connectivity)\n")
    net.pingAll()

    # ── TEST 2: Directed ping ─────────────────────────────────────────────── #
    info("\n*** TEST 2: h1 → h4 (cross-switch ping)\n")
    h1, h4 = net.get("h1", "h4")
    info(h1.cmd("ping -c 4 10.0.0.4"))

    # ── TEST 3: Bandwidth ─────────────────────────────────────────────────── #
    info("\n*** TEST 3: iperf h1 → h3\n")
    h1, h3 = net.get("h1", "h3")
    net.iperf((h1, h3))

    # ── TEST 4: Dump flow tables ──────────────────────────────────────────── #
    info("\n*** TEST 4: Flow table dump (OpenFlow 1.0)\n")
    for sw_name in ["s1", "s2", "s3"]:
        info(f"\n--- {sw_name} ---\n")
        sw = net.get(sw_name)
        info(sw.cmd("ovs-ofctl dump-flows " + sw_name))

    # ── TEST 5: Link failure + recovery ───────────────────────────────────── #
    info("\n*** TEST 5: Link failure simulation (s1 ↔ s2)\n")
    net.configLinkStatus("s1", "s2", "down")
    info("Link s1-s2 DOWN → check controller log for LINK_DELETE event\n")
    time.sleep(2)

    info("Restoring link s1-s2...\n")
    net.configLinkStatus("s1", "s2", "up")
    time.sleep(2)
    info("Link restored → check controller log for LINK_ADD event\n")

    info("\n*** Post-recovery pingAll\n")
    net.pingAll()

    info("\n*** All automated tests complete. Entering CLI...\n")
    info("Useful CLI commands:\n")
    info("  mininet> pingall\n")
    info("  mininet> h1 ping h4 -c3\n")
    info("  mininet> sh ovs-ofctl dump-flows s1\n")
    info("  mininet> link s1 s2 down\n")
    info("  mininet> link s1 s2 up\n")
    CLI(net)
    net.stop()


if __name__ == "__main__":
    run_demo()
