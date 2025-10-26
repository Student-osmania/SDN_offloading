#!/usr/bin/env python3
"""
Ryu LTE Controller - Algorithm 1 Implementation
Port: 6653, REST: 8080
"""

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, ipv4, udp
from ryu.lib import hub
from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from webob import Response
import requests
import math
import sys
import os
import time
import csv
import json

# Import prediction modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from src.prediction.channel_predictor import ChannelPredictor
from src.prediction.quality_classifier import QualityClassifier
from src.network.traffic_offloading import TrafficOffloader

class LTEController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {'wsgi': WSGIApplication}

    def __init__(self, *args, **kwargs):
        super(LTEController, self).__init__(*args, **kwargs)
        wsgi = kwargs['wsgi']
        
        # MAC to port mapping
        self.mac_to_port = {}
        self.datapaths = {}
        
        # Prediction modules
        self.predictor = ChannelPredictor()
        self.classifier = QualityClassifier()
        self.offloader = None
        
        # N2 (sta2) metrics
        self.n2_pos = (20.0, 50.0, 0.0)
        self.rssi = -60.0
        self.pdr = 0.95
        self.lte_throughput = 15.0
        self.wifi_throughput = 10.0
        
        # Flow monitoring
        self.flow_stats = {}
        self.monitored_flows = set()
        self.flow_start_time = {}
        self.last_poll_time = time.time()
        
        # Algorithm 1 parameters (from paper)
        self.T_c = 15.0  # Threshold time for video transfer (seconds)
        self.current_flow_volume = 150.0  # MB - simulated video file size
        self.flow_detected = False
        
        # WiFi controller communication
        self.wifi_controller_url = "http://localhost:8081/wifi_load"
        self.wifi_offload_url = "http://localhost:8081/wifi_offload"
        
        # Monitoring intervals
        self.monitoring_interval = 5  # seconds
        self.flow_poll_interval = 2  # seconds
        
        # REST API
        wsgi.register(LteRestController, {'lte_app': self})
        
        # CSV logging
        self.csv_file = 'results/lte_log.csv'
        os.makedirs('results', exist_ok=True)
        with open(self.csv_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['timestamp', 'node', 'position_x', 'position_y', 'rssi', 'pdr', 
                           'predicted_quality', 'lte_throughput', 'wifi_load', 'event', 'flowlet_id'])
        
        self._print_header()
        
        # Start monitoring threads
        self.monitor_thread = hub.spawn(self._monitor_loop)
        self.flow_poll_thread = hub.spawn(self._poll_flow_stats)

    def _print_header(self):
        print("\n" + "="*80)
        print("  LTE CONTROLLER - ALGORITHM 1 IMPLEMENTATION")
        print("  Port: 6653 | REST API: 8080")
        print("="*80 + "\n")

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        
        self.datapaths[datapath.id] = datapath
        self.logger.info("[LTE] Switch DPID: 0x%016x connected", datapath.id)
        
        # Initialize offloader with first datapath (edge switch)
        if self.offloader is None:
            self.offloader = TrafficOffloader(datapath, None)
        
        # Install table-miss flow entry
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

    def add_flow(self, datapath, priority, match, actions, idle_timeout=0, hard_timeout=0):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                match=match, instructions=inst,
                                idle_timeout=idle_timeout, hard_timeout=hard_timeout)
        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']
        
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]
        
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
        
        dst = eth.dst
        src = eth.src
        dpid = datapath.id
        
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port
        
        # Detect video flow (iperf on port 5001)
        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        if ip_pkt and not self.flow_detected:
            udp_pkt = pkt.get_protocol(udp.udp)
            if udp_pkt and (udp_pkt.dst_port == 5001 or udp_pkt.src_port == 5001):
                flow_key = (ip_pkt.src, ip_pkt.dst)
                if flow_key not in self.monitored_flows:
                    self.monitored_flows.add(flow_key)
                    self.flow_start_time[flow_key] = time.time()
                    self.flow_detected = True
                    self.current_flow_volume = 150.0  # MB
                    self.logger.info("[LTE] 🎬 FLOW DETECTED: %s → %s", ip_pkt.src, ip_pkt.dst)
                    self.flow_stats[flow_key] = {'byte_count': 0, 'packet_count': 0}
        
        # Forwarding logic
        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD
        
        actions = [parser.OFPActionOutput(out_port)]
        
        # Install flow to avoid packet_in next time
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)
            self.add_flow(datapath, 1, match, actions, idle_timeout=30)
        
        # Send packet out
        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=data)
        datapath.send_msg(out)

    def _poll_flow_stats(self):
        """Periodically poll flow statistics"""
        while True:
            hub.sleep(self.flow_poll_interval)
            for dpid, datapath in self.datapaths.items():
                parser = datapath.ofproto_parser
                req = parser.OFPFlowStatsRequest(datapath)
                datapath.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply_handler(self, ev):
        """Handle flow statistics replies"""
        current_time = time.time()
        
        for stat in ev.msg.body:
            if stat.priority == 0 or stat.packet_count == 0:
                continue
            
            if 'ipv4_src' in stat.match and 'ipv4_dst' in stat.match:
                src_ip = stat.match['ipv4_src']
                dst_ip = stat.match['ipv4_dst']
                flow_key = (src_ip, dst_ip)
                
                if flow_key in self.monitored_flows:
                    prev = self.flow_stats.get(flow_key, {})
                    prev_bytes = prev.get('byte_count', 0)
                    
                    self.flow_stats[flow_key] = {
                        'byte_count': stat.byte_count,
                        'packet_count': stat.packet_count,
                        'duration': stat.duration_sec,
                        'last_update': current_time
                    }
                    
                    # Calculate throughput
                    time_delta = current_time - self.last_poll_time
                    if time_delta > 0 and prev_bytes > 0:
                        byte_delta = stat.byte_count - prev_bytes
                        throughput_mbps = (byte_delta * 8) / (time_delta * 1e6)
                        self.flow_stats[flow_key]['throughput'] = throughput_mbps
        
        self.last_poll_time = current_time

    def _monitor_loop(self):
        """Main monitoring loop - implements Algorithm 1"""
        self.logger.info("[LTE] Monitoring loop started")
        iteration = 0
        startup_wait = 15
        
        while True:
            hub.sleep(self.monitoring_interval)
            iteration += 1
            
            if not self.datapaths:
                continue
            
            # Wait for flow detection
            if not self.flow_detected and iteration < startup_wait // self.monitoring_interval:
                if iteration == 1:
                    self.logger.info("[LTE] ⏳ Waiting for flow detection...")
                continue
            
            if not self.flow_detected:
                continue
            
            print(f"\n╔{'═'*78}╗")
            print(f"║ MONITORING CYCLE #{iteration}".ljust(79) + "║")
            print(f"╚{'═'*78}╝\n")
            
            # Update metrics based on position
            self._update_metrics()
            
            # Calculate transmitted data
            transmitted_MB = (self.lte_throughput / 8.0) * self.monitoring_interval
            self.current_flow_volume = max(0.0, self.current_flow_volume - transmitted_MB)
            
            self.logger.info("[LTE] Transmitted: %.2f MB | Remaining: %.2f MB",
                           transmitted_MB, self.current_flow_volume)
            
            if self.current_flow_volume <= 0.0:
                self.logger.info("[LTE] ✅ Flow completed")
                self._log_to_csv('n2', 'COMPLETED', None)
                continue
            
            # Query WiFi load
            wifi_load = self._query_wifi_load()
            if wifi_load is None:
                wifi_load = 0.30
            
            # Calculate T_LTE (Equation 7)
            data_rate_MBps = self.lte_throughput / 8.0
            T_LTE = self.current_flow_volume / data_rate_MBps if data_rate_MBps > 0 else 999
            self.logger.info("[LTE] T_LTE = %.2f sec (Eq 7)", T_LTE)
            
            # Predict channel quality
            quality_class = self.classifier.classify(self.rssi, self.pdr)
            quality_pred, confidence = self.predictor.predict(self.rssi, self.pdr)
            
            indicator = "🟢" if quality_class == 'Good' else ("🟡" if quality_class == 'Intermediate' else "🔴")
            self.logger.info("[PREDICT] %s Quality: %s (%.1f%%) | Class: %s",
                           indicator, quality_pred, confidence*100, quality_class)
            
            # Log current state
            lte_load = 1.0 - (self.lte_throughput / 20.0)
            self._log_to_csv('n2', 'MONITOR', None, quality_class, lte_load, wifi_load)
            
            # Algorithm 1 decision logic
            if T_LTE < self.T_c:
                self.logger.info("[LTE] T_LTE < T_c → Check quality")
                if quality_class in ['Bad', 'Intermediate']:
                    self.logger.warning("[OFFLOAD] Quality=%s → WiFi offloading", quality_class)
                    self._execute_algorithm_1_offload(wifi_load)
                else:
                    self.logger.info("[LTE] Good quality → LTE-only")
            else:
                self.logger.warning("[LTE] T_LTE ≥ T_c → Offload required")
                self._execute_algorithm_1_offload(wifi_load)

    def _update_metrics(self):
        """Update RSSI, PDR, and throughput based on position"""
        
        def dist(a, b):
            return math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2)
        
        enb_pos = (20.0, 50.0, 0.0)
        ap_pos = (90.0, 50.0, 0.0)
        
        d_enb = max(1.0, dist(self.n2_pos, enb_pos))
        d_ap = max(1.0, dist(self.n2_pos, ap_pos))
        
        # Calculate RSSI (log-distance path loss model)
        rssi_0 = -50.0
        path_loss_exp = 3.5
        self.rssi = rssi_0 - 10.0 * path_loss_exp * math.log10(d_enb)
        self.rssi = max(-100.0, min(-50.0, self.rssi))
        
        # Calculate PDR based on flow statistics and distance
        total_rx = 0
        total_tx = 0
        for flow_key in self.monitored_flows:
            if flow_key in self.flow_stats:
                stats = self.flow_stats[flow_key]
                rx = stats.get('packet_count', 0)
                tx = max(rx, stats.get('byte_count', 0) // 1400 + 1)
                total_rx += rx
                total_tx += tx
        
        if total_tx > 10:
            self.pdr = min(1.0, total_rx / total_tx)
        else:
            # Estimate PDR based on distance
            self.pdr = max(0.65, 1.0 - (d_enb / 80.0) * 0.35)
        
        # Calculate LTE throughput (Shannon capacity with PDR adjustment)
        snr_db = self.rssi + 100.0
        snr_linear = max(0.01, 10 ** (snr_db / 10.0))
        capacity_mbps = 10.0 * math.log2(snr_linear + 1.0)
        self.lte_throughput = max(2.0, min(20.0, capacity_mbps * self.pdr))
        
        # Estimate WiFi throughput based on distance to AP
        wifi_factor = max(0.2, 1.0 - (d_ap / 80.0))
        self.wifi_throughput = 8.0 + 12.0 * wifi_factor
        
        self.logger.info("[METRICS] N2 pos=(%.1f, %.1f) | d_eNB=%.1f m, d_AP=%.1f m",
                        self.n2_pos[0], self.n2_pos[1], d_enb, d_ap)
        self.logger.info("[METRICS] RSSI=%.1f dBm | PDR=%.3f | LTE=%.2f Mbps | WiFi=%.2f Mbps",
                        self.rssi, self.pdr, self.lte_throughput, self.wifi_throughput)

    def _execute_algorithm_1_offload(self, wifi_load):
        """Execute Algorithm 1 offloading decision"""
        
        if not self.monitored_flows:
            return
        
        flow_key = list(self.monitored_flows)[0]
        src_ip, dst_ip = flow_key
        
        print(f"\n╔{'═'*78}╗")
        print("║ OFFLOAD EXECUTION (ALGORITHM 1)".ljust(79) + "║")
        print(f"╚{'═'*78}╝\n")
        
        # Calculate V_LTE and V_WiFi (Equations 8, 9, 10)
        T_prime = self.T_c
        V_LTE = (self.lte_throughput / 8.0) * T_prime
        V_WiFi = max(0.0, self.current_flow_volume - V_LTE)
        
        self.logger.info("[OFFLOAD] V_LTE = %.2f MB", V_LTE)
        self.logger.info("[OFFLOAD] V_WiFi = %.2f MB", V_WiFi)
        
        if V_WiFi <= 1.0:
            self.logger.info("[OFFLOAD] No significant WiFi offload needed")
            return
        
        # Exchange credentials with WiFi controller
        if not self._exchange_credentials_with_wifi(src_ip, dst_ip):
            self.logger.error("[REST] Controller communication FAILED")
            return
        
        # Generate flowlet ID
        flowlet_id = int(time.time() * 1000) % 10000
        self.logger.info("[OFFLOAD] Calculating flowlet (ID: %d)", flowlet_id)
        
        # Execute offload using Algorithm 2 (flowlet-based multipath)
        success = self.offloader.execute_offload(
            src_ip, dst_ip, wifi_load,
            self.lte_throughput, self.wifi_throughput, V_WiFi
        )
        
        if success:
            self.logger.info("[OFFLOAD] ✅ Offload executed successfully")
            self._log_to_csv('n2', 'OFFLOAD', flowlet_id)
        else:
            self.logger.error("[OFFLOAD] ❌ Failed")

    def _exchange_credentials_with_wifi(self, src_ip, dst_ip):
        """Exchange UE credentials with WiFi controller"""
        try:
            payload = {
                'src_ip': src_ip,
                'dst_ip': dst_ip,
                'ue_credentials': {
                    'mac': '00:00:00:00:00:02',
                    'auth_token': 'TOKEN_N2'
                }
            }
            response = requests.post(self.wifi_offload_url, json=payload, timeout=2)
            if response.status_code == 200:
                result = response.json()
                self.logger.info("[REST] WiFi Response: %s", result.get('success'))
                return result.get('success', False)
            return False
        except Exception as e:
            self.logger.error("[REST] Exception: %s", str(e))
            return False

    def _query_wifi_load(self):
        """Query WiFi load from WiFi controller"""
        try:
            response = requests.get(self.wifi_controller_url, timeout=2)
            if response.status_code == 200:
                return response.json().get('load', 0.30)
        except:
            pass
        return None

    def _log_to_csv(self, node, event, flowlet_id=None, quality='', lte_load=0.0, wifi_load=0.0):
        """Log event to CSV file"""
        try:
            with open(self.csv_file, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    time.time(), node, 
                    f"{self.n2_pos[0]:.1f}", f"{self.n2_pos[1]:.1f}",
                    f"{self.rssi:.2f}", f"{self.pdr:.3f}", 
                    quality, f"{self.lte_throughput:.2f}",
                    f"{wifi_load:.3f}", event, flowlet_id or ''
                ])
        except Exception as e:
            self.logger.error("[CSV] Error: %s", str(e))


class LteRestController(ControllerBase):
    def __init__(self, req, link, data, **config):
        super(LteRestController, self).__init__(req, link, data, **config)
        self.lte_app = data['lte_app']

    @route('lte', '/ue_metrics', methods=['POST'])
    def ue_metrics(self, req, **kwargs):
        """Receive UE position and metrics updates"""
        try:
            payload = json.loads(req.body.decode('utf-8'))
            pos = payload.get('position', [20.0, 50.0, 0.0])
            self.lte_app.n2_pos = (float(pos[0]), float(pos[1]), float(pos[2]))
            
            # Optionally update RSSI if provided
            lte_rssi = payload.get('lte_rssi')
            if lte_rssi and -100 < lte_rssi < -40:
                self.lte_app.rssi = lte_rssi
            
            return Response(content_type='application/json; charset=utf-8', 
                          body=b'{"ok": true}')
        except Exception as e:
            return Response(status=400, 
                          body=f'{{"ok": false, "error": "{str(e)}"}}'.encode('utf-8'))