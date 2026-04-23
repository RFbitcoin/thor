"""
BGeometrics MCP (Model Context Protocol) integration.
Streamable HTTP interface to all 154+ BTC metrics at once.
"""
import requests
import os
import json
from dotenv import load_dotenv
import time

load_dotenv(os.path.expanduser('~/.thor/config/.env'))
API_KEY = os.getenv('BGEOMETRICS_API_KEY', 'P9htMlpOYn')  # Fallback to provided token
MCP_ENDPOINT = os.getenv('MCP_ENDPOINT', "http://100.80.92.76:3000/mcp/message")

_session_id = None
_session_expires = 0
_cache = {}
CACHE_TTL = 60

def _init_session():
    """Initialize MCP session with API token."""
    global _session_id, _session_expires
    
    if _session_id and time.time() < _session_expires:
        return _session_id  # Session still valid
    
    try:
        payload = {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "token": API_KEY
            },
            "id": 1
        }
        
        resp = requests.post(
            MCP_ENDPOINT,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        resp.raise_for_status()
        
        # Parse response (may be single JSON or newline-delimited)
        text = resp.text.strip()
        if text.startswith('['):
            # Array response
            data = json.loads(text)
            _session_id = API_KEY  # Use token as session ID
        else:
            # Single or newline-delimited
            lines = text.split('\n')
            data = json.loads(lines[0])
            _session_id = data.get('mcp-session-id', API_KEY)
        
        _session_expires = time.time() + 3600  # 1h session
        print(f"MCP session initialized")
        return _session_id
    except Exception as e:
        print(f"MCP session init error: {e}")
        return None

def _call_tool(method, params):
    """Call MCP tool method."""
    session_id = _init_session()
    if not session_id:
        return None
    
    try:
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": method,
                "arguments": params,
                "token": API_KEY
            }
        }
        
        resp = requests.post(
            MCP_ENDPOINT,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "mcp-session-id": session_id,
                "Authorization": f"Bearer {API_KEY}"
            },
            timeout=15
        )
        resp.raise_for_status()
        
        # Parse response
        text = resp.text.strip()
        if not text:
            return None
        
        # Handle various response formats
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Try newline-delimited
            lines = text.split('\n')
            data = json.loads(lines[0])
        
        if isinstance(data, dict) and 'result' in data:
            return data['result']
        elif isinstance(data, list) and len(data) > 0:
            return data[0] if isinstance(data[0], dict) else data
        else:
            return data
    except Exception as e:
        print(f"MCP tool call error: {e}")
        return None

def get_bitcoin_metrics():
    """Get all Bitcoin metrics in one call."""
    cache_key = "bitcoin_metrics"
    
    if cache_key in _cache:
        entry = _cache[cache_key]
        if time.time() < entry['expires']:
            return entry['data']
    
    # Request all key metrics at once
    metrics_list = [
        "sopr",
        "mvrv-zscore",
        "btc-price",
        "funding-rate",
        "fear-greed",
        "exchange-netflow",
        "exchange-inflow",
        "exchange-outflow",
        "lth-position-change",
        "sth-position-change",
        "realized-volatility",
        "open-interest",
        "long-short-ratio",
        "dxy",
        "vix",
        "m2",
        "fed-funds-rate"
    ]
    
    result = _call_tool("get_latest", {"metrics": metrics_list})
    
    if result:
        _cache[cache_key] = {
            'data': result,
            'expires': time.time() + CACHE_TTL
        }
    
    return result or {}

def parse_metrics(raw_metrics):
    """Parse raw MCP metrics into THOR signal format."""
    parsed = {}
    
    # On-chain metrics
    parsed['sopr'] = raw_metrics.get('sopr', {}).get('value')
    parsed['mvrv_zscore'] = raw_metrics.get('mvrv-zscore', {}).get('value')
    parsed['exchange_netflow'] = raw_metrics.get('exchange-netflow', {}).get('value')
    parsed['exchange_inflow'] = raw_metrics.get('exchange-inflow', {}).get('value')
    parsed['exchange_outflow'] = raw_metrics.get('exchange-outflow', {}).get('value')
    
    # Holder metrics
    parsed['lth_change'] = raw_metrics.get('lth-position-change', {}).get('value')
    parsed['sth_change'] = raw_metrics.get('sth-position-change', {}).get('value')
    
    # Market metrics
    parsed['btc_price'] = raw_metrics.get('btc-price', {}).get('value')
    parsed['funding_rate'] = raw_metrics.get('funding-rate', {}).get('value')
    parsed['fear_greed'] = raw_metrics.get('fear-greed', {}).get('value')
    parsed['realized_vol'] = raw_metrics.get('realized-volatility', {}).get('value')
    
    # Derivatives
    parsed['open_interest'] = raw_metrics.get('open-interest', {}).get('value')
    parsed['long_short_ratio'] = raw_metrics.get('long-short-ratio', {}).get('value')
    
    # Macro
    parsed['dxy'] = raw_metrics.get('dxy', {}).get('value')
    parsed['vix'] = raw_metrics.get('vix', {}).get('value')
    parsed['m2'] = raw_metrics.get('m2', {}).get('value')
    parsed['fed_rate'] = raw_metrics.get('fed-funds-rate', {}).get('value')
    
    return parsed

if __name__ == '__main__':
    print("Testing BGeometrics MCP...")
    metrics = get_bitcoin_metrics()
    if metrics:
        parsed = parse_metrics(metrics)
        print(json.dumps(parsed, indent=2))
    else:
        print("No metrics retrieved")
