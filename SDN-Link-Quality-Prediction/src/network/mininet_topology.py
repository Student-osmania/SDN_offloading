#!/usr/bin/python3

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel, info
from mininet.link import TCLink
from mn_wifi.net import Mininet_wifi
from mn_wifi.node import OVSKernelAP, Station
from mn_wifi.link import wmediumd
from mn_wifi.wmediumdConnector import interference
import time
import threading
import requests
import json

def create_topology():
    """Create the LTE-WiFi heterogeneous network topology"""
    
    info('*** Creating network topology\n')
    
    net = Mininet_wifi(
        controller=RemoteController,
        link=wmediumd,
        wmediumd_mode=interference,
        accessPoint=OVSKernelAP,
        switch=OVSKernelSwitch
    )
    
    info('*** Adding controllers\n')
    # LTE Controller on port 6653
    c0_lte = net.addController('c0', controller=RemoteController, 
                               ip='127.0.0.1', port=6653)
    
    # WiFi Controller on port 6654
    c1_wifi = net.addController('c1', controller=RemoteController,
                                ip='127.0.0.1', port=6654)
    
    info('*** Creating WiFi Access Point\n')
    # WiFi AP at position (90, 50, 0) with 50m range
    ap1 = net.addAccessPoint(
        'ap1',
        ssid='offload-wifi',
        mode='g',
        channel='6',
        position='90,50,0',
        range=50,
        cls=OVSKernelAP,
        dpid='0000000000000001',
        protocols='OpenFlow13'
    )
    
    info('*** Creating LTE eNodeB (simulated as AP)\n')
    # LTE eNodeB at position (20, 50, 0) with 100m range
    enodeb = net.addAccessPoint(
        'enodeb',
        ssid='lte-network',
        mode='g',
        channel='1',
        position='20,50,0',
        range=100,
        cls=OVSKernelAP,
        dpid='0000000000000002',
        protocols='OpenFlow13'
    )
    
    info('*** Creating switches for LTE and WiFi core networks\n')
    # LTE Core Network Switch (connected to LTE controller)
    s1_lte = net.addSwitch('s1', cls=OVSKernelSwitch, dpid='0000000000000003', protocols='OpenFlow13')
    
    # WiFi Core Network Switch (connected to WiFi controller)
    s2_wifi = net.addSwitch('s2', cls=OVSKernelSwitch, dpid='0000000000000004', protocols='OpenFlow13')
    
    # Edge switch connecting both networks (connected to LTE controller for offloading)
    s3_edge = net.addSwitch('s3', cls=OVSKernelSwitch, dpid='0000000000000005', protocols='OpenFlow13')
    
    info('*** Creating server/host\n')
    # Server hosting the video content
    h1 = net.addHost('h1', ip='10.0.0.100/24', mac='00:00:00:00:00:01')
    
    info('*** Creating mobile stations\n')
    # N2 - Main dual-interface node (LTE + WiFi)
    sta2 = net.addStation(
        'sta2',
        ip='10.0.0.2/24',
        mac='00:00:00:00:00:02',
        position='20,50,0',  # Starting position near LTE eNodeB
        range=30
    )
    
    # N1 - LTE only node
    sta1 = net.addStation(
        'sta1',
        ip='10.0.0.1/24',
        mac='00:00:00:00:00:11',
        position='15,55,0',
        range=30
    )
    
    # N3, N4 - LTE only nodes
    sta3 = net.addStation(
        'sta3',
        ip='10.0.0.3/24',
        mac='00:00:00:00:00:13',
        position='25,45,0',
        range=30
    )
    
    sta4 = net.addStation(
        'sta4',
        ip='10.0.0.4/24',
        mac='00:00:00:00:00:14',
        position='18,52,0',
        range=30
    )
    
    # N5-N9 - WiFi nodes (creating load on WiFi network)
    wifi_positions = [
        (85, 55, 0),
        (95, 50, 0),
        (88, 45, 0),
        (92, 52, 0),
        (90, 48, 0)
    ]
    
    stations_wifi = []
    for i in range(5, 10):
        sta = net.addStation(
            f'sta{i}',
            ip=f'10.0.0.{i}/24',
            mac=f'00:00:00:00:00:{i+10}',
            position=f'{wifi_positions[i-5][0]},{wifi_positions[i-5][1]},{wifi_positions[i-5][2]}',
            range=30
        )
        stations_wifi.append(sta)
    
    # N10 - LTE only node
    sta10 = net.addStation(
        'sta10',
        ip='10.0.0.10/24',
        mac='00:00:00:00:00:20',
        position='22,55,0',
        range=30
    )
    
    info('*** Configuring propagation model\n')
    net.setPropagationModel(model="logDistance", exp=3.5)
    
    info('*** Configuring WiFi nodes\n')
    net.configureWifiNodes()
    
    info('*** Creating links\n')
    # Link server to edge switch (1 Gbps)
    net.addLink(h1, s3_edge, cls=TCLink, bw=1000)
    
    # Link edge switch to LTE core
    net.addLink(s3_edge, s1_lte, cls=TCLink, bw=1000)
    
    # Link edge switch to WiFi core
    net.addLink(s3_edge, s2_wifi, cls=TCLink, bw=1000)
    
    # Link LTE core to eNodeB (100 Mbps backhaul)
    net.addLink(s1_lte, enodeb, cls=TCLink, bw=100)
    
    # Link WiFi core to AP (100 Mbps backhaul)
    net.addLink(s2_wifi, ap1, cls=TCLink, bw=100)
    
    info('*** Starting network\n')
    net.build()
    c0_lte.start()
    c1_wifi.start()
    
    # Start switches with appropriate controllers
    enodeb.start([c0_lte])
    s1_lte.start([c0_lte])
    s3_edge.start([c0_lte])  # Edge switch controlled by LTE controller
    
    ap1.start([c1_wifi])
    s2_wifi.start([c1_wifi])
    
    info('*** Setting up mobility for sta2 (N2)\n')
    # Move sta2 from LTE coverage (20,50,0) to WiFi coverage (90,50,0) over 50 seconds
    net.startMobility(time=0)
    net.mobility(sta2, 'start', time=1, position='20,50,0')
    net.mobility(sta2, 'stop', time=51, position='90,50,0')
    net.stopMobility(time=52)
    
    info('*** Network is ready\n')
    info('=' * 80 + '\n')
    info('TOPOLOGY OVERVIEW:\n')
    info('=' * 80 + '\n')
    info('Server (h1): 10.0.0.100 - Video content source\n')
    info('LTE eNodeB: Position (20, 50, 0) - Range: 100m\n')
    info('WiFi AP: Position (90, 50, 0) - Range: 50m\n')
    info('sta2 (N2): Dual-interface mobile node\n')
    info('  - Starts at LTE coverage (20, 50, 0)\n')
    info('  - Moves to WiFi coverage (90, 50, 0) over 50 seconds\n')
    info('sta1, sta3, sta4, sta10: LTE-only nodes\n')
    info('sta5-sta9: WiFi-only nodes (creating WiFi load)\n')
    info('=' * 80 + '\n')
    
    return net

def setup_traffic_monitoring(net):
    """Setup traffic generation and monitoring"""
    
    info('\n*** Setting up traffic monitoring\n')
    
    h1 = net.get('h1')
    sta2 = net.get('sta2')
    
    # Start iperf server on h1
    info('*** Starting iperf server on h1 (port 5001)\n')
    h1.cmd('iperf -s -u -p 5001 -i 1 > /tmp/iperf_server.log 2>&1 &')
    time.sleep(2)
    
    # Function to create WiFi load
    def create_wifi_load():
        time.sleep(5)
        info('*** Creating WiFi load from sta5-sta9\n')
        for i in range(5, 9):
            sta = net.get(f'sta{i}')
            # Generate 2 Mbps UDP traffic per station
            sta.cmd(f'iperf -c 10.0.0.100 -u -p 5001 -b 2M -t 60 > /tmp/wifi_load_sta{i}.log 2>&1 &')
        info('*** WiFi load generation started\n')
    
    load_thread = threading.Thread(target=create_wifi_load)
    load_thread.daemon = True
    load_thread.start()
    
    # Function to send position updates to LTE controller
    def send_position_updates():
        time.sleep(3)
        info('*** Starting position update thread\n')
        
        for t in range(0, 52):
            time.sleep(1)
            
            # Get sta2 position
            pos = sta2.params.get('position', [20.0, 50.0, 0.0])
            
            # Send position to LTE controller via REST API
            try:
                payload = {
                    'position': [float(pos[0]), float(pos[1]), float(pos[2])],
                    'lte_rssi': -60.0  # Will be calculated based on position
                }
                requests.post('http://localhost:8080/ue_metrics', 
                            json=payload, timeout=1)
            except:
                pass
    
    position_thread = threading.Thread(target=send_position_updates)
    position_thread.daemon = True
    position_thread.start()
    
    # Function to monitor sta2 throughput
    def monitor_offloading():
        time.sleep(10)
        info('\n*** Starting video traffic from h1 to sta2 (10 Mbps)\n')
        info('*** Expected behavior:\n')
        info('    0-30s: LTE only (good signal)\n')
        info('    30-40s: Entering WiFi range, LTE+WiFi offloading may begin\n')
        info('    40-50s: Strong WiFi signal, multipath active\n\n')
        
        # Start 10 Mbps UDP video stream to sta2
        sta2.cmd('iperf -c 10.0.0.100 -u -p 5001 -b 10M -t 55 -i 1 > /tmp/sta2_throughput.log 2>&1 &')
    
    monitor_thread = threading.Thread(target=monitor_offloading)
    monitor_thread.daemon = True
    monitor_thread.start()

def print_usage_instructions():
    """Print usage instructions for the CLI"""
    
    info('\n' + '=' * 80 + '\n')
    info('MININET-WIFI CLI COMMANDS:\n')
    info('=' * 80 + '\n')
    info('Basic commands:\n')
    info('  nodes              - List all nodes\n')
    info('  links              - Show all links\n')
    info('  net                - Show network topology\n')
    info('  dump               - Dump node information\n')
    info('\n')
    info('Position monitoring:\n')
    info('  py sta2.params["position"]  - Check sta2 current position\n')
    info('  distance sta2 enodeb         - Distance from LTE eNodeB\n')
    info('  distance sta2 ap1            - Distance from WiFi AP\n')
    info('\n')
    info('Traffic monitoring:\n')
    info('  tail -f /tmp/sta2_throughput.log     - Monitor sta2 throughput\n')
    info('  tail -f /tmp/iperf_server.log        - Monitor server traffic\n')
    info('  tail -f /tmp/wifi_load_sta5.log      - Monitor WiFi load\n')
    info('\n')
    info('Log files:\n')
    info('  results/lte_log.csv   - LTE controller decisions\n')
    info('  results/wifi_log.csv  - WiFi controller offload events\n')
    info('\n')
    info('Exit:\n')
    info('  exit or quit       - Exit Mininet-WiFi CLI\n')
    info('=' * 80 + '\n\n')

def main():
    """Main function to run the topology"""
    
    setLogLevel('info')
    
    # Create topology
    net = create_topology()
    
    # Setup traffic monitoring and offloading demonstration
    setup_traffic_monitoring(net)
    
    # Print usage instructions
    print_usage_instructions()
    
    # Start CLI
    info('*** Starting CLI (network will run for mobility demonstration)\n')
    info('*** Ensure both Ryu controllers are running:\n')
    info('    Terminal 1: ryu-manager --ofp-tcp-listen-port 6653 ryu_controller_Lte.py\n')
    info('    Terminal 2: ryu-manager --ofp-tcp-listen-port 6654 ryu_controller_Wifi.py\n\n')
    
    CLI(net)
    
    # Cleanup
    info('\n*** Stopping network\n')
    net.stop()

if __name__ == '__main__':
    main()