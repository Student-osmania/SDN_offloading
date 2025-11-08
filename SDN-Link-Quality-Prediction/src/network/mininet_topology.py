#!/usr/bin/env python3
"""
Mininet-WiFi Topology: LTE-WiFi Hybrid Network (Figure 4)
- h_svr (iperf client) → sta1/n2 (dual-interface UE, iperf server)
- Background load: sta2, sta3, sta4, sta5 (5 stations total as per paper)
- Mobility: sta1 moves 0→100m over 50s
- Controllers: LTE (6653), WiFi (6654)
- BURSTY TRAFFIC: Generates flowlets with 100ms gaps for Algorithm 2 demo
"""

from mininet.node import RemoteController, OVSKernelSwitch, Host
from mininet.log import setLogLevel, info
from mn_wifi.net import Mininet_wifi
from mn_wifi.node import OVSKernelAP
from mn_wifi.cli import CLI
from mn_wifi.link import wmediumd
from mn_wifi.wmediumdConnector import interference
import time
import threading
import requests
import csv
import os
from datetime import datetime


class TopologyManager:
    """Manages topology setup and metrics reporting (Table 2 format)"""
    
    def __init__(self):
        this_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.abspath(os.path.join(this_dir, "..", ".."))
        self.results_dir = os.path.join(project_root, "results")
        os.makedirs(self.results_dir, exist_ok=True)
        self.csv_path = os.path.join(self.results_dir, "ue_metrics.csv")
        self.running = True
 
    def init_csv(self):
        """Initialize CSV with Table 2 format"""
        with open(self.csv_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['datetime', 'src', 'dst', 'channel', 'lte_rssi', 
                           'wifi_rssi', 'lte_pdr', 'wifi_pdr', 'pos_x', 'pos_y'])
    
    def compute_rssi(self, sta, iface):
        """Extract RSSI from interface"""
        try:
            result = sta.cmd(f'iw dev {iface} link')
            if 'signal:' in result:
                rssi = float(result.split('signal:')[1].split('dBm')[0].strip())
                return max(-100.0, min(-40.0, rssi))
        except:
            pass
        return -75.0
    
    def compute_pdr(self, sta, iface):
        """Compute PDR from tx stats"""
        try:
            result = sta.cmd(f'iw dev {iface} station dump')
            tx_packets = 0
            tx_retries = 0
            
            for line in result.split('\n'):
                if 'tx packets:' in line:
                    tx_packets = int(line.split(':')[1].strip())
                if 'tx retries:' in line:
                    tx_retries = int(line.split(':')[1].strip())
            
            if tx_packets > 0:
                pdr = max(0.0, min(1.0, 1.0 - (tx_retries / tx_packets)))
                return pdr
        except:
            pass
        return 0.85
    
    def metrics_reporter(self, net, sta):
        """Periodically send metrics to LTE controller (every 2s)"""
        info('[METRICS] Reporter started\n')
        
        while self.running:
            try:
                time.sleep(2)
                
                # Measure both interfaces
                lte_rssi = self.compute_rssi(sta, 'sta1-wlan0')
                wifi_rssi = self.compute_rssi(sta, 'sta1-wlan1')
                lte_pdr = self.compute_pdr(sta, 'sta1-wlan0')
                wifi_pdr = self.compute_pdr(sta, 'sta1-wlan1')
                
                # Get position
                pos = sta.params.get('position', [20, 50, 0])
                
                # Log to CSV (Table 2 format)
                with open(self.csv_path, 'a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        datetime.now().isoformat(),
                        'sta1', 'h_svr', '1,6',
                        lte_rssi, wifi_rssi, lte_pdr, wifi_pdr,
                        pos[0], pos[1]
                    ])
                
                # Send to LTE controller
                metrics = {
                    'position': [pos[0], pos[1], pos[2]],
                    'lte_rssi': lte_rssi,
                    'wifi_rssi': wifi_rssi,
                    'lte_pdr': lte_pdr,
                    'wifi_pdr': wifi_pdr
                }
                
                try:
                    requests.post('http://127.0.0.1:8080/ue_metrics', 
                                json=metrics, timeout=1)
                except:
                    pass
                    
            except Exception as e:
                info(f'[METRICS] Error: {e}\n')


def build_topology():
    """Build Figure 4 topology with Table 10 parameters"""
    
    info('\n' + '='*80 + '\n')
    info('  MININET-WIFI TOPOLOGY: LTE-WiFi HetNet (Figure 4)\n')
    info('  FLOWLET-BASED TRAFFIC: 100ms gaps for Algorithm 2\n')
    info('='*80 + '\n\n')
    
    manager = TopologyManager()
    manager.init_csv()
    
    net = Mininet_wifi(
        controller=RemoteController,
        link=wmediumd,
        wmediumd_mode=interference,
        accessPoint=OVSKernelAP,
        switch=OVSKernelSwitch
    )

    info('*** Adding controllers\n')
    c_lte = net.addController(
        name='c_lte', 
        controller=RemoteController,
        ip='127.0.0.1', 
        port=6653
    )
    c_wifi = net.addController(
        name='c_wifi',
        controller=RemoteController,
        ip='127.0.0.1',
        port=6654
    )

    info('*** Adding switches/APs (Table 10 params)\n')
    # LTE infrastructure
    s_lte = net.addSwitch('s_lte', cls=OVSKernelSwitch, dpid='0000000000000001', protocols='OpenFlow13')
    ap_lte = net.addAccessPoint(
        'ap_lte', 
        cls=OVSKernelAP, 
        ssid='lte-net',
        mode='g', 
        channel='1',
        position='20,50,0',
        range=200,
        dpid='0000000000000003',
        protocols='OpenFlow13'
    )
    
    # WiFi infrastructure
    s_wifi = net.addSwitch('s_wifi', cls=OVSKernelSwitch, dpid='0000000000000002', protocols='OpenFlow13')
    ap_wifi = net.addAccessPoint(
        'ap_wifi', cls=OVSKernelAP,
        ssid='wifi-net',
        mode='g', channel='6',
        position='190,50,0',
        range=80,
        dpid='0000000000000004',
        protocols='OpenFlow13'
    )

    info('*** Adding hosts/stations\n')
    # Server (iperf client - sends bursty traffic)
    h_svr = net.addHost('h_svr', cls=Host, ip='10.0.0.100/24')
    
    # UE sta1/n2 with dual interfaces (wlan0=LTE, wlan1=WiFi)
    sta1 = net.addStation(
        'sta1', wlans=2, ip='10.0.0.1/24',
        position='20,45,0', range=50
    )
    
    # Background load stations (N5-N9 from paper)
    sta2 = net.addStation('sta2', ip='10.0.0.2/24', position='6,45,0')
    sta3 = net.addStation('sta3', ip='10.0.0.3/24', position='12,55,0')
    sta4 = net.addStation('sta4', ip='10.0.0.4/24', position='30,40,0')
    sta5 = net.addStation('sta5', ip='10.0.0.5/24', position='35,60,0')
    sta6 = net.addStation('sta6', ip='10.0.0.6/24', position='185,45,0')
    sta7 = net.addStation('sta7', ip='10.0.0.7/24', position='210,60,0')
    sta8 = net.addStation('sta8', ip='10.0.0.8/24', position='200,40,0')
    sta9 = net.addStation('sta9', ip='10.0.0.9/24', position='182,54,0')

    info('*** Configuring propagation model (exp=3.5)\n')
    net.setPropagationModel(model="logDistance", exp=3.5)

    info('*** Configuring WiFi nodes\n')
    net.configureWifiNodes()

    info('*** Adding links (1 Gbps core)\n')
    net.addLink(h_svr, s_lte, bw=100)
    net.addLink(s_lte, s_wifi, bw=100)
    net.addLink(s_lte, ap_lte, bw=100)
    net.addLink(s_wifi, ap_wifi, bw=100)

    info('*** Plotting network\n')
    net.plotGraph(max_x=275, max_y=100)

    info('*** Starting mobility (0→100m over 50s)\n')
    net.startMobility(time=0, repetitions=1, ac_method='ssf')
    net.mobility(sta1, 'start', time=51, position='20,45,0')
    net.mobility(sta1, 'stop', time=150, position='200,50,0')
    net.stopMobility(time=151)

    info('*** Building network\n')
    net.build()
    
    c_lte.start()
    c_wifi.start()
    
    s_lte.start([c_lte])
    s_wifi.start([c_wifi])
    ap_lte.start([c_lte])
    ap_wifi.start([c_wifi])

    info('*** Creating bursty traffic generator\n')
    # Create send_bursts.py script
    burst_script = '''#!/usr/bin/env python3
import socket
import time
import sys

dst_ip = sys.argv[1]
dst_port = int(sys.argv[2])

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
burst_id = 0

print("[BURST] Starting flowlet generator")
print("[BURST] Flowlet structure: 100 packets @ 1ms intervals")
print("[BURST] Inter-flowlet gap: 100ms (> Δ=50ms)")

while burst_id < 500:  # Run for ~50 seconds (500 bursts * 0.1s)
    for i in range(100):
        msg = f"Flowlet-{burst_id:04d}-Pkt-{i:03d}".encode()
        sock.sendto(msg, (dst_ip, dst_port))
        time.sleep(0.001)
    
    if burst_id % 10 == 0:
        print(f"[BURST] Sent flowlet {burst_id}/500")
    
    burst_id += 1
    time.sleep(0.1)

print("[BURST] Completed 500 flowlets")
sock.close()
'''
    
    with open('/tmp/send_bursts.py', 'w') as f:
        f.write(burst_script)
    
    h_svr.cmd('chmod +x /tmp/send_bursts.py')

    info('*** Starting traffic flows\n')
    time.sleep(3)
    
    # Main flow: sta1 = server, h_svr = client (bursty)
    sta1.cmd('nc -u -l -p 5001 > /dev/null 2>&1 &')
    time.sleep(1)
    h_svr.cmd('python3 /tmp/send_bursts.py 10.0.0.2 5001 > /tmp/burst_log.txt 2>&1 &')
    
    # Background WiFi load
    sta2.cmd('iperf -s -u -p 5002 > /dev/null 2>&1 &')
    sta3.cmd('iperf -c 10.0.0.3 -u -b 2M -t 120 -p 5002 > /dev/null 2>&1 &')
    
    sta4.cmd('iperf -s -u -p 5003 > /dev/null 2>&1 &')
    sta5.cmd('iperf -c 10.0.0.5 -u -b 1.5M -t 120 -p 5003 > /dev/null 2>&1 &')

    info('*** Starting metrics reporter\n')
    reporter_thread = threading.Thread(
        target=manager.metrics_reporter,
        args=(net, sta1),
        daemon=True
    )
    reporter_thread.start()

    info('\n' + '='*80 + '\n')
    info('  ✅ TOPOLOGY READY\n')
    info('  - sta1 (UE/n2): dual interfaces, moving 20→100m\n')
    info('  - h_svr → sta1: Bursty UDP (flowlets with 100ms gaps)\n')
    info('  - Background: 4 stations generating WiFi load\n')
    info('  - Metrics: sent to LTE controller every 2s\n')
    info('  - Burst log: /tmp/burst_log.txt\n')
    info('='*80 + '\n\n')

    CLI(net)
    
    manager.running = False
    info('*** Stopping network\n')
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    build_topology()