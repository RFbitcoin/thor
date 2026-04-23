import os,sys
sys.path.insert(0,"/home/rfranklin/thor")
from data.coingecko import get_ohlcv

def calc_rsi(p,n=14):
 if len(p)<n+1:return 50
 d=[p[i]-p[i-1]for i in range(1,len(p))]
 g=sum(x for x in d[-n:]if x>0)/n
 l=sum(-x for x in d[-n:]if x<0)/n
 return 50 if l==0 else round(100-(100/(1+g/l)),2)

def get_rsi(s="BTC"):
 r=get_ohlcv(s,days=30)
 p=[x[1]for x in r.get("prices",[])]
 return {"symbol":s,"rsi":calc_rsi(p)}

def calc_ma(prices, period):
 if len(prices) < period: return None
 return round(sum(prices[-period:]) / period, 2)

def get_ma(s="BTC"):
 r = get_ohlcv(s, days=210)
 prices = [x[1] for x in r.get("prices", [])]
 ma50 = calc_ma(prices, 50)
 ma200 = calc_ma(prices, 200)
 current = prices[-1] if prices else None
 cross = None
 if ma50 and ma200:
  cross = "golden" if ma50 > ma200 else "death"
 return {"symbol": s, "ma50": ma50, "ma200": ma200, "current": current, "cross": cross}

if __name__=="__main__":
 import json
 print(json.dumps(get_rsi("BTC"),indent=2))
 print(json.dumps(get_ma("BTC"),indent=2))
