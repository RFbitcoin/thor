import urllib.request, urllib.parse, hashlib, hmac, base64, time, json, os

CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.json')

def _load_keys():
    with open(CONFIG_FILE) as f:
        cfg = json.load(f)
    return cfg['kraken']['api_key'], cfg['kraken']['private_key']

def _kraken_request(uri_path, data={}):
    api_key, private_key = _load_keys()
    api_nonce = str(int(time.time() * 1000))
    data['nonce'] = api_nonce
    post_data = urllib.parse.urlencode(data)
    encoded = (api_nonce + post_data).encode()
    message = uri_path.encode() + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(private_key), message, hashlib.sha512)
    sig = base64.b64encode(mac.digest()).decode()
    headers = {'API-Key': api_key, 'API-Sign': sig, 'Content-Type': 'application/x-www-form-urlencoded'}
    req = urllib.request.Request('https://api.kraken.com' + uri_path, post_data.encode(), headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())

def get_balance():
    result = _kraken_request('/0/private/Balance')
    if result.get('error'):
        return {'error': result['error']}
    balances = {}
    for k, v in result.get('result', {}).items():
        val = float(v)
        if val > 0:
            # Normalize asset names (Kraken prefixes with X/Z)
            name = k
            if k.startswith('X') and len(k) == 4: name = k[1:]
            if k.startswith('Z') and len(k) == 4: name = k[1:]
            balances[name] = val
    return balances

def get_open_orders():
    result = _kraken_request('/0/private/OpenOrders')
    if result.get('error'):
        return {'error': result['error']}
    orders = []
    for oid, o in result.get('result', {}).get('open', {}).items():
        d = o.get('descr', {})
        orders.append({
            'id': oid,
            'pair': d.get('pair'),
            'type': d.get('type'),
            'ordertype': d.get('ordertype'),
            'price': d.get('price'),
            'volume': o.get('vol'),
            'vol_exec': o.get('vol_exec'),
            'status': o.get('status'),
            'opened': o.get('opentm')
        })
    return orders

def get_trade_history():
    result = _kraken_request('/0/private/TradesHistory', {'trades': True})
    if result.get('error'):
        return {'error': result['error']}
    trades = []
    for tid, t in list(result.get('result', {}).get('trades', {}).items())[:20]:
        trades.append({
            'id': tid,
            'pair': t.get('pair'),
            'type': t.get('type'),
            'price': float(t.get('price', 0)),
            'vol': float(t.get('vol', 0)),
            'cost': float(t.get('cost', 0)),
            'fee': float(t.get('fee', 0)),
            'time': t.get('time')
        })
    trades.sort(key=lambda x: x['time'], reverse=True)
    return trades
