"""Calibration boundary regressions; recorded model failures are data, never execution."""
import copy
import hashlib
import json
from pathlib import Path
import pytest
from backend import db,model_schemas,providers,verification,worker
from backend.contracts import ProviderRequest
from test_live_pipeline_contract import generic
from test_provider_contracts import envelope,provider_environment

FAILURES=json.loads((Path(__file__).parent/'fixtures/calibration/acceptance-failures.json').read_text())


def payload(): return copy.deepcopy(FAILURES[0]['payload'])


def valid_output(request_payload):
    file=request_payload['files'][0]
    return {'edits':[{'op':'update','path':file['path'],'expected_hash':file['hash'],'destination':None,'content':file['content'].replace('Hello world','Hello Steward'),'replacements':[],'complete_file':True}],
            'explanation':'Update only requested behavior assertions','coverage':[{'criterion_id':item['id'],'path':file['path'],'test':'test_greet'} for item in request_payload['acceptance_criteria']]}


@pytest.mark.parametrize('case',FAILURES,ids=lambda case:case['case']+'-'+case['label'])
def test_recorded_role_and_hash_failures_are_rejected_by_request_schema(case):
    # The old generic schema accepts these; request-bound generation must not.
    model_schemas.validate(case['output'],'acceptance')
    with pytest.raises(ValueError):model_schemas.validate(case['output'],'acceptance',payload=case['payload'])


def test_update_paths_and_hashes_are_paired_not_independent_enums():
    request=payload(); other={'path':'tests/test_other.py','content':'def test_other(): assert True\n','complete':True}
    other['hash']=hashlib.sha256(other['content'].encode()).hexdigest(); request['files'].append(other)
    good=valid_output(request)
    model_schemas.validate(good,'acceptance',payload=request)
    for path,value in [(request['read_only_sources'][0]['path'],good['edits'][0]['expected_hash']), (other['path'],good['edits'][0]['expected_hash']), (good['edits'][0]['path'],other['hash'])]:
        wrong=copy.deepcopy(good);wrong['edits'][0].update(path=path,expected_hash=value)
        with pytest.raises(ValueError):model_schemas.validate(wrong,'acceptance',payload=request)


def test_whole_file_update_needs_supplied_complete_evidence_but_anchors_remain_valid():
    request=payload(); request['files'][0]['complete']=False
    output=valid_output(request)
    with pytest.raises(ValueError):model_schemas.validate(output,'acceptance',payload=request)
    output['edits'][0].update(content=None,replacements=[{'old':'Hello world','new':'Hello Steward'}],complete_file=False)
    model_schemas.validate(output,'acceptance',payload=request)


def test_new_test_creation_remains_possible_without_existing_test_files():
    request=payload();request['files']=[]
    output={'edits':[{'op':'create','path':'tests/test_new_behavior.py','expected_hash':None,'destination':None,'content':'def test_new_behavior(): assert 2 == 2\n','replacements':[],'complete_file':False}],
            'explanation':'Authored schema fixture only','coverage':[{'criterion_id':item['id'],'path':'tests/test_new_behavior.py','test':'test_new_behavior'} for item in request['acceptance_criteria']]}
    model_schemas.validate(output,'acceptance',payload=request)
    regression={key:value for key,value in output.items() if key!='coverage'}
    model_schemas.validate(regression,'regression',payload=request)
    output['edits'][0]['expected_hash']='a'*64
    with pytest.raises(ValueError):model_schemas.validate(output,'acceptance',payload=request)


@pytest.mark.parametrize('problem',['unknown','duplicate','missing','extra'])
def test_coverage_is_bound_to_current_criteria(problem):
    request=payload();output=valid_output(request)
    if problem=='unknown':output['coverage'][0]['criterion_id']='other-criterion'
    elif problem=='duplicate':output['coverage'][1]['criterion_id']=output['coverage'][0]['criterion_id']
    elif problem=='missing':output['coverage'].pop()
    else:output['coverage'].append(copy.deepcopy(output['coverage'][0]))
    with pytest.raises(ValueError):model_schemas.validate(output,'acceptance',payload=request)


@pytest.mark.parametrize('kind',['openai','ollama'])
def test_same_bound_schema_is_sent_and_enforced_locally(kind,monkeypatch):
    monkeypatch.setenv('STEWARD_MODEL_PROVIDER',kind)
    request=payload();good=valid_output(request);bad=copy.deepcopy(good)
    bad['edits'][0]['path']=request['read_only_sources'][0]['path']
    sent=[]
    responses=iter([bad,good])
    def transport(path,body,**kwargs):
        sent.append(body);return envelope(kind,next(responses))
    monkeypatch.setattr(providers,'request',transport)
    result=providers.complete(ProviderRequest(task='acceptance',payload=request,cache=False,max_output_tokens=512))
    assert result.output==good and result.telemetry['attempts']==2
    bound=model_schemas.schema_for('acceptance',payload=request)
    assert all((body['format'] if kind=='ollama' else body['response_format']['json_schema']['schema'])==bound for body in sent)
    assert model_schemas.SCHEMA_VERSION!='steward-schema-2' and providers.PROMPT_VERSION!='steward-12'


def test_cached_response_is_revalidated_without_rewriting_model_output(monkeypatch):
    request=payload();good=valid_output(request);calls=[]
    monkeypatch.setattr(providers,'request',lambda *args,**kwargs:calls.append(1) or envelope(output=good))
    typed=ProviderRequest(task='acceptance',payload=request,max_output_tokens=512)
    first=providers.complete(typed);key=first.telemetry['cache_key']
    bad=copy.deepcopy(good);bad['edits'][0]['expected_hash']=request['read_only_sources'][0]['hash']
    db.mutate('model_cache',key,lambda cached:{**cached,'result':bad})
    second=providers.complete(typed)
    assert second.output==good and not second.telemetry['cached'] and len(calls)==2


def prepare_fix(job,monkeypatch):
    db.mutate('jobs',job['id'],lambda item:{**item,'type':'fix'})
    original=verification.run
    monkeypatch.setattr(verification,'run',lambda *args,**kwargs:[*original(*args,**kwargs),{'id':'reproduced','check_id':'tests','phase':'regression_before','status':'failed','exit_code':1}])


def test_worker_test_request_drops_conflicting_source_patch_instruction(generic,monkeypatch):
    job,calls,_=generic
    prepare_fix(job,monkeypatch)
    worker.process_job(job['id'])
    assert db.get('jobs',job['id'])['status']=='Ready for review'
    tests=next(request for request in calls if request.task=='regression')
    assert 'complete cumulative patch' not in tests.payload['edit_contract']
    assert 'only test' in tests.payload['edit_contract'].lower()
    implementation=next(request for request in calls if request.task=='implementation')
    assert 'complete cumulative patch' in implementation.payload['edit_contract']


def test_changed_bound_schema_invalidates_resumed_test_stage(generic,monkeypatch):
    job,calls,_=generic
    prepare_fix(job,monkeypatch)
    worker.process_job(job['id'])
    before={stage['id'] for stage in db.list_records('run_stages') if stage['stage']=='regression'}
    schema_for=model_schemas.schema_for
    def changed(task,legacy=False,**kwargs):
        schema=copy.deepcopy(schema_for(task,legacy,**kwargs))
        if task=='regression' and kwargs.get('payload') is not None:schema['description']='Different request-bound schema policy'
        return schema
    monkeypatch.setattr(model_schemas,'schema_for',changed)
    db.mutate('jobs',job['id'],lambda current:{**current,'status':'Queued','current_revision':None,'approval':None})
    worker.process_job(job['id'])
    after={stage['id'] for stage in db.list_records('run_stages') if stage['stage']=='regression'}
    assert len(after-before)==1
    assert len([request for request in calls if request.task=='regression'])==2


def test_corrupt_completed_test_stage_fails_closed_on_resume(generic,monkeypatch):
    job,calls,_=generic
    prepare_fix(job,monkeypatch)
    worker.process_job(job['id'])
    stage=next(stage for stage in db.list_records('run_stages') if stage['stage']=='regression')
    invalid=copy.deepcopy(stage['output']);invalid['edits'][0]['expected_hash']='a'*64
    db.mutate('run_stages',stage['id'],lambda current:{**current,'output':invalid})
    db.mutate('jobs',job['id'],lambda current:{**current,'status':'Queued','current_revision':None,'approval':None})
    worker.process_job(job['id'])
    assert db.get('jobs',job['id'])['status']=='Failed'
    assert len([request for request in calls if request.task=='regression'])==1
    assert len(db.list_records('candidate_attempts'))==1


def test_planning_requests_explicit_product_choices_before_concrete_plan():
    instruction=providers._system('planning')
    assert 'approved_decisions' in instruction and 'return that decision before a concrete plan' in instruction


def test_legacy_regression_contract_remains_compatible():
    model_schemas.validate({'edits':[{'path':'tests/test_new.py','content':'assert True\n'}],'explanation':'Legacy'},'regression',legacy=True,payload={})
