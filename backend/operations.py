"""Durable import, refresh and scan queue, executed by the worker process."""
import hashlib
import json
import threading
import time
import uuid
from pathlib import Path
from . import db, workflow
from .contracts import CancellationToken

_stop = threading.Event()
_thread = None
_owner = 'operations-' + uuid.uuid4().hex


def submit(kind, subject, payload, *, archive=None, idempotency_key=None):
    if kind not in {'import', 'upload', 'refresh', 'scan', 'summarize', 'audit'}:
        raise ValueError('Unsupported operation')
    payload = dict(payload)
    if archive is not None:
        if len(archive) > 20 * 1024 * 1024:
            raise ValueError('Archive exceeds 20 MiB upload limit')
        digest = hashlib.sha256(archive).hexdigest()
        target = db.DATA_DIR / 'ingestion-input' / (digest + '.zip')
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(archive)
        payload['archive_hash'] = digest
    if idempotency_key is not None and (not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200):
        raise ValueError('Idempotency key must contain 1–200 characters')
    identity = idempotency_key or uuid.uuid4().hex
    item = workflow.enqueue(kind, subject, payload, f'{kind}:{subject}:{identity}')
    return {**item, 'operation_id': item['id']}


def cancel(id):
    def apply(con):
        row = con.execute('SELECT data FROM work_items WHERE id=?', (id,)).fetchone()
        if not row:
            raise LookupError('Operation not found')
        item = json.loads(row[0])
        if item['status'] not in {'queued', 'running'}:
            raise workflow.Conflict('Operation already finished')
        item.update(status='cancelled', lease_token=None, lease_until=0)
        return workflow._save_work(con, item)
    return db.atomic(apply)


def advance(id, owner=None):
    owner = owner or _owner
    item = workflow.claim_work(id, owner)
    if not item:
        return workflow.get_work(id)
    token = item['lease_token']
    stopped = threading.Event()
    owned_by_loop = threading.current_thread() is _thread
    def lost():
        current = workflow.get_work(id)
        return (owned_by_loop and _stop.is_set()) or not current or current.get('lease_token') != token or current['status'] != 'running'
    cancel_token = CancellationToken(predicate=lost, deadline=time.monotonic() + 1800)
    def heartbeat():
        while not stopped.wait(20):
            try:
                workflow.checkpoint_work(id, owner, lease_token=token)
            except workflow.Conflict:
                return
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    def checkpoint(stage):
        cancel_token.raise_if_cancelled()
        workflow.checkpoint_work(id, owner, {'stage': stage}, lease_token=token)
    def complete_publication(con,result):
        row=con.execute('SELECT data FROM work_items WHERE id=?',(id,)).fetchone()
        current=json.loads(row[0]) if row else None
        if not current or current.get('lease_token')!=token or current['status']!='running' or current.get('lease_until',0)<=time.time():
            raise workflow.Conflict('Operation ownership changed before publication')
        workflow._save_work(con,{**current,'status':'complete','checkpoint':{'stage':'complete'},'finished_at':db.now(),
            'lease_until':0,'result':{k:v for k,v in result.items() if k not in {'root','index'}}})
    try:
        from . import main, repository
        payload = item['payload']
        checkpoint('fetching' if item['kind'] in {'import', 'upload'} else 'refreshing')
        if item['kind'] in {'import', 'upload'}:
            repo_id = id
            result = db.get('repositories', repo_id)
            if result is None:
                root = db.DATA_DIR / 'repositories' / repo_id / token
                if item['kind'] == 'import':
                    origin = repository.import_git(payload['url'], root, cancel=cancel_token)
                else:
                    data = (db.DATA_DIR / 'ingestion-input' / (payload['archive_hash'] + '.zip')).read_bytes()
                    origin = repository.import_zip(data, root)
                checkpoint('indexing')
                result = main.register_repo(root, payload.get('name') or 'Imported repository', origin, repo_id=repo_id, cancel=cancel_token,on_publish=complete_publication)
        elif item['kind'] == 'refresh':
            data = None
            if payload.get('archive_hash'):
                data = (db.DATA_DIR / 'ingestion-input' / (payload['archive_hash'] + '.zip')).read_bytes()
            result = main.refresh_repo(item['subject'], replacement=data, cancel=cancel_token,on_publish=complete_publication)
        elif item['kind'] == 'summarize':
            from . import summaries
            checkpoint('summarizing')
            result={'summaries':summaries.build(item['subject'],payload['snapshot_id'],cancel_token)}
        elif item['kind'] == 'audit':
            from . import reviews
            checkpoint('reviewing')
            result = reviews.execute(item['subject'], payload, cancel_token)
            def publish_review(con):
                repo = db.get_in(con, 'repositories', item['subject'])
                if not repo or (repo.get('source_snapshot_id') or repo['snapshot']) != payload['snapshot_id']:
                    raise workflow.Conflict('Repository changed before review publication; start a new review')
                complete_publication(con, result)
            db.atomic(publish_review)
        else:
            result = main.run_scan(item['subject'], cancel=cancel_token)
        if workflow.get_work(id)['status']=='complete':
            return workflow.get_work(id)
        checkpoint('published')
        if isinstance(result, dict):
            result = {key: value for key, value in result.items() if key not in {'root','index'}}
        workflow.checkpoint_work(id, owner, {'stage': 'complete'}, lease_token=token, status='complete', result=result, finished_at=db.now())
    except Exception as exc:
        try:
            interrupted=owned_by_loop and _stop.is_set()
            workflow.checkpoint_work(id, owner, lease_token=token, status='queued' if interrupted else 'failed', error='Worker stopped; operation remains queued' if interrupted else str(exc)[:2000], finished_at=None if interrupted else db.now())
            if item['kind'] == 'scan':
                db.mutate('repositories', item['subject'], lambda r: {**r, 'last_error': str(exc)[:2000]})
        except workflow.Conflict:
            pass
    finally:
        stopped.set()
        thread.join(timeout=1)
    return workflow.get_work(id)


def loop():
    recovered_at=0
    while not _stop.is_set():
        db.put('services', {'id': 'operations', 'heartbeat': db.now(), 'status': 'running'})
        if time.monotonic()-recovered_at>30:
            from . import integration
            integration.recover_pending()
            recovered_at=time.monotonic()
        for item in workflow.list_work():
            if _stop.is_set():
                break
            if item['status'] == 'queued' or (item['status'] == 'running' and item.get('lease_until', 0) < time.time()):
                advance(item['id'])
        _stop.wait(.5)


def start_operations():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=loop, name='steward-operations', daemon=True)
    _thread.start()


def stop_operations():
    _stop.set()
    if _thread:
        _thread.join(timeout=3)
    db.put('services', {'id': 'operations', 'heartbeat': db.now(), 'status': 'stopped'})
