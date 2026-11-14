"""Independent functional acceptance checks; fixed fixtures, no live inference."""
import json
import threading
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend import db, providers, sample, worker
from backend.main import app
from backend.operations import advance


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init()
    repo = sample.seed_sample()
    return repo


def scenario(name):
    return next(j for j in db.list_records('jobs') if j['scenario'] == name)


def prepare(job):
    db.mutate('jobs', job['id'], lambda j: {**j, 'status':'Queued', 'preparation_approved':True})
    from provider_fixture import run_with_plan_approval
    run_with_plan_approval(job['id'])
    result = db.get('jobs', job['id'])
    assert result['status'] == 'Ready for review', result
    return result


def test_scan_deduplicates_preseeded_advisory(workspace):
    client = TestClient(app)
    for _ in range(2):
        response = client.post(f"/api/repositories/{workspace['id']}/scan", json={})
        assert response.status_code == 202
        assert advance(response.json()['id'])['status'] == 'complete'
    advisory_jobs = [j for j in db.list_records('jobs') if j.get('scenario') == 'security']
    assert len(advisory_jobs) == 1
    assert advisory_jobs[0].get('source_url', '').startswith('fixture:')
    assert advisory_jobs[0]['source_url'].endswith('STEWARD-DEMO-001')


def test_rejected_advisory_stays_rejected_when_unrelated_file_changes(workspace):
    client = TestClient(app)
    scanned = advance(client.post(f"/api/repositories/{workspace['id']}/scan", json={}).json()['id'])['result']['jobs'][0]
    assert client.post(f"/api/jobs/{scanned['id']}/reject", json={}).status_code == 200
    before = len(db.list_records('jobs'))
    readme = Path(workspace['root']) / 'README.md'
    readme.write_text(readme.read_text() + '\nUnrelated documentation update.\n')
    again = advance(client.post(f"/api/repositories/{workspace['id']}/scan", json={}).json()['id'])['result']['jobs'][0]
    assert again['id'] == scanned['id']
    assert again['status'] == 'Rejected'
    assert len(db.list_records('jobs')) == before


def test_expired_lease_recovered_after_worker_start(workspace):
    job = scenario('security')
    db.mutate('jobs', job['id'], lambda j: {**j, 'status':'Implementing', 'preparation_approved':True, 'lease_token':'dead-worker', 'lease_until':time.time()-60})
    worker.start_worker()
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if db.get('jobs', job['id'])['status'] in {'Ready for review','Failed'}:
                break
            time.sleep(.1)
        assert db.get('jobs', job['id'])['status'] == 'Ready for review'
        assert len([r for r in db.list_records('revisions') if r['job_id'] == job['id']]) == 1
    finally:
        worker.stop_worker()


def test_cancel_stops_actual_check_process(workspace):
    job = scenario('bug')
    db.mutate('jobs', job['id'], lambda j: {**j, 'status':'Verifying', 'lease_token':'check-token', 'lease_until':time.time()+60})
    outcomes = []
    def execute():
        try:
            # Fixed test-authored process, never imported/generated code.
            worker.check(job['id'], 'check-token', Path(workspace['root']), 'test', 'import time; time.sleep(30)', 'fixed cancellation probe')
            outcomes.append('unexpected completion')
        except worker.LostLease:
            outcomes.append('cancelled')
    thread = threading.Thread(target=execute)
    thread.start()
    process = None
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            process = worker._processes.get(job['id'])
            if process: break
            time.sleep(.02)
        assert process is not None
        assert TestClient(app).post(f"/api/jobs/{job['id']}/cancel", json={}).status_code == 200
        thread.join(5)
        assert not thread.is_alive()
        assert process.poll() is not None
        assert outcomes == ['cancelled']
        assert db.get('jobs', job['id'])['status'] == 'Cancelled'
        assert not db.list_records('revisions')
    finally:
        if process and process.poll() is None: process.kill()
        thread.join(5)


def test_approved_knowledge_enters_later_provider_context_and_notes_do_not(workspace, monkeypatch):
    db.mutate('repositories', workspace['id'], lambda r: {**r, 'is_sample':False})
    client = TestClient(app)
    common = {'repo_id': workspace['id'], 'title':'Integer cents', 'content':'APPROVED_POLICY_MARKER: use integer cents', 'scope':'pricing.py'}
    knowledge = client.post('/api/knowledge', json=common).json()
    unrelated = client.post('/api/knowledge', json={**common, 'scope':'unrelated/*', 'content':'UNRELATED_POLICY', 'status':'team-approved'}).json()
    job = scenario('bug')
    client.post(f"/api/jobs/{job['id']}/note", json={'text':'TEAM_NOTE_PRIVATE_MARKER'})
    assert client.post(f"/api/knowledge/{knowledge['id']}/approve", json={'version':knowledge['version'],'snapshot':workspace['snapshot']}).status_code == 200
    calls = []
    def complete(task, payload, risk):
        calls.append((task, payload))
        if task == 'review': return {'approved':True, 'findings':[]}, {'mode':'test transport'}
        return {'edits':[{'path':'pricing.py', 'content':'def total(items, tax_rate):\n    return 0\n'}], 'explanation':'Transport fixture'}, {'mode':'test transport'}
    monkeypatch.setattr(providers, 'complete', complete)
    from provider_fixture import adapt_legacy_provider
    adapt_legacy_provider(monkeypatch)
    prepare(job)
    prompt = json.dumps(calls[0][1])
    assert 'APPROVED_POLICY_MARKER' in prompt
    assert 'UNRELATED_POLICY' not in prompt
    assert 'TEAM_NOTE_PRIVATE_MARKER' not in prompt
    first_hash = calls[0][1]['context']['context_hash']
    client.patch(f"/api/knowledge/{knowledge['id']}", json={'content':'REVISED_POLICY_MARKER: preserve cents'})
    assert db.get('jobs', job['id'])['status'] == 'Failed'
    updated_knowledge = db.get('knowledge', knowledge['id'])
    assert updated_knowledge['status'] == 'observed'
    assert client.post(f"/api/knowledge/{knowledge['id']}/approve", json={'version':updated_knowledge['version'],'snapshot':workspace['snapshot']}).status_code == 200
    assert client.post(f"/api/jobs/{job['id']}/retry", json={}).status_code == 200
    from provider_fixture import run_with_plan_approval
    run_with_plan_approval(job['id'])
    assert db.get('jobs', job['id'])['status'] == 'Ready for review'
    assert calls[2][1]['context']['context_hash'] != first_hash
    assert 'REVISED_POLICY_MARKER' in json.dumps(calls[2][1])


def test_overlapping_prepared_jobs_cannot_overwrite_integrated_patch(workspace):
    first = prepare(scenario('bug'))
    other = db.put('jobs', {k:v for k,v in first.items() if k not in {'id','current_revision'}})
    second = prepare(other)
    client = TestClient(app)
    for job in (first, second):
        assert client.post(f"/api/jobs/{job['id']}/accept", json={'revision_id':job['current_revision']}).status_code == 200
    assert client.post(f"/api/jobs/{first['id']}/apply", json={'revision_id':first['current_revision']}).status_code == 200
    response = client.post(f"/api/jobs/{second['id']}/apply", json={'revision_id':second['current_revision']})
    assert response.status_code == 409
    assert 'Overlapping' in response.json()['detail']
    assert sample.trusted_tree(Path(workspace['root']))


def test_failed_provider_run_retains_actual_context(workspace, monkeypatch):
    db.mutate('repositories', workspace['id'], lambda r: {**r, 'is_sample':False})
    job = scenario('bug')
    db.mutate('jobs', job['id'], lambda j: {**j, 'status':'Queued', 'preparation_approved':True})
    def fail(*args):
        raise ValueError('Transport unavailable in deliberate test')
    monkeypatch.setattr(providers, 'complete', fail)
    worker.process_job(job['id'])
    assert db.get('jobs', job['id'])['status'] == 'Failed'
    run = next(r for r in db.list_records('runs') if r['job_id'] == job['id'])
    assert run['context']['snapshot'] == workspace['snapshot']
    assert {'app.py', 'pricing.py', 'config.py', 'tests/test_quote.py'} <= {i['path'] for i in run['context']['items']}


def test_scheduled_polling_resumes_and_deduplicates(workspace):
    from backend import main,operations
    db.mutate('repositories', workspace['id'], lambda r: {**r, 'monitoring':True, 'next_scan':'2000-01-01T00:00:00+00:00'})
    def count_scans():
        return len([e for e in db.list_records('events') if e['message'].startswith('Scan finished:')])
    initial = count_scans()
    for expected in (initial + 1, initial + 2):
        main.poll_stop.clear()
        operations.start_operations()
        thread = threading.Thread(target=main.scheduler)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and count_scans() < expected:
                time.sleep(.1)
            assert count_scans() == expected
            repo = db.get('repositories', workspace['id'])
            assert repo['last_scan'] and repo['next_scan'] > repo['last_scan']
        finally:
            main.poll_stop.set()
            thread.join(5)
            operations.stop_operations()
        assert not thread.is_alive()
        db.mutate('repositories', workspace['id'], lambda r: {**r, 'next_scan':'2000-01-01T00:00:00+00:00'})
    assert len([j for j in db.list_records('jobs') if j.get('scenario') == 'security']) == 1
