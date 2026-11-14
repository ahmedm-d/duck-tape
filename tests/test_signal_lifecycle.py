import json
from backend import db, signals, sample


def test_scan_all_resolved_versions_and_aliases(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init()
    packages = {f'node_modules/p{i}': {'version': '1.0.0'} for i in range(47)}
    index = {'files': [{'path': 'package-lock.json', 'status': 'indexed', 'content': json.dumps({'packages': packages})}]}
    calls = []
    def query(package):
        calls.append(package['name'])
        return [{'id': 'GHSA-one', 'aliases': ['CVE-2026-1000'], 'summary': 'One'}, {'id': 'CVE-2026-1000', 'aliases': ['GHSA-one'], 'summary': 'Same'}]
    monkeypatch.setattr(signals, 'query_osv', query)
    result = signals.scan({'id': 'repo', 'snapshot': 'base', 'index': index})
    assert len(calls) == 47
    assert result['scanned'] == 47 and len(result['signals']) == 47
    signals.scan({'id': 'repo', 'snapshot': 'base', 'index': index})
    assert len(calls) == 47


def test_uv_lock_and_partial_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init()
    index = {'files': [{'path': 'uv.lock', 'status': 'indexed', 'content': 'version = 1\n[[package]]\nname="six"\nversion="1.17.0"\nsource={ registry="https://pypi.org/simple" }\n'}]}
    packages, gaps = signals.dependencies(index)
    assert packages[0]['name'] == 'six' and packages[0]['version'] == '1.17.0'
    monkeypatch.setattr(signals, 'query_osv', lambda package: (_ for _ in ()).throw(TimeoutError()))
    result = signals.scan({'id': 'repo', 'snapshot': 'base', 'index': index})
    assert result['failed'] == 1 and result['complete'] is False


def test_source_identity_changes_only_with_evidence_and_snooze_expires(tmp_path, monkeypatch):
    from backend.main import create_job, JobInput
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init()
    repo = sample.seed_sample()
    body = JobInput(repo_id=repo['id'], title='Sourced request', description='Version one', source_key='external-42')
    one = create_job(body)
    db.mutate('jobs', one['id'], lambda j: {**j, 'status': 'Rejected'})
    assert create_job(body)['id'] == one['id']
    changed = create_job(body.model_copy(update={'description': 'Version two'}))
    assert changed['id'] != one['id']
    db.mutate('jobs', changed['id'], lambda j: {**j, 'status': 'Rejected', 'snoozed_until': '2000-01-01T00:00:00+00:00'})
    assert create_job(body.model_copy(update={'description': 'Version two'}))['status'] == 'Awaiting approval'


def test_preparation_card_has_actual_repository_evidence(tmp_path, monkeypatch):
    from backend.main import create_job, JobInput
    monkeypatch.setattr(db,'DATA_DIR',tmp_path)
    db.init()
    repo=sample.seed_sample()
    job=create_job(JobInput(repo_id=repo['id'],title='Quote quantity is ignored',description='Trace app.py quote through pricing and config with tests.'))
    assert job['status']=='Awaiting approval'
    assert {'app.py','pricing.py','config.py','tests/test_quote.py'}<=set(job['affected_areas'])
    assert job['qualification_context']['items']
    assert 'investigation' in job['rationale'].lower()
