import json

import pytest
from fastapi.testclient import TestClient

from backend import db, main

from tests.test_chunk_retrieval import register

LOCK = json.dumps({'lockfileVersion': 3, 'packages': {'': {'name': 'shop'}, 'node_modules/lodash': {'version': '4.17.20'}}}, indent=2)


def entry(**changes):
    return {'id': 'GHSA-35jh-r3h4-6jhm', 'kind': 'vulnerability', 'revision': 1, 'content_hash': 'c' * 64,
            'source_url': 'https://osv.dev/vulnerability/GHSA-35jh-r3h4-6jhm', 'summary': 'Command injection in lodash template',
            'status': 'active', 'affected': [{'ecosystem': 'npm', 'package': 'lodash', 'ranges': [{'introduced': '0', 'fixed': '4.17.21'}]}],
            **changes}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path / 'store')
    monkeypatch.delenv('STEWARD_EMBEDDING_MODEL', raising=False)
    monkeypatch.delenv('STEWARD_ATLAS_RETRIEVAL', raising=False)
    db.init()
    snapshot = register(tmp_path, 'repo', {'package-lock.json': LOCK, 'index.js': 'const _ = require("lodash")\n'})
    return TestClient(main.app), snapshot


def test_profile_and_match_routes_are_local_and_snapshot_bound(client):
    api, snapshot = client
    profile = api.get('/api/repositories/repo/profile').json()
    assert profile['snapshot_id'] == snapshot['id'] and profile['packages'][0]['name'] == 'lodash'
    response = api.post('/api/repositories/repo/applicability', json={'entries': [entry(), entry(id='OTHER', affected=[{'ecosystem': 'npm', 'package': 'react', 'versions': ['18.0.0']}])]})
    assert [item['status'] for item in response.json()['matches']] == ['matched', 'not_matched']
    assert db.list_records('jobs') == []
    assert api.post('/api/repositories/repo/applicability', json={'entries': [entry()], 'snapshot_id': 'old'}).status_code == 409
    assert api.post('/api/repositories/repo/applicability', json={'entries': [entry(kind='rumour')]}).status_code == 422


def test_intake_dedupes_unchanged_rescans_and_refuses_withdrawn_or_unmatched(client):
    api, _ = client
    first = api.post('/api/repositories/repo/applicability/intake', json={'entry': entry(retrieved_at='10:00')}).json()
    again = api.post('/api/repositories/repo/applicability/intake', json={'entry': entry(retrieved_at='16:00')}).json()
    job = first['jobs'][0]
    assert (job['source'], job['type'], job['source_key']) == ('security', 'security', 'GHSA-35jh-r3h4-6jhm:lodash:4.17.20')
    assert again['jobs'][0]['id'] == job['id'] and len(db.list_records('jobs')) == 1
    assert again['invalidated'] == []
    revised = api.post('/api/repositories/repo/applicability/intake', json={'entry': entry(revision=2, content_hash='d' * 64)}).json()
    assert revised['jobs'][0]['id'] != job['id'] and revised['invalidated'] == [job['id']]
    old = db.get('jobs', job['id'])
    assert old['status'] == 'Failed' and old['stale'] and old['approval'] is None and 'revision 2' in old['error']
    assert db.get('jobs', revised['jobs'][0]['id'])['status'] == 'Awaiting approval'
    withdrawn = api.post('/api/repositories/repo/applicability/intake', json={'entry': entry(revision=3, content_hash='e' * 64, status='withdrawn')})
    assert withdrawn.status_code == 200 and withdrawn.json()['jobs'] == []
    assert withdrawn.json()['invalidated'] == [revised['jobs'][0]['id']] and 'withdrawn' in withdrawn.json()['detail']
    unrelated = entry(affected=[{'ecosystem': 'npm', 'package': 'react', 'versions': ['18.0.0']}])
    assert api.post('/api/repositories/repo/applicability/intake', json={'entry': unrelated}).json()['jobs'] == []
    assert api.post('/api/repositories/repo/applicability/intake', json={'entry': entry(revision=0)}).status_code == 422


def test_rejected_job_on_unchanged_evidence_stays_rejected(client):
    api, _ = client
    job = api.post('/api/repositories/repo/applicability/intake', json={'entry': entry()}).json()['jobs'][0]
    db.put('jobs', {**db.get('jobs', job['id']), 'status': 'Rejected'})
    again = api.post('/api/repositories/repo/applicability/intake', json={'entry': entry(retrieved_at='later')}).json()
    assert again['jobs'][0]['id'] == job['id'] and db.get('jobs', job['id'])['status'] == 'Rejected'
    api.post('/api/repositories/repo/applicability/intake', json={'entry': entry(status='withdrawn')})
    assert db.get('jobs', job['id'])['status'] == 'Rejected'
