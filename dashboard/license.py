"""
THOR License Engine — validates subscriptions via LemonSqueezy.
Uses activate/validate endpoints (no API key needed from client side).
Caches result daily; 7-day grace period if validation server unreachable.
"""
import os, json, time, socket
import requests
from pathlib import Path

LEMON_ACTIVATE_URL = "https://api.lemonsqueezy.com/v1/licenses/activate"
LEMON_VALIDATE_URL = "https://api.lemonsqueezy.com/v1/licenses/validate"

GRACE_DAYS     = 7
CHECK_INTERVAL = 24 * 3600   # re-validate once per day

# LemonSqueezy variant ID → tier name
VARIANT_TIERS = {
    'da932673-6a1c-40bb-831b-bc76e39af4bc': 'pro',    # Signal Pro    $29/mo
    'b6b72467-a208-4555-b639-fad615b6d6b8': 'elite',  # Signal Elite  $59/mo
    'd1526a6f-7241-4bc6-9562-15b22c2e0148': 'elite+', # Signal Elite+ $99/mo
}

_BASE       = Path(__file__).resolve().parent.parent
_ENV_PATH   = _BASE / '.env'
_CACHE_PATH = _BASE / '.license_cache.json'


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_cache():
    try:
        return json.loads(_CACHE_PATH.read_text())
    except Exception:
        return {}

def _save_cache(data):
    try:
        _CACHE_PATH.write_text(json.dumps(data))
    except Exception:
        pass

def _read_env_value(key_name):
    """Read a value from .env file at runtime (not startup environ)."""
    try:
        for line in _ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line.startswith(f'{key_name}='):
                return line.split('=', 1)[1].strip()
    except Exception:
        pass
    return os.getenv(key_name, '').strip()

def _write_env_values(updates: dict):
    """Write/update key=value pairs in .env without clobbering other lines."""
    try:
        text  = _ENV_PATH.read_text() if _ENV_PATH.exists() else ''
        lines = text.splitlines()
        found = {k: False for k in updates}
        new_lines = []
        for line in lines:
            replaced = False
            for k, v in updates.items():
                if line.startswith(f'{k}='):
                    new_lines.append(f'{k}={v}')
                    found[k] = True
                    replaced = True
                    break
            if not replaced:
                new_lines.append(line)
        for k, v in updates.items():
            if not found[k]:
                new_lines.append(f'{k}={v}')
        _ENV_PATH.write_text('\n'.join(new_lines) + '\n')
    except Exception as e:
        print(f"Warning: could not write .env: {e}")


# ── Public API ────────────────────────────────────────────────────────────────

def activate(license_key: str):
    """
    Activate a license key for this THOR instance with LemonSqueezy.
    Returns (success: bool, message: str, response_data: dict).
    """
    instance_name = f"THOR-{socket.gethostname()}"
    try:
        r = requests.post(
            LEMON_ACTIVATE_URL,
            data={'license_key': license_key, 'instance_name': instance_name},
            timeout=12,
        )
        data = r.json()
        lk   = data.get('license_key', {})

        if data.get('activated') or lk.get('status') == 'active':
            instance_id = data.get('instance', {}).get('id', '')
            variant_id  = str(lk.get('variant_id', ''))
            tier        = VARIANT_TIERS.get(variant_id, 'pro')
            _write_env_values({
                'THOR_LICENSE_KEY':      license_key,
                'THOR_LICENSE_INSTANCE': instance_id,
            })
            _save_cache({
                'status':          'valid',
                'last_check':      time.time(),
                'last_valid_time': time.time(),
                'instance_id':     instance_id,
                'key_preview':     license_key[:8] + '...',
                'tier':            tier,
            })
            return True, 'License activated successfully.', data

        # Already activated on another instance
        if 'already activated' in str(data.get('error', '')).lower():
            return False, (
                'This key is already active on another device. '
                'Deactivate it from your LemonSqueezy dashboard first.'
            ), data

        error = data.get('error', 'Invalid license key.')
        return False, error, data

    except requests.exceptions.ConnectionError:
        return False, 'Could not reach the license server. Check your internet connection.', {}
    except Exception as e:
        return False, str(e), {}


def check():
    """
    Return current license status as a dict:
      status  : 'valid' | 'grace' | 'invalid' | 'required'
      message : human-readable string
      + optional: key_preview, days_remaining
    """
    key = _read_env_value('THOR_LICENSE_KEY')

    if not key:
        return {
            'status':  'required',
            'message': 'Enter your license key to activate THOR.',
        }

    cache       = _load_cache()
    now         = time.time()
    last_check  = cache.get('last_check', 0)
    last_valid  = cache.get('last_valid_time', 0)

    # Serve cached valid result if fresh enough
    if cache.get('status') == 'valid' and (now - last_check) < CHECK_INTERVAL:
        return {
            'status':      'valid',
            'message':     'License active.',
            'key_preview': cache.get('key_preview', ''),
        }

    # Try online validation
    instance_id = cache.get('instance_id') or _read_env_value('THOR_LICENSE_INSTANCE')
    try:
        payload = {'license_key': key}
        if instance_id:
            payload['instance_id'] = instance_id

        r    = requests.post(LEMON_VALIDATE_URL, data=payload, timeout=12)
        data = r.json()

        if data.get('valid'):
            variant_id = str(data.get('license_key', {}).get('variant_id', ''))
            tier       = VARIANT_TIERS.get(variant_id, cache.get('tier', 'pro'))
            _save_cache({
                'status':          'valid',
                'last_check':      now,
                'last_valid_time': now,
                'instance_id':     instance_id,
                'key_preview':     key[:8] + '...',
                'tier':            tier,
            })
            return {
                'status':      'valid',
                'message':     'License active.',
                'key_preview': key[:8] + '...',
                'tier':        tier,
            }

        # Genuinely invalid / cancelled / expired
        _save_cache({**cache, 'status': 'invalid', 'last_check': now})
        return {
            'status':  'invalid',
            'message': 'Your license is no longer active. Please renew at thor.rfbitcoin.com.',
        }

    except Exception:
        # Network unreachable — grace period
        if last_valid and (now - last_valid) < GRACE_DAYS * 86400:
            days_left = max(1, int(GRACE_DAYS - (now - last_valid) / 86400))
            return {
                'status':        'grace',
                'message':       f'License server unreachable — grace period active ({days_left}d remaining).',
                'days_remaining': days_left,
            }
        if last_valid:
            return {
                'status':  'invalid',
                'message': 'License could not be validated and grace period has expired.',
            }
        return {
            'status':  'required',
            'message': 'Could not reach license server. Check your internet connection.',
        }
