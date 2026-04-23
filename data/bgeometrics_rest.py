"""
BGeometrics REST integration — enhanced with exchange flows and LTH/STH.
Pulls on-chain, derivatives, and macro data directly from BGeometrics REST API.
No MCP complexity. 154+ metrics available.
"""
import requests
import os
import json
import time
from dotenv import load_dotenv

load_dotenv(os.path.expanduser('~/.thor/config/.env'))
API_KEY = os.getenv('BGEOMETRICS_API_KEY', 'P9htMlpOYn')
BASE_URL = "https://bitcoin-data.com/v1"

_cache = {}
CACHE_TTL = 120  # Cache for 2 minutes (aggressive to stay under quota)

def _get_metric(metric_name, **kwargs):
    """Fetch single metric from BGeometrics REST API.
    With 2-min cache TTL, pulling all 7 metrics every 60s = ~7 req/min = ~420 req/hour (just under 400 limit).
    At 2-min cache: 7 req per 2 min = ~210 req/hour (well under quota).
    """
    cache_key = f"bgeom_{metric_name}"
    
    # Check cache
    if cache_key in _cache:
        entry = _cache[cache_key]
        if time.time() < entry['expires']:
            # print(f"[CACHE HIT] {metric_name}")  # Debug
            return entry['data']
    
    try:
        url = f"{BASE_URL}/{metric_name}"
        params = {'token': API_KEY}
        params.update(kwargs)
        
        resp = requests.get(url, params=params, timeout=2)  # Fast fail on unavailable endpoints
        resp.raise_for_status()
        
        data = resp.json()
        
        # Normalize response
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
        # print(f"[API CALL] {metric_name} (cached for {CACHE_TTL}s)")  # Debug
        
        return latest
    except Exception as e:
        # Silent fail on timeout/429 — fast fallback
        # if str(e).find('429') == -1:  # Only log non-429 errors
        #     print(f"[ERROR] {metric_name}: {e}")
        # Return stale cache if available (failover)
        if cache_key in _cache:
            return _cache[cache_key]['data']
        return None

# ============================================================================
# ON-CHAIN METRICS
# ============================================================================

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
            reason = f'Balanced (SOPR={value:.3f})'
        
        return {
            'signal': signal,
            'value': value,
            'reason': reason
        }
    except Exception as e:
        print(f"Error parsing SOPR: {e}")
        return {'signal': 'UNKNOWN', 'value': None, 'reason': str(e)}

def get_mvrv_zscore():
    """
    MVRV Z-Score (Market-to-Realized Value).
    >1.5 = overbought (sellers emerge)
    <-1.5 = oversold (buyers accumulate)
    """
    data = _get_metric('mvrv-zscore')
    if not data:
        return {'signal': 'UNKNOWN', 'value': None, 'reason': 'Data unavailable'}
    
    try:
        value = float(data.get('mvrv_zscore', 0))
        if value > 2.0:
            signal = 'BEARISH'
            reason = f'Extreme overbought (Z={value:.3f})'
        elif value > 1.5:
            signal = 'BEARISH'
            reason = f'Overbought (Z={value:.3f})'
        elif value < -1.5:
            signal = 'BULLISH'
            reason = f'Oversold accumulation (Z={value:.3f})'
        elif value < -1.0:
            signal = 'BULLISH'
            reason = f'Buyer interest (Z={value:.3f})'
        else:
            signal = 'NEUTRAL'
            reason = f'Fair value (Z={value:.3f})'
        
        return {
            'signal': signal,
            'value': value,
            'reason': reason
        }
    except Exception as e:
        print(f"Error parsing MVRV: {e}")
        return {'signal': 'UNKNOWN', 'value': None, 'reason': str(e)}

# ============================================================================
# EXCHANGE & HOLDER METRICS
# ============================================================================

def get_exchange_flows():
    """Exchange inflow/outflow (0 if unavailable)."""
    inflow_data = _get_metric('exchange-inflow')
    outflow_data = _get_metric('exchange-outflow')
    
    inflow = float(inflow_data.get('value', 0)) if inflow_data else 0
    outflow = float(outflow_data.get('value', 0)) if outflow_data else 0
    
    net = outflow - inflow  # Positive = more leaving (bullish)
    
    if net > 100000:
        signal = 'BULLISH'
        reason = f'Strong outflow ({net:.0f} BTC leaving exchanges)'
    elif net < -100000:
        signal = 'BEARISH'
        reason = f'Heavy inflow ({abs(net):.0f} BTC entering exchanges)'
    else:
        signal = 'NEUTRAL'
        reason = f'Balanced flows (net={net:.0f})'
    
    return {
        'signal': signal,
        'inflow': inflow,
        'outflow': outflow,
        'net': net,
        'reason': reason
    }

def get_lth_sth_change():
    """Long-term vs Short-term holder position changes."""
    lth_data = _get_metric('lth-position-change')
    sth_data = _get_metric('sth-position-change')
    
    lth = float(lth_data.get('value', 0)) if lth_data else 0
    sth = float(sth_data.get('value', 0)) if sth_data else 0
    
    if lth > 0 and sth < 0:
        signal = 'BULLISH'
        reason = 'LTH accumulating, STH selling'
    elif lth < 0 and sth > 0:
        signal = 'BEARISH'
        reason = 'LTH distributing, STH accumulating'
    else:
        signal = 'NEUTRAL'
        reason = f'Mixed signals (LTH={lth:+.0f}, STH={sth:+.0f})'
    
    return {
        'signal': signal,
        'lth': lth,
        'sth': sth,
        'reason': reason
    }

# ============================================================================
# DERIVATIVES METRICS
# ============================================================================

def get_funding_rate():
    """Funding rate (positive=longs pay shorts, bullish pressure)."""
    data = _get_metric('funding-rate')
    if not data:
        return {'signal': 'UNKNOWN', 'value': None, 'reason': 'Data unavailable'}
    
    try:
        value = float(data.get('value', 0))
        if value > 0.05:
            signal = 'BEARISH'
            reason = f'Extreme long leverage ({value:.4f}% per 8h)'
        elif value > 0.01:
            signal = 'BEARISH'
            reason = f'Longs overleveraged ({value:.4f}%)'
        elif value < -0.05:
            signal = 'BULLISH'
            reason = f'Extreme short squeeze ({value:.4f}%)'
        else:
            signal = 'NEUTRAL'
            reason = f'Balanced funding ({value:.4f}%)'
        
        return {
            'signal': signal,
            'value': value,
            'reason': reason
        }
    except Exception as e:
        print(f"Error parsing funding rate: {e}")
        return {'signal': 'UNKNOWN', 'value': None, 'reason': str(e)}

# ============================================================================
# SENTIMENT & VOLATILITY
# ============================================================================

def get_fear_greed():
    """Fear & Greed Index from Alternative.me (0=extreme fear, 100=extreme greed)."""
    try:
        resp = requests.get('https://api.alternative.me/fng/?limit=1', timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        if data.get('data') and len(data['data']) > 0:
            value = int(data['data'][0]['value'])
        else:
            return {'signal': 'UNKNOWN', 'value': 50, 'reason': 'No F&G data'}
        
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
            'reason': f'F&G={value} (Alternative.me)'
        }
    except Exception as e:
        print(f"Error fetching F&G: {e}")
        return {'signal': 'UNKNOWN', 'value': 50, 'reason': 'F&G fetch failed'}

def get_realized_volatility():
    """Realized Volatility (annualized, %)."""
    data = _get_metric('realized-volatility')
    if not data:
        return {'signal': 'UNKNOWN', 'value': None, 'reason': 'Data unavailable'}
    
    try:
        value = float(data.get('value', 50))
        if value > 100:
            signal = 'BEARISH'
            reason = f'Extreme volatility ({value:.1f}%)'
        elif value > 60:
            signal = 'VOLATILE'
            reason = f'High volatility ({value:.1f}%)'
        elif value < 20:
            signal = 'BULLISH'
            reason = f'Low volatility, range-bound ({value:.1f}%)'
        else:
            signal = 'NEUTRAL'
            reason = f'Normal volatility ({value:.1f}%)'
        
        return {
            'signal': signal,
            'value': value,
            'reason': reason
        }
    except Exception as e:
        print(f"Error parsing volatility: {e}")
        return {'signal': 'UNKNOWN', 'value': None, 'reason': str(e)}

# ============================================================================
# AGGREGATOR (pillar scores for signal engine)
# ============================================================================

def get_all_metrics():
    """Fetch all 7 pillars and return scores (0-100)."""
    return {
        'sopr': get_sopr(),
        'mvrv_zscore': get_mvrv_zscore(),
        'exchange_flows': get_exchange_flows(),
        'lth_sth': get_lth_sth_change(),
        'funding_rate': get_funding_rate(),
        'fear_greed': get_fear_greed(),
        'realized_volatility': get_realized_volatility()
    }

if __name__ == '__main__':
    print(json.dumps(get_all_metrics(), indent=2, default=str))

# ============================================================================
# AGGREGATOR FUNCTION FOR SIGNAL ENGINE
# ============================================================================

def get_enhanced_summary():
    """Return structured summary for aggregator.py signal engine."""
    metrics = get_all_metrics()
    
    # Map metrics to pillar scores (0-100 scale)
    sopr = metrics['sopr']
    mvrv = metrics['mvrv_zscore']
    flows = metrics['exchange_flows']
    lth_sth = metrics['lth_sth']
    funding = metrics['funding_rate']
    fg = metrics['fear_greed']
    vol = metrics['realized_volatility']
    
    # Score each metric (0-100)
    def signal_to_score(signal):
        scores = {
            'EXTREME_GREED': 100, 'GREED': 75,
            'NEUTRAL': 50,
            'FEAR': 25, 'EXTREME_FEAR': 0,
            'BULLISH': 75, 'BEARISH': 25,
            'VOLATILE': 50
        }
        return scores.get(signal, 50)
    
    sopr_score = signal_to_score(sopr['signal'])
    mvrv_score = signal_to_score(mvrv['signal'])
    flows_score = signal_to_score(flows['signal'])
    lth_score = signal_to_score(lth_sth['signal'])
    funding_score = signal_to_score(funding['signal'])
    fg_score = fg['value'] if fg['value'] else 50
    vol_score = signal_to_score(vol['signal'])
    
    # Average for composite on-chain score
    onchain_score = (sopr_score + mvrv_score + flows_score + lth_score + vol_score) / 5.0
    
    return {
        'score': onchain_score,
        'max_score': 100,
        'pillars': {
            'sopr': sopr_score,
            'mvrv': mvrv_score,
            'flows': flows_score,
            'holders': lth_score,
            'vol': vol_score,
            'fear_greed': fg_score,
            'funding': funding_score
        },
        'details': {
            'sopr': sopr,
            'mvrv': mvrv,
            'flows': flows,
            'lth_sth': lth_sth,
            'funding': funding,
            'fg': fg,
            'vol': vol
        }
    }
