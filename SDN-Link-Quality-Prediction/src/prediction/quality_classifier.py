#!/usr/bin/env python3
"""
Quality Classifier - Section III-A2
Deterministic classification using Tables 4-8 thresholds
"""

class QualityClassifier:
    """
    Deterministic channel quality classifier.
    
    Based on research paper Section III-A2 and Tables 4-8.
    Classifies channel quality into Good, Intermediate, or Bad categories
    using RSSI and PDR thresholds.
    """
    
    def __init__(self):
        """Initialize quality classifier with paper thresholds"""
        
        # General thresholds from Tables 4-5
        self.rssi_good_threshold = -75.0   # dBm
        self.rssi_bad_threshold = -87.0    # dBm
        self.pdr_good_threshold = 0.85     # 85%
        self.pdr_bad_threshold = 0.75      # 75%
        
        # Video-specific thresholds from Table 8
        self.video_rssi_threshold = -87.0  # dBm
        self.video_pdr_threshold = 0.75    # 75%

    def classify(self, rssi: float, pdr: float) -> str:
        """
        Classify channel quality based on RSSI and PDR (Table 6).
        
        Classification rules (priority order):
        1. Bad: PDR ≤ 0.75 OR RSSI ≤ -87 dBm
        2. Good: PDR ≥ 0.85 AND RSSI ≥ -75 dBm
        3. Intermediate: Otherwise
        
        Args:
            rssi: Received Signal Strength Indicator (dBm)
            pdr: Packet Delivery Ratio (0.0 to 1.0)
        
        Returns:
            str: 'Good', 'Intermediate', or 'Bad'
        """
        try:
            # Bad quality: either PDR or RSSI below bad threshold
            if pdr <= self.pdr_bad_threshold or rssi <= self.rssi_bad_threshold:
                return "Bad"
            
            # Good quality: both PDR and RSSI above good threshold
            if pdr >= self.pdr_good_threshold and rssi >= self.rssi_good_threshold:
                return "Good"
            
            # Intermediate quality: between thresholds
            return "Intermediate"
            
        except Exception:
            # Fallback to intermediate if any error
            return "Intermediate"
    
    def classify_for_video(self, rssi: float, pdr: float) -> str:
        """
        Classify channel quality specifically for video applications (Table 8).
        
        Video requires higher quality thresholds to maintain QoE.
        
        Args:
            rssi: Received Signal Strength Indicator (dBm)
            pdr: Packet Delivery Ratio (0.0 to 1.0)
        
        Returns:
            str: 'Good', 'Intermediate', or 'Bad'
        """
        # Bad quality for video: below video thresholds
        if rssi <= self.video_rssi_threshold or pdr <= self.video_pdr_threshold:
            return "Bad"
        
        # Good quality for video: well above thresholds
        elif rssi >= -75.0 and pdr >= 0.85:
            return "Good"
        
        # Intermediate quality for video
        else:
            return "Intermediate"
    
    def compute_rsrp(self, rssi: float, n_prb: int = 100) -> float:
        """
        Compute RSRP (Reference Signal Received Power) from RSSI.
        
        Based on Equation 11 from the paper:
        RSRP = RSSI - 10*log10(12 * N_PRB)
        
        Args:
            rssi: Received Signal Strength Indicator (dBm)
            n_prb: Number of Physical Resource Blocks (default: 100)
        
        Returns:
            float: RSRP in dBm
        """
        import math
        rsrp = rssi - 10 * math.log10(12 * n_prb)
        return rsrp