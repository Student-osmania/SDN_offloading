#!/usr/bin/env python3
"""
Ryu LTE Controller
Main controller that predicts link quality and triggers offloading
Listens on port 6653
"""

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, ipv4
from ryu.lib import hub
import requests
import random
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from src.prediction.channel_predictor import ChannelPredictor
from src.prediction.quality_classifier import QualityClassifier
from src.network.traffic_offloading import TrafficOffloader


class LTEController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    
    def __init__(self, *args, **kwargs):
        super(LTEController, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {}
        self.predictor = ChannelPredictor()
        self.classifier = QualityClassifier()
        self.offloader = None
        
        self.rssi = -70.0
        self.pdr = 0.90
        self.lte_throughput = 10.0
        self.wifi_throughput = 15.0
        
        self.wifi_controller_url = "http://localhost:8080/wifi_load"
        self.monitoring_interval = 5
        
        self.monitored_flows = {}
        
        self.logger.info("LTE Controller initialized on port 6653")
        
        self.monitor_thread = hub.spawn(self._monitor_loop)
    
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        
        self.datapaths[datapath.id] = datapath
        self.logger.info(f"LTE switch connected: DPID={datapath.id}")
        
        if self.offloader is None:
            self.offloader = TrafficOffloader(datapath, None)
        
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
        
        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        if ip_pkt:
            flow_key = (ip_pkt.src, ip_pkt.dst)
            self.monitored_flows[flow_key] = True
        
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
    
    def _monitor_loop(self):
        """Periodic monitoring and prediction loop"""
        self.logger.info("Starting monitoring loop...")
        
        while True:
            hub.sleep(self.monitoring_interval)
            
            if not self.datapaths:
                continue
            
            self._simulate_metrics()
            
            quality, confidence = self.predictor.predict(self.rssi, self.pdr)
            quality_class = self.classifier.classify(self.rssi, self.pdr)
            
            self.logger.info(f"Metrics: RSSI={self.rssi:.1f} dBm, PDR={self.pdr:.2f}")
            self.logger.info(f"Predicted: {quality} (confidence={confidence:.2f}), Class: {quality_class}")
            
            if quality_class in ['Bad', 'Intermediate']:
                self._handle_poor_quality(quality_class)
            else:
                self.logger.info("Link quality Good - LTE only")
    
    def _simulate_metrics(self):
        """Simulate changing RSSI and PDR values"""
        self.rssi += random.uniform(-3, 3)
        self.rssi = max(-100, min(-60, self.rssi))
        
        self.pdr += random.uniform(-0.05, 0.05)
        self.pdr = max(0.5, min(1.0, self.pdr))
    
    def _handle_poor_quality(self, quality_class):
        """Handle poor link quality by querying WiFi and offloading"""
        self.logger.info(f"Poor quality detected: {quality_class}")
        
        wifi_load = self._query_wifi_load()
        
        if wifi_load is None:
            self.logger.warning("WiFi controller unreachable")
            return
        
        self.logger.info(f"WiFi load: {wifi_load:.2f}")
        
        if quality_class == 'Bad' or (quality_class == 'Intermediate' and wifi_load < 0.75):
            if self.offloader and self.monitored_flows:
                flow_key = list(self.monitored_flows.keys())[0]
                src_ip, dst_ip = flow_key
                
                self.logger.info(f"Triggering offload for {src_ip} → {dst_ip}")
                
                success = self.offloader.execute_offload(
                    src_ip, dst_ip, wifi_load,
                    self.lte_throughput, self.wifi_throughput
                )
                
                if success:
                    self.logger.info("✓ Offload executed successfully")
                else:
                    self.logger.info("✗ Offload not executed (WiFi overloaded)")
        else:
            self.logger.info("WiFi too loaded - staying on LTE")
    
    def _query_wifi_load(self):
        """Query WiFi controller for current load via REST API"""
        try:
            response = requests.get(self.wifi_controller_url, timeout=2)
            if response.status_code == 200:
                data = response.json()
                return data.get('load', 0.5)
        except requests.exceptions.RequestException as e:
            self.logger.debug(f"WiFi query failed: {e}")
        
        return None