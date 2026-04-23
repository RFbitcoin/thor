import requests

# Binance public API — no key required
SYMBOL_MAP = {'BTC': 'BTCUSDT', 'ETH': 'ETHUSDT', 'XRP': 'XRPUSDT', 'SOL': 'SOLUSDT'}

def _to_pair(symbol):
    s = symbol.upper()
    return SYMBOL_MAP.get(s, s + 'USDT')

def _binance_klines(symbol, interval, limit):
    pair = _to_pair(symbol)
    try:
        r = requests.get('https://api.binance.com/api/v3/klines',
            params={'symbol': pair, 'interval': interval, 'limit': limit},
            timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f'Binance error: {e}')
        return []

def get_ohlcv(symbol, days=30):
    """Returns dict with 'prices' list of [timestamp_ms, close] — used by indicators.py"""
    limit = min(days, 1000)
    raw = _binance_klines(symbol, '1d', limit)
    prices = [[int(d[0]), float(d[4])] for d in raw]
    return {'prices': prices}

def get_price_binance(symbol):
    """Get current price for any symbol from Binance."""
    pair = _to_pair(symbol)
    try:
        r = requests.get('https://api.binance.com/api/v3/ticker/24hr',
            params={'symbol': pair}, timeout=10)
        r.raise_for_status()
        d = r.json()
        return {
            'price': float(d['lastPrice']),
            'change_24h': float(d['priceChangePercent']),
            'change_7d': 0,
            'volume_24h': float(d['quoteVolume']),
            'market_cap': 0
        }
    except Exception as e:
        print(f'Binance price error: {e}')
        return {}

def get_ohlcv_candles(symbol, days=30):
    """Returns candle list for LightweightCharts"""
    if days <= 1:
        interval, limit = '30m', 48
    elif days <= 7:
        interval, limit = '4h', 42
    elif days <= 30:
        interval, limit = '1d', 30
    else:
        interval, limit = '1d', min(days, 365)
    raw = _binance_klines(symbol, interval, limit)
    seen = set()
    candles = []
    for d in raw:
        t = int(d[0] // 1000)
        if t not in seen:
            seen.add(t)
            candles.append({'time': t, 'open': float(d[1]), 'high': float(d[2]),
                            'low': float(d[3]), 'close': float(d[4]), 'volume': float(d[5])})
    return sorted(candles, key=lambda x: x['time'])
