"""
Signal Engine for Backtesting
Generates signals using 7-pillar consensus with learned weights
"""
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, List, Tuple
from dataclasses import dataclass

@dataclass
class Signal:
    timestamp: datetime
    signal_type: str  # "BUY", "SELL", "NEUTRAL"
    score: float  # 0-100
    conviction: float  # 0-100, confidence in signal
    regime: str  # "BULL", "BEAR", "RANGING", "TRANSITION"
    pillar_scores: Dict[str, float]  # Per-pillar breakdown
    reasoning: str  # Why this signal fired

class SignalEngine:
    def __init__(self, learned_weights: Dict[str, float] = None):
        """
        Initialize with learned weights from logistic regression.
        If None, uses equal weights (will be replaced after backtesting).
        """
        self.pillar_names = [
            "momentum",
            "on_chain",
            "derivatives",
            "macro",
            "sentiment",
            "volume",
            "regime_fit"
        ]
        
        if learned_weights:
            self.weights = learned_weights
        else:
            # Equal weights initially
            self.weights = {pillar: 1.0 / len(self.pillar_names) for pillar in self.pillar_names}
        
        self.conviction_threshold = 0.40  # 40% conviction required for signal (more sensitive in early stage)
    
    def calculate_momentum_pillar(self, rsi: float, ma_slope: float) -> float:
        """RSI + MA trend signal [0, 100]"""
        # RSI < 30 = bullish oversold, RSI > 70 = bearish overbought
        rsi_signal = 100 - abs(rsi - 50) * 2  # Center at 50, scale to [0, 100]
        ma_signal = 50 + (ma_slope / max(abs(ma_slope), 0.01)) * 50  # MA momentum [0, 100]
        
        return np.clip((rsi_signal + ma_signal) / 2, 0, 100)
    
    def calculate_on_chain_pillar(self, sopr: float, mvrv_zscore: float, 
                                  exchange_netflow: float, lth_supply_change: float) -> float:
        """On-chain metrics: accumulation vs distribution"""
        # SOPR > 1.0 = profit-taking, SOPR < 1.0 = accumulation (bullish)
        sopr_signal = 50 + (1.0 - sopr) * 50  # Invert so lower = more bullish
        
        # MVRV Z-Score: < -1 = undervalued (buy), > 1.5 = overbought (sell)
        mvrv_signal = 50 - mvrv_zscore * 20  # Higher Z = less bullish
        
        # Exchange flows: negative = coins leaving (accumulation, bullish)
        netflow_signal = 50 - (exchange_netflow / max(abs(exchange_netflow), 1000)) * 50
        
        # LTH supply: negative change = LTHs accumulating (bullish)
        lth_signal = 50 - (lth_supply_change / max(abs(lth_supply_change), 0.1)) * 50
        
        return np.clip(np.mean([sopr_signal, mvrv_signal, netflow_signal, lth_signal]), 0, 100)
    
    def calculate_derivatives_pillar(self, funding_rate: float, oi_change: float, 
                                    basis: float) -> float:
        """Funding rates + Open Interest + Basis indicate leverage sentiment"""
        # Positive funding = longs paying shorts (bearish crowding)
        # Negative funding = shorts paying longs (bullish opportunity)
        funding_signal = 50 - funding_rate * 100  # Higher funding = less bullish
        
        # OI expanding on rallies = weak rally (bearish)
        oi_signal = 50 - (oi_change / max(abs(oi_change), 100)) * 50
        
        # Positive basis (futures > spot) = backwardation (bullish)
        basis_signal = 50 + basis * 100
        
        return np.clip(np.mean([funding_signal, oi_signal, basis_signal]), 0, 100)
    
    def calculate_macro_pillar(self, fed_rate: float, m2_growth: float, 
                              dxy: float, vix: float) -> float:
        """Macro liquidity and macro regime"""
        # Higher rates = tighter liquidity (less bullish for risk assets)
        rate_signal = 50 - fed_rate * 10  # Scale: each 1% rate = -10 points
        
        # M2 growth: contracting = deflation (bearish), expanding = inflation (bullish in crypto)
        m2_signal = 50 + (m2_growth / max(abs(m2_growth), 0.01)) * 50
        
        # DXY: high USD strength = less bullish for crypto
        dxy_signal = 50 - (dxy - 100) / 5  # Normalized around 100
        
        # VIX: elevated = risk off (bearish for crypto)
        vix_signal = 50 - vix / 2  # Each point of VIX = -0.5 to score
        
        return np.clip(np.mean([rate_signal, m2_signal, dxy_signal, vix_signal]), 0, 100)
    
    def calculate_sentiment_pillar(self, fear_greed: float, social_volume: float, 
                                  whale_transactions: float) -> float:
        """Market sentiment and crowd positioning"""
        # Fear & Greed: < 25 = extreme fear (buy), > 75 = extreme greed (sell)
        fg_signal = abs(fear_greed - 50) * 2  # Extremes = higher signal
        if fear_greed > 75 or fear_greed < 25:
            fg_signal = 75  # Extreme conviction
        
        # Social volume: spikes on both tops and bottoms, need context
        sv_signal = 50 + (social_volume / max(abs(social_volume), 1000)) * 30
        
        # Whale transactions: big moves signal concentration
        whale_signal = 50 + (whale_transactions / max(abs(whale_transactions), 100)) * 30
        
        return np.clip(np.mean([fg_signal, sv_signal, whale_signal]), 0, 100)
    
    def calculate_volume_pillar(self, volume_change: float, volume_ma_ratio: float) -> float:
        """Volume and breadth"""
        # Volume spike on moves = confirmation
        vol_signal = 50 + (volume_change / max(abs(volume_change), 1000)) * 50
        
        # Volume MA ratio: > 1.0 = elevated volume (bullish on rallies, bearish on dumps)
        vol_ma_signal = 50 + ((volume_ma_ratio - 1.0) / max(abs(volume_ma_ratio - 1.0), 0.5)) * 50
        
        return np.clip(np.mean([vol_signal, vol_ma_signal]), 0, 100)
    
    def calculate_regime_fit_pillar(self, regime: str, signal_score: float) -> float:
        """Does the signal align with the regime?"""
        # In bull regime, bullish signals (>50) should have higher conviction
        # In bear regime, bearish signals (<50) should have higher conviction
        # Regime-misaligned signals get discounted
        
        if regime == "BULL" and signal_score > 50:
            return signal_score  # Aligned
        elif regime == "BEAR" and signal_score < 50:
            return 100 - signal_score  # Aligned (inverted)
        elif regime == "RANGING":
            return 50  # Ranging = no regime preference
        else:
            return signal_score * 0.7  # Misaligned = less conviction
    
    def generate_signal(self, timestamp: datetime, 
                       ohlc_data: Dict, on_chain_data: Dict, 
                       derivatives_data: Dict, macro_data: Dict, 
                       sentiment_data: Dict) -> Signal:
        """
        Generate composite signal from all pillars.
        All data should be current (latest bar/day).
        """
        
        # Calculate each pillar
        momentum = self.calculate_momentum_pillar(
            ohlc_data.get('rsi', 50),
            ohlc_data.get('ma_slope', 0)
        )
        
        on_chain = self.calculate_on_chain_pillar(
            on_chain_data.get('sopr', 1.0),
            on_chain_data.get('mvrv_zscore', 0),
            on_chain_data.get('exchange_netflow', 0),
            on_chain_data.get('lth_supply_change', 0)
        )
        
        derivatives = self.calculate_derivatives_pillar(
            derivatives_data.get('funding_rate', 0),
            derivatives_data.get('oi_change', 0),
            derivatives_data.get('basis', 0)
        )
        
        macro = self.calculate_macro_pillar(
            macro_data.get('fed_rate', 5.0),
            macro_data.get('m2_growth', 0),
            macro_data.get('dxy', 100),
            macro_data.get('vix', 20)
        )
        
        sentiment = self.calculate_sentiment_pillar(
            sentiment_data.get('fear_greed', 50),
            sentiment_data.get('social_volume', 0),
            sentiment_data.get('whale_transactions', 0)
        )
        
        volume = self.calculate_volume_pillar(
            sentiment_data.get('volume_change', 0),
            sentiment_data.get('volume_ma_ratio', 1.0)
        )
        
        # Detect regime
        regime = self._detect_regime(on_chain, derivatives, macro)
        
        regime_fit = self.calculate_regime_fit_pillar(regime, np.mean([momentum, on_chain, derivatives, sentiment]))
        
        # Composite score (weighted average)
        pillar_scores = {
            "momentum": momentum,
            "on_chain": on_chain,
            "derivatives": derivatives,
            "macro": macro,
            "sentiment": sentiment,
            "volume": volume,
            "regime_fit": regime_fit
        }
        
        weighted_sum = sum(pillar_scores[p] * self.weights.get(p, 0.14) for p in self.pillar_names)
        composite_score = np.clip(weighted_sum, 0, 100)
        
        # Conviction = alignment across pillars (low std dev = high conviction)
        pillar_values = [pillar_scores[p] for p in self.pillar_names]
        std_dev = np.std(pillar_values)
        conviction = max(0, 100 - std_dev * 1.5)  # Lower std = higher conviction
        
        # Anti-crowding override
        if sentiment_data.get('fear_greed', 50) > 85 and derivatives_data.get('funding_rate', 0) > 0.1:
            conviction *= 0.7  # Extreme crowding = lower conviction
        
        # Generate signal
        if conviction < self.conviction_threshold:
            signal_type = "NEUTRAL"
        elif composite_score > 55:
            signal_type = "BUY"
        elif composite_score < 45:
            signal_type = "SELL"
        else:
            signal_type = "NEUTRAL"
        
        reasoning = f"{signal_type} signal: composite={composite_score:.1f}, conviction={conviction:.1f}, regime={regime}"
        
        return Signal(
            timestamp=timestamp,
            signal_type=signal_type,
            score=composite_score,
            conviction=conviction,
            regime=regime,
            pillar_scores=pillar_scores,
            reasoning=reasoning
        )
    
    def _detect_regime(self, on_chain_score: float, derivatives_score: float, macro_score: float) -> str:
        """Simple regime detection from key pillars"""
        avg_score = np.mean([on_chain_score, derivatives_score, macro_score])
        
        if avg_score > 65:
            return "BULL"
        elif avg_score < 35:
            return "BEAR"
        elif 45 < avg_score < 55:
            return "RANGING"
        else:
            return "TRANSITION"


if __name__ == "__main__":
    engine = SignalEngine()
    
    # Test signal
    test_signal = engine.generate_signal(
        timestamp=datetime.now(),
        ohlc_data={"rsi": 35, "ma_slope": 0.05},
        on_chain_data={"sopr": 0.95, "mvrv_zscore": -1.2, "exchange_netflow": -1000, "lth_supply_change": -0.5},
        derivatives_data={"funding_rate": 0.02, "oi_change": 100, "basis": 0.001},
        macro_data={"fed_rate": 5.0, "m2_growth": 0.01, "dxy": 105, "vix": 18},
        sentiment_data={"fear_greed": 28, "social_volume": 500, "whale_transactions": 10, "volume_change": 0.3, "volume_ma_ratio": 1.2}
    )
    
    print(f"Signal: {test_signal.signal_type} ({test_signal.score:.1f})")
    print(f"Conviction: {test_signal.conviction:.1f}%")
    print(f"Regime: {test_signal.regime}")
    print(f"Reasoning: {test_signal.reasoning}")
    print(f"\nPillar Breakdown:")
    for pillar, score in test_signal.pillar_scores.items():
        print(f"  {pillar}: {score:.1f}")
