import os
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env'))

FRED_API_KEY = os.getenv('FRED_API_KEY')
BASE_URL = 'https://api.stlouisfed.org/fred/series/observations'

def get_series(series_id, limit=1):
    params = {
        'series_id': series_id,
        'api_key': FRED_API_KEY,
        'file_type': 'json',
        'sort_order': 'desc',
        'limit': limit
    }
    try:
        r = requests.get(BASE_URL, params=params, timeout=10)
        r.raise_for_status()
        obs = r.json().get('observations', [])
        return [{'date': o['date'], 'value': o['value']} for o in obs]
    except Exception as e:
        print(f'FRED error ({series_id}): {e}')
        return []

def get_macro():
    return {
        'fed_funds_rate': get_series('FEDFUNDS', 1),
        'm2_money_supply': get_series('M2SL', 1),
    }

if __name__ == '__main__':
    import json
    print(json.dumps(get_macro(), indent=2))
