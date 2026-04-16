# SDN Controller – Mininet + POX (OpenFlow 1.0)

> **Course Assignment** | SDN solution demonstrating controller–switch interaction, flow rule design, topology change detection, and network behavior — implemented with **POX** controller and Mininet.

---

## Problem Statement

Traditional networks rely on distributed per-device control planes that are rigid and hard to manage. Software-Defined Networking (SDN) separates the **control plane** from the **data plane**, centralising intelligence in a controller.

This project implements an SDN solution using **Mininet** and **POX** (OpenFlow 1.0) that:

- Performs **MAC learning** and installs **proactive flow rules** on switches
- Detects **topology changes** (switch connect/disconnect, link add/delete) in real time
- Maintains a live **topology map** and logs all changes to disk
- Verifies **functional correctness** through automated ping, iperf, and link-failure tests

---

## Architecture

```
         ┌──────────────────────────┐
         │      POX Controller      │  ← sdn_controller.py  (port 6633)
         │   TopologyChangeDetector │
         └──────────┬───────────────┘
                    │  OpenFlow 1.0
           ┌────────▼────────┐
           │   s1 (OVS)      │  core switch
           └───┬─────────┬───┘
               │         │
        ┌──────▼──┐  ┌───▼─────┐
        │   s2    │  │   s3    │
        └──┬───┬──┘  └──┬───┬──┘
           │   │         │   │
          h1  h2        h3  h4
     10.0.0.1 .2       .3  .4
```

**Flow Rule Design:**

| Priority | Match Fields | Action | Purpose |
|----------|-------------|--------|---------|
| 0 | `*` (wildcard all) | Send to Controller | Table-miss / catch-all |
| 1 | `in_port + dl_src + dl_dst` | Output(port) | MAC-learned unicast |

---

## File Structure

```
sdn_pox/
├── sdn_controller.py   # POX component – MAC learning + Topology Change Detector
├── topology.py         # Mininet topology + automated tests
└── README.md           # This file
```

---

## Setup / Execution Steps

### Prerequisites

```bash
# 1. Install Mininet (includes OVS)
sudo apt-get update
sudo apt-get install -y mininet

# 2. Install POX
git clone https://github.com/noxrepo/pox.git ~/pox

# 3. Copy the controller into POX's ext/ folder
cp sdn_controller.py ~/pox/ext/

# 4. (Optional) Wireshark for packet capture
sudo apt-get install -y wireshark tshark
```

### Step 1 – Start the POX Controller

Open **Terminal 1**:

```bash
cd ~/pox
python pox.py log.level --DEBUG openflow.discovery sdn_controller
```

Expected output:
```
INFO:openflow.of_01:[Con 1] Connected to 00-00-00-00-00-01
INFO:SDNController:[SWITCH CONNECTED] DPID=00-00-00-00-00-01
INFO:SDNController:[FLOW RULE] Table-miss entry installed on DPID=00-00-00-00-00-01
INFO:SDNController:[TOPOLOGY CHANGE] {"event": "SWITCH_ENTER", "dpid": "00-00-00-00-00-01", ...}
```

### Step 2 – Start the Mininet Topology

Open **Terminal 2**:

```bash
sudo python3 topology.py
```

This will:
1. Create switches s1, s2, s3 and hosts h1–h4
2. Connect all switches to the POX controller at `127.0.0.1:6633`
3. Run automated tests (pingAll, directed ping, iperf, flow table dump, link failure)
4. Drop into the Mininet CLI

### Step 3 – Manual CLI Commands

```bash
mininet> pingall                             # All-pairs connectivity test
mininet> h1 ping h4 -c 5                    # Directed ping h1 → h4
mininet> iperf h1 h3                         # Bandwidth test
mininet> sh ovs-ofctl dump-flows s1          # View flow table on s1
mininet> sh ovs-ofctl dump-flows s2          # View flow table on s2
mininet> link s1 s2 down                     # Simulate link failure
mininet> link s1 s2 up                       # Restore link
mininet> exit                                # Quit Mininet
```

---

## Expected Output

### pingAll (0% packet loss)

```
h1 -> h2 h3 h4
h2 -> h1 h3 h4
h3 -> h1 h2 h4
h4 -> h1 h2 h3
*** Results: 0% dropped (12/12 received)
```

### Flow Table (ovs-ofctl dump-flows s2)

```
OFPST_FLOW reply:
 cookie=0x0, priority=0, actions=CONTROLLER:65535
 cookie=0x0, priority=1, in_port=1,dl_src=00:00:00:00:00:01,
   dl_dst=00:00:00:00:00:02, actions=output:2
 cookie=0x0, priority=1, in_port=2,dl_src=00:00:00:00:00:02,
   dl_dst=00:00:00:00:00:01, actions=output:1
```

### Controller log – Topology events

```
[TOPOLOGY CHANGE] {"event": "SWITCH_ENTER", "dpid": "00-00-00-00-00-01", "ports": [1,2]}
[TOPOLOGY MAP] Current State:
  Switch DPID=00-00-00-00-00-01
    Ports     : [1, 2]
    Neighbors : ['00-00-00-00-00-02', '00-00-00-00-00-03']
  Total Links : 2
    00-00-00-00-00-01:1 ↔ 00-00-00-00-00-02:3
    00-00-00-00-00-01:2 ↔ 00-00-00-00-00-03:3

[TOPOLOGY CHANGE] {"event": "LINK_DELETE", "src_dpid": "00-00-00-00-00-01", ...}
```

---

## Proof of Execution

### Capture OpenFlow messages with tshark

```bash
sudo tshark -i lo -f "tcp port 6633" -w openflow_capture.pcap
# Open in Wireshark and filter: openflow_v1
```

### View log files

```bash
cat /tmp/sdn_events.log              # All controller events
cat /tmp/topology_changes.json       # Topology change history (JSON)
```

### Screenshot checklist for submission

- [ ] `ovs-ofctl dump-flows s1/s2/s3` output (flow tables)
- [ ] Wireshark showing PacketIn / FlowMod messages
- [ ] `pingall` output showing 0% packet loss
- [ ] Controller terminal showing topology change events
- [ ] Link failure + recovery log entries

---

## Key Concepts Demonstrated

| Concept | Location in Code |
|---------|-----------------|
| Controller–Switch handshake | `SwitchHandler.__init__` → `_install_table_miss` |
| Table-miss flow entry (priority 0) | `_install_table_miss()` |
| MAC learning | `_handle_PacketIn` |
| Proactive flow rule installation | `ofp_flow_mod` with `priority=1` |
| Switch connect event | `_handle_ConnectionUp` |
| Switch disconnect event | `_handle_ConnectionDown` |
| Link add/delete detection | `_handle_LinkEvent` |
| Live topology map | `_display_topology()` |
| Persistent event log | `/tmp/sdn_events.log`, `/tmp/topology_changes.json` |
