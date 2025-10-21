#!/usr/bin/env python3
"""
Quality Classifier - Converts metrics into discrete quality classes
Based on thresholds from Table 8 in the paper
"""

class QualityClassifier:
    def __init__(self):
        self.rssi_thresholds = {
            'good': -75,
            'bad': -90
        }
        self.pdr_thresholds = {
            'good': 0.85,
            'bad': 0.65
        }
    
    def classify(self, rssi, pdr):
        """
        Classify link quality based on RSSI and PDR thresholds
        
        Args:
            rssi (float): RSSI in dBm
            pdr (float): PDR ratio (0-1)
        
        Returns:
            str: 'Good', 'Intermediate', or 'Bad'
        """
        if pdr >= self.pdr_thresholds['good']:
            if rssi >= self.rssi_thresholds['good']:
                return 'Good'
            elif rssi >= self.rssi_thresholds['bad']:
                return 'Intermediate'
            else:
                return 'Bad'
        
        elif pdr >= self.pdr_thresholds['bad']:
            if rssi >= self.rssi_thresholds['good']:
                return 'Intermediate'
            else:
                return 'Bad'
        
        else:
            return 'Bad'
    
    def is_offload_needed(self, quality):
        """
        Determine if offloading is needed based on quality
        
        Args:
            quality (str): Link quality class
        
        Returns:
            bool: True if offloading should be triggered
        """
        return quality in ['Bad', 'Intermediate']
    
    def get_offload_priority(self, quality):
        """
        Get offload priority level
        
        Returns:
            int: 0 (no offload), 1 (check WiFi), 2 (immediate offload)
        """
        priority_map = {
            'Good': 0,
            'Intermediate': 1,
            'Bad': 2
        }
        return priority_map.get(quality, 0)


def classify_link_quality(rssi, pdr):
    """Convenience function"""
    classifier = QualityClassifier()
    return classifier.classify(rssi, pdr)


if __name__ == '__main__':
    classifier = QualityClassifier()
    
    test_data = [
        (-65, 0.93, 'Good', 0),
        (-80, 0.78, 'Intermediate', 1),
        (-86, 0.68, 'Bad', 2),
        (-70, 0.60, 'Bad', 2),
        (-95, 0.90, 'Bad', 2)
    ]
    
    print("Quality Classification Tests:")
    print("-" * 70)
    print(f"{'RSSI':>6} | {'PDR':>5} | {'Expected':^13} | {'Result':^13} | Priority")
    print("-" * 70)
    
    for rssi, pdr, expected, exp_priority in test_data:
        result = classifier.classify(rssi, pdr)
        priority = classifier.get_offload_priority(result)
        match = "✓" if result == expected else "✗"
        print(f"{rssi:>6} | {pdr:>5.2f} | {expected:^13} | {result:^13} {match} | {priority}")