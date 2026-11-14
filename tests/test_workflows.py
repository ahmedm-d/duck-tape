import io
import zipfile
import pytest
from fastapi.testclient import TestClient
from backend import db, sample, worker
from backend.main import app


@pytest.fixture
def workspace(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA_DIR',tmp_path)
    db.init()
    repo=sample.seed_sample()
    return TestClient(app),repo


def scenario(repo,name):
    return next(j for j in db.list_records('jobs') if j['repo_id']==repo['id'] and j['scenario']==name)


def test_patch_regression_revision_and_managed_apply(workspace):
    c,r=workspace; j=scenario(r,'bug'); url=f"/api/jobs/{j['id']}"
    worker.process_job(j['id'])
    assert db.get('jobs',j['id'])['status']=='Awaiting approval'
    assert c.post(url+'/approve',json={}).status_code==200
    worker.process_job(j['id'])
    detail=c.get(url).json()
    assert detail['status']=='Ready for review',detail.get('error')
    revision=detail['current_revision']
    assert any(x['phase']=='regression-before' and x['exit_code']!=0 for x in detail['checks'])
    assert any(x['phase']=='after' and x['exit_code']==0 for x in detail['checks'])
    assert 'diff --git' in c.get(url+'/patch').text
    assert c.post(url+'/accept',json={'revision_id':revision}).status_code==200
    assert c.post(url+'/revise',json={'text':'Add regression for zero quantity and default quantity'}).status_code==200
    assert db.get('jobs',j['id'])['approval'] is None
    assert c.post(url+'/revise',json={'text':'Concurrent revision'}).status_code==409
    worker.process_job(j['id'])
    detail=c.get(url).json()
    assert detail['status']=='Ready for review',detail.get('error')
    assert len(detail['revisions'])==2
    assert detail['current_revision']!=revision
    assert c.post(url+'/accept',json={'revision_id':revision}).status_code==409
    assert c.post(url+'/accept',json={'revision_id':detail['current_revision']}).status_code==200
    applied=c.post(url+'/apply',json={'revision_id':detail['current_revision']})
    assert applied.status_code==200,applied.text
    assert applied.json()['status']=='Completed'
    assert 'quantity' not in (db.DATA_DIR/'repositories'/r['id']/'pricing.py').read_text()
    from pathlib import Path
    assert 'quantity' in (Path(applied.json()['integration_root'])/'pricing.py').read_text()


def test_decision_then_resume_and_benchmark(workspace):
    c,r=workspace; j=scenario(r,'feature'); url=f"/api/jobs/{j['id']}"
    c.post(url+'/approve',json={}); worker.process_job(j['id'])
    detail=c.get(url).json()
    assert detail['status']=='Needs input'
    d=detail['decisions'][0]
    assert c.post(url+'/decide',json={'decision_id':d['id'],'option':'strict'}).status_code==200
    worker.process_job(j['id'])
    assert c.get(url).json()['status']=='Ready for review'
    j=scenario(r,'performance'); c.post(f"/api/jobs/{j['id']}/approve",json={}); worker.process_job(j['id'])
    detail=c.get(f"/api/jobs/{j['id']}").json()
    assert detail['status']=='Ready for review',detail.get('error')
    assert {x['phase'] for x in detail['checks']} >= {'benchmark-before','benchmark-after'}


def test_refresh_import_and_scan_deduplication(workspace):
    from backend.operations import advance
    c,r=workspace
    a=advance(c.post(f"/api/repositories/{r['id']}/scan").json()['id'])['result']
    b=advance(c.post(f"/api/repositories/{r['id']}/scan").json()['id'])['result']
    assert [j['id'] for j in a['jobs']]==[j['id'] for j in b['jobs']]
    note=c.post('/api/knowledge',json={'repo_id':r['id'],'title':'Money','content':'Integer cents','status':'team-approved'}).json()
    root=db.DATA_DIR/'repositories'/r['id']
    (root/'search.py').unlink()
    (root/'config.py').write_text('TAX_RATE = 20\nMAX_ITEMS = 50\n')
    refreshed=advance(c.post(f"/api/repositories/{r['id']}/refresh").json()['id'])['result']
    refreshed=c.get(f"/api/repositories/{refreshed['id']}").json()
    assert refreshed['snapshot']!=r['snapshot']
    assert not any(f['path']=='search.py' for f in refreshed['index']['files'])
    assert db.get('knowledge',note['id'])['status']=='stale'
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as z:
        z.writestr('app.py','from helper import value\nprint(value)\n')
        z.writestr('helper.py','value = 3\n')
        z.writestr('.env','PASSWORD=do-not-index')
    imported=c.post('/api/repositories/upload',files={'file':('second.zip',buf.getvalue(),'application/zip')})
    assert imported.status_code==202,imported.text
    other=advance(imported.json()['id'])['result']
    other=c.get(f"/api/repositories/{other['id']}").json()
    assert any(f['status']=='excluded' for f in other['index']['files'])
    assert 'do-not-index' not in str(other['index'])
    assert c.patch(f"/api/repositories/{other['id']}",json={'policies':{'ticket':'automatic'}}).status_code==200
    j=c.post('/api/jobs',json={'repo_id':other['id'],'title':'Trace value in helper'}).json()
    assert j['status']=='Queued'
