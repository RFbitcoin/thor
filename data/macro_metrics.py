"""
Macro DeFi & ETF metrics from free public APIs.
- TVL (DefiLlama protocols)
- Stablecoin market cap (CoinGecko: USDT, USDC, DAI, BUSD, etc.)
- Volume & 24h flows (CoinGecko BTC/ETH volume + change)

Note: Real ETF flow data requires paid subscriptions (Glassnode, CoinShares).
This uses volume + % change as institutional interest proxy.
"""
import requests
import time

_cache = {}
CACHE_TTL = 300  # Cache macro for 5 minutes (less volatile)

def _cached_get(key, url, timeout=10):
    """Helper: cached HTTP GET."""
    if key in _cache and time.time() < _cache[key]['expires']:
        return _cache[key]['data']
    
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        _cache[key] = {'data': data, 'expires': time.time() + CACHE_TTL}
        return data
    except Exception as e:
        print(f"[ERROR] {key}: {e}")
        if key in _cache:
            return _cache[key]['data']
    return None

def get_defi_tvl():
    """Total Value Locked in DeFi (DefiLlama protocols)."""
    data = _cached_get('defi_tvl', 'https://api.llama.fi/protocols')
    if not data:
        return {
            'tvl_usd': None,
            'top_protocols': [],
            'signal': 'UNKNOWN',
            'reason': 'DefiLlama unavailable'
        }
    
    try:
        if isinstance(data, list):
            total_tvl = sum(float(p.get('tvl', 0)) for p in data if isinstance(p, dict) and p.get('tvl') is not None)
            top = sorted([{'name': p.get('name'), 'tvl': p.get('tvl')} for p in data if p.get('tvl') is not None], 
                        key=lambda x: x.get('tvl', 0), reverse=True)[:5]
        else:
            total_tvl = 0
            top = []
        
        signal = 'BULLISH' if total_tvl > 100e9 else 'NEUTRAL' if total_tvl > 50e9 else 'BEARISH'
        
        return {
            'tvl_usd': total_tvl,
            'top_protocols': top,
            'signal': signal,
            'reason': f'DeFi TVL: ${total_tvl/1e9:.2f}B'
        }
    except Exception as e:
        print(f"Error parsing TVL: {e}")
        return {
            'tvl_usd': None,
            'top_protocols': [],
            'signal': 'UNKNOWN',
            'reason': str(e)
        }

def get_stablecoin_market_cap():
    """Stablecoin market cap (USDC, USDT, DAI, BUSD, etc.)."""
    stables = ['tether', 'usd-coin', 'binance-usd', 'dai', 'true-usd']
    url = f'https://api.coingecko.com/api/v3/simple/price?ids={",".join(stables)}&vs_currencies=usd&include_market_cap=true'
    data = _cached_get('stablecoin_mcap', url)
    
    if not data:
        return {
            'stablecoin_mcap': None,
            'top_stables': {},
            'signal': 'UNKNOWN',
            'reason': 'CoinGecko unavailable'
        }
    
    try:
        total_mcap = sum(float(v.get('usd_market_cap', 0)) for v in data.values() if isinstance(v, dict))
        top_stables = {k: v.get('usd_market_cap') for k, v in data.items() if v.get('usd_market_cap')}
        top_stables = dict(sorted(top_stables.items(), key=lambda x: x[1], reverse=True))
        
        signal = 'BULLISH' if total_mcap > 150e9 else 'NEUTRAL' if total_mcap > 100e9 else 'BEARISH' if total_mcap > 0 else 'UNKNOWN'
        
        return {
            'stablecoin_mcap': total_mcap,
            'top_stables': top_stables,
            'signal': signal,
            'reason': f'Stablecoin mcap: ${total_mcap/1e9:.2f}B'
        }
    except Exception as e:
        print(f"Error parsing stablecoin mcap: {e}")
        return {
            'stablecoin_mcap': None,
            'top_stables': {},
            'signal': 'UNKNOWN',
            'reason': str(e)
        }

def get_volume_flows():
    """Bitcoin & Ethereum 24h volume & market activity."""
    url = 'https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_market_cap=true&include_24h_vol=true&include_24h_change=true'
    data = _cached_get('btc_eth_volume', url)
    
    if not data:
        return {
            'btc_vol_24h': None,
            'eth_vol_24h': None,
            'signal': 'UNKNOWN',
            'reason': 'CoinGecko unavailable'
        }
    
    try:
        btc_data = data.get('bitcoin', {})
        eth_data = data.get('ethereum', {})
        
        btc_vol = btc_data.get('usd_24h_vol')
        eth_vol = eth_data.get('usd_24h_vol')
        btc_change = btc_data.get('usd_24h_change', 0)
        eth_change = eth_data.get('usd_24h_change', 0)
        
        # High volume + positive change = institutional buying
        if btc_vol and btc_vol > 30e9 and btc_change > 0:
            signal = 'BULLISH'
        elif btc_vol and (btc_vol < 15e9 or btc_change < -3):
            signal = 'BEARISH'
        else:
            signal = 'NEUTRAL'
        
        reason = f'BTC vol: ${btc_vol/1e9:.2f}B, change: {btc_change:+.2f}%' if btc_vol else 'Volume data unavailable'
        
        return {
            'btc_vol_24h': btc_vol,
            'eth_vol_24h': eth_vol,
            'btc_change_24h': btc_change,
            'eth_change_24h': eth_change,
            'signal': signal,
            'reason': reason
        }
    except Exception as e:
        print(f"Error parsing volume/flows: {e}")
        return {
            'btc_vol_24h': None,
            'eth_vol_24h': None,
            'signal': 'UNKNOWN',
            'reason': str(e)
        }

def get_macro_summary():
    """Aggregate all macro metrics."""
    return {
        'tvl': get_defi_tvl(),
        'stablecoin_mcap': get_stablecoin_market_cap(),
        'volume_flows': get_volume_flows()
    }

if __name__ == '__main__':
    import json
    summary = get_macro_summary()
    print(json.dumps(summary, indent=2, default=str))
