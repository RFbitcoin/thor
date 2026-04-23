"""
Macro cross-asset signals: DXY, S&P 500, BTC correlation, stablecoin dominance.
"""
import requests

HEADERS = {'User-Agent': 'Mozilla/5.0'}

def _yahoo_change(ticker, days=5):
    """Fetch % change over last N days from Yahoo Finance."""
    try:
        r = requests.get(
            f'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}',
            params={'interval': '1d', 'range': f'{days}d'},
            headers=HEADERS, timeout=10)
        closes = r.json()['chart']['result'][0]['indicators']['quote'][0]['close']
        closes = [x for x in closes if x is not None]
        if len(closes) < 2: return 0, closes[-1] if closes else 0
        change = ((closes[-1] - closes[0]) / closes[0]) * 100
        return round(change, 2), round(closes[-1], 2)
    except Exception as e:
        print(f'Yahoo {ticker} error: {e}')
        return 0, 0

def get_dxy():
    """DXY (Dollar Index). Rising DXY = risk-off = bearish crypto."""
    change, value = _yahoo_change('DX-Y.NYB', 5)
    if change > 1:    signal = 'STRONG';  score = -1   # strong dollar = bearish crypto
    elif change > 0.3: signal = 'RISING'; score = -1
    elif change < -1: signal = 'WEAK';   score = 1    # weak dollar = bullish crypto
    elif change < -0.3: signal = 'FALLING'; score = 1
    else:              signal = 'NEUTRAL'; score = 0
    return {'value': value, 'change_5d': change, 'signal': signal, 'score': score}

def get_spx():
    """S&P 500 correlation proxy. Crypto is risk-on; SPX trending up = tailwind."""
    change, value = _yahoo_change('%5EGSPC', 5)
    if change > 2:    signal = 'STRONG';  score = 1
    elif change > 0.5: signal = 'RISING'; score = 1
    elif change < -2: signal = 'WEAK';   score = -1
    elif change < -0.5: signal = 'FALLING'; score = -1
    else:              signal = 'NEUTRAL'; score = 0
    return {'value': value, 'change_5d': change, 'signal': signal, 'score': score}

def get_us10y():
    """10Y Treasury yield. Rapidly rising yields = risk-off = bearish crypto."""
    change, value = _yahoo_change('%5ETNX', 5)
    if change > 5:    signal = 'SURGING'; score = -1   # yields surging = bad
    elif change > 2:  signal = 'RISING';  score = -1
    elif change < -5: signal = 'FALLING'; score = 1    # yields falling = good
    elif change < -2: signal = 'EASING';  score = 1
    else:             signal = 'STABLE';  score = 0
    return {'value': value, 'change_5d': change, 'signal': signal, 'score': score}

def get_stablecoin_dominance():
    """
    USDT + USDC dominance proxy via CoinGecko free.
    Rising stablecoin dominance = money leaving crypto = bearish.
    """
    try:
        r = requests.get('https://api.coingecko.com/api/v3/global', timeout=10)
        data = r.json().get('data', {})
        dom = data.get('market_cap_percentage', {})
        usdt_dom = dom.get('usdt', 0)
        usdc_dom = dom.get('usdc', 0)
        stable_dom = round(usdt_dom + usdc_dom, 2)
        btc_dom = round(dom.get('btc', 0), 2)
        # High stable dom = money on sidelines = potential fuel OR fear
        if stable_dom > 12:  signal = 'HIGH';    score = -1
        elif stable_dom > 8: signal = 'ELEVATED'; score = 0
        else:                signal = 'LOW';     score = 1
        return {'stable_dominance': stable_dom, 'btc_dominance': btc_dom,
                'signal': signal, 'score': score}
    except Exception as e:
        print(f'Stablecoin dom error: {e}')
        return {'stable_dominance': 0, 'btc_dominance': 0, 'signal': 'UNKNOWN', 'score': 0}

def get_macro_cross_summary():
    """Full macro cross-asset pillar."""
    dxy = get_dxy()
    spx = get_spx()
    us10y = get_us10y()
    stable = get_stablecoin_dominance()
    score = dxy['score'] + spx['score'] + us10y['score'] + stable['score']
    return {
        'dxy': dxy,
        'spx': spx,
        'us10y': us10y,
        'stablecoin': stable,
        'score': score,
        'max_score': 4
    }

if __name__ == '__main__':
    import json
    print(json.dumps(get_macro_cross_summary(), indent=2))
