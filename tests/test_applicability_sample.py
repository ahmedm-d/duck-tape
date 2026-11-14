"""End-to-end catalogue fixture -> Parcel sample profile -> match -> existing intake."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import db, main, sample

CATALOGUE = json.loads((Path(__file__).parent / 'fixtures' / 'catalogue' / 'sample_entries.json').read_text(encoding='utf-8'))


@pytest.fixture
def parcel(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path / 'store')
    monkeypatch.delenv('STEWARD_EMBEDDING_MODEL', raising=False)
    monkeypatch.delenv('STEWARD_ATLAS_RETRIEVAL', raising=False)
    db.init()
    return TestClient(main.app), sample.seed_sample()


def test_fixture_catalogue_is_labelled():
    assert 'LOCAL FIXTURE' in CATALOGUE['label']
    assert all(entry['source_url'].startswith('fixture:') for entry in CATALOGUE['entries'])


def test_sample_matches_unmatched_and_unknown_with_lockfile_citation(parcel):
    api, repo = parcel
    response = api.post(f'/api/repositories/{repo["id"]}/applicability', json={'entries': CATALOGUE['entries']}).json()
    assert [item['status'] for item in response['matches']] == ['matched', 'not_matched', 'unknown']
    matched = response['matches'][0]
    assert matched['snapshot_id'] == repo['source_snapshot_id']
    assert matched['citations'] == [{'path': 'requirements.lock', 'start_line': 2, 'end_line': 2,
                                     'hash': next(f['hash'] for f in repo['index']['files'] if f['path'] == 'requirements.lock')}]
    assert any('1.0.1' in item for item in matched['acceptance_criteria'])


def test_sample_match_becomes_one_approval_gated_security_job(parcel):
    api, repo = parcel
    before = {job['id'] for job in db.list_records('jobs')}
    body = {'entry': CATALOGUE['entries'][0], 'snapshot_id': repo['source_snapshot_id']}
    first = api.post(f'/api/repositories/{repo["id"]}/applicability/intake', json=body).json()
    again = api.post(f'/api/repositories/{repo["id"]}/applicability/intake', json=body).json()
    job = first['jobs'][0]
    assert job['id'] not in before and again['jobs'][0]['id'] == job['id']
    assert (job['source'], job['type'], job['status']) == ('security', 'security', 'Awaiting approval')
    assert 'requirements.lock' in job['affected_areas']
    assert {item['id'] for item in db.list_records('jobs')} - before == {job['id']}
    sample_security = next(item for item in db.list_records('jobs') if item.get('scenario') == 'security')
    assert sample_security['status'] == 'Awaiting approval'
    stored = db.get('jobs', job['id'])
    assert stored['catalogue']['entry_id'] == 'DUCKTAPE-FIXTURE-CAT-001' and stored['catalogue']['entry_revision'] == 1
    assert stored['catalogue']['snapshot_id'] == repo['source_snapshot_id']
    assert stored['applicability']['citations'][0]['path'] == 'requirements.lock'
    assert stored['acceptance_criteria'] == stored['applicability']['acceptance_criteria']


def test_lockfile_change_invalidates_job_and_old_citation(parcel):
    api, repo = parcel
    entry = CATALOGUE['entries'][0]
    job = api.post(f'/api/repositories/{repo["id"]}/applicability/intake', json={'entry': entry}).json()['jobs'][0]
    lock = Path(repo['root']) / 'requirements.lock'
    lock.write_text(lock.read_text(encoding='utf-8').replace('fixture-parser==1.0.0', 'fixture-parser==1.0.1'), encoding='utf-8', newline='')
    main.refresh_repo(repo['id'])
    current = db.get('repositories', repo['id'])
    assert current['source_snapshot_id'] != repo['source_snapshot_id']
    stale = db.get('jobs', job['id'])
    assert stale['status'] == 'Failed' and stale['stale'] and stale['approval'] is None
    old = api.post(f'/api/repositories/{repo["id"]}/applicability', json={'entries': [entry], 'snapshot_id': repo['source_snapshot_id']})
    assert old.status_code == 409
    now = api.post(f'/api/repositories/{repo["id"]}/applicability', json={'entries': [entry]}).json()['matches'][0]
    assert now['status'] == 'not_matched' and now['snapshot_id'] == current['source_snapshot_id'] and not now['citations']
    assert api.post(f'/api/repositories/{repo["id"]}/applicability/intake', json={'entry': entry}).json()['jobs'] == []


def test_deleted_lockfile_leaves_no_current_dependency_evidence(parcel):
    api, repo = parcel
    (Path(repo['root']) / 'requirements.lock').unlink()
    main.refresh_repo(repo['id'])
    profile = api.get(f'/api/repositories/{repo["id"]}/profile').json()
    assert all(item['path'] != 'requirements.lock' for item in profile['packages'])
    now = api.post(f'/api/repositories/{repo["id"]}/applicability', json={'entries': [CATALOGUE['entries'][0]]}).json()['matches'][0]
    assert now['status'] in {'not_matched', 'unknown'} and not now['citations']
