import io
import zipfile
import pytest
from fastapi.testclient import TestClient
from backend import db
from backend.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init()
    db.put('repositories', {'id':'repo','name':'test','root':str(tmp_path),'snapshot':'s1','is_sample':False,'policies':{'ticket':'approve_first'},'index':{'files':[], 'edges':[], 'overview':'test'}})
    return TestClient(app)


def test_duck_tape_api_activity_and_download_branding(client, monkeypatch, tmp_path):
    from backend import main
    monkeypatch.setattr(main, 'DIST', tmp_path / 'missing-frontend')
    assert client.get('/openapi.json').json()['info']['title'] == 'Duck Tape'
    assert client.get('/').json()['message'].startswith('Duck Tape backend ready.')
    assert db.event('Ready')['author'] == 'Duck Tape'
    job = client.post('/api/jobs', json={'repo_id':'repo','title':'A useful request'}).json()
    assert all(event['author'] == 'Duck Tape' for event in db.list_records('events'))
    db.put('jobs', {**job, 'current_revision':'revision'})
    db.put('revisions', {'id':'revision','job_id':job['id'],'base_snapshot':'s1',
                        'patch_hash':'hash','diff':'sample patch','files':[]})
    patch = client.get(f"/api/jobs/{job['id']}/patch")
    assert patch.headers['content-disposition'] == f'attachment; filename="duck-tape-{job["id"][:8]}.patch"'
    assert patch.text == 'sample patch'
    files = client.get(f"/api/jobs/{job['id']}/files")
    assert files.headers['content-disposition'] == f'attachment; filename="duck-tape-{job["id"][:8]}.zip"'
    with zipfile.ZipFile(io.BytesIO(files.content)) as archive:
        assert set(archive.namelist()) == {'duck-tape.patch', 'duck-tape-changes.json'}


def test_approval_separate_from_patch_approval_and_idempotent(client):
    payload = {'repo_id':'repo','title':'Fix a thing','description':'Specific requirement','source':'ticket'}
    job = client.post('/api/jobs',json=payload).json()
    assert job['status'] == 'Awaiting approval'
    assert client.post('/api/jobs',json=payload).json()['id'] == job['id']
    assert client.post(f"/api/jobs/{job['id']}/accept",json={}).status_code == 409
    assert client.post(f"/api/jobs/{job['id']}/approve",json={}).json()['status'] == 'Queued'
    assert client.post(f"/api/jobs/{job['id']}/approve",json={}).status_code == 409
    assert client.post(f"/api/jobs/{job['id']}/cancel",json={}).json()['status'] == 'Cancelled'
    assert client.post(f"/api/jobs/{job['id']}/revise",json={'text':'change'}).status_code == 409


def test_notes_do_not_change_revision_inputs(client):
    j=client.post('/api/jobs',json={'repo_id':'repo','title':'A useful request'}).json()
    client.post(f"/api/jobs/{j['id']}/note",json={'text':'team secret note'})
    after=db.get('jobs',j['id'])
    assert 'team secret note' not in str(after)
    assert len(client.get(f"/api/jobs/{j['id']}").json()['notes']) == 1


def test_exact_revision_required(client):
    job=db.put('jobs',{'repo_id':'repo','status':'Ready for review','base_snapshot':'s1','current_revision':'r2'})
    db.put('revisions',{'id':'r2','job_id':job['id'],'base_snapshot':'s1','patch_hash':'hash','files':[]})
    assert client.post(f"/api/jobs/{job['id']}/accept",json={'revision_id':'r1'}).status_code==409
    assert client.post(f"/api/jobs/{job['id']}/accept",json={'revision_id':'r2'}).status_code==200
    assert client.post(f"/api/jobs/{job['id']}/apply",json={'revision_id':'r2'}).status_code==409


def test_origin_and_validation(client):
    assert client.post('/api/jobs',json={},headers={'origin':'https://evil.example'}).status_code==403
    assert client.post('/api/jobs',json={'repo_id':'repo','title':''}).status_code==422


def test_retry_changed_base_does_not_reuse_stale_patch(client):
    j=db.put('jobs',{'repo_id':'repo','status':'Failed','base_snapshot':'old','current_revision':'old-patch','preparation_approved':True,'approval':{'revision_id':'old-patch'}})
    result=client.post(f"/api/jobs/{j['id']}/retry",json={}).json()
    assert result['status']=='Queued'
    assert result['base_snapshot']=='s1'
    assert result['current_revision'] is None
    assert result['approval'] is None
