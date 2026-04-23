"""
BGeometrics on-chain data integration.
Pulls SOPR, MVRV Z-Score, funding rate, and fear & greed from BGeometrics API.
"""
import requests
import os
from dotenv import load_dotenv
import time

load_dotenv(os.path.expanduser('~/.thor/config/.env'))
API_KEY = os.getenv('BGEOMETRICS_API_KEY')
BASE_URL = "https://bitcoin-data.com/v1"

_cache = {}
CACHE_TTL = 60  # Cache for 1 minute

def _get_metric(metric_name, api_key_field='token'):
    """Fetch single metric from BGeometrics."""
    cache_key = f"bgeom_{metric_name}"
    
    # Check cache
    if cache_key in _cache:
        entry = _cache[cache_key]
        if time.time() < entry['expires']:
            return entry['data']
    
    try:
        url = f"{BASE_URL}/{metric_name}"
        params = {'token': API_KEY}
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        
        data = resp.json()
        if isinstance(data, list) and len(data) > 0:
            latest = data[-1]  # Most recent entry
        elif isinstance(data, dict):
            latest = data
        else:
            return None
        
        # Cache it
        _cache[cache_key] = {
            'data': latest,
            'expires': time.time() + CACHE_TTL
        }
        
        return latest
    except Exception as e:
        print(f"Error fetching {metric_name}: {e}")
        return None

def get_sopr():
    """
    Spent Output Profit Ratio.
    >1.0 = holders selling at profit (bearish)
    <1.0 = holders selling at loss (bullish accumulation)
    """
    data = _get_metric('sopr')
    if not data:
        return {'signal': 'UNKNOWN', 'value': None, 'reason': 'Data unavailable'}
    
    try:
        value = float(data.get('sopr', 1.0))
        if value > 1.05:
            signal = 'BEARISH'
            reason = f'Heavy profit-taking (SOPR={value:.3f})'
        elif value < 0.95:
            signal = 'BULLISH'
            reason = f'Strong accumulation (SOPR={value:.3f})'
        else:
            signal = 'NEUTRAL'
            reason = f'SOPR={value:.3f}'
        
        return {
            'signal': signal,
            'value': value,
            'reason': reason
        }
    except:
        return {'signal': 'UNKNOWN', 'value': None, 'reason': 'Parse error'}

def get_mvrv_zscore():
    """
    Market Value to Realized Value Z-Score.
    Cycle indicator: extremely high/low readings signal tops/bottoms.
    """
    data = _get_metric('mvrv-zscore')
    if not data:
        return {'signal': 'UNKNOWN', 'value': None, 'reason': 'Data unavailable'}
    
    try:
        value = float(data.get('mvrvZscore', 0))
        if value > 2.5:
            signal = 'OVERBOUGHT'
            reason = f'Market at historical high (Z={value:.2f})'
        elif value < -2.5:
            signal = 'OVERSOLD'
            reason = f'Market at historical low (Z={value:.2f})'
        elif value > 1.5:
            signal = 'EXTENDED'
            reason = f'Market extended above average (Z={value:.2f})'
        elif value < -1.5:
            signal = 'DEPRESSED'
            reason = f'Market depressed below average (Z={value:.2f})'
        else:
            signal = 'NEUTRAL'
            reason = f'Z={value:.2f}'
        
        return {
            'signal': signal,
            'value': value,
            'reason': reason
        }
    except:
        return {'signal': 'UNKNOWN', 'value': None, 'reason': 'Parse error'}

def get_fear_greed():
    """Fear & Greed Index (0=extreme fear, 100=extreme greed)."""
    data = _get_metric('fear-greed')
    if not data:
        return {'signal': 'UNKNOWN', 'value': None, 'reason': 'Data unavailable'}
    
    try:
        value = float(data.get('fearGreed', 50))
        if value > 75:
            signal = 'EXTREME_GREED'
        elif value > 55:
            signal = 'GREED'
        elif value > 45:
            signal = 'NEUTRAL'
        elif value > 25:
            signal = 'FEAR'
        else:
            signal = 'EXTREME_FEAR'
        
        return {
            'signal': signal,
            'value': value,
            'reason': f'Fear & Greed Index: {value:.0f}'
        }
    except:
        return {'signal': 'UNKNOWN', 'value': None, 'reason': 'Parse error'}

def get_funding_rate():
    """
    Current funding rate on BTC perpetuals.
    Positive = longs pay shorts (bullish crowding)
    Negative = shorts pay longs (bearish crowding / smart money shorting)
    """
    data = _get_metric('funding-rate')
    if not data:
        return {'signal': 'UNKNOWN', 'value': None, 'reason': 'Data unavailable'}
    
    try:
        value = float(data.get('fundingRate', 0))
        
        if value > 0.05:
            signal = 'CROWDED_LONGS'
            reason = f'Extreme positive funding ({value:+.4f}%) - longs heavily leveraged'
        elif value > 0.02:
            signal = 'POSITIVE_FUNDING'
            reason = f'Positive funding ({value:+.4f}%) - longs paying'
        elif value < -0.05:
            signal = 'CROWDED_SHORTS'
            reason = f'Extreme negative funding ({value:+.4f}%) - shorts heavily leveraged'
        elif value < -0.02:
            signal = 'NEGATIVE_FUNDING'
            reason = f'Negative funding ({value:+.4f}%) - shorts paying (bullish contrarian)'
        else:
            signal = 'NEUTRAL'
            reason = f'Neutral funding ({value:+.4f}%)'
        
        return {
            'signal': signal,
            'value': value,
            'reason': reason
        }
    except:
        return {'signal': 'UNKNOWN', 'value': None, 'reason': 'Parse error'}

def get_onchain_summary():
    """Get all on-chain metrics and compile a summary signal."""
    sopr = get_sopr()
    mvrv = get_mvrv_zscore()
    funding = get_funding_rate()
    fear_greed = get_fear_greed()
    
    # Score each metric: +1 bullish, 0 neutral, -1 bearish
    score = 0
    reasons = []
    
    if sopr['signal'] == 'BEARISH': score -= 1
    elif sopr['signal'] == 'BULLISH': score += 1
    if sopr['reason']:
        reasons.append(f"SOPR: {sopr['reason']}")
    
    if mvrv['signal'] in ('OVERBOUGHT', 'EXTENDED'): score -= 1
    elif mvrv['signal'] in ('OVERSOLD', 'DEPRESSED'): score += 1
    if mvrv['reason']:
        reasons.append(f"MVRV: {mvrv['reason']}")
    
    if funding['signal'] == 'CROWDED_LONGS': score -= 1
    elif funding['signal'] == 'NEGATIVE_FUNDING': score += 1
    if funding['reason']:
        reasons.append(f"Funding: {funding['reason']}")
    
    if fear_greed['signal'] == 'EXTREME_GREED': score -= 1
    elif fear_greed['signal'] == 'EXTREME_FEAR': score += 1
    if fear_greed['reason']:
        reasons.append(f"Sentiment: {fear_greed['reason']}")
    
    # Composite signal
    if score > 1:
        signal = 'BULLISH'
    elif score < -1:
        signal = 'BEARISH'
    else:
        signal = 'NEUTRAL'
    
    return {
        'signal': signal,
        'score': score,
        'max_score': 4,
        'reasons': reasons,
        'sopr': sopr,
        'mvrv': mvrv,
        'funding': funding,
        'fear_greed': fear_greed,
    }

if __name__ == '__main__':
    import json
    print(json.dumps(get_onchain_summary(), indent=2))
