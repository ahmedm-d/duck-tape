"""The Atlas startup gate is exercised with mocks only: no network, no real cluster."""
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from backend import atlas_startup, data_platforms

REAL_TRUST = atlas_startup.use_system_trust_store
SECRET = 'never-show-this'
URI = 'mongodb+srv://user:' + SECRET + '@cluster.mongodb.net/'


@pytest.fixture(autouse=True)
def required(monkeypatch):
    for key in ('ENABLED', 'URI', 'DIMENSIONS'):
        monkeypatch.delenv('STEWARD_ATLAS_' + key, raising=False)
    monkeypatch.setenv('STEWARD_ATLAS_REQUIRED', '1')
    monkeypatch.setattr(atlas_startup, 'use_system_trust_store', lambda: False)


def configure(monkeypatch):
    monkeypatch.setenv('STEWARD_ATLAS_ENABLED', '1')
    monkeypatch.setenv('STEWARD_ATLAS_URI', URI)
    monkeypatch.setenv('STEWARD_ATLAS_DIMENSIONS', '3')


def fake_collection(monkeypatch, pings, fail=None):
    class Database:
        def command(self, name):
            pings.append(name)
            if fail:
                raise fail
            return {'ok': 1}

    class Collection:
        database = Database()

    @contextmanager
    def opened(config):
        yield Collection()

    monkeypatch.setattr(data_platforms, '_atlas_collection', opened)


def message(exc):
    text = str(exc.value)
    assert SECRET not in text and 'cluster.mongodb.net' not in text
    return text


def test_not_enabled_is_a_clear_error(monkeypatch):
    with pytest.raises(atlas_startup.AtlasStartupError) as exc:
        atlas_startup.check_atlas_ready()
    text = message(exc)
    assert 'STEWARD_ATLAS_ENABLED=1' in text and 'STEWARD_ATLAS_REQUIRED=0' in text and 'tests and CI' in text


def test_missing_configuration_is_an_error_without_secrets(monkeypatch):
    monkeypatch.setenv('STEWARD_ATLAS_ENABLED', '1')
    monkeypatch.setenv('STEWARD_ATLAS_URI', 'mongodb+srv://user:' + SECRET + '@evil.example.com/')
    with pytest.raises(atlas_startup.AtlasStartupError) as exc:
        atlas_startup.check_atlas_ready()
    text = message(exc)
    assert 'STEWARD_ATLAS_URI' in text and 'STEWARD_ATLAS_DIMENSIONS' in text and 'evil.example.com' not in text


def test_unset_uri_is_an_error(monkeypatch):
    monkeypatch.setenv('STEWARD_ATLAS_ENABLED', '1')
    with pytest.raises(atlas_startup.AtlasStartupError, match='incomplete or invalid'):
        atlas_startup.check_atlas_ready()


def test_missing_pymongo_is_an_error(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setitem(sys.modules, 'pymongo', None)
    pings = []
    fake_collection(monkeypatch, pings)
    with pytest.raises(atlas_startup.AtlasStartupError, match='pymongo is not installed') as exc:
        atlas_startup.check_atlas_ready()
    message(exc)
    assert pings == []


def test_ping_failure_is_an_error_without_driver_detail(monkeypatch):
    configure(monkeypatch)
    pytest.importorskip('pymongo')
    fake_collection(monkeypatch, [], fail=RuntimeError('auth failed for ' + URI))
    with pytest.raises(atlas_startup.AtlasStartupError, match='connectivity check failed') as exc:
        atlas_startup.check_atlas_ready()
    message(exc)
    assert exc.value.__cause__ is None


def test_ping_success_is_ok(monkeypatch):
    configure(monkeypatch)
    pytest.importorskip('pymongo')
    pings = []
    fake_collection(monkeypatch, pings)
    assert atlas_startup.check_atlas_ready() is None
    assert pings == ['ping']


def test_not_required_skips_the_check(monkeypatch):
    monkeypatch.setenv('STEWARD_ATLAS_REQUIRED', '0')
    called = []
    monkeypatch.setattr(data_platforms, '_enabled', lambda name: called.append(name) or False)
    assert atlas_startup.check_atlas_ready() is None
    assert called == []


def test_system_trust_store_is_optional(monkeypatch):
    monkeypatch.setattr(atlas_startup, 'use_system_trust_store', REAL_TRUST)
    monkeypatch.setattr(atlas_startup, '_TRUSTSTORE_INJECTED', False)
    monkeypatch.setitem(sys.modules, 'truststore', None)
    assert atlas_startup.use_system_trust_store() is False
    injected = []
    monkeypatch.setitem(sys.modules, 'truststore', type('T', (), {'inject_into_ssl': staticmethod(lambda: injected.append(1))}))
    assert atlas_startup.use_system_trust_store() is True and injected == [1]
    # Idempotent: injecting again after pymongo is loaded recurses, so later calls are no-ops.
    assert atlas_startup.use_system_trust_store() is True and injected == [1]


def test_importing_the_app_does_not_run_the_check():
    root = Path(__file__).resolve().parents[1]
    code = ('import sys\nfrom backend import atlas_startup\n'
            'def boom(): raise SystemExit("check ran on import")\n'
            'atlas_startup.check_atlas_ready = boom\n'
            'import backend.main\nprint("imported")\n')
    env = {'STEWARD_ATLAS_REQUIRED': '1', 'PYTHONPATH': str(root / 'vendor-current'), 'STEWARD_DATA_DIR': str(root / 'data' / 'tests' / 'import-check')}
    import os
    result = subprocess.run([sys.executable, '-c', code], cwd=root, env={**os.environ, **env},
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0 and 'imported' in result.stdout, result.stderr[-500:]
    assert 'check_atlas_ready' not in (root / 'backend' / 'main.py').read_text(encoding='utf-8')
