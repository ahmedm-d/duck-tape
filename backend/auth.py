"""Supabase account sign-in for the Duck Tape workspace API.

Supabase Auth owns account creation, passwords and email confirmation; Duck Tape never receives a password.
The browser signs up or signs in with the project's *publishable* key and sends the Supabase access token
as `Authorization: Bearer <token>` on each `/api` request. The backend asks Supabase Auth
(`GET /auth/v1/user`) whether the token is valid and caches a positive answer for at most CACHE_SECONDS
(never past the token's own expiry), so a revoked session stops working within that window.

Three modes:
- **off**: Supabase not configured. No sign-in; every request is anonymous.
- **optional** (default once `STEWARD_SUPABASE_URL` and `STEWARD_SUPABASE_PUBLISHABLE_KEY` are set): the whole
  workspace works without an account; people sign up or sign in only when they choose to. A valid token
  attaches the account to the request; a missing, expired or unverifiable token is treated as anonymous.
- **required** (`STEWARD_AUTH_REQUIRED=1`, opt-in): every `/api` request except PUBLIC_PATHS needs a valid
  session (401 otherwise, 503 when Supabase Auth cannot be asked) and run.py refuses to start without a
  working configuration.

Tests, CI and the deterministic acceptance gates set `STEWARD_AUTH_REQUIRED=0` and clear the Supabase
variables. Only account identifiers (Supabase user id and email) are stored locally; no repository content
is ever sent to Supabase.
"""
import base64
import hashlib
import json
import os
import threading
import time
from urllib.parse import urlparse

import httpx

from .atlas_startup import use_system_trust_store

FLAG = 'STEWARD_AUTH_REQUIRED'
URL_ENV = 'STEWARD_SUPABASE_URL'
KEY_ENV = 'STEWARD_SUPABASE_PUBLISHABLE_KEY'
CACHE_SECONDS = 60
CACHE_LIMIT = 1000
TIMEOUT_SECONDS = 10
MAX_TOKEN_BYTES = 8192
# Reachable without a session: liveness and the public sign-in configuration the browser needs.
PUBLIC_PATHS = frozenset({'/api/health', '/api/auth/config'})

_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


class AuthConfigError(RuntimeError):
    """Sign-in is required but Supabase is not configured or not reachable."""


class AuthError(Exception):
    """The request carries no valid Supabase session (HTTP 401)."""


class AuthUnavailable(Exception):
    """Supabase Auth could not be asked about the session (HTTP 503)."""


def auth_required() -> bool:
    """Strict mode: True only when STEWARD_AUTH_REQUIRED is set to 1/true/yes/on."""
    return os.getenv(FLAG, '0').strip().lower() in {'1', 'true', 'yes', 'on'}


def auth_configured() -> bool:
    return bool(os.getenv(URL_ENV, '').strip() and os.getenv(KEY_ENV, '').strip())


def auth_enabled() -> bool:
    """Sign-in is offered (optional mode) or enforced (required mode)."""
    return auth_required() or auth_configured()


def mode() -> str:
    return 'required' if auth_required() else 'optional' if auth_configured() else 'off'


def config() -> tuple[str, str]:
    """Return (project URL, publishable key), validated; raise AuthConfigError naming what is wrong."""
    raw_url = os.getenv(URL_ENV, '').strip()
    key = os.getenv(KEY_ENV, '').strip()
    if not raw_url or not key:
        raise AuthConfigError(f'set {URL_ENV} and {KEY_ENV} in .env (Supabase project URL and publishable key)')
    url = raw_url.rstrip('/')
    for suffix in ('/rest/v1', '/auth/v1'):
        if url.endswith(suffix): url = url[:-len(suffix)]
    parsed = urlparse(url)
    # Plain HTTP only for a local Supabase CLI stack; any remote project must be HTTPS.
    schemes = {'https', 'http'} if parsed.hostname in {'127.0.0.1', 'localhost'} else {'https'}
    if parsed.scheme not in schemes or not parsed.hostname or parsed.username or parsed.password \
            or parsed.query or parsed.fragment or parsed.path not in {'', '/'}:
        raise AuthConfigError(f'{URL_ENV} must be the HTTPS project URL, for example https://<ref>.supabase.co')
    if key.startswith('sb_secret_') or _jwt_role(key) == 'service_role':
        raise AuthConfigError(f'{KEY_ENV} must be the publishable (browser) key, never the secret key')
    return url, key


def public_config() -> dict:
    """What the browser needs to sign in. The publishable key is designed to be public."""
    if not auth_enabled(): return {'enabled': False}
    url, key = config()
    return {'enabled': True, 'required': auth_required(), 'provider': 'supabase', 'url': url, 'publishable_key': key}


def describe() -> str:
    current = mode()
    if current == 'off': return 'Sign-in off (Supabase not configured); local single workspace.'
    if current == 'optional': return 'Supabase accounts optional: sign in to attach your account; the shared team workspace stays open without one.'
    return 'Supabase accounts required: every API request needs a valid Supabase session; one shared team workspace.'


def check_auth_ready() -> None:
    """Startup check for run.py: config is valid and Supabase Auth accepts the publishable key."""
    if not auth_enabled(): return
    url, key = config()
    use_system_trust_store()
    try:
        response = httpx.get(url + '/auth/v1/settings', headers={'apikey': key}, timeout=TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        raise AuthConfigError(f'cannot reach Supabase Auth ({type(exc).__name__})') from None
    if response.status_code != 200:
        raise AuthConfigError(f'Supabase Auth rejected the publishable key (HTTP {response.status_code})')


def bearer_token(header: str | None) -> str:
    scheme, _, token = (header or '').partition(' ')
    token = token.strip()
    if scheme.lower() != 'bearer' or not token: raise AuthError('Sign in required')
    if len(token.encode()) > MAX_TOKEN_BYTES or token.count('.') != 2: raise AuthError('Invalid session token')
    return token


def verify(token: str) -> dict:
    """Return {'id', 'email'} for a valid Supabase access token, or raise AuthError / AuthUnavailable."""
    digest = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()
    with _lock:
        cached = _cache.get(digest)
        if cached and cached[0] > now: return dict(cached[1])
        _cache.pop(digest, None)
    expires = _jwt_claims(token).get('exp')
    if isinstance(expires, (int, float)) and expires <= now: raise AuthError('Session expired; sign in again')
    url, key = config()
    use_system_trust_store()
    try:
        response = httpx.get(url + '/auth/v1/user', headers={'apikey': key, 'Authorization': 'Bearer ' + token},
                             timeout=TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        raise AuthUnavailable(f'Supabase Auth unavailable ({type(exc).__name__})') from None
    if response.status_code in {401, 403}: raise AuthError('Session expired or invalid; sign in again')
    if response.status_code != 200: raise AuthUnavailable(f'Supabase Auth returned HTTP {response.status_code}')
    try: body = response.json()
    except ValueError: raise AuthUnavailable('Supabase Auth returned an unreadable response') from None
    user_id = body.get('id') if isinstance(body, dict) else None
    if not isinstance(user_id, str) or not user_id: raise AuthUnavailable('Supabase Auth returned no user id')
    account = {'id': user_id, 'email': body.get('email') if isinstance(body.get('email'), str) else None}
    until = now + CACHE_SECONDS
    if isinstance(expires, (int, float)): until = min(until, float(expires))
    with _lock:
        if len(_cache) >= CACHE_LIMIT:
            for stale in [name for name, (when, _) in _cache.items() if when <= now] or list(_cache)[:CACHE_LIMIT // 2]:
                _cache.pop(stale, None)
        _cache[digest] = (until, account)
    _remember(account)
    return dict(account)


def clear_cache() -> None:
    with _lock: _cache.clear()


def _remember(account: dict) -> None:
    """Keep a local member row (SQLite stays authoritative) so the team list shows real accounts."""
    from . import db

    def upsert(con):
        previous = db.get_in(con, 'members', account['id']) or {}
        return db.put_in(con, 'members', {**previous, 'id': account['id'], 'email': account['email'],
                                          'provider': 'supabase', 'last_seen_at': db.now()})
    db.atomic(upsert)


def _jwt_claims(token: str) -> dict:
    """Unverified claims, used only to cap caching and reject obviously expired tokens; Supabase decides validity."""
    try:
        part = token.split('.')[1]
        value = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
        return value if isinstance(value, dict) else {}
    except (IndexError, ValueError):
        return {}


def _jwt_role(key: str) -> str | None:
    return _jwt_claims(key).get('role') if key.count('.') == 2 else None
