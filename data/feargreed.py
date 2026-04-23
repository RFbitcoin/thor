import requests
import time

_cache = {}
CACHE_TTL = 120  # Cache F&G for 2 minutes

def get_alternative_me_fg():
    """Pull Fear & Greed from Alternative.me (free, no auth)
    Cached for 120s to reduce external API calls.
    """
    # Check cache
    if 'alt_me_fg' in _cache and time.time() < _cache['alt_me_fg']['expires']:
        return _cache['alt_me_fg']['data']
    
    try:
        r = requests.get('https://api.alternative.me/fng/', timeout=10)
        r.raise_for_status()
        d = r.json().get('data', [{}])[0]
        result = {'value': int(d.get('value', 50)), 'label': d.get('value_classification', 'Neutral')}
        
        # Cache it
        _cache['alt_me_fg'] = {'data': result, 'expires': time.time() + CACHE_TTL}
        return result
    except Exception as e:
        print(f"Alternative.me error: {e}")
        # Return stale cache if available
        if 'alt_me_fg' in _cache:
            return _cache['alt_me_fg']['data']
    return {'value': 50, 'label': 'Neutral'}

def get_derivatives_sentiment():
    """Pull funding rates from Binance as derivatives sentiment proxy
    High positive funding = greed, negative/low = fear
    Maps to 0-100 scale (0=max fear, 100=max greed)
    Cached for 120s (Binance free, no limits, but reduce chatter).
    """
    # Check cache
    if 'deriv_sentiment' in _cache and time.time() < _cache['deriv_sentiment']['expires']:
        return _cache['deriv_sentiment']['data']
    
    try:
        # Get BTC perp funding rate from Binance
        r = requests.get('https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1', timeout=10)
        r.raise_for_status()
        funding_data = r.json()
        if funding_data:
            funding_rate = float(funding_data[0]['fundingRate'])
            # Typical range: -0.02 to +0.02
            # Map to 0-100: -0.02 = 0 (fear), 0 = 50 (neutral), +0.02 = 100 (greed)
            # Clamp to [-0.03, 0.03] to handle extremes
            funding_rate = max(-0.03, min(0.03, funding_rate))
            sentiment_score = int(50 + (funding_rate / 0.03) * 50)
            sentiment_score = max(0, min(100, sentiment_score))  # Clamp to 0-100
            
            label = 'Extreme Greed' if sentiment_score > 80 else \
                    'Greed' if sentiment_score > 65 else \
                    'Neutral' if sentiment_score > 35 else \
                    'Fear' if sentiment_score > 20 else \
                    'Extreme Fear'
            
            result = {'value': sentiment_score, 'label': label, 'funding_rate': funding_rate}
            _cache['deriv_sentiment'] = {'data': result, 'expires': time.time() + CACHE_TTL}
            return result
    except Exception as e:
        print(f"Derivatives sentiment error: {e}")
        if 'deriv_sentiment' in _cache:
            return _cache['deriv_sentiment']['data']
    return {'value': 50, 'label': 'Neutral', 'funding_rate': 0}

def get_fear_greed():
    """Combined F&G: Average of Alternative.me and derivatives sentiment"""
    alt_fg = get_alternative_me_fg()
    deriv_sentiment = get_derivatives_sentiment()
    
    # Weighted average: Alternative.me (60%) + Derivatives (40%)
    # Alt.me is more established, derivatives is a live proxy
    composite_value = int(alt_fg['value'] * 0.6 + deriv_sentiment['value'] * 0.4)
    
    label = 'Extreme Greed' if composite_value > 80 else \
            'Greed' if composite_value > 65 else \
            'Neutral' if composite_value > 35 else \
            'Fear' if composite_value > 20 else \
            'Extreme Fear'
    
    return {
        'value': composite_value,
        'label': label,
        'alternative_me': alt_fg,
        'derivatives': deriv_sentiment
    }

if __name__ == '__main__':
    import json
    print(json.dumps(get_fear_greed(), indent=2))
