"""Stable browser addresses and explicit managed-branch application."""
import pytest
from fastapi.testclient import TestClient
from backend import db, main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init()
    return TestClient(main.app)


def repo(identity, **values):
    return db.put('repositories', {'id': identity, 'name': 'Uploaded code',
        'snapshot': 's1', 'index': {'files': [], 'edges': []}, **values})


def test_addresses_are_unique_safe_and_stable(client):
    origin = {'source': 'https://github.com/Acme/Orders.git'}
    repo('one', origin=origin)
    repo('two', origin=origin)
    repo('three', is_sample=True)
    repo('four', name='../../% unsafe')
    addresses = {r['id']: (r['route_owner'], r['route_name']) for r in client.get('/api/repositories').json()}
    assert addresses['one'] == ('acme', 'orders')
    assert addresses['three'] == ('demo', 'parcel')
    assert len(set(addresses.values())) == 4
    assert all(value and value not in ('.', '..') and '/' not in value and '%' not in value for pair in addresses.values() for value in pair)
    db.mutate('repositories', 'one', lambda r: {**r, 'name': 'Renamed', 'origin': {'source': 'https://github.com/Elsewhere/New'}})
    assert addresses == {r['id']: (r['route_owner'], r['route_name']) for r in client.get('/api/repositories').json()}
    assert client.get('/api/repositories/one').json()['route_name'] == 'orders'


def test_reserved_owner_does_not_shadow_app_routes(client):
    repo('one', origin={'source': 'https://github.com/settings/config'})
    assert client.get('/api/repositories').json()[0]['route_owner'] != 'settings'


def test_reading_existing_addresses_does_not_mutate_records(client):
    repo('one')
    client.get('/api/repositories')
    version = db.get('repositories', 'one')['record_version']
    client.get('/api/repositories')
    client.get('/api/repositories/one')
    assert db.get('repositories', 'one')['record_version'] == version


def test_concurrent_address_backfill_is_idempotent(client):
    from concurrent.futures import ThreadPoolExecutor
    from backend.routing import ensure_routes
    for identity in range(8):
        repo(str(identity), name='Same upload')
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: ensure_routes(), range(8)))
    records = db.list_records('repositories')
    assert len({(r['route_owner'], r['route_name']) for r in records}) == 8
    assert all(r['record_version'] == 2 for r in records)


def test_apply_rejects_wrong_target_before_side_effects(client, monkeypatch):
    from backend import integration
    repo('one')
    db.put('jobs', {'id': 'job', 'repo_id': 'one', 'state': 'Ready for review'})
    calls = []
    monkeypatch.setattr(integration, 'apply', lambda *args: calls.append(args) or {'state': 'Completed'})
    assert client.get('/api/jobs/job').json()['managed_target_branch'] == 'steward/integration'
    assert client.post('/api/jobs/job/apply', json={'revision_id': 'rev', 'target_branch': 'main'}).status_code == 409
    assert not calls
    assert client.post('/api/jobs/job/apply', json={'revision_id': 'rev', 'target_branch': 'steward/integration'}).status_code == 200
    assert len(calls) == 1


@pytest.mark.parametrize('path', ['/', '/dashboard', '/acme/orders', '/acme/orders/codebase', '/acme/orders/jobs/123', '/settings', '/screens'])
def test_clean_url_reload_serves_app(client, tmp_path, monkeypatch, path):
    (tmp_path / 'index.html').write_text('<title>Duck Tape</title>', encoding='utf-8')
    monkeypatch.setattr(main, 'DIST', tmp_path)
    response = client.get(path)
    assert response.status_code == 200 and '<title>Duck Tape</title>' in response.text
    assert client.get('/api/not-a-real-endpoint').status_code == 404
