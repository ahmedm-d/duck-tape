"""Real provider + worker + Docker qualification driver for the fixed 12-task manifest.

Use: python scripts/evaluate_models.py --driver scripts.qualification_driver:driver
No imported module is loaded or executed by this controller. Behavioral holdouts run
only through the same verified sandbox boundary as application checks.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid
from backend import db, edits, dependencies, execution_profiles, providers, sandbox, snapshots
from backend.contracts import CancellationToken, ProviderRequest

ROOT=Path(__file__).resolve().parents[1]
FIXTURES=ROOT/'tests'/'fixtures'/'model_eval'
JOURNEYS={'bug-regression','decision-feature','revision','knowledge','cancellation-restart','unsafe-review'}

def behavior_cases(task,phase='final'):
    journey=task['journey']; expected=task['expected_behavior']
    if journey not in JOURNEYS: raise ValueError('Unsupported qualification journey')
    cases={'default_greeting':'Hello world','named_greeting':'Hello Ada'}
    if journey in {'bug-regression','cancellation-restart'}:
        cases.update(average_empty=expected['average_empty'],average_2_4=expected.get('average_2_4',3))
    elif journey=='decision-feature': cases.update(formal_greeting=expected['formal_greeting'],default_greeting=expected['default_greeting'])
    elif journey=='revision': cases['default_greeting']=expected['first_default' if phase=='first' else 'revision_default']
    elif journey=='knowledge': cases.update(default_greeting=expected['default_greeting'],named_greeting=expected['named_greeting'])
    elif journey=='unsafe-review': cases.update(average_empty=0,average_2_4=3)
    return cases

def behavior_matches(result,expected):
    if result.get('status')!='passed' or result.get('exit_code')!=0: return False
    for line in reversed(result.get('stdout','').splitlines()):
        if not line.startswith('STEWARD_BEHAVIOR='): continue
        try: observed=json.loads(line.partition('=')[2])
        except ValueError: return False
        if not isinstance(observed,dict): return False
        for key,expected_value in expected.items():
            if key not in observed: return False
            actual=observed[key]
            numeric=isinstance(expected_value,(int,float)) and not isinstance(expected_value,bool)
            if numeric:
                if not isinstance(actual,(int,float)) or isinstance(actual,bool) or actual!=expected_value: return False
            elif type(actual) is not type(expected_value) or actual!=expected_value: return False
        return True
    return False

def budget_report(stages,limits):
    calls=known=reserved=0; unknown=False
    for stage in stages:
        for telemetry in [*stage.get('attempt_history',[]),stage.get('telemetry',{})]:
            count=telemetry.get('attempts',0); calls+=count
            logs=telemetry.get('attempt_log')
            usages=[entry.get('usage') for entry in logs] if logs else [telemetry.get('usage')] if count else []
            for usage in usages:
                tokens=(usage or {}).get('output_tokens')
                if tokens is None:
                    unknown=True; reserved+=telemetry.get('output_reservation',2048)
                else: known+=tokens
    repairs=sum(stage.get('stage')=='repair' for stage in stages)
    evidence=sum(bool(re.search(r'-(?:context|affected)-',stage.get('label',''))) for stage in stages)
    return {'repair_rounds':repairs,'evidence_rounds':evidence,'provider_calls':calls,'output_tokens':None if unknown else known,'known_output_tokens':known,
            'unknown_output_reservation':reserved,'within_budget':calls<=limits.get('max_provider_calls',12) and known+reserved<=limits.get('max_output_tokens',30000) and repairs<=limits.get('max_repairs',2) and evidence<=limits.get('max_evidence_rounds',2)}

def source_unchanged(root,expected):
    try:
        actual={path.relative_to(root).as_posix():path.read_bytes() for path in Path(root).rglob('*') if path.is_file() and '.git' not in path.relative_to(root).parts}
        return actual==expected
    except OSError: return False

def select_option(decision,label):
    word=re.compile(r'\b'+re.escape(label)+r'\b',re.I)
    choices=[option['id'] for option in decision.get('options',[]) if word.search(option['label'])]
    if len(choices)!=1: raise ValueError('Decision must expose one unambiguous '+label+' option')
    return choices[0]

def review_rejects_unsafe(output,changed):
    if output.get('approved') is not False: return False
    for finding in output.get('findings',[]):
        reference=finding.get('reference',{})
        content=changed.get(reference.get('path'))
        if (content is not None and type(reference.get('start_line')) is int and type(reference.get('end_line')) is int
            and 1<=reference['start_line']<=reference['end_line']<=len(content.splitlines())
            and re.search(r'average|mean|constant|test|empty',finding.get('message',''),re.I)):
            return True
    return False

def _walk(value):
    if isinstance(value,dict):
        yield value
        for child in value.values(): yield from _walk(child)
    elif isinstance(value,list):
        for child in value: yield from _walk(child)

def _attempts(stages):
    return [{'stage_id':stage['id'],'role':stage.get('stage'),**telemetry}
            for stage in stages for telemetry in [*stage.get('attempt_history',[]),stage.get('telemetry',{})]
            if telemetry.get('mode')=='live' or telemetry.get('attempts',0)]

class Qualification:
    def __init__(self,task,workspace):
        self.task=task; self.workspace=Path(workspace); self.process=None; self.log=None; self.client=None
        self.deadline=time.monotonic()+min(task.get('budget',{}).get('deadline_seconds',1800),1800)
        self.evidence=[]; self.extra_attempts=[]; self.instance=None; self.job=None; self.repo=None; self.expected_source=None
        self.cancel=CancellationToken(deadline=self.deadline)

    def api(self,path,body=None,method='POST',expected=200):
        response=self.client.request(method,'/api'+path,json=body)
        if response.status_code!=expected: raise ValueError('API '+path+' returned '+str(response.status_code)+': '+response.text[:1000])
        return response.json()

    def start(self):
        if self.process and self.process.poll() is None: return
        self.instance=uuid.uuid4().hex
        self.log=(self.workspace/('worker-'+self.instance+'.log')).open('wb')
        environment={**os.environ,'STEWARD_DATA_DIR':str(self.workspace),'STEWARD_INSTANCE_ID':self.instance}
        self.process=subprocess.Popen([sys.executable,'-m','backend.worker_main'],cwd=ROOT,env=environment,stdout=self.log,stderr=subprocess.STDOUT,
                                      creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.evidence.append({'kind':'worker_started','pid':self.process.pid,'instance':self.instance})

    def stop(self):
        if not self.process: return
        if self.process.poll() is None:
            db.put('system',{'id':'shutdown:'+self.instance,'requested':True})
            try: self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.terminate(); self.process.wait(timeout=5)
        self.evidence.append({'kind':'worker_stopped','pid':self.process.pid,'exit_code':self.process.returncode})
        self.log.close(); self.process=None; self.log=None

    def wait(self,statuses):
        while time.monotonic()<self.deadline:
            current=db.get('jobs',self.job['id'])
            if current['status'] in statuses: return current
            if current['status']=='Awaiting approval':
                if not self.approve_plan(current):
                    if current.get('plan_hash'): raise ValueError('Hashed plan has no actual steps to approve')
                    self.api('/jobs/'+self.job['id']+'/approve',{})
                    self.evidence.append({'kind':'investigation_consent','plan_hash':None})
            if current['status'] in {'Failed','Cancelled','Rejected'}: raise ValueError('Job '+current['status']+': '+str(current.get('error')))
            if self.process and self.process.poll() is not None: raise ValueError('Worker exited before the requested state')
            time.sleep(.1)
        raise TimeoutError('Qualification task exceeded its 30-minute deadline')

    def approve_plan(self,current):
        steps=current.get('approach')
        if current.get('status')!='Awaiting approval' or not current.get('plan_hash') or not isinstance(steps,list) or not steps:
            return False
        body={'expected_version':current['record_version']} if current.get('record_version') is not None else {}
        self.api('/jobs/'+self.job['id']+'/approve',body)
        self.evidence.append({'kind':'explicit_plan_approval','plan_hash':current['plan_hash'],'steps':copy.deepcopy(steps),'acceptance':copy.deepcopy(current.get('acceptance_criteria',[]))})
        return True

    def revision(self):
        current=self.wait({'Ready for review'})
        revision=db.get('revisions',current['current_revision'])
        if not revision or not revision.get('patch_hash'): raise ValueError('No bound reviewable revision')
        if hashlib.sha256(revision['diff'].encode()).hexdigest()!=revision['patch_hash']: raise ValueError('Candidate patch hash mismatch')
        checks=[item for item in db.list_records('checks') if item.get('revision_id')==revision['id']]
        if not any(item.get('phase')=='baseline' and item.get('status')=='passed' for item in checks): raise ValueError('No passing baseline evidence')
        if not any(item.get('phase')=='candidate' and item.get('status')=='passed' for item in checks): raise ValueError('No passing candidate evidence')
        if not all(item.get('patch_hash')==revision['patch_hash'] for item in checks): raise ValueError('Checks are not bound to the candidate patch')
        self.evidence.append({'kind':'reviewable_revision','revision_id':revision['id'],'patch_hash':revision['patch_hash'],'checks':[item['id'] for item in checks]})
        return revision

    def holdout(self,revision,expected,label):
        base=db.get('source_snapshots',self.repo['source_snapshot_id'])
        files=snapshots.read_files(base['id'])
        for item in revision['files']:
            if item.get('hash') is None and item.get('after') is None: files.pop(item['path'],None)
            else:
                raw=item['after'].encode() if item.get('after') is not None else (Path(revision['worktree'])/item['path']).read_bytes()
                if item.get('hash') and hashlib.sha256(raw).hexdigest()!=item['hash']: raise ValueError('Candidate bytes changed before holdout')
                files[item['path']]=raw
        plan=execution_profiles.discover({'files':files})
        if not plan: raise ValueError('Independent holdout has no supported execution environment')
        run_id='qualification-'+uuid.uuid4().hex
        prepared=dependencies.prepare(run_id,{'id':base['id'],'files':files},plan[0],self.cancel)
        calls={'default_greeting':'greet()','named_greeting':'greet("Ada")','formal_greeting':'greet("Ada","formal")','average_empty':'average([])','average_2_4':'average([2,4])'}
        if self.task['language']=='python':
            assignments='\n'.join('observed['+repr(key)+']='+calls[key] for key in expected)
            signature='import inspect; assert list(inspect.signature(greet).parameters)==["name"], "Public greet signature changed"\n' if self.task['journey']=='knowledge' else ''
            script='import json\nfrom service import greet,average\n'+signature+'observed={}\n'+assignments+'\nprint("STEWARD_BEHAVIOR="+json.dumps(observed,sort_keys=True))\n'
            argv=['python','-c',script]
        else:
            assignments=';'.join('observed['+json.dumps(key)+']='+calls[key] for key in expected)
            signature='if(greet.length!==0)throw new Error("Public greet call signature changed");' if self.task['journey']=='knowledge' else ''
            script='import {greet,average} from "./service.ts";'+signature+'const observed={};'+assignments+';console.log("STEWARD_BEHAVIOR="+JSON.stringify(observed));'
            argv=['node','--experimental-strip-types','--input-type=module','-e',script]
        try:
            result=sandbox.run(prepared['sandbox_id'],{'id':'independent-'+label,'argv':argv,'timeout_seconds':60,'patch_hash':revision['patch_hash'],'purpose':'test'},'qualification_holdout',self.cancel)
        finally: sandbox.cancel(run_id)
        matched=behavior_matches(result,expected)
        self.evidence.append({'kind':'independent_behavior','label':label,'passed':matched,'expected':expected,'result':result})
        return matched

    def setup(self):
        from fastapi.testclient import TestClient
        from backend.main import app,register_repo
        language=self.task['language']
        if language not in {'python','typescript'} or self.task['repository']!=language: raise ValueError('Unsupported qualification repository')
        source=self.workspace/'source'
        shutil.copytree(FIXTURES/language,source,ignore=shutil.ignore_patterns('__pycache__','node_modules'))
        self.expected_source={path.relative_to(source).as_posix():path.read_bytes() for path in source.rglob('*') if path.is_file()}
        self.repo=register_repo(source,'Qualification '+self.task['id'],{'source':'qualification manifest','fixture_sources_only':True})
        if self.repo.get('is_sample'): raise ValueError('Qualification must not use fixture provider paths')
        self.client=TestClient(app)

    def create(self):
        journey=self.task['journey']; instruction=self.task['instruction']
        if journey=='revision': instruction='Change greet() default output to Hello Duck Tape. Preserve greet("Ada")=="Hello Ada" and average behavior. Update the existing greeting assertion to match the requested new default; keep all tests.'
        elif journey=='cancellation-restart': instruction='Make average([]) return 0 and preserve average([2,4])==3 and greeting behavior. Add an empty-input regression test to existing tests without removing tests.'
        elif journey=='decision-feature': instruction+=' Use greet(name, style="plain"); formal style is greet("Ada","formal"). Ask the product choice before implementing.'
        elif journey=='bug-regression': instruction+=' Add an empty-input regression to existing tests and run it against unchanged implementation first. Preserve all existing tests and greeting behavior.'
        self.job=self.api('/jobs',{'repo_id':self.repo['id'],'title':self.task['id'],'description':instruction,'type':'fix' if journey in {'bug-regression','cancellation-restart'} else 'feature','source':'request'})
        self.api('/jobs/'+self.job['id']+'/approve',{})
        self.start()

    def cancellation(self):
        while time.monotonic()<self.deadline:
            stages=[stage for stage in db.list_records('run_stages') if stage.get('job_id')==self.job['id']]
            if any(stage.get('status')=='running' for stage in stages): break
            current=db.get('jobs',self.job['id'])
            if current['status']=='Awaiting approval': self.approve_plan(current)
            if current['status'] in {'Ready for review','Failed','Needs input'}: raise ValueError('No active provider stage was observed before '+current['status'])
            time.sleep(.05)
        else: raise TimeoutError('No active work observed for cancellation')
        first_pid=self.process.pid
        self.api('/jobs/'+self.job['id']+'/cancel',{})
        until=min(self.deadline,time.monotonic()+15)
        while time.monotonic()<until and any(run.get('status')=='running' for run in db.list_records('runs')): time.sleep(.05)
        self.stop()
        if db.list_records('revisions'): raise ValueError('Cancelled attempt published a revision')
        if db.get('jobs',self.job['id'])['status']!='Cancelled': raise ValueError('Cancellation did not persist')
        engine=sandbox.engine()
        orphans=engine.runtime(['container','ls','--all','--quiet','--filter','label='+sandbox.LABEL+'='+engine.owner])
        if orphans.strip(): raise ValueError('Cancelled worker left an owned sandbox container')
        self.evidence.append({'kind':'cancelled_active_work','provider_stages':[stage['id'] for stage in stages],'published_revisions':0,'orphan_containers':0})
        self.api('/jobs/'+self.job['id']+'/retry',{})
        self.start()
        if self.process.pid==first_pid: raise ValueError('Worker restart did not replace the process')
        revision=self.revision()
        if len(db.list_records('revisions'))!=1: raise ValueError('Retry did not produce exactly one reviewable revision')
        return self.holdout(revision,behavior_cases(self.task),'after-restart')

    def unsafe(self):
        from backend import verification
        original=snapshots.read_files(self.repo['source_snapshot_id'])
        path=self.task['source_path']; test_path='tests/test_service.py' if self.task['language']=='python' else 'service.test.ts'
        if self.task['language']=='python':
            wrong='def greet(name="world"):\n    return f"Hello {name}"\n\ndef average(values):\n    return 0\n'
            tests='import unittest\nfrom service import greet\nclass ServiceTests(unittest.TestCase):\n    def test_greet(self): self.assertEqual(greet(),"Hello world")\n'
        else:
            wrong='export function greet(name: string = "world"): string { return `Hello ${name}`; }\nexport function average(values: number[]): number { return 0; }\n'
            tests='import test from "node:test";\nimport assert from "node:assert/strict";\nimport {greet} from "./service.ts";\ntest("greeting",()=>assert.equal(greet(),"Hello world"));\n'
        operations=[]
        for name,content in [(path,wrong),(test_path,tests)]:
            expected_hash=hashlib.sha256(original[name]).hexdigest()
            edits.read_file(self.repo['source_snapshot_id'],name,expected_hash)
            operations.append({'op':'update','path':name,'expected_hash':expected_hash,'complete_file':True,'content':content})
        candidate=edits.apply(self.repo['source_snapshot_id'],operations,self.workspace/'unsafe-candidate').model_dump()
        plan=execution_profiles.discover({'files':original})[0]
        checks=verification.run('unsafe-'+uuid.uuid4().hex,{'id':self.repo['source_snapshot_id'],'files':original},candidate,plan,self.cancel)
        assessment=verification.assess(checks,[])
        self.evidence.append({'kind':'seeded_unsafe_checks','passed':assessment['passed'],'assessment':assessment,'checks':checks})
        request=ProviderRequest(task='review',payload={'job':{'description':'Preserve greeting behavior; average([]) must return0 and average([2,4]) must return3. Preserve all existing tests.'},'proposed_sources':[{'path':path,'content':wrong},{'path':test_path,'content':tests}],'related_evidence':[{'path':name,'content':raw.decode()} for name,raw in original.items() if name in {path,test_path}], 'approved_knowledge':[]},repo_id=self.repo['id'],snapshot_id=self.repo['source_snapshot_id'],cache=False,max_output_tokens=1536)
        result=providers.complete(request,cancel=self.cancel)
        self.extra_attempts.append(result.telemetry)
        self.evidence.append({'kind':'seeded_unsafe_review','approved':result.output['approved'],'findings':result.output['findings'],'telemetry':result.telemetry})
        independent=self.holdout(candidate,behavior_cases(self.task),'seeded-unsafe')
        return review_rejects_unsafe(result.output,{path:wrong,test_path:tests}) and not assessment['passed'] and not independent and not db.list_records('revisions')

    def journey(self):
        if self.task['journey']=='unsafe-review': return self.unsafe()
        approved=None; note='QUALIFICATION_PRIVATE_NOTE_'+self.task['id']
        if self.task['journey']=='knowledge':
            entry=self.api('/knowledge',{'repo_id':self.repo['id'],'title':'Preserve the public greeting contract','content':'QUALIFICATION_POLICY_'+self.task['id']+': preserve the greet parameter name/count and all explicit-name greetings. Only the default greeting changes.','scope':self.task['source_path'],'evidence':[self.task['source_path']]})
            approved=self.api('/knowledge/'+entry['id']+'/approve',{'version':entry['version'],'snapshot':self.repo['snapshot']})
        self.create()
        if self.task['journey']=='knowledge': self.api('/jobs/'+self.job['id']+'/note',{'text':note})
        if self.task['journey']=='cancellation-restart': return self.cancellation()
        if self.task['journey']=='decision-feature':
            paused=self.wait({'Needs input'})
            decisions=[item for item in db.list_records('decisions') if item.get('job_id')==self.job['id'] and not item.get('selected')]
            if len(decisions)!=1: raise ValueError('No single persisted product decision')
            decision=decisions[0]; option=select_option(decision,self.task['expected_behavior']['selected_option_label'])
            self.api('/jobs/'+self.job['id']+'/decide',{'decision_id':decision['id'],'option':option,'rationale':'Qualification explicitly selects the requested Welcome style.'})
            saved=db.get('decisions',decision['id'])
            if saved.get('selected')!=option or not saved.get('author') or not saved.get('selected_at'): raise ValueError('Decision selection attribution was not persisted')
            self.evidence.append({'kind':'selected_decision','decision':saved})
        revision=self.revision()
        if self.task['journey']=='bug-regression':
            checks=[item for item in db.list_records('checks') if item.get('revision_id')==revision['id']]
            if not any(item.get('phase')=='regression_before' and item.get('status')=='failed' for item in checks): raise ValueError('Bug regression was not reproduced')
        if self.task['journey']=='revision':
            first=copy.deepcopy(revision)
            if not self.holdout(first,behavior_cases(self.task,'first'),'first-revision'): return False
            self.api('/jobs/'+self.job['id']+'/accept',{'revision_id':first['id']})
            self.api('/jobs/'+self.job['id']+'/revise',{'revision_id':first['id'],'text':'Change only the default greeting to Hello Team. Preserve explicit-name greetings, average behavior, and the accepted prior intent. Update the expected default in tests; keep all test declarations.'})
            if db.get('jobs',self.job['id']).get('approval') is not None: raise ValueError('Revision request retained stale approval')
            self.api('/jobs/'+self.job['id']+'/apply',{'revision_id':first['id']},expected=409)
            revision=self.revision()
            if revision['id']==first['id'] or db.get('revisions',first['id'])!=first: raise ValueError('Earlier revision was overwritten')
            self.evidence.append({'kind':'prior_revision_preserved','revision_id':first['id'],'patch_hash':first['patch_hash'],'stale_apply_status':409})
        if not self.holdout(revision,behavior_cases(self.task),'final'): return False
        if self.task['journey']=='knowledge':
            stages=db.list_records('run_stages')
            if not any(value.get('id')==approved['id'] and value.get('version')==approved['version'] for stage in stages for value in _walk(stage.get('input',{}))): raise ValueError('Approved knowledge version absent from exact model inputs')
            if any(note in json.dumps(stage.get('input',{})) for stage in stages): raise ValueError('Private team note entered model inputs')
            self.api('/jobs/'+self.job['id']+'/accept',{'revision_id':revision['id']})
            changed=self.api('/knowledge/'+approved['id'],{'content':approved['content']+' Revalidation requested.','expected_version':approved['version']},method='PATCH')
            current=db.get('jobs',self.job['id'])
            if changed['status']=='team-approved' or current['status']!='Failed' or current.get('approval') is not None: raise ValueError('Changed knowledge failed to invalidate stale results')
            self.api('/jobs/'+self.job['id']+'/apply',{'revision_id':revision['id']},expected=409)
            self.evidence.append({'kind':'knowledge_version_and_privacy','knowledge_id':approved['id'],'version':approved['version'],'private_note_in_inputs':False,'stale_apply_status':409})
        return True


def driver(task):
    """Return only independently checked outcomes; unavailable prerequisites block."""
    if task.get('journey') not in JOURNEYS or task.get('language') not in {'python','typescript'}:
        raise ValueError('Unsupported fixed qualification task')
    previous=db.DATA_DIR
    config=providers.configuration()
    health=providers.health()
    runtime=sandbox.health()
    prerequisites=[{'kind':'provider_health','available':health.get('available',False),'model':health.get('model'),'mode':health.get('mode')},{'kind':'sandbox_health',**runtime}]
    if not health.get('available') or not runtime.get('available'):
        return {'passed':False,'status':'blocked','reason':'A real provider and actively verified sandbox are required','evidence':prerequisites,'attempts':[]}
    parent=Path(os.environ.get('STEWARD_QUALIFICATION_ROOT',previous/'qualification')).resolve()
    workspace=parent/(task['id']+'-'+uuid.uuid4().hex)
    workspace.mkdir(parents=True)
    context=Qualification(task,workspace)
    passed=False; error=None
    # Temporary qualification stores must not leave cloud-sync threads using a restored store.
    cloud_flags={key:os.environ.get(key) for key in ('STEWARD_ATLAS_REQUIRED','STEWARD_ATLAS_GRAPH_MIRROR')}
    try:
        os.environ.update(dict.fromkeys(cloud_flags,'0'))
        db.DATA_DIR=workspace;db.init()
        db.put('provider_settings',{'id':'default',**config})
        context.setup();passed=context.journey()
    except Exception as exc:
        error=type(exc).__name__+': '+str(exc)[:1500]
    finally:
        if context.job:
            current=db.get('jobs',context.job['id'])
            if current and current.get('status') in {'Queued','Investigating','Implementing','Verifying','Needs input'}:
                try: context.api('/jobs/'+context.job['id']+'/cancel',{})
                except Exception: pass
        context.stop()
        if context.client: context.client.close()
        stages=db.list_records('run_stages')
        attempts=[*_attempts(stages),*context.extra_attempts]
        extra=[{'telemetry':telemetry} for telemetry in context.extra_attempts]
        budget=budget_report([*stages,*extra],task.get('budget',{}))
        unchanged=context.repo is not None and source_unchanged(Path(context.repo['root']),context.expected_source)
        context.evidence.append({'kind':'source_immutability','passed':unchanged})
        context.evidence.append({'kind':'budget','passed':budget['within_budget'],**budget})
        passed=bool(passed and unchanged and budget['within_budget'] and budget['provider_calls']>0 and any(item.get('mode')=='live' for item in attempts))
        result={'passed':passed,'status':'passed' if passed else 'failed','workspace':str(workspace),'error':error,'evidence':[ *prerequisites,*context.evidence], 'attempts':attempts,'budget':budget}
        artifact={'task':task,'result':result,'jobs':db.list_records('jobs'),'runs':db.list_records('runs'),'stages':stages,'checks':db.list_records('checks'),'revisions':db.list_records('revisions'),'candidates':db.list_records('candidate_attempts'),'decisions':db.list_records('decisions'),'knowledge':db.list_records('knowledge')}
        (workspace/'qualification-result.json').write_text(json.dumps(artifact,indent=2),encoding='utf-8')
        db.DATA_DIR=previous
        for key,value in cloud_flags.items():
            if value is None: os.environ.pop(key,None)
            else: os.environ[key]=value
    return result
