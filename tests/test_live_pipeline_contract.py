"""Generic controller contracts use deterministic provider/check transports, not live claims."""
import hashlib
from pathlib import Path
import pytest
from backend import db, repository, snapshots, worker, providers, verification, execution_profiles
from backend.contracts import ProviderResult


@pytest.fixture
def generic(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path/'store'); db.init()
    root=tmp_path/'source'; root.mkdir()
    (root/'app.py').write_bytes(b'def value():\n    return 1\n')
    (root/'requirements.txt').write_text('')
    index=repository.index_repository(root)
    source=snapshots.capture('generic',root,{})
    repo=db.put('repositories',{'id':'generic','name':'generic','root':str(root),'index':index,'snapshot':index['snapshot'],'source_snapshot_id':source['id'],'is_sample':False})
    job=db.put('jobs',{'repo_id':repo['id'],'title':'Fix value','description':'Return 2 and preserve function name.','status':'Queued','base_snapshot':repo['snapshot'],'source_snapshot_id':source['id'],'preparation_approved':True,'risk':'low'})
    calls=[]
    def complete(request,cancel=None):
        calls.append(request)
        if request.task=='regression': output={'edits':[{'op':'create','path':'tests/test_value.py','expected_hash':None,'destination':None,'content':'import unittest\nfrom app import value\nclass ValueTests(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(value(), 2)\n','replacements':[],'complete_file':False}],'explanation':'Reproduce wrong value'}
        elif request.task=='investigation': output={'summary':'value returns 1','evidence':[{'path':'app.py','start_line':1,'end_line':2}],'missing':[]}
        elif request.task=='planning': output={'steps':[{'description':'Change return','paths':['app.py']}],'acceptance':['value returns 2']}
        elif request.task=='review': output={'approved':True,'findings':[]}
        else: output={'edits':[{'op':'update','path':'app.py','expected_hash':hashlib.sha256(b'def value():\n    return 1\n').hexdigest(),'destination':None,'content':None,'replacements':[{'old':'return 1','new':'return 2'}],'complete_file':False}],'explanation':'Fix value'}
        return ProviderResult(output=output,telemetry={'model':'deterministic transport','mode':'test','attempts':1,'usage':{'input_tokens':1,'output_tokens':1},'attempt_log':[]})
    monkeypatch.setattr(providers,'complete',complete)
    monkeypatch.setattr(execution_profiles,'discover',lambda snapshot:[{'profile':'python-unittest','commands':[{'id':'tests','argv':['python','-m','unittest'],'required':True}],'fingerprint':'fixed'}])
    def verify(run,base,candidate,plan,cancel=None):
        return [{'id':'baseline-'+run,'check_id':'tests','phase':'baseline','status':'passed','exit_code':0}, {'id':'after-'+run,'check_id':'tests','phase':'candidate','status':'passed','exit_code':0}]
    monkeypatch.setattr(verification,'run',verify)
    return job,calls,complete


def test_persists_real_investigation_plan_and_fingerprints(generic):
    job,calls,_=generic
    worker.process_job(job['id'])
    updated=db.get('jobs',job['id'])
    assert updated['status']=='Ready for review',updated
    assert [call.task for call in calls]==['investigation','planning','implementation','review']
    run=db.list_records('runs')[0]
    assert run['investigation']['summary']=='value returns 1'
    assert run['plan']['acceptance']==['value returns 2']
    assert all(stage['input_fingerprint'] and stage['status']=='complete' for stage in db.list_records('run_stages'))
    assert db.list_records('revisions')[0]['source_snapshot_id']==job['source_snapshot_id']


def test_approve_first_stops_after_plan_before_edits(generic):
    job,calls,_=generic
    db.mutate('jobs',job['id'],lambda item:{**item,'requires_approval':True,'preparation_approved':False})
    worker.process_job(job['id'])
    assert db.get('jobs',job['id'])['status']=='Awaiting approval'
    assert [call.task for call in calls]==['investigation','planning']
    assert not db.list_records('revisions')


def test_check_failure_repairs_with_actual_feedback(generic,monkeypatch):
    job,calls,_=generic
    invocations=[]
    def verify(run,base,candidate,plan,cancel=None):
        invocations.append(1)
        return [{'id':'b'+str(len(invocations)),'check_id':'tests','phase':'baseline','status':'passed','exit_code':0}, {'id':'a'+str(len(invocations)),'check_id':'tests','phase':'candidate','status':'failed' if len(invocations)==1 else 'passed','exit_code':1 if len(invocations)==1 else 0,'stdout':'assert actual == expected'}]
    monkeypatch.setattr(verification,'run',verify)
    worker.process_job(job['id'])
    assert db.get('jobs',job['id'])['status']=='Ready for review'
    repairs=[request for request in calls if request.task=='repair']
    assert len(repairs)==1
    assert 'assert actual == expected' in str(repairs[0].payload['feedback'])


def test_two_repairs_stop_and_keep_inspectable_attempts(generic,monkeypatch):
    job,calls,_=generic
    monkeypatch.setattr(verification,'run',lambda *a,**kw:[{'id':'b','check_id':'tests','phase':'baseline','status':'passed'}, {'id':'a','check_id':'tests','phase':'candidate','status':'failed','stdout':'still broken'}])
    worker.process_job(job['id'])
    assert db.get('jobs',job['id'])['status']=='Failed'
    assert len([call for call in calls if call.task=='repair'])==2
    assert not db.list_records('revisions')
    assert len(db.list_records('candidate_attempts'))==3


def test_unavailable_sandbox_preserves_an_unverified_exportable_patch(generic,monkeypatch):
    from backend import sandbox,main
    job,calls,_=generic
    def unavailable(*args,**kwargs):
        raise sandbox.SandboxUnavailable('Docker is unavailable; arbitrary source was not executed')
    monkeypatch.setattr(verification,'run',unavailable)
    worker.process_job(job['id'])
    detail=main.job_detail(job['id'])
    assert detail['status']=='Failed' and 'Docker is unavailable' in detail['error']
    assert not detail['revisions'] and not detail.get('approval')
    run=detail['runs'][-1]
    assert 'return 2' in run['candidate_diff']
    assert run['candidate_patch_hash']==hashlib.sha256(run['candidate_diff'].encode()).hexdigest()
    assert run['candidate_files'][0]['path']=='app.py'
    assert not any(call.task=='review' for call in calls)


def test_cancelled_provider_never_publishes(generic,monkeypatch):
    job,_,complete=generic
    def cancel(request,token=None):
        result=complete(request,token)
        if request.task=='implementation': db.mutate('jobs',job['id'],lambda item:{**item,'status':'Cancelled','lease_token':None})
        return result
    monkeypatch.setattr(providers,'complete',cancel)
    worker.process_job(job['id'])
    assert db.get('jobs',job['id'])['status']=='Cancelled'
    assert not db.list_records('revisions')


def test_conflicting_approved_knowledge_pauses_and_choice_is_job_local(generic):
    from backend import workflow
    job,calls,_=generic
    for identity,value in [('one','integer'),('two','float')]:
        db.put('knowledge',{'id':identity,'repo_id':'generic','status':'team-approved','title':identity,'content':value,'scope':'app.py','version':1,'constraint':{'key':'money','value':value}})
    worker.process_job(job['id'])
    assert db.get('jobs',job['id'])['status']=='Needs input'
    assert not calls
    decision=db.list_records('decisions')[0]
    workflow.transition(job['id'],'decide',{'decision_id':decision['id'],'option':'one'})
    worker.process_job(job['id'])
    assert db.get('jobs',job['id'])['status']=='Ready for review'
    implementation=next(request for request in calls if request.task=='implementation')
    assert [entry['id'] for entry in implementation.payload['approved_knowledge']]==['one']
    assert db.get('knowledge','two')['status']=='team-approved'


def test_unsupported_review_remains_inspectable_after_one_adjudication(generic,monkeypatch):
    job,calls,complete=generic
    def unsupported(request,cancel=None):
        if request.task=='review':
            calls.append(request)
            return ProviderResult(output={'approved':False,'findings':[{'message':'unsupported','reference':{'path':'missing.py','start_line':1,'end_line':2}}]},telemetry={'attempts':1,'usage':None,'mode':'test'})
        return complete(request,cancel)
    monkeypatch.setattr(providers,'complete',unsupported)
    worker.process_job(job['id'])
    assert db.get('jobs',job['id'])['status']=='Failed'
    assert len([request for request in calls if request.task=='review'])==2
    assert db.list_records('runs')[0]['review_adjudication']['original']['findings']
    assert not db.list_records('revisions')


def test_bug_cannot_publish_without_required_regression(generic):
    job,calls,_=generic
    db.mutate('jobs',job['id'],lambda item:{**item,'type':'fix'})
    worker.process_job(job['id'])
    assert db.get('jobs',job['id'])['status']=='Failed'
    assert not db.list_records('revisions')
    assert len([request for request in calls if request.task=='repair'])==2


def test_lost_lease_during_provider_preserves_new_owner_and_blocks_publication(generic,monkeypatch):
    job,_,complete=generic
    def supersede(request,cancel=None):
        result=complete(request,cancel)
        if request.task=='implementation':
            db.mutate('jobs',job['id'],lambda item:{**item,'lease_token':'new-owner','status':'Implementing'})
        return result
    monkeypatch.setattr(providers,'complete',supersede)
    worker.process_job(job['id'])
    assert db.get('jobs',job['id'])['lease_token']=='new-owner'
    assert not db.list_records('revisions')


def test_decision_resume_replans_without_duplicate_cards(generic,monkeypatch):
    from backend import workflow
    job,calls,complete=generic
    def choose(request,cancel=None):
        if request.task=='planning' and not request.payload.get('approved_decisions'):
            calls.append(request)
            return ProviderResult(output={'decision':{'title':'Return value','options':[{'id':'two','label':'Two','tradeoff':'Requested return'},{'id':'three','label':'Three','tradeoff':'Different return'}],'recommendation':'two'}},telemetry={'attempts':1,'usage':None,'mode':'test'})
        return complete(request,cancel)
    monkeypatch.setattr(providers,'complete',choose)
    worker.process_job(job['id']);worker.process_job(job['id'])
    assert len(db.list_records('decisions'))==1
    decision=db.list_records('decisions')[0]
    workflow.transition(job['id'],'decide',{'decision_id':decision['id'],'option':'two'})
    worker.process_job(job['id'])
    assert db.get('jobs',job['id'])['status']=='Ready for review'
    assert len(db.list_records('decisions'))==1


def test_unrecognized_required_build_script_cannot_be_silently_omitted(generic,monkeypatch):
    job,calls,_=generic
    monkeypatch.setattr(execution_profiles,'discover',lambda source:[{'profile':'node-npm','commands':[{'id':'test','argv':['node','--test']}],'unfamiliar_commands':{'build':'custom-builder build','deploy':'custom-deployer'}}])
    worker.process_job(job['id'])
    assert db.get('jobs',job['id'])['status']=='Failed'
    assert 'build' in db.get('jobs',job['id'])['error']
    assert not any(request.task=='implementation' for request in calls)


def test_regression_role_error_gets_one_bounded_correction(generic,monkeypatch):
    job,calls,complete=generic
    db.mutate('jobs',job['id'],lambda item:{**item,'type':'fix'})
    original=verification.run
    def verify(*args,**kwargs):
        return [*original(*args,**kwargs),{'id':'reproduced','check_id':'tests','phase':'regression_before','status':'failed','exit_code':1}]
    monkeypatch.setattr(verification,'run',verify)
    bad=False
    def confused(request,cancel=None):
        nonlocal bad
        result=complete(request,cancel)
        if request.task=='regression' and not bad:
            bad=True
            # CREATE stays generically expressible; controller guards still reject source paths.
            return ProviderResult(output={'edits':[{'op':'create','path':'app.py','expected_hash':None,'destination':None,'content':'def value(): return 2\n','replacements':[],'complete_file':False}],'explanation':'Wrong role'},telemetry=result.telemetry)
        return result
    monkeypatch.setattr(providers,'complete',confused)
    worker.process_job(job['id'])
    current=db.get('jobs',job['id'])
    assert current['status']=='Ready for review',current
    regression=[request for request in calls if request.task=='regression']
    assert len(regression)==2
    assert all(item['path']!='app.py' for item in regression[0].payload['files'])
    assert any(item['path']=='app.py' for item in regression[0].payload['read_only_sources'])
    assert 'only test files' in str(regression[1].payload['feedback'])


def test_decision_resumption_does_not_reset_total_provider_budget(generic,monkeypatch):
    from backend import workflow
    job,calls,complete=generic
    def another_decision(request,cancel=None):
        if request.task=='planning':
            calls.append(request)
            count=len(request.payload.get('approved_decisions',[]))
            return ProviderResult(output={'decision':{'title':'Choice '+str(count),'options':[{'id':'a','label':'A','tradeoff':'First'},{'id':'b','label':'B','tradeoff':'Second'}],'recommendation':'a'}},telemetry={'attempts':1,'usage':{'output_tokens':1}})
        return complete(request,cancel)
    monkeypatch.setattr(providers,'complete',another_decision)
    for _ in range(8):
        worker.process_job(job['id'])
        current=db.get('jobs',job['id'])
        if current['status']!='Needs input': break
        card=db.get('decisions',current['decision_id'])
        workflow.transition(job['id'],'decide',{'decision_id':card['id'],'option':'a'})
    current=db.get('jobs',job['id'])
    assert current['status']=='Failed',current
    assert 'budget exhausted' in current['error']
    assert len(calls)==12


@pytest.mark.parametrize('kind',['security','performance'])
def test_specialized_claims_need_independent_evidence(generic,monkeypatch,kind):
    from backend import signals
    job,calls,_=generic
    db.mutate('jobs',job['id'],lambda item:{**item,'type':kind})
    evidence=[]
    def security(current,base,candidate,cancel=None):
        evidence.append((current,base,candidate))
        return {'id':'security-proof','check_id':'security-remediation','phase':'advisory_after','required':True,'status':'unavailable','stdout':'Missing exact before/after advisory evidence'}
    monkeypatch.setattr(signals,'verify_security',security,raising=False)
    worker.process_job(job['id'])
    current=db.get('jobs',job['id'])
    assert current['status']=='Failed',current
    assert not db.list_records('revisions')
    assert not any(request.task=='review' for request in calls)
    if kind=='security':
        assert evidence and evidence[0][1]['id']==job['source_snapshot_id']
        assert evidence[0][2]['files']
        assert any(check.get('check_id')=='security-remediation' for check in db.list_records('checks'))
    else:
        assert 'benchmark' in str(db.list_records('candidate_attempts'))


@pytest.mark.parametrize('kind',['security','performance'])
def test_specialized_independent_evidence_allows_review(generic,monkeypatch,kind):
    from backend import signals
    job,calls,_=generic
    db.mutate('jobs',job['id'],lambda item:{**item,'type':kind})
    monkeypatch.setattr(signals,'verify_security',lambda *args:{'id':'security-proof','check_id':'security-remediation','phase':'advisory_after','required':True,'status':'passed','evidence':{'before_affected':True,'after_affected':False}},raising=False)
    original=verification.run
    def measured(*args,**kwargs):
        result=original(*args,**kwargs)
        if kind=='performance':
            result.extend({'id':phase,'phase':phase,'check_id':'benchmark','status':'passed','environment':{'image':'deterministic sandbox identity'},'benchmark':{'workload':'fixed inputs','samples_seconds':samples}} for phase,samples in [('benchmark_before',[2,2.1,2.2]),('benchmark_after',[1,1.1,1.2])])
        return result
    monkeypatch.setattr(verification,'run',measured)
    worker.process_job(job['id'])
    current=db.get('jobs',job['id'])
    assert current['status']=='Ready for review',current
    assert any(request.task=='review' for request in calls)


@pytest.mark.parametrize('executed',[True,False])
def test_feature_criteria_require_executed_generated_tests(generic,monkeypatch,executed):
    job,calls,complete=generic
    db.mutate('jobs',job['id'],lambda item:{**item,'type':'feature'})
    def accepts(request,cancel=None):
        if request.task!='acceptance': return complete(request,cancel)
        calls.append(request)
        return ProviderResult(output={'edits':[{'op':'create','path':'tests/test_value.py','expected_hash':None,'destination':None,'content':'import unittest\nfrom app import value\nclass ValueTests(unittest.TestCase):\n    def test_requested_value(self):\n        self.assertEqual(value(),2)\n','replacements':[],'complete_file':False}],
                                      'coverage':[{'criterion_id':'criterion-1','path':'tests/test_value.py','test':'test_requested_value'}],
                                      'explanation':'Check requested return'},telemetry={'attempts':1,'usage':None})
    monkeypatch.setattr(providers,'complete',accepts)
    original=verification.run
    def verify(run,base,candidate,plan,cancel=None):
        result=original(run,base,candidate,plan,cancel)
        commands=[command for command in plan['commands'] if command.get('candidate_only')]
        assert commands and all(command['required'] and 'test_value.py' in command['argv'] for command in commands)
        return [*result,*[{'id':run+'-'+command['id'],'check_id':command['id'],'phase':'candidate','status':'passed','exit_code':0,'test_count':1 if executed else 0,'stdout':'test_requested_value ... ok' if executed else 'No tests ran','skipped_count':0} for command in commands]]
    monkeypatch.setattr(verification,'run',verify)
    worker.process_job(job['id'])
    current=db.get('jobs',job['id'])
    assert current['status']==('Ready for review' if executed else 'Failed'),current
    assert bool(db.list_records('revisions')) is executed
    assert any(request.task=='review' for request in calls) is executed
    if executed:
        revision=db.list_records('revisions')[0]
        assert revision['acceptance_coverage'][0]['criterion_id']=='criterion-1'
        assert revision['assessment']['advisories'][0]['status']=='validated'
