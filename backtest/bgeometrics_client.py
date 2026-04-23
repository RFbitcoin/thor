import requests
import pandas as pd
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
import json

load_dotenv(os.path.expanduser('~/.thor/config/.env'))
API_KEY = os.getenv('BGEOMETRICS_API_KEY')
BASE_URL = "https://bitcoin-data.com/v1"

class BGeometricsClient:
    def __init__(self, api_key=None):
        self.api_key = api_key or API_KEY
        self.base_url = BASE_URL
        self.session = requests.Session()
    
    def _get(self, metric, start_date=None, end_date=None):
        """Fetch metric data. Returns list of dicts with 'd' (date) and metric value."""
        url = f"{self.base_url}/{metric}"
        params = {'token': self.api_key}
        
        if start_date:
            params['start'] = start_date
        if end_date:
            params['end'] = end_date
        
        resp = self.session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        
        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            print(f"JSON decode error for {metric}: {e}")
            print(f"Response text: {resp.text[:500]}")
            return []
        
        if isinstance(data, list):
            return data
        elif isinstance(data, dict) and 'd' in data:
            return [data]  # Single value wrapped
        else:
            print(f"Unexpected format for {metric}: {type(data)}")
            return []
    
    def _parse_date_value(self, row, value_key):
        """Parse date column, handling various formats including milliseconds."""
        if 'd' in row:
            date_str = row['d']
            # Remove milliseconds if present (e.g., "2026-04-04T12:34:56.001Z" -> "2026-04-04T12:34:56Z")
            if '.' in date_str:
                date_str = date_str.split('.')[0] + 'Z'
            try:
                # Parse and then strip timezone to ensure consistency
                dt = pd.to_datetime(date_str, utc=True).tz_localize(None)
                return dt, row.get(value_key)
            except:
                return None, None
        return None, None
    
    def get_sopr(self, start_date=None, end_date=None):
        """Spent Output Profit Ratio"""
        data = self._get('sopr', start_date, end_date)
        records = []
        for row in data:
            date, val = self._parse_date_value(row, 'sopr')
            if date and val is not None:
                records.append({'date': date, 'sopr': float(val)})
        return pd.DataFrame(records).set_index('date').sort_index()
    
    def get_mvrv_zscore(self, start_date=None, end_date=None):
        """MVRV Z-Score"""
        data = self._get('mvrv-zscore', start_date, end_date)
        records = []
        for row in data:
            date, val = self._parse_date_value(row, 'mvrvZscore')
            if date and val is not None:
                records.append({'date': date, 'mvrv_zscore': float(val)})
        return pd.DataFrame(records).set_index('date').sort_index()
    
    def get_btc_price(self, start_date=None, end_date=None):
        """BTC Price in USD"""
        data = self._get('btc-price', start_date, end_date)
        records = []
        for row in data:
            date, val = self._parse_date_value(row, 'btcPrice')
            if date and val is not None:
                records.append({'date': date, 'btc_price': float(val)})
        return pd.DataFrame(records).set_index('date').sort_index()
    
    def get_funding_rate(self, start_date=None, end_date=None):
        """Funding Rate (handles timestamps with milliseconds)"""
        data = self._get('funding-rate', start_date, end_date)
        records = []
        for row in data:
            date, val = self._parse_date_value(row, 'fundingRate')
            if date and val is not None:
                records.append({'date': date, 'funding_rate': float(val)})
        return pd.DataFrame(records).set_index('date').sort_index()
    
    def get_fear_greed(self, start_date=None, end_date=None):
        """Fear & Greed Index"""
        data = self._get('fear-greed', start_date, end_date)
        records = []
        for row in data:
            date, val = self._parse_date_value(row, 'fearGreed')
            if date and val is not None:
                records.append({'date': date, 'fear_greed': float(val)})
        return pd.DataFrame(records).set_index('date').sort_index()

    def get_vix_history(self, start_date=None, end_date=None):
        """Historical daily VIX from Yahoo Finance (no auth required)."""
        try:
            url = 'https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX'
            params = {'interval': '1d', 'range': '2y'}
            headers = {'User-Agent': 'Mozilla/5.0 (compatible; THOR/1.0)'}
            r = self.session.get(url, params=params, headers=headers, timeout=10)
            r.raise_for_status()
            data = r.json()
            result = data['chart']['result'][0]
            timestamps = result['timestamp']
            closes = result['indicators']['quote'][0]['close']
            records = []
            for ts, v in zip(timestamps, closes):
                if v is None:
                    continue
                date = pd.Timestamp(ts, unit='s').normalize()
                records.append({'date': date, 'vix': float(v)})
            df = pd.DataFrame(records).set_index('date').sort_index()
            if start_date:
                df = df.loc[start_date:]
            if end_date:
                df = df.loc[:end_date]
            return df
        except Exception as e:
            print(f"VIX history fetch error: {e}")
            return pd.DataFrame(columns=['vix'])

    def get_binance_ohlcv(self, start_date=None, end_date=None, symbol='BTCUSDT'):
        """Historical daily OHLCV from Binance public API (no key required).
        Returns columns: open, high, low, close, volume (quote volume in USDT).
        """
        try:
            if start_date and end_date:
                days = (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days + 10
            else:
                days = 750
            limit = min(max(days, 30), 1000)

            params = {'symbol': symbol, 'interval': '1d', 'limit': limit}
            if start_date:
                params['startTime'] = int(pd.Timestamp(start_date).timestamp() * 1000)

            r = self.session.get(
                'https://api.binance.com/api/v3/klines',
                params=params, timeout=15
            )
            r.raise_for_status()
            klines = r.json()

            records = []
            for k in klines:
                date = pd.Timestamp(k[0], unit='ms').normalize()
                records.append({
                    'date':   date,
                    'open':   float(k[1]),
                    'high':   float(k[2]),
                    'low':    float(k[3]),
                    'close':  float(k[4]),
                    'volume': float(k[7]),   # quote volume (USDT)
                })
            df = pd.DataFrame(records).set_index('date').sort_index()
            if end_date:
                df = df.loc[:end_date]
            return df
        except Exception as e:
            print(f"Binance OHLCV fetch error: {e}")
            return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])


if __name__ == '__main__':
    client = BGeometricsClient()
    print("Testing BGeometrics client...")

    # Quick test
    try:
        sopr = client.get_sopr(start_date='2026-01-01', end_date='2026-01-10')
        print(f"SOPR shape: {sopr.shape}")
        print(sopr.head())
    except Exception as e:
        print(f"Error: {e}")
