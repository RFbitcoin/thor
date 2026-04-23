import os, requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..','.env'))

BASE = 'https://api.cryptoquant.com/v1/btc'

def get_flows():
    try:
        r = requests.get(BASE + '/exchange-flows/inflow?window=day&exchange=all_exchange', timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(e)
        return {}
    
if __name__=='__main__':
    import json
    print(json.dumps(get_flows(), indent=2))
        
        
