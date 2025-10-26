# SDN-Based Multipath Data Offloading: LTE-WiFi HetNet

Paper-accurate implementation of "SDN-Based Multipath Data Offloading Scheme Using Link Quality Prediction for LTE and WiFi Networks"

## Project Structure

```
project_root/
│
├── config/
│   ├── __init__.py
│   └── model_config.py
│
├── data/
│   ├── __init__.py
│   └── iot_lab_dataset.csv
│
├── docs/
│   ├── ARCHITECTURE.md
│   └── USAGE.md
│
├── results/
│   ├── wifi_logs.csv
│   └── lte_logs.csv
│
├── scripts/
│   ├── run_offloading.sh
│   ├── run_training.sh
│   └── start_network.sh
│
├── src/
│   ├── __init__.py
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── blstm_model.py
│   │   ├── lstm_model.py
│   │   └── trainer.py
│   │
│   ├── network/
│   │   ├── __init__.py
│   │   ├── mininet_topology.py
│   │   ├── ryu_controller_Lte.py
│   │   ├── ryu_controller_Wifi.py
│   │   └── traffic_offloading.py
│   │
│   ├── prediction/
│   │   ├── __init__.py
│   │   ├── channel_predictor.py
│   │   └── quality_classifier.py
│   │
│   ├── visualization/
│   │   ├── __init__.py
│   │   └── dashboard.py
│
├── README.md
├── requirements.txt
└── setup.py
```

## Prerequisites

### System Requirements

- Ubuntu 24.04 (or compatible)
- Python 3.9+
- sudo privileges

### Install Dependencies

```bash
# Install Mininet-WiFi
sudo apt update
sudo apt install -y git python3-pip
git clone https://github.com/intrig-unicamp/mininet-wifi
cd mininet-wifi
sudo util/install.sh -Wlnfv

# Install Ryu SDN Controller
sudo pip3 install ryu

# Install other Python dependencies
sudo pip3 install requests numpy

# Optional: TensorFlow for BLSTM model (if available)
sudo pip3 install tensorflow
```

## Running the Simulation

### Step 1: Start LTE Controller (Terminal 1)

```bash
source ~/ryu-env/bin/activate
export PYTHONPATH=~/project_root:$PYTHONPATH
cd ~/project_root/src/network
ryu-manager --ofp-tcp-listen-port 6653 --wsapi-port 8080 ryu_controller_Lte.py
```

**Expected output:**

```
================================================================================
  LTE CONTROLLER - ALGORITHM 1 IMPLEMENTATION
  Port: 6653 | REST API: 8080
================================================================================

[LTE] Monitoring loop started
```

### Step 2: Start WiFi Controller (Terminal 2)

```bash
source ~/ryu-env/bin/activate
export PYTHONPATH=~/project_root:$PYTHONPATH
cd ~/project_root/src/network
ryu-manager --ofp-tcp-listen-port 6654 --wsapi-port 8081 ryu_controller_Wifi.py
```

**Expected output:**

```
================================================================================
  WIFI CONTROLLER - OFFLOAD RESOURCE MANAGER
  Port: 6654 | REST API: 8081
================================================================================

[WiFi] ✅ WiFi network ready
```

### Step 3: Start Mininet Topology (Terminal 3)

```bash
cd ~/project_root/src/network
sudo python3 mininet_topology.py
```

**Expected output:**

```
================================================================================
TOPOLOGY OVERVIEW:
================================================================================
Server (h1): 10.0.0.100 - Video content source
LTE eNodeB: Position (20, 50, 0) - Range: 100m
WiFi AP: Position (90, 50, 0) - Range: 50m
sta2 (N2): Dual-interface mobile node
  - Starts at LTE coverage (20, 50, 0)
  - Moves to WiFi coverage (90, 50, 0) over 50 seconds
================================================================================

mininet-wifi>
```

## What to Observe

### Terminal 1 (LTE Controller)

Watch for these key events:

```
[LTE] 🎬 FLOW DETECTED: 10.0.0.2 → 10.0.0.100

╔══════════════════════════════════════════════════════════════════════════════╗
║ MONITORING CYCLE #1                                                          ║
╚══════════════════════════════════════════════════════════════════════════════╝

[METRICS] N2 pos=(20.0, 50.0) | d_eNB=0.0 m, d_AP=70.0 m
[METRICS] RSSI=-50.0 dBm | PDR=0.950 | LTE=18.05 Mbps | WiFi=8.00 Mbps
[PREDICT] 🟢 Quality: Good (100.0%) | Class: Good
[LTE] T_LTE < T_c → Check quality
[LTE] Good quality → LTE-only

... (sta2 moves toward WiFi) ...

[METRICS] N2 pos=(55.0, 50.0) | d_eNB=35.0 m, d_AP=35.0 m
[METRICS] RSSI=-72.3 dBm | PDR=0.850 | LTE=10.20 Mbps | WiFi=15.60 Mbps
[PREDICT] 🟡 Quality: Intermediate (75.0%) | Class: Intermediate
[OFFLOAD] Quality=Intermediate → WiFi offloading

╔══════════════════════════════════════════════════════════════════════════════╗
║ OFFLOAD EXECUTION (ALGORITHM 1)                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝

[OFFLOAD] V_LTE = 19.13 MB
[OFFLOAD] V_WiFi = 55.87 MB
[REST] WiFi Response: True

╔══════════════════════════════════════════════════════════════════════════════╗
║ FLOWLET-BASED OFFLOADING (ALGORITHM 2)                                       ║
╚══════════════════════════════════════════════════════════════════════════════╝

[FLOWLET] 📊 MULTIPATH SPLIT CALCULATION (Algorithm 2, Line 8)
[FLOWLET]    ├─ Total throughput (D): 25.80 Mbps
[FLOWLET]    ├─ LTE ratio (a₁): 0.395 (39.5%)
[FLOWLET]    └─ WiFi ratio (a₂): 0.605 (60.5%)

[FLOWLET] ✅ Flow entry installed successfully
[FLOWLET] 🎯 FLOWLET MECHANISM ACTIVE
[OFFLOAD] ✅ Offload executed successfully
```

### Terminal 2 (WiFi Controller)

Watch for offload requests:

```
╔══════════════════════════════════════════════════════════════════════════════╗
║ OFFLOAD REQUEST RECEIVED                                                     ║
╚══════════════════════════════════════════════════════════════════════════════╝

[WiFi] 🔥 Offload request for UE 00:00:00:00:00:02
[WiFi]    └─ Flow: 10.0.0.2 → 10.0.0.100
[WiFi] ✅ Credentials verified
[WiFi] 📊 Current load = 0.25
[WiFi] Gateway selected: ap_1
[WiFi] IP allocated: 10.0.2.42
[WiFi] ✅ Offload ACCEPTED
[WiFi]    ├─ UE: 00:00:00:00:00:02
[WiFi]    ├─ IP: 10.0.2.42
[WiFi]    ├─ Bandwidth: 16.67 Mbps
[WiFi]    └─ New load: 0.30
```

### Terminal 3 (Mininet)

Monitor position and throughput:

```bash
# Check sta2 position
mininet-wifi> py sta2.params["position"]
[55.0, 50.0, 0.0]

# Check distance from eNodeB and AP
mininet-wifi> distance sta2 enodeb
35.0

mininet-wifi> distance sta2 ap1
35.0

# Monitor throughput logs
mininet-wifi> tail -f /tmp/sta2_throughput.log
```

## Understanding the Logs

### CSV Log Files

**results/lte_log.csv:**

```csv
timestamp,node,position_x,position_y,rssi,pdr,predicted_quality,lte_throughput,wifi_load,event,flowlet_id
1698765432.1,n2,20.0,50.0,-50.00,0.950,Good,18.05,0.250,MONITOR,
1698765437.1,n2,27.5,50.0,-58.32,0.920,Good,15.84,0.250,MONITOR,
1698765442.1,n2,35.0,50.0,-66.64,0.880,Intermediate,12.32,0.300,OFFLOAD,4521
```

**results/wifi_log.csv:**

```csv
timestamp,event,ue_id,wifi_load,allocated_ip,bandwidth_mbps
1698765442.2,ACCEPTED,00:00:00:00:00:02,0.300,10.0.2.42,16.67
```

## Key Behaviors to Verify

### 1. Mobility Detection

- sta2 moves from (20,50,0) to (90,50,0) over 50 seconds
- Position updates sent to LTE controller every second
- RSSI and throughput change based on distance

### 2. Channel Quality Prediction

- **Good**: RSSI ≥ -75 dBm AND PDR ≥ 0.85
- **Bad**: RSSI ≤ -87 dBm OR PDR ≤ 0.75
- **Intermediate**: Between Good and Bad

### 3. Offloading Decision (Algorithm 1)

- **T_LTE < T_c AND Quality Good**: LTE only
- **T_LTE < T_c AND Quality Bad/Intermediate**: Trigger offload
- **T_LTE ≥ T_c**: Always trigger offload

### 4. Flowlet-Based Multipath (Algorithm 2)

- Traffic split based on LTE:WiFi throughput ratio
- OpenFlow group table with weighted buckets
- Idle timeout for flowlet detection (Δ = 50ms)
- Avoids packet reordering within flowlets

## Troubleshooting

### Controllers not connecting

```bash
# Check if ports are in use
sudo netstat -tulpn | grep 6653
sudo netstat -tulpn | grep 6654

# Kill existing Ryu processes
sudo pkill -f ryu-manager
```

### Mininet topology issues

```bash
# Clean up Mininet
sudo mn -c

# Check Open vSwitch
sudo ovs-vsctl show
```

### Flow not detected

- Ensure iperf server started on h1 (port 5001)
- Check flow tables: `sudo ovs-ofctl dump-flows s3 -O OpenFlow13`
- Verify controllers connected to switches

### No offloading occurring

- Check WiFi controller REST API: `curl http://localhost:8081/wifi_load`
- Verify sta2 position reaches WiFi range (x > 50)
- Check LTE controller logs for quality classification

## Expected Timeline

| Time (s) | Position    | Event                                   |
| -------- | ----------- | --------------------------------------- |
| 0-10     | (20, 50)    | Flow detection, LTE-only                |
| 10-30    | (20→55, 50) | Moving toward WiFi, quality monitoring  |
| 30-40    | (55→75, 50) | Intermediate quality, offload triggered |
| 40-50    | (75→90, 50) | WiFi range, multipath active            |
| 50+      | (90, 50)    | Near WiFi AP, high WiFi throughput      |

## Paper Alignment

This implementation faithfully replicates:

- **Figure 2**: SDN-based HetNet architecture with dual controllers
- **Table 10**: Network parameters (ranges, positions, bandwidth)
- **Algorithm 1**: Data offloading decision logic
- **Algorithm 2**: Flowlet-based multipath traffic splitting
- **Tables 4-8**: Quality classification thresholds
- **Equations 7-11**: Throughput, volume, and RSRP calculations

## Citation

Based on:

```
Kamath, S., et al. (2024). "SDN-Based Multipath Data Offloading Scheme
Using Link Quality Prediction for LTE and WiFi Networks."
IEEE Access, 12, 176554-176568.
```
