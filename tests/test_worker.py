import json
import pytest
from backend import db, sample, providers, worker

@pytest.fixture
def workspace(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA_DIR',tmp_path)
    db.init()
    repo=sample.seed_sample()
    return repo

def queued(scenario):
    job=next(j for j in db.list_records('jobs') if j.get('scenario')==scenario)
    return db.mutate('jobs',job['id'],lambda j:{**j,'status':'Queued','preparation_approved':True})

def test_real_bug_patch_baseline_regression_after(workspace):
    job=queued('bug'); worker.process_job(job['id'])
    result=db.get('jobs',job['id'])
    assert result['status']=='Ready for review',result
    revision=db.get('revisions',result['current_revision'])
    assert 'diff --git a/pricing.py b/pricing.py' in revision['diff']
    assert '__pycache__' not in revision['diff']
    assert revision['diff'].count('diff --git ')==len(revision['files'])
    checks={c['phase']:c for c in db.list_records('checks')}
    assert checks['baseline']['exit_code']==0
    assert checks['regression-before']['exit_code']!=0
    assert checks['after']['exit_code']==0
    assert checks['after']['patch_hash']==revision['patch_hash']
    assert sample.trusted_tree(__import__('pathlib').Path(workspace['root']))

def test_feature_pauses_until_decision(workspace):
    job=queued('feature'); worker.process_job(job['id'])
    assert db.get('jobs',job['id'])['status']=='Needs input'
    assert not db.list_records('revisions')
    decision=db.list_records('decisions')[0]
    db.mutate('decisions',decision['id'],lambda d:{**d,'selected':'strict'})
    db.mutate('jobs',job['id'],lambda j:{**j,'status':'Queued'})
    worker.process_job(job['id'])
    assert db.get('jobs',job['id'])['status']=='Ready for review'

def test_note_does_not_enter_context_and_revision_preserved(workspace):
    job=queued('bug')
    db.put('notes',{'job_id':job['id'],'text':'PRIVATE TEAM NOTE MUST NOT ENTER PROMPT'})
    worker.process_job(job['id'])
    first=db.get('jobs',job['id'])['current_revision']
    db.mutate('jobs',job['id'],lambda j:{**j,'status':'Queued','revision_instructions':'Document this fixture revision','approval':first})
    worker.process_job(job['id'])
    result=db.get('jobs',job['id'])
    assert result['approval'] is None
    assert result['current_revision']!=first
    assert len(db.list_records('revisions'))==2
    assert 'PRIVATE TEAM' not in json.dumps(db.list_records('runs'))

def test_cancelled_and_duplicate_work_never_publish(workspace):
    job=queued('bug')
    db.mutate('jobs',job['id'],lambda j:{**j,'status':'Cancelled'})
    worker.process_job(job['id'])
    assert not db.list_records('revisions')
    job=queued('security'); worker.process_job(job['id']); worker.process_job(job['id'])
    assert len(db.list_records('revisions'))==1

def test_generic_provider_receives_approved_context_with_deterministic_checks(workspace,monkeypatch):
    repo=db.mutate('repositories',workspace['id'],lambda r:{**r,'is_sample':False})
    job=queued('bug')
    db.put('knowledge',{'repo_id':repo['id'],'status':'team-approved','title':'Integer cents','content':'Use integer cents','scope':'*'})
    calls=[]
    def complete(task,payload,risk):
        calls.append((task,payload))
        if task=='review': return {'approved':True,'findings':[]},{'model':'test transport','usage':None}
        if len(calls)==1: return {'request_context':'config.TAX_RATE tests/test_quote.py'},{'model':'test transport','usage':None}
        return {'edits':[{'path':'pricing.py','content':'def total(items, tax_rate):\n    return 0\n'}],'explanation':'Transport fixture'}, {'model':'test transport','mode':'test','usage':None}
    monkeypatch.setattr(providers,'complete',complete)
    from provider_fixture import adapt_legacy_provider
    adapt_legacy_provider(monkeypatch)
    from provider_fixture import run_with_plan_approval
    run_with_plan_approval(job['id'])
    result=db.get('jobs',job['id'])
    assert result['status']=='Ready for review',result
    assert calls[0][1]['context']['items']
    assert calls[0][1]['approved_knowledge'][0]['title']=='Integer cents'
    assert calls[-1][0]=='review'
    assert calls[1][1]['context']['additional_queries']==['config.TAX_RATE tests/test_quote.py']
    assert db.list_records('checks')[0]['environment']=='deterministic check transport'

def test_provider_failure_and_path_guards(workspace,monkeypatch,tmp_path):
    db.mutate('repositories',workspace['id'],lambda r:{**r,'is_sample':False})
    job=queued('bug')
    def failure(*args): raise ValueError('Provider timeout')
    monkeypatch.setattr(providers,'complete',failure)
    worker.process_job(job['id'])
    assert db.get('jobs',job['id'])['status']=='Failed'
    for path in ('../escape','C:/escape','.git/hooks/run','.env','x\\y'):
        with pytest.raises(ValueError): worker.safe_path(tmp_path,path)
    with pytest.raises(ValueError): providers.validate({'edits':'wrong'})

def test_benchmark_records_comparable_measurements(workspace):
    job=queued('performance'); worker.process_job(job['id'])
    assert db.get('jobs',job['id'])['status']=='Ready for review'
    checks={c['phase']:c for c in db.list_records('checks')}
    before=json.loads(checks['benchmark-before']['stdout'])
    after=json.loads(checks['benchmark-after']['stdout'])
    assert before['workload']==after['workload']
    assert len(before['samples_seconds'])==5
    assert after['median_seconds']>0

def test_cancellation_during_provider_blocks_late_revision(workspace,monkeypatch):
    job=queued('bug')
    original=sample.fixture_response
    def response(*args):
        result=original(*args)
        db.mutate('jobs',job['id'],lambda j:{**j,'status':'Cancelled'})
        return result
    monkeypatch.setattr(sample,'fixture_response',response)
    worker.process_job(job['id'])
    assert db.get('jobs',job['id'])['status']=='Cancelled'
    assert not db.list_records('revisions')
    assert db.list_records('runs')[0]['context']['items']

def test_two_workers_claim_once_and_sample_seed_idempotent(workspace):
    import concurrent.futures
    sample.seed_jobs(workspace)
    assert len(db.list_records('jobs'))==4
    job=queued('bug')
    with concurrent.futures.ThreadPoolExecutor(2) as executor:
        list(executor.map(worker.process_job,[job['id'],job['id']]))
    assert len(db.list_records('revisions'))==1
