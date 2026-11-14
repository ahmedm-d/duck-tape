"""Adapt older deterministic provider tests to the typed controller, with labelled fake checks."""
import hashlib
from backend import providers, verification
from backend.contracts import ProviderResult


def run_with_plan_approval(job_id):
    """Explicitly review and approve generated plans in legacy transport tests."""
    from backend import db,worker,workflow
    if db.get('jobs',job_id)['status']=='Awaiting approval':
        workflow.transition(job_id,'approve',{})
    worker.process_job(job_id)
    planned=db.get('jobs',job_id)
    if planned['status']=='Awaiting approval':
        assert planned.get('plan_hash') and planned.get('approach')
        workflow.transition(job_id,'approve',{})
        worker.process_job(job_id)


def adapt_legacy_provider(monkeypatch):
    legacy = providers.complete
    def complete(request, cancel=None):
        payload = request.payload
        if request.task == 'regression':
            original=next(item for item in payload['files'] if '/test_' in item['path'] or item['path'].startswith('test_'))
            output={'edits':[{'op':'update','path':original['path'],'expected_hash':original['hash'],'destination':None,'content':original['content']+'\n# Deterministic regression transport fixture\n','replacements':[],'complete_file':True}],'explanation':'Deterministic regression edit transport'}
            telemetry={'mode':'test','attempts':1,'usage':None}
        elif request.task == 'investigation':
            output = {'summary':'Deterministic investigation transport','evidence':[], 'missing':[]}
            telemetry = {'mode':'test','attempts':1,'usage':None}
        elif request.task == 'planning':
            output = {'steps':[{'description':'Deterministic plan transport','paths':[item['path'] for item in payload['context']['items']]}], 'acceptance':['Deterministic fixture checks']}
            telemetry = {'mode':'test','attempts':1,'usage':None}
        else:
            output,telemetry = legacy('review' if request.task=='review' else 'implementation',payload,request.risk)
            telemetry = {**telemetry,'attempts':1}
            if 'edits' in output:
                originals = {item['path']:item for item in payload['files']}
                output = {**output,'edits':[{'op':'update' if edit['path'] in originals else 'create', 'path':edit['path'],
                                           'expected_hash':originals[edit['path']]['hash'] if edit['path'] in originals else None,
                                           'destination':None,'content':edit['content'],'replacements':[],
                                           'complete_file':edit['path'] in originals} for edit in output['edits']]}
        return ProviderResult(output=output,telemetry=telemetry)
    monkeypatch.setattr(providers,'complete',complete)
    def checks(run,base,candidate,plan,cancel=None):
        return [{'id':run+'-before','check_id':'tests','phase':'baseline','status':'passed','exit_code':0,'environment':'deterministic check transport'},
                {'id':run+'-regression','check_id':'tests','phase':'regression_before','status':'failed','exit_code':1,'environment':'deterministic check transport'},
                {'id':run+'-after','check_id':'tests','phase':'candidate','status':'passed','exit_code':0,'environment':'deterministic check transport'}]
    monkeypatch.setattr(verification,'run',checks)
