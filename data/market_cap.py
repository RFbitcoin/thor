"""
Market Cap data — Binance primary, CoinGecko for dominance (4h cache).

Binance /api/v3/ticker/24hr gives us: price, 24h volume, 24h % change.
Market cap = price × circulating supply (hardcoded approximations for top coins).
Dominance comes from CoinGecko /global, cached aggressively to avoid rate limits.
"""
import requests
import time

_cache = {}

def _cached(key):
    e = _cache.get(key)
    return e['data'] if e and time.time() < e['expires'] else None

def _cache_set(key, data, ttl=600):
    _cache[key] = {'data': data, 'expires': time.time() + ttl}

# Approximate circulating supplies (updated periodically — used for market cap estimate)
_SUPPLY = {
    'BTC':   19_800_000,
    'ETH':  120_000_000,
    'SOL':   460_000_000,
    'XRP': 57_000_000_000,
    'ADA': 35_000_000_000,
    'DOGE': 145_000_000_000,
    'AVAX':  410_000_000,
    'DOT':  1_400_000_000,
    'LINK':  587_000_000,
    'LTC':    74_000_000,
    'BCH':    19_700_000,
    'MATIC': 9_900_000_000,
    'UNI':   600_000_000,
    'ATOM':  390_000_000,
    'XLM': 30_000_000_000,
    'ALGO': 8_600_000_000,
    'VET': 86_700_000_000,
    'TRX': 86_000_000_000,
    'ETC':   148_000_000,
    'AAVE':  15_000_000,
    'NEAR': 1_100_000_000,
}

def _binance_pair(symbol: str) -> str:
    return symbol.upper() + 'USDT'


def get_market_cap(symbol='BTC'):
    """Current market cap, volume, 24h change — sourced from Binance."""
    sym = symbol.upper()
    key = f'mcap_{sym}'
    cached = _cached(key)
    if cached:
        return cached

    pair = _binance_pair(sym)
    try:
        r = requests.get(
            'https://api.binance.com/api/v3/ticker/24hr',
            params={'symbol': pair},
            timeout=8,
        )
        r.raise_for_status()
        d = r.json()

        price      = float(d.get('lastPrice', 0))
        vol_usd    = float(d.get('quoteVolume', 0))   # already in USDT
        pct_change = float(d.get('priceChangePercent', 0))

        # Market cap estimate: price × known circulating supply
        supply   = _SUPPLY.get(sym, 0)
        mcap_usd = price * supply if supply else 0

        data = {
            'market_cap_usd':   round(mcap_usd),
            'volume_24h_usd':   round(vol_usd),
            'mcap_change_24h':  round(pct_change, 2),   # price % ≈ mcap % (supply is constant)
            'price_change_24h': round(pct_change, 2),
            'current_price':    round(price, 2),
            'high_24h':         round(float(d.get('highPrice', 0)), 2),
            'low_24h':          round(float(d.get('lowPrice', 0)), 2),
            'symbol':           sym,
            'source':           'binance',
        }
        _cache_set(key, data, 300)   # 5-min cache
        return data

    except Exception as e:
        print(f'[MCAP] Error for {sym}: {e}')
        return {
            'market_cap_usd': 0, 'volume_24h_usd': 0,
            'mcap_change_24h': 0, 'price_change_24h': 0,
            'current_price': 0, 'symbol': sym, 'source': 'error',
        }


def get_dominance():
    """BTC/ETH market dominance from CoinGecko — 4-hour cache to avoid rate limits."""
    cached = _cached('btc_dominance')
    if cached:
        return cached

    try:
        r = requests.get(
            'https://api.coingecko.com/api/v3/global',
            timeout=8,
        )
        r.raise_for_status()
        pct = r.json().get('data', {}).get('market_cap_percentage', {})

        result = {
            'btc_dominance': round(pct.get('btc', 0), 2),
            'eth_dominance': round(pct.get('eth', 0), 2),
            'source':        'coingecko',
        }
        _cache_set('btc_dominance', result, 4 * 3600)   # cache 4 hours
        return result

    except Exception as e:
        print(f'[DOMINANCE] Error: {e}')
        # Return last known reasonable values as fallback
        return {'btc_dominance': 57.0, 'eth_dominance': 11.0, 'source': 'fallback'}


def get_mcap_history(symbol='BTC', days=30):
    """Daily close prices from Binance as market cap sparkline proxy."""
    sym  = symbol.upper()
    key  = f'mcap_hist_binance_{sym}_{days}'
    cached = _cached(key)
    if cached:
        return cached

    pair = _binance_pair(sym)
    try:
        r = requests.get(
            'https://api.binance.com/api/v3/klines',
            params={'symbol': pair, 'interval': '1d', 'limit': days},
            timeout=8,
        )
        r.raise_for_status()
        history = [[int(k[0]), float(k[4])] for k in r.json()]
        data = {'history': history, 'symbol': sym, 'days': days, 'source': 'binance'}
        _cache_set(key, data, 600)
        return data

    except Exception as e:
        print(f'[MCAP HISTORY] Error for {sym}: {e}')
        return {'history': [], 'symbol': sym, 'days': days, 'source': 'error'}


if __name__ == '__main__':
    import json
    print('BTC Market Cap:')
    print(json.dumps(get_market_cap('BTC'), indent=2))
    print('\nDominance:')
    print(json.dumps(get_dominance(), indent=2))
    print('\nHistory (5 pts):')
    h = get_mcap_history('BTC', 5)
    print(json.dumps(h, indent=2))
