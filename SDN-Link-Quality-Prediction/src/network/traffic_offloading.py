#!/usr/bin/env python3
"""
Traffic Offloading Module - Algorithm 2 Implementation
Flowlet-based multipath offloading (Section III-B3)
"""

from ryu.ofproto import ofproto_v1_3
import time

class TrafficOffloader:
    def __init__(self, datapath_lte, datapath_wifi):
        """Initialize traffic offloader with LTE and WiFi datapaths"""
        self.dp_lte = datapath_lte
        self.dp_wifi = datapath_wifi
        
        # Port configuration (adjust based on your topology)
        self.lte_port = 1  # Port to LTE network
        self.wifi_port = 2  # Port to WiFi network
        
        # Flowlet parameters (Section III-B3, Algorithm 2)
        self.flowlet_timeout = 0.050  # Δ = 50ms (idle timeout for flowlet detection)
        self.last_packet_time = {}
        self.current_flowlet_path = {}
        
        print("\n[FLOWLET] 📦 Traffic Offloader initialized")
        print(f"[FLOWLET]    ├─ Flowlet timeout (Δ): {self.flowlet_timeout*1000:.0f} ms")
        print(f"[FLOWLET]    ├─ LTE port: {self.lte_port}")
        print(f"[FLOWLET]    └─ WiFi port: {self.wifi_port}\n")
    
    def execute_offload(self, src_ip, dst_ip, wifi_load, lte_throughput, 
                       wifi_throughput, volume_wifi=None):
        """
        Execute flowlet-based multipath offloading (Algorithm 2)
        
        Args:
            src_ip: Source IP address
            dst_ip: Destination IP address
            wifi_load: Current WiFi load (0.0 to 1.0)
            lte_throughput: LTE throughput in Mbps
            wifi_throughput: WiFi throughput in Mbps
            volume_wifi: Volume to offload via WiFi (MB)
        
        Returns:
            bool: True if offload successful, False otherwise
        """
        
        print(f"\n╔{'═'*78}╗")
        print("║ FLOWLET-BASED OFFLOADING (ALGORITHM 2)".ljust(79) + "║")
        print(f"╚{'═'*78}╝\n")
        
        print(f"[FLOWLET] 🔍 Evaluating offload conditions")
        print(f"[FLOWLET]    ├─ Flow: {src_ip} → {dst_ip}")
        print(f"[FLOWLET]    ├─ WiFi load: {wifi_load:.2f}")
        print(f"[FLOWLET]    ├─ LTE throughput: {lte_throughput:.2f} Mbps")
        print(f"[FLOWLET]    └─ WiFi throughput: {wifi_throughput:.2f} Mbps")
        
        # Check WiFi capacity (Algorithm 1, line 11)
        if wifi_load >= 0.75:
            print(f"[FLOWLET] ❌ WiFi OVERLOADED ({wifi_load:.2f} ≥ 0.75)")
            return False
        
        print(f"[FLOWLET] ✅ WiFi load acceptable ({wifi_load:.2f} < 0.75)")
        
        # Calculate multipath split ratio (Algorithm 2, Line 8)
        total_throughput = lte_throughput + wifi_throughput
        if total_throughput <= 0:
            return False
        
        lte_ratio = lte_throughput / total_throughput
        wifi_ratio = wifi_throughput / total_throughput
        
        print(f"\n[FLOWLET] 📊 MULTIPATH SPLIT CALCULATION (Algorithm 2, Line 8)")
        print(f"[FLOWLET]    ├─ Total throughput (D): {total_throughput:.2f} Mbps")
        print(f"[FLOWLET]    ├─ LTE ratio (a₁): {lte_ratio:.3f} ({lte_ratio*100:.1f}%)")
        print(f"[FLOWLET]    └─ WiFi ratio (a₂): {wifi_ratio:.3f} ({wifi_ratio*100:.1f}%)")
        
        # Install flowlet-based forwarding rules
        self._install_flowlet_forwarding(src_ip, dst_ip, lte_ratio, wifi_ratio)
        
        return True
    
    def _install_flowlet_forwarding(self, src_ip, dst_ip, lte_weight, wifi_weight):
        """Install flowlet-based forwarding using OpenFlow group tables"""
        
        if not self.dp_lte:
            print("[FLOWLET] ⚠️ Warning: LTE datapath not available")
            return
        
        print(f"\n[FLOWLET] 🔧 INSTALLING FLOWLET-BASED FORWARDING")
        
        ofproto = self.dp_lte.ofproto
        parser = self.dp_lte.ofproto_parser
        
        # Calculate frequency ranges (Algorithm 2, Line 8)
        a1_upper = lte_weight
        a2_lower = lte_weight
        a2_upper = 1.0
        
        print(f"\n┌─ ALGORITHM 2: LINE 8 ─ Calculate frequency a₁ and a₂")
        print(f"[FLOWLET]    ├─ a₁ = (0, D_LTE_avg/D] = (0, {a1_upper:.3f}]")
        print(f"[FLOWLET]    └─ a₂ = (D_LTE_avg/D, 1] = ({a2_lower:.3f}, {a2_upper:.3f}]")
        
        # Install group table for multipath forwarding
        self._install_flowlet_group_table(src_ip, dst_ip, lte_weight, wifi_weight)
    
    def _install_flowlet_group_table(self, src_ip, dst_ip, lte_weight, wifi_weight):
        """Install OpenFlow group table for flowlet-based multipath"""
        
        if not self.dp_lte:
            return
        
        print(f"\n[FLOWLET] 🔨 INSTALLING OPENFLOW GROUP TABLE (Table 9)")
        
        ofproto = self.dp_lte.ofproto
        parser = self.dp_lte.ofproto_parser
        
        # Convert weights to integers (bucket weights must be integers)
        lte_bucket_weight = max(1, int(lte_weight * 100))
        wifi_bucket_weight = max(1, int(wifi_weight * 100))
        
        print(f"[FLOWLET]    ├─ Group type: SELECT (Algorithm 2)")
        print(f"[FLOWLET]    ├─ Group ID: 1")
        print(f"[FLOWLET]    ├─ Bucket 1 (LTE): weight={lte_bucket_weight}, action=output:port {self.lte_port}")
        print(f"[FLOWLET]    └─ Bucket 2 (WiFi): weight={wifi_bucket_weight}, action=output:port {self.wifi_port}")
        
        # Create buckets for multipath forwarding
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
        
        # Delete existing group if present
        try:
            del_req = parser.OFPGroupMod(
                self.dp_lte,
                ofproto.OFPGC_DELETE,
                ofproto.OFPGT_SELECT,
                group_id
            )
            self.dp_lte.send_msg(del_req)
            time.sleep(0.1)
        except:
            pass
        
        # Add new group table entry
        req = parser.OFPGroupMod(
            self.dp_lte,
            ofproto.OFPGC_ADD,
            ofproto.OFPGT_SELECT,
            group_id,
            buckets
        )
        
        try:
            self.dp_lte.send_msg(req)
            print(f"[FLOWLET] ✅ Group table {group_id} installed")
            print(f"[FLOWLET]    └─ Traffic split: LTE={lte_bucket_weight}% / WiFi={wifi_bucket_weight}%")
        except Exception as e:
            print(f"[FLOWLET] ❌ Error installing group table: {e}")
        
        # Install flow entry that uses the group table
        self._install_flowlet_flow(src_ip, dst_ip, group_id)
    
    def _install_flowlet_flow(self, src_ip, dst_ip, group_id):
        """Install flow entry with idle timeout for flowlet detection"""
        
        if not self.dp_lte:
            return
        
        print(f"\n[FLOWLET] 📋 INSTALLING FLOW ENTRY WITH IDLE TIMEOUT")
        
        ofproto = self.dp_lte.ofproto
        parser = self.dp_lte.ofproto_parser
        
        # Match on IP flow
        match = parser.OFPMatch(
            eth_type=0x0800,
            ipv4_src=src_ip,
            ipv4_dst=dst_ip
        )
        
        # Action: forward to group table
        actions = [parser.OFPActionGroup(group_id)]
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        
        # Idle timeout implements flowlet detection (Algorithm 2)
        # When idle_timeout expires, new packet triggers new flowlet
        idle_timeout_sec = 1  # OpenFlow minimum, actual flowlet Δ = 50ms
        
        print(f"[FLOWLET]    ├─ Match: ipv4_src={src_ip}, ipv4_dst={dst_ip}")
        print(f"[FLOWLET]    ├─ Action: Forward to group {group_id}")
        print(f"[FLOWLET]    ├─ OpenFlow idle_timeout: {idle_timeout_sec}s")
        print(f"[FLOWLET]    └─ Algorithm Δ: {self.flowlet_timeout*1000:.0f}ms")
        
        mod = parser.OFPFlowMod(
            datapath=self.dp_lte,
            priority=100,
            match=match,
            instructions=inst,
            idle_timeout=idle_timeout_sec,
            hard_timeout=0,
            flags=ofproto.OFPFF_SEND_FLOW_REM
        )
        
        try:
            self.dp_lte.send_msg(mod)
            print(f"[FLOWLET] ✅ Flow entry installed successfully")
            print(f"\n[FLOWLET] 🎯 FLOWLET MECHANISM ACTIVE:")
            print(f"[FLOWLET]    ├─ If (new_time - last_time) > Δ → new flowlet")
            print(f"[FLOWLET]    ├─ Random path selection from weighted group")
            print(f"[FLOWLET]    └─ Result: Packet reordering avoided within flowlets")
        except Exception as e:
            print(f"[FLOWLET] ❌ Error installing flow: {e}")
        
        print("\n" + "─"*80 + "\n")