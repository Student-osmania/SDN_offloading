#!/usr/bin/env python3
"""
Link Quality Classifier - Threshold-based Classification
Implements: Tables 4-6 (RSSI/PDR thresholds), Table 8 (Video quality)
"""

def classify_link(rssi, pdr):
    """
    Classify link quality using Tables 4-6 thresholds
    Combined priority: PDR first (as per Table 6)
    
    Args:
        rssi: Received Signal Strength Indicator (dBm)
        pdr: Packet Delivery Ratio (0-1)
    
    Returns:
        (label, confidence): Quality label and confidence score
    """
    
    # Table 4: RSSI thresholds
    # Good: ≥ -74 dBm
    # Intermediate: -88 to -74 dBm
    # Bad: ≤ -88 dBm
    
    # Table 5: PDR thresholds
    # Good: ≥ 0.88
    # Intermediate: 0.76 to 0.88
    # Bad: ≤ 0.76
    
    # Table 6: Combined (PDR has priority)
    rssi_norm = normalize_rssi(rssi)
    pdr_norm = pdr
    
    # Combined score: 50% RSSI + 50% PDR
    score = 0.5 * rssi_norm + 0.5 * pdr_norm
    
    # Classification logic (PDR first as per Table 6)
    if pdr >= 0.88 and rssi >= -74:
        label = 'Good'
        confidence = score
    elif pdr <= 0.76 or rssi <= -88:
        label = 'Bad'
        confidence = 1.0 - score
    else:
        label = 'Intermediate'
        confidence = 0.5 + abs(0.5 - score)  # Distance from boundary
    
    return label, confidence


def classify_video_quality(rssi, pdr):
    """
    Table 8: Video quality thresholds for 1080p
    RSSI ≥ -90 dBm, PDR ≥ 0.75
    
    Returns:
        (suitable, label): Whether link is suitable for video + quality label
    """
    
    if rssi >= -90 and pdr >= 0.75:
        if rssi >= -74 and pdr >= 0.88:
            return True, 'Good'
        else:
            return True, 'Acceptable'
    else:
        return False, 'Insufficient'


def normalize_rssi(rssi, rssi_min=-100.0, rssi_max=-50.0):
    """
    Normalize RSSI to [0, 1] range
    
    Args:
        rssi: RSSI value in dBm
        rssi_min: Minimum expected RSSI (default: -100 dBm)
        rssi_max: Maximum expected RSSI (default: -50 dBm)
    
    Returns:
        Normalized RSSI in [0, 1]
    """
    return max(0.0, min(1.0, (rssi - rssi_min) / (rssi_max - rssi_min)))


def get_quality_thresholds():
    """
    Return all quality thresholds from Tables 4-8
    Useful for testing and verification
    """
    return {
        'rssi': {
            'good': -74,
            'bad': -88,
            'video': -90  
        },
        'pdr': {
            'good': 0.88,
            'bad': 0.76,
            'video': 0.75  
        },
        'rsrp': {
            'threshold': -90  
        }
    }
