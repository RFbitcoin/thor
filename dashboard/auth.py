"""
THOR Auth Layer
---------------
Protects trading endpoints while keeping the dashboard read-only public.

Password is stored as a PBKDF2-SHA256 hash in ../.env as THOR_PASSWORD_HASH.
If that key is absent or empty, THOR is in first-run mode and shows the
setup screen instead of a login prompt.

No external dependencies — uses Python stdlib hashlib only.
"""
import os
import secrets
import hashlib
import re
from datetime import datetime, timedelta
from functools import wraps
from flask import request, jsonify
from pathlib import Path
from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parent.parent / '.env'

def _reload_env():
    load_dotenv(_ENV_PATH, override=True)

_reload_env()

# ---------------------------------------------------------------------------
# Password hashing  (PBKDF2-HMAC-SHA256, 260 000 iterations)
# ---------------------------------------------------------------------------
_ITERATIONS = 260_000

def _hash_password(password: str, salt: str | None = None) -> str:
    """Return 'salt:hash' string.  Generates a new salt if none given."""
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), _ITERATIONS)
    return f"{salt}:{dk.hex()}"

def _verify_password(password: str, stored: str) -> bool:
    """Verify password against stored 'salt:hash' string."""
    try:
        salt, _ = stored.split(':', 1)
        return secrets.compare_digest(stored, _hash_password(password, salt))
    except Exception:
        return False

# ---------------------------------------------------------------------------
# .env read / write helpers
# ---------------------------------------------------------------------------
def _read_env_value(key: str) -> str:
    """Read a single key from the .env file (bypasses os.environ cache)."""
    try:
        for line in _ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line.startswith(f'{key}='):
                return line[len(key)+1:].strip()
    except Exception:
        pass
    return ''

def _write_env_value(key: str, value: str):
    """Update or append a key=value line in the .env file."""
    try:
        text = _ENV_PATH.read_text() if _ENV_PATH.exists() else ''
        pattern = re.compile(rf'^{re.escape(key)}=.*$', re.MULTILINE)
        if pattern.search(text):
            text = pattern.sub(f'{key}={value}', text)
        else:
            text = text.rstrip('\n') + f'\n{key}={value}\n'
        _ENV_PATH.write_text(text)
        _reload_env()
    except Exception as e:
        raise RuntimeError(f"Could not write to .env: {e}")

# ---------------------------------------------------------------------------
# First-run detection
# ---------------------------------------------------------------------------
def is_first_run() -> bool:
    """True if no password hash has been set yet."""
    return not bool(_read_env_value('THOR_PASSWORD_HASH'))

# ---------------------------------------------------------------------------
# Password management
# ---------------------------------------------------------------------------
def setup_password(new_password: str) -> tuple[dict, int]:
    """
    Set password for the first time.
    Only succeeds if THOR is still in first-run mode.
    """
    if not is_first_run():
        return {'error': 'Setup already complete. Use change-password instead.'}, 403
    if len(new_password) < 8:
        return {'error': 'Password must be at least 8 characters.'}, 400

    _write_env_value('THOR_PASSWORD_HASH', _hash_password(new_password))
    # Remove the legacy plaintext key if present
    _write_env_value('THOR_PASSWORD', '')
    return {'ok': True, 'msg': 'Password set. Welcome to THOR.'}, 200

def change_password(current_password: str, new_password: str) -> tuple[dict, int]:
    """Change password — requires current password for verification."""
    stored = _read_env_value('THOR_PASSWORD_HASH')
    if not stored or not _verify_password(current_password, stored):
        return {'error': 'Current password is incorrect.'}, 401
    if len(new_password) < 8:
        return {'error': 'New password must be at least 8 characters.'}, 400
    if current_password == new_password:
        return {'error': 'New password must be different from the current one.'}, 400

    _write_env_value('THOR_PASSWORD_HASH', _hash_password(new_password))
    # Invalidate all existing sessions so everyone must log in again
    SESSIONS.clear()
    return {'ok': True, 'msg': 'Password updated. Please log in again.'}, 200

# ---------------------------------------------------------------------------
# Session store
# ---------------------------------------------------------------------------
SESSIONS: dict = {}

def _create_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    SESSIONS[token] = {
        'username': username,
        'created_at': datetime.now().isoformat(),
        'expires_at': (datetime.now() + timedelta(hours=24)).isoformat(),
    }
    return token

def _validate_token(token: str) -> bool:
    session = SESSIONS.get(token)
    if not session:
        return False
    return datetime.now() < datetime.fromisoformat(session['expires_at'])

# ---------------------------------------------------------------------------
# Public auth functions
# ---------------------------------------------------------------------------
def login(username: str, password: str) -> tuple[dict, int]:
    """Authenticate and return a bearer token."""
    if username != 'admin':
        return {'error': 'Invalid credentials'}, 401
    stored = _read_env_value('THOR_PASSWORD_HASH')
    if not stored or not _verify_password(password, stored):
        return {'error': 'Invalid credentials'}, 401
    token = _create_session(username)
    return {'token': token, 'username': username}, 200

def logout(token: str) -> tuple[dict, int]:
    """Invalidate a session token."""
    SESSIONS.pop(token, None)
    return {'ok': True}, 200

def require_auth(f):
    """Route decorator — rejects requests without a valid bearer token."""
    @wraps(f)
    def decorated(*args, **kwargs):
        header = request.headers.get('Authorization', '')
        if not header.startswith('Bearer '):
            return jsonify({'error': 'Authentication required'}), 401
        token = header.split(' ', 1)[1]
        if not _validate_token(token):
            return jsonify({'error': 'Invalid or expired session'}), 401
        return f(*args, **kwargs)
    return decorated
