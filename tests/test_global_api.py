"""Public API boundaries against isolated persistent catalogue data."""
import json
from pathlib import Path

from fastapi.testclient import TestClient
from backend.main import app
from backend.global_catalogue import Catalogue, GlobalEntry


def test_catalogue_api_is_bounded_and_uses_public_store(tmp_path, monkeypatch):
    path = tmp_path / 'catalogue.sqlite3'
    monkeypatch.setenv('STEWARD_GLOBAL_CATALOGUE_PATH', str(path))
    record = json.loads((Path(__file__).resolve().parents[1] / 'integrations/global/fixtures/global-vulnerability.json').read_text())
    Catalogue(path).ingest(GlobalEntry.model_validate(record))
    client = TestClient(app)
    response = client.get('/api/global-catalogue', params={'ecosystem': 'PyPI', 'package': 'duck-tape-fixture', 'limit': 1})
    assert response.status_code == 200
    assert len(response.json()['entries']) == 1
    assert response.json()['coverage'] == 'unknown'
    assert response.json()['provider'] == 'local'
    graph = client.get('/api/global-catalogue/graph', params={'ecosystem': 'PyPI', 'package': 'duck-tape-fixture', 'limit': 1})
    assert graph.status_code == 200
    assert len(graph.json()['edges']) == 2
    assert graph.json()['coverage'] == 'unknown'
    assert client.get('/api/global-catalogue', params={'ecosystem': 'PyPI', 'package': 'duck-tape-fixture', 'limit': 101}).status_code == 422
    assert client.get('/api/global-catalogue', params={'ecosystem': 'PyPI', 'package': 'x' * 257}).status_code == 422


def test_disabled_atlas_api_does_not_connect(tmp_path, monkeypatch):
    from backend import data_platforms
    monkeypatch.delenv('STEWARD_GLOBAL_ATLAS_ENABLED', raising=False)
    monkeypatch.setattr(data_platforms, '_atlas_collection', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('connected')))
    response = TestClient(app).get('/api/global-catalogue', params={'ecosystem': 'PyPI', 'package': 'duck-tape-fixture', 'provider': 'atlas'})
    assert response.status_code == 503
    assert response.json()['detail'] == 'Public Atlas unavailable; coverage remains unknown.'

