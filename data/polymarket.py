import os, requests
from dotenv import load_dotenv

GAMMA_URL = 'https://gamma-api.polymarket.com'

def get_crypto_markets():
    try:
        r = requests.get(GAMMA_URL + '/markets' ,timeout=10)
        r.raise_for_status()
        return r.json()[:5]
    except Exception as e:
        print(e)
        return []
            
    if __name__== '__main__':
        import json
        print(json.dumps(get_crypto_markets(), indent=2))
