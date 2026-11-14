"""Real local graphs, catalogue and queue; external OSV/Atlas transports are isolated."""
import pytest

from backend import db, main, operations, signals
from backend.contracts import CancellationToken, CancelledError
from tests.test_applicability_catalogue import env, fresh


def test_combined_graph_links_real_manifest_to_public_advisory(env):
    api, snapshot, catalogue, _ = env
    catalogue.ingest(fresh())
    response = api.post('/api/repositories/demo/knowledge-map', json={'engine': 'builtin'})
    assert response.status_code == 200
    body = response.json()
    assert body['snapshot_id'] == snapshot['id']
    assert any(n['path'] == 'app.py' for n in body['repository']['nodes'])
    public_ids = {n['id'] for g in body['public'] for n in g['nodes']}
    local_ids = {n['id'] for n in body['repository']['nodes']}
    assert body['links']
    for link in body['links']:
        assert link['source'] in local_ids and link['target'] in public_ids
        assert link['evidence']['path'] == 'requirements.txt'
        assert link['exploitability'] == 'unknown'
    assert not db.list_records('jobs')
    assert api.post('/api/repositories/demo/knowledge-map', json={'snapshot_id': 'old'}).status_code == 409


def test_catalogue_scan_creates_bound_job_and_rescan_preserves_rejection(env, monkeypatch):
    api, _, catalogue, _ = env
    catalogue.ingest(fresh())
    monkeypatch.setattr(signals, 'query_osv', lambda *a, **kw: pytest.fail('Catalogue mode must not query OSV separately'))
    config = api.patch('/api/repositories/demo', json={'security_source': 'catalogue_local', 'policies': {'security': 'approve_first'}})
    assert config.status_code == 200 and config.json()['security_source'] == 'catalogue_local'
    operation = api.post('/api/repositories/demo/scan').json()
    result = operations.advance(operation['id'])
    assert result['status'] == 'complete', result
    [job] = db.list_records('jobs')
    assert job['status'] == 'Awaiting approval' and job['catalogue']['entry_id'] == 'CVE-2024-35195'
    assert job['applicability']['citations'][0]['path'] == 'requirements.txt'
    assert api.post(f'/api/jobs/{job["id"]}/reject', json={}).status_code == 200
    main.run_scan('demo')
    assert len(db.list_records('jobs')) == 1
    assert db.get('jobs', job['id'])['status'] == 'Rejected'
    assert db.get('repositories', 'demo')['scan_report']['catalogue']['coverage'] == 'unknown'


def test_catalogue_scan_automatic_policy_and_withdrawal(env, monkeypatch):
    api, _, catalogue, _ = env
    stored = catalogue.ingest(fresh())
    api.patch('/api/repositories/demo', json={'security_source': 'catalogue_local', 'policies': {'security': 'automatic'}})
    main.run_scan('demo')
    [job] = db.list_records('jobs')
    assert job['status'] == 'Queued' and job['catalogue']
    catalogue.ingest(fresh(withdrawn_at='2026-11-14T00:00:00Z', source_content_hash='b' * 64))
    main.run_scan('demo')
    assert db.get('jobs', job['id'])['status'] == 'Failed'
    assert db.get('jobs', job['id'])['approval'] is None
    assert api.post(f'/api/jobs/{job["id"]}/retry', json={}).status_code == 409


def test_catalogue_scan_cancelled_before_intake(env, monkeypatch):
    api, _, catalogue, _ = env
    catalogue.ingest(fresh())
    api.patch('/api/repositories/demo', json={'security_source': 'catalogue_local'})
    token = CancellationToken()
    original = main._catalogue_assessment
    def cancelled(*args, **kwargs):
        result = original(*args, **kwargs)
        token.cancel()
        return result
    monkeypatch.setattr(main, '_catalogue_assessment', cancelled)
    with pytest.raises(CancelledError):
        main.run_scan('demo', cancel=token)
    assert not db.list_records('jobs')


def test_security_source_validation_and_atlas_failure(env, monkeypatch):
    from backend import global_pipeline
    api, _, _, _ = env
    assert api.patch('/api/repositories/demo', json={'security_source': 'invented'}).status_code == 422
    api.patch('/api/repositories/demo', json={'security_source': 'catalogue_atlas'})
    def unavailable(*a, **kw):
        raise global_pipeline.Error('unavailable')
    monkeypatch.setattr(global_pipeline, 'query_atlas', unavailable)
    operation = api.post('/api/repositories/demo/scan').json()
    result = operations.advance(operation['id'])
    assert result['status'] == 'failed' and 'unknown' in result['error']
    assert not db.list_records('jobs')


def test_withdrawal_without_job_blocks_old_active_evidence(env):
    api, _, _, _ = env
    from backend.applicability import from_global_entry
    old = from_global_entry(fresh().model_dump(mode='json'))
    withdrawn = {**old, 'revision': 2, 'content_hash': 'c' * 64, 'status': 'withdrawn'}
    route = '/api/repositories/demo/applicability/intake'
    assert api.post(route, json={'entry': withdrawn}).json()['jobs'] == []
    assert api.post(route, json={'entry': old}).status_code == 409
    assert not db.list_records('jobs')


def test_catalogue_refresh_requires_reassessment_before_retry(env):
    api, _, catalogue, root = env
    catalogue.ingest(fresh())
    entry = api.post('/api/repositories/demo/applicability/catalogue', json={}).json()['entries'][0]
    [job] = api.post('/api/repositories/demo/applicability/intake', json={'entry': entry}).json()['jobs']
    (root / 'demo' / 'app.py').write_text('import requests\n# changed source\n')
    main.refresh_repo('demo')
    assert api.post(f'/api/jobs/{job["id"]}/retry', json={}).status_code == 409
    [revalidated] = api.post('/api/repositories/demo/applicability/intake', json={'entry': entry}).json()['jobs']
    assert revalidated['id'] == job['id'] and not revalidated['stale']
    assert api.post(f'/api/jobs/{job["id"]}/retry', json={}).status_code == 200


def test_scheduler_uses_catalogue_and_publishes_evidence_atomically(env, monkeypatch):
    from backend import monitoring
    api, _, catalogue, _ = env
    catalogue.ingest(fresh())
    api.patch('/api/repositories/demo', json={'security_source': 'catalogue_local', 'monitoring': True,
                                            'policies': {'security': 'automatic'}})
    seen = []
    original = db.put_in
    def inspect(con, kind, record):
        if kind == 'jobs' and record.get('status') == 'Queued':
            assert record['catalogue']['entry_id'] == 'CVE-2024-35195'
            assert record['applicability']['citations']
            seen.append(record)
        return original(con, kind, record)
    monkeypatch.setattr(db, 'put_in', inspect)
    [operation_id] = monitoring.schedule_due(db.now())
    assert monitoring.schedule_due(db.now()) == []
    assert operations.advance(operation_id)['status'] == 'complete'
    assert seen
