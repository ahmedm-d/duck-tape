import asyncio
import hashlib
import io
import json
import os
import threading
import uuid
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, UploadFile, File, Request, Query
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from . import db

SOURCES = ('ticket','request','security','analysis','research')
TERMINAL = {'Cancelled','Rejected','Completed'}
ACTIVE = {'Queued','Investigating','Implementing','Verifying'}
intake_lock = threading.RLock()
poll_stop = threading.Event()


def serialized(fn):
    @wraps(fn)
    def locked(*args,**kwargs):
        with intake_lock:
            return fn(*args,**kwargs)
    return locked


def require(kind, id):
    value = db.get(kind, id)
    if value is None:
        raise HTTPException(404, f'{kind} record not found')
    return value


def approved_knowledge(repo_id):
    return [k for k in db.list_records('knowledge') if k.get('repo_id') == repo_id and k.get('status') == 'team-approved']


def store_snapshot(repo):
    db.put('snapshots',{'id':repo['id']+':'+(repo.get('source_snapshot_id') or repo['snapshot']),'repo_id':repo['id'],'snapshot':repo['snapshot'],'source_snapshot_id':repo.get('source_snapshot_id'),'index':repo['index']})
    from . import retrieval
    retrieval.index_chunks(repo['id'],repo.get('source_snapshot_id') or repo['snapshot'],repo['index'])


def refresh_repo(id, replacement=None, cancel=None, on_publish=None):
    from .repository import index_repository
    from . import snapshots
    repo = require('repositories', id)
    before = repo['snapshot']
    before_source=repo.get('source_snapshot_id')
    origin = snapshots._origin(repo.get('origin'))
    if replacement is not None or origin.get('source', '').startswith('https://'):
        source = snapshots.refresh_zip(id, replacement, cancel) if replacement is not None else snapshots.refresh(id, cancel)
        root = db.DATA_DIR / 'refreshed' / id / uuid.uuid4().hex
        snapshots.materialize(source['id'], root)
    else:
        root = Path(repo['root'])
        source = snapshots.capture(id, root, origin, cancel)
    index = index_repository(root, repo_id=id, source_snapshot_id=source['id'])
    index.update(repo_id=id, source_snapshot_id=source['id'])
    if cancel:
        cancel.raise_if_cancelled()
    candidate={**repo,'index':index,'snapshot':index['snapshot'],'source_snapshot_id':source['id'],'root':str(root),
        'origin':source['origin'],'branch':source.get('branch'),'commit':source.get('commit')}
    store_snapshot(repo)
    store_snapshot(candidate)
    old_source=db.get('source_snapshots',before_source) if before_source else None
    old_files={f['path']:(f.get('hash'),f.get('mode')) for f in (old_source or {}).get('files',[]) if f['status']=='included'}
    new_files={f['path']:(f.get('hash'),f.get('mode')) for f in source['files'] if f['status']=='included'}
    changed=[path for path in old_files.keys()|new_files.keys() if old_files.get(path)!=new_files.get(path)] if old_source else None
    def publish(con):
        if cancel: cancel.raise_if_cancelled()
        current=db.get_in(con,'repositories',id)
        if current['snapshot'] != before or current.get('source_snapshot_id')!=before_source:
            raise ValueError('Repository changed during refresh; retry against its current snapshot')
        updated=db.put_in(con,'repositories',{**current,**{key:candidate[key] for key in ('index','snapshot','source_snapshot_id','root','origin','branch','commit')}})
        db.index_search(updated,con)
        if before != candidate['snapshot'] or before_source!=source['id']:
            from . import knowledge as knowledge_service
            knowledge_service.invalidate_snapshot(id,current['index'],index,con,changed)
            for row in con.execute("SELECT data FROM records WHERE kind='jobs'").fetchall():
                job=json.loads(row[0])
                if job.get('repo_id') == id and job.get('status') not in TERMINAL:
                    db.put_in(con,'jobs',{**job,'status':'Failed','error':'Repository changed. Retry to investigate the new snapshot.','approval':None,'stale':True,'lease_token':None,'lease_until':0})
            db.put_in(con,'events',{'message':'Snapshot refreshed; dependent knowledge and pending jobs invalidated','repo_id':id,'author':'Duck Tape'})
        if on_publish: on_publish(con,updated)
        return updated
    return db.atomic(publish)


def scheduler():
    while not poll_stop.wait(3):
        try:
            from . import monitoring
            monitoring.schedule_due(db.now())
        except Exception as exc:
            db.event(f'Monitoring scheduling failed: {type(exc).__name__}')


@asynccontextmanager
async def lifespan(app):
    from .sample import seed_sample, seed_jobs
    from .providers import start_local_runner,start_embedding_runner
    db.init()
    await asyncio.to_thread(start_local_runner)
    await asyncio.to_thread(start_embedding_runner)
    repo = seed_sample()
    seed_jobs(repo)
    for r in db.list_records('repositories'):
        if not r.get('source_snapshot_id'):
            from . import snapshots
            source = snapshots.capture(r['id'], Path(r['root']), r.get('origin'))
            r = db.put('repositories', {**r, 'source_snapshot_id': source['id']})
        r['index'].update(repo_id=r['id'], source_snapshot_id=r['source_snapshot_id'])
        db.put('repositories', r)
        db.index_search(r)
        store_snapshot(r)
    embedded = os.environ.get('STEWARD_EMBEDDED_WORKER') == '1'
    if embedded:
        from .worker import start_worker
        from .operations import start_operations
        start_worker()
        start_operations()
    poll_stop.clear()
    thread = threading.Thread(target=scheduler, daemon=True)
    thread.start()
    yield
    poll_stop.set()
    thread.join(timeout=5)
    if embedded:
        from .worker import stop_worker
        from .operations import stop_operations
        stop_worker()
        stop_operations()


app = FastAPI(title='Duck Tape', lifespan=lifespan)


@app.middleware('http')
async def local_origin(request: Request, call_next):
    host = request.url.hostname
    if host not in ('localhost','127.0.0.1','::1','testserver'):
        return JSONResponse({'detail':'Local workspace only'},403)
    origin = request.headers.get('origin')
    if request.method not in ('GET','HEAD','OPTIONS') and origin:
        parsed = urlparse(origin)
        if parsed.hostname not in ('localhost','127.0.0.1','::1') or parsed.netloc != request.headers.get('host'):
            return JSONResponse({'detail':'Cross-origin mutation rejected'},403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.exception_handler(ValueError)
async def invalid(request, exc):
    return JSONResponse({'detail':str(exc)},400)


class JobInput(BaseModel):
    repo_id: str
    title: str = Field(min_length=3,max_length=200)
    description: str = Field(default='',max_length=30000)
    source: Literal['ticket','request','security','analysis','research'] = 'ticket'
    type: Literal['feature','fix','refactor','security','performance'] = 'fix'
    priority: Literal['low','medium','high','critical'] = 'medium'
    scenario: str | None = None
    source_url: str | None = None
    source_key: str | None = Field(default=None,max_length=512)

    @field_validator('source_url')
    @classmethod
    def safe_source_link(cls,value):
        if not value: return None
        if value.startswith('fixture:'): return value
        parsed=urlparse(value)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or len(value)>2000:
            raise ValueError('Source links must be HTTP(S) URLs without credentials')
        return value


@app.get('/api/health')
def health():
    worker=db.get('services','worker') or {}
    active=worker.get('status')=='running' and worker.get('heartbeat','')>(datetime.now(timezone.utc)-timedelta(seconds=15)).isoformat()
    return {'status':'ok','workspace':'local','worker':'running' if active else 'stopped','monitoring':'running' if active and not poll_stop.is_set() else 'stopped'}


@app.get('/api/global-catalogue')
def global_catalogue_query(ecosystem: str = Query(min_length=1, max_length=64),
                           package: str = Query(min_length=1, max_length=256),
                           limit: int = Query(default=25, ge=1, le=100),
                           provider: Literal['local', 'atlas'] = 'local') -> dict:
    from .global_catalogue import Catalogue, default_path
    if provider == 'atlas':
        from .global_pipeline import query_atlas, Error
        try:
            return query_atlas(ecosystem, package, limit=limit)
        except Error:
            raise HTTPException(503, 'Public Atlas unavailable; coverage remains unknown.') from None
    return {**Catalogue(default_path()).query(ecosystem, package, limit=limit), 'provider': 'local'}


@app.get('/api/global-catalogue/graph')
def global_catalogue_graph(ecosystem: str = Query(min_length=1, max_length=64),
                           package: str = Query(min_length=1, max_length=256),
                           limit: int = Query(default=25, ge=1, le=100),
                           provider: Literal['local', 'atlas'] = 'local') -> dict:
    from .global_catalogue import graph_result
    return graph_result(global_catalogue_query(ecosystem, package, limit, provider))


@app.get('/api/repositories')
def repositories():
    return [{k:v for k,v in r.items() if k not in ('index','root')} for r in db.list_records('repositories')]


@app.get('/api/repositories/{id}')
def repository_detail(id:str):
    r=require('repositories',id)
    return {k:v for k,v in r.items() if k != 'root'}


def register_repo(root, name, origin, repo_id=None, cancel=None, on_publish=None):
    from .repository import index_repository
    from . import snapshots
    repo_id=repo_id or str(uuid.uuid4())
    source=snapshots.capture(repo_id, root, origin, cancel)
    index=index_repository(root, repo_id=repo_id, source_snapshot_id=source['id'])
    index.update(repo_id=repo_id,source_snapshot_id=source['id'])
    if cancel: cancel.raise_if_cancelled()
    repo={'id':repo_id,'name':str(name)[:100],'root':str(root),'origin':source['origin'],'branch':source.get('branch'),'commit':source.get('commit'),'source_snapshot_id':source['id'],'is_sample':False,'snapshot':index['snapshot'],'index':index,'policies':{s:'approve_first' for s in SOURCES},'monitoring':False,'poll_interval':300,'last_scan':None,'next_scan':None}
    store_snapshot(repo)
    def publish(con):
        if cancel: cancel.raise_if_cancelled()
        if db.get_in(con,'repositories',repo_id): raise ValueError('Repository identity already exists')
        result=db.put_in(con,'repositories',repo)
        db.index_search(result,con)
        db.put_in(con,'events',{'message':'Repository imported and indexed','repo_id':repo_id,'author':'Duck Tape'})
        if on_publish: on_publish(con,result)
        return result
    return db.atomic(publish)


@app.post('/api/repositories/import',status_code=202)
def import_repository(body:dict,request:Request):
    from . import operations
    url=str(body.get('url',''))
    if len(url)>2000 or not url.startswith('https://'): raise HTTPException(422,'Provide a public HTTPS repository URL')
    return operations.submit('import','',{'url':url,'name':str(body.get('name') or url.rstrip('/').split('/')[-1])[:100]},idempotency_key=request.headers.get('idempotency-key'))


@app.post('/api/repositories/upload',status_code=202)
async def upload_repository(request:Request,file:UploadFile=File(...)):
    from . import operations
    data=await file.read(20*1024*1024+1)
    if len(data)>20*1024*1024: raise HTTPException(413,'Archive limit is 20 MB')
    return operations.submit('upload','',{'name':Path(file.filename or 'Uploaded repository').stem},archive=data,idempotency_key=request.headers.get('idempotency-key'))


@app.post('/api/repositories/{id}/refresh',status_code=202)
def refresh(id:str):
    from . import operations
    require('repositories',id)
    return operations.submit('refresh',id,{})


@app.post('/api/repositories/{id}/replace',status_code=202)
async def replace_archive(id:str,file:UploadFile=File(...)):
    from . import operations
    require('repositories',id)
    data=await file.read(20*1024*1024+1)
    return operations.submit('refresh',id,{},archive=data)


@app.get('/api/operations')
def list_operations():
    from .workflow import list_work
    return list_work()


@app.get('/api/operations/{id}')
def get_operation(id:str):
    from .workflow import get_work
    result=get_work(id)
    if not result: raise HTTPException(404,'Operation not found')
    return result


@app.post('/api/operations/{id}/{action}')
def operation_action(id:str,action:str):
    from . import operations,workflow
    try:
        if action=='retry': return workflow.retry_work(id)
        if action=='cancel': return operations.cancel(id)
        raise HTTPException(404,'Unknown operation action')
    except LookupError as exc: raise HTTPException(404,str(exc)) from exc
    except workflow.Conflict as exc: raise HTTPException(409,str(exc)) from exc


@app.patch('/api/repositories/{id}')
def configure_repo(id:str,body:dict):
    def update(r):
        if 'policies' in body:
            if not isinstance(body['policies'],dict) or any(k not in SOURCES or v not in ('automatic','approve_first') for k,v in body['policies'].items()):
                raise HTTPException(422,'Invalid source policy')
            r['policies']={**r.get('policies',{}),**body['policies']}
        if 'monitoring' in body: r['monitoring']=bool(body['monitoring'])
        if 'poll_interval' in body: r['poll_interval']=max(30,min(86400,int(body['poll_interval'])))
        if 'security_source' in body:
            if body['security_source'] not in ('osv','catalogue_local','catalogue_atlas'):
                raise HTTPException(422,'security_source must be osv, catalogue_local or catalogue_atlas')
            r['security_source']=body['security_source']
        return r
    require('repositories',id)
    return db.mutate('repositories',id,update)


@app.get('/api/repositories/{id}/search')
def search(id:str,q:str=''):
    from .retrieval import retrieve
    repo=require('repositories',id)
    return retrieve(id,repo.get('source_snapshot_id') or repo['snapshot'],q,knowledge=approved_knowledge(id))


@app.post('/api/repositories/{id}/ask')
def ask(id:str,body:dict):
    from .retrieval import answer
    repo=require('repositories',id)
    question=str(body.get('question',''))[:4000]
    return answer(id,repo.get('source_snapshot_id') or repo['snapshot'],question,knowledge=approved_knowledge(id))


@app.get('/api/repositories/{id}/source')
def source_file(id:str,path:str,snapshot:str|None=None):
    from . import retrieval
    repo=require('repositories',id)
    snapshot=snapshot or repo.get('source_snapshot_id') or repo['snapshot']
    index=retrieval.load_index(id,snapshot)
    file=next((f for f in index['files'] if f['path']==path and f['status']=='indexed'),None)
    if file is None: raise HTTPException(404,'Source path is not indexed in this snapshot')
    return {**file,'repo_id':id,'snapshot':snapshot}


@app.get('/api/repositories/{id}/summaries')
def repository_summaries(id:str):
    repo=require('repositories',id)
    snapshot=repo.get('source_snapshot_id') or repo['snapshot']
    return [s for s in db.list_records('summaries') if s.get('repo_id')==id and s.get('snapshot_id')==snapshot]


@app.post('/api/repositories/{id}/summaries',status_code=202)
def summarize_repository(id:str):
    from . import operations
    repo=require('repositories',id)
    return operations.submit('summarize',id,{'snapshot_id':repo.get('source_snapshot_id') or repo['snapshot']})


@app.get('/api/jobs')
def jobs(repo_id:str|None=None,summary:bool=False):
    records=[j for j in db.list_records('jobs') if repo_id is None or j['repo_id']==repo_id]
    if summary:
        excluded={'context','qualification_context','description','revision_instructions','selected_decisions','plan','investigation'}
        return [{k:v for k,v in j.items() if k not in excluded} for j in records]
    return records


@app.post('/api/jobs')
@app.post('/api/intake')
def create_job(body:JobInput):
    return _create_job(body)


def _create_job(body, *, enrich=None, expected_snapshot=None, catalogue_binding=None, cancel=None):
    from .retrieval import retrieve
    evidence_repo=require('repositories',body.repo_id)
    context=retrieve({**evidence_repo['index'],'snapshot':evidence_repo['snapshot'],'repo_id':evidence_repo['id']},body.title+' '+body.description,semantic=False)
    affected=list(dict.fromkeys(item['path'] for item in context['items']))
    def create(con):
        if cancel: cancel.raise_if_cancelled()
        repo=db.get_in(con,'repositories',body.repo_id)
        if not repo: raise HTTPException(404,'Repository not found')
        if repo['snapshot']!=evidence_repo['snapshot']: raise HTTPException(409,'Repository changed during intake; retry')
        if expected_snapshot and (repo.get('source_snapshot_id') or repo['snapshot'])!=expected_snapshot:
            raise HTTPException(409,'Repository changed during catalogue matching; re-assess applicability')
        existing=[json.loads(row[0]) for row in con.execute("SELECT data FROM records WHERE kind='jobs'").fetchall()]
        if catalogue_binding:
            _check_catalogue_observation(con,body.repo_id,catalogue_binding)
            for previous in existing:
                bound=previous.get('catalogue') or {}
                if previous.get('repo_id')!=body.repo_id or bound.get('entry_id')!=catalogue_binding['entry_id']: continue
                if (bound.get('entry_revision',0)>catalogue_binding['entry_revision'] or
                    (bound.get('entry_revision')==catalogue_binding['entry_revision'] and bound.get('entry_hash')!=catalogue_binding['entry_hash'])):
                    raise HTTPException(409,'Newer or conflicting catalogue evidence already exists; re-assess applicability')
        if body.scenario and not repo.get('is_sample'): raise HTTPException(422,'Fixture scenarios require a sample repository')
        identity=body.source_key or f'{body.title.strip().lower()}|{body.description.strip()}'
        fingerprint=hashlib.sha256(json.dumps([body.title,body.description,body.source_url,body.type],ensure_ascii=False).encode()).hexdigest()
        evidence_version=fingerprint if body.source_key else repo['snapshot']
        key=hashlib.sha256(f'{body.repo_id}|{body.source}|{identity}|{evidence_version}'.encode()).hexdigest()
        automatic=repo.get('policies',{}).get(body.source)=='automatic'
        for job in existing:
            same_source=body.source_key and job.get('repo_id')==body.repo_id and job.get('source')==body.source and job.get('source_key')==body.source_key
            if job.get('dedup_key')==key or (same_source and job.get('evidence_fingerprint',fingerprint)==fingerprint):
                if job.get('snoozed_until') and job['snoozed_until']<=db.now() and job['status']=='Rejected':
                    job.update(status='Queued' if automatic else 'Awaiting approval',snoozed_until=None,preparation_approved=automatic,approval=None)
                if enrich: job=enrich(job)
                return db.put_in(con,'jobs',{**job,'evidence_fingerprint':fingerprint})
        record=body.model_dump()
        record.update(status='Queued' if automatic else 'Awaiting approval',source_snapshot_id=repo.get('source_snapshot_id'),base_snapshot=repo['snapshot'],dedup_key=key,evidence_fingerprint=fingerprint,risk='Unassessed',rationale='Explicit request scoped to this repository; investigation must establish supporting evidence.',qualification='request' if body.source in {'ticket','request'} else 'hypothesis',acceptance_criteria=[body.description or body.title],approach='Retrieve related implementation, tests and constraints; prepare a bounded patch and record checks.',affected_areas=[],approval=None,author='Alex · demo member',requires_approval=not automatic,preparation_approved=automatic)
        record.update(affected_areas=affected,rationale='Source matched repository evidence in '+', '.join(affected[:6])+'. Applicability still requires investigation.' if affected else 'No direct repository match; investigation must establish applicability.',qualification_context=context)
        if enrich: record=enrich(record)
        job=db.put_in(con,'jobs',record)
        signal=db.put_in(con,'signals',{'job_id':job['id'],'repo_id':repo['id'],'source':body.source,'original':body.description,'source_url':body.source_url,'source_identity':identity,'evidence_fingerprint':fingerprint,'dedup_key':key,'retrieved_at':db.now()})
        job=db.put_in(con,'jobs',{**job,'signal_id':signal['id']})
        db.put_in(con,'events',{'message':'Signal qualified; '+job['status'].lower(),'job_id':job['id'],'repo_id':repo['id'],'author':'Duck Tape'})
        return job
    return db.atomic(create)


@app.get('/api/jobs/{id}')
def job_detail(id:str):
    j=require('jobs',id)
    for kind in ('revisions','checks','notes','decisions','runs'):
        j[kind]=[x for x in db.list_records(kind) if x.get('job_id')==id]
    return j


@app.post('/api/jobs/{id}/{action}')
def job_action(id:str,action:str,body:dict={}):
    from . import workflow
    with intake_lock:
        job=require('jobs',id)
        if action=='apply':
            return apply_job(job,body)
        if action not in {'approve','cancel','reject','retry','revise','decide','accept','note','snooze'}:
            raise HTTPException(404,'Unknown job action')
        try:
            result=workflow.transition(id,action,body,body.get('expected_version'))
        except workflow.Conflict as exc:
            raise HTTPException(409,str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(404,str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc
        if action in ('cancel','reject','snooze'):
            from . import worker
            worker.cancel_job(id)
        return job_detail(id) if action=='note' else result


def apply_job(job,body):
    from . import integration,workflow
    try:
        return integration.apply(job['id'],body.get('revision_id'),job.get('input_fingerprint'))
    except workflow.Conflict as exc:
        raise HTTPException(409,str(exc)) from exc
    except Exception as exc:
        raise HTTPException(409,'Managed application could not finish. Inspect the recorded operation and retry: '+str(exc)[:500]) from exc


def get_revision(id,rid):
    job=require('jobs',id)
    revision=require('revisions',rid or job.get('current_revision',''))
    if revision['job_id']!=id: raise HTTPException(404,'Revision not found')
    return revision


@app.get('/api/jobs/{id}/patch')
def export_patch(id:str,revision_id:str|None=None):
    revision=get_revision(id,revision_id)
    return Response(revision['diff'],media_type='text/x-diff',headers={'Content-Disposition':f'attachment; filename="duck-tape-{id[:8]}.patch"'})


@app.get('/api/jobs/{id}/files')
def export_files(id:str,revision_id:str|None=None):
    revision=get_revision(id,revision_id)
    from . import integration
    return Response(integration.export_files(revision['id']),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="duck-tape-{id[:8]}.zip"'})


@app.get('/api/knowledge')
def knowledge(repo_id:str|None=None):
    return [k for k in db.list_records('knowledge') if not repo_id or k.get('repo_id')==repo_id]


@app.post('/api/knowledge')
@serialized
def add_knowledge(body:dict):
    from . import knowledge as service
    require('repositories',body.get('repo_id',''))
    return service.create(body)


def invalidate_knowledge(repo_id):
    for job in db.list_records('jobs'):
        if job.get('repo_id')==repo_id and job.get('status') in ACTIVE|{'Ready for review','Needs input'}:
            def invalidate(j):
                if j.get('status') in ACTIVE|{'Ready for review','Needs input'}:
                    j.update(status='Failed',error='Knowledge changed; retry to use current approved decisions.',approval=None,lease_token=None)
                return j
            db.mutate('jobs',job['id'],invalidate)
    db.event('Knowledge changed; dependent pending work invalidated',repo_id=repo_id)


@app.patch('/api/knowledge/{id}')
@serialized
def edit_knowledge(id:str,body:dict):
    from . import knowledge as service
    require('knowledge',id)
    try:
        return service.revise(id,body,body.get('expected_version'),body.get('snapshot'))
    except service.Conflict as exc:
        raise HTTPException(409,str(exc)) from exc


@app.get('/api/knowledge/{id}/history')
def knowledge_history(id:str):
    from . import knowledge as service
    require('knowledge',id)
    return service.history(id)


@app.post('/api/knowledge/{id}/approve')
@serialized
def approve_knowledge(id:str,body:dict):
    from . import knowledge as service
    entry=require('knowledge',id)
    repo=require('repositories',entry['repo_id'])
    try:
        return service.approve(id,body.get('version'),body.get('snapshot'))
    except service.Conflict as exc:
        raise HTTPException(409,str(exc)) from exc


@app.post('/api/jobs/{id}/discussions/{discussion_id}/promote')
@serialized
def promote_knowledge(id:str,discussion_id:str):
    from . import knowledge as service
    require('jobs',id)
    return service.promote(id,discussion_id)


@app.get('/api/activity')
def activity(): return list(reversed(db.list_records('events')))[0:300]


@app.get('/api/integrations')
def integration_status():
    """Local readiness only: this route never probes or uploads to a cloud service."""
    from . import data_platforms, providers
    config = providers.configuration()
    integrations = data_platforms.status()
    for kind, variable in (('adaption', 'ADAPTION_API_KEY'), ('huggingface', 'HF_TOKEN')):
        enabled = config['kind'] == kind
        endpoint, official = urlparse(config['endpoint']), urlparse(providers.SPONSOR_ENDPOINTS[kind])
        native_origin = (endpoint.scheme, endpoint.netloc.lower()) == (official.scheme, official.netloc.lower())
        credential = os.getenv('STEWARD_MODEL_KEY') or (native_origin and os.getenv(variable))
        configured = enabled and bool(config['model']) and bool(credential)
        integrations[kind] = {
            'enabled': enabled,
            'configured': configured,
            'state': 'configured' if configured else 'needs_configuration' if enabled else 'not_selected',
            'detail': 'Selected configuration is present; run provider health and a checked task to verify it.' if configured else
                      'Set a model and a server-side credential valid for this endpoint; no remote request was made.' if enabled else
                      'Select this provider in Inference providers to use it. No remote request was made.',
        }
    return {'schema_version': 1, 'live_verified': False, 'integrations': integrations}


@app.get('/api/settings')
@app.post('/api/settings/health')
def settings():
    from .providers import health as provider_health
    from . import sandbox
    return {'providers':provider_health(),'sandbox':sandbox.health(),'authentication':'Local single workspace; demo members are labels, not authentication.','monitoring':health()['monitoring'],'services':db.list_records('services')}


@app.patch('/api/settings/provider')
def configure_provider(body:dict):
    from . import providers
    permitted={'endpoint','kind','model','small_model','context_limit','embedding_model','embedding_endpoint'}
    if set(body)-permitted: raise HTTPException(422,'Only non-secret provider fields can be saved; credentials belong in the server environment')
    config={**providers.configuration(),**body}
    for field in ('endpoint','embedding_endpoint'):
        endpoint=config.get(field,'')
        if field=='embedding_endpoint' and endpoint=='': continue
        if not isinstance(endpoint,str) or len(endpoint)>2000 or any(ord(char)<=32 for char in endpoint):
            raise HTTPException(422,'Provider endpoint must be a bounded URL without whitespace')
        try:
            parsed=urlparse(endpoint)
            if parsed.port is not None and not 1<=parsed.port<=65535: raise ValueError('port')
        except ValueError as exc:
            raise HTTPException(422,'Provider endpoint has an invalid host or port') from exc
        if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise HTTPException(422,'Provider endpoint needs a HTTP(S) URL without credentials or query parameters')
        if parsed.scheme=='http' and parsed.hostname not in {'localhost','127.0.0.1','::1'}:
            raise HTTPException(422,'Remote providers require HTTPS')
    if config.get('kind') not in {'ollama','openai','adaption','huggingface'}: raise HTTPException(422,'Choose Ollama, OpenAI-compatible, Adaption or Hugging Face inference')
    for name in ('model','small_model','embedding_model'):
        if not isinstance(config.get(name,''),str) or len(config.get(name,''))>200: raise HTTPException(422,'Model identifiers must be bounded strings')
    if type(config.get('context_limit')) is not int or not 1024<=config['context_limit']<=131072: raise HTTPException(422,'Context limit must be 1024–131072')
    saved=db.put('provider_settings',{'id':'default',**{k:config[k] for k in permitted}})
    managed=[key for key,env in {'endpoint':'STEWARD_MODEL_ENDPOINT','kind':'STEWARD_MODEL_PROVIDER','model':'STEWARD_MODEL','small_model':'STEWARD_SMALL_MODEL','context_limit':'STEWARD_CONTEXT_LIMIT','embedding_model':'STEWARD_EMBEDDING_MODEL','embedding_endpoint':'STEWARD_EMBEDDING_ENDPOINT'}.items() if env in os.environ]
    db.event('Non-secret inference configuration updated')
    return {'configuration':providers.configuration(),'saved':saved,'environment_overrides':managed}


@app.post('/api/sample/reset')
def reset_sample():
    from .sample import seed_sample,seed_jobs
    repo=seed_sample(force_new=True)
    seed_jobs(repo)
    db.index_search(repo)
    return repo


@app.post('/api/repositories/{id}/scan',status_code=202)
def scan_repo(id:str):
    from . import operations
    require('repositories',id)
    return operations.submit('scan',id,{})


def run_scan(id:str,cancel=None):
    from .signals import scan
    repo=refresh_repo(id,cancel=cancel)
    security_source=repo.get('security_source','osv')
    results=scan(repo,cancel=cancel,include_security=security_source=='osv')
    catalogue=None
    if security_source!='osv':
        catalogue=_catalogue_assessment(id,{'provider':'atlas' if security_source=='catalogue_atlas' else 'local'},cancel=cancel,all_entries=True)
    if cancel: cancel.raise_if_cancelled()
    with intake_lock:
        created=[]
        if catalogue:
            for entry in catalogue['entries']:
                if cancel: cancel.raise_if_cancelled()
                outcome=_applicability_intake(id,{'entry':entry,'snapshot_id':catalogue['profile']['snapshot_id']},cancel=cancel)
                created.extend(outcome['jobs'])
            results['catalogue']=catalogue
            results['gaps']+=catalogue['limitations']
            results['complete']=False  # A bounded catalogue never proves complete vulnerability coverage.
        for signal in results['signals']:
            created.append(_create_job(JobInput(repo_id=id,**signal),cancel=cancel))
        interval=repo.get('poll_interval',300)
        db.mutate('repositories',id,lambda r:{**r,'last_scan':db.now(),'last_success':db.now() if results.get('complete') else r.get('last_success'),'last_error':None if results.get('complete') else '; '.join(results.get('gaps',[]))[:2000],'next_scan':(datetime.now(timezone.utc)+timedelta(seconds=interval)).isoformat(),'scan_report':results})
        db.event(f'Scan finished: {len(created)} sourced findings (deduplicated intake)',repo_id=id)
        return {'jobs':created,**results}


@app.post('/api/repositories/{id}/research')
def research(id:str,body:dict):
    require('repositories',id)
    text=str(body.get('text',''))[:30000]
    url=body.get('url')
    if url:
        from .repository import safe_fetch
        fetched=safe_fetch(url)
        text=fetched.decode('utf-8',errors='replace')[:30000] if isinstance(fetched,bytes) else str(fetched)[:30000]
    if not text.strip(): raise HTTPException(422,'Paste source text or provide a public HTTPS URL')
    return create_job(JobInput(repo_id=id,title=str(body.get('title','Research proposal'))[:200],description='Unverified research hypothesis. Investigate repository applicability and define a measurable comparison. Source evidence:\n'+text,source='research',type='performance',source_url=url))


def _current_profile(id:str,snapshot_id:str|None=None):
    from .applicability import profile_for
    repo=require('repositories',id)
    current=repo.get('source_snapshot_id') or repo['snapshot']
    if snapshot_id not in (None,current,repo['snapshot']): raise HTTPException(409,'Snapshot is not current; re-assess applicability')
    return profile_for(id,current)


@app.get('/api/repositories/{id}/graph')
def repository_graph(id:str,engine:str='auto'):
    """Snapshot-bound knowledge map: Graphify when installed, else the labelled built-in symbol graph."""
    from .graphify_graph import GraphifyUnavailable, knowledge_graph
    repo=require('repositories',id)
    try: return knowledge_graph(id,repo.get('source_snapshot_id') or repo['snapshot'],engine)
    except GraphifyUnavailable as exc: raise HTTPException(503,str(exc)) from None
    except ValueError as exc: raise HTTPException(422,str(exc)) from None


@app.get('/api/repositories/{id}/agent-context')
def repository_agent_context(id:str,query:str,budget_tokens:int=3500,job_id:str|None=None,snapshot_id:str|None=None):
    """graph_context v1 for agents and the UI: local, snapshot-bound, null means unknown."""
    from .agent_context import agent_graph_context
    repo=require('repositories',id)
    current=repo.get('source_snapshot_id') or repo['snapshot']
    if snapshot_id not in (None,current,repo['snapshot']): raise HTTPException(409,'Snapshot is not current; rebuild the graph context')
    job=None
    if job_id:
        job=require('jobs',job_id)
        if job.get('repo_id')!=id: raise HTTPException(404,'Job not found in this repository')
    try: return agent_graph_context(id,current,query,budget_tokens,job=job)
    except ValueError as exc: raise HTTPException(422,str(exc)) from None


@app.post('/api/repositories/{id}/atlas-graph-mirror')
def repository_atlas_graph_mirror(id:str):
    """Explicit opt-in: mirror the current snapshot's graph and approved knowledge to private Atlas."""
    from . import atlas_graph
    from .data_platforms import DataPlatformError
    repo=require('repositories',id)
    current=repo.get('source_snapshot_id') or repo['snapshot']
    try: return atlas_graph.mirror(id,current)
    except atlas_graph.MirrorRefused as exc: raise HTTPException(409,str(exc)) from None
    except DataPlatformError as exc: raise HTTPException(503,str(exc)) from None


@app.get('/api/repositories/{id}/profile')
def repository_profile(id:str):
    return _current_profile(id)


@app.post('/api/repositories/{id}/applicability')
def repository_applicability(id:str,body:dict):
    """Match public catalogue entries locally; nothing is uploaded or queued."""
    from .applicability import catalogue_entry, match
    entries=body.get('entries')
    if not isinstance(entries,list) or not 1<=len(entries)<=100: raise HTTPException(422,'Provide 1 to 100 catalogue entries')
    profile=_current_profile(id,body.get('snapshot_id'))
    try: matches=[match(profile,catalogue_entry(entry)) for entry in entries]
    except ValueError as exc: raise HTTPException(422,str(exc)) from None
    return {'profile':{key:profile[key] for key in ('repo_id','snapshot_id','profile_hash','gaps','capabilities')},'matches':matches}


@app.post('/api/repositories/{id}/applicability/catalogue')
def applicability_catalogue(id:str,body:dict):
    """Match the public catalogue against this repository's resolved dependencies; creates no jobs.

    Body: {snapshot_id?, provider: 'local'|'atlas' (default 'local'), limit?: 1..100 per package}.
    provider=atlas sends the exact ecosystem/package names from the private profile to the public
    catalogue cluster, so it is an explicit choice; local reads only the separate local catalogue file.
    An Atlas failure is HTTP 503 with unknown coverage, never a silent local fallback. Coverage is always
    unknown: no entry is not safe. `entries` carries the matcher-shape entry for each matched record,
    ready for the explicit /applicability/intake route.
    """
    return _catalogue_assessment(id,body)


def _catalogue_assessment(id,body,*,include_graphs=False,cancel=None,all_entries=False):
    from .applicability import from_global_entry, match
    provider=body.get('provider','local')
    limit=body.get('limit',25)
    if provider not in ('local','atlas'): raise HTTPException(422,"provider must be 'local' or 'atlas'")
    if type(limit) is not int or not 1<=limit<=100: raise HTTPException(422,'limit must be an integer from 1 to 100')
    profile=_current_profile(id,body.get('snapshot_id'))
    names=sorted({(item['ecosystem'],item['name']) for item in profile['packages']})
    queried,errors,limitations,found,truncated=[],[],[],{},False
    graphs=[]
    from .global_pipeline import Error, query_atlas
    if provider=='local':
        from .global_catalogue import Catalogue, default_path
        catalogue=Catalogue(default_path())
    for ecosystem,package in names[:200]:
        if cancel: cancel.raise_if_cancelled()
        try:
            if provider=='atlas': result=query_atlas(ecosystem,package,limit=limit)
            else: result=catalogue.query(ecosystem,package,limit=limit)
        except Error: raise HTTPException(503,'Public Atlas unavailable; coverage remains unknown.') from None
        except ValueError as exc:
            errors.append({'ecosystem':ecosystem,'package':package,'detail':str(exc)[:300]})
            continue
        queried.append({'ecosystem':ecosystem,'package':package,'entries':len(result['entries']),'truncated':result['truncated']})
        limitations+=result['limitations']
        if include_graphs:
            from .global_catalogue import graph_result
            graphs.append(graph_result(result))
        for record in result['entries']: found.setdefault((record['canonical_id'],record['material_revision']),record)
        truncated=truncated or result['truncated']
    if len(names)>200: limitations.append(f'Only the first 200 of {len(names)} resolved packages were queried.')
    if truncated: limitations.append('At least one package has more catalogue entries than the limit; raise limit or narrow the query.')
    if profile['gaps']: limitations.append(f'{len(profile["gaps"])} unresolved manifest gap(s) in the profile; absence of a match is not absence of exposure.')
    if provider=='atlas': limitations.append("Atlas readers cannot see another machine's failed source refresh; stored freshness is the only freshness shown.")
    entries=[from_global_entry(record) for record in found.values()]
    try: matches=[match(profile,entry) for entry in entries]
    except ValueError as exc: raise HTTPException(422,str(exc)) from None
    return {**({'graphs':graphs} if include_graphs else {}),'profile':{**{key:profile[key] for key in ('repo_id','snapshot_id','profile_hash','gaps','capabilities')},'package_count':len(profile['packages'])},
            'provider':provider,'queried':queried,'matches':matches,'entries':[entry for entry,item in zip(entries,matches) if all_entries or item['status']=='matched'],
            'coverage':'unknown','limitations':list(dict.fromkeys(limitations+['Catalogue coverage is partial; no match does not mean unaffected.'])),'errors':errors}


def _check_catalogue_observation(con,repo_id,binding,*,observe=False):
    key=hashlib.sha256(json.dumps([repo_id,binding['entry_id']]).encode()).hexdigest()
    previous=db.get_in(con,'catalogue_observations',key)
    if previous and (previous['entry_revision']>binding['entry_revision'] or
                     (previous['entry_revision']==binding['entry_revision'] and previous['entry_hash']!=binding['entry_hash'])):
        raise HTTPException(409,'Newer or conflicting catalogue evidence already observed; re-assess applicability')
    if observe:
        db.put_in(con,'catalogue_observations',{'id':key,'repo_id':repo_id,**binding})


def _invalidate_catalogue_jobs(repo_id:str,entry_id:str,current:set[str],reason:str,*,expected_snapshot=None,cancel=None,revision=None,entry_hash=None):
    """Fail open jobs for this catalogue entry whose evidence no longer matches the current revision/match."""
    def invalidate(con):
        if cancel: cancel.raise_if_cancelled()
        repo=db.get_in(con,'repositories',repo_id)
        if expected_snapshot and (repo.get('source_snapshot_id') or repo['snapshot'])!=expected_snapshot:
            raise HTTPException(409,'Repository changed during catalogue matching; re-assess applicability')
        if entry_hash:
            _check_catalogue_observation(con,repo_id,{'entry_id':entry_id,'entry_revision':revision,'entry_hash':entry_hash})
        stale=[]
        for row in con.execute("SELECT data FROM records WHERE kind='jobs'").fetchall():
            job=json.loads(row[0])
            if revision is not None and (job.get('catalogue') or {}).get('entry_revision',0)>revision:
                continue
            bound=job.get('catalogue') or {}
            if entry_hash and job.get('repo_id')==repo_id and bound.get('entry_id')==entry_id and bound.get('entry_revision')==revision and bound.get('entry_hash')!=entry_hash:
                raise HTTPException(409,'Conflicting catalogue evidence; re-assess applicability')
            if (job.get('repo_id')==repo_id and job.get('status') not in TERMINAL|{'Failed'} and job.get('source') in {'security','analysis','research'}
                    and str(job.get('source_key') or '').startswith(entry_id+':') and job.get('evidence_fingerprint') not in current):
                db.put_in(con,'jobs',{**job,'status':'Failed','error':reason,'approval':None,'stale':True,'lease_token':None,'lease_until':0})
                stale.append(job['id'])
        if stale: db.put_in(con,'events',{'message':f'Catalogue entry {entry_id[:80]} changed; {len(stale)} dependent job(s) invalidated','repo_id':repo_id,'author':'Duck Tape'})
        return stale
    return db.atomic(invalidate)


@app.post('/api/repositories/{id}/applicability/intake')
def applicability_intake(id:str,body:dict):
    """Re-match one entry on the current snapshot, then use existing intake and preparation policies.

    Open jobs created from an earlier revision, or from an entry that is now withdrawn or no
    longer matches, are invalidated; rejected/snoozed decisions on unchanged evidence are kept.
    """
    return _applicability_intake(id,body)


def _applicability_intake(id,body,*,cancel=None):
    from .applicability import catalogue_entry, intake_payloads, match
    if cancel: cancel.raise_if_cancelled()
    profile=_current_profile(id,body.get('snapshot_id'))
    try:
        entry=catalogue_entry(body.get('entry'))
        found=match(profile,entry)
    except ValueError as exc: raise HTTPException(422,str(exc)) from None
    def observe(con):
        if cancel: cancel.raise_if_cancelled()
        repo=db.get_in(con,'repositories',id)
        if (repo.get('source_snapshot_id') or repo['snapshot'])!=profile['snapshot_id']:
            raise HTTPException(409,'Repository changed during catalogue matching; re-assess applicability')
        _check_catalogue_observation(con,id,{key:found[key] for key in ('entry_id','entry_revision','entry_hash')},observe=True)
    db.atomic(observe)
    try: payloads=intake_payloads(found,entry)
    except ValueError as exc:
        stale=_invalidate_catalogue_jobs(id,found['entry_id'],set(),f'Catalogue entry is no longer a current match ({exc}); re-assess applicability.',
                                         expected_snapshot=profile['snapshot_id'],cancel=cancel,revision=found['entry_revision'],entry_hash=found['entry_hash'])
        return {'match':found,'jobs':[],'invalidated':stale,'detail':str(exc)}
    binding={'entry_id':found['entry_id'],'entry_revision':found['entry_revision'],'entry_hash':found['entry_hash'],'entry_kind':found['entry_kind'],
             'match_key':found['match_key'],'profile_hash':found['profile_hash'],'snapshot_id':found['snapshot_id']}
    evidence={key:found[key] for key in ('status','evidence','citations','rationale','acceptance_criteria','limitations','cwe_ids','affected_symbols')}
    def bind(job):
        if job.get('status') in TERMINAL: return job
        if job.get('status')=='Failed' and job.get('stale'):
            return {**job,'catalogue':binding,'applicability':evidence,'stale':False,'approval':None}
        if job.get('status')=='Failed' or job.get('catalogue')==binding: return job
        bound={**job,'catalogue':binding,'applicability':evidence}
        if not job.get('plan_hash'): bound.update(rationale=found['rationale'],acceptance_criteria=found['acceptance_criteria'])
        return bound
    jobs=[_create_job(JobInput(**payload),enrich=bind,expected_snapshot=profile['snapshot_id'],catalogue_binding=binding,cancel=cancel) for payload in payloads]
    stale=_invalidate_catalogue_jobs(id,found['entry_id'],{job.get('evidence_fingerprint') for job in jobs},
                                     f'Catalogue entry changed to revision {found["entry_revision"]}; retry against the current revision.',
                                     expected_snapshot=profile['snapshot_id'],cancel=cancel,revision=found['entry_revision'],entry_hash=found['entry_hash'])
    return {'match':found,'jobs':jobs,'invalidated':stale}


@app.post('/api/repositories/{id}/knowledge-map')
def repository_knowledge_map(id:str,body:dict):
    """Join existing graphs through cited dependency matches; no cloud write or job creation."""
    if body.get('engine','auto') not in ('auto','builtin','graphify'):
        raise HTTPException(422,'engine must be auto, builtin or graphify')
    assessment=_catalogue_assessment(id,body,include_graphs=True)
    graph=repository_graph(id,body.get('engine','auto'))
    if graph['snapshot_id']!=assessment['profile']['snapshot_id']:
        raise HTTPException(409,'Repository changed while assembling knowledge; retry')
    public=assessment.pop('graphs')
    files={node['path']:node['id'] for node in graph['nodes'] if node['id'].startswith('file:')}
    entries={node['entry']['canonical_id']:node['id'] for group in public for node in group['nodes'] if node['type']=='entry'}
    links=[{'source':files[citation['path']],'target':entries[found['entry_id']],
            'relation':'affected_dependency','status':found['status'],'entry_status':found['entry_status'],
            'evidence':citation,'exploitability':'unknown'}
           for found in assessment['matches'] if found['status']=='matched' and found['entry_id'] in entries
           for citation in found['citations'] if citation['path'] in files]
    return {'repo_id':id,'snapshot_id':graph['snapshot_id'],'repository':graph,'public':public,
            'links':links,'applicability':assessment,'coverage':'unknown'}


DIST=Path(__file__).resolve().parents[1]/'frontend'/'dist'
if DIST.exists():
    app.mount('/assets',StaticFiles(directory=DIST/'assets'),name='assets')


@app.get('/{path:path}')
def frontend(path:str):
    if path.startswith('api/'): raise HTTPException(404,'Unknown API route')
    if (DIST/'index.html').exists(): return FileResponse(DIST/'index.html')
    return {'message':'Duck Tape backend ready. Build frontend: cd frontend && npm install && npm run build'}
