#!/usr/bin/env python3
"""
Mininet-WiFi Topology for LTE-WiFi HetNet
Mobility-Aware Link Quality Prediction Simulation
"""

from mininet.log import setLogLevel, info
from mn_wifi.net import Mininet_wifi
from mn_wifi.node import OVSKernelAP
from mininet.node import RemoteController
from mn_wifi.cli import CLI
from mn_wifi.link import wmediumd
from mn_wifi.wmediumdConnector import interference
import time
import os


def create_hetnet_topology():
    """
    Create HetNet topology with LTE eNB, WiFi AP, and mobile nodes
    N2 is dual-homed and mobile (moves from LTE → WiFi)
    """
    
    info('*** Creating Mininet-WiFi HetNet Topology\n')

    net = Mininet_wifi(
        controller=RemoteController,
        link=wmediumd,
        wmediumd_mode=interference,
        noise_th=-91,
        fading_cof=3
    )

    info('*** Connecting to remote controllers\n')
    c_lte = net.addController(
        'c_lte',
        controller=RemoteController,
        ip='127.0.0.1',
        port=6653
    )
    c_wifi = net.addController(
        'c_wifi',
        controller=RemoteController,
        ip='127.0.0.1',
        port=6654
    )

    info('*** Adding LTE base station (eNodeB)\n')
    s_lte = net.addSwitch(
        's_lte',
        dpid='0000000000000001',
        protocols='OpenFlow13'
    )

    info('*** Adding WiFi Access Point\n')
    ap1 = net.addAccessPoint(
        'ap1',
        ssid='HetNet-WiFi',
        mode='g',
        channel='6',
        position='50,50,0',
        range=30,
        dpid='0000000000000002',
        protocols='OpenFlow13',
        cls=OVSKernelAP
    )

    info('*** Adding mobile stations\n')

    # LTE-only nodes
    n1 = net.addStation('n1', ip='10.0.1.1/24', position='10,10,0')
    n3 = net.addStation('n3', ip='10.0.1.3/24', position='15,15,0')
    n4 = net.addStation('n4', ip='10.0.1.4/24', position='20,10,0')

    # Dual-interface (LTE + WiFi) mobile node
    n2 = net.addStation('n2', ip='10.0.1.2/24', position='10,20,0', range=15)

    # WiFi-only nodes
    n5 = net.addStation('n5', ip='10.0.2.5/24', position='45,45,0', range=15)
    n6 = net.addStation('n6', ip='10.0.2.6/24', position='55,45,0', range=15)
    n7 = net.addStation('n7', ip='10.0.2.7/24', position='50,55,0', range=15)
    n8 = net.addStation('n8', ip='10.0.2.8/24', position='45,55,0', range=15)
    n9 = net.addStation('n9', ip='10.0.2.9/24', position='55,50,0', range=15)

    # Server node
    n10 = net.addStation('n10', ip='10.0.1.10/24', position='10,30,0')

    info('*** Configuring propagation model\n')
    net.setPropagationModel(model='logDistance', exp=4)

    info('*** Creating links\n')
    # LTE wired backhaul
    net.addLink(n1, s_lte)
    net.addLink(n2, s_lte)
    net.addLink(n3, s_lte)
    net.addLink(n4, s_lte)
    net.addLink(n10, s_lte)

    # LTE–WiFi interconnection backbone
    net.addLink(s_lte, ap1)

    info('*** Configuring nodes\n')
    net.configureNodes()

    info('*** Starting network\n')
    net.build()

    info('*** Connecting switches to remote controllers\n')
    c_lte.start()
    c_wifi.start()

    s_lte.start([c_lte])
    ap1.start([c_wifi])

    time.sleep(3)

    info('*** Configuring WiFi associations\n')
    for sta in [n5, n6, n7, n8, n9]:
        sta.cmd('iw dev %s-wlan0 connect HetNet-WiFi' % sta.name)

    time.sleep(2)

    info('*** Configuring mobility for N2 (dual-interface)\n')
    net.startMobility(time=0)
    net.mobility(n2, 'start', time=1, position='10,20,0')
    net.mobility(n2, 'stop', time=50, position='50,50,0')
    net.stopMobility(time=51)

    info('\n')
    info('=' * 70 + '\n')
    info('*** HetNet Topology Active\n')
    info('=' * 70 + '\n')
    info('  LTE Network (s_lte): N1, N2, N3, N4, N10 (server)\n')
    info('  WiFi Network (ap1):  N5, N6, N7, N8, N9\n')
    info('  Dual-interface:      N2 (moving LTE → WiFi)\n')
    info('\n')
    info('  Controllers:\n')
    info('    - LTE:  port 6653 (external Ryu)\n')
    info('    - WiFi: port 6654 (external Ryu)\n')
    info('\n')
    info('*** Mobility: N2 moves from (10,20) → (50,50) over 50s\n')
    info('*** RSSI will degrade as N2 moves, triggering offload prediction\n\n')

    info('*** Starting traffic flows\n')
    n10.cmd('iperf -s -u > /tmp/iperf_server.log 2>&1 &')
    time.sleep(1)
    n2.cmd('iperf -c 10.0.1.10 -u -b 10M -t 120 > /tmp/iperf_n2.log 2>&1 &')

    for sta in [n5, n6, n7]:
        sta.cmd(f'iperf -c 10.0.1.10 -u -b 2M -t 120 > /tmp/iperf_{sta.name}.log 2>&1 &')

    info('*** Active flows:\n')
    info('  - N2 → N10: 10 Mbps (mobile)\n')
    info('  - N5,N6,N7 → N10: 2 Mbps each (WiFi load)\n\n')
    info('*** Type "exit" to stop\n\n')

    CLI(net)

    info('*** Stopping network\n')
    net.stop()


def main():
    setLogLevel('info')

    info('\n')
    info('=' * 70 + '\n')
    info('  SDN-Based LTE-WiFi HetNet with Mininet-WiFi\n')
    info('  Mobility-Aware Link Quality Prediction\n')
    info('=' * 70 + '\n\n')
    info('*** Prerequisites:\n')
    info('  1. Ryu LTE controller MUST be running on port 6653\n')
    info('  2. Ryu WiFi controller MUST be running on port 6654\n')
    info('  3. wmediumd installed for wireless simulation\n\n')

    time.sleep(2)

    try:
        create_hetnet_topology()
    except KeyboardInterrupt:
        info('\n*** Interrupted\n')
    except Exception as e:
        info(f'\n*** Error: {e}\n')
        import traceback
        traceback.print_exc()
    finally:
        os.system('sudo mn -c 2>/dev/null')


if __name__ == '__main__':
    main()