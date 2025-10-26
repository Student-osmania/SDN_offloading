#!/usr/bin/env python3
"""
Ryu WiFi Controller - Resource Manager
Port: 6654, REST: 8081
"""

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types
from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from webob import Response
import json
import time
import csv
import os

wifi_controller_instance = None

class WiFiController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {'wsgi': WSGIApplication}
    
    def __init__(self, *args, **kwargs):
        super(WiFiController, self).__init__(*args, **kwargs)
        
        # MAC to port mapping
        self.mac_to_port = {}
        self.datapaths = {}
        
        # WiFi network parameters
        self.wifi_load = 0.25
        self.connected_clients = 5  # sta5-sta9 initially
        self.max_clients = 20
        
        # Offloaded UEs tracking
        self.offloaded_ues = {}
        self.allocated_resources = {}
        
        # Make instance globally accessible for REST API
        global wifi_controller_instance
        wifi_controller_instance = self
        
        # REST API
        wsgi = kwargs['wsgi']
        wsgi.register(WiFiRESTController, {'wifi_controller': self})
        
        # CSV logging
        self.csv_file = 'results/wifi_log.csv'
        os.makedirs('results', exist_ok=True)
        with open(self.csv_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['timestamp', 'event', 'ue_id', 'wifi_load', 
                           'allocated_ip', 'bandwidth_mbps'])
        
        self._print_header()
    
    def _print_header(self):
        print("\n" + "="*80)
        print("  WIFI CONTROLLER - OFFLOAD RESOURCE MANAGER")
        print("  Port: 6654 | REST API: 8081")
        print("="*80 + "\n")
    
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        
        self.datapaths[datapath.id] = datapath
        self.logger.info("[WiFi] AP DPID: 0x%016x connected", datapath.id)
        
        # Install table-miss flow entry
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, 
                                         ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)
        
        self.logger.info("[WiFi] ✅ WiFi network ready")
    
    def add_flow(self, datapath, priority, match, actions):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=priority, 
                               match=match, instructions=inst)
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
        
        # Forwarding logic
        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD
        
        actions = [parser.OFPActionOutput(out_port)]
        
        # Install flow to avoid packet_in next time
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)
            self.add_flow(datapath, 1, match, actions)
        
        # Send packet out
        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=data)
        datapath.send_msg(out)
    
    def get_wifi_load(self):
        """Calculate current WiFi load"""
        if self.connected_clients > 0:
            self.wifi_load = min(self.connected_clients / self.max_clients, 1.0)
        return round(self.wifi_load, 2)
    
    def handle_offload_request(self, src_ip, dst_ip, ue_credentials):
        """Handle offload request from LTE controller"""
        
        ue_id = ue_credentials.get('mac', 'unknown')
        
        print(f"\n╔{'═'*78}╗")
        print("║ OFFLOAD REQUEST RECEIVED".ljust(79) + "║")
        print(f"╚{'═'*78}╝\n")
        
        self.logger.info("[WiFi] 🔥 Offload request for UE %s", ue_id)
        self.logger.info("[WiFi]    └─ Flow: %s → %s", src_ip, dst_ip)
        
        # Verify UE credentials
        if not self._verify_ue_credentials(ue_credentials):
            self.logger.error("[WiFi] ❌ Authentication FAILED")
            self._log_to_csv('AUTH_FAILED', ue_id, self.get_wifi_load(), '', 0.0)
            return {'success': False, 'reason': 'authentication_failed'}
        
        self.logger.info("[WiFi] ✅ Credentials verified")
        
        # Check WiFi capacity
        current_load = self.get_wifi_load()
        self.logger.info("[WiFi] 📊 Current load = %.2f", current_load)
        
        if current_load >= 0.85:
            self.logger.error("[WiFi] ❌ WiFi OVERLOADED")
            self._log_to_csv('REJECTED', ue_id, current_load, '', 0.0)
            return {
                'success': False, 
                'reason': 'capacity_exceeded', 
                'current_load': current_load
            }
        
        # Select WiFi gateway
        gateway_ap = self._select_wifi_gateway()
        if not gateway_ap:
            return {'success': False, 'reason': 'no_gateway'}
        
        self.logger.info("[WiFi] Gateway selected: %s", gateway_ap)
        
        # Allocate resources
        allocated_ip = self._allocate_wifi_ip(ue_id)
        allocated_bandwidth = self._allocate_bandwidth(ue_id)
        
        self.logger.info("[WiFi] IP allocated: %s", allocated_ip)
        
        # Store offload information
        self.offloaded_ues[ue_id] = {
            'src_ip': src_ip,
            'dst_ip': dst_ip,
            'credentials': ue_credentials,
            'timestamp': time.time()
        }
        
        self.allocated_resources[ue_id] = {
            'ip_address': allocated_ip,
            'gateway': gateway_ap,
            'bandwidth': allocated_bandwidth
        }
        
        # Update client count
        self.connected_clients += 1
        new_load = self.get_wifi_load()
        
        self.logger.info("[WiFi] ✅ Offload ACCEPTED")
        self.logger.info("[WiFi]    ├─ UE: %s", ue_id)
        self.logger.info("[WiFi]    ├─ IP: %s", allocated_ip)
        self.logger.info("[WiFi]    ├─ Bandwidth: %.2f Mbps", allocated_bandwidth)
        self.logger.info("[WiFi]    └─ New load: %.2f", new_load)
        
        self._log_to_csv('ACCEPTED', ue_id, new_load, allocated_ip, allocated_bandwidth)
        
        return {
            'success': True,
            'ue_id': ue_id,
            'allocated_ip': allocated_ip,
            'gateway': gateway_ap,
            'bandwidth_mbps': allocated_bandwidth,
            'timestamp': time.time()
        }
    
    def _verify_ue_credentials(self, credentials):
        """Verify UE credentials"""
        return 'mac' in credentials and 'auth_token' in credentials
    
    def _select_wifi_gateway(self):
        """Select WiFi gateway AP"""
        if self.datapaths:
            return f"ap_{list(self.datapaths.keys())[0]}"
        return None
    
    def _allocate_wifi_ip(self, ue_id):
        """Allocate IP address for offloaded UE"""
        ip_suffix = (hash(ue_id) % 200) + 20
        return f"10.0.2.{ip_suffix}"
    
    def _allocate_bandwidth(self, ue_id):
        """Allocate bandwidth for offloaded UE"""
        available_bandwidth = 50.0  # Mbps
        current_users = max(1, len(self.offloaded_ues) + 1)
        return available_bandwidth / current_users
    
    def _log_to_csv(self, event, ue_id, wifi_load, allocated_ip='', bandwidth=0.0):
        """Log event to CSV file"""
        try:
            with open(self.csv_file, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    time.time(), event, ue_id, f"{wifi_load:.3f}",
                    allocated_ip, f"{bandwidth:.2f}"
                ])
        except Exception as e:
            self.logger.error("[CSV] Error: %s", str(e))


class WiFiRESTController(ControllerBase):
    def __init__(self, req, link, data, **config):
        super(WiFiRESTController, self).__init__(req, link, data, **config)
        self.wifi_controller = data['wifi_controller']
    
    @route('wifi', '/wifi_load', methods=['GET'])
    def get_wifi_load(self, req, **kwargs):
        """Return current WiFi load"""
        load = self.wifi_controller.get_wifi_load()
        response_data = {
            'load': load,
            'clients': self.wifi_controller.connected_clients,
            'max_clients': self.wifi_controller.max_clients,
            'timestamp': time.time()
        }
        
        wifi_controller_instance.logger.info("[REST] 📨 GET /wifi_load → %.2f", load)
        
        return Response(content_type='application/json; charset=utf-8',
                       body=json.dumps(response_data).encode('utf-8'))
    
    @route('wifi', '/wifi_offload', methods=['POST'])
    def handle_offload_request(self, req, **kwargs):
        """Handle offload request from LTE controller"""
        try:
            data = json.loads(req.body.decode('utf-8'))
            src_ip = data.get('src_ip')
            dst_ip = data.get('dst_ip')
            ue_credentials = data.get('ue_credentials', {})
            
            wifi_controller_instance.logger.info("[REST] 📨 POST /wifi_offload")
            
            result = self.wifi_controller.handle_offload_request(
                src_ip, dst_ip, ue_credentials
            )
            
            status_code = 200 if result['success'] else 400
            
            return Response(status=status_code, 
                          content_type='application/json; charset=utf-8',
                          body=json.dumps(result).encode('utf-8'))
        except Exception as e:
            wifi_controller_instance.logger.error("[REST] ❌ Error: %s", str(e))
            return Response(status=500, 
                          content_type='application/json; charset=utf-8',
                          body=json.dumps({
                              'success': False, 
                              'error': str(e)
                          }).encode('utf-8'))