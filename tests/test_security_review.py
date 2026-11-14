"""Independent security review regressions; no live model or untrusted execution."""
import io
import json
import zipfile
import threading
import pytest
from fastapi.testclient import TestClient
from backend import repository, worker, db, sample, providers
from backend.main import app


@pytest.mark.parametrize('name', ['.GIT/config', '.git./config', '.git /config', 'NUL.py', 'x:stream', 'x/.ENV.local'])
def test_windows_edit_aliases_rejected(tmp_path, name):
    with pytest.raises(ValueError):
        worker.safe_path(tmp_path, name)


def test_case_variant_secret_directories_and_environment_excluded(tmp_path):
    (tmp_path / '.AWS').mkdir()
    (tmp_path / '.AWS' / 'config.json').write_text('{"value":"private"}')
    (tmp_path / '.GIT').mkdir()
    (tmp_path / '.GIT' / 'notes.md').write_text('private git metadata')
    (tmp_path / '.ENV.json').write_text('{"value":"private environment"}')
    indexed = repository.index_repository(tmp_path)
    assert indexed['coverage']['indexed'] == 0
    assert 'private' not in json.dumps(indexed)


@pytest.mark.parametrize('content', [
    '{"api_key": "abcdefghijklmnop123456"}',
    'export const password = "abcdefghijklmnop123456";',
    'DATABASE_PASSWORD=abcdefghijklmnop123456',
])
def test_common_credential_assignments_excluded(tmp_path, content):
    (tmp_path / 'settings.json').write_text(content)
    indexed = repository.index_repository(tmp_path)
    assert indexed['files'][0]['status'] == 'excluded'
    assert 'abcdefghijklmnop123456' not in json.dumps(indexed)


@pytest.mark.parametrize('name', ['NUL.py', 'CON/file.py', 'a./secret.py', 'a /secret.py', 'C:escape.py'])
def test_archive_windows_aliases_rejected(tmp_path, name):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr(name, 'never executed')
    with pytest.raises(ValueError):
        repository.import_zip(buffer.getvalue(), tmp_path / 'import')


def test_dns_mixed_public_private_rejected(monkeypatch):
    monkeypatch.setattr(repository.socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('1.1.1.1', 443)), (2, 1, 6, '', ('127.0.0.1', 443))])
    with pytest.raises(ValueError):
        repository.validate_public_url('https://untrusted.example/research')


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init()
    repo = sample.seed_sample()
    return repo


def test_cancel_during_provider_prevents_late_revision(workspace, monkeypatch):
    db.mutate('repositories', workspace['id'], lambda r: {**r, 'is_sample': False})
    job = next(j for j in db.list_records('jobs') if j['scenario'] == 'bug')
    db.mutate('jobs', job['id'], lambda j: {**j, 'status': 'Queued', 'preparation_approved': True})
    entered, released = threading.Event(), threading.Event()
    def complete(*args):
        entered.set()
        assert released.wait(10)
        return {'edits': [{'path': 'pricing.py', 'content': 'value = 1\n'}], 'explanation': 'Mock delayed transport'}, {'mode': 'test'}
    monkeypatch.setattr(providers, 'complete', complete)
    from provider_fixture import adapt_legacy_provider
    adapt_legacy_provider(monkeypatch)
    worker.process_job(job['id'])
    planned=db.get('jobs',job['id'])
    assert planned['status']=='Awaiting approval' and planned['plan_hash']
    assert not entered.is_set()
    from backend import workflow
    workflow.transition(job['id'],'approve',{})
    thread = threading.Thread(target=worker.process_job, args=(job['id'],))
    thread.start()
    try:
        assert entered.wait(10)
        client = TestClient(app)
        assert client.post(f"/api/jobs/{job['id']}/cancel", json={}).status_code == 200
    finally:
        released.set()
        thread.join(10)
    assert not thread.is_alive()
    assert db.get('jobs', job['id'])['status'] == 'Cancelled'
    assert not db.list_records('revisions')


def test_concurrent_revision_requests_only_queue_once(workspace):
    job = next(j for j in db.list_records('jobs') if j['scenario'] == 'bug')
    db.mutate('jobs', job['id'], lambda j: {**j, 'status': 'Ready for review', 'current_revision': 'old', 'approval': {'revision_id': 'old'}})
    barrier = threading.Barrier(2)
    responses = []
    def revise():
        barrier.wait()
        responses.append(TestClient(app).post(f"/api/jobs/{job['id']}/revise", json={'text': 'Add edge coverage'}).status_code)
    threads = [threading.Thread(target=revise) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(10)
    assert sorted(responses) == [200, 409]
    updated = db.get('jobs', job['id'])
    assert updated['approval'] is None
    assert updated['revision_instructions'] == ['Add edge coverage']


def test_repository_refresh_invalidates_approval_and_deleted_evidence(workspace):
    from pathlib import Path
    job = next(j for j in db.list_records('jobs') if j['scenario'] == 'bug')
    db.mutate('jobs', job['id'], lambda j: {**j, 'status': 'Ready for review', 'approval': {'revision_id': 'old'}})
    (Path(workspace['root']) / 'pricing.py').unlink()
    response = TestClient(app).post(f"/api/repositories/{workspace['id']}/refresh", json={})
    assert response.status_code == 202
    from backend.operations import advance
    result = advance(response.json()['id'])
    assert result['status'] == 'complete', result
    refreshed=TestClient(app).get(f"/api/repositories/{workspace['id']}").json()
    assert not any(f['path'] == 'pricing.py' for f in refreshed['index']['files'])
    updated = db.get('jobs', job['id'])
    assert updated['status'] == 'Failed' and updated['approval'] is None
    assert updated['stale']
