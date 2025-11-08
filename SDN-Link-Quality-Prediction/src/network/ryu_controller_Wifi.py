#!/usr/bin/env python3
"""
WiFi SDN Controller - Load Monitoring & Resource Allocation
Port: 6654, REST API: 8081
Implements: Equation 3 (WiFi load calculation), Figure 3 (message exchange)
"""

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types
from ryu.lib import hub
from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from webob import Response
import time
import csv
import os
import json


class WiFiController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {'wsgi': WSGIApplication}

    def __init__(self, *args, **kwargs):
        super(WiFiController, self).__init__(*args, **kwargs)
        
        self.mac_to_port = {}
        self.datapaths = {}
        self.flow_stats = {}
        self.last_stats_time = time.time()
        
        # Equation 3 parameters
        self.current_load = 0.0
        self.total_throughput_mbps = 0.0
        self.wifi_capacity_mbps = 54.0  # 802.11g max (Table 10)
        
        # Authenticated UEs with credentials
        self.authenticated_ues = {}  # {ue_mac: {'token': ..., 'auth_time': ...}}
        self.valid_tokens = {
            '00:00:00:00:00:02': 'TOKEN_000000000002',  # sta1
            '00:00:00:00:00:03': 'TOKEN_000000000003',  # sta2
            '00:00:00:00:00:04': 'TOKEN_000000000004',  # sta3
        }
        
        # Logging
        this_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.abspath(os.path.join(this_dir, "..", ".."))
        self.results_dir = os.path.join(project_root, "results")
        os.makedirs(self.results_dir, exist_ok=True)
        self.csv_path = os.path.join(self.results_dir, "wifi_log.csv")
        self._init_csv()
        
        # REST API
        wsgi = kwargs['wsgi']
        wsgi.register(WiFiRestController, {'wifi_app': self})
        
        self._print_header()
        
        # Start monitoring thread
        self.monitor_thread = hub.spawn(self._load_monitoring_loop)

    def _init_csv(self):
        """Initialize log CSV"""
        with open(self.csv_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['timestamp', 'load', 'throughput_mbps', 
                           'capacity_mbps', 'num_flows', 'event'])

    def _print_header(self):
        print("\n" + "="*80)
        print("  WiFi SDN CONTROLLER - LOAD MONITORING")
        print("  Port: 6654 | REST API: 8081")
        print("  Equation 3: D_WiFi_avg = (1/K) * Σ L_i")
        print("="*80 + "\n")

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        
        self.datapaths[datapath.id] = datapath
        self.logger.info("[WiFi] Switch connected: DPID=0x%016x", datapath.id)
        
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
        """Standard L2 forwarding"""
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

    def _load_monitoring_loop(self):
        """Equation 3: Continuously monitor WiFi load"""
        self.logger.info("[WiFi] Load monitoring started (Equation 3)")
        
        while True:
            hub.sleep(2)
            
            for dp in self.datapaths.values():
                parser = dp.ofproto_parser
                req = parser.OFPFlowStatsRequest(dp)
                dp.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply_handler(self, ev):
        """Calculate load from flow statistics (Equation 3)"""
        current_time = time.time()
        time_delta = current_time - self.last_stats_time
        
        if time_delta < 0.5:
            return
        
        user_throughputs = []  # Per-user throughput list
        
        for stat in ev.msg.body:
            if stat.priority > 0 and stat.packet_count > 0:
                flow_key = (stat.match.get('eth_src', ''), 
                           stat.match.get('eth_dst', ''))
                
                prev_bytes = self.flow_stats.get(flow_key, {}).get('byte_count', 0)
                byte_delta = stat.byte_count - prev_bytes
                
                self.flow_stats[flow_key] = {
                    'byte_count': stat.byte_count,
                    'packet_count': stat.packet_count
                }
                
                # Calculate per-flow throughput
                if time_delta > 0 and byte_delta > 0:
                    flow_tput_mbps = (byte_delta * 8) / (time_delta * 1e6)
                    user_throughputs.append(flow_tput_mbps)
        
        # Equation 3: D_WiFi_avg = (1/K) * Σ L_i
        if user_throughputs:
            D_wifi_avg = sum(user_throughputs) / len(user_throughputs)
            self.total_throughput_mbps = D_wifi_avg
            self.current_load = min(1.0, D_wifi_avg / self.wifi_capacity_mbps)
        else:
            self.total_throughput_mbps = 0.0
            self.current_load = 0.0
        
        self.last_stats_time = current_time
        
        # Log
        self._log_load(len(user_throughputs), 'MONITOR')

    def _log_load(self, num_flows, event):
        """Log load to CSV"""
        try:
            with open(self.csv_path, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    time.time(),
                    self.current_load,
                    self.total_throughput_mbps,
                    self.wifi_capacity_mbps,
                    num_flows,
                    event
                ])
        except Exception as e:
            self.logger.error("[CSV] Error: %s", str(e))


class WiFiRestController(ControllerBase):
    def __init__(self, req, link, data, **config):
        super(WiFiRestController, self).__init__(req, link, data, **config)
        self.wifi_app = data['wifi_app']

    @route('wifi', '/wifi_load', methods=['GET'])
    def get_wifi_load(self, req, **kwargs):
        """Figure 3 Step 4: Return current WiFi load (Equation 3)"""
        response = {
            'load': self.wifi_app.current_load,
            'throughput_mbps': self.wifi_app.total_throughput_mbps,
            'capacity_mbps': self.wifi_app.wifi_capacity_mbps,
            'gateway': 's_wifi',
            'port': 2
        }
        
        self.wifi_app.logger.info("[REST] Load query: %.3f", 
                                 self.wifi_app.current_load)
        
        return Response(content_type='application/json',
                      body=json.dumps(response).encode('utf-8'))

    @route('wifi', '/offload_confirm', methods=['POST'])
    def offload_confirm(self, req, **kwargs):
        """Figure 3 Steps 6-7: Authenticate UE and confirm offload"""
        try:
            data = json.loads(req.body.decode('utf-8'))
            
            ue_id = data.get('ue_id', 'unknown')
            ue_mac = data.get('ue_mac', '')
            requested_bw = data.get('requested_bw', 5.0)
            
            self.wifi_app.logger.info("[REST] Offload request from LTE")
            self.wifi_app.logger.info("[REST]   UE: %s (MAC: %s)", ue_id, ue_mac)
            self.wifi_app.logger.info("[REST]   Requested BW: %.2f Mbps", requested_bw)
            
            # Step 7: Verify UE credentials
            provided_token = data.get('auth_token', '')
            expected_token = self.wifi_app.valid_tokens.get(ue_mac, None)
            
            if expected_token is None:
                self.wifi_app.logger.error("[REST] ❌ Unknown UE MAC: %s", ue_mac)
                return Response(status=403, 
                              body=json.dumps({'success': False, 'error': 'Unknown UE'}).encode())
            
            if provided_token != expected_token:
                self.wifi_app.logger.error("[REST] ❌ Invalid token for UE: %s", ue_mac)
                return Response(status=403,
                              body=json.dumps({'success': False, 'error': 'Invalid credentials'}).encode())
            
            # Authentication successful
            self.wifi_app.authenticated_ues[ue_mac] = {
                'token': provided_token,
                'auth_time': time.time()
            }
            self.wifi_app.logger.info("[REST] ✅ UE %s authenticated", ue_mac)
            
            # Step 8: Allocate resources
            available_bw = self.wifi_app.wifi_capacity_mbps * (1 - self.wifi_app.current_load)
            allocated_bw = min(requested_bw, available_bw)
            
            self.wifi_app.logger.info("[REST]   Available: %.2f Mbps", available_bw)
            self.wifi_app.logger.info("[REST]   Allocated: %.2f Mbps", allocated_bw)
            
            # Step 10: Send acknowledgment
            response = {
                'success': True,
                'message': 'UE authenticated and resources allocated',
                'allocated_bw': allocated_bw,
                'gateway': 's_wifi',
                'port': 2
            }
            
            self.wifi_app._log_load(len(self.wifi_app.authenticated_ues), 'OFFLOAD_CONFIRM')
            
            return Response(content_type='application/json',
                          body=json.dumps(response).encode('utf-8'))
            
        except Exception as e:
            self.wifi_app.logger.error("[REST] Error: %s", str(e))
            return Response(status=400,
                          body=json.dumps({'success': False, 'error': str(e)}).encode('utf-8'))