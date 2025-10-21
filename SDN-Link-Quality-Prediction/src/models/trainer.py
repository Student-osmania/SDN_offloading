"""
Training script for channel quality prediction
Handles data preprocessing, classification, and model training
Based on Section III-A of the research paper
"""

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import confusion_matrix, classification_report, accuracy_score
import matplotlib.pyplot as plt
import seaborn as sns
import model_config as config


class ChannelQualityTrainer:
    def __init__(self, data_path):
        """
        Initialize trainer with dataset
        Args:
            data_path: Path to IoT-LAB dataset
        """
        self.data = pd.read_csv(data_path)
        self.scaler = StandardScaler()
        self.X_train = None
        self.X_test = None
        self.y_train = None
        self.y_test = None
        
    def preprocess_data(self):
        """
        Data preprocessing as per Section III-A-1
        - Remove redundancy
        - Fill missing values with mean
        - Drop tx_count (std and mean don't vary)
        """
        # Remove tx_count column
        if 'tx_count' in self.data.columns:
            self.data = self.data.drop('tx_count', axis=1)
        
        # Fill missing values with mean
        self.data.fillna(self.data.mean(), inplace=True)
        
        # Remove duplicates
        self.data.drop_duplicates(inplace=True)
        
        print("Data preprocessing completed")
        print(f"Dataset shape: {self.data.shape}")
        
    def classify_links(self, method='combined'):
        """
        Classify links into Good, Intermediate, Bad classes
        Args:
            method: 'rssi', 'pdr', or 'combined' (default)
        Based on Tables 4, 5, 6 from the paper
        """
        if method == 'rssi':
            # RSSI-based classification (Table 4)
            conditions = [
                self.data['rssi_dbm'] >= config.RSSI_THRESHOLDS['GOOD'],
                (self.data['rssi_dbm'] < config.RSSI_THRESHOLDS['GOOD']) & 
                (self.data['rssi_dbm'] >= config.RSSI_THRESHOLDS['BAD']),
                self.data['rssi_dbm'] < config.RSSI_THRESHOLDS['BAD']
            ]
            
        elif method == 'pdr':
            # PDR-based classification (Table 5)
            conditions = [
                self.data['pdr'] >= config.PDR_THRESHOLDS['GOOD'],
                (self.data['pdr'] < config.PDR_THRESHOLDS['GOOD']) & 
                (self.data['pdr'] >= config.PDR_THRESHOLDS['BAD']),
                self.data['pdr'] < config.PDR_THRESHOLDS['BAD']
            ]
            
        else:  # combined (Table 6 - PDR has priority)
            # Combined classification with PDR priority
            conditions = [
                (self.data['pdr'] >= config.COMBINED_THRESHOLDS['PDR_GOOD']),
                (self.data['pdr'] >= config.COMBINED_THRESHOLDS['PDR_INTERMEDIATE']) & 
                (self.data['pdr'] < config.COMBINED_THRESHOLDS['PDR_GOOD']) |
                ((self.data['rssi_dbm'] >= config.COMBINED_THRESHOLDS['RSSI_BAD']) & 
                 (self.data['rssi_dbm'] < config.COMBINED_THRESHOLDS['RSSI_GOOD'])),
                (self.data['pdr'] < config.COMBINED_THRESHOLDS['PDR_INTERMEDIATE'])
            ]
        
        choices = [0, 1, 2]  # 0=Good, 1=Intermediate, 2=Bad
        self.data['link_quality'] = np.select(conditions, choices, default=1)
        
        print(f"Classification completed using {method} method")
        print(self.data['link_quality'].value_counts())
        
    def create_sequences(self, feature_cols):
        """
        Create time-series sequences with T=30 timesteps
        Section III-A-3 of the paper
        """
        X, y = [], []
        
        # Extract features and labels
        features = self.data[feature_cols].values
        labels = self.data['link_quality'].values
        
        # Create sequences
        for i in range(len(features) - config.TIMESTEPS):
            X.append(features[i:i + config.TIMESTEPS])
            y.append(labels[i + config.TIMESTEPS])
        
        return np.array(X), np.array(y)
    
    def prepare_data(self, feature_cols=['rssi_dbm', 'pdr']):
        """
        Prepare training and testing data with feature scaling
        Uses StandardScaler as mentioned in Section III-A-3
        """
        # Create sequences
        X, y = self.create_sequences(feature_cols)
        
        # Split into train and test (80-20 split)
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, 
            test_size=(1 - config.TRAIN_TEST_SPLIT),
            shuffle=False  # Time-series data
        )
        
        # Feature scaling using StandardScaler
        # Reshape for scaling
        n_samples_train, n_timesteps, n_features = X_train.shape
        X_train_reshaped = X_train.reshape(-1, n_features)
        
        # Fit and transform training data
        X_train_scaled = self.scaler.fit_transform(X_train_reshaped)
        X_train_scaled = X_train_scaled.reshape(n_samples_train, n_timesteps, n_features)
        
        # Transform test data
        n_samples_test = X_test.shape[0]
        X_test_reshaped = X_test.reshape(-1, n_features)
        X_test_scaled = self.scaler.transform(X_test_reshaped)
        X_test_scaled = X_test_scaled.reshape(n_samples_test, n_timesteps, n_features)
        
        # Convert labels to categorical (one-hot encoding)
        from tensorflow.keras.utils import to_categorical
        y_train_cat = to_categorical(y_train, num_classes=config.OUTPUT_CLASSES)
        y_test_cat = to_categorical(y_test, num_classes=config.OUTPUT_CLASSES)
        
        self.X_train = X_train_scaled
        self.X_test = X_test_scaled
        self.y_train = y_train_cat
        self.y_test = y_test_cat
        self.y_test_labels = y_test  # Keep original labels for evaluation
        
        print(f"Training data shape: {self.X_train.shape}")
        print(f"Testing data shape: {self.X_test.shape}")
        
        return self.X_train, self.X_test, self.y_train, self.y_test
    
    def plot_confusion_matrix(self, y_true, y_pred, title='Confusion Matrix'):
        """
        Plot confusion matrix as shown in Figures 5, 6, 7 of the paper
        """
        cm = confusion_matrix(y_true, y_pred)
        cm_normalized = cm.astype('float') / cm.sum(axis=1)[:, np.newaxis]
        
        plt.figure(figsize=(8, 6))
        sns.heatmap(cm_normalized, annot=True, fmt='.2%', cmap='Blues',
                    xticklabels=['Good', 'Intermediate', 'Bad'],
                    yticklabels=['Good', 'Intermediate', 'Bad'])
        plt.title(title)
        plt.ylabel('True Label')
        plt.xlabel('Predicted Label')
        plt.tight_layout()
        plt.savefig(f'{title.replace(" ", "_")}.png')
        plt.show()
        
    def evaluate_model(self, model, model_name):
        """
        Evaluate model and generate metrics
        """
        # Predict
        _, y_pred = model.predict(self.X_test)
        
        # Calculate accuracy
        accuracy = accuracy_score(self.y_test_labels, y_pred)
        print(f"\n{model_name} Accuracy: {accuracy*100:.2f}%")
        
        # Classification report
        print(f"\n{model_name} Classification Report:")
        print(classification_report(self.y_test_labels, y_pred,
                                    target_names=['Good', 'Intermediate', 'Bad']))
        
        # Confusion matrix
        self.plot_confusion_matrix(self.y_test_labels, y_pred, 
                                   f'{model_name} Confusion Matrix')
        
        return accuracy, y_pred
    
    def analyze_misclassified(self, y_pred):
        """
        Analyze misclassified links as shown in Figure 13
        """
        misclassified = np.where(self.y_test_labels != y_pred)[0]
        print(f"\nTotal misclassified links: {len(misclassified)}")
        
        return misclassified


# Main training pipeline
if __name__ == "__main__":
    # Initialize trainer
    trainer = ChannelQualityTrainer('iot_lab_dataset.csv')
    
    # Preprocess data
    trainer.preprocess_data()
    
    # Classify links (combined method - PDR + RSSI)
    trainer.classify_links(method='combined')
    
    # Prepare data for training
    X_train, X_test, y_train, y_test = trainer.prepare_data()
    
    print("\nData preparation completed. Ready for model training.")