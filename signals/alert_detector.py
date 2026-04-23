"""
Smart alert detector for THOR.
Triggers on:
- Regime changes (BULL -> BEAR, etc.)
- Conviction threshold crossings (e.g., conviction breaks above 75%)
- Divergence (price moving opposite to on-chain/derivatives)
"""
import json
import os
import time
from datetime import datetime

STATE_FILE = os.path.expanduser('~/.thor/alert_state.json')

class AlertDetector:
    def __init__(self):
        self.state = self._load_state()
    
    def _load_state(self):
        """Load last alert state from disk with safe defaults."""
        defaults = {
            'last_regime': None,
            'last_conviction': 0,
            'last_price': None,
            'regime_alert_time': 0,  # Use 0 instead of None to avoid arithmetic errors
            'conviction_alert_time': 0,
        }
        
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    loaded = json.load(f)
                    # Merge loaded state with defaults (fill missing keys)
                    defaults.update(loaded)
                    # Ensure all required keys exist and are valid types
                    defaults['regime_alert_time'] = defaults.get('regime_alert_time') or 0
                    defaults['conviction_alert_time'] = defaults.get('conviction_alert_time') or 0
                    return defaults
            except Exception as e:
                print(f'[WARN] Failed to load alert state: {e}')
        
        return defaults
    
    def _save_state(self):
        """Persist state to disk."""
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, 'w') as f:
                json.dump(self.state, f)
        except:
            pass
    
    def detect_alerts(self, signal_data):
        """
        Detect and return alerts based on signal data.
        Returns list of {type, message, severity, data}
        """
        alerts = []
        current_time = time.time()
        
        # 1. REGIME CHANGE ALERT
        current_regime = signal_data.get('regime', 'UNKNOWN')
        last_regime = self.state.get('last_regime')
        regime_alert_time = self.state.get('regime_alert_time') or 0
        
        if last_regime and current_regime != last_regime:
            # Only alert once per regime change (anti-spam)
            if (current_time - regime_alert_time) > 3600:  # 1 hour cooldown
                alerts.append({
                    'type': 'regime_change',
                    'severity': 'HIGH',
                    'message': f'Regime changed: {last_regime} → {current_regime}',
                    'data': {
                        'from': last_regime,
                        'to': current_regime,
                        'confidence': signal_data.get('regime_confidence', 0),
                    }
                })
                self.state['regime_alert_time'] = current_time
        
        self.state['last_regime'] = current_regime
        
        # 2. CONVICTION THRESHOLD ALERTS
        current_conviction = signal_data.get('conviction', 0)
        last_conviction = self.state.get('last_conviction', 0)
        conviction_alert_time = self.state.get('conviction_alert_time', 0)
        
        # Alert when conviction crosses into "high confidence" territory (75%)
        if last_conviction < 75 and current_conviction >= 75:
            if current_time - conviction_alert_time > 3600:
                signal = signal_data.get('signal', 'NEUTRAL')
                alerts.append({
                    'type': 'high_conviction',
                    'severity': 'MEDIUM',
                    'message': f'High conviction {signal} signal ({current_conviction}%)',
                    'data': {
                        'conviction': current_conviction,
                        'signal': signal,
                        'composite': signal_data.get('composite', 0),
                        'pillars': signal_data.get('pillars', {}),
                    }
                })
                self.state['conviction_alert_time'] = current_time
        
        # Alert when conviction drops below 30% (low confidence)
        if last_conviction > 30 and current_conviction <= 30:
            if current_time - conviction_alert_time > 3600:
                alerts.append({
                    'type': 'low_conviction',
                    'severity': 'LOW',
                    'message': f'Conviction dropped to {current_conviction}% - signal unreliable',
                    'data': {
                        'conviction': current_conviction,
                        'signal': signal_data.get('signal', 'NEUTRAL'),
                    }
                })
                self.state['conviction_alert_time'] = current_time
        
        self.state['last_conviction'] = current_conviction
        
        # 3. DIVERGENCE DETECTION
        # Price moving but on-chain/sentiment not confirming
        price = signal_data.get('price', 0)
        pillars = signal_data.get('pillars', {})
        composite = signal_data.get('composite', 0)
        
        if price and self.state.get('last_price'):
            price_change = (price - self.state['last_price']) / self.state['last_price']
            
            # Price up but on-chain bearish = bearish divergence
            if price_change > 0.02 and pillars.get('onchain', 0) < -0.3 and composite < -0.2:
                alerts.append({
                    'type': 'bearish_divergence',
                    'severity': 'MEDIUM',
                    'message': f'Price up {price_change*100:+.1f}% but on-chain showing weakness',
                    'data': {
                        'price_change': price_change,
                        'onchain_signal': pillars.get('onchain', 0),
                        'composite': composite,
                    }
                })
            
            # Price down but on-chain bullish = bullish divergence
            elif price_change < -0.02 and pillars.get('onchain', 0) > 0.3 and composite > 0.2:
                alerts.append({
                    'type': 'bullish_divergence',
                    'severity': 'MEDIUM',
                    'message': f'Price down {price_change*100:+.1f}% but on-chain showing strength',
                    'data': {
                        'price_change': price_change,
                        'onchain_signal': pillars.get('onchain', 0),
                        'composite': composite,
                    }
                })
        
        self.state['last_price'] = price
        
        # Persist state
        self._save_state()
        
        return alerts

# Global detector instance
_detector = None

def get_detector():
    global _detector
    if _detector is None:
        _detector = AlertDetector()
    return _detector

def detect_alerts(signal_data):
    """Convenience function."""
    return get_detector().detect_alerts(signal_data)
