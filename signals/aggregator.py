"""
THOR Elite Signal Aggregator — v3
7-pillar regime-aware scoring with conviction measurement.
Pillars: technical, derivatives, macro, sentiment, vix, volume, btc_dominance
"""
import sys, os, statistics, signal as signal_mod, threading
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from data.cmc import get_price
from data.coingecko import get_price_binance, get_ohlcv, _binance_klines
from data.indicators import get_rsi, get_ma
from data.feargreed import get_fear_greed
from data.derivatives import get_derivatives_summary
from data.macro_cross import get_macro_cross_summary, get_stablecoin_dominance
from data.sentiment import get_sentiment_summary
from data.fred import get_macro
from signals.regime import get_regime
import requests as _req

_HEADERS = {'User-Agent': 'Mozilla/5.0'}

def _get_vix_score():
    """
    VIX pillar: cross-asset fear gauge.
    High VIX = market fear = risk-off = bearish crypto.
    Low VIX = complacency = risk-on environment.
    """
    try:
        r = _req.get(
            'https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1d&range=10d',
            headers=_HEADERS, timeout=6).json()
        meta = r['chart']['result'][0]['meta']
        vix = float(meta.get('regularMarketPrice', 20))
        prev = float(meta.get('chartPreviousClose', vix))
        change = ((vix - prev) / prev) * 100 if prev else 0

        if vix < 15:
            score, signal = 1, 'COMPLACENT'       # very low fear — risk-on
        elif vix < 20:
            score, signal = 1, 'LOW'               # calm market — mild bullish
        elif vix < 25:
            score, signal = 0, 'NEUTRAL'
        elif vix < 30:
            score, signal = -1, 'ELEVATED'         # rising fear — caution
        elif vix < 40:
            score, signal = -1, 'HIGH FEAR'
        else:
            score, signal = -2, 'EXTREME FEAR'     # crash territory

        # Rapidly rising VIX (>15% in a day) = extra bearish signal
        if change > 15:
            score -= 1
            signal += ' (SPIKE)'

        return {'vix': round(vix, 2), 'change': round(change, 2),
                'signal': signal, 'score': score, 'max_score': 2}
    except Exception as e:
        print(f'VIX pillar error: {e}')
        return {'vix': 20, 'signal': 'UNKNOWN', 'score': 0, 'max_score': 2}


def _get_volume_score(symbol):
    """
    Volume confirmation pillar: are price moves backed by volume?
    High volume on up moves = conviction. Low volume = suspect.
    Uses last completed daily candle to avoid partial-day distortion.
    """
    try:
        raw = _binance_klines(symbol, '1d', 32)
        if len(raw) < 10:
            return {'score': 0, 'max_score': 2, 'signal': 'UNKNOWN'}

        # Drop the last (incomplete) candle — use completed candles only
        completed = raw[:-1]
        vols   = [float(d[7]) for d in completed]   # quote volume
        closes = [float(d[4]) for d in completed]

        vol_24h  = vols[-1]                          # last completed day
        avg_30d  = sum(vols[-30:]) / min(30, len(vols))
        ratio    = vol_24h / avg_30d if avg_30d else 1.0

        # Direction of last 3 completed candles
        price_up = closes[-1] > closes[-4] if len(closes) >= 4 else True

        if ratio > 2.0 and price_up:
            score, signal = 2, 'HIGH VOL BREAKOUT'    # strong bullish confirmation
        elif ratio > 1.5 and price_up:
            score, signal = 1, 'ABOVE AVG (BULL)'
        elif ratio > 2.0 and not price_up:
            score, signal = -2, 'HIGH VOL BREAKDOWN'  # strong bearish confirmation
        elif ratio > 1.5 and not price_up:
            score, signal = -1, 'ABOVE AVG (BEAR)'
        elif ratio < 0.5:
            score, signal = 0, 'LOW VOL (WEAK MOVE)'  # either direction — unreliable
        else:
            score, signal = 0, 'NORMAL'

        return {'vol_24h': round(vol_24h), 'avg_30d': round(avg_30d),
                'ratio': round(ratio, 2), 'signal': signal,
                'score': score, 'max_score': 2}
    except Exception as e:
        print(f'Volume pillar error: {e}')
        return {'score': 0, 'max_score': 2, 'signal': 'UNKNOWN'}


def _get_btc_dominance_score(btc_dom_current=0):
    """
    BTC Dominance trend pillar.
    Rising dominance = capital rotating INTO BTC = bullish for BTC.
    Falling dominance = alt season or capital leaving crypto = bearish BTC.
    Uses CoinGecko for market cap data.
    btc_dom_current is passed in from the already-fetched macro stablecoin data.
    """
    try:
        # CoinGecko global: returns btc dominance % directly
        r = _req.get('https://api.coingecko.com/api/v3/global', timeout=8)
        global_data = r.json().get('data', {})
        btc_dom = round(float(global_data.get('market_cap_percentage', {}).get('btc', 0)), 2) or btc_dom_current

        # 7-day BTC price history via CoinGecko market_chart
        btc_hist = _req.get(
            'https://api.coingecko.com/api/v3/coins/bitcoin/market_chart',
            params={'vs_currency': 'usd', 'days': 8, 'interval': 'daily'}, timeout=8).json()
        prices = [float(p[1]) for p in btc_hist.get('prices', [])]

        if len(prices) >= 2:
            btc_change_7d = ((prices[-1] - prices[0]) / prices[0]) * 100
        else:
            btc_change_7d = 0

        # Score based on dominance level and trend direction
        # High dominance (>55%) with rising = very bullish for BTC
        if btc_dom > 55 and btc_change_7d > 5:
            score, signal = 1, f'DOMINANT & RISING ({btc_dom:.1f}%)'
        elif btc_dom > 50:
            score, signal = 1, f'STRONG ({btc_dom:.1f}%)'
        elif btc_dom > 45:
            score, signal = 0, f'NEUTRAL ({btc_dom:.1f}%)'
        else:
            score, signal = -1, f'WEAK — alt season ({btc_dom:.1f}%)'

        return {'btc_dominance': btc_dom, 'btc_change_7d': round(btc_change_7d, 2),
                'signal': signal, 'score': score, 'max_score': 1}
    except Exception as e:
        print(f'BTC dominance pillar error: {e}')
        # Fallback to passed-in dominance value if available
        if btc_dom_current > 0:
            score = 1 if btc_dom_current > 50 else (0 if btc_dom_current > 45 else -1)
            return {'btc_dominance': btc_dom_current, 'btc_change_7d': 0,
                    'signal': f'{"STRONG" if score > 0 else "NEUTRAL" if score == 0 else "WEAK"} ({btc_dom_current:.1f}%)',
                    'score': score, 'max_score': 1}
        return {'btc_dominance': 0, 'dom_change_7d': 0, 'signal': 'UNKNOWN', 'score': 0, 'max_score': 1}

def _get_rsi_divergence_score(symbol):
    """
    RSI Divergence pillar.
    Bullish divergence:  price makes lower low  + RSI makes higher low  → reversal up likely.
    Bearish divergence:  price makes higher high + RSI makes lower high → reversal down likely.
    Hidden bullish:      price makes higher low  + RSI makes lower low  → trend continuation up.
    Hidden bearish:      price makes lower high  + RSI makes higher high→ trend continuation down.
    """
    try:
        raw = _binance_klines(symbol, '1d', 40)
        if len(raw) < 25:
            return {'score': 0, 'max_score': 2, 'signal': 'UNKNOWN'}

        closes = [float(c[4]) for c in raw[:-1]]   # completed candles only

        # Compute RSI(14) from scratch
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i-1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        period = 14
        avg_g = sum(gains[:period]) / period
        avg_l = sum(losses[:period]) / period
        rsi_vals = [50.0] * (period + 1)
        for i in range(period, len(gains)):
            avg_g = (avg_g * (period - 1) + gains[i]) / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period
            rs = avg_g / (avg_l + 1e-9)
            rsi_vals.append(100 - 100 / (1 + rs))
        while len(rsi_vals) < len(closes):
            rsi_vals.append(50.0)

        # Compare two windows:
        #   window A: 12–8 days ago (a recent swing)
        #   window B: last 4 days   (most recent swing)
        n = len(closes)
        p_a_lo = min(closes[n-12:n-6]);  r_a_lo = min(rsi_vals[n-12:n-6])
        p_b_lo = min(closes[n-5:n]);     r_b_lo = min(rsi_vals[n-5:n])
        p_a_hi = max(closes[n-12:n-6]);  r_a_hi = max(rsi_vals[n-12:n-6])
        p_b_hi = max(closes[n-5:n]);     r_b_hi = max(rsi_vals[n-5:n])

        PRICE_TOL = 0.005   # 0.5% — price must be meaningfully different
        RSI_TOL   = 2.0     # RSI must differ by ≥2 points

        score, signal = 0, 'NO DIVERGENCE'
        if p_b_lo < p_a_lo * (1 - PRICE_TOL) and r_b_lo > r_a_lo + RSI_TOL:
            score, signal = 2,  'BULLISH DIVERGENCE'
        elif p_b_hi > p_a_hi * (1 + PRICE_TOL) and r_b_hi < r_a_hi - RSI_TOL:
            score, signal = -2, 'BEARISH DIVERGENCE'
        elif p_b_lo > p_a_lo * (1 + PRICE_TOL) and r_b_lo < r_a_lo - RSI_TOL:
            score, signal = 1,  'HIDDEN BULL (continuation)'
        elif p_b_hi < p_a_hi * (1 - PRICE_TOL) and r_b_hi > r_a_hi + RSI_TOL:
            score, signal = -1, 'HIDDEN BEAR (continuation)'

        return {'score': score, 'max_score': 2, 'signal': signal,
                'rsi_current': round(rsi_vals[-1], 1)}
    except Exception as e:
        print(f'RSI Divergence pillar error: {e}')
        return {'score': 0, 'max_score': 2, 'signal': 'UNKNOWN'}


def _get_ema200_score(symbol):
    """
    200 EMA + Price Action pillar.
    The 200-day EMA is the most-watched institutional trend line.
    Three sub-signals: price vs EMA, EMA slope, MA50 vs EMA200 (golden/death cross context).
    Fetches 220 daily candles so the EMA is properly warmed up.
    """
    try:
        raw = _binance_klines(symbol, '1d', 222)
        if len(raw) < 55:
            return {'score': 0, 'max_score': 3, 'signal': 'UNKNOWN'}

        closes = [float(c[4]) for c in raw[:-1]]

        # Compute EMA via Wilder smoothing (k = 2 / (n+1))
        def ema(prices, span):
            k = 2.0 / (span + 1)
            e = [prices[0]]
            for p in prices[1:]:
                e.append(p * k + e[-1] * (1 - k))
            return e

        e200 = ema(closes, 200)
        e50  = ema(closes, 50)

        current   = closes[-1]
        ema200_now = e200[-1]
        ema50_now  = e50[-1]

        dist_pct  = (current - ema200_now) / ema200_now * 100
        # 10-day EMA slope as % change
        slope_pct = (e200[-1] - e200[-11]) / e200[-11] * 100 if len(e200) > 10 else 0

        score, reasons = 0, []

        # Sub-signal 1: price above/below 200 EMA
        if current > ema200_now:
            score += 1
            reasons.append(f'Above 200 EMA (+{dist_pct:.1f}%)')
        else:
            score -= 1
            reasons.append(f'Below 200 EMA ({dist_pct:.1f}%)')

        # Sub-signal 2: 200 EMA slope — is the long-term trend rising or falling?
        if slope_pct > 0.3:
            score += 1
            reasons.append(f'200 EMA rising (slope {slope_pct:+.2f}%)')
        elif slope_pct < -0.3:
            score -= 1
            reasons.append(f'200 EMA falling (slope {slope_pct:+.2f}%)')

        # Sub-signal 3: MA50 vs MA200 — golden / death cross
        if ema50_now > ema200_now:
            score += 1
            reasons.append('MA50 > MA200 (golden cross)')
        else:
            score -= 1
            reasons.append('MA50 < MA200 (death cross)')

        sig = 'BULL' if score > 0 else ('BEAR' if score < 0 else 'NEUTRAL')
        return {'score': score, 'max_score': 3, 'signal': sig,
                'ema200': round(ema200_now, 0), 'dist_pct': round(dist_pct, 2),
                'slope': round(slope_pct, 3), 'reasons': reasons}
    except Exception as e:
        print(f'200 EMA pillar error: {e}')
        return {'score': 0, 'max_score': 3, 'signal': 'UNKNOWN'}


def _get_vwap_score(symbol):
    """
    VWAP (Volume Weighted Average Price) pillar — live only, intraday.
    Uses the last 24 completed hourly candles from Binance.
    Price above VWAP = institutional buying pressure (bullish).
    Price below VWAP = selling pressure (bearish).
    Distance from VWAP indicates overextension or capitulation.
    """
    try:
        raw = _binance_klines(symbol, '1h', 26)
        if len(raw) < 10:
            return {'score': 0, 'max_score': 2, 'signal': 'UNKNOWN'}

        completed = raw[:-1]   # drop the partial current candle

        # VWAP = Σ(typical_price × quote_volume) / Σ(quote_volume)
        tp_vol = sum(
            (float(c[2]) + float(c[3]) + float(c[4])) / 3 * float(c[7])
            for c in completed
        )
        total_vol = sum(float(c[7]) for c in completed)
        vwap      = tp_vol / total_vol if total_vol > 0 else float(completed[-1][4])

        current  = float(completed[-1][4])
        dist_pct = (current - vwap) / vwap * 100

        if dist_pct > 3.0:
            score, signal = 2,  f'WELL ABOVE VWAP (+{dist_pct:.1f}%)'
        elif dist_pct > 1.0:
            score, signal = 1,  f'ABOVE VWAP (+{dist_pct:.1f}%)'
        elif dist_pct > -1.0:
            score, signal = 0,  f'AT VWAP ({dist_pct:+.1f}%)'
        elif dist_pct > -3.0:
            score, signal = -1, f'BELOW VWAP ({dist_pct:.1f}%)'
        else:
            score, signal = -2, f'WELL BELOW VWAP ({dist_pct:.1f}%)'

        return {'vwap': round(vwap, 2), 'current': round(current, 2),
                'dist_pct': round(dist_pct, 2), 'signal': signal,
                'score': score, 'max_score': 2}
    except Exception as e:
        print(f'VWAP pillar error: {e}')
        return {'score': 0, 'max_score': 2, 'signal': 'UNKNOWN'}


def _normalize(score, max_score):
    """Normalize pillar score to -1..+1 range."""
    if max_score == 0: return 0
    return max(-1.0, min(1.0, score / max_score))

def _get_technical_score(symbol, price_data):
    """Technical pillar: MA, RSI, price trend."""
    score = 0
    reasons = []
    c24 = float(price_data.get('change_24h', 0))
    c7  = float(price_data.get('change_7d', 0))
    if c24 > 2:   score += 1; reasons.append(f'Price +{c24:.1f}% 24h')
    elif c24 < -2: score -= 1; reasons.append(f'Price {c24:.1f}% 24h')
    if c7 > 5:    score += 1; reasons.append(f'Strong 7d trend +{c7:.1f}%')
    elif c7 < -5:  score -= 1; reasons.append(f'Weak 7d trend {c7:.1f}%')
    ma = get_ma(symbol)
    rsi_data = get_rsi(symbol)
    rsi = rsi_data.get('rsi', 50)
    if ma.get('cross') == 'golden': score += 1; reasons.append('Golden Cross')
    elif ma.get('cross') == 'death': score -= 1; reasons.append('Death Cross')
    if ma.get('current') and ma.get('ma50'):
        if ma['current'] > ma['ma50']: score += 1; reasons.append('Above MA50')
        else: score -= 1; reasons.append('Below MA50')
    if rsi < 30:   score += 1; reasons.append(f'RSI oversold ({rsi})')
    elif rsi > 70: score -= 1; reasons.append(f'RSI overbought ({rsi})')
    return {'score': score, 'max_score': 5, 'reasons': reasons, 'ma': ma, 'rsi': rsi}

def get_signal(symbol='BTC'):
    # 1. Get price data
    p = get_price([symbol]).get(symbol, {})
    if not p: p = get_price_binance(symbol)
    
    # 1b. Get 24h price change from Binance ticker
    try:
        import requests as req
        pair = symbol.upper() + 'USDT'
        ticker = req.get(f'https://api.binance.com/api/v3/ticker/24hr?symbol={pair}', timeout=5).json()
        p['price_change_24h'] = float(ticker.get('priceChangePercent', 0))
    except:
        p['price_change_24h'] = 0

    # 2. Get regime first (sets weights)
    regime_data = get_regime(symbol)
    regime = regime_data.get('regime', 'RANGING')
    weights = regime_data.get('weights', {})

    # 3. Gather all pillars
    technical  = _get_technical_score(symbol, p)
    derivs     = get_derivatives_summary(symbol)
    macro      = get_macro_cross_summary()
    sentiment  = get_sentiment_summary(symbol)
    vix        = _get_vix_score()
    volume     = _get_volume_score(symbol)
    btc_dom    = _get_btc_dominance_score(macro.get('stablecoin', {}).get('btc_dominance', 0))
    rsi_div    = _get_rsi_divergence_score(symbol)
    ema200     = _get_ema200_score(symbol)
    vwap       = _get_vwap_score(symbol)

    # 4. Normalize each pillar to -1..+1
    pillars = {
        'technical':   _normalize(technical['score'],  technical['max_score']),
        'derivatives': _normalize(derivs['score'],     derivs['max_score']),
        'macro':       _normalize(macro['score'],      macro['max_score']),
        'sentiment':   _normalize(sentiment['score'],  sentiment['max_score']),
        'vix':         _normalize(vix['score'],        vix['max_score']),
        'volume':      _normalize(volume['score'],     volume['max_score']),
        'btc_dom':     _normalize(btc_dom['score'],    btc_dom['max_score']),
        'rsi_div':     _normalize(rsi_div['score'],    rsi_div['max_score']),
        'ema200':      _normalize(ema200['score'],     ema200['max_score']),
        'vwap':        _normalize(vwap['score'],       vwap['max_score']),
    }

    # 5. Apply regime weights
    weighted = {k: v * weights.get(k, 1.0) for k, v in pillars.items()}

    # 6. Composite score (-1..+1)
    composite = sum(weighted.values()) / len(weighted)

    # 7. Conviction = how much pillars AGREE (low std = high conviction)
    pillar_values = list(pillars.values())
    std_dev = statistics.stdev(pillar_values) if len(pillar_values) > 1 else 0
    conviction = max(0, round((1 - std_dev) * 100))

    # 8. Anti-crowding override:
    # If funding is extreme AND F&G is extreme, downgrade conviction
    fg_val = sentiment['fear_greed'].get('value', 50)
    funding_val = derivs['funding'].get('current', 0)
    if (fg_val > 75 and funding_val > 0.03) or (fg_val < 25 and funding_val < -0.02):
        conviction = max(0, conviction - 20)

    # 9. Signal threshold
    if composite > 0.25 and conviction > 45:   signal = 'BUY'
    elif composite < -0.25 and conviction > 45: signal = 'SELL'
    else:                                        signal = 'NEUTRAL'

    # Collect all reasons
    all_reasons = technical['reasons'].copy()
    if derivs['funding']['signal'] != 'NEUTRAL':
        all_reasons.append(f"Funding: {derivs['funding']['signal']} ({derivs['funding']['current']:+.4f}%)")
    if derivs['long_short']['signal'] not in ('BALANCED', 'UNKNOWN'):
        all_reasons.append(f"L/S Ratio: {derivs['long_short']['signal']} ({derivs['long_short']['longs_pct']}% longs)")
    if macro['dxy']['signal'] not in ('NEUTRAL', 'UNKNOWN'):
        all_reasons.append(f"DXY {macro['dxy']['signal']} ({macro['dxy']['change_5d']:+.1f}% 5d)")
    if macro['spx']['signal'] not in ('NEUTRAL', 'UNKNOWN'):
        all_reasons.append(f"SPX {macro['spx']['signal']} ({macro['spx']['change_5d']:+.1f}% 5d)")
    all_reasons.append(f"Sentiment: {sentiment['news']['signal']} ({sentiment['news']['bull_pct']}% bullish)")
    if vix['signal'] not in ('NEUTRAL', 'UNKNOWN'):
        all_reasons.append(f"VIX: {vix['signal']} ({vix['vix']})")
    if volume['signal'] not in ('NORMAL', 'UNKNOWN'):
        all_reasons.append(f"Volume: {volume['signal']} (ratio {volume.get('ratio', 0):.1f}x avg)")
    if btc_dom['signal'] not in ('UNKNOWN',) and 'STABLE' not in btc_dom['signal']:
        all_reasons.append(f"BTC Dom: {btc_dom['signal']}")
    if rsi_div['signal'] not in ('NO DIVERGENCE', 'UNKNOWN'):
        all_reasons.append(f"RSI Div: {rsi_div['signal']} (RSI {rsi_div.get('rsi_current', '?')})")
    if ema200['signal'] not in ('NEUTRAL', 'UNKNOWN'):
        all_reasons.append(f"200 EMA: {ema200['signal']} ({ema200.get('dist_pct', 0):+.1f}%)")
    if vwap['signal'] not in ('UNKNOWN',) and 'AT VWAP' not in vwap['signal']:
        all_reasons.append(f"VWAP: {vwap['signal']}")

    return {
        'signal': signal,
        'composite': round(composite, 3),
        'conviction': conviction,
        'regime': regime,
        'regime_confidence': regime_data.get('confidence', 0),
        'pillars': {k: round(v, 3) for k, v in pillars.items()},
        'weighted_pillars': {k: round(v, 3) for k, v in weighted.items()},
        'reasons': all_reasons,
        'price': p.get('price'),
        'price_change_24h': p.get('price_change_24h', 0),
        'fear_greed': sentiment['fear_greed'],
        'rsi': technical['rsi'],
        'ma': technical['ma'],
        'derivatives': derivs,
        'macro': macro,
        'sentiment': sentiment,
        'regime_data': regime_data,
        'vix':     vix,
        'volume':  volume,
        'btc_dom': btc_dom,
        'rsi_div': rsi_div,
        'ema200':  ema200,
        'vwap':    vwap,
    }

if __name__ == '__main__':
    import json
    print(json.dumps(get_signal('BTC'), indent=2))
