#!/usr/bin/env python3
"""
BLSTM-based Channel Quality & Throughput Prediction
Implements: Figure 1 (BLSTM architecture), Equations 1-2, Tables 3-7
"""

import numpy as np
import os
import csv
import json
from datetime import datetime
from tensorflow.keras.models import load_model


class ChannelPredictor:
    """BLSTM-based predictor with dynamic scaler parameters"""
    
    def __init__(self, model_path=None, 
                 scaler_path=None):
        
        this_dir = os.path.dirname(os.path.abspath(__file__))  
        project_root = os.path.abspath(os.path.join(this_dir, "..", "..")) 
        
        if model_path is None:
            model_path = os.path.join(project_root, "src", "models", "blstm_model.h5")
        if scaler_path is None:
            scaler_path = os.path.join(project_root, "config", "scaler_params.json")
        
        # store absolute paths
        self.model_path = os.path.abspath(model_path)
        self.scaler_path = os.path.abspath(scaler_path)
        
        self.results_dir = os.path.join(project_root, "results")
        os.makedirs(self.results_dir, exist_ok=True)
        self.log_path = os.path.join(self.results_dir, 'predict_log.csv')
        self._init_log()
        
        # Load BLSTM model (Figure 1 architecture)
        try:
            # load_model expects file path
            self.model = load_model(self.model_path)
            self.model_loaded = True
            print(f"[PREDICTOR] ✅ BLSTM model loaded: {self.model_path}")
        except Exception as e:
            print(f"[PREDICTOR] ⚠️ Model load failed: {e}")
            print(f"[PREDICTOR] Tried path: {self.model_path}")
            print("[PREDICTOR] Using fallback prediction")
            self.model_loaded = False
            self.model = None
        
        # Load scaler parameters (Table 3 statistics)
        self.scaler_params = self._load_scaler_params(self.scaler_path)
    
    def _init_log(self):
        """Initialize prediction log"""
        try:
            with open(self.log_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['timestamp', 'interface', 'input_rssi', 'input_pdr',
                               'pred_tput', 'pred_quality', 'confidence', 'method'])
        except Exception as e:
            print(f"[PREDICTOR] ❌ Could not init log at {self.log_path}: {e}")
    
    def _load_scaler_params(self, scaler_path):
        """Load or create scaler parameters (Table 3)"""
        if os.path.exists(scaler_path):
            try:
                with open(scaler_path, 'r') as f:
                    params = json.load(f)
                    print(f"[PREDICTOR] Scaler params loaded: {scaler_path}")
                    return params
            except Exception as e:
                print(f"[PREDICTOR] ⚠️ Failed to read scaler params: {e}")
        
        # Default parameters (must be computed from dataset later)
        print("[PREDICTOR] ⚠️ Using default scaler params")
        return {
            'rssi_mean': -75.0,
            'rssi_std': 10.0,
            'pdr_mean': 0.85,
            'pdr_std': 0.15
        }
    
    def _preprocess(self, rssi_series, pdr_series):
        """Table 7: StandardScaler preprocessing"""
        rssi_arr = np.array(rssi_series)
        pdr_arr = np.array(pdr_series)
        
        # Equation: (x - mean) / std
        rssi_scaled = (rssi_arr - self.scaler_params['rssi_mean']) / self.scaler_params['rssi_std']
        pdr_scaled = (pdr_arr - self.scaler_params['pdr_mean']) / self.scaler_params['pdr_std']
        
        # Shape: (1, 30, 2) for BLSTM input
        features = np.column_stack([rssi_scaled, pdr_scaled])
        return features.reshape(1, len(rssi_series), 2)
    
    def predict_throughput(self, interface_name, rssi_series, pdr_series):
        """
        Predict throughput using BLSTM (Figure 1)
        Returns: (predicted_tput_mbps, quality_label)
        """
        
        if len(rssi_series) < 30 or len(pdr_series) < 30:
            # Not enough samples
            return 5.0, 'Intermediate'
        
        last_rssi = rssi_series[-1]
        last_pdr = pdr_series[-1]
        
        # BLSTM prediction (if model loaded)
        if self.model_loaded and self.model is not None:
            try:
                X = self._preprocess(rssi_series, pdr_series)
                
                # Equation 1: Y_t = σ(h_forward, h_backward)
                pred_probs = self.model.predict(X, verbose=0)[0]
                pred_class = np.argmax(pred_probs)
                confidence = float(pred_probs[pred_class])
                
                # Map class to throughput (based on training)
                throughput_map = {0: 2.0, 1: 5.0, 2: 8.0}  # Bad, Intermediate, Good
                quality_map = {0: 'Bad', 1: 'Intermediate', 2: 'Good'}
                
                predicted_tput = throughput_map.get(pred_class, 5.0)
                quality_label = quality_map.get(pred_class, 'Intermediate')
                method = 'BLSTM'
                
            except Exception as e:
                print(f"[PREDICTOR] BLSTM error: {e}, using fallback")
                predicted_tput, quality_label, confidence, method = self._fallback_prediction(
                    last_rssi, last_pdr
                )
        else:
            # Fallback prediction
            predicted_tput, quality_label, confidence, method = self._fallback_prediction(
                last_rssi, last_pdr
            )
        
        # Apply Table 8 video quality thresholds (RSSI ≥ -90, PDR ≥ 0.75)
        if last_rssi >= -90 and last_pdr >= 0.75:
            # Link meets video quality requirements
            if last_rssi >= -74 and last_pdr >= 0.88:
                predicted_tput *= 1.2
                quality_label = 'Good'
            else:
                # Acceptable for video (between thresholds)
                quality_label = 'Intermediate'
        else:
            # Below video threshold
            predicted_tput *= 0.7
            quality_label = 'Bad'
        
        # Clamp to realistic range
        predicted_tput = max(1.0, min(20.0, predicted_tput))
        
        # Log prediction
        self._log_prediction(interface_name, last_rssi, last_pdr, 
                           predicted_tput, quality_label, confidence, method)
        
        return predicted_tput, quality_label
    
    def _fallback_prediction(self, rssi, pdr):
        """Fallback: heuristic-based prediction using Tables 4-6"""
        # Calculate score
        rssi_norm = (rssi + 100) / 50.0  # Normalize [-100, -50] to [0, 1]
        score = 0.5 * rssi_norm + 0.5 * pdr
        
        if rssi >= -74 and pdr >= 0.88:
            tput = 8.0
            quality = 'Good'
            confidence = score
        elif rssi <= -88 or pdr <= 0.76:
            tput = 2.0
            quality = 'Bad'
            confidence = 1.0 - score
        else:
            tput = 5.0
            quality = 'Intermediate'
            confidence = 0.5
        
        return tput, quality, confidence, 'Fallback'
    
    def predict_link_quality(self, interface_name, rssi_series, pdr_series):
        """Predict quality class only (for compatibility)"""
        _, quality_label = self.predict_throughput(interface_name, rssi_series, pdr_series)
        
        # Calculate confidence from last sample
        last_rssi = rssi_series[-1]
        last_pdr = pdr_series[-1]
        rssi_norm = (last_rssi + 100) / 50.0
        score = 0.5 * rssi_norm + 0.5 * last_pdr
        
        if quality_label == 'Good':
            confidence = score
        elif quality_label == 'Bad':
            confidence = 1.0 - score
        else:
            confidence = 0.5
        
        return quality_label, confidence
    
    def _log_prediction(self, interface, rssi, pdr, tput, quality, conf, method):
        """Log prediction to CSV"""
        try:
            with open(self.log_path, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    datetime.now().isoformat(),
                    interface, rssi, pdr, tput, quality, conf, method
                ])
        except Exception as e:
            print(f"[PREDICTOR] Log error: {e}")


def compute_scaler_params_from_dataset(csv_path=None,
                                       output_path=None):
    """
    Utility: Compute scaler parameters from collected dataset (Table 3)
    Run this after collecting data to generate proper scaler_params.json
    """
    import pandas as pd
    
    # Resolve project root relative to this file
    this_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(this_dir, "..", ".."))
    
    if csv_path is None:
        csv_path = os.path.join(project_root, "results", "ue_metrics.csv")
    if output_path is None:
        output_path = os.path.join(project_root, "config", "scaler_params.json")
    
    print(f"[SCALER] Computing parameters from {csv_path}")
    
    if not os.path.exists(csv_path):
        print(f"[SCALER] ⚠️ Dataset not found: {csv_path}")
        return
    
    try:
        df = pd.read_csv(csv_path)
        
        # Table 3: Calculate mean, std for RSSI and PDR
        rssi_cols = ['lte_rssi', 'wifi_rssi']
        pdr_cols = ['lte_pdr', 'wifi_pdr']
        
        rssi_data = df[rssi_cols].values.flatten()
        pdr_data = df[pdr_cols].values.flatten()
        
        # Remove NaN values
        rssi_data = rssi_data[~np.isnan(rssi_data)]
        pdr_data = pdr_data[~np.isnan(pdr_data)]
        
        scaler_params = {
            'rssi_mean': float(np.mean(rssi_data)),
            'rssi_std': float(np.std(rssi_data)),
            'rssi_min': float(np.min(rssi_data)),
            'rssi_max': float(np.max(rssi_data)),
            'pdr_mean': float(np.mean(pdr_data)),
            'pdr_std': float(np.std(pdr_data)),
            'pdr_min': float(np.min(pdr_data)),
            'pdr_max': float(np.max(pdr_data))
        }
        
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(scaler_params, f, indent=2)
        
        print(f"[SCALER] ✅ Parameters saved to {output_path}")
        print(f"[SCALER] RSSI: mean={scaler_params['rssi_mean']:.2f}, std={scaler_params['rssi_std']:.2f}")
        print(f"[SCALER] PDR: mean={scaler_params['pdr_mean']:.3f}, std={scaler_params['pdr_std']:.3f}")
        
    except Exception as e:
        print(f"[SCALER] ❌ Error: {e}")
