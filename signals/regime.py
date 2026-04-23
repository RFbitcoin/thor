"""
Regime Detector — classifies market into BULL / BEAR / RANGING / TRANSITION.
Uses a rules-based ensemble (no ML deps needed) across price structure,
momentum, volatility, and derivatives.
Weights all other pillars differently per regime.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from data.coingecko import get_ohlcv

def get_regime(symbol='BTC'):
    """
    Classify market regime using:
    - Price vs MA50/MA200
    - MA50 slope (trending up/down)
    - 30d vs 90d volatility ratio (vol expansion = transition)
    - RSI range (bull: RSI holds >50; bear: RSI caps <50)
    - Price drawdown from recent high
    """
    try:
        # Get 200 days of daily closes
        r = get_ohlcv(symbol, days=200)
        prices = [x[1] for x in r.get('prices', [])]
        if len(prices) < 100:
            return {'regime': 'UNKNOWN', 'confidence': 0, 'weights': _default_weights('RANGING')}

        # Core metrics
        ma50  = sum(prices[-50:]) / 50
        ma200 = sum(prices[-200:]) / 200 if len(prices) >= 200 else sum(prices) / len(prices)
        ma50_slope = (sum(prices[-10:]) / 10) - (sum(prices[-20:-10]) / 10)  # rising or falling
        current = prices[-1]

        # Volatility: 30d vs 90d daily returns std
        import statistics
        returns = [(prices[i] - prices[i-1]) / prices[i-1] for i in range(1, len(prices))]
        vol_30d  = statistics.stdev(returns[-30:]) if len(returns) >= 30 else 0.02
        vol_90d  = statistics.stdev(returns[-90:]) if len(returns) >= 90 else 0.02
        vol_ratio = vol_30d / vol_90d if vol_90d > 0 else 1.0

        # Drawdown from 90d high
        high_90d = max(prices[-90:])
        drawdown = ((current - high_90d) / high_90d) * 100

        # RSI(14) using last 30 days
        def rsi(p, n=14):
            d = [p[i]-p[i-1] for i in range(1, len(p))]
            g = sum(x for x in d[-n:] if x > 0) / n
            l = sum(-x for x in d[-n:] if x < 0) / n
            return 50 if l == 0 else 100 - (100 / (1 + g / l))
        rsi_val = rsi(prices[-30:])

        # Scoring
        score = 0
        factors = []

        # Price structure
        if current > ma200:   score += 2; factors.append('Price above MA200')
        else:                 score -= 2; factors.append('Price below MA200')
        if current > ma50:    score += 1; factors.append('Price above MA50')
        else:                 score -= 1; factors.append('Price below MA50')
        if ma50 > ma200:      score += 1; factors.append('Golden Cross')
        else:                 score -= 1; factors.append('Death Cross')
        if ma50_slope > 0:    score += 1; factors.append('MA50 rising')
        else:                 score -= 1; factors.append('MA50 falling')

        # Momentum
        if rsi_val > 55:      score += 1; factors.append(f'RSI bullish ({rsi_val:.0f})')
        elif rsi_val < 45:    score -= 1; factors.append(f'RSI bearish ({rsi_val:.0f})')

        # Volatility regime
        if vol_ratio > 1.5:   factors.append('Vol expansion — transition signal')
        if drawdown < -20:    score -= 1; factors.append(f'Deep drawdown {drawdown:.1f}%')

        # Classify
        if vol_ratio > 1.5 and abs(score) < 3:
            regime = 'TRANSITION'
            confidence = 50
        elif score >= 4:
            regime = 'BULL'
            confidence = min(95, 60 + score * 5)
        elif score <= -4:
            regime = 'BEAR'
            confidence = min(95, 60 + abs(score) * 5)
        elif -2 <= score <= 2:
            regime = 'RANGING'
            confidence = 60
        elif score > 2:
            regime = 'BULL'
            confidence = 55
        else:
            regime = 'BEAR'
            confidence = 55

        return {
            'regime': regime,
            'confidence': confidence,
            'score': score,
            'factors': factors,
            'metrics': {
                'current': round(current, 2),
                'ma50': round(ma50, 2),
                'ma200': round(ma200, 2),
                'rsi': round(rsi_val, 1),
                'vol_ratio': round(vol_ratio, 2),
                'drawdown_90d': round(drawdown, 1)
            },
            'weights': _default_weights(regime)
        }
    except Exception as e:
        print(f'Regime error: {e}')
        return {'regime': 'UNKNOWN', 'confidence': 0, 'weights': _default_weights('RANGING')}

def _default_weights(regime):
    """
    Pillar weights per regime. Values are multipliers (1.0 = normal).
    In BULL: amplify on-chain/derivatives flow, downweight macro.
    In BEAR: amplify macro/sentiment, downweight derivatives (already crowded).
    In RANGING: equal weight everything.
    In TRANSITION: boost sentiment/macro, penalize momentum signals.
    """
    weights = {
        'BULL': {
            'technical': 1.2,
            'derivatives': 1.3,
            'macro': 0.7,
            'sentiment': 1.0,
            'vix': 0.8,        # VIX less critical in established bull
            'volume': 1.3,     # volume breakouts matter most in bull
            'btc_dom': 1.0,
        },
        'BEAR': {
            'technical': 1.0,
            'derivatives': 0.8,
            'macro': 1.4,
            'sentiment': 1.2,
            'vix': 1.5,        # VIX is most important signal in bear market
            'volume': 1.0,
            'btc_dom': 0.8,    # dom less meaningful when everything is falling
        },
        'RANGING': {
            'technical': 1.0,
            'derivatives': 1.0,
            'macro': 1.0,
            'sentiment': 1.0,
            'vix': 1.0,
            'volume': 1.2,     # volume breakout = ranging market breaking out
            'btc_dom': 1.0,
        },
        'TRANSITION': {
            'technical': 0.7,
            'derivatives': 0.8,
            'macro': 1.3,
            'sentiment': 1.3,
            'vix': 1.4,        # VIX spike often triggers transitions
            'volume': 1.2,
            'btc_dom': 1.1,
        },
        'UNKNOWN': {
            'technical': 1.0,
            'derivatives': 1.0,
            'macro': 1.0,
            'sentiment': 1.0,
            'vix': 1.0,
            'volume': 1.0,
            'btc_dom': 1.0,
        }
    }
    return weights.get(regime, weights['RANGING'])

if __name__ == '__main__':
    import json
    print(json.dumps(get_regime('BTC'), indent=2))
