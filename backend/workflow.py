"""Transactional state changes and durable work ownership."""
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from . import db

STATES = {'Proposed', 'Awaiting approval', 'Queued', 'Investigating', 'Needs input',
          'Implementing', 'Verifying', 'Ready for review', 'Failed', 'Cancelled', 'Rejected', 'Completed'}
TERMINAL = {'Cancelled', 'Rejected', 'Completed'}
ACTIVE = {'Investigating', 'Implementing', 'Verifying'}
AUTHOR = 'Alex · demo member'


class Conflict(ValueError):
    pass


def input_fingerprint(inputs):
    if hasattr(inputs, 'model_dump'):
        inputs = inputs.model_dump(mode='json')
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def checks_fingerprint(checks):
    return input_fingerprint(sorted(checks, key=lambda item: item['id']))


def _require(con, kind, id):
    value = db.get_in(con, kind, id)
    if value is None:
        raise LookupError(f'{kind} record not found')
    return value


def _text(payload):
    value = str(payload.get('text', '')).strip()
    if not 1 <= len(value) <= 20000:
        raise ValueError('Text needs 1–20000 characters')
    return value


def transition(job_id, action, payload, expected_version=None):
    def apply(con):
        j = _require(con, 'jobs', job_id)
        if expected_version is not None and j.get('record_version', 0) != expected_version:
            raise Conflict('Job changed; reload before performing this action')
        status = j['status']
        allowed = {
            'approve': {'Awaiting approval', 'Proposed'}, 'retry': {'Failed', 'Cancelled'},
            'revise': {'Ready for review'}, 'decide': {'Needs input'}, 'accept': {'Ready for review'},
            'cancel': STATES - TERMINAL, 'reject': STATES - TERMINAL, 'snooze': STATES - TERMINAL,
            'note': STATES,
        }
        if action not in allowed or status not in allowed[action]:
            raise Conflict(f'Illegal transition: {action} from {status}')
        if action == 'note':
            db.put_in(con, 'notes', {'job_id': job_id, 'text': _text(payload), 'author': AUTHOR, 'kind': 'team-note'})
            return j
        if action == 'approve':
            j.update(status='Queued', preparation_approved=True, error=None,
                     preparation_approval={'plan_hash': j.get('plan_hash'), 'at': db.now(), 'author': AUTHOR})
        elif action in {'cancel', 'reject', 'snooze'}:
            j.update(status='Cancelled' if action == 'cancel' else 'Rejected', lease_token=None, lease_until=0, approval=None)
            if action == 'snooze':
                until = payload.get('until')
                try:
                    parsed = datetime.fromisoformat(until) if isinstance(until, str) else None
                    if parsed is None or parsed.tzinfo is None or parsed <= datetime.now(timezone.utc):
                        raise ValueError()
                except ValueError:
                    raise ValueError('Snooze requires a future UTC timestamp')
                j['snoozed_until'] = parsed.astimezone(timezone.utc).isoformat()
        elif action == 'retry':
            repo = _require(con, 'repositories', j['repo_id'])
            if j.get('base_snapshot') != repo['snapshot']:
                j['current_revision'] = None
            j.update(status='Queued' if j.get('preparation_approved') else 'Awaiting approval',
                     base_snapshot=repo['snapshot'], source_snapshot_id=repo.get('source_snapshot_id'),
                     error=None, stale=False, lease_token=None, lease_until=0, approval=None,
                     retry_generation=j.get('retry_generation', 0) + 1)
        elif action == 'revise':
            text = _text(payload)
            if payload.get('revision_id') and payload['revision_id'] != j.get('current_revision'):
                raise Conflict('Revision changed; reload before requesting revision')
            j.update(status='Queued', revision_instructions=[*j.get('revision_instructions', []), text],
                     approval=None, error=None, lease_token=None, lease_until=0)
            db.put_in(con, 'notes', {'job_id': job_id, 'text': text, 'author': AUTHOR, 'kind': 'revision-request'})
        elif action == 'decide':
            if payload.get('decision_id')!=j.get('decision_id'):
                raise Conflict('Decision changed; answer the current pending question')
            d = _require(con, 'decisions', payload.get('decision_id', ''))
            if d.get('selected') is not None:
                raise Conflict('Current decision has already been answered')
            option = payload.get('option')
            if d['job_id'] != job_id or option not in [o['id'] for o in d['options']]:
                raise ValueError('Invalid decision option')
            d.update(selected=option, author=AUTHOR, selected_at=db.now())
            db.put_in(con, 'decisions', d)
            j.update(status='Queued', approval=None,
                     selected_decisions={**j.get('selected_decisions', {}), d['id']: option})
        elif action == 'accept':
            rid = payload.get('revision_id')
            if not rid or rid != j.get('current_revision'):
                raise Conflict('Approval must name the current revision')
            revision = _require(con, 'revisions', rid)
            repo = _require(con, 'repositories', j['repo_id'])
            if revision.get('job_id') != job_id or revision['base_snapshot'] != repo['snapshot'] or j.get('stale'):
                raise Conflict('Patch base is stale')
            if revision.get('input_fingerprint') and revision['input_fingerprint'] != j.get('input_fingerprint'):
                raise Conflict('Patch inputs changed; prepare again')
            j['approval'] = {'revision_id': rid, 'patch_hash': revision['patch_hash'],
                             'input_fingerprint': revision.get('input_fingerprint'),
                             'checks_hash': checks_fingerprint([json.loads(row[0]) for row in con.execute("SELECT data FROM records WHERE kind='checks'").fetchall() if json.loads(row[0]).get('revision_id') == rid]),
                             'author': AUTHOR, 'at': db.now()}
        result = db.put_in(con, 'jobs', j)
        db.put_in(con, 'events', {'message': f'Job action: {action}', 'job_id': job_id, 'repo_id': j['repo_id'], 'author': AUTHOR})
        return result
    return db.atomic(apply)


def enqueue(kind, subject, payload, idempotency_key):
    def insert(con):
        row = con.execute('SELECT data FROM work_items WHERE idempotency_key=?', (idempotency_key,)).fetchone()
        if row:
            return json.loads(row[0])
        for row in con.execute('SELECT data FROM work_items WHERE kind=? AND subject=?', (kind, subject)).fetchall():
            pending = json.loads(row[0])
            if pending['status'] in {'queued', 'running'} and pending['payload'] == payload:
                return pending
        item = {'id': str(uuid.uuid4()), 'kind': kind, 'subject': subject, 'payload': payload,
                'idempotency_key': idempotency_key, 'status': 'queued', 'created_at': db.now(),
                'updated_at': db.now(), 'attempts': 0, 'checkpoint': {}, 'lease_until': 0}
        con.execute('INSERT INTO work_items VALUES(?,?,?,?,?)', (item['id'], kind, subject, idempotency_key, json.dumps(item)))
        return item
    return db.atomic(insert)


def get_work(id):
    with db.connect() as con:
        row = con.execute('SELECT data FROM work_items WHERE id=?', (id,)).fetchone()
    return json.loads(row[0]) if row else None


def list_work():
    with db.connect() as con:
        return [json.loads(row[0]) for row in con.execute('SELECT data FROM work_items ORDER BY rowid')]


def _save_work(con, item):
    item['updated_at'] = db.now()
    con.execute('UPDATE work_items SET data=? WHERE id=?', (json.dumps(item), item['id']))
    return item


def claim_work(id, owner):
    def claim(con):
        row = con.execute('SELECT data FROM work_items WHERE id=?', (id,)).fetchone()
        if not row:
            return None
        item = json.loads(row[0])
        if item['status'] != 'queued' and not (item['status'] == 'running' and item.get('lease_until', 0) < time.time()):
            return None
        item.update(status='running', owner=owner, lease_token=str(uuid.uuid4()),
                    lease_until=time.time() + 90, attempts=item['attempts'] + 1)
        return _save_work(con, item)
    return db.atomic(claim)


def checkpoint_work(id, owner, checkpoint=None, *, lease_token=None, **fields):
    def update(con):
        row = con.execute('SELECT data FROM work_items WHERE id=?', (id,)).fetchone()
        item = json.loads(row[0]) if row else None
        if (not item or item.get('owner') != owner or item['status'] != 'running'
                or not lease_token or item.get('lease_token') != lease_token
                or item.get('lease_until', 0) <= time.time()):
            raise Conflict('Work ownership changed')
        item.update(fields, lease_until=time.time() + 90)
        if checkpoint is not None:
            item['checkpoint'] = checkpoint
        return _save_work(con, item)
    return db.atomic(update)


def recover_job(id):
    def recover(con):
        job = _require(con, 'jobs', id)
        if job['status'] not in ACTIVE or job.get('lease_until', 0) >= time.time():
            return job
        token = job.get('lease_token')
        for row in con.execute("SELECT data FROM records WHERE kind='runs'").fetchall():
            run = json.loads(row[0])
            if run.get('job_id') == id and run.get('lease_token') == token and run.get('status') == 'running':
                db.put_in(con, 'runs', {**run, 'status': 'interrupted', 'finished_at': db.now(),
                                       'error': 'Worker lease expired; work will resume from persisted inputs'})
        return db.put_in(con, 'jobs', {**job, 'status': 'Queued', 'lease_token': None,
                                      'lease_until': 0, 'error': 'Recovered expired worker lease'})
    return db.atomic(recover)


def retry_work(id):
    def update(con):
        row = con.execute('SELECT data FROM work_items WHERE id=?', (id,)).fetchone()
        if not row:
            raise LookupError('Operation not found')
        item = json.loads(row[0])
        if item['status'] not in {'failed', 'cancelled'}:
            raise Conflict('Only failed operations can retry')
        item.update(status='queued', error=None, owner=None, lease_until=0)
        return _save_work(con, item)
    return db.atomic(update)
