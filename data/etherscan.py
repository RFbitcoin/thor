import os, requests
from dotenv import load_dotenv

ETHERSCAN_KEY = os.getenv('ETHERSCAN_KEY')
BASE_URL = 'https://api.etherscan.io/api'

def get_eth_price():
    params = {'module': 'stats', 'action': 'ethprice', 'apikey': ETHERSCAN_KEY}
    try:
        r = requests.get(BASE_URL, params=params, timeout=10)
        r.raise_for_status()
        return r.json().get('result', {})
    except Exception as e:
        print(e)
        return {}
    
if __name__ == '__main__':

            import json
            print(json.dumps(get_eth_price(), indent=2))
