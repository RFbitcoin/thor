"""
Derivatives microstructure signals from Binance Futures API.
Covers: funding rate, open interest, long/short ratio, spot vs perp volume.
"""
import requests

FAPI = 'https://fapi.binance.com'

def _pair(symbol):
    s = symbol.upper()
    return s if s.endswith('USDT') else s + 'USDT'

def get_funding_rate(symbol='BTC'):
    """Current and recent funding rates. >0.01% = longs paying = bullish crowding."""
    pair = _pair(symbol)
    try:
        # Current rate
        r = requests.get(f'{FAPI}/fapi/v1/premiumIndex', params={'symbol': pair}, timeout=10)
        current = float(r.json().get('lastFundingRate', 0))
        # Last 8 periods (24h)
        r2 = requests.get(f'{FAPI}/fapi/v1/fundingRate',
            params={'symbol': pair, 'limit': 8}, timeout=10)
        history = [float(x['fundingRate']) for x in r2.json()]
        avg_24h = sum(history) / len(history) if history else current
        # Signal: extreme positive = crowded longs = bearish contrarian
        # extreme negative = crowded shorts = bullish contrarian
        if current > 0.0005:     signal = 'CROWDED_LONG'    # bearish contrarian
        elif current > 0.0001:   signal = 'MILD_LONG'
        elif current < -0.0003:  signal = 'CROWDED_SHORT'   # bullish contrarian
        elif current < -0.0001:  signal = 'MILD_SHORT'
        else:                    signal = 'NEUTRAL'
        return {
            'current': round(current * 100, 5),   # as percentage
            'avg_24h': round(avg_24h * 100, 5),
            'signal': signal,
            'score': -1 if 'CROWDED_LONG' in signal else 1 if 'CROWDED_SHORT' in signal else 0
        }
    except Exception as e:
        print(f'Funding rate error: {e}')
        return {'current': 0, 'avg_24h': 0, 'signal': 'UNKNOWN', 'score': 0}

def get_open_interest(symbol='BTC'):
    """OI trend — rising OI + rising price = bullish; rising OI + falling price = bearish."""
    pair = _pair(symbol)
    try:
        # OI history (5min intervals, last 24 periods = 2h)
        r = requests.get(f'{FAPI}/futures/data/openInterestHist',
            params={'symbol': pair, 'period': '1h', 'limit': 24}, timeout=10)
        data = r.json()
        if not data: return {'oi': 0, 'oi_change_24h': 0, 'signal': 'UNKNOWN', 'score': 0}
        oi_now = float(data[-1]['sumOpenInterest'])
        oi_24h_ago = float(data[0]['sumOpenInterest'])
        oi_change = ((oi_now - oi_24h_ago) / oi_24h_ago) * 100 if oi_24h_ago else 0
        if oi_change > 5:    signal = 'RISING'
        elif oi_change < -5: signal = 'FALLING'
        else:                signal = 'STABLE'
        return {
            'oi': round(oi_now, 0),
            'oi_change_24h': round(oi_change, 2),
            'signal': signal,
            'score': 0  # OI alone is neutral; combined with price direction in aggregator
        }
    except Exception as e:
        print(f'OI error: {e}')
        return {'oi': 0, 'oi_change_24h': 0, 'signal': 'UNKNOWN', 'score': 0}

def get_long_short_ratio(symbol='BTC'):
    """Long/short account ratio. Extreme longs = contrarian bearish."""
    pair = _pair(symbol)
    try:
        r = requests.get(f'{FAPI}/futures/data/globalLongShortAccountRatio',
            params={'symbol': pair, 'period': '1h', 'limit': 24}, timeout=10)
        data = r.json()
        if not data: return {'ratio': 1.0, 'longs_pct': 50, 'signal': 'NEUTRAL', 'score': 0}
        latest = data[-1]
        ratio = float(latest['longShortRatio'])
        longs_pct = float(latest['longAccount']) * 100
        # Contrarian: >70% longs = crowded = bearish; <40% longs = bearish sentiment = bullish
        if longs_pct > 70:   signal = 'CROWDED_LONG';  score = -1
        elif longs_pct > 60: signal = 'LEANING_LONG';  score = 0
        elif longs_pct < 35: signal = 'CROWDED_SHORT'; score = 1
        elif longs_pct < 45: signal = 'LEANING_SHORT'; score = 0
        else:                signal = 'BALANCED';      score = 0
        return {'ratio': round(ratio, 3), 'longs_pct': round(longs_pct, 1),
                'signal': signal, 'score': score}
    except Exception as e:
        print(f'L/S ratio error: {e}')
        return {'ratio': 1.0, 'longs_pct': 50, 'signal': 'NEUTRAL', 'score': 0}

def get_spot_perp_volume(symbol='BTC'):
    """Spot vs perp volume ratio. High perp/spot = speculative; high spot = real demand."""
    pair = _pair(symbol)
    spot_pair = pair  # same pair on spot
    try:
        spot = requests.get('https://api.binance.com/api/v3/ticker/24hr',
            params={'symbol': spot_pair}, timeout=10).json()
        perp = requests.get(f'{FAPI}/fapi/v1/ticker/24hr',
            params={'symbol': pair}, timeout=10).json()
        spot_vol = float(spot.get('quoteVolume', 0))
        perp_vol = float(perp.get('quoteVolume', 0))
        ratio = perp_vol / spot_vol if spot_vol > 0 else 1.0
        if ratio > 3:    signal = 'SPECULATIVE'  # perp dominates = speculative
        elif ratio < 0.8: signal = 'SPOT_LED'    # spot leads = genuine demand
        else:             signal = 'BALANCED'
        score = -1 if signal == 'SPECULATIVE' else 1 if signal == 'SPOT_LED' else 0
        return {'spot_vol': round(spot_vol, 0), 'perp_vol': round(perp_vol, 0),
                'ratio': round(ratio, 2), 'signal': signal, 'score': score}
    except Exception as e:
        print(f'Spot/perp error: {e}')
        return {'spot_vol': 0, 'perp_vol': 0, 'ratio': 1.0, 'signal': 'UNKNOWN', 'score': 0}

def get_derivatives_summary(symbol='BTC'):
    """Full derivatives pillar — returns all metrics + composite score."""
    funding = get_funding_rate(symbol)
    oi = get_open_interest(symbol)
    ls = get_long_short_ratio(symbol)
    spv = get_spot_perp_volume(symbol)
    score = funding['score'] + ls['score'] + spv['score']
    
    # Add liquidations data (placeholder for now)
    liquidations_24h = 45000000 if symbol == 'BTC' else 8000000  # USD estimates
    liquidations_change = 2.1  # % change
    
    return {
        'funding': funding,
        'open_interest': oi,
        'long_short': ls,
        'spot_perp': spv,
        'liquidations_24h': liquidations_24h,
        'liquidations_change_24h': liquidations_change,
        'score': score,
        'max_score': 3
    }

if __name__ == '__main__':
    import json
    print(json.dumps(get_derivatives_summary('BTC'), indent=2))
