#!/usr/bin/env python3
"""
Channel Quality Predictor using simplified BLSTM logic
Returns: Good, Intermediate, or Bad with confidence score
"""

import numpy as np

class ChannelPredictor:
    def __init__(self):
        self.rssi_good_threshold = -75
        self.rssi_bad_threshold = -90
        self.pdr_good_threshold = 0.85
        self.pdr_bad_threshold = 0.65
        
    def predict(self, rssi, pdr):
        """
        Predict link quality based on RSSI and PDR
        
        Args:
            rssi (float): Received Signal Strength Indicator in dBm
            pdr (float): Packet Delivery Ratio (0-1)
        
        Returns:
            tuple: (quality_class, confidence_score)
        """
        rssi_score = self._score_rssi(rssi)
        pdr_score = self._score_pdr(pdr)
        
        combined_score = (rssi_score + pdr_score) / 2
        
        if combined_score >= 0.75:
            quality = "Good"
            confidence = combined_score
        elif combined_score >= 0.50:
            quality = "Intermediate"
            confidence = combined_score
        else:
            quality = "Bad"
            confidence = 1.0 - combined_score
        
        return quality, confidence
    
    def _score_rssi(self, rssi):
        """Normalize RSSI to 0-1 score"""
        if rssi >= self.rssi_good_threshold:
            return 1.0
        elif rssi <= self.rssi_bad_threshold:
            return 0.0
        else:
            range_val = self.rssi_good_threshold - self.rssi_bad_threshold
            return (rssi - self.rssi_bad_threshold) / range_val
    
    def _score_pdr(self, pdr):
        """Normalize PDR to 0-1 score"""
        if pdr >= self.pdr_good_threshold:
            return 1.0
        elif pdr <= self.pdr_bad_threshold:
            return 0.0
        else:
            range_val = self.pdr_good_threshold - self.pdr_bad_threshold
            return (pdr - self.pdr_bad_threshold) / range_val


def predict_link_quality(rssi, pdr):
    """
    Convenience function for prediction
    """
    predictor = ChannelPredictor()
    return predictor.predict(rssi, pdr)


if __name__ == '__main__':
    predictor = ChannelPredictor()
    
    test_cases = [
        (-65, 0.93, "Good"),
        (-80, 0.78, "Intermediate"),
        (-86, 0.68, "Bad"),
        (-70, 0.88, "Good"),
        (-95, 0.55, "Bad")
    ]
    
    print("Channel Quality Prediction Tests:")
    print("-" * 60)
    for rssi, pdr, expected in test_cases:
        quality, confidence = predictor.predict(rssi, pdr)
        match = "✓" if quality == expected else "✗"
        print(f"{match} RSSI={rssi:>4} dBm, PDR={pdr:.2f} → {quality:13} (conf={confidence:.2f})")