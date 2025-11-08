#!/usr/bin/env python3
"""
LTE SDN Controller - Full Algorithm 1 & 2 Implementation
Port: 6653, REST API: 8080
Implements: Tables 4-8, Equations 3-11, Figures 2-3, Algorithm 2 (Flowlet-based)
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
from collections import deque

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

import tensorflow as tf
tf.get_logger().setLevel("ERROR")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from src.prediction.channel_predictor import ChannelPredictor
from src.prediction.quality_classifier import classify_link
from src.network.traffic_offloading import TrafficOffloader


class LTEController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {'wsgi': WSGIApplication}

    def __init__(self, *args, **kwargs):
        super(LTEController, self).__init__(*args, **kwargs)
        
        # Core components
        self.mac_to_port = {}
        self.datapaths = {}
        self.predictor = ChannelPredictor()
        self.offloader = None
        
        # Windowed metrics (30 samples for BLSTM)
        self.lte_rssi_window = deque(maxlen=30)
        self.lte_pdr_window = deque(maxlen=30)
        self.wifi_rssi_window = deque(maxlen=30)
        self.wifi_pdr_window = deque(maxlen=30)
        
        # Current state
        self.ue_position = [20.0, 50.0, 0.0]
        self.current_rssi_lte = -60.0
        self.current_pdr_lte = 0.95
        self.current_rssi_wifi = -70.0
        self.current_pdr_wifi = 0.90
        
        # Algorithm 1 parameters (Table 10, Section III-B)
        self.T_c = 15.0
        self.V_total = 150.0
        self.V_remaining = 150.0
        self.flow_detected = False
        self.monitored_flows = set()
        self.flow_stats = {}
        self.flow_start_time = {}
        self.last_poll_time = time.time()
        
        # Algorithm 2: Per-flow flowlet tracking
        self.packet_times = {}
        self.flowlet_counters = {}
        self.packet_counters = {}
        # Application Identification Module (Figure 2)
        self.application_profiles = {
            'video_1080p': {
                'ports': [5001],
                'min_rate_mbps': 5.0,
                'max_delay_ms': 50,
                'qos_class': 'high',
                'description': '1080p Video Streaming'
            },
            'video_720p': {
                'ports': [5002],
                'min_rate_mbps': 3.0,
                'max_delay_ms': 100,
                'qos_class': 'medium',
                'description': '720p Video Streaming'
            },
            'web': {
                'ports': [80, 443, 8080],
                'min_rate_mbps': 1.0,
                'max_delay_ms': 200,
                'qos_class': 'medium',
                'description': 'Web Browsing'
            },
            'voip': {
                'ports': [5060, 5061],
                'min_rate_mbps': 0.1,
                'max_delay_ms': 20,
                'qos_class': 'critical',
                'description': 'Voice over IP'
            },
            'default': {
                'ports': [],
                'min_rate_mbps': 2.0,
                'max_delay_ms': 100,
                'qos_class': 'low',
                'description': 'Best Effort'
            }
        }
        
        # Authentication and Charging Module (Figure 2)
        self.authenticated_ues = {}  # {ue_mac: {'auth_time':,'quota_mb':,'used_mb':}}
        self.charging_records = []   # List of charging events
        
        # Table 10 parameters
        self.LTE_BANDWIDTH_MHZ = 10
        self.NUM_PRB = 100
        self.VIDEO_BITRATE_MBPS = 5.0
        self.RTT_MS = 50
        self.RSRP_THRESHOLD_DBM = -90
        
        # WiFi controller URLs
        self.wifi_load_url = "http://127.0.0.1:8081/wifi_load"
        self.wifi_offload_url = "http://127.0.0.1:8081/offload_confirm"
        
        # Logging
        this_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.abspath(os.path.join(this_dir, "..", ".."))
        self.results_dir = os.path.join(project_root, "results")
        os.makedirs(self.results_dir, exist_ok=True)
        self.csv_path = os.path.join(self.results_dir, "lte_log.csv")
        self.flowlet_log_path = os.path.join(self.results_dir, "flowlet_log.csv")
        self._init_csv()

        # REST API
        wsgi = kwargs['wsgi']
        wsgi.register(LteRestController, {'lte_app': self})
        
        self._print_header()
        
        # Start monitoring threads
        self.monitor_thread = hub.spawn(self._algorithm_1_loop)
        self.stats_thread = hub.spawn(self._poll_flow_stats_loop)
        self.group_stats_thread = hub.spawn(self._poll_group_stats_loop)

    def _init_csv(self):
        """Initialize log CSVs"""
        with open(self.csv_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'timestamp', 'pos_x', 'pos_y',
                'lte_rssi', 'lte_pdr', 'wifi_rssi', 'wifi_pdr',
                'pred_lte_tput', 'pred_wifi_tput', 'pred_quality',
                'wifi_load', 'alpha', 'T_LTE', 'V_LTE', 'V_WiFi',
                'decision', 'flowlet_id'
            ])
        
        with open(self.flowlet_log_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['timestamp', 'flow_key', 'flowlet_id', 'gap_ms', 
                           'is_new_flowlet', 'packet_count'])
    
    def _print_header(self):
        print("\n" + "="*80)
        print("  LTE SDN CONTROLLER - ALGORITHM 1 & 2")
        print("  Port: 6653 | REST: 8080")
        print("  Prediction: BLSTM | Offload: Flowlet-based Multipath")
        print("  Flowlet Threshold: Δ = 50ms (Algorithm 2 Line 4)")
        print("="*80 + "\n")

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        
        self.datapaths[datapath.id] = datapath
        self.logger.info("[LTE] Switch connected: DPID=0x%016x", datapath.id)
        
        # Initialize traffic offloader
        if self.offloader is None and datapath.id == 1:
            self.offloader = TrafficOffloader(datapath, self.logger)
        
        # Table-miss flow
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, 
                                         ofproto.OFPCML_NO_BUFFER)]
        self._add_flow(datapath, 0, match, actions)

    def _add_flow(self, datapath, priority, match, actions, idle=0, hard=0):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath, priority=priority, match=match,
            instructions=inst, idle_timeout=idle, hard_timeout=hard
        )
        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        """Algorithm 2: Packet processing with per-packet flowlet detection"""
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
        
        # Flow detection (video on port 5001)
        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        if ip_pkt:
            udp_pkt = pkt.get_protocol(udp.udp)
            if udp_pkt:
                flow_key = (ip_pkt.src, ip_pkt.dst)
                
                if flow_key not in self.monitored_flows:
                    # Application Identification
                    app_name, app_profile = self.identify_application(
                        udp_pkt.src_port, udp_pkt.dst_port
                    )
                    
                    self.monitored_flows.add(flow_key)
                    self.flow_start_time[flow_key] = time.time()
                    self.flow_detected = True
                    
                    # Use app-specific requirements
                    #self.T_c = app_profile['max_delay_ms'] / 1000.0  # Convert to seconds
                    self.VIDEO_BITRATE_MBPS = app_profile['min_rate_mbps']
                    
                    self.logger.info("[FLOW] Detected: %s → %s (App: %s, Min Rate: %.1f Mbps)",
                                   ip_pkt.src, ip_pkt.dst, app_name, app_profile['min_rate_mbps'])
                    self.flow_stats[flow_key] = {'byte_count': 0, 'packet_count': 0}
                    self.packet_counters[flow_key] = 0
                    self.flowlet_counters[flow_key] = 0
                
                # Algorithm 2 Lines 2-4: Per-packet flowlet detection
                if flow_key in self.monitored_flows:
                    current_time = time.time()
                    self.packet_counters[flow_key] += 1
                    
                    if flow_key not in self.packet_times:
                        self.packet_times[flow_key] = current_time
                        is_new_flowlet = True
                        gap_ms = 0.0
                    else:
                        gap = current_time - self.packet_times[flow_key]
                        gap_ms = gap * 1000
                        is_new_flowlet = (gap > 0.05)  # Δ = 50ms
                        self.packet_times[flow_key] = current_time
                    
                    if is_new_flowlet:
                        self.flowlet_counters[flow_key] += 1
                        flowlet_id = self.flowlet_counters[flow_key]
                        
                        # Log flowlet detection
                        if flowlet_id % 5 == 0:  # Log every 5th flowlet
                            self.logger.info(
                                "[FLOWLET] Flow %s → %s: Flowlet #%d (gap=%.1fms, pkt#%d)",
                                flow_key[0], flow_key[1], flowlet_id, gap_ms,
                                self.packet_counters[flow_key]
                            )
                        
                        # Log to CSV
                        self._log_flowlet(flow_key, flowlet_id, gap_ms, 
                                        is_new_flowlet, self.packet_counters[flow_key])
        
        # Forwarding
        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD
        
        actions = [parser.OFPActionOutput(out_port)]
        
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)
            self._add_flow(datapath, 1, match, actions, idle=30)
        
        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data
        
        out = parser.OFPPacketOut(
            datapath=datapath, buffer_id=msg.buffer_id,
            in_port=in_port, actions=actions, data=data
        )
        datapath.send_msg(out)

    def _log_flowlet(self, flow_key, flowlet_id, gap_ms, is_new, pkt_count):
        """Log flowlet detection to CSV"""
        try:
            with open(self.flowlet_log_path, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    time.time(),
                    f"{flow_key[0]}→{flow_key[1]}",
                    flowlet_id, gap_ms, is_new, pkt_count
                ])
        except:
            pass

    def _poll_flow_stats_loop(self):
        """Continuously poll flow stats (every 2s)"""
        while True:
            hub.sleep(2)
            for dp in self.datapaths.values():
                parser = dp.ofproto_parser
                req = parser.OFPFlowStatsRequest(dp)
                dp.send_msg(req)

    def _poll_group_stats_loop(self):
        """Poll group statistics to verify flowlet distribution"""
        hub.sleep(15)  # Wait for group installation
        
        while True:
            hub.sleep(10)
            for dp in self.datapaths.values():
                if self.offloader and self.offloader.group_installed:
                    parser = dp.ofproto_parser
                    req = parser.OFPGroupStatsRequest(dp, 0, group_id=1)
                    dp.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPGroupStatsReply, MAIN_DISPATCHER)
    def group_stats_reply_handler(self, ev):
        """Log group bucket statistics (Algorithm 2 verification)"""
        for stat in ev.msg.body:
            if stat.group_id == 1:
                self.logger.info("[GROUP STATS] group_id=1 (SELECT type)")
                
                for i, bucket in enumerate(stat.bucket_stats):
                    path = "LTE" if i == 0 else "WiFi"
                    self.logger.info(
                        "[GROUP STATS]   Bucket %d (%s): packets=%d, bytes=%d",
                        i, path, bucket.packet_count, bucket.byte_count
                    )
                
                # Calculate distribution ratio
                if len(stat.bucket_stats) == 2:
                    total_pkts = sum(b.packet_count for b in stat.bucket_stats)
                    if total_pkts > 0:
                        lte_ratio = stat.bucket_stats[0].packet_count / total_pkts
                        wifi_ratio = stat.bucket_stats[1].packet_count / total_pkts
                        self.logger.info(
                            "[GROUP STATS]   Distribution: LTE=%.1f%%, WiFi=%.1f%%",
                            lte_ratio * 100, wifi_ratio * 100
                        )

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply_handler(self, ev):
        """Update flow statistics"""
        current_time = time.time()
        
        for stat in ev.msg.body:
            if stat.priority == 0 or stat.packet_count == 0:
                continue
            
            match = stat.match
            if 'ipv4_src' in match and 'ipv4_dst' in match:
                flow_key = (match['ipv4_src'], match['ipv4_dst'])
                
                if flow_key in self.monitored_flows:
                    prev = self.flow_stats.get(flow_key, {})
                    prev_bytes = prev.get('byte_count', 0)
                    
                    self.flow_stats[flow_key] = {
                        'byte_count': stat.byte_count,
                        'packet_count': stat.packet_count,
                        'duration': stat.duration_sec
                    }
                    
                    time_delta = current_time - self.last_poll_time
                    if time_delta > 0 and prev_bytes > 0:
                        byte_delta = stat.byte_count - prev_bytes
                        tput_mbps = (byte_delta * 8) / (time_delta * 1e6)
                        self.flow_stats[flow_key]['throughput'] = tput_mbps
        
        self.last_poll_time = current_time

    def _algorithm_1_loop(self):
        """Main loop: Algorithm 1 implementation"""
        self.logger.info("[ALGORITHM 1] Started")
        iteration = 0
        
        while not self.flow_detected:
            hub.sleep(2)
            if iteration % 5 == 0:
                self.logger.info("[ALGORITHM 1] Waiting for flow detection...")
            iteration += 1
        
        self.logger.info("[ALGORITHM 1] Flow detected, starting monitoring\n")
        cycle = 0
        
        while True:
            hub.sleep(5)
            cycle += 1
            
            print(f"\n{'='*80}")
            print(f"  ALGORITHM 1 - CYCLE #{cycle}")
            print(f"{'='*80}\n")
            
            # Step 1: Update metrics windows
            if len(self.lte_rssi_window) >= 30:
                # Step 2: Query WiFi load (Equation 3)
                wifi_load = self._query_wifi_load()
                
                # Step 3: Calculate T_LTE (Equation 7)
                lte_tput_mbps = self._calculate_lte_throughput()
                T_LTE = self._calculate_T_LTE(self.V_remaining, lte_tput_mbps)
                
                self.logger.info("[STEP 3] T_LTE = %.2f sec (Eq 7: V/D_LTE)", T_LTE)
                self.logger.info("[STEP 3] T_c = %.2f sec (Video threshold)", self.T_c)
                
                # Step 4: Predict channel quality using BLSTM
                pred_lte_tput, pred_lte_quality = self.predictor.predict_throughput(
                    'lte', list(self.lte_rssi_window), list(self.lte_pdr_window)
                )
                pred_wifi_tput, pred_wifi_quality = self.predictor.predict_throughput(
                    'wifi', list(self.wifi_rssi_window), list(self.wifi_pdr_window)
                )
                
                quality_label, confidence = classify_link(
                    self.current_rssi_lte, self.current_pdr_lte
                )
                
                indicator = "🟢" if quality_label == 'Good' else (
                    "🟡" if quality_label == 'Intermediate' else "🔴"
                )
                self.logger.info("[STEP 4] %s LTE Quality: %s (%.1f%% conf)",
                               indicator, quality_label, confidence*100)
                self.logger.info("[STEP 4] Predicted: LTE=%.2f Mbps, WiFi=%.2f Mbps",
                               pred_lte_tput, pred_wifi_tput)
                
                # Step 5-7: Decision logic
                decision = "LTE_ONLY"
                alpha = 0.0
                V_LTE = 0.0
                V_WiFi = 0.0
                flowlet_id = None
                
                if T_LTE < self.T_c:
                    # Algorithm 1 Line 10: Check if quality = BAD class (Table 8)
                    if quality_label == 'Bad':
                        self.logger.warning("[STEP 6] Quality=%s → Initiate offload", 
                                          quality_label)
                        decision = "OFFLOAD"
                        alpha, V_LTE, V_WiFi, flowlet_id = self._execute_offload(
                            wifi_load, pred_lte_tput, pred_wifi_tput
                        )
                    else:
                        self.logger.info("[STEP 6] Quality=%s → LTE only", quality_label)
                else:
                    self.logger.warning("[STEP 8] T_LTE ≥ T_c → Offload required")
                    decision = "OFFLOAD"
                    alpha, V_LTE, V_WiFi, flowlet_id = self._execute_offload(
                        wifi_load, pred_lte_tput, pred_wifi_tput
                    )
                
                transmitted_MB = (lte_tput_mbps / 8.0) * 5
                self.V_remaining = max(0.0, self.V_remaining - transmitted_MB)
                
                self.logger.info("[PROGRESS] Transmitted: %.2f MB | Remaining: %.2f MB",
                               transmitted_MB, self.V_remaining)
                
                self._log_decision(
                    pred_lte_tput, pred_wifi_tput, quality_label,
                    wifi_load, alpha, T_LTE, V_LTE, V_WiFi,
                    decision, flowlet_id
                )
                
                if self.V_remaining <= 0.0:
                    self.logger.info("[COMPLETE] ✅ Flow transfer completed\n")
                    break
            
            else:
                self.logger.info("[WARMUP] Collecting samples: %d/30",
                               len(self.lte_rssi_window))

    def _calculate_lte_throughput(self):
        """Calculate LTE throughput using Equations 4-5"""
        # Equation 11: RSRP from RSSI
        rsrp = self.current_rssi_lte - 10 * math.log10(12 * self.NUM_PRB)
        
        # Equation 4: Shannon capacity
        # SNR = RSRP - Noise_Power (thermal noise floor at -174 dBm/Hz + 10*log10(BW))
        noise_power_dbm = -174 + 10 * math.log10(self.LTE_BANDWIDTH_MHZ * 1e6)  # Thermal noise
        snr_db = rsrp - noise_power_dbm
        snr_linear = max(0.01, 10 ** (snr_db / 10.0))
        capacity_mbps = self.LTE_BANDWIDTH_MHZ * math.log2(snr_linear + 1.0)
        
        # Equation 5: R = R_LTE × N_PRB
        R_per_prb = capacity_mbps / self.NUM_PRB  # Rate per PRB
        allocated_prb_count = int(self.NUM_PRB * 0.8)  # Allocate 80 PRBs out of 100
        R_user = R_per_prb * allocated_prb_count  # Equation 5
        
        # Apply PDR factor
        throughput = R_user * self.current_pdr_lte
        
        return max(2.0, min(20.0, throughput))

    def _calculate_T_LTE(self, volume_mb, data_rate_mbps):
        """Equation 7: T_LTE = V / D_LTE_avg"""
        data_rate_mbps_actual = data_rate_mbps / 8.0
        if data_rate_mbps_actual > 0:
            return volume_mb / data_rate_mbps_actual
        return 999.0

    def _execute_offload(self, wifi_load, pred_lte_tput, pred_wifi_tput):
        """Steps 11-14: Execute Algorithm 2 (Flowlet-based multipath)"""
        
        print(f"\n{'─'*80}")
        print("  EXECUTING OFFLOAD (ALGORITHM 2)")
        print(f"{'─'*80}\n")
        
        total_tput = pred_lte_tput + pred_wifi_tput
        if total_tput > 0:
            alpha = (pred_wifi_tput / total_tput) * (1 - wifi_load)
        else:
            alpha = 0.0
        
        alpha = max(0.0, min(1.0, alpha))
        
        # Calculate T_LTE for remaining volume
        T_LTE = self._calculate_T_LTE(self.V_remaining, pred_lte_tput)
        
        # Equation 9: T'_LTE = T_c - T_LTE
        if T_LTE < self.T_c:
            T_prime_LTE = self.T_c - T_LTE  # Time available after LTE transfer
        else:
            T_prime_LTE = 0.0  # No time left, must offload immediately
        
        # Equation 8: V_LTE = D_LTE_avg × T'_LTE (if time remains) or use T_c
        if T_prime_LTE > 0:
            V_LTE = (pred_lte_tput / 8.0) * T_prime_LTE
        else:
            V_LTE = (pred_lte_tput / 8.0) * self.T_c
        
        # Equation 10: V_WiFi = V - V_LTE
        V_WiFi = max(0.0, self.V_remaining - V_LTE)
        
        self.logger.info("[OFFLOAD] Alpha = %.3f (WiFi ratio)", alpha)
        self.logger.info("[OFFLOAD] V_LTE = %.2f MB (Eq 8)", V_LTE)
        self.logger.info("[OFFLOAD] V_WiFi = %.2f MB (Eq 10)", V_WiFi)
        
        if V_WiFi < 1.0:
            self.logger.info("[OFFLOAD] V_WiFi too small, LTE only")
            return 0.0, V_LTE, 0.0, None
        
        if not self._exchange_credentials():
            self.logger.error("[OFFLOAD] ❌ Credential exchange failed")
            return 0.0, V_LTE, 0.0, None
        
        flowlet_id = int(time.time() * 1000) % 100000
        self.logger.info("[OFFLOAD] Flowlet ID: %d (Δ > 50ms)", flowlet_id)
        
        # Algorithm 2 Step 13-14: Install/update group table
        if self.offloader:
            # Get current flow key
            flow_key = list(self.monitored_flows)[0] if self.monitored_flows else None
            
            success = self.offloader.install_or_update_group(
                flow_key, alpha, pred_lte_tput, pred_wifi_tput
            )
            
            if success:
                self.logger.info("[OFFLOAD] ✅ Group table deployed")
                
                # Install flow rule to use group table (CRITICAL FIX)
                self._install_group_flow_rule()
                
                return alpha, V_LTE, V_WiFi, flowlet_id
            else:
                self.logger.error("[OFFLOAD] ❌ Group table failed")
        
        return 0.0, V_LTE, 0.0, None

    def _install_group_flow_rule(self):
        """Install flow rule to direct traffic through group table"""
        if not self.datapaths:
            return
        
        dp = list(self.datapaths.values())[0]
        parser = dp.ofproto_parser
        
        # Match video flow to sta1 on port 5001
        match = parser.OFPMatch(
            eth_type=0x0800,
            ipv4_dst='10.0.0.2',
            ip_proto=17,
            udp_dst=5001
        )
        
        # Action: use group table (not direct output port)
        actions = [parser.OFPActionGroup(group_id=1)]
        
        # High priority to override L2 forwarding
        self._add_flow(dp, 10, match, actions, idle=0, hard=120)
        
        self.logger.info("[FLOW RULE] Installed: ipv4_dst=10.0.0.2, udp_dst=5001 → group_id=1")

    def _exchange_credentials(self):
        """Figure 3: Step 6-7 - Exchange UE credentials with WiFi controller"""
        try:
            ue_mac = '00:00:00:00:00:02'
            
            # Authenticate UE if not already authenticated
            if ue_mac not in self.authenticated_ues:
                credentials = {'auth_token': 'TOKEN_000000000002'}
                if not self.authenticate_ue(ue_mac, credentials):
                    return False
            
            payload = {
                'ue_id': 'sta1',
                'ue_mac': ue_mac,
                'flow_id': 'h_svr→sta1:5001',
                'requested_bw': 5.0,
                'auth_token': 'TOKEN_000000000002'
            }
            
            resp = requests.post(self.wifi_offload_url, json=payload, timeout=2)
            if resp.status_code == 200:
                result = resp.json()
                if result.get('success'):
                    self.logger.info("[REST] WiFi confirmed: %s", 
                                   result.get('message', 'OK'))
                    return True
            
            return False
        except Exception as e:
            self.logger.error("[REST] Exception: %s", str(e))
            return False

    def _query_wifi_load(self):
        """Equation 3: Query WiFi average throughput / load"""
        try:
            resp = requests.get(self.wifi_load_url, timeout=2)
            if resp.status_code == 200:
                data = resp.json()
                load = data.get('load', 0.30)
                self.logger.info("[STEP 2] WiFi load = %.3f (Eq 3)", load)
                return load
        except:
            pass
        
        self.logger.warning("[STEP 2] WiFi unreachable, using default=0.30")
        return 0.30

    def _log_decision(self, pred_lte, pred_wifi, quality, wifi_load, 
                     alpha, T_LTE, V_LTE, V_WiFi, decision, flowlet_id):
        """Log decision to CSV"""
        try:
            with open(self.csv_path, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    time.time(),
                    self.ue_position[0], self.ue_position[1],
                    self.current_rssi_lte, self.current_pdr_lte,
                    self.current_rssi_wifi, self.current_pdr_wifi,
                    pred_lte, pred_wifi, quality,
                    wifi_load, alpha, T_LTE, V_LTE, V_WiFi,
                    decision, flowlet_id or ''
                ])
        except Exception as e:
            self.logger.error("[CSV] Error: %s", str(e))
            
    def identify_application(self, src_port, dst_port):
        """Application Identification Module (Figure 2)"""
        for app_name, profile in self.application_profiles.items():
            if app_name == 'default':
                continue
            if src_port in profile['ports'] or dst_port in profile['ports']:
                self.logger.info("[APP-ID] Identified: %s (%s)", 
                               app_name, profile['description'])
                return app_name, profile
        
        # Default profile
        return 'default', self.application_profiles['default']
        
    def authenticate_ue(self, ue_mac, ue_credentials):
        """Authentication Module (Figure 2)"""
        # Simple token-based authentication
        expected_token = f"TOKEN_{ue_mac.replace(':', '').upper()}"
        provided_token = ue_credentials.get('auth_token', '')
        
        if provided_token == expected_token:
            self.authenticated_ues[ue_mac] = {
                'auth_time': time.time(),
                'quota_mb': 1000.0,  # 1GB quota
                'used_mb': 0.0,
                'status': 'active'
            }
            self.logger.info("[AUTH] ✅ UE %s authenticated", ue_mac)
            return True
        else:
            self.logger.warning("[AUTH] ❌ UE %s authentication failed", ue_mac)
            return False
    
    def update_charging(self, ue_mac, data_mb):
        """Update charging record for UE"""
        if ue_mac in self.authenticated_ues:
            self.authenticated_ues[ue_mac]['used_mb'] += data_mb
            
            # Log charging event
            self.charging_records.append({
                'timestamp': time.time(),
                'ue_mac': ue_mac,
                'data_mb': data_mb,
                'total_used': self.authenticated_ues[ue_mac]['used_mb']
            })
            
            # Check quota
            if self.authenticated_ues[ue_mac]['used_mb'] > self.authenticated_ues[ue_mac]['quota_mb']:
                self.logger.warning("[CHARGING] ⚠️ UE %s exceeded quota", ue_mac)


class LteRestController(ControllerBase):
    def __init__(self, req, link, data, **config):
        super(LteRestController, self).__init__(req, link, data, **config)
        self.lte_app = data['lte_app']

    @route('lte', '/ue_metrics', methods=['POST'])
    def post_ue_metrics(self, req, **kwargs):
        """Receive UE metrics from topology"""
        try:
            data = json.loads(req.body.decode('utf-8'))
            
            pos = data.get('position', [20, 50, 0])
            self.lte_app.ue_position = pos
            
            self.lte_app.current_rssi_lte = data.get('lte_rssi', -60.0)
            self.lte_app.current_pdr_lte = data.get('lte_pdr', 0.95)
            self.lte_app.current_rssi_wifi = data.get('wifi_rssi', -70.0)
            self.lte_app.current_pdr_wifi = data.get('wifi_pdr', 0.90)
            
            self.lte_app.lte_rssi_window.append(self.lte_app.current_rssi_lte)
            self.lte_app.lte_pdr_window.append(self.lte_app.current_pdr_lte)
            self.lte_app.wifi_rssi_window.append(self.lte_app.current_rssi_wifi)
            self.lte_app.wifi_pdr_window.append(self.lte_app.current_pdr_wifi)
            
            return Response(content_type='application/json',
                          body=b'{"status": "ok"}')
        except Exception as e:
            return Response(status=400, body=json.dumps({'error': str(e)}).encode())

    @route('lte', '/lte_status', methods=['GET'])
    def get_lte_status(self, req, **kwargs):
        """Return current LTE controller status"""
        status = {
            'position': self.lte_app.ue_position,
            'lte_rssi': self.lte_app.current_rssi_lte,
            'lte_pdr': self.lte_app.current_pdr_lte,
            'wifi_rssi': self.lte_app.current_rssi_wifi,
            'wifi_pdr': self.lte_app.current_pdr_wifi,
            'V_remaining': self.lte_app.V_remaining,
            'flow_detected': self.lte_app.flow_detected,
            'flowlets_detected': sum(self.lte_app.flowlet_counters.values()),
            'packets_processed': sum(self.lte_app.packet_counters.values())
        }
        return Response(content_type='application/json',
                      body=json.dumps(status).encode())