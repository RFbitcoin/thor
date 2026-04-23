import sys, os, time
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
import requests
import sys, os as os_module
sys.path.insert(0, os_module.path.dirname(os_module.path.abspath(__file__)))
from auth import (require_auth, login as auth_login, logout as auth_logout,
                  is_first_run as auth_is_first_run,
                  setup_password as auth_setup,
                  change_password as auth_change_password)
from data.cmc import get_price
from data.fred import get_macro
from signals.aggregator import get_signal
from signals.alert_detector import detect_alerts
from data.coingecko import get_ohlcv_candles
from data.macro_metrics import get_macro_summary
from trading.paper import get_portfolio, buy, sell, sell_short, close, reset
from trading.gmx_client import GMXClient
from trading.auto_trader import AutoTrader

# ── GMX / Auto-trader singletons (initialised once at startup) ───────────────
_gmx_client  = GMXClient()
_auto_trader = AutoTrader(_gmx_client, lambda sym: get_signal(sym))
from data.indicators import get_rsi, get_ma
from data.market_cap import get_market_cap, get_dominance, get_mcap_history
import json

app = Flask(__name__)
CORS(app)

# ---------------------------------------------------------------------------
# Simple TTL cache
# ---------------------------------------------------------------------------
_cache = {}

def cache_get(key):
    entry = _cache.get(key)
    if entry and time.time() < entry['expires']:
        return entry['data']
    return None

def cache_set(key, data, ttl):
    _cache[key] = {'data': data, 'expires': time.time() + ttl}

# ---------------------------------------------------------------------------
# Kraken lockout tracker — backs off for KRAKEN_LOCKOUT_TTL seconds after a
# EGeneral:Temporary lockout response so we stop hammering the API.
# ---------------------------------------------------------------------------
_kraken_lockout_until = 0
KRAKEN_LOCKOUT_TTL = 15 * 60  # 15 minutes

# ---------------------------------------------------------------------------
# Alert system — log signal changes and events
# ---------------------------------------------------------------------------
MAX_ALERTS = 200
ALERT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'trading', 'alerts.json')
_last_signals = {}  # Track last signal per symbol to detect changes

# Load persisted alerts from disk on startup
try:
    with open(ALERT_FILE, 'r') as _af:
        _alerts = json.load(_af)[:MAX_ALERTS]
except Exception:
    _alerts = []

def log_alert(alert_type, symbol, message, data=None):
    """Log an alert to memory and disk"""
    alert = {
        'timestamp': time.time(),
        'type': alert_type,  # 'signal_change', 'price_alert', 'error', 'info'
        'symbol': symbol,
        'message': message,
        'data': data
    }
    _alerts.insert(0, alert)  # Newest first
    if len(_alerts) > MAX_ALERTS:
        _alerts.pop()
    # Persist to disk
    try:
        import json
        os.makedirs(os.path.dirname(ALERT_FILE), exist_ok=True)
        with open(ALERT_FILE, 'w') as f:
            json.dump(_alerts, f)
    except:
        pass

def detect_signal_change(symbol, current_signal):
    """Detect if signal changed for a symbol"""
    last = _last_signals.get(symbol)
    if last != current_signal:
        _last_signals[symbol] = current_signal
        if last is not None:  # Only alert on change, not first load
            log_alert('signal_change', symbol, f'Signal changed: {last} → {current_signal}', {'from': last, 'to': current_signal})
        return True
    return False


# ---------------------------------------------------------------------------
# Routes — static
# ---------------------------------------------------------------------------

@app.route('/')
def landing():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'landing.html')

@app.route('/dashboard')
def index():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'index.html')

@app.route('/about')
def about():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'about.html')

@app.route('/dist/<path:filename>')
def dist_files(filename):
    dist_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'dist')
    return send_from_directory(dist_dir, filename, as_attachment=True)

@app.route('/robots.txt')
def robots():
    from flask import Response
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Allow: /about\n"
        "Disallow: /api/\n"
        "Disallow: /api/auth/\n\n"
        "Sitemap: https://thor.rfbitcoin.com/sitemap.xml\n"
    )
    return Response(content, mimetype='text/plain')

@app.route('/sitemap.xml')
def sitemap():
    from flask import Response
    content = '''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://thor.rfbitcoin.com/</loc>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>https://thor.rfbitcoin.com/about</loc>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
</urlset>'''
    return Response(content, mimetype='application/xml')

@app.route('/manifest.json')
def manifest():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'manifest.json')

@app.route('/service-worker.js')
def service_worker():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'service-worker.js', mimetype='application/javascript')

@app.route('/icon.png')
def icon():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'icon.png')

@app.route('/icon-192.png')
def icon_192():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'icon-192.png')

@app.route('/icon-512.png')
def icon_512():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'icon-512.png')

@app.route('/THOR_Overview.md')
def overview():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'THOR_Overview.md', mimetype='text/markdown')

@app.route('/<path:filename>')
def serve_static(filename):
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), filename)


# ---------------------------------------------------------------------------
# Routes — data (all cached)
# ---------------------------------------------------------------------------

@app.route('/api/quick/<symbol>')
def quick_signal(symbol):
    """Fast endpoint: price + RSI only (no full aggregation). ~500ms."""
    sym = symbol.upper()
    key = f'quick_{sym}'
    cached = cache_get(key)
    if cached is not None:
        return jsonify(cached)
    from data.indicators import get_rsi, get_ma
    from data.cmc import get_price
    import requests as req
    
    prefer_kraken = request.args.get('prefer') == 'kraken'

    def _kraken_price(sym):
        from trading.kraken_client import _to_pair as _kpair, _public as _kpub
        kraken_pair = _kpair(sym, 'USD')
        kdata = _kpub('Ticker', {'pair': kraken_pair})
        krow  = next(iter(kdata.values()))
        price  = float(krow['c'][0])
        k_open = float(krow['o'])
        change = round((price - k_open) / k_open * 100, 2) if k_open else 0
        return {'price': price, 'price_change_24h': change}

    def _binance_price(sym):
        pair   = sym.upper() + 'USDT'
        ticker = req.get(f'https://api.binance.com/api/v3/ticker/24hr?symbol={pair}', timeout=3).json()
        if 'priceChangePercent' not in ticker:
            raise ValueError('not on Binance')
        return {
            'price':            float(ticker.get('lastPrice', 0)) or None,
            'price_change_24h': float(ticker['priceChangePercent']),
        }

    p = {}
    if prefer_kraken:
        # Live mode: Kraken price first, Binance fallback
        try:
            p = _kraken_price(sym)
        except Exception:
            try:
                p = _binance_price(sym)
            except Exception:
                pass
    else:
        # Default: Binance first, Kraken fallback
        try:
            p = _binance_price(sym)
        except Exception:
            try:
                p = _kraken_price(sym)
            except Exception:
                pass

    if not p.get('price'):
        from data.coingecko import get_price_binance
        fallback = get_price_binance(sym) or {}
        p.setdefault('price', fallback.get('price'))
        p.setdefault('price_change_24h', 0)
    
    # Get RSI
    rsi_data = get_rsi(sym)
    rsi = rsi_data.get('rsi', 50)
    
    # Get MA
    ma_data = get_ma(sym)
    
    data = {
        'symbol': sym,
        'price': p.get('price'),
        'price_change_24h': p.get('price_change_24h', 0),
        'rsi': rsi,
        'ma': ma_data,
        'quick': True  # Flag to indicate partial data
    }
    cache_set(key, data, 20)
    return jsonify(data)

@app.route('/api/signals/multi')
def signals_multi():
    """Return BTC, ETH, SOL signals in one call for multi-asset overview."""
    result = {}
    for sym in ['BTC', 'ETH', 'SOL']:
        cached = cache_get(f'signal_{sym}')
        if cached:
            result[sym] = cached
        else:
            try:
                data = get_signal(sym)
                cache_set(f'signal_{sym}', data, 20)
                result[sym] = data
            except Exception as e:
                result[sym] = {'error': str(e)}
    return jsonify(result)

@app.route('/api/signal/<symbol>')
def signal(symbol):
    sym = symbol.upper()
    key = f'signal_{sym}'
    cached = cache_get(key)
    if cached is not None:
        return jsonify(cached)
    data = get_signal(sym)
    # Log signal for performance analytics (BTC only, silent)
    if sym == 'BTC':
        try:
            from analytics.signal_logger import log_signal
            log_signal(data)
        except Exception:
            pass
    # Detect signal changes
    signal_val = data.get('signal', 'NEUTRAL')
    detect_signal_change(sym, signal_val)
    # Detect alerts (regime change, conviction thresholds, etc.)
    alerts = detect_alerts(data)
    if alerts:
        for alert in alerts:
            log_alert(alert['type'], sym, alert['message'], alert['data'])
    # Telegram alert on strong signal
    try:
        conviction = data.get('conviction', 0)
        composite  = data.get('composite', 0)
        price      = data.get('price') or 0
        regime     = data.get('regime', 'RANGING')
        if conviction >= 70 and abs(composite) >= 0.35:
            direction = 'BUY' if composite > 0 else 'SELL'
            from notifications.telegram import alert_strong_signal
            alert_strong_signal(direction, conviction, composite, price, regime)
    except Exception:
        pass
    cache_set(key, data, 20)  # 20s TTL so indicators update more frequently
    return jsonify(data)


@app.route('/api/prices')
def prices():
    symbols_param = request.args.get('symbols', 'BTC,ETH,XRP,SOL')
    symbols = [s.strip().upper() for s in symbols_param.split(',')]
    cache_key = f'prices_{",".join(sorted(symbols))}'
    cached = cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)
    data = get_price(symbols)
    cache_set(cache_key, data, 30)
    return jsonify(data)


@app.route('/api/macro')
def macro():
    cached = cache_get('macro')
    if cached is not None:
        return jsonify(cached)
    # Combine FRED macro + DeFi metrics
    fred_data = get_macro()
    defi_data = get_macro_summary()
    data = {
        'fred': fred_data,
        'defi': defi_data
    }
    cache_set('macro', data, 600)  # Cache for 10 min (more volatile with DeFi)
    return jsonify(data)


@app.route('/api/ohlc/<symbol>')
def ohlc(symbol):
    days = int(request.args.get('days', 30))
    key = f'ohlc_{symbol.upper()}_{days}'
    cached = cache_get(key)
    if cached is not None:
        return jsonify(cached)
    data = get_ohlcv_candles(symbol.upper(), days)
    cache_set(key, data, 300)
    return jsonify(data)


@app.route('/api/volume/<symbol>')
def volume(symbol):
    key = f'volume_{symbol.upper()}'
    cached = cache_get(key)
    if cached is not None:
        return jsonify(cached)
    from data.coingecko import _binance_klines
    raw = _binance_klines(symbol.upper(), '1d', 30)
    if not raw:
        return jsonify({'error': 'no data'})
    vols = [float(d[7]) for d in raw]
    vol_24h = vols[-1]
    avg_7d = sum(vols[-7:]) / 7
    avg_30d = sum(vols) / len(vols)
    ratio = vol_24h / avg_30d if avg_30d else 1
    sig = 'HIGH' if ratio > 1.5 else 'LOW' if ratio < 0.5 else 'NORMAL'
    data = {'vol_24h': vol_24h, 'avg_7d': avg_7d, 'avg_30d': avg_30d, 'ratio': round(ratio, 2), 'signal': sig}
    cache_set(key, data, 300)
    return jsonify(data)


@app.route('/api/ticker/<symbol>')
def ticker(symbol):
    import requests as req
    sym = symbol.upper()
    key = f'ticker_{sym}'
    cached = cache_get(key)
    if cached is not None:
        return jsonify(cached)
    pair = sym + 'USDT'
    try:
        t = req.get(f'https://api.binance.com/api/v3/ticker/24hr?symbol={pair}', timeout=5).json()
        price = float(t.get('lastPrice', 0))
        high = float(t.get('highPrice', 0))
        low = float(t.get('lowPrice', 0))
        vol_usd = float(t.get('quoteVolume', 0))
        mc_data = {}
        try:
            cg_id = {'BTC': 'bitcoin', 'ETH': 'ethereum', 'XRP': 'ripple', 'SOL': 'solana'}.get(sym, sym.lower())
            cg = req.get(
                f'https://api.coingecko.com/api/v3/coins/{cg_id}?localization=false&tickers=false&community_data=false&developer_data=false',
                timeout=5).json()
            mc = cg.get('market_data', {})
            mc_data['market_cap'] = mc.get('market_cap', {}).get('usd', 0)
            mc_data['circulating_supply'] = mc.get('circulating_supply', 0)
            mc_data['ath'] = mc.get('ath', {}).get('usd', 0)
            mc_data['ath_change_pct'] = mc.get('ath_change_percentage', {}).get('usd', 0)
        except:
            pass
        dom = None
        if sym == 'BTC':
            try:
                g = req.get('https://api.coingecko.com/api/v3/global', timeout=5).json()
                dom = g.get('data', {}).get('market_cap_percentage', {}).get('btc')
            except:
                pass
        data = {'price': price, 'high_24h': high, 'low_24h': low, 'volume_usd': vol_usd, 'dominance': dom, **mc_data}
        cache_set(key, data, 30)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/network')
def network():
    import requests as req
    cached = cache_get('network')
    if cached is not None:
        return jsonify(cached)
    out = {}
    try:
        s = req.get('https://blockchain.info/stats?format=json', timeout=6).json()
        out['block_height'] = s.get('n_blocks_total')
        out['hash_rate_eh'] = round(s.get('hash_rate', 0) / 1e9, 2)
        out['difficulty'] = s.get('difficulty')
        out['minutes_between_blocks'] = round(s.get('minutes_between_blocks', 0), 1)
        out['total_fees_btc'] = round(s.get('total_fees_btc', 0) / 1e8, 4)
    except Exception as e:
        out['error_chain'] = str(e)
    try:
        m = req.get('https://mempool.space/api/mempool', timeout=5).json()
        out['mempool_tx'] = m.get('count')
        out['mempool_vsize_mb'] = round(m.get('vsize', 0) / 1e6, 1)
        fees = req.get('https://mempool.space/api/v1/fees/recommended', timeout=5).json()
        out['fee_fast'] = fees.get('fastestFee')
        out['fee_med'] = fees.get('halfHourFee')
        out['fee_slow'] = fees.get('hourFee')
    except Exception as e:
        out['error_mempool'] = str(e)
    try:
        height = out.get('block_height', 0)
        if height:
            next_halving = ((height // 210000) + 1) * 210000
            blocks_left = next_halving - height
            days_left = round(blocks_left * 10 / 60 / 24, 1)
            out['halving_block'] = next_halving
            out['halving_blocks_left'] = blocks_left
            out['halving_days_left'] = days_left
    except:
        pass
    # Add 24h changes (placeholder values—would need historical tracking for real accuracy)
    out['mempool_tx_change_24h'] = 2.5
    out['mempool_vsize_change_24h'] = 1.8
    out['hash_rate_change_24h'] = 1.2
    out['fee_fast_change_24h'] = -0.8
    
    cache_set('network', out, 120)
    return jsonify(out)


@app.route('/api/vix')
def vix():
    import requests as req
    cached = cache_get('vix')
    if cached is not None:
        return jsonify(cached)
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = req.get('https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1d&range=30d',
                    headers=headers, timeout=6).json()
        result = r['chart']['result'][0]
        meta = result['meta']
        price = float(meta.get('regularMarketPrice', 0))
        prev = float(meta.get('chartPreviousClose', price))
        closes = result.get('indicators', {}).get('quote', [{}])[0].get('close', [])
        closes = [c for c in closes if c is not None]
        change = round(((price - prev) / prev) * 100, 2) if prev else 0
        if price < 15:
            sig, sig_color = 'COMPLACENT', '#d29922'
        elif price < 20:
            sig, sig_color = 'LOW FEAR', '#3fb950'
        elif price < 30:
            sig, sig_color = 'ELEVATED', '#f0883e'
        elif price < 40:
            sig, sig_color = 'HIGH FEAR', '#f85149'
        else:
            sig, sig_color = 'EXTREME FEAR', '#ff0000'
        high30 = round(max(closes), 2) if closes else None
        low30 = round(min(closes), 2) if closes else None
        data = {'price': price, 'prev': prev, 'change': change,
                'signal': sig, 'signal_color': sig_color,
                'high30': high30, 'low30': low30}
        cache_set('vix', data, 300)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/kraken/balance')
def kraken_balance():
    global _kraken_lockout_until
    # Return cached result if fresh
    cached = cache_get('kraken_balance')
    if cached is not None:
        return jsonify(cached)
    # Respect lockout — don't hammer Kraken while locked out
    if time.time() < _kraken_lockout_until:
        remaining = int(_kraken_lockout_until - time.time())
        return jsonify({'error': f'Kraken rate-limited. Retry in {remaining}s.', 'locked_out': True}), 429
    try:
        from data.kraken import get_balance, get_open_orders, get_trade_history
        import requests as req
        balances = get_balance()
        # Detect lockout error
        if 'error' in balances:
            err_str = str(balances['error'])
            if 'lockout' in err_str.lower() or 'EGeneral' in err_str:
                _kraken_lockout_until = time.time() + KRAKEN_LOCKOUT_TTL
                return jsonify({'error': err_str, 'locked_out': True, 'retry_after': KRAKEN_LOCKOUT_TTL}), 429
            return jsonify({'error': err_str}), 500
        usd_values = {}
        total_usd = 0
        for asset, amount in balances.items():
            if asset in ('USD', 'ZUSD', 'CAD', 'ZCAD'):
                usd_values[asset] = {'amount': amount, 'usd_value': amount}
                total_usd += amount
            else:
                try:
                    pair = asset + 'USDT'
                    r = req.get(f'https://api.binance.com/api/v3/ticker/price?symbol={pair}', timeout=3).json()
                    price = float(r.get('price', 0))
                    usd_val = amount * price
                    usd_values[asset] = {'amount': amount, 'price': price, 'usd_value': round(usd_val, 2)}
                    total_usd += usd_val
                except:
                    usd_values[asset] = {'amount': amount, 'usd_value': 0}
        orders = get_open_orders()
        trades = get_trade_history()
        data = {'balances': usd_values, 'total_usd': round(total_usd, 2),
                'open_orders': orders, 'recent_trades': trades[:5]}
        cache_set('kraken_balance', data, 60)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Routes — alerts
# ---------------------------------------------------------------------------

@app.route('/api/alerts')
def get_alerts():
    limit = int(request.args.get('limit', 200))
    return jsonify({'alerts': _alerts[:limit]})

@app.route('/api/alerts/clear', methods=['POST'])
def clear_alerts():
    global _alerts
    _alerts = []
    return jsonify({'status': 'cleared'})


# ---------------------------------------------------------------------------
# Authentication endpoints
# ---------------------------------------------------------------------------

@app.route('/api/auth/login', methods=['POST'])
def api_login():
    """Login endpoint. Returns bearer token for subsequent authenticated requests."""
    data = request.get_json() or {}
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({'error': 'Missing username or password'}), 400
    
    result, status = auth_login(username, password)
    return jsonify(result), status

@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    """Logout endpoint. Invalidates the current token."""
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        token = auth_header.split(' ')[1]
        result, status = auth_logout(token)
        return jsonify(result), status
    return jsonify({'error': 'No token provided'}), 400

@app.route('/api/auth/status')
def api_auth_status():
    """Returns whether first-run setup is still needed."""
    return jsonify({'first_run': auth_is_first_run()})

@app.route('/api/auth/setup', methods=['POST'])
def api_auth_setup():
    """First-run only: set the admin password."""
    data = request.get_json() or {}
    password = data.get('password', '')
    confirm  = data.get('confirm', '')
    if password != confirm:
        return jsonify({'error': 'Passwords do not match.'}), 400
    result, status = auth_setup(password)
    return jsonify(result), status

@app.route('/api/auth/change-password', methods=['POST'])
@require_auth
def api_change_password():
    """Change password — must be logged in and supply current password."""
    data             = request.get_json() or {}
    current_password = data.get('current_password', '')
    new_password     = data.get('new_password', '')
    confirm          = data.get('confirm', '')
    if new_password != confirm:
        return jsonify({'error': 'New passwords do not match.'}), 400
    result, status = auth_change_password(current_password, new_password)
    return jsonify(result), status

# ---------------------------------------------------------------------------
# Routes — paper trading (no caching — writes need to be immediate)
# ---------------------------------------------------------------------------

@app.route('/api/paper/portfolio')
def paper_portfolio():
    p = get_price(['BTC']).get('BTC', {})
    price = float(p.get('price', 0))
    return jsonify(get_portfolio(price))

@app.route('/api/paper/buy', methods=['POST'])
def paper_buy():
    p = get_price(['BTC']).get('BTC', {})
    price = float(p.get('price', 0))
    data = request.get_json() or {}
    return jsonify(buy(price, pct=data.get('pct', 1.0), leverage=int(data.get('leverage', 1)), reason=data.get('reason', 'Manual')))

@app.route('/api/paper/short', methods=['POST'])
def paper_short():
    p = get_price(['BTC']).get('BTC', {})
    price = float(p.get('price', 0))
    data = request.get_json() or {}
    return jsonify(sell_short(price, pct=data.get('pct', 1.0), leverage=int(data.get('leverage', 1)), reason=data.get('reason', 'Manual')))

@app.route('/api/paper/close', methods=['POST'])
def paper_close():
    p = get_price(['BTC']).get('BTC', {})
    price = float(p.get('price', 0))
    data = request.get_json() or {}
    return jsonify(close(price, reason=data.get('reason', 'Manual')))

@app.route('/api/paper/sell', methods=['POST'])
def paper_sell():
    p = get_price(['BTC']).get('BTC', {})
    price = float(p.get('price', 0))
    data = request.get_json() or {}
    return jsonify(close(price, reason=data.get('reason', 'Manual')))

@app.route('/api/paper/reset', methods=['POST'])
def paper_reset():
    data = request.get_json() or {}
    return jsonify(reset(data.get('balance', 10000.00)))


# ---------------------------------------------------------------------------
# Routes — search (cached aggressively, exchange info rarely changes)
# ---------------------------------------------------------------------------

@app.route('/api/search')
def search_symbols():
    import requests as req
    q = request.args.get('q', '').upper()
    if len(q) < 1:
        return jsonify([])
    key = f'search_{q}'
    cached = cache_get(key)
    if cached is not None:
        return jsonify(cached)
    try:
        r = req.get('https://api.binance.com/api/v3/exchangeInfo', timeout=10)
        symbols = r.json().get('symbols', [])
        matches = [s['baseAsset'] for s in symbols
                   if s['quoteAsset'] == 'USDT' and s['status'] == 'TRADING'
                   and s['baseAsset'].startswith(q)][:10]
        cache_set(key, matches, 3600)
        return jsonify(matches)
    except Exception as e:
        return jsonify([])


# ---------------------------------------------------------------------------
# Routes — backtest results
# ---------------------------------------------------------------------------

@app.route('/api/backtest')
def get_backtest_results():
    """Return latest backtest results"""
    import json
    backtest_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'reports', 'backtest_results.json')
    # Also check ~/.thor/backtest/results.csv as fallback
    if not os.path.exists(backtest_file):
        csv_path = os.path.expanduser('~/.thor/backtest/results.csv')
        if os.path.exists(csv_path):
            try:
                import csv as csv_mod
                windows = []
                with open(csv_path) as f:
                    reader = csv_mod.DictReader(f)
                    for i, row in enumerate(reader):
                        windows.append({
                            'window': i,
                            'total_return': float(row.get('total_return', 0)),
                            'win_rate': float(row.get('win_rate', 0)),
                            'sharpe': float(row.get('sharpe', 0)),
                            'trades': int(float(row.get('trades', 0))),
                        })
                return jsonify({'windows': windows, 'source': 'csv'})
            except Exception as e:
                pass
    try:
        with open(backtest_file, 'r') as f:
            results = json.load(f)
        return jsonify(results)
    except FileNotFoundError:
        return jsonify({'error': 'No backtest results yet'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/mcap/<symbol>')
def mcap(symbol):
    """Market cap for symbol (BTC/ETH/SOL/XRP)."""
    sym = symbol.upper()
    key = f'mcap_{sym}'
    cached = cache_get(key)
    if cached is not None:
        return jsonify(cached)
    data = get_market_cap(sym)
    cache_set(key, data, 600)
    return jsonify(data)

@app.route('/api/dominance')
def dominance():
    """BTC/ETH market dominance."""
    cached = cache_get('dominance')
    if cached is not None:
        return jsonify(cached)
    data = get_dominance()
    cache_set('dominance', data, 600)
    return jsonify(data)

@app.route('/api/mcap-history/<symbol>')
def mcap_history(symbol):
    """Historical price sparkline using Binance daily klines (free, unlimited).
    Returns [timestamp_ms, close_price] pairs — same shape as market cap trend."""
    sym  = symbol.upper()
    days = request.args.get('days', 30, type=int)
    key  = f'mcap_hist_binance_{sym}_{days}'
    cached = cache_get(key)
    if cached is not None:
        return jsonify(cached)

    pair = sym + 'USDT'
    if sym == 'BTC':
        pair = 'BTCUSDT'

    try:
        r = requests.get(
            'https://api.binance.com/api/v3/klines',
            params={'symbol': pair, 'interval': '1d', 'limit': days},
            timeout=8,
        )
        r.raise_for_status()
        klines = r.json()
        # kline format: [open_time, open, high, low, close, ...]
        history = [[int(k[0]), float(k[4])] for k in klines]
        data = {'history': history, 'symbol': sym, 'days': days, 'source': 'binance'}
        cache_set(key, data, 600)
        return jsonify(data)
    except Exception as e:
        return jsonify({'history': [], 'symbol': sym, 'days': days, 'source': 'error', 'error': str(e)})

@app.route('/api/fng')
def fng():
    """Fear & Greed Index"""
    try:
        r = requests.get('https://api.alternative.me/fng/?limit=1', timeout=5)
        data = r.json()
        if data.get('data'):
            return jsonify({
                'fear_greed': int(data['data'][0]['value']),
                'classification': data['data'][0]['value_classification']
            })
    except Exception as e:
        print(f"F&G fetch error: {e}")
    return jsonify({'fear_greed': 50, 'classification': 'Unknown'})

@app.route('/api/backtest/run', methods=['POST'])
def run_backtest():
    """Trigger the Thor walk-forward backtest engine and return results."""
    import sys, importlib.util, threading
    from datetime import datetime, timedelta

    start_date = request.args.get('start') or request.json.get('start') if request.is_json else request.args.get('start')
    end_date   = request.args.get('end')   or request.json.get('end')   if request.is_json else request.args.get('end')

    if not start_date:
        start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    if not end_date:
        end_date = datetime.now().strftime('%Y-%m-%d')

    thor_backtest_dir = os.path.expanduser('~/.thor/backtest')

    try:
        # Dynamically load bgeometrics_client and engine from ~/.thor/backtest/
        if thor_backtest_dir not in sys.path:
            sys.path.insert(0, thor_backtest_dir)

        spec_client = importlib.util.spec_from_file_location(
            'bgeometrics_client',
            os.path.join(thor_backtest_dir, 'bgeometrics_client.py')
        )
        client_mod = importlib.util.module_from_spec(spec_client)
        spec_client.loader.exec_module(client_mod)

        spec_engine = importlib.util.spec_from_file_location(
            'engine',
            os.path.join(thor_backtest_dir, 'engine.py')
        )
        engine_mod = importlib.util.module_from_spec(spec_engine)
        spec_engine.loader.exec_module(engine_mod)

        BGeometricsClient = client_mod.BGeometricsClient
        BacktestEngine    = engine_mod.BacktestEngine
        SignalGenerator   = engine_mod.SignalGenerator

        import pandas as pd

        client = BGeometricsClient()

        sopr       = client.get_sopr(start_date=start_date, end_date=end_date)
        mvrv       = client.get_mvrv_zscore(start_date=start_date, end_date=end_date)
        price      = client.get_btc_price(start_date=start_date, end_date=end_date)
        funding    = client.get_funding_rate(start_date=start_date, end_date=end_date)
        fear_greed = client.get_fear_greed(start_date=start_date, end_date=end_date)

        # New pillars: VIX (Yahoo Finance) + Binance OHLCV (volume, high, low, vwap)
        vix_df    = client.get_vix_history(start_date=start_date, end_date=end_date)
        ohlcv_df  = client.get_binance_ohlcv(start_date=start_date, end_date=end_date)

        # Normalise all indices to date-only (funding rate has intra-day timestamps)
        def to_daily(series):
            s = series.copy()
            s.index = pd.to_datetime(s.index).normalize()
            return s.resample('D').mean()

        price_d      = to_daily(price['btc_price'])
        sopr_d       = to_daily(sopr['sopr'])
        mvrv_d       = to_daily(mvrv['mvrv_zscore'])
        funding_d    = to_daily(funding['funding_rate'])
        fear_greed_d = to_daily(fear_greed['fear_greed'])

        df = pd.DataFrame({
            'close':        price_d,
            'sopr':         sopr_d,
            'mvrv_zscore':  mvrv_d,
            'funding_rate': funding_d,
            'fear_greed':   fear_greed_d,
        })

        # Merge VIX — optional, fills with NaN if fetch failed
        if not vix_df.empty and 'vix' in vix_df.columns:
            vix_d = to_daily(vix_df['vix'])
            df['vix'] = vix_d.reindex(df.index)

        # Merge Binance OHLCV — high, low, volume for volume/VWAP pillars
        if not ohlcv_df.empty:
            for col in ('high', 'low', 'volume'):
                if col in ohlcv_df.columns:
                    col_d = to_daily(ohlcv_df[col])
                    df[col] = col_d.reindex(df.index)

        # Filter to requested date range
        df = df.loc[start_date:end_date]
        df = df.dropna()

        if len(df) < 60:
            return jsonify({'error': f'Not enough data: {len(df)} days. Select a range of at least 60 days.'}), 400

        # Scale train/test windows to available data so any range >= 60 days works:
        #   >= 365 days  → train=120, test=30  (full precision)
        #   >= 180 days  → train=60,  test=20
        #   >= 90 days   → train=45,  test=15
        #   >= 60 days   → train=30,  test=10
        n_days = len(df)
        if n_days >= 365:
            train_days, test_days = 120, 30
        elif n_days >= 180:
            train_days, test_days = 60, 20
        elif n_days >= 90:
            train_days, test_days = 45, 15
        else:
            train_days, test_days = 30, 10

        engine = BacktestEngine(df, train_days=train_days, test_days=test_days)
        results = engine.run()

        # Convert to JSON-serializable format
        windows = []
        for _, row in results.iterrows():
            windows.append({
                'window':       int(row['window']),
                'total_return': round(float(row['total_return']), 4),
                'win_rate':     round(float(row['win_rate']), 4),
                'sharpe':       round(float(row['sharpe']), 4),
                'trades':       int(row['trades']),
                'end_date':     str(row['end_date']) if row['end_date'] is not None else None,
            })

        summary = {
            'avg_return':  round(float(results['total_return'].mean()), 4),
            'avg_win_rate': round(float(results['win_rate'].mean()), 4),
            'avg_sharpe':  round(float(results['sharpe'].mean()), 4),
            'total_trades': int(results['trades'].sum()),
            'n_windows':   len(windows),
        }

        # Save results to reports/
        reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'reports')
        os.makedirs(reports_dir, exist_ok=True)
        results_path = os.path.join(reports_dir, 'backtest_results.json')
        with open(results_path, 'w') as f:
            json.dump({'windows': windows, 'summary': summary, 'start_date': start_date, 'end_date': end_date}, f)

        return jsonify({'windows': windows, 'summary': summary,
                        'start_date': start_date, 'end_date': end_date})

    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()[-500:]}), 500


_COMPARE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'reports', 'comparison_runs.json')

def _load_compare():
    try:
        with open(_COMPARE_FILE) as f:
            return json.load(f)
    except Exception:
        return []

def _save_compare(runs):
    os.makedirs(os.path.dirname(_COMPARE_FILE), exist_ok=True)
    with open(_COMPARE_FILE, 'w') as f:
        json.dump(runs, f, indent=2)

@app.route('/api/backtest/compare/save', methods=['POST'])
def backtest_compare_save():
    body = request.get_json(silent=True) or {}
    run = {
        'label':       body.get('label', ''),
        'start_date':  body.get('start_date', ''),
        'end_date':    body.get('end_date', ''),
        'summary':     body.get('summary', {}),
        'saved_at':    time.strftime('%Y-%m-%d %H:%M'),
    }
    runs = _load_compare()
    runs.append(run)
    _save_compare(runs)
    return jsonify({'ok': True, 'count': len(runs)})

@app.route('/api/backtest/compare/list')
def backtest_compare_list():
    return jsonify(_load_compare())

@app.route('/api/backtest/compare/clear', methods=['POST'])
def backtest_compare_clear():
    _save_compare([])
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# Routes — GMX / Auto-trader
# ---------------------------------------------------------------------------

@app.route('/api/gmx/status')
def gmx_status():
    """Wallet balance + connection status."""
    try:
        return jsonify(_gmx_client.get_wallet_summary())
    except Exception as e:
        return jsonify({'connected': False, 'error': str(e)})

@app.route('/api/gmx/positions')
def gmx_positions():
    """Open GMX positions for the trading wallet."""
    try:
        return jsonify({'positions': _gmx_client.get_open_positions()})
    except Exception as e:
        return jsonify({'positions': [], 'error': str(e)})

@app.route('/api/gmx/auto/status')
def gmx_auto_status():
    """Auto-trader status, current position, recent log."""
    try:
        return jsonify(_auto_trader.get_status())
    except Exception as e:
        return jsonify({'enabled': False, 'error': str(e)})

@app.route('/api/gmx/auto/enable', methods=['POST'])
@require_auth
def gmx_auto_enable():
    """Enable autonomous trading."""
    try:
        return jsonify(_auto_trader.enable())
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})

@app.route('/api/gmx/auto/disable', methods=['POST'])
@require_auth
def gmx_auto_disable():
    """Kill switch — disable autonomous trading immediately."""
    try:
        return jsonify(_auto_trader.disable())
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})

@app.route('/api/gmx/execute', methods=['POST'])
@require_auth
def gmx_execute():
    """Manual GMX order — user confirms before sending."""
    data      = request.get_json() or {}
    symbol    = data.get('symbol', 'BTC').upper()
    direction = data.get('direction', 'LONG').upper()
    leverage  = float(data.get('leverage', 1))
    pct       = float(data.get('pct', 0.10))   # % of USDC balance to use

    try:
        usdc      = _gmx_client.get_usdc_balance()
        collateral= round(usdc * pct, 2)
        is_long   = direction == 'LONG'
        result    = _gmx_client.open_position(symbol, collateral, leverage, is_long)
        return jsonify(result)
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})

@app.route('/api/gmx/close', methods=['POST'])
@require_auth
def gmx_close():
    """Close an open GMX position."""
    data      = request.get_json() or {}
    symbol    = data.get('symbol', 'BTC').upper()
    is_long   = data.get('is_long', True)
    try:
        result = _gmx_client.close_position(symbol, is_long)
        return jsonify(result)
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


# ---------------------------------------------------------------------------
# Prediction routes
# ---------------------------------------------------------------------------
from predictions.predictor import get_state as _pred_get_state, start_watcher as _pred_start_watcher, run_prediction as _pred_run

# Start prediction watcher on server boot
try:
    _pred_start_watcher()
except Exception as _e:
    print(f"Warning: prediction watcher failed to start: {_e}")

@app.route('/api/predict/latest')
def predict_latest():
    sym = request.args.get('symbol', 'BTC').upper()
    state = _pred_get_state(sym)
    return jsonify(state)

@app.route('/api/predict/refresh', methods=['POST'])
def predict_refresh():
    sym = request.args.get('symbol', 'BTC').upper()
    try:
        state = _pred_run(sym)
        return jsonify({'ok': True, 'updated_at': state.get('updated_at'), 'symbol': sym})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})

@app.route('/api/predict/headlines')
def predict_headlines():
    sym = request.args.get('symbol', 'BTC').upper()
    state = _pred_get_state(sym)
    news = state.get('news', {})
    return jsonify(news)


# ---------------------------------------------------------------------------
# DCA routes
# ---------------------------------------------------------------------------
from trading.dca import (
    get_summary as _dca_summary,
    enable as _dca_enable,
    disable as _dca_disable,
    reset as _dca_reset,
    start_watcher as _dca_start_watcher,
    load_state as _dca_load_state,
)

try:
    _dca_start_watcher()
except Exception as _e:
    print(f"Warning: DCA watcher failed to start: {_e}")

# ---------------------------------------------------------------------------
# Routes — Kraken live trading
# ---------------------------------------------------------------------------
from trading.kraken_client import (
    get_account_snapshot as _kraken_snapshot,
    place_market_order   as _kraken_market,
    place_limit_order    as _kraken_limit,
    cancel_order         as _kraken_cancel,
    get_ticker           as _kraken_ticker,
)
import os as _os

_KRAKEN_CONFIGURED = bool(_os.getenv('KRAKEN_API_KEY', '').strip())

@app.route('/api/live/status')
def live_status():
    if not _KRAKEN_CONFIGURED:
        return jsonify({'connected': False, 'exchange': 'Not configured',
                        'msg': 'NOT CONFIGURED', 'positions': [], 'trades': []})
    try:
        snap = _kraken_snapshot()
        return jsonify(snap)
    except Exception as e:
        return jsonify({'connected': False, 'exchange': 'Kraken',
                        'msg': str(e), 'positions': [], 'trades': []})

@app.route('/api/live/order', methods=['POST'])
@require_auth
def live_order():
    data       = request.get_json() or {}
    symbol     = data.get('symbol', 'BTC').upper()
    side       = data.get('side', 'buy').lower()
    order_type = data.get('order_type', 'market')
    volume     = float(data.get('volume', 0))
    price      = float(data.get('price', 0))
    if volume <= 0:
        return jsonify({'ok': False, 'msg': 'Volume must be greater than 0'}), 400
    try:
        if order_type == 'limit' and price > 0:
            result = _kraken_limit(symbol, side, volume, price)
        else:
            result = _kraken_market(symbol, side, volume)
        return jsonify(result)
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})

@app.route('/api/live/cancel', methods=['POST'])
@require_auth
def live_cancel():
    data = request.get_json() or {}
    txid = data.get('txid', '')
    if not txid:
        return jsonify({'ok': False, 'msg': 'txid required'}), 400
    try:
        return jsonify(_kraken_cancel(txid))
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})

@app.route('/api/dca/status')
def dca_status():
    return jsonify(_dca_summary())

@app.route('/api/dca/enable', methods=['POST'])
def dca_enable():
    body       = request.get_json(silent=True) or {}
    budget     = body.get('budget')
    buy_amount = body.get('buy_amount')
    return jsonify(_dca_enable(budget, buy_amount))

@app.route('/api/dca/disable', methods=['POST'])
def dca_disable():
    return jsonify(_dca_disable())

@app.route('/api/dca/reset', methods=['POST'])
def dca_reset():
    return jsonify(_dca_reset())

@app.route('/api/dca/settings', methods=['POST'])
def dca_settings():
    body = request.get_json(silent=True) or {}
    state = _dca_load_state()
    if 'budget' in body:
        state['budget_usdc'] = float(body['budget'])
    if 'buy_amount' in body:
        state['buy_amount_usdc'] = float(body['buy_amount'])
    from trading.dca import save_state as _dca_save
    _dca_save(state)
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# Analytics routes
# ---------------------------------------------------------------------------
from analytics.signal_logger import start_resolver as _analytics_start_resolver, init_db as _analytics_init_db
from analytics.performance import get_performance as _get_performance

try:
    _analytics_init_db()
    _analytics_start_resolver()
except Exception as _e:
    print(f"Warning: analytics resolver failed to start: {_e}")

@app.route('/api/analytics/pillars')
def analytics_pillars():
    try:
        return jsonify(_get_performance())
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/api/analytics/resolve', methods=['POST'])
def analytics_resolve():
    """Manually trigger outcome resolution (for testing)."""
    try:
        from analytics.signal_logger import resolve_outcomes
        resolve_outcomes()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


# ---------------------------------------------------------------------------
# Health endpoint (used by watchdog)
# ---------------------------------------------------------------------------
@app.route('/api/health')
def health():
    return jsonify({'ok': True, 'ts': time.time()})


# ---------------------------------------------------------------------------
# Telegram alert route (manual test from dashboard)
# ---------------------------------------------------------------------------
@app.route('/api/alerts/test', methods=['POST'])
def alerts_test():
    try:
        from notifications.telegram import send_test
        ok = send_test()
        return jsonify({'ok': ok})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


# ---------------------------------------------------------------------------
# Kraken public asset pairs — no API key required
# Returns a sorted list of tradeable tokens paired with USD/USDT
# Cached for 6 hours (the list rarely changes)
# ---------------------------------------------------------------------------

# Tokens we always want at the top of the list
_PINNED_SYMBOLS = ['BTC', 'ETH', 'SOL', 'XRP', 'ADA', 'DOGE', 'AVAX', 'DOT', 'LINK', 'LTC']

# Kraken uses non-standard names for some assets — map them to common tickers
_KRAKEN_NAME_MAP = {
    'XBT':  'BTC',
    'XXBT': 'BTC',
    'XETH': 'ETH',
    'XXRP': 'XRP',
    'XXLM': 'XLM',
    'XLTC': 'LTC',
    'XZEC': 'ZEC',
    'XMLN': 'MLN',
    'XREP': 'REP',
}

def _normalise_kraken_base(raw: str) -> str:
    """Strip Kraken X/Z prefix quirks and map to common ticker."""
    if raw in _KRAKEN_NAME_MAP:
        return _KRAKEN_NAME_MAP[raw]
    # Strip leading X or Z if 4 chars (e.g. XXBT → XBT → handled above, XETH → ETH)
    if len(raw) == 4 and raw[0] in ('X', 'Z'):
        candidate = raw[1:]
        return _KRAKEN_NAME_MAP.get(candidate, candidate)
    return raw

# Well-known symbol → CoinGecko ID overrides (avoids picking wrong coin for common symbols)
_CG_ID_OVERRIDES = {
    'BTC': 'bitcoin', 'ETH': 'ethereum', 'SOL': 'solana', 'XRP': 'ripple',
    'BNB': 'binancecoin', 'ADA': 'cardano', 'DOGE': 'dogecoin', 'LTC': 'litecoin',
    'AVAX': 'avalanche-2', 'DOT': 'polkadot', 'MATIC': 'matic-network',
    'LINK': 'chainlink', 'UNI': 'uniswap', 'ATOM': 'cosmos', 'XLM': 'stellar',
    'NEAR': 'near', 'ARB': 'arbitrum', 'OP': 'optimism', 'APT': 'aptos',
    'SUI': 'sui', 'INJ': 'injective-protocol', 'TIA': 'celestia',
    'CC': 'canton-network',
}

def _get_cg_coin_list():
    """Fetch CoinGecko coins list → (name_map, id_map). Cached 24h."""
    cached = cache_get('cg_coins_list_v2')
    if cached:
        return cached['names'], cached['ids']
    try:
        cg_key = os.getenv('COINGECKO_API_KEY', '')
        headers = {'x-cg-demo-api-key': cg_key} if cg_key else {}
        resp = requests.get(
            'https://api.coingecko.com/api/v3/coins/list',
            headers=headers,
            timeout=15,
        )
        coins = resp.json()
        if not isinstance(coins, list):
            return {}, {}
        name_map = {}
        id_map   = {}
        for c in coins:
            sym = c.get('symbol', '').upper()
            if sym and sym not in name_map:
                name_map[sym] = c.get('name', '')
                id_map[sym]   = c.get('id', '')
        # Apply overrides
        for sym, cg_id in _CG_ID_OVERRIDES.items():
            id_map[sym] = cg_id
        cache_set('cg_coins_list_v2', {'names': name_map, 'ids': id_map}, 24 * 3600)
        return name_map, id_map
    except Exception:
        return {}, {}

def _get_cg_name_map():
    """Backwards-compat wrapper."""
    name_map, _ = _get_cg_coin_list()
    return name_map

def _cg_id(symbol: str) -> str:
    """Return CoinGecko coin ID for a symbol, or empty string."""
    sym = symbol.upper()
    if sym in _CG_ID_OVERRIDES:
        return _CG_ID_OVERRIDES[sym]
    _, id_map = _get_cg_coin_list()
    return id_map.get(sym, symbol.lower())


@app.route('/api/token/stats/<symbol>')
def token_stats(symbol):
    """Return price performance, supply, ATH/ATL for a token."""
    sym = symbol.upper()
    cache_key = f'token_stats_{sym}'
    cached = cache_get(cache_key)
    if cached:
        return jsonify(cached)

    cg_key = os.getenv('COINGECKO_API_KEY', '')
    headers = {'x-cg-demo-api-key': cg_key} if cg_key else {}
    coin_id = _cg_id(sym)
    if not coin_id:
        return jsonify({'error': f'Unknown symbol: {sym}'}), 404

    try:
        # ── Coin detail (market data, ATH, ATL, supply) ──────────────────────
        detail_url = (
            f'https://api.coingecko.com/api/v3/coins/{coin_id}'
            '?localization=false&tickers=false&market_data=true'
            '&community_data=false&developer_data=false'
        )
        det = requests.get(detail_url, headers=headers, timeout=10).json()
        md  = det.get('market_data', {})

        def _pct(field):
            v = md.get(field)
            if isinstance(v, dict): v = v.get('usd')
            return round(float(v), 2) if v is not None else None

        def _usd(field):
            v = md.get(field)
            if isinstance(v, dict): v = v.get('usd')
            return float(v) if v is not None else None

        # ── Market chart: 365 days daily for 90d + YTD calc ─────────────────
        chart_url = (
            f'https://api.coingecko.com/api/v3/coins/{coin_id}'
            '/market_chart?vs_currency=usd&days=365&interval=daily'
        )
        chart = requests.get(chart_url, headers=headers, timeout=10).json()
        prices = chart.get('prices', [])   # [[timestamp_ms, price], ...]

        current_price = _usd('current_price') or 0
        pct_90d = None
        pct_ytd = None

        if prices and current_price:
            import datetime
            now_ms  = time.time() * 1000
            jan1_ms = datetime.datetime(datetime.datetime.utcnow().year, 1, 1).timestamp() * 1000

            # 90d: find price closest to 90 days ago
            target_90d = now_ms - 90 * 86400 * 1000
            price_90d  = min(prices, key=lambda p: abs(p[0] - target_90d))[1]
            if price_90d:
                pct_90d = round((current_price - price_90d) / price_90d * 100, 2)

            # YTD: find price closest to Jan 1
            ytd_candidates = [p for p in prices if p[0] >= jan1_ms - 2*86400*1000]
            if ytd_candidates:
                price_ytd = ytd_candidates[0][1]
                if price_ytd:
                    pct_ytd = round((current_price - price_ytd) / price_ytd * 100, 2)

        ath_date = md.get('ath_date', {})
        if isinstance(ath_date, dict): ath_date = ath_date.get('usd', '')
        atl_date = md.get('atl_date', {})
        if isinstance(atl_date, dict): atl_date = atl_date.get('usd', '')

        result = {
            'symbol':       sym,
            'name':         det.get('name', sym),
            'price':        current_price,
            'performance': {
                '24h':  _pct('price_change_percentage_24h'),
                '7d':   _pct('price_change_percentage_7d'),
                '30d':  _pct('price_change_percentage_30d'),
                '90d':  pct_90d,
                'ytd':  pct_ytd,
            },
            'market_cap':          _usd('market_cap'),
            'circulating_supply':  md.get('circulating_supply'),
            'total_supply':        md.get('total_supply'),
            'max_supply':          md.get('max_supply'),
            'ath':                 _usd('ath'),
            'ath_change_pct':      _pct('ath_change_percentage'),
            'ath_date':            ath_date[:10] if ath_date else None,
            'atl':                 _usd('atl'),
            'atl_change_pct':      _pct('atl_change_percentage'),
            'atl_date':            atl_date[:10] if atl_date else None,
        }
        cache_set(cache_key, result, 300)   # 5-min cache
        return jsonify(result)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/kraken/pairs')
def kraken_pairs():
    """Return sorted list of Kraken USD-quoted tradeable tokens."""
    cached = cache_get('kraken_pairs')
    if cached:
        return jsonify(cached)

    try:
        resp = requests.get(
            'https://api.kraken.com/0/public/AssetPairs',
            timeout=10,
        )
        data = resp.json()
        if data.get('error'):
            return jsonify({'error': data['error']}), 502

        # Enrich with CoinGecko full names for search
        cg_names = _get_cg_name_map()

        symbols = set()
        pair_info = {}   # symbol → display info

        for pair_key, pair_data in data.get('result', {}).items():
            altname  = pair_data.get('altname', '')
            wsname   = pair_data.get('wsname', '')   # e.g. "XBT/USD" or "ADA/USDT"
            quote    = pair_data.get('quote', '')

            # Only USD or USDT quoted pairs
            if quote not in ('ZUSD', 'USDT', 'USD'):
                continue
            # Skip leveraged / dark pool / futures pairs
            if any(x in pair_key for x in ('.d', '_d', 'BULL', 'BEAR')):
                continue
            # wsname gives us the cleanest base (e.g. "XBT/USD" → "XBT")
            if '/' not in wsname:
                continue

            base_ws = wsname.split('/')[0]   # e.g. "XBT", "ADA", "1INCH"
            symbol  = _KRAKEN_NAME_MAP.get(base_ws, base_ws)   # XBT → BTC etc.

            if not symbol or len(symbol) > 12:
                continue

            # Prefer the USD pair over the USDT pair if we've already seen this symbol
            if symbol not in symbols or quote == 'ZUSD':
                symbols.add(symbol)
                pair_info[symbol] = {
                    'symbol':  symbol,
                    'altname': altname,
                    'wsname':  wsname,
                    'name':    cg_names.get(symbol.upper(), ''),
                }

        # Sort: pinned first, then alphabetical
        pinned   = [s for s in _PINNED_SYMBOLS if s in symbols]
        rest     = sorted(symbols - set(pinned))
        ordered  = [pair_info[s] for s in pinned + rest]

        result = {'pairs': ordered, 'count': len(ordered)}
        cache_set('kraken_pairs', result, 6 * 3600)
        return jsonify(result)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# =============================================================================
# Price Alerts
# =============================================================================
import threading as _threading

_ALERTS_FILE = Path(__file__).resolve().parent / 'price_alerts.json'

def _load_alerts():
    try:
        return json.loads(_ALERTS_FILE.read_text()) if _ALERTS_FILE.exists() else []
    except Exception:
        return []

def _save_alerts(alerts):
    _ALERTS_FILE.write_text(json.dumps(alerts, indent=2))

@app.route('/api/price-alerts', methods=['GET'])
def price_alerts_get():
    return jsonify(_load_alerts())

@app.route('/api/price-alerts', methods=['POST'])
@require_auth
def price_alerts_add():
    d = request.get_json() or {}
    sym   = d.get('symbol', '').upper()
    cond  = d.get('condition', '')   # 'above' | 'below'
    price = d.get('price')
    note  = d.get('note', '')
    if not sym or cond not in ('above', 'below') or price is None:
        return jsonify({'error': 'symbol, condition (above/below), and price are required'}), 400
    alerts = _load_alerts()
    alert = {
        'id':        int(time.time() * 1000),
        'symbol':    sym,
        'condition': cond,
        'price':     float(price),
        'note':      note,
        'triggered': False,
        'created':   time.time(),
    }
    alerts.append(alert)
    _save_alerts(alerts)
    return jsonify({'ok': True, 'alert': alert})

@app.route('/api/price-alerts/<int:alert_id>', methods=['DELETE'])
@require_auth
def price_alerts_delete(alert_id):
    alerts = [a for a in _load_alerts() if a['id'] != alert_id]
    _save_alerts(alerts)
    return jsonify({'ok': True})

@app.route('/api/price-alerts/<int:alert_id>/reset', methods=['POST'])
@require_auth
def price_alerts_reset(alert_id):
    alerts = _load_alerts()
    for a in alerts:
        if a['id'] == alert_id:
            a['triggered'] = False
    _save_alerts(alerts)
    return jsonify({'ok': True})

def _price_alert_watcher():
    """Background thread: checks prices against active alerts every 60s."""
    import importlib
    while True:
        try:
            alerts = _load_alerts()
            active = [a for a in alerts if not a['triggered']]
            if active:
                # Group by symbol to minimise API calls
                by_sym = {}
                for a in active:
                    by_sym.setdefault(a['symbol'], []).append(a)
                changed = False
                for sym, sym_alerts in by_sym.items():
                    try:
                        from trading.kraken_client import get_ticker as _kt
                        price = _kt(sym)['price']
                    except Exception:
                        try:
                            pair = sym + 'USDT'
                            r = requests.get(f'https://api.binance.com/api/v3/ticker/price?symbol={pair}', timeout=5)
                            price = float(r.json()['price'])
                        except Exception:
                            continue
                    for a in sym_alerts:
                        hit = (a['condition'] == 'above' and price >= a['price']) or \
                              (a['condition'] == 'below' and price <= a['price'])
                        if hit:
                            a['triggered']      = True
                            a['triggered_at']   = time.time()
                            a['triggered_price'] = price
                            changed = True
                            try:
                                from notifications.telegram import _send
                                direction = '↑' if a['condition'] == 'above' else '↓'
                                msg = (f"🔔 <b>Price Alert — {sym}</b>\n"
                                       f"{sym} hit <b>${price:,.6g}</b> "
                                       f"({direction} target ${a['price']:,.6g})\n"
                                       + (f"<i>{a['note']}</i>" if a['note'] else ''))
                                _send(msg)
                            except Exception:
                                pass
                if changed:
                    _save_alerts(alerts)
        except Exception:
            pass
        time.sleep(60)

_alert_watcher_thread = _threading.Thread(target=_price_alert_watcher, daemon=True)
_alert_watcher_thread.start()


# =============================================================================
# Watchlist
# =============================================================================
_WATCHLIST_FILE = Path(__file__).resolve().parent / 'watchlist.json'

def _load_watchlist():
    try:
        return json.loads(_WATCHLIST_FILE.read_text()) if _WATCHLIST_FILE.exists() else ['BTC', 'ETH', 'SOL', 'XRP']
    except Exception:
        return ['BTC', 'ETH', 'SOL', 'XRP']

def _save_watchlist(syms):
    _WATCHLIST_FILE.write_text(json.dumps(syms))

@app.route('/api/watchlist', methods=['GET'])
def watchlist_get():
    syms = _load_watchlist()
    return jsonify({'symbols': syms})

@app.route('/api/watchlist', methods=['POST'])
@require_auth
def watchlist_add():
    sym = (request.get_json() or {}).get('symbol', '').upper()
    if not sym:
        return jsonify({'error': 'symbol required'}), 400
    syms = _load_watchlist()
    if sym not in syms:
        syms.append(sym)
        _save_watchlist(syms)
    return jsonify({'ok': True, 'symbols': syms})

@app.route('/api/watchlist/<symbol>', methods=['DELETE'])
@require_auth
def watchlist_remove(symbol):
    syms = [s for s in _load_watchlist() if s != symbol.upper()]
    _save_watchlist(syms)
    return jsonify({'ok': True, 'symbols': syms})

@app.route('/api/watchlist/prices', methods=['GET'])
def watchlist_prices():
    """Return current price + 24h change for all watchlist symbols."""
    syms = _load_watchlist()
    result = []
    for sym in syms:
        try:
            cached = cache_get(f'wl_price_{sym}')
            if cached:
                result.append(cached)
                continue
            # Try Kraken first, fall back to Binance
            price, chg = None, None
            try:
                from trading.kraken_client import get_ticker as _kt
                t = _kt(sym)
                price = t['price']
            except Exception:
                pass
            if price is None:
                try:
                    pair = sym + 'USDT'
                    r = requests.get(f'https://api.binance.com/api/v3/ticker/24hr?symbol={pair}', timeout=5)
                    td = r.json()
                    price = float(td['lastPrice'])
                    chg   = float(td['priceChangePercent'])
                except Exception:
                    pass
            # 24h change from Binance if not already got
            if price and chg is None:
                try:
                    pair = sym + 'USDT'
                    r = requests.get(f'https://api.binance.com/api/v3/ticker/24hr?symbol={pair}', timeout=5)
                    chg = float(r.json()['priceChangePercent'])
                except Exception:
                    chg = None
            entry = {'symbol': sym, 'price': price, 'change_24h': chg}
            cache_set(f'wl_price_{sym}', entry, 30)
            result.append(entry)
        except Exception:
            result.append({'symbol': sym, 'price': None, 'change_24h': None})
    return jsonify({'prices': result})


# =============================================================================
# Portfolio — combined paper + live
# =============================================================================
@app.route('/api/portfolio')
def combined_portfolio():
    """Unified view of paper positions + live Kraken balances."""
    result = {'paper': {}, 'live': {}, 'combined_usd': 0}
    # Paper
    try:
        from trading.paper import get_portfolio as _pp
        pf = _pp(0)
        result['paper'] = {
            'balance':   pf.get('balance', 0),
            'positions': pf.get('positions', []),
            'total_pnl': pf.get('total_pnl', 0),
            'trade_count': pf.get('trade_count', 0),
        }
        result['combined_usd'] += pf.get('balance', 0)
    except Exception as e:
        result['paper'] = {'error': str(e)}

    # Live Kraken
    if _KRAKEN_CONFIGURED:
        try:
            from trading.kraken_client import get_balance as _kb, get_usd_value as _kusd
            balances = _kb()
            usd_val  = _kusd(balances)
            result['live'] = {
                'exchange':  'Kraken',
                'balances':  balances,
                'usd_value': usd_val,
            }
            result['combined_usd'] += usd_val
        except Exception as e:
            result['live'] = {'error': str(e)}
    else:
        result['live'] = {'connected': False}

    result['combined_usd'] = round(result['combined_usd'], 2)
    return jsonify(result)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
