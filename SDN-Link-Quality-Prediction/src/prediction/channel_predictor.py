#!/usr/bin/env python3
"""
Channel Quality Predictor - Section III-A
Implements BLSTM-based prediction with Tables 4-6 thresholds
"""

import os
import numpy as np

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

class ChannelPredictor:
    """
    Channel quality predictor using BLSTM or threshold-based classification.
    
    Based on research paper Section III-A and Tables 4-6.
    Uses RSSI and PDR to predict channel quality: Good, Intermediate, or Bad.
    """
    
    def __init__(self, model_path='models/blstm_model.h5'):
        """
        Initialize channel predictor.
        
        Args:
            model_path: Path to pre-trained BLSTM model (optional)
        """
        # Thresholds from Table 4 (RSSI)
        self.rssi_good_threshold = -75.0  # dBm
        self.rssi_bad_threshold = -87.0   # dBm
        
        # Thresholds from Table 5 (PDR)
        self.pdr_good_threshold = 0.85
        self.pdr_bad_threshold = 0.75
        
        # Model configuration
        self.model = None
        self.use_model = False
        
        # Try to load BLSTM model if available
        try:
            from tensorflow import keras
            if os.path.exists(model_path):
                self.model = keras.models.load_model(model_path, compile=False)
                self.use_model = True
                print(f"[ChannelPredictor] ✅ Loaded BLSTM model from {model_path}")
            else:
                print(f"[ChannelPredictor] ⚠️ Model not found at {model_path}")
                print(f"[ChannelPredictor] Using threshold-based prediction")
        except ImportError:
            print("[ChannelPredictor] ⚠️ TensorFlow unavailable")
            print("[ChannelPredictor] Using threshold-based prediction")

    def predict(self, rssi, pdr):
        """
        Predict channel quality class.
        
        Args:
            rssi: Received Signal Strength Indicator (dBm)
            pdr: Packet Delivery Ratio (0.0 to 1.0)
        
        Returns:
            tuple: (quality_class, confidence)
                - quality_class: 'Good', 'Intermediate', or 'Bad'
                - confidence: Prediction confidence (0.0 to 1.0)
        """
        if self.use_model and self.model is not None:
            return self._predict_with_model(rssi, pdr)
        else:
            return self._predict_with_thresholds(rssi, pdr)

    def _predict_with_model(self, rssi, pdr):
        """Predict using BLSTM model"""
        
        # Normalize inputs
        rssi_norm = self._normalize_rssi(rssi)
        pdr_norm = pdr
        
        # Prepare input for BLSTM (shape: [batch, timesteps, features])
        input_data = np.array([[[rssi_norm, pdr_norm]]], dtype=np.float32)
        
        # Predict
        prediction = self.model.predict(input_data, verbose=0)
        
        # Get class with highest probability
        class_idx = np.argmax(prediction[0])
        confidence = float(prediction[0][class_idx])
        
        # Map index to quality class
        quality_map = {0: 'Bad', 1: 'Intermediate', 2: 'Good'}
        quality = quality_map.get(class_idx, 'Intermediate')
        
        return quality, confidence

    def _predict_with_thresholds(self, rssi, pdr):
        """
        Predict using threshold-based classification (Table 6).
        
        This is the fallback method when BLSTM model is not available.
        Based on combined RSSI and PDR thresholds from the paper.
        """
        
        # Good quality: both RSSI and PDR above good thresholds
        if pdr >= self.pdr_good_threshold and rssi >= self.rssi_good_threshold:
            return "Good", 1.0
        
        # Bad quality: either RSSI or PDR below bad thresholds
        elif pdr <= self.pdr_bad_threshold or rssi <= self.rssi_bad_threshold:
            return "Bad", 1.0
        
        # Intermediate quality: between good and bad thresholds
        else:
            # Calculate confidence based on how far from thresholds
            rssi_score = self._score_rssi(rssi)
            pdr_score = self._score_pdr(pdr)
            combined = (rssi_score + pdr_score) / 2.0
            return "Intermediate", combined

    def _normalize_rssi(self, rssi):
        """Normalize RSSI to [0, 1] range for model input"""
        rssi_min, rssi_max = -100.0, -60.0
        return max(0.0, min(1.0, (rssi - rssi_min) / (rssi_max - rssi_min)))

    def _score_rssi(self, rssi):
        """Calculate RSSI score between bad and good thresholds"""
        if rssi >= self.rssi_good_threshold:
            return 1.0
        elif rssi <= self.rssi_bad_threshold:
            return 0.0
        else:
            span = self.rssi_good_threshold - self.rssi_bad_threshold
            return (rssi - self.rssi_bad_threshold) / span

    def _score_pdr(self, pdr):
        """Calculate PDR score between bad and good thresholds"""
        if pdr >= self.pdr_good_threshold:
            return 1.0
        elif pdr <= self.pdr_bad_threshold:
            return 0.0
        else:
            span = self.pdr_good_threshold - self.pdr_bad_threshold
            return (pdr - self.pdr_bad_threshold) / span