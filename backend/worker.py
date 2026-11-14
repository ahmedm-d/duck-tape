"""Persistent leased jobs. Host execution is restricted to reviewed sample fixtures."""
import hashlib
import json
import os
import ast
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path, PurePosixPath
from . import db, providers, sample

ACTIVE = {'Investigating','Implementing','Verifying'}
TERMINAL = {'Cancelled','Rejected','Completed'}
_stop = threading.Event()
_thread = None
_processes = {}
_tokens = {}
_owned_jobs = {}

class LostLease(Exception):
    pass


def acceptance_commands(coverage,profile,files):
    """Construct fixed runner argv from validated test references, never model commands."""
    commands=[]
    for reference in coverage:
        path=reference['path']; name=reference['test']
        safe_path(db.DATA_DIR/'acceptance',path)
        if not name or len(name)>128 or any(ord(char)<32 for char in name):
            raise ValueError('Acceptance test reference must be a concrete test name')
        content=files.get(path)
        if isinstance(content,bytes): content=content.decode('utf-8')
        if not isinstance(content,str) or name not in content:
            raise ValueError('Acceptance test reference does not resolve to changed test source')
        kind=profile.get('profile')
        if kind in ('python-unittest','python-pytest'):
            if not re.fullmatch(r'test_[A-Za-z0-9_]+',name) or not path.endswith('.py'):
                raise ValueError('Python acceptance reference requires a test function name')
            tree=ast.parse(content)
            functions=[node for node in ast.walk(tree) if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name==name]
            if len(functions)!=1 or not any(isinstance(node,ast.Assert) or isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr.startswith('assert') for node in ast.walk(functions[0])):
                raise ValueError('Acceptance test must contain an assertion')
            parents={child:parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
            node=parents[functions[0]]; selector=[name]
            while isinstance(node,ast.ClassDef):
                selector.insert(0,node.name); node=parents[node]
            if not isinstance(node,ast.Module): raise ValueError('Acceptance function must be directly discoverable')
            argv=['python','-m','pytest','-v',path+'::'+'::'.join(selector)] if kind=='python-pytest' else ['python','-m','unittest','discover','-s',str(PurePosixPath(path).parent),'-p',PurePosixPath(path).name,'-k','*.'+'.'.join(selector),'-v']
        elif kind=='node-npm':
            if not re.search(r'\b(?:test|it)\s*\(\s*([\'\"])'+re.escape(name)+r'\1',content) or not re.search(r'\b(?:assert|expect)\s*[.(]',content):
                raise ValueError('JS acceptance reference requires a literal test label and assertion')
            runner=next((command['argv'][0] for command in profile['commands'] if command.get('kind',command.get('purpose'))=='test'),None)
            if runner=='node':
                argv=['node',*(['--experimental-strip-types'] if path.endswith('.ts') else []),'--test','--test-reporter=tap','--test-name-pattern','^'+re.escape(name)+'$',path]
            elif runner=='./node_modules/.bin/vitest': argv=[runner,'run',path,'--reporter','verbose','-t','^'+re.escape(name)+'$']
            elif runner=='./node_modules/.bin/jest': argv=[runner,'--runInBand','--runTestsByPath',path,'--verbose','--testNamePattern','^'+re.escape(name)+'$']
            else: raise ValueError('Unsupported acceptance test runner')
        else: raise ValueError('Unsupported acceptance execution profile')
        commands.append({'id':'acceptance-'+reference['criterion_id'],'argv':argv,'purpose':'test','kind':'test','required':True,'candidate_only':True,'timeout_seconds':300})
    return commands


def acceptance_observed(record,reference,profile):
    if record.get('status')!='passed' or type(record.get('test_count')) is not int or record['test_count']<=record.get('skipped_count',0): return False
    output=record.get('stdout',''); name=re.escape(reference['test'])
    if profile['profile']=='python-unittest':
        return not record.get('skipped_count',0) and bool(re.search(r'^'+name+r'(?:\s|\().*\.\.\. ok\s*$',output,re.M))
    if profile['profile']=='python-pytest':
        return not record.get('skipped_count',0) and bool(re.search(r'::'+name+r'(?:\[[^\n]*\])?\s+PASSED\b',output))
    if any(command['argv'][0]=='node' and command.get('kind',command.get('purpose'))=='test' for command in profile['commands']):
        return any(re.fullmatch(r'\s*ok \d+ (?:- )?'+name+r'(?:\s*#.*)?',line) and not re.search(r'#\s*(?:SKIP|TODO)\b',line,re.I) for line in output.splitlines())
    return bool(re.search(r'[✓✔√]\s+(?:[^\n]* > )?'+name+r'(?:\s+\([^\n]*\))?\s*$',output,re.M))

def guard(job_id, token, status=None, **fields):
    def update(job):
        if job.get('lease_token') != token or job['status'] not in ACTIVE or job.get('lease_until',0) <= time.time():
            raise LostLease()
        job.update(fields)
        if status:
            job['status']=status
        job['lease_until']=time.time()+180 if job['status'] in ACTIVE else 0
        if job['status'] not in ACTIVE:
            job['lease_token']=None
        return job
    return db.mutate('jobs',job_id,update)

def cancel_job(job_id):
    if job_id in _tokens:
        _tokens[job_id].cancel()
    process=_processes.get(job_id)
    if process and process.poll() is None:
        process.kill()

def check(job_id, token, root, phase, script, command):
    guard(job_id,token)
    env={k:v for k,v in os.environ.items() if k.upper() in ('SYSTEMROOT','WINDIR','TEMP','TMP')}
    start=time.monotonic()
    process=subprocess.Popen([sys.executable,'-I','-B','-c',script],cwd=root,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env=env)
    _processes[job_id]=process
    output=''
    try:
        while True:
            try:
                output,_=process.communicate(timeout=.2)
                break
            except subprocess.TimeoutExpired:
                guard(job_id,token)
                if time.monotonic()-start>20:
                    process.kill()
                    output,_=process.communicate()
                    output+='\nCheck timed out after 20 seconds'
                    break
    except BaseException:
        process.kill(); process.communicate()
        raise
    finally:
        _processes.pop(job_id,None)
    guard(job_id,token)
    return {'id':str(uuid.uuid4()),'job_id':job_id,'phase':phase,'command':command,'exit_code':process.returncode,'duration':time.monotonic()-start,'stdout':output[-16000:],'status':'passed' if process.returncode==0 else 'failed','environment':sys.version.split()[0], 'created_at':db.now()}

TEST_SCRIPT='import sys,unittest; sys.path.insert(0,"."); result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover("tests")); sys.exit(not result.wasSuccessful())'
BENCH_SCRIPT='import sys,time,json,statistics; sys.path.insert(0,"."); from search import unique_labels; values=[str(i%2500) for i in range(5000)]; samples=[]\nfor _ in range(5):\n start=time.perf_counter(); result=unique_labels(values); samples.append(time.perf_counter()-start)\nassert len(result)==2500\nprint(json.dumps({"workload":"5000 labels, 2500 unique; 5 repetitions","median_seconds":statistics.median(samples),"samples_seconds":samples}))'

def safe_path(root, name):
    path=PurePosixPath(name)
    devices={'con','prn','aux','nul',*(f'com{i}' for i in range(1,10)),*(f'lpt{i}' for i in range(1,10))}
    if not name or '\\' in name or ':' in name or path.is_absolute() or any(p in ('..','') or p.rstrip(' .')!=p or p.casefold()=='.git' or p.split('.')[0].casefold() in devices or any(ord(c)<32 for c in p) for p in path.parts) or name.startswith('/'):
        raise ValueError('Unsafe edit path')
    target=(root / name).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError('Edit escapes checkout')
    if any(p.lower().startswith('.env') or p.lower() in ('.aws','.ssh') or p.lower().endswith(('.pem','.key')) for p in path.parts):
        raise ValueError('Secret paths cannot be edited')
    return target

def publish(job_id, token, revision, checks):
    # Publish the revision and state together, so cancellation cannot leave a late patch.
    with db.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        current=json.loads(con.execute('SELECT data FROM records WHERE kind=? AND id=?',('jobs',job_id)).fetchone()[0])
        if current.get('lease_token')!=token or current['status']!='Verifying' or current.get('lease_until',0)<=time.time():
            raise LostLease()
        for kind, records in [('revisions',[revision]),('checks',checks)]:
            for record in records:
                con.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)',(kind,record['id'],json.dumps(record)))
        current.update(status='Ready for review',current_revision=revision['id'],approval=None,lease_token=None,lease_until=0,error=None,updated_at=db.now())
        db.put_in(con,'jobs',current)
        for row in con.execute("SELECT data FROM records WHERE kind='runs'").fetchall():
            run=json.loads(row[0])
            if run.get('job_id')==job_id and run.get('lease_token')==token and run.get('status')=='running':
                db.put_in(con,'runs',{**run,'status':'complete','revision_id':revision['id'],'finished_at':db.now()})
        db.put_in(con,'events',{'message':'Patch revision ready for review','job_id':job_id,'repo_id':current['repo_id'],'author':'Duck Tape'})

def _process_legacy_fixture(job_id):
    token=str(uuid.uuid4())
    def claim(job):
        if job['status']!='Queued':
            raise LostLease()
        if job.get('requires_approval') and not job.get('preparation_approved'):
            raise ValueError('Preparation approval required')
        job.update(status='Investigating',lease_token=token,lease_until=time.time()+180,error=None)
        return job
    try:
        job=db.mutate('jobs',job_id,claim)
    except (LostLease,ValueError):
        return
    _owned_jobs[job_id]=token
    run=db.put('runs',{'job_id':job_id,'status':'running','lease_token':token,'started_at':db.now(),'base_snapshot':job['base_snapshot']})
    checks=[]
    try:
        from .retrieval import retrieve
        repo=db.get('repositories',job['repo_id'])
        if job['base_snapshot']!=repo['snapshot']:
            raise ValueError('Repository snapshot changed; refresh this job before retrying')
        knowledge=[k for k in db.list_records('knowledge') if k.get('repo_id')==repo['id'] and k.get('status')=='team-approved']
        decisions=[d for d in db.list_records('decisions') if d.get('job_id')==job_id]
        decisions=[{**d,'selected':job.get('selected_decisions',{}).get(d['id'],d.get('selected'))} for d in decisions]
        context=retrieve(repo['index'],job['title']+' '+job['description'],knowledge=knowledge)
        context.update(snapshot=repo['snapshot'],repo_id=repo['id'],overview=repo['index'].get('overview',''))
        knowledge=context.get('knowledge',[])
        run=db.put('runs',{**run,'context':context})
        files={f['path']:f['content'] for f in repo['index']['files'] if f.get('status')=='indexed' and isinstance(f.get('content'),str)}
        fixture=bool(repo.get('is_sample') and job.get('scenario') in {s[0] for s in sample.SCENARIOS})
        payload={'task':'implementation','job':{k:job.get(k) for k in ('id','title','description','revision_instructions','current_revision')},'snapshot':repo['snapshot'],'context':context,'approved_decisions':decisions,'approved_knowledge':knowledge}
        prior=None
        if job.get('current_revision'):
            prior=db.get('revisions',job['current_revision'])
            if not prior or prior['job_id']!=job_id or prior['base_snapshot']!=repo['snapshot']:
                raise ValueError('Prior revision does not match this job and snapshot')
            payload['previous_revision']={k:prior[k] for k in ('id','number','diff','files','patch_hash')}
            payload['revision_contract']='Preserve the accepted intent of previous changes while addressing the revision instructions. Return the complete cumulative patch against the original snapshot.'
        if fixture:
            response=sample.fixture_response(job,files,decisions)
            telemetry={'model':'reviewed sample fixture','mode':'offline sample','usage':None,'latency':0,'routing_reason':'Explicit bundled sample scenario only'}
        else:
            response,telemetry=providers.complete('implementation',payload,job.get('risk','medium'))
            requests=[]
            for attempt in range(2):
                if 'request_context' not in response:
                    break
                guard(job_id,token)
                query=response['request_context']
                additional=retrieve(repo['index'],query,knowledge=[k for k in db.list_records('knowledge') if k.get('repo_id')==repo['id'] and k.get('status')=='team-approved'])
                items={item['path']:item for item in context['items']}
                items.update({item['path']:item for item in additional['items']})
                policies={k['id']:k for k in context.get('knowledge',[])}
                policies.update({k['id']:k for k in additional.get('knowledge',[])})
                context={**context,'items':list(items.values())[:24],'knowledge':list(policies.values()),'additional_queries':context.get('additional_queries',[])+[query]}
                context['context_hash']=hashlib.sha256(json.dumps(context,sort_keys=True).encode()).hexdigest()
                payload.update(context=context,approved_knowledge=context['knowledge'])
                run=db.put('runs',{**run,'context':context})
                requests.append({'query':query,'provider':telemetry})
                response,telemetry=providers.complete('implementation',payload,job.get('risk','medium'))
            telemetry['context_requests']=requests
            if 'request_context' in response:
                raise ValueError('Investigation blocked: model requested more evidence after two additional retrieval rounds')
        providers.validate(response)
        run=db.put('runs',{**run,'provider':telemetry})
        guard(job_id,token)
        if 'decision' in response:
            decision=db.put('decisions',{'job_id':job_id,**response['decision'],'selected':None})
            guard(job_id,token,status='Needs input',decision_id=decision['id'])
            db.put('runs',{**run,'status':'needs_input','provider':telemetry,'context':context})
            return
        if prior and not fixture:
            cumulative={f['path']:{'path':f['path'],'content':f['after']} for f in prior['files']}
            cumulative.update({edit['path']:edit for edit in response['edits']})
            response={**response,'edits':list(cumulative.values())}
        guard(job_id,token,status='Implementing')
        root=Path(db.DATA_DIR)/'worktrees'/job_id/token
        root.mkdir(parents=True)
        for path,content in files.items():
            target=safe_path(root,path); target.parent.mkdir(parents=True,exist_ok=True); target.write_text(content,encoding='utf-8',newline='')
        sample.git(root,'init','-b','prepared')
        sample.git(root,'add','.')
        sample.git(root,'-c','user.name=Duck Tape','-c','user.email=demo@localhost','commit','--allow-empty','-m','Indexed snapshot '+repo['snapshot'])
        trusted=fixture and sample.trusted_tree(root)
        if trusted:
            checks.append(check(job_id,token,root,'baseline',TEST_SCRIPT,'python -m unittest discover -s tests -v'))
            # Run the new regression tests against unchanged implementation first.
            tests=[edit for edit in response['edits'] if edit['path'].startswith('tests/')]
            if tests:
                for edit in tests:
                    safe_path(root,edit['path']).write_text(edit['content'],encoding='utf-8',newline='')
                checks.append(check(job_id,token,root,'regression-before',TEST_SCRIPT,'new regression test against baseline'))
                for edit in tests:
                    safe_path(root,edit['path']).write_text(files[edit['path']],encoding='utf-8',newline='')
            if job['scenario']=='performance':
                checks.append(check(job_id,token,root,'benchmark-before',BENCH_SCRIPT,'fixed 5000-label workload × 5'))
        changed=[]
        seen=set()
        for edit in response['edits']:
            path=edit['path']
            if path in seen:
                raise ValueError('Duplicate edit path')
            seen.add(path)
            target=safe_path(root,path)
            before=target.read_text(encoding='utf-8') if target.exists() else ''
            target.parent.mkdir(parents=True,exist_ok=True); target.write_text(edit['content'],encoding='utf-8',newline='')
            if before!=edit['content']:
                changed.append({'path':path,'before':before,'after':edit['content']})
        if not changed:
            raise ValueError('Provider returned no changes')
        sample.git(root,'add','-N','--',*[edit['path'] for edit in response['edits']])
        diff=sample.git(root,'diff','--no-ext-diff','--binary','HEAD')
        guard(job_id,token,status='Verifying')
        if trusted:
            checks.append(check(job_id,token,root,'after',TEST_SCRIPT,'python -m unittest discover -s tests -v'))
            if checks[-1]['exit_code']:
                raise ValueError('Prepared patch failed reviewed fixture tests')
            if job['scenario']=='performance':
                checks.append(check(job_id,token,root,'benchmark-after',BENCH_SCRIPT,'fixed 5000-label workload × 5'))
            if job['scenario']=='security':
                checks.append({'id':str(uuid.uuid4()),'job_id':job_id,'phase':'advisory-after','command':'compare fixture lockfile version with fixture advisory','exit_code':0 if 'fixture-parser==1.0.1' in (root/'requirements.lock').read_text() else 1,'duration':0,'stdout':'STEWARD-DEMO-001 fixture: affected 1.0.0 → fixed 1.0.1; not a live OSV finding.','status':'passed'})
        else:
            checks.append({'id':str(uuid.uuid4()),'job_id':job_id,'phase':'after','command':'execution unavailable','exit_code':None,'duration':0,'stdout':'No constrained sandbox available. Arbitrary repository/model code was not executed on the host.','status':'unavailable'})
        review={'approved':True,'findings':['Fixture patch inspected against fixed scenario; execution evidence is recorded separately.']}
        run=db.put('runs',{**run,'candidate_diff':diff,'candidate_files':changed,'provider':telemetry})
        if not fixture:
            changed_paths={f['path'] for f in changed}
            proposed_sources=[{'path':f['path'],'content':f['after'],'origin':'proposed patch'} for f in changed]
            related_sources=[item for item in context['items'] if item['path'] not in changed_paths]
            review_payload={'task':'review','job':payload['job'],'approved_knowledge':payload['approved_knowledge'],'approved_decisions':decisions,'proposed_sources':proposed_sources,'related_evidence':related_sources,'snapshot':repo['snapshot']}
            review,review_telemetry=providers.complete('review',review_payload,job.get('risk','medium'))
            telemetry['review']=review_telemetry
            run=db.put('runs',{**run,'provider':telemetry,'review':review})
            if not review['approved']:
                raise ValueError('Independent model review requested changes: '+'; '.join(str(f) for f in review['findings'])[:700])
        guard(job_id,token)
        number=1+max([r['number'] for r in db.list_records('revisions') if r['job_id']==job_id] or [0])
        revision={'id':str(uuid.uuid4()),'job_id':job_id,'number':number,'diff':diff,'files':changed,'explanation':response['explanation'],'base_snapshot':repo['snapshot'],'patch_hash':hashlib.sha256(diff.encode()).hexdigest(),'context':context,'confidence':'Supported by executed fixture checks' if trusted else 'Limited: execution unavailable','uncertainty':['No constrained sandbox for arbitrary code'] if not trusted else ['Fixture scenario only; not arbitrary model verification'],'provider':telemetry,'review':review,'worktree':str(root),'created_at':db.now(),'author':'Duck Tape'}
        for item in checks:
            item.update(revision_id=revision['id'],base_snapshot=repo['snapshot'],patch_hash=revision['patch_hash'])
        publish(job_id,token,revision,checks)
        db.put('runs',{**run,'status':'complete','context':context,'provider':telemetry,'finished_at':db.now(),'revision_id':revision['id']})
        db.event('Patch revision '+str(number)+' ready for review',job_id,repo['id'])
    except LostLease:
        db.put('runs',{**run,'status':'cancelled_or_superseded','finished_at':db.now()})
    except Exception as exc:
        try:
            guard(job_id,token,status='Failed',error=str(exc)[:1000],lease_until=0)
        except LostLease:
            pass
        db.put('runs',{**run,'status':'failed','error':str(exc)[:1000],'checks':checks,'finished_at':db.now()})

    finally:
        _owned_jobs.pop(job_id,None)


def process_job(job_id):
    from . import edits, snapshots, execution_profiles, verification, knowledge as policies, workflow, model_schemas, retrieval
    from .contracts import CancellationToken, ProviderRequest
    initial = db.get('jobs', job_id)
    if not initial:
        return
    repo = db.get('repositories', initial['repo_id'])
    if repo.get('is_sample') and initial.get('scenario') in {item[0] for item in sample.SCENARIOS}:
        return _process_legacy_fixture(job_id)
    token = uuid.uuid4().hex
    def claim(job):
        if job['status'] != 'Queued':
            raise LostLease()
        return {**job, 'status': 'Investigating', 'lease_token': token, 'lease_until': time.time()+180, 'error': None}
    try:
        job = db.mutate('jobs', job_id, claim)
    except LostLease:
        return
    stop_heartbeat = threading.Event()
    def cancelled():
        current = db.get('jobs', job_id)
        return not current or current.get('lease_token') != token or current['status'] not in ACTIVE or current.get('lease_until',0) <= time.time()
    cancel = CancellationToken(cancelled, time.monotonic()+1800)
    _tokens[job_id]=cancel
    _owned_jobs[job_id]=token
    def heartbeat():
        while not stop_heartbeat.wait(10):
            try: guard(job_id, token)
            except LostLease:
                cancel.cancel()
                return
    pulse = threading.Thread(target=heartbeat, daemon=True)
    pulse.start()
    run = db.put('runs', {'job_id': job_id, 'lease_token': token, 'status': 'running', 'started_at': db.now(), 'base_snapshot': job['base_snapshot'], 'provider_attempts': []})
    checks = []
    def save(**fields):
        nonlocal run
        guard(job_id, token)
        if 'provider_attempts' in fields:
            records=fields['provider_attempts']
            coding=next((item for item in records if item.get('role') in ('implementation','repair')),records[0] if records else {})
            review=next((item for item in reversed(records) if item.get('role')=='review'),None)
            fields['provider']={**coding,'stages':records,'review':review}
        run = db.put('runs', {**run, **fields})
    def pause(status, **fields):
        guard(job_id, token, status=status, **fields)
        db.put('runs', {**run, 'status': 'needs_input' if status == 'Needs input' else 'awaiting_approval', 'finished_at': db.now()})
    try:
        if job['base_snapshot'] != repo['snapshot']:
            raise ValueError('Repository snapshot changed; refresh this job before retrying')
        source_id = job.get('source_snapshot_id') or repo.get('source_snapshot_id')
        if not source_id:
            source = snapshots.capture(repo['id'], Path(repo['root']), repo.get('origin', {}), cancel)
            source_id = source['id']
            db.mutate('repositories', repo['id'], lambda record: {**record, 'source_snapshot_id': source_id})
        source = db.get('source_snapshots', source_id)
        if not source or source['repo_id'] != repo['id']:
            raise ValueError('Source snapshot does not belong to this repository')
        source_files = snapshots.read_files(source_id)
        decisions = [record for record in db.list_records('decisions') if record.get('job_id') == job_id]
        decisions = [{**record, 'selected': job.get('selected_decisions', {}).get(record['id'], record.get('selected'))} for record in decisions]
        approved = [record for record in db.list_records('knowledge') if record.get('repo_id') == repo['id'] and record.get('status') == 'team-approved']
        context = retrieval.retrieve(repo['index'], job['title']+' '+job['description'], knowledge=approved)
        context.pop('latency_ms',None)
        context.update(snapshot=repo['snapshot'], repo_id=repo['id'])
        seed = {'job': {key: job.get(key) for key in ('id','title','description','base_snapshot','current_revision','revision_instructions','retry_generation')},
                'source_snapshot_id': source_id, 'knowledge': approved,
                'decisions': [{key: record.get(key) for key in ('id','selected','selected_at','author','rationale','constraint_versions')} for record in decisions if record.get('selected')]}
        workflow_key = workflow.input_fingerprint(seed)
        budget_key=workflow.input_fingerprint({'job':seed['job'],'source_snapshot_id':source_id})
        earlier=[record for record in db.list_records('runs') if record.get('budget_key')==budget_key]
        if earlier:
            from datetime import datetime
            elapsed=sum(max(0,datetime.fromisoformat(record.get('finished_at') or record['updated_at']).timestamp()-datetime.fromisoformat(record['started_at']).timestamp()) for record in earlier)
            cancel.deadline=time.monotonic()+max(0,1800-elapsed)
        save(context=context, source_snapshot_id=source_id, workflow_key=workflow_key,budget_key=budget_key)
        guard(job_id, token, input_fingerprint=workflow_key, source_snapshot_id=source_id)
        def budget():
            stages = [stage for stage in db.list_records('run_stages') if stage.get('budget_key') == budget_key]
            telemetry_records = [telemetry for stage in stages for telemetry in stage.get('attempt_history', [])+[stage.get('telemetry', {})]]
            attempts = sum(telemetry.get('attempts', 0) for telemetry in telemetry_records)
            output = 0
            for telemetry in telemetry_records:
                log = telemetry.get('attempt_log') or ([{'usage':telemetry.get('usage')}] if telemetry.get('attempts') else [])
                output += sum((entry.get('usage') or {}).get('output_tokens') if (entry.get('usage') or {}).get('output_tokens') is not None else telemetry.get('output_reservation',1024) for entry in log)
            return attempts, output
        def call(task, payload, label):
            guard(job_id, token)
            request = ProviderRequest(task=task, payload=payload, repo_id=repo['id'], snapshot_id=source_id, risk=job.get('risk','medium'),
                                      max_output_tokens=min(2048, providers.capabilities(task)['max_output_tokens']), failure_count=len(payload.get('feedback', [])), cache=False)
            config=providers.configuration()
            serialized=json.dumps(payload,separators=(',',':'))
            try:
                resolved_model,_=providers.route(task,request.risk,len(serialized.encode()),request.scope,request.failure_count)
            except providers.ProviderError as exc:
                if exc.code!='no_model_configured': raise
                resolved_model=None  # Live completion still rejects unconfigured providers.
            provider_identity={'endpoint':config['endpoint'],'kind':config['kind'],'model':resolved_model,
                               'capabilities':providers.capabilities(task),'prompt':providers.PROMPT_VERSION,'schema':model_schemas.schema_for(task,payload=payload)}
            fingerprint = workflow.input_fingerprint({'request':request.model_dump(), 'workflow_key':workflow_key, 'label':label,'provider':provider_identity})
            stage_id = workflow.input_fingerprint({'job_id':job_id,'fingerprint':fingerprint})
            previous = db.get('run_stages',stage_id)
            if previous and previous['status'] == 'complete':
                model_schemas.validate(previous['output'],task,payload=payload)
                save(provider_attempts=[*run.get('provider_attempts',[]),{**previous['telemetry'],'resumed_stage':stage_id,'attempts':0,'usage':None}])
                return previous['output']
            attempts, output_tokens = budget()
            if attempts >= 12 or output_tokens+request.max_output_tokens > 30000:
                raise ValueError('Job provider call/output budget exhausted')
            request = request.model_copy(update={'attempt_budget':min(2,12-attempts,(30000-output_tokens)//request.max_output_tokens)})
            stage = db.put('run_stages', {'id':stage_id,'job_id':job_id,'run_id':run['id'],'stage':task,'label':label,'workflow_key':workflow_key,'budget_key':budget_key,
                                          'input_fingerprint':fingerprint,'input':request.model_dump(),'status':'running',
                                          'attempt_history':(previous.get('attempt_history',[])+[previous['telemetry']]) if previous else [],
                                          'telemetry': {'attempts':1,'usage':None,'output_reservation':request.max_output_tokens}})
            telemetry=stage['telemetry']
            try:
                result = providers.complete(request, cancel)
                telemetry=result.telemetry
                guard(job_id, token)
                model_schemas.validate(result.output,task,payload=payload)
                if task=='investigation':
                    for citation in result.output['evidence']:
                        if not any(item['path']==citation['path'] and item['start_line']<=citation['start_line']<=citation['end_line']<=item['end_line'] for item in payload['context']['items']):
                            raise ValueError('Investigation citation does not resolve to supplied evidence')
                if task=='planning' and 'steps' in result.output:
                    for step in result.output['steps']:
                        for path in step['paths']: safe_path(db.DATA_DIR/'worktrees'/job_id,path)
                db.put('run_stages',{**stage,'status':'complete','output':result.output,'telemetry':result.telemetry})
                save(provider_attempts=[*run.get('provider_attempts',[]),result.telemetry])
                if budget()[0] > 12 or budget()[1] > 30000:
                    raise ValueError('Job provider call/output budget exhausted')
                return result.output
            except BaseException as exc:
                db.put('run_stages',{**stage,'status':'failed' if isinstance(exc,Exception) else 'interrupted','error':type(exc).__name__,
                                     'telemetry':getattr(exc,'telemetry',None) or telemetry})
                raise
        def decision(output, label):
            details = output['decision']
            identity = workflow.input_fingerprint({'job':job_id,'workflow':workflow_key,'label':label,'decision':details})
            existing = db.get('decisions',identity)
            if existing and existing.get('selected'):
                return False
            card = db.put('decisions',{'id':identity,'job_id':job_id,**details,'selected':None})
            pause('Needs input',decision_id=card['id'])
            return True
        def constraints(paths):
            nonlocal approved, context
            relevant = policies.for_paths(repo['id'],repo['snapshot'],paths)
            # Resolve each connected set together; sequential pairwise filtering can
            # otherwise remove every option from a later overlapping pair.
            groups=[]
            for conflict in policies.conflicts(repo['id'],paths):
                entries=set(conflict['entries'])
                overlapping=[group for group in groups if group['key']==conflict['key'] and group['entries'] & entries]
                for group in overlapping:
                    entries |= group['entries']
                    groups.remove(group)
                groups.append({'key':conflict['key'],'entries':entries})
            for group in groups:
                conflict={'key':group['key'],'entries':sorted(group['entries'])}
                entries = [record for record in relevant if record['id'] in conflict['entries']]
                versions = {record['id']:record.get('version',1) for record in entries}
                identity = workflow.input_fingerprint({'job':job_id,'conflict':conflict,'versions':versions})
                card = db.get('decisions',identity)
                if not card or card.get('selected') not in versions:
                    options = [{'id':record['id'],'label':record['title'],'tradeoff':'Use this approved constraint for this job only; conflicting policy remains unchanged.'} for record in entries]
                    card = db.put('decisions',{'id':identity,'job_id':job_id,'title':'Resolve conflicting approved '+conflict['key']+' constraints',
                                              'options':options,'recommendation':None,'selected':None,'constraint_versions':versions,'kind':'knowledge-conflict'})
                    pause('Needs input',decision_id=card['id'])
                    return False
                relevant = [record for record in relevant if record['id'] not in versions or record['id']==card['selected']]
            approved = relevant
            context['knowledge'] = relevant
            context['context_hash'] = workflow.input_fingerprint({'items':context['items'],'knowledge':relevant})
            save(context=context)
            return True
        paths = {item['path'] for item in context['items']}
        if not constraints(paths): return
        base_payload = {'job':seed['job'],'context':context,'approved_knowledge':approved,'approved_decisions':[record for record in decisions if record.get('selected')], 'snapshot':source_id}
        investigation = call('investigation',base_payload,'investigation')
        save(investigation=investigation)
        plan = call('planning',{**base_payload,'investigation':investigation},'planning')
        evidence_rounds=0
        while 'request_context' in plan and evidence_rounds<2:
            evidence_rounds+=1
            query=plan['request_context']
            additional=retrieval.retrieve(repo['index'],query,knowledge=approved)
            additional.pop('latency_ms',None)
            items={(item['path'],item['start_line'],item['end_line']):item for item in context['items']+additional['items']}
            context['items']=list(items.values())
            context['additional_queries']=context.get('additional_queries',[])+[query]
            paths|={item['path'] for item in additional['items']}
            if not constraints(paths): return
            base_payload.update(context=context,approved_knowledge=approved)
            save(context=context,evidence_rounds=evidence_rounds)
            plan=call('planning',{**base_payload,'investigation':investigation},'planning-context-'+str(evidence_rounds))
        save(plan=plan)
        if 'decision' in plan:
            if decision(plan,'plan'): return
            raise ValueError('Selected decision must be incorporated into a new plan')
        if 'request_context' in plan:
            raise ValueError('Planning evidence request budget exhausted: '+plan['request_context'])
        paths |= {path for step in plan['steps'] for path in step['paths']}
        if not constraints(paths): return
        plan_hash=workflow.input_fingerprint({'plan':plan,'workflow_key':workflow_key})
        guard(job_id,token,approach=plan['steps'],acceptance_criteria=plan['acceptance'],affected_areas=sorted(paths),plan_hash=plan_hash,risk=job.get('risk','medium'))
        if job.get('requires_approval') and (not job.get('preparation_approved') or (job.get('preparation_approval') or {}).get('plan_hash')!=plan_hash):
            pause('Awaiting approval',preparation_approved=False,preparation_approval=None)
            return
        profiles = execution_profiles.discover(source)
        if not profiles:
            raise ValueError('Analysis only: no supported execution profile; configure locked Python or npm checks')
        profile = profiles[0]
        unsupported=set(profile.get('unfamiliar_commands',{})) & {'test','build','typecheck','check-types'}
        if unsupported:
            save(execution_profile=profile)
            raise ValueError('Analysis only: unsupported required check scripts: '+', '.join(sorted(unsupported))+'; configure a tested execution adapter before preparation')
        full_files = []
        for path in sorted(paths):
            if path not in source_files: continue
            data = source_files[path]
            if b'\0' in data: continue
            full = edits.read_file(source_id,path,hashlib.sha256(data).hexdigest())
            full_files.append({'path':path,'hash':hashlib.sha256(full).hexdigest(),'complete':True,'content':full.decode('utf-8')})
        previous = db.get('revisions',job['current_revision']) if job.get('current_revision') else None
        if previous:
            if previous.get('job_id')!=job_id or previous['base_snapshot']!=repo['snapshot']:
                raise ValueError('Previous revision does not belong to this job and source base')
            previous={key:previous.get(key) for key in ('id','number','diff','files','patch_hash')}
        payload = {**base_payload,'plan':plan,'files':full_files,'approved_knowledge':approved,'previous_revision':previous,
                   'edit_contract':'Return the complete cumulative patch against the supplied immutable source. Preserve prior changes unless explicitly requested to revert. Include a regression test for bugs, preserving all existing tests.'}
        def compact_context():
            complete_paths={item['path'] for item in full_files}
            return {**{key:value for key,value in context.items() if key!='knowledge'},
                    'items':[{key:value for key,value in item.items() if key!='content' or item['path'] not in complete_paths} for item in context['items']]}
        payload['context']=compact_context()
        regression_operations=[]
        regression_repairs=0
        acceptance_coverage=[]
        acceptance_requirements=[]
        acceptance_sources={}
        if job.get('type') in ('fix','feature'):
            test_role='acceptance' if job['type']=='feature' else 'regression'
            def is_test(path):
                name=PurePosixPath(path).name
                return any(part in ('test','tests') for part in PurePosixPath(path).parts) or name.startswith('test_') or '.test.' in name or '.spec.' in name
            regression_payload={**payload,'files':[item for item in full_files if is_test(item['path'])],
                                'read_only_sources':[item for item in full_files if not is_test(item['path'])],
                                'plan':{'acceptance':plan['acceptance']},
                                'edit_contract':'Return only test-file edits against the supplied immutable source. Preserve existing tests and use the supplied path and hash together for UPDATE. Never edit read_only_sources; CREATE is only for new test paths.',
                                'instruction':'Write only regression tests for the requested behavior. files lists editable test paths with exact hashes; read_only_sources is context and must never be edited. Leave source implementation unchanged.'}
            if test_role=='acceptance':
                regression_payload.update(acceptance_criteria=[{'id':'criterion-'+str(number),'description':description} for number,description in enumerate(plan['acceptance'],1)],
                                          instruction='Write only executable feature acceptance tests. Cover every supplied criterion id with an exact test function name or JS/TS test label. files are editable tests; read_only_sources must not be edited. Leave implementation unchanged; preserve existing unrelated assertions.')
            regression=call(test_role,regression_payload,test_role)
            def check_regression(output,destination):
                nonlocal acceptance_sources
                if 'edits' not in output:
                    raise ValueError('Bug regression stage needs concrete test edits before implementation')
                if any(not is_test(operation['path']) or operation.get('destination') and not is_test(operation['destination']) for operation in output['edits']):
                    raise ValueError('Regression stage may change only test files: '+', '.join(item['path'] for item in regression_payload['files']))
                prepared_tests=edits.apply(source,output['edits'],destination)
                if test_role=='acceptance':
                    coverage=output.get('coverage',[])
                    expected={criterion['id'] for criterion in regression_payload['acceptance_criteria']}
                    if {reference['criterion_id'] for reference in coverage}!=expected or len(coverage)!=len(expected):
                        raise ValueError('Every plan acceptance criterion needs exactly one executable test reference')
                    changed_tests={item['path']:item['after'] for item in prepared_tests.files if item.get('hash') and is_test(item['path'])}
                    acceptance_commands(coverage,profile,changed_tests)
                    acceptance_sources={reference['path']:changed_tests[reference['path']] for reference in coverage}
            try:
                check_regression(regression,db.DATA_DIR/'worktrees'/job_id/token/(test_role+'-preflight'))
            except ValueError as exc:
                save(**{test_role+'_failure':{'output':regression,'error':str(exc)[:1000]}})
                regression_repairs=1
                regression=call(test_role,{**regression_payload,'feedback':[{'error':str(exc)[:1000],'previous_edits':regression.get('edits',[])}],
                                              'instruction':'Correct the indicated test references and edit preconditions. Change only test files. Existing supplied test paths need UPDATE with the supplied hash, not CREATE; preserve criterion coverage.'},test_role+'-correction')
                check_regression(regression,db.DATA_DIR/'worktrees'/job_id/token/(test_role+'-corrected'))
            regression_operations=regression['edits']
            payload['required_'+test_role+'_edits']=regression_operations
            payload['edit_contract']+=' The controller preserves required test edits; focus on source edits and do not remove acceptance or regression assertions.'
            if test_role=='acceptance':
                acceptance_coverage=regression['coverage']
                acceptance_requirements=[{'id':reference['criterion_id'],'check_id':'acceptance-'+reference['criterion_id'],'required':True} for reference in acceptance_coverage]
                payload['acceptance_coverage']=acceptance_coverage
            save(**{test_role:regression})
        feedback=[]
        guard(job_id,token,status='Implementing')
        for attempt in range(3-regression_repairs):
            task = 'implementation' if attempt==0 else 'repair'
            response = call(task,{**payload,'feedback':feedback},task+'-'+str(attempt))
            while 'request_context' in response and evidence_rounds<2:
                evidence_rounds+=1
                query=response['request_context']
                additional=retrieval.retrieve(repo['index'],query,knowledge=approved)
                additional.pop('latency_ms',None)
                items={(item['path'],item['start_line'],item['end_line']):item for item in context['items']+additional['items']}
                context['items']=list(items.values())
                context['additional_queries']=context.get('additional_queries',[])+[query]
                paths|={item['path'] for item in additional['items']}
                if not constraints(paths): return
                payload.update(context=compact_context(),approved_knowledge=approved)
                save(context=context,evidence_rounds=evidence_rounds)
                response=call(task,{**payload,'feedback':feedback},task+'-context-'+str(evidence_rounds))
            if 'request_context' in response: raise ValueError('Evidence request budget exhausted')
            if 'decision' in response:
                if decision(response,'implementation'): return
                raise ValueError('Selected decision must be incorporated into new edits')
            if regression_operations:
                combined={operation['path']:operation for operation in regression_operations}
                combined.update({operation['path']:operation for operation in response['edits']})
                response={**response,'edits':list(combined.values())}
            touched=paths|{operation['path'] for operation in response['edits']}|{operation['destination'] for operation in response['edits'] if operation.get('destination')}
            if not constraints(touched): return
            known={item['path'] for item in full_files}
            missing={operation['path'] for operation in response['edits'] if operation['op']!='create' and operation['path'] not in known}
            supplied_policies={(item['id'],item.get('version',1)) for item in payload['approved_knowledge']}
            new_policies={(item['id'],item.get('version',1)) for item in approved}-supplied_policies
            if missing or new_policies:
                if evidence_rounds>=2: raise ValueError('Newly affected files require complete source evidence')
                evidence_rounds+=1
                for path in sorted(missing):
                    if path not in source_files: raise ValueError('Edit references absent source file')
                    data=edits.read_file(source_id,path,hashlib.sha256(source_files[path]).hexdigest())
                    full_files.append({'path':path,'hash':hashlib.sha256(data).hexdigest(),'complete':True,'content':data.decode('utf-8')})
                payload.update(files=full_files,context=compact_context(),approved_knowledge=approved)
                response=call(task,{**payload,'feedback':feedback},task+'-affected-'+str(evidence_rounds))
                if 'edits' not in response: raise ValueError('Affected-file evidence needs concrete edits')
                if regression_operations:
                    combined={operation['path']:operation for operation in regression_operations}
                    combined.update({operation['path']:operation for operation in response['edits']})
                    response={**response,'edits':list(combined.values())}
                touched|={operation['path'] for operation in response['edits']}|{operation['destination'] for operation in response['edits'] if operation.get('destination')}
                if not constraints(touched): return
                if {(item['id'],item.get('version',1)) for item in approved}-{(item['id'],item.get('version',1)) for item in payload['approved_knowledge']}:
                    raise ValueError('Newly affected knowledge needs another bounded evidence round before editing')
            attempt_record={'job_id':job_id,'run_id':run['id'],'attempt':attempt,'response':response,'feedback':feedback,'source_snapshot_id':source_id}
            try:
                candidate=edits.apply(source,response['edits'],db.DATA_DIR/'worktrees'/job_id/token/str(attempt))
                if acceptance_coverage:
                    changed_tests={item['path']:item['after'] for item in candidate.files if item.get('hash') and is_test(item['path'])}
                    if any(changed_tests.get(path)!=content for path,content in acceptance_sources.items()):
                        raise ValueError('Implementation must preserve the separately generated acceptance test assertions')
                    commands=acceptance_commands(acceptance_coverage,profile,changed_tests)
                    candidate_profile={**profile,'commands':[*profile['commands'],*commands]}
                else: candidate_profile=profile
                guard(job_id,token,status='Verifying')
                save(candidate_diff=candidate.diff,candidate_files=candidate.files,candidate_patch_hash=candidate.patch_hash)
                verification_candidate=candidate.model_dump()
                verification_candidate['files']=[{**item,'after':None if item['hash'] is None else (Path(candidate.worktree)/item['path']).read_bytes()} for item in candidate.files]
                result=verification.run(run['id']+'-'+str(attempt),source,verification_candidate,candidate_profile,cancel)
                result=[item.model_dump() if hasattr(item,'model_dump') else item for item in result]
                for reference in acceptance_coverage:
                    for record in result:
                        if record.get('phase')=='candidate' and record.get('check_id')=='acceptance-'+reference['criterion_id']:
                            if not acceptance_observed(record,reference,profile):
                                record.update(status='failed',exit_reason='Selected acceptance test was not observed executing')
                if job.get('type')=='security':
                    from . import signals
                    security_check=signals.verify_security(job,source,verification_candidate,cancel)
                    result.append(security_check.model_dump() if hasattr(security_check,'model_dump') else security_check)
                for item in result:
                    item.update(job_id=job_id,run_id=run['id'],patch_hash=candidate.patch_hash,source_snapshot_id=source_id)
                    db.put('checks',item)
                checks.extend(result)
                assessment=verification.assess(result,acceptance_requirements)
                if job.get('type')=='security' and security_check.get('status')!='passed':
                    assessment['passed']=False
                    assessment['limitations']=[*assessment['limitations'],'Required matching before/after advisory recheck did not establish remediation; inspect advisory evidence.']
                if job.get('type')=='performance' and (not assessment.get('benchmarks') or not all(item.get('valid') and item.get('improvement_percent',0)>0 for item in assessment['benchmarks'])):
                    assessment['passed']=False
                    assessment['limitations']=[*assessment['limitations'],'Required comparable benchmark must record unchanged workload/environment, raw before/after samples and measured positive improvement.']
                if job.get('type')=='fix' and assessment.get('regression_reproduced') is not True:
                    assessment['passed']=False
                    assessment['limitations']=[*assessment['limitations'],'Required bug regression must fail against the unchanged implementation and pass against the candidate; add a targeted regression while preserving existing tests.']
                attempt_record.update(candidate=candidate.model_dump(),checks=result,assessment=assessment)
                db.put('candidate_attempts',attempt_record)
                save(candidate_diff=candidate.diff,candidate_files=candidate.files,checks=checks,assessment=assessment)
                if not assessment['passed']:
                    excerpts=[]
                    for item in result:
                        excerpt={key:item.get(key) for key in ('id','phase','check_id','status','exit_code','exit_reason','test_count')}
                        if item.get('status')!='passed':
                            text=item.get('stdout','')
                            limit=1024 if item.get('phase')=='candidate' else 512
                            excerpt.update(stdout=text[-limit:],stdout_truncated=len(text)>limit)
                        excerpts.append(excerpt)
                    feedback=[{'kind':'verification','checks':excerpts,'assessment':assessment}]
                else:
                    review_payload={'job':seed['job'],'approved_knowledge':approved,'approved_decisions':payload['approved_decisions'],
                                    'proposed_sources':[{'path':item['path'],'content':item['after']} for item in candidate.files if not item['binary']],
                                    'checks':result,'patch':candidate.diff,'plan':plan,'acceptance_coverage':acceptance_coverage}
                    review=call('review',review_payload,'review-'+str(attempt))
                    save(review=review)
                    references={item['path']:item.get('after') for item in candidate.files}
                    supported=all(finding['reference']['path'] in references and isinstance(references[finding['reference']['path']],str)
                                  and finding['reference']['end_line']<=len(references[finding['reference']['path']].splitlines()) for finding in review['findings'])
                    if not supported:
                        original_review=review
                        review=call('review',{**review_payload,'feedback':[{'kind':'unsupported_review_claim','review':review,'message':'Finding does not resolve to supplied changed source; re-evaluate and cite valid lines, or approve if there is no concrete defect.'}]},'review-adjudication-'+str(attempt))
                        save(review_adjudication={'original':original_review,'adjudicated':review})
                        supported=all(finding['reference']['path'] in references and isinstance(references[finding['reference']['path']],str)
                                      and finding['reference']['end_line']<=len(references[finding['reference']['path']].splitlines()) for finding in review['findings'])
                        if not supported:
                            raise RuntimeError('Unsupported review claim remains unresolved after one adjudication; inspect review evidence')
                    if not review['approved']:
                        feedback=[{'kind':'review','review':review}]
                    else:
                        guard(job_id,token,status='Verifying')
                        number=1+max([item['number'] for item in db.list_records('revisions') if item.get('job_id')==job_id] or [0])
                        revision={'id':uuid.uuid4().hex,'job_id':job_id,'number':number,'diff':candidate.diff,'files':candidate.files,'patch_hash':candidate.patch_hash,
                                  'base_snapshot':repo['snapshot'],'source_snapshot_id':source_id,'input_fingerprint':workflow_key,'worktree':candidate.worktree,
                                  'context':context,'provider':run.get('provider',{}),'review':review,'explanation':response['explanation'],
                                  'assessment':assessment,'acceptance_coverage':acceptance_coverage,
                                  'confidence':'Supported by sandbox checks and separate model review','uncertainty':assessment['limitations'],'created_at':db.now(),'author':'Duck Tape'}
                        for item in result: item.update(revision_id=revision['id'],base_snapshot=repo['snapshot'])
                        publish(job_id,token,revision,result)
                        return
            except (ValueError,UnicodeError) as exc:
                feedback=[{'kind':'edit_or_verification','error':str(exc)[:1000]}]
                db.put('candidate_attempts',{**attempt_record,'error':str(exc)[:1000]})
            if attempt==2-regression_repairs: raise ValueError('Two repair cycles exhausted; inspect candidate attempts and check/review feedback')
            guard(job_id,token,status='Implementing')
        raise ValueError('No publishable candidate')
    except LostLease:
        db.put('runs',{**run,'status':'cancelled_or_superseded','finished_at':db.now()})
    except Exception as exc:
        was_cancelled=cancel.is_set()
        try: guard(job_id,token,status='Failed',error=str(exc)[:1000])
        except LostLease: pass
        db.put('runs',{**run,'status':'cancelled_or_superseded' if was_cancelled else 'failed','error':str(exc)[:1000],
                       'provider_failure':getattr(exc,'telemetry',None),'checks':checks,'finished_at':db.now()})
    finally:
        stop_heartbeat.set()
        _tokens.pop(job_id,None)
        _owned_jobs.pop(job_id,None)
        pulse.join(timeout=1)
        from . import sandbox
        sandbox.cancel(run['id'])


def advance(work_item_id, owner):
    from . import workflow
    from .contracts import RunCheckpoint
    item=workflow.claim_work(work_item_id,owner)
    if not item: raise LostLease()
    stopped=threading.Event()
    def heartbeat():
        while not stopped.wait(10):
            try: workflow.checkpoint_work(work_item_id,owner,lease_token=item['lease_token'])
            except workflow.Conflict:
                cancel_job(item['subject'])
                owned=_owned_jobs.get(item['subject'])
                if owned:
                    def release(job):
                        if job.get('lease_token')==owned:
                            job.update(status='Cancelled',lease_token=None,lease_until=0)
                        return job
                    db.mutate('jobs',item['subject'],release)
                return
    pulse=threading.Thread(target=heartbeat,daemon=True)
    pulse.start()
    try: process_job(item['subject'])
    finally:
        stopped.set()
        pulse.join(timeout=1)
    job=db.get('jobs',item['subject'])
    checkpoint={'stage':job['status'],'owner':owner,'input_fingerprint':job.get('input_fingerprint',''),'outputs':{'job_id':job['id']}}
    workflow.checkpoint_work(work_item_id,owner,checkpoint,lease_token=item['lease_token'],status='complete' if job['status'] in ('Ready for review','Needs input','Awaiting approval') else 'failed')
    return RunCheckpoint(**checkpoint)


def loop():
    while not _stop.is_set():
        for job in db.list_records('jobs'):
            if _stop.is_set():
                break
            if job['status'] in ACTIVE and job.get('lease_until',0)<time.time():
                from .workflow import recover_job
                job=recover_job(job['id'])
            if job['status']=='Queued':
                process_job(job['id'])
        _stop.wait(.5)

def start_worker():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread=threading.Thread(target=loop,name='steward-worker',daemon=True)
    _thread.start()

def stop_worker():
    _stop.set()
    for job in db.list_records('jobs'):
        if job['status'] in ACTIVE and job['id'] in _owned_jobs:
            cancel_job(job['id'])
            def release(current):
                if current['status'] in ACTIVE and current.get('lease_token')==_owned_jobs.get(job['id']):
                    current.update(status='Queued',lease_token=None,lease_until=0)
                return current
            db.mutate('jobs',job['id'],release)
    for job_id in list(_processes):
        cancel_job(job_id)
    if _thread:
        _thread.join(timeout=8)
