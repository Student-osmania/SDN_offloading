#!/usr/bin/env python3
"""
Traffic Offloading Utilities - Algorithm 2 (Flowlet-based Multipath)
Implements: Group table management, per-flow flowlet detection, Equations 8-10
"""

import time
import csv
import os
from datetime import datetime


class TrafficOffloader:
    """Implements Algorithm 2: Flowlet-based traffic splitting with per-flow state"""
    
    def __init__(self, datapath, logger):
        self.datapath = datapath
        self.logger = logger
        self.group_installed = False
        
        # Algorithm 2: Per-flow flowlet tracking
        self.flowlet_times = {}
        self.flowlet_threshold = 0.05  # Δ = 50ms (Algorithm 2, line 4)
        
        # Port assignments (Figure 4 topology)
        self.lte_port = 1
        self.wifi_port = 2
        
        # Logging
        this_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.abspath(os.path.join(this_dir, "..", ".."))
        self.results_dir = os.path.join(project_root, "results")
        os.makedirs(self.results_dir, exist_ok=True)
        self.log_path = os.path.join(self.results_dir, "offload_log.csv")
        self._init_log()
    
    def _init_log(self):
        """Initialize offload log"""
        with open(self.log_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['timestamp', 'flow_key', 'action', 'alpha', 
                           'lte_weight', 'wifi_weight', 'lte_tput', 'wifi_tput', 
                           'flowlet_gap_ms'])
    
    def detect_flowlet(self, flow_key, current_time):
        """Algorithm 2 Line 4: Detect flowlet boundary for specific flow"""
        if flow_key not in self.flowlet_times:
            self.flowlet_times[flow_key] = current_time
            return True, 0.0
        
        gap = current_time - self.flowlet_times[flow_key]
        is_new_flowlet = (gap > self.flowlet_threshold)
        
        if is_new_flowlet:
            self.flowlet_times[flow_key] = current_time
            if self.logger:
                self.logger.info("[FLOWLET] Flow %s: New flowlet (gap=%.3fs > Δ=%.3fs)",
                               flow_key, gap, self.flowlet_threshold)
        
        return is_new_flowlet, gap
    
    def install_or_update_group(self, flow_key, alpha, lte_tput, wifi_tput):
        """Algorithm 2 Lines 8-18: Install/update SELECT group table"""
        
        current_time = time.time()
        is_new_flowlet, gap = self.detect_flowlet(flow_key, current_time)
        
        if not is_new_flowlet and self.group_installed:
            if self.logger:
                self.logger.info("[FLOWLET] Gap=%.3fs < Δ=%.3fs, skip update",
                               gap, self.flowlet_threshold)
            return True
        
        # Algorithm 2 Line 8: Calculate frequencies (weights)
        lte_weight = max(1, int((1 - alpha) * 100))
        wifi_weight = max(1, int(alpha * 100))
        
        if self.logger:
            self.logger.info("[FLOWLET] New flowlet detected (gap=%.3fs)", gap)
            self.logger.info("[FLOWLET] Weights: LTE=%d, WiFi=%d", lte_weight, wifi_weight)
        
        ofproto = self.datapath.ofproto
        parser = self.datapath.ofproto_parser
        
        # Algorithm 2 Lines 12-17: Create buckets
        buckets = [
            parser.OFPBucket(
                weight=lte_weight,
                watch_port=ofproto.OFPP_ANY,
                watch_group=ofproto.OFPG_ANY,
                actions=[parser.OFPActionOutput(self.lte_port)]
            ),
            parser.OFPBucket(
                weight=wifi_weight,
                watch_port=ofproto.OFPP_ANY,
                watch_group=ofproto.OFPG_ANY,
                actions=[parser.OFPActionOutput(self.wifi_port)]
            )
        ]
        
        # Algorithm 2 Line 18: Install or modify group
        if not self.group_installed:
            command = ofproto.OFPGC_ADD
            action = 'INSTALL'
            self.group_installed = True
        else:
            command = ofproto.OFPGC_MODIFY
            action = 'UPDATE'
        
        try:
            req = parser.OFPGroupMod(
                self.datapath,
                command,
                ofproto.OFPGT_SELECT,
                group_id=1,
                buckets=buckets
            )
            self.datapath.send_msg(req)
            
            if self.logger:
                self.logger.info("[GROUP] %s: group_id=1, type=SELECT", action)
            
            # Log to CSV
            self._log_action(flow_key, action, alpha, lte_weight, wifi_weight, 
                           lte_tput, wifi_tput, gap * 1000)
            
            return True
            
        except Exception as e:
            if self.logger:
                self.logger.error("[GROUP] Error: %s", str(e))
            return False
    
    def _log_action(self, flow_key, action, alpha, lte_w, wifi_w, lte_t, wifi_t, gap_ms):
        """Log offload action"""
        try:
            with open(self.log_path, 'a', newline='') as f:
                writer = csv.writer(f)
                flow_str = f"{flow_key[0]}→{flow_key[1]}" if flow_key else "unknown"
                writer.writerow([
                    datetime.now().isoformat(),
                    flow_str, action, alpha, lte_w, wifi_w, lte_t, wifi_t, gap_ms
                ])
        except Exception as e:
            if self.logger:
                self.logger.error("[LOG] Error: %s", str(e))


def log_offload_decision(alpha, throughput_lte, throughput_wifi, action, 
                         results_dir=None):
    """Standalone logging function for external use"""
    this_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(this_dir, "..", ".."))

    # default to project_root/results
    if results_dir is None:
        results_dir = os.path.join(project_root, "results")

    os.makedirs(results_dir, exist_ok=True)
    log_path = os.path.join(results_dir, "offload_decisions.csv")
    
    if not os.path.exists(log_path):
        with open(log_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['timestamp', 'alpha', 'lte_tput', 'wifi_tput', 'action'])
    
    with open(log_path, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.now().isoformat(),
            alpha, throughput_lte, throughput_wifi, action
        ])