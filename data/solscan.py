"""
Solana on-chain data via free Solana RPC (no auth needed).
Gets price from Binance, chain metrics from mainnet-beta RPC.
"""
import requests
import time

SOLANA_RPC = "https://api.mainnet-beta.solana.com"

_cache = {}
CACHE_TTL = 300  # 5 min cache

def _cached(key):
    if key in _cache:
        entry = _cache[key]
        if time.time() < entry['expires']:
            return entry['data']
    return None

def _cache_set(key, data):
    _cache[key] = {'data': data, 'expires': time.time() + CACHE_TTL}

def get_sol_price():
    """Get SOL/USD from Binance (free, no auth)."""
    cached = _cached('sol_price')
    if cached:
        return cached
    
    try:
        res = requests.get(
            "https://api.binance.com/api/v3/ticker/24hr",
            params={'symbol': 'SOLUSDT'},
            timeout=5
        )
        res.raise_for_status()
        d = res.json()
        data = {
            'price': float(d['lastPrice']),
            'change_24h': float(d['priceChangePercent']),
            'volume_24h': float(d['quoteVolume']),
            'source': 'binance'
        }
        _cache_set('sol_price', data)
        return data
    except Exception as e:
        print(f"[SOL] Price error: {e}")
        return {'price': 0, 'change_24h': 0, 'volume_24h': 0, 'source': 'error'}

def get_chain_metrics():
    """Get Solana chain health metrics via free RPC."""
    cached = _cached('sol_chain')
    if cached:
        return cached
    
    try:
        # Get current slot (chain speed indicator)
        res = requests.post(
            SOLANA_RPC,
            json={"jsonrpc": "2.0", "id": 1, "method": "getSlot"},
            timeout=5
        )
        res.raise_for_status()
        slot = res.json().get('result', 0)
        
        # Get supply (total SOL)
        res = requests.post(
            SOLANA_RPC,
            json={"jsonrpc": "2.0", "id": 1, "method": "getSupply"},
            timeout=5
        )
        res.raise_for_status()
        supply = res.json().get('result', {}).get('value', {})
        total_sol = supply.get('total', 0) / 1e9  # lamports to SOL
        
        # Get active validator count
        res = requests.post(
            SOLANA_RPC,
            json={"jsonrpc": "2.0", "id": 1, "method": "getVoteAccounts"},
            timeout=5
        )
        res.raise_for_status()
        vote_accts = res.json().get('result', {})
        active_validators = len(vote_accts.get('current', []))
        
        data = {
            'slot': slot,
            'total_supply_sol': round(total_sol, 2),
            'active_validators': active_validators,
            'network_status': 'HEALTHY' if slot > 0 else 'DOWN',
            'source': 'solana_rpc'
        }
        _cache_set('sol_chain', data)
        return data
    except Exception as e:
        print(f"[SOL] Chain metrics error: {e}")
        return {
            'slot': 0,
            'total_supply_sol': 0,
            'active_validators': 0,
            'network_status': 'ERROR',
            'source': 'error'
        }

def get_sol_summary():
    """Complete SOL metrics summary."""
    return {
        'price': get_sol_price(),
        'chain': get_chain_metrics(),
        'timestamp': time.time(),
        'source': 'solana_rpc_free'
    }

if __name__ == '__main__':
    import json
    result = get_sol_summary()
    print(json.dumps(result, indent=2))
    print(f"\n✓ SOL: ${result['price']['price']} ({result['price']['change_24h']:+.2f}%)")
    print(f"✓ Network: {result['chain']['network_status']} | Validators: {result['chain']['active_validators']}")
