"""Atlas-required defaults: retrieval and catalogue lookups use Atlas unless explicitly told otherwise.

Fixture/mocked transport only: Atlas search and `query_atlas` are faked; no cluster is contacted.
"""
import pytest
from fastapi.testclient import TestClient

from backend import data_platforms, db, global_pipeline, main, retrieval
from backend.global_catalogue import Catalogue, query_result

from tests.test_applicability_catalogue import fresh
from tests.test_atlas_retrieval import FILES, fake_search
from tests.test_chunk_retrieval import register

APP = 'import requests\n\n\ndef fetch(url):\n    return requests.get(url, timeout=5)\n'
PARAMS = {'ecosystem': 'PyPI', 'package': 'requests', 'limit': 5}
PATHS = ['/api/global-catalogue', '/api/global-catalogue/graph']


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path / 'store')
    for name in ('STEWARD_EMBEDDING_MODEL', 'STEWARD_ATLAS_RETRIEVAL', 'STEWARD_GRAPH_ENGINE'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('STEWARD_GLOBAL_CATALOGUE_PATH', str(tmp_path / 'catalogue.sqlite3'))
    db.init()
    return tmp_path


def required(monkeypatch, value='1'):
    monkeypatch.setenv('STEWARD_ATLAS_REQUIRED', value)


def unavailable(*args, **kwargs):
    raise data_platforms.DataPlatformError('Atlas vector index is not ready for queries')


def atlas_fails(*args, **kwargs):
    raise global_pipeline.Error('Public Atlas query failed; coverage remains unknown')


def atlas_answer(calls):
    def answer(ecosystem, package, limit=20):
        calls.append((ecosystem, package, limit))
        return {**query_result([fresh()], ecosystem, package, limit), 'provider': 'atlas'}
    return answer


# retrieval

def test_required_mode_uses_atlas_candidates_by_default(store, monkeypatch):
    snapshot = register(store, 'repo', FILES)
    calls = []
    monkeypatch.setattr(data_platforms, 'atlas_search', fake_search(calls, [('pricing.py', 0.91)]))
    required(monkeypatch)
    result = retrieval.retrieve('repo', snapshot['id'], 'how is the total computed', 4000, graph=False)
    assert calls and 'atlas' in result['mode']
    assert all(item['path'] != 'secret.py' for item in result['items'])
    assert any(item.startswith('Atlas candidates: 1 validated') for item in result['missing'])


def test_explicit_atlas_false_disables_even_when_required(store, monkeypatch):
    snapshot = register(store, 'repo', FILES)
    calls = []
    monkeypatch.setattr(data_platforms, 'atlas_search', fake_search(calls, [('pricing.py', 0.91)]))
    required(monkeypatch)
    result = retrieval.retrieve('repo', snapshot['id'], 'shipping handler', 4000, atlas=False)
    assert calls == [] and 'atlas' not in result['mode']
    assert not any('Atlas' in item for item in result['missing'])


def test_required_mode_failure_note_says_required_but_unavailable(store, monkeypatch):
    snapshot = register(store, 'repo', FILES)
    monkeypatch.setattr(data_platforms, 'atlas_search', unavailable)
    required(monkeypatch)
    result = retrieval.retrieve('repo', snapshot['id'], 'quote handler', 4000)
    assert 'atlas' not in result['mode'] and result['items']
    notes = [item for item in result['missing'] if 'Atlas' in item]
    assert len(notes) == 1 and notes[0].startswith('Atlas was required but unavailable') and 'not ready' in notes[0]
    assert 'local keyword/graph fallback' in notes[0]


def test_explicit_atlas_true_failure_keeps_ordinary_note_when_required(store, monkeypatch):
    snapshot = register(store, 'repo', FILES)
    monkeypatch.setattr(data_platforms, 'atlas_search', unavailable)
    required(monkeypatch)
    result = retrieval.retrieve('repo', snapshot['id'], 'quote handler', 4000, atlas=True)
    assert any(item.startswith('Atlas candidates unavailable') for item in result['missing'])


def test_not_required_keeps_flag_only_behaviour(store, monkeypatch):
    snapshot = register(store, 'repo', FILES)
    calls = []
    monkeypatch.setattr(data_platforms, 'atlas_search', fake_search(calls, [('pricing.py', 0.91)]))
    required(monkeypatch, '0')
    result = retrieval.retrieve('repo', snapshot['id'], 'how is the total computed', 4000, graph=False)
    assert calls == [] and 'atlas' not in result['mode']
    monkeypatch.setenv('STEWARD_ATLAS_RETRIEVAL', '1')
    result = retrieval.retrieve('repo', snapshot['id'], 'how is the total computed', 4000, graph=False)
    assert calls and 'atlas' in result['mode']


# catalogue routes

@pytest.fixture
def api(store):
    register(store, 'demo', {'requirements.txt': 'requests==2.31.0\n', 'app.py': APP})
    Catalogue(store / 'catalogue.sqlite3').ingest(fresh())
    return TestClient(main.app)


def applicability(api, **body):
    return api.post('/api/repositories/demo/applicability/catalogue', json=body)


def test_applicability_catalogue_defaults_to_atlas_when_required(api, monkeypatch):
    calls = []
    monkeypatch.setattr(global_pipeline, 'query_atlas', atlas_answer(calls))
    required(monkeypatch)
    body = applicability(api).json()
    assert calls == [('PyPI', 'requests', 25)] and body['provider'] == 'atlas'
    assert body['coverage'] == 'unknown'


def test_applicability_catalogue_defaults_to_local_when_not_required(api, monkeypatch):
    calls = []
    monkeypatch.setattr(global_pipeline, 'query_atlas', atlas_answer(calls))
    body = applicability(api).json()
    assert calls == [] and body['provider'] == 'local'


def test_applicability_catalogue_explicit_local_is_honoured_when_required(api, monkeypatch):
    calls = []
    monkeypatch.setattr(global_pipeline, 'query_atlas', atlas_answer(calls))
    required(monkeypatch)
    body = applicability(api, provider='local').json()
    assert calls == [] and body['provider'] == 'local'
    assert applicability(api, provider='remote').status_code == 422


def test_applicability_catalogue_atlas_failure_is_503_not_local_fallback(api, monkeypatch):
    monkeypatch.setattr(global_pipeline, 'query_atlas', atlas_fails)
    required(monkeypatch)
    response = applicability(api)
    assert response.status_code == 503 and 'coverage remains unknown' in response.json()['detail']
    assert db.list_records('jobs') == []


@pytest.mark.parametrize('path', PATHS)
def test_global_catalogue_routes_default_to_atlas_when_required(api, monkeypatch, path):
    calls = []
    monkeypatch.setattr(global_pipeline, 'query_atlas', atlas_answer(calls))
    required(monkeypatch)
    response = api.get(path, params=PARAMS)
    assert response.status_code == 200 and calls == [('PyPI', 'requests', 5)]
    if path == PATHS[0]:
        assert response.json()['provider'] == 'atlas'


@pytest.mark.parametrize('path', PATHS)
def test_global_catalogue_routes_default_to_local_when_not_required(api, monkeypatch, path):
    calls = []
    monkeypatch.setattr(global_pipeline, 'query_atlas', atlas_answer(calls))
    response = api.get(path, params=PARAMS)
    assert response.status_code == 200 and calls == []
    if path == PATHS[0]:
        assert response.json()['provider'] == 'local'


@pytest.mark.parametrize('path', PATHS)
def test_global_catalogue_explicit_local_and_atlas_failure(api, monkeypatch, path):
    calls = []
    monkeypatch.setattr(global_pipeline, 'query_atlas', atlas_answer(calls))
    required(monkeypatch)
    assert api.get(path, params={**PARAMS, 'provider': 'local'}).status_code == 200 and calls == []
    monkeypatch.setattr(global_pipeline, 'query_atlas', atlas_fails)
    response = api.get(path, params=PARAMS)
    assert response.status_code == 503 and 'coverage remains unknown' in response.json()['detail']
