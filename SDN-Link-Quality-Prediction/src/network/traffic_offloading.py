#!/usr/bin/env python3
"""
Traffic Offloading Module
Implements flowlet-based multipath offloading between LTE and WiFi
"""

from ryu.ofproto import ofproto_v1_3

class TrafficOffloader:
    def __init__(self, datapath_lte, datapath_wifi):
        self.dp_lte = datapath_lte
        self.dp_wifi = datapath_wifi
        self.lte_port = 1
        self.wifi_port = 2
        self.flowlet_timeout = 50  # milliseconds
        
    def execute_offload(self, src_ip, dst_ip, wifi_load, lte_throughput, wifi_throughput):
        """
        Execute traffic offloading based on network conditions
        
        Args:
            src_ip (str): Source IP address
            dst_ip (str): Destination IP address
            wifi_load (float): Current WiFi load (0-1)
            lte_throughput (float): LTE throughput in Mbps
            wifi_throughput (float): WiFi throughput in Mbps
        
        Returns:
            bool: True if offload was executed
        """
        if wifi_load >= 0.75:
            print(f"  WiFi overloaded ({wifi_load:.2f}). Keeping LTE-only.")
            return False
        
        total_throughput = lte_throughput + wifi_throughput
        lte_ratio = lte_throughput / total_throughput
        wifi_ratio = wifi_throughput / total_throughput
        
        print(f"  Offloading: LTE={lte_ratio:.2f}, WiFi={wifi_ratio:.2f}")
        
        self._install_group_table(src_ip, dst_ip, lte_ratio, wifi_ratio)
        
        return True
    
    def _install_group_table(self, src_ip, dst_ip, lte_weight, wifi_weight):
        """
        Install OpenFlow group table for traffic splitting
        """
        if not self.dp_lte:
            print("  Warning: LTE datapath not available")
            return
        
        ofproto = self.dp_lte.ofproto
        parser = self.dp_lte.ofproto_parser
        
        lte_bucket_weight = int(lte_weight * 100)
        wifi_bucket_weight = int(wifi_weight * 100)
        
        buckets = [
            parser.OFPBucket(
                weight=lte_bucket_weight,
                watch_port=ofproto.OFPP_ANY,
                watch_group=ofproto.OFPG_ANY,
                actions=[parser.OFPActionOutput(self.lte_port)]
            ),
            parser.OFPBucket(
                weight=wifi_bucket_weight,
                watch_port=ofproto.OFPP_ANY,
                watch_group=ofproto.OFPG_ANY,
                actions=[parser.OFPActionOutput(self.wifi_port)]
            )
        ]
        
        group_id = 1
        req = parser.OFPGroupMod(
            self.dp_lte,
            ofproto.OFPGC_ADD,
            ofproto.OFPGT_SELECT,
            group_id,
            buckets
        )
        
        try:
            self.dp_lte.send_msg(req)
            print(f"  Group table installed: LTE={lte_bucket_weight}%, WiFi={wifi_bucket_weight}%")
        except Exception as e:
            print(f"  Error installing group table: {e}")
        
        self._install_flow_to_group(src_ip, dst_ip, group_id)
    
    def _install_flow_to_group(self, src_ip, dst_ip, group_id):
        """
        Install flow entry that directs traffic to group table
        """
        if not self.dp_lte:
            return
        
        ofproto = self.dp_lte.ofproto
        parser = self.dp_lte.ofproto_parser
        
        match = parser.OFPMatch(
            eth_type=0x0800,
            ipv4_src=src_ip,
            ipv4_dst=dst_ip
        )
        
        actions = [parser.OFPActionGroup(group_id)]
        
        inst = [parser.OFPInstructionActions(
            ofproto.OFPIT_APPLY_ACTIONS,
            actions
        )]
        
        mod = parser.OFPFlowMod(
            datapath=self.dp_lte,
            priority=100,
            match=match,
            instructions=inst,
            idle_timeout=60,
            hard_timeout=0
        )
        
        try:
            self.dp_lte.send_msg(mod)
            print(f"  Flow rule installed: {src_ip} → {dst_ip} via group {group_id}")
        except Exception as e:
            print(f"  Error installing flow: {e}")
    
    def remove_offload(self, src_ip, dst_ip):
        """
        Remove offloading and return to LTE-only
        """
        if not self.dp_lte:
            return
        
        ofproto = self.dp_lte.ofproto
        parser = self.dp_lte.ofproto_parser
        
        match = parser.OFPMatch(
            eth_type=0x0800,
            ipv4_src=src_ip,
            ipv4_dst=dst_ip
        )
        
        mod = parser.OFPFlowMod(
            datapath=self.dp_lte,
            command=ofproto.OFPFC_DELETE,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            match=match
        )
        
        self.dp_lte.send_msg(mod)
        print(f"  Offload removed: {src_ip} → {dst_ip}")


if __name__ == '__main__':
    print("Traffic Offloading Module")
    print("This module requires Ryu controller context to run.")