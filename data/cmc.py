import os
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

CMC_API_KEY = os.getenv('CMC_API_KEY')
BASE_URL = 'https://pro-api.coinmarketcap.com/v1'
BINANCE_URL = 'https://api.binance.com/api/v3'

def get_price_binance(symbols=['BTC', 'ETH', 'XRP']):
    """Fetch latest price from Binance (no rate limits, reliable)"""
    results = {}
    for sym in symbols:
        try:
            resp = requests.get(f'{BINANCE_URL}/ticker/24hr?symbol={sym}USDT', timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                results[sym] = {
                    'price': float(data['lastPrice']),
                    'change_24h': float(data['priceChangePercent']),
                    'change_7d': 0,
                    'volume_24h': float(data['quoteVolume']),
                    'market_cap': 0,
                }
        except Exception as e:
            print(f'Binance error for {sym}: {e}')
    return results

def get_price(symbols=['BTC', 'ETH', 'XRP']):
    """Fetch latest price + market data. Uses Binance (reliable, no rate limits)"""
    return get_price_binance(symbols)

if __name__ == '__main__':
    import json
    data = get_price(['BTC', 'ETH', 'XRP', 'ONDO', 'LINK', 'AVAX', 'SOL'])
    print(json.dumps(data, indent=2))
