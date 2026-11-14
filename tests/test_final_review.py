"""Fresh acceptance review: actual Git patches and concurrent policy edits."""
import threading
import subprocess
import pytest
from fastapi.testclient import TestClient
from backend import db, sample, worker, main, providers


@pytest.fixture
def ready(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init()
    repo = sample.seed_sample()
    job = next(j for j in db.list_records('jobs') if j['scenario'] == 'bug')
    db.mutate('jobs',job['id'],lambda j:{**j,'status':'Queued','preparation_approved':True})
    worker.process_job(job['id'])
    job = db.get('jobs',job['id'])
    assert job['status'] == 'Ready for review', job
    return repo,job


def test_diff_only_contains_declared_changed_files(ready):
    _,job = ready
    revision = db.get('revisions',job['current_revision'])
    assert '__pycache__' not in revision['diff']
    declared = {item['path'] for item in revision['files']}
    actual = {line.split(' b/',1)[1] for line in revision['diff'].splitlines() if line.startswith('diff --git ')}
    assert actual == declared


def test_knowledge_edit_serializes_with_approved_application(ready, monkeypatch):
    repo,job = ready
    client = TestClient(main.app)
    assert client.post(f"/api/jobs/{job['id']}/accept",json={'revision_id':job['current_revision']}).status_code == 200
    entered,release,started,finished = (threading.Event() for _ in range(4))
    original = sample.git
    def paused_git(root,*args):
        if args[:2] == ('apply','--check'):
            entered.set()
            assert release.wait(10)
        return original(root,*args)
    monkeypatch.setattr(sample,'git',paused_git)
    responses = []
    def apply():
        responses.append(client.post(f"/api/jobs/{job['id']}/apply",json={'revision_id':job['current_revision']}))
    def policy():
        started.set()
        client.post('/api/knowledge',json={'repo_id':repo['id'],'title':'New constraint','content':'Never change pricing','scope':'pricing.py','status':'team-approved'})
        finished.set()
    applying = threading.Thread(target=apply)
    changing = threading.Thread(target=policy)
    applying.start()
    try:
        assert entered.wait(10)
        changing.start()
        assert started.wait(2)
        # Policy mutations must share the application lock, preventing stale approval use.
        assert not finished.wait(.5), 'Policy invalidated approval while Git application still proceeded'
    finally:
        release.set()
        applying.join(15)
        if changing.ident: changing.join(15)
    assert responses and responses[0].status_code == 200


def test_managed_git_does_not_inherit_host_configuration(tmp_path, monkeypatch):
    global_config = tmp_path / 'host.gitconfig'
    global_config.write_text('[steward]\n review-marker = host-only\n',encoding='utf-8')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL',str(global_config))
    root = tmp_path / 'repo'
    root.mkdir()
    sample.git(root,'init')
    # Configuration can name executable clean filters; managed Git must not inherit it.
    with pytest.raises(subprocess.CalledProcessError):
        sample.git(root,'config','--get','steward.review-marker')


def test_generic_revision_receives_previous_actual_patch(ready, monkeypatch):
    repo,job = ready
    previous = db.get('revisions',job['current_revision'])
    db.mutate('repositories',repo['id'],lambda r:{**r,'is_sample':False})
    payloads = []
    def complete(task,payload,risk):
        if task == 'review': return {'approved':True,'findings':[]},{'mode':'test transport'}
        payloads.append(payload)
        return {'edits':[{'path':f['path'],'content':f['after']} for f in previous['files']],'explanation':'Retain first patch as explicitly requested'}, {'mode':'test transport'}
    monkeypatch.setattr(providers,'complete',complete)
    from provider_fixture import adapt_legacy_provider
    adapt_legacy_provider(monkeypatch)
    client = TestClient(main.app)
    assert client.post(f"/api/jobs/{job['id']}/revise",json={'text':'Keep the current fix and review its edge cases'}).status_code == 200
    from provider_fixture import run_with_plan_approval
    run_with_plan_approval(job['id'])
    import json
    serialized = json.dumps(payloads[0])
    assert previous['diff'] in [v for v in payloads[0].values() if isinstance(v,str)] or 'diff --git' in serialized
    assert 'previous_revision' in payloads[0]
    assert payloads[0]['previous_revision']['id'] == previous['id']
