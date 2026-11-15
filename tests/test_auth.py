"""Supabase sign-in gate: mocked Supabase Auth transport only; no live project is contacted."""
import base64
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import auth, db, main

URL = 'https://example-ref.supabase.co'
KEY = 'sb_publishable_test-key'


def token(exp=None, role='authenticated'):
    def part(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b'=').decode()
    return '.'.join([part({'alg': 'ES256'}), part({'sub': 'user-1', 'role': role, 'exp': exp or int(time.time()) + 3600}), 'signature'])


class Supabase:
    """Records calls to the mocked Supabase Auth endpoints."""

    def __init__(self, status=200, body=None, error=None):
        self.calls, self.status, self.error = [], status, error
        self.body = body if body is not None else {'id': 'user-1', 'email': 'dev@example.com', 'aud': 'authenticated'}

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, dict(headers or {})))
        if self.error: raise self.error
        return httpx.Response(self.status, json=self.body, request=httpx.Request('GET', url))


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path / 'store')
    db.init()
    auth.clear_cache()
    yield TestClient(main.app)
    auth.clear_cache()


@pytest.fixture
def required(monkeypatch):
    monkeypatch.setenv('STEWARD_AUTH_REQUIRED', '1')
    monkeypatch.setenv('STEWARD_SUPABASE_URL', URL + '/rest/v1/')
    monkeypatch.setenv('STEWARD_SUPABASE_PUBLISHABLE_KEY', KEY)
    supabase = Supabase()
    monkeypatch.setattr(auth.httpx, 'get', supabase.get)
    return supabase


@pytest.fixture
def optional(monkeypatch):
    """Supabase configured, sign-in not compulsory: the default once the project URL and key are set."""
    monkeypatch.delenv('STEWARD_AUTH_REQUIRED', raising=False)
    monkeypatch.setenv('STEWARD_SUPABASE_URL', URL)
    monkeypatch.setenv('STEWARD_SUPABASE_PUBLISHABLE_KEY', KEY)
    supabase = Supabase()
    monkeypatch.setattr(auth.httpx, 'get', supabase.get)
    return supabase


def test_off_without_supabase_configuration(api, monkeypatch):
    monkeypatch.delenv('STEWARD_AUTH_REQUIRED', raising=False)
    assert auth.mode() == 'off' and not auth.auth_enabled()
    assert api.get('/api/repositories').status_code == 200
    assert api.get('/api/auth/config').json() == {'enabled': False}
    assert 'off' in api.get('/api/settings').json()['authentication']


def test_optional_mode_keeps_the_workspace_open_without_an_account(api, optional):
    assert auth.mode() == 'optional'
    assert api.get('/api/repositories').status_code == 200
    assert api.get('/api/auth/me').json() == {'enabled': True, 'required': False, 'account': None}
    assert api.get('/api/auth/config').json()['required'] is False
    assert 'optional' in api.get('/api/settings').json()['authentication']
    assert optional.calls == []  # anonymous requests never contact Supabase


def test_optional_mode_attaches_a_valid_account(api, optional):
    me = api.get('/api/auth/me', headers={'Authorization': 'Bearer ' + token()}).json()
    assert me['account'] == {'id': 'user-1', 'email': 'dev@example.com'}
    assert db.get('members', 'user-1')['provider'] == 'supabase'


@pytest.mark.parametrize('status,error', [(401, None), (500, None), (200, httpx.ConnectError('down'))])
def test_optional_mode_treats_bad_or_unverifiable_tokens_as_anonymous(api, optional, status, error):
    optional.status, optional.error = status, error
    response = api.get('/api/auth/me', headers={'Authorization': 'Bearer ' + token()})
    assert response.status_code == 200 and response.json()['account'] is None
    assert api.get('/api/jobs', headers={'Authorization': 'Basic nope'}).status_code == 200


def test_required_mode_rejects_requests_without_a_session(api, required):
    for headers in ({}, {'Authorization': 'Basic abc'}, {'Authorization': 'Bearer not-a-jwt'}):
        response = api.get('/api/repositories', headers=headers)
        assert response.status_code == 401 and response.headers['www-authenticate'] == 'Bearer'
    assert required.calls == []


def test_public_paths_and_frontend_stay_reachable(api, required):
    assert api.get('/api/health').status_code == 200
    config = api.get('/api/auth/config').json()
    # The /rest/v1 suffix users copy from the dashboard is stripped; only the publishable key is exposed.
    assert config == {'enabled': True, 'required': True, 'provider': 'supabase', 'url': URL, 'publishable_key': KEY}
    assert api.get('/login').status_code == 200
    assert required.calls == []


def test_valid_session_passes_records_member_and_is_cached(api, required):
    headers = {'Authorization': 'Bearer ' + token()}
    assert api.get('/api/repositories', headers=headers).status_code == 200
    me = api.get('/api/auth/me', headers=headers).json()
    assert me == {'enabled': True, 'required': True, 'account': {'id': 'user-1', 'email': 'dev@example.com'}}
    assert len(required.calls) == 1
    url, sent = required.calls[0]
    assert url == URL + '/auth/v1/user'
    assert sent['apikey'] == KEY and sent['Authorization'] == headers['Authorization']
    member = db.get('members', 'user-1')
    assert member['email'] == 'dev@example.com' and member['provider'] == 'supabase'
    assert 'Supabase accounts required' in api.get('/api/settings', headers=headers).json()['authentication']


def test_rejected_or_expired_sessions_get_401(api, required):
    required.status = 401
    assert api.get('/api/jobs', headers={'Authorization': 'Bearer ' + token()}).status_code == 401
    calls = len(required.calls)
    expired = api.get('/api/jobs', headers={'Authorization': 'Bearer ' + token(exp=int(time.time()) - 5)})
    assert expired.status_code == 401 and len(required.calls) == calls  # rejected without asking Supabase


def test_rejection_is_not_cached(api, required):
    value = token()
    required.status = 401
    assert api.get('/api/jobs', headers={'Authorization': 'Bearer ' + value}).status_code == 401
    required.status = 200
    assert api.get('/api/jobs', headers={'Authorization': 'Bearer ' + value}).status_code == 200


def test_supabase_outage_is_503_not_access(api, required):
    required.error = httpx.ConnectError('down')
    response = api.get('/api/jobs', headers={'Authorization': 'Bearer ' + token()})
    assert response.status_code == 503 and 'unavailable' in response.json()['detail']
    required.error, required.status = None, 500
    assert api.get('/api/jobs', headers={'Authorization': 'Bearer ' + token()}).status_code == 503


@pytest.mark.parametrize('url,key,message', [
    ('', KEY, 'STEWARD_SUPABASE_URL'),
    (URL, '', 'STEWARD_SUPABASE_PUBLISHABLE_KEY'),
    ('http://example-ref.supabase.co', KEY, 'HTTPS'),
    ('https://user:pass@example-ref.supabase.co', KEY, 'HTTPS'),
    (URL, 'sb_secret_abc', 'never the secret key'),
    (URL, token(role='service_role'), 'never the secret key'),
])
def test_config_rejects_unsafe_values(monkeypatch, url, key, message):
    monkeypatch.setenv('STEWARD_SUPABASE_URL', url)
    monkeypatch.setenv('STEWARD_SUPABASE_PUBLISHABLE_KEY', key)
    with pytest.raises(auth.AuthConfigError, match=message):
        auth.config()


def test_local_supabase_cli_may_use_http(monkeypatch):
    monkeypatch.setenv('STEWARD_SUPABASE_URL', 'http://127.0.0.1:54321')
    monkeypatch.setenv('STEWARD_SUPABASE_PUBLISHABLE_KEY', KEY)
    assert auth.config() == ('http://127.0.0.1:54321', KEY)


def test_startup_check(required, monkeypatch):
    auth.check_auth_ready()
    assert required.calls[-1] == (URL + '/auth/v1/settings', {'apikey': KEY})
    required.status = 401
    with pytest.raises(auth.AuthConfigError, match='rejected'):
        auth.check_auth_ready()
    monkeypatch.setenv('STEWARD_AUTH_REQUIRED', '0')
    monkeypatch.delenv('STEWARD_SUPABASE_URL')
    required.error = AssertionError('must not be called when sign-in is off')
    auth.check_auth_ready()


def test_run_py_only_refuses_to_start_in_required_mode():
    text = (main.Path(main.__file__).resolve().parents[1] / 'run.py').read_text(encoding='utf-8')
    assert 'check_auth_ready()' in text and 'if auth.auth_required(): raise SystemExit' in text
    assert "setdefault('STEWARD_AUTH_REQUIRED'" not in text
