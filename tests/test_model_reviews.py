import base64
import json

import pytest

from backend import db, operations, providers, reviews, workflow
from backend.agent_adapters import ROLES
from backend.contracts import CancellationToken, ProviderResult


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init()
    db.put('repositories', {'id':'repo','snapshot':'snap','source_snapshot_id':'snap',
                          'origin':{'source':'https://github.com/team/project'}})
    return tmp_path


@pytest.mark.parametrize('role', sorted(ROLES))
def test_claude_roles_are_prompted_not_checkpoints(monkeypatch, role):
    monkeypatch.setenv('STEWARD_MODEL_PROVIDER','anthropic')
    monkeypatch.setenv('STEWARD_MODEL','claude-test')
    monkeypatch.setenv('STEWARD_SPECIALIST_MODE','1')
    target=providers.resolve_target('document',role)
    assert target['specialist'] and target['agent_role']==role and target['model']=='claude-test'
    assert 'not an Adaption checkpoint' in target['routing_reason']
    monkeypatch.setenv('STEWARD_SPECIALIST_MODE','0')
    assert not providers.resolve_target('document',role)['specialist']


def install_review(monkeypatch):
    evidence={'path':'app.py','start_line':1,'end_line':2,'content':'def load(value):\n    return eval(value)'}
    monkeypatch.setattr(reviews.retrieval,'retrieve',lambda *a,**k: {'items':[evidence],'knowledge':[], 'missing':[]})
    monkeypatch.setattr(reviews.agent_context,'agent_graph_context',lambda *a,**k: {'edges':[]})
    monkeypatch.setattr(providers,'resolve_target',lambda *a,**k: {'specialist':False})
    calls=[]
    def complete(request, cancel=None):
        calls.append(request)
        return ProviderResult(output={'summary':'Potential unsafe evaluation','findings':[{'message':'Untrusted input may reach eval',
            'severity':'high','reference':{'path':'app.py','start_line':2,'end_line':2},'verification':'Test controlled untrusted input in a sandbox'}],
            'limitations':['Input reachability not established']},telemetry={'model':'mock-claude','usage':None})
    monkeypatch.setattr(providers,'complete',complete)
    return calls


def test_security_review_persists_and_excludes_notes(store, monkeypatch):
    calls=install_review(monkeypatch)
    db.put('notes',{'repo_id':'repo','content':'PRIVATE TEAM NOTE'})
    item=reviews.submit('repo','security')
    assert reviews.submit('repo','security')['id']==item['id']
    result=operations.advance(item['id'])
    assert result['status']=='complete', result
    assert result['result']['findings'][0]['status']=='hypothesis'
    assert not result['result']['checks_executed']
    assert 'PRIVATE TEAM NOTE' not in json.dumps(calls[0].payload)
    assert reviews.list_reviews('repo')[0]['result']==result['result']


def test_cancel_during_model_call_cannot_publish(store, monkeypatch):
    install_review(monkeypatch)
    item=reviews.submit('repo','security')
    complete=providers.complete
    def cancel_call(request,cancel=None):
        result=complete(request,cancel)
        operations.cancel(item['id'])
        return result
    monkeypatch.setattr(providers,'complete',cancel_call)
    result=operations.advance(item['id'])
    assert result['status']=='cancelled' and not result.get('result')


@pytest.mark.parametrize('fault',['citation','snapshot'])
def test_invalid_review_cannot_publish(store,monkeypatch,fault):
    install_review(monkeypatch)
    complete=providers.complete
    def invalid(request,cancel=None):
        result=complete(request,cancel)
        if fault=='citation': result.output['findings'][0]['reference']['end_line']=300
        else: db.put('repositories',{'id':'repo','snapshot':'new','source_snapshot_id':'new'})
        return result
    monkeypatch.setattr(providers,'complete',invalid)
    item=reviews.submit('repo','security')
    result=operations.advance(item['id'])
    assert result['status']=='failed' and not result.get('result')


@pytest.mark.parametrize('url',['http://github.com/a/b','https://localhost/a/b','https://github.com/a/../b','https://github.com/a/b?x=1','https://token@github.com/a/b'])
def test_github_boundary(url):
    with pytest.raises(ValueError): reviews.github_name(url)


def test_pull_sources_are_immutable_bounded_and_secret_filtered(monkeypatch):
    calls=[]
    head='a'*40
    def fetch(path,cancel):
        calls.append(path)
        if '/contents/' in path:
            assert path.endswith('?ref='+head)
            return {'type':'file','encoding':'base64','size':5,'content':base64.b64encode(b'x = 1').decode()}
        if '/files?' in path:
            return [{'filename':'.env','status':'added'},{'filename':'app.py','status':'modified','patch':'@@ -1 +1 @@\n+x = 1'}]
        return {'head':{'sha':head,'repo':{'full_name':'fork/project'}},'base':{'sha':'b'*40,'repo':{'full_name':'team/project'}},'changed_files':2}
    monkeypatch.setattr(reviews,'fetch_json',fetch)
    context=reviews.pull_context('team/project',1,head,CancellationToken())
    assert [item['path'] for item in context['items']]==['app.py']
    assert not any('/contents/.env' in path for path in calls)
    with pytest.raises(ValueError,match='changed'): reviews.pull_context('team/project',1,'c'*40,CancellationToken())


def test_refresh_between_review_and_publication_is_rejected(store, monkeypatch):
    def execute(*args):
        db.put('repositories',{'id':'repo','source_snapshot_id':'changed','snapshot':'changed'})
        return {'summary':'must not publish'}
    monkeypatch.setattr(reviews,'execute',execute)
    item=reviews.submit('repo','security')
    result=operations.advance(item['id'])
    assert result['status']=='failed' and not result.get('result')


def test_finished_reviews_can_be_run_again(store, monkeypatch):
    install_review(monkeypatch)
    first=reviews.submit('repo','security')
    assert operations.advance(first['id'])['status']=='complete'
    assert reviews.submit('repo','security')['id']!=first['id']


def test_review_context_is_bounded_before_inference(monkeypatch):
    monkeypatch.setattr(providers, 'resolve_target', lambda *a, **k: {'specialist': False})
    monkeypatch.setattr(providers, 'capabilities', lambda *a: {'context_limit': 16000, 'max_output_tokens':4096})
    body={'context': {'items':[{'path':'small.py','content':'x=1'}, {'path':'large.py','content':'x'*40000}], 'missing':[]},
          'approved_knowledge': [], 'graph_context':None, 'specialist_review': {'text':'x'*16000}}
    reviews.fit_context(body, 'audit')
    assert [item['path'] for item in body['context']['items']]==['small.py']
    assert 'specialist_review' not in body
    assert body['context']['missing']


def test_large_single_file_review_keeps_resolvable_line_excerpt(monkeypatch):
    monkeypatch.setattr(providers, 'resolve_target', lambda *a, **k: {'specialist': False})
    monkeypatch.setattr(providers, 'capabilities', lambda *a: {'context_limit': 16000, 'max_output_tokens':4096})
    lines = ['value = 123456789'] * 3000
    body = {'context': {'items':[{'path':'large.py','content':'\n'.join(lines),
                                 'start_line':1,'end_line':3000}], 'missing':[]}}
    reviews.fit_context(body, 'audit')
    item = body['context']['items'][0]
    assert 0 < item['end_line'] < 3000
    assert item['content'].splitlines() == lines[:item['end_line']]
    assert body['context']['missing']
