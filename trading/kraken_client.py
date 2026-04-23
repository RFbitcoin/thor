"""
Kraken REST API Client for THOR Live Trading
---------------------------------------------
Handles authentication, balance, orders, and trade history.
Kraken uses HMAC-SHA512 signing for private endpoints.
"""
import hashlib
import hmac
import base64
import time
import urllib.parse
import os
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / '.env')

BASE_URL  = 'https://api.kraken.com'
API_KEY   = os.getenv('KRAKEN_API_KEY', '').strip()
API_SECRET = os.getenv('KRAKEN_SECRET', '').strip()

# Kraken uses XBT internally for Bitcoin
_SYMBOL_MAP = {
    'BTC':  'XBT',
    'DOGE': 'XDG',
}
def _to_kraken_base(symbol: str) -> str:
    return _SYMBOL_MAP.get(symbol.upper(), symbol.upper())

def _to_pair(symbol: str, quote: str = 'USD') -> str:
    """Convert symbol to Kraken pair string, e.g. ETH → ETHUSD."""
    return _to_kraken_base(symbol) + quote

# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------
def _sign(urlpath: str, data: dict, secret: str) -> str:
    postdata = urllib.parse.urlencode(data)
    encoded  = (str(data['nonce']) + postdata).encode()
    message  = urlpath.encode() + hashlib.sha256(encoded).digest()
    mac      = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()

def _private(endpoint: str, params: dict = None) -> dict:
    """Call a private Kraken API endpoint."""
    if not API_KEY or not API_SECRET:
        raise RuntimeError('Kraken API keys not configured.')
    urlpath = f'/0/private/{endpoint}'
    data = {'nonce': str(int(time.time() * 1000))}
    if params:
        data.update(params)
    headers = {
        'API-Key':  API_KEY,
        'API-Sign': _sign(urlpath, data, API_SECRET),
    }
    r = requests.post(BASE_URL + urlpath, headers=headers, data=data, timeout=10)
    r.raise_for_status()
    result = r.json()
    if result.get('error'):
        raise RuntimeError(f"Kraken API error: {result['error']}")
    return result.get('result', {})

def _public(endpoint: str, params: dict = None) -> dict:
    """Call a public Kraken API endpoint."""
    r = requests.get(BASE_URL + f'/0/public/{endpoint}', params=params or {}, timeout=10)
    r.raise_for_status()
    result = r.json()
    if result.get('error'):
        raise RuntimeError(f"Kraken API error: {result['error']}")
    return result.get('result', {})

# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------
def get_balance() -> dict:
    """
    Returns cleaned balance dict: {'BTC': 0.5, 'ETH': 2.1, 'USD': 1500.0, ...}
    Filters out zero balances and maps Kraken names back to standard symbols.
    """
    raw = _private('Balance')
    _REVERSE = {'XXBT': 'BTC', 'XETH': 'ETH', 'XLTC': 'LTC',
                'XXRP': 'XRP', 'XXMR': 'XMR', 'ZUSD': 'USD',
                'ZGBP': 'GBP', 'ZEUR': 'EUR', 'XDOGE': 'DOGE', 'XDG': 'DOGE'}
    result = {}
    for k, v in raw.items():
        amount = float(v)
        if amount > 0.0000001:
            clean_key = _REVERSE.get(k, k.lstrip('XZ'))
            result[clean_key] = round(amount, 8)
    return result

_FIAT_TO_USD_FALLBACK = {
    'USD': 1.0, 'USDT': 1.0, 'USDC': 1.0,
    'CAD': 0.73, 'EUR': 1.08, 'GBP': 1.26,
    'AUD': 0.64, 'CHF': 1.11, 'JPY': 0.0067,
}

def get_usd_value(balances: dict) -> float:
    """Estimate total USD value of all balances."""
    total = 0.0
    for sym, amount in balances.items():
        if sym in _FIAT_TO_USD_FALLBACK:
            total += amount * _FIAT_TO_USD_FALLBACK[sym]
            continue
        try:
            ticker = get_ticker(sym)
            total += amount * ticker.get('price', 0)
        except Exception:
            pass
    return round(total, 2)

# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------
def get_ticker(symbol: str) -> dict:
    """Get current price for a symbol."""
    pair   = _to_pair(symbol)
    result = _public('Ticker', {'pair': pair})
    # Kraken returns data under the actual pair key (may differ from requested)
    data   = next(iter(result.values()))
    price  = float(data['c'][0])   # 'c' = last trade closed [price, volume]
    return {'symbol': symbol, 'price': price, 'pair': pair}

# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------
def get_open_orders() -> list:
    """Returns list of open orders."""
    result = _private('OpenOrders')
    orders = []
    for txid, o in result.get('open', {}).items():
        desc = o.get('descr', {})
        orders.append({
            'txid':      txid,
            'pair':      desc.get('pair', ''),
            'type':      desc.get('type', ''),      # buy / sell
            'ordertype': desc.get('ordertype', ''), # market / limit
            'price':     float(desc.get('price', 0) or 0),
            'volume':    float(o.get('vol', 0)),
            'filled':    float(o.get('vol_exec', 0)),
            'status':    o.get('status', ''),
            'opened':    o.get('opentm', 0),
        })
    return orders

def get_trade_history(count: int = 20) -> list:
    """Returns recent closed trades."""
    result = _private('TradesHistory', {'trades': True})
    trades_raw = result.get('trades', {})
    trades = []
    for txid, t in list(trades_raw.items())[:count]:
        trades.append({
            'txid':     txid,
            'pair':     t.get('pair', ''),
            'type':     t.get('type', ''),   # buy / sell
            'price':    float(t.get('price', 0)),
            'volume':   float(t.get('vol', 0)),
            'cost':     float(t.get('cost', 0)),
            'fee':      float(t.get('fee', 0)),
            'pnl':      float(t.get('net', 0) if 'net' in t else 0),
            'time':     t.get('time', 0),
        })
    # Sort newest first
    trades.sort(key=lambda x: x['time'], reverse=True)
    return trades

def place_market_order(symbol: str, side: str, volume: float) -> dict:
    """
    Place a market order.
    side: 'buy' or 'sell'
    volume: amount in base currency (e.g. 0.001 BTC)
    """
    pair = _to_pair(symbol)
    result = _private('AddOrder', {
        'pair':      pair,
        'type':      side.lower(),
        'ordertype': 'market',
        'volume':    str(volume),
    })
    return {
        'ok':    True,
        'txids': result.get('txid', []),
        'desc':  result.get('descr', {}).get('order', ''),
    }

def place_limit_order(symbol: str, side: str, volume: float, price: float) -> dict:
    """
    Place a limit order.
    side: 'buy' or 'sell'
    """
    pair = _to_pair(symbol)
    result = _private('AddOrder', {
        'pair':      pair,
        'type':      side.lower(),
        'ordertype': 'limit',
        'price':     str(price),
        'volume':    str(volume),
    })
    return {
        'ok':    True,
        'txids': result.get('txid', []),
        'desc':  result.get('descr', {}).get('order', ''),
    }

def cancel_order(txid: str) -> dict:
    """Cancel an open order by transaction ID."""
    result = _private('CancelOrder', {'txid': txid})
    return {'ok': True, 'count': result.get('count', 0)}

# ---------------------------------------------------------------------------
# Convenience: full account snapshot
# ---------------------------------------------------------------------------
def get_account_snapshot() -> dict:
    """All account data in one call — used by /api/live/status."""
    balances  = get_balance()
    usd_value = get_usd_value(balances)
    orders    = get_open_orders()
    trades    = get_trade_history(10)
    return {
        'connected':  True,
        'exchange':   'Kraken',
        'balance':    usd_value,
        'balances':   balances,
        'positions':  orders,   # open orders act as "positions" for spot
        'trades':     trades,
        'msg':        'CONNECTED',
    }


if __name__ == '__main__':
    import json
    print('Testing Kraken connection...')
    try:
        snap = get_account_snapshot()
        print(f"Connected: {snap['connected']}")
        print(f"Total USD value: ${snap['balance']:,.2f}")
        print(f"Balances: {json.dumps(snap['balances'], indent=2)}")
        print(f"Open orders: {len(snap['positions'])}")
        print(f"Recent trades: {len(snap['trades'])}")
    except Exception as e:
        print(f"Error: {e}")
