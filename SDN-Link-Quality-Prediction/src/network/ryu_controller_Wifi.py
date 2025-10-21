#!/usr/bin/env python3
"""
Ryu WiFi Controller
Manages WiFi switch and provides load information via REST API
Listens on port 6654
"""

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types
from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from webob import Response
import json
import time

wifi_controller_instance = None

class WiFiController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {'wsgi': WSGIApplication}
    
    def __init__(self, *args, **kwargs):
        super(WiFiController, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {}
        self.wifi_load = 0.3
        self.connected_clients = 0
        self.max_clients = 20
        
        global wifi_controller_instance
        wifi_controller_instance = self
        
        wsgi = kwargs['wsgi']
        wsgi.register(WiFiRESTController, {'wifi_controller': self})
        
        self.logger.info("WiFi Controller initialized on port 6654")
    
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        
        self.datapaths[datapath.id] = datapath
        self.logger.info(f"WiFi switch connected: DPID={datapath.id}")
        
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)
    
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
        
        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD
        
        actions = [parser.OFPActionOutput(out_port)]
        
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)
            self.add_flow(datapath, 1, match, actions)
        
        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data
        
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=data)
        datapath.send_msg(out)
    
    def get_wifi_load(self):
        """Calculate WiFi load based on connected clients"""
        if self.connected_clients > 0:
            self.wifi_load = min(self.connected_clients / self.max_clients, 1.0)
        else:
            self.wifi_load = 0.3 + (time.time() % 10) * 0.04
        
        return round(self.wifi_load, 2)
    
    def update_client_count(self, count):
        """Update connected client count"""
        self.connected_clients = count
        self.logger.info(f"WiFi clients: {count}, Load: {self.get_wifi_load():.2f}")


class WiFiRESTController(ControllerBase):
    def __init__(self, req, link, data, **config):
        super(WiFiRESTController, self).__init__(req, link, data, **config)
        self.wifi_controller = data['wifi_controller']
    
    @route('wifi', '/wifi_load', methods=['GET'])
    def get_wifi_load(self, req, **kwargs):
        """REST endpoint to get WiFi load"""
        load = self.wifi_controller.get_wifi_load()
        
        response_data = {
            'load': load,
            'clients': self.wifi_controller.connected_clients,
            'max_clients': self.wifi_controller.max_clients,
            'timestamp': time.time()
        }
        
        return Response(content_type='application/json',
                       body=json.dumps(response_data))
    
    @route('wifi', '/wifi_status', methods=['GET'])
    def get_status(self, req, **kwargs):
        """REST endpoint for WiFi status"""
        status = {
            'controller': 'WiFi',
            'switches': len(self.wifi_controller.datapaths),
            'load': self.wifi_controller.get_wifi_load(),
            'status': 'active'
        }
        
        return Response(content_type='application/json',
                       body=json.dumps(status))