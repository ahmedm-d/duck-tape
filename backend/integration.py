"""Exact approved patches apply only to a journalled, managed local Git branch."""
import hashlib
import io
import json
import time
import threading
import uuid
import zipfile
from pathlib import Path
from . import db, edits, repository, sample, snapshots, workflow


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _validate(job, revision, repo, expected, con=None):
    approval=job.get('approval') or {}
    if (job['status']!='Ready for review' or job.get('current_revision')!=revision['id']
        or approval.get('revision_id')!=revision['id'] or revision['job_id']!=job['id']):
        raise workflow.Conflict('Approve the exact current revision before applying')
    if revision['base_snapshot']!=repo['snapshot'] or job.get('stale'):
        raise workflow.Conflict('Repository base changed; refresh and prepare again')
    if _sha(revision['diff'].encode())!=revision['patch_hash'] or approval.get('patch_hash')!=revision['patch_hash']:
        raise workflow.Conflict('Approved patch changed; prepare again')
    if expected!=job.get('input_fingerprint') or revision.get('input_fingerprint')!=job.get('input_fingerprint') or approval.get('input_fingerprint')!=revision.get('input_fingerprint'):
        raise workflow.Conflict('Patch inputs changed; prepare again')
    records=[json.loads(row[0]) for row in con.execute("SELECT data FROM records WHERE kind='checks'").fetchall()] if con else db.list_records('checks')
    checks=[c for c in records if c.get('revision_id')==revision['id']]
    if approval.get('checks_hash') and approval['checks_hash']!=workflow.checks_fingerprint(checks):
        raise workflow.Conflict('Verification evidence changed since approval')
    for check in checks:
        if check.get('patch_hash') not in (None,revision['patch_hash']) or check.get('base_snapshot') not in (None,repo['snapshot'],revision.get('source_snapshot_id')):
            raise workflow.Conflict('Verification is bound to different patch inputs')


def _target_matches(root, revision):
    for file in revision['files']:
        path=file['path']
        if repository.unsafe_path(path) or repository.secret_path(path):
            raise workflow.Conflict('Unsafe patch path')
        target=root.joinpath(*path.split('/'))
        expected=file.get('hash')
        if expected is None and file.get('after') is not None:
            expected=_sha(file['after'].encode())
        if expected is None:
            if target.exists(): return False
        elif not target.is_file() or _sha(target.read_bytes())!=expected:
            return False
    return True


def _source_is_current(repo, source_id):
    fresh=snapshots.capture(repo['id'],Path(repo['root']),repo.get('origin'))
    if source_id:
        expected=db.get('source_snapshots',source_id)
        if not expected or fresh['manifest_hash']!=expected['manifest_hash']:
            raise workflow.Conflict('Source checkout changed; refresh and prepare again')
    elif repository.index_repository(Path(repo['root']))['snapshot']!=repo['snapshot']:
        raise workflow.Conflict('Source checkout changed; refresh and prepare again')
    return fresh


def _owned(operation, con=None):
    current=db.get_in(con,'applications',operation['id']) if con else db.get('applications',operation['id'])
    if not current or current.get('owner')!=operation.get('owner') or current.get('lease_until',0)<=time.time():
        raise workflow.Conflict('Managed application ownership changed; recovery must revalidate it')
    return current


def _complete(operation):
    _owned(operation)
    root=Path(operation['root'])
    revision=db.get('revisions',operation['revision_id'])
    marker='Steward-Revision: '+revision['id']
    if marker not in sample.git(root,'log','-1','--format=%B') or not _target_matches(root,revision):
        raise workflow.Conflict('Managed commit does not match the approved revision')
    commit=sample.git(root,'rev-parse','HEAD').strip()
    if sample.git(root,'status','--porcelain').strip():
        raise workflow.Conflict('Managed integration contains uncommitted changes')
    job=db.get('jobs',operation['job_id'])
    _source_is_current(db.get('repositories',job['repo_id']),operation.get('source_snapshot_id'))
    def complete(con):
        current_operation=_owned(operation,con)
        job=db.get_in(con,'jobs',operation['job_id'])
        current_revision=db.get_in(con,'revisions',revision['id'])
        repo=db.get_in(con,'repositories',job['repo_id'])
        _validate(job,current_revision,repo,operation.get('input_fingerprint'),con)
        if operation.get('approval')!=job.get('approval'):
            raise workflow.Conflict('Approval changed during managed application')
        result=db.put_in(con,'jobs',{**job,'status':'Completed','applied_commit':commit,
            'integration_branch':'steward/integration','integration_root':str(root),'applied_at':db.now()})
        db.put_in(con,'applications',{**current_operation,'status':'complete','commit':commit,'lease_until':0})
        db.put_in(con,'events',{'message':'Approved revision applied to managed local branch','job_id':job['id'],'repo_id':job['repo_id'],'author':'Duck Tape'})
        return result
    return db.atomic(complete)


def reconcile(operation_id):
    operation=db.get('applications',operation_id)
    if not operation: raise ValueError('Application operation not found')
    return apply(operation['job_id'],operation['revision_id'],operation.get('input_fingerprint'))


def recover_pending():
    """Resume expired applications; retain actionable conflicts for explicit review."""
    for operation in db.list_records('applications'):
        if operation.get('status') not in {'prepared','applying'} or operation.get('lease_until',0)>time.time():
            continue
        try:
            reconcile(operation['id'])
        except Exception as exc:
            def record(con):
                current=db.get_in(con,'applications',operation['id'])
                if current and current.get('lease_until',0)<=time.time() and current.get('status')!='complete':
                    db.put_in(con,'applications',{**current,'status':'needs attention','error':str(exc)[:2000]})
                    db.put_in(con,'events',{'message':'Managed application needs review: '+str(exc)[:500],
                        'job_id':operation['job_id'],'author':'Duck Tape'})
            db.atomic(record)


def apply(job_id, revision_id, expected_input_fingerprint=None):
    job=db.get('jobs',job_id)
    revision=db.get('revisions',revision_id)
    if not job or not revision: raise ValueError('Job or revision not found')
    if job['status']=='Completed' and job.get('current_revision')==revision_id:
        return job
    repo=db.get('repositories',job['repo_id'])
    _validate(job,revision,repo,expected_input_fingerprint)
    source_id=revision.get('source_snapshot_id') or repo.get('source_snapshot_id')
    fresh=_source_is_current(repo,source_id)
    source_id=source_id or fresh['id']
    root=db.DATA_DIR/'integrations'/repo['id']/source_id[:16]
    token=uuid.uuid4().hex
    def claim(con):
        current=db.get_in(con,'jobs',job_id)
        _validate(current,db.get_in(con,'revisions',revision_id),db.get_in(con,'repositories',repo['id']),expected_input_fingerprint,con)
        for row in con.execute("SELECT data FROM records WHERE kind='applications'").fetchall():
            other=json.loads(row[0])
            if other.get('root')==str(root) and other.get('lease_until',0)>time.time():
                raise workflow.Conflict('Managed application is already running')
        old=db.get_in(con,'applications',revision_id) or {}
        return db.put_in(con,'applications',{**old,'id':revision_id,'job_id':job_id,'revision_id':revision_id,
            'source_snapshot_id':source_id,'input_fingerprint':expected_input_fingerprint,'root':str(root),
            'status':old.get('status','prepared'),'owner':token,'lease_until':time.time()+180,'approval':current['approval']})
    operation=db.atomic(claim)
    stopped=threading.Event()
    def update_operation(**changes):
        return db.atomic(lambda con:db.put_in(con,'applications',{**_owned(operation,con),**changes}))
    def heartbeat():
        while not stopped.wait(20):
            try: update_operation(lease_until=time.time()+180)
            except workflow.Conflict: return
    pulse=threading.Thread(target=heartbeat,name='managed-application',daemon=True)
    pulse.start()
    try:
        _owned(operation)
        if not (root/'.git').exists():
            staging=root.parent/('initializing-'+uuid.uuid4().hex)
            snapshots.materialize(source_id,staging)
            sample.git(staging,'init','-b','steward/integration')
            source=db.get('source_snapshots',source_id)
            modes={f['path']:f['mode'] for f in source['files'] if f['status']=='included'}
            edits._stage(staging,snapshots.read_files(source_id),modes)
            _owned(operation)
            sample.git(staging,'-c','user.name=Duck Tape','-c','user.email=local@duck-tape','commit','--allow-empty','-m','Managed immutable baseline')
            staging.rename(root)
        head=sample.git(root,'rev-parse','HEAD').strip()
        marker='Steward-Revision: '+revision_id
        if marker in sample.git(root,'log','-1','--format=%B'):
            return _complete(operation)
        if not operation.get('base_commit'):
            baseline=snapshots.read_files(source_id)
            for file in revision['files']:
                target=root/file['path']
                if (target.read_bytes() if target.is_file() else None)!=baseline.get(file['path']):
                    raise workflow.Conflict('Overlapping change in '+file['path']+'; prepare a new patch')
            # An advanced managed base needs renewed investigation/checks.
            if 'Steward-Revision:' in sample.git(root,'log','-1','--format=%B'):
                raise workflow.Conflict('Managed integration base advanced; prepare a new patch against that branch')
            operation=update_operation(base_commit=head)
        if head!=operation['base_commit']:
            raise workflow.Conflict('Managed integration base changed; refusing to overwrite')
        patch=db.DATA_DIR/'integrations'/(revision_id+'.patch')
        patch.write_bytes(revision['diff'].encode())
        dirty=bool(sample.git(root,'status','--porcelain').strip())
        if dirty:
            if operation['status']!='applying' or not _target_matches(root,revision):
                raise workflow.Conflict('Managed integration has unexpected changes; inspect before retrying')
            sample.git(root,'apply','--reverse','--check',str(patch.resolve()))
        else:
            sample.git(root,'apply','--check',str(patch.resolve()))
            operation=update_operation(status='applying')
            sample.git(root,'apply',str(patch.resolve()))
        if not _target_matches(root,revision):
            raise workflow.Conflict('Applied bytes do not match the approved patch')
        source=db.get('source_snapshots',source_id)
        modes={f['path']:f['mode'] for f in source['files'] if f['status']=='included'}
        for file in revision['files']:
            if not (root/file['path']).exists():
                sample.git(root,'update-index','--force-remove','--',file['path'])
                modes.pop(file['path'],None)
            else:
                modes[file['path']]=file.get('mode') or modes.get(file['path'],0o644)
        _owned(operation)
        edits._stage(root,{path:(root/path).read_bytes() for path in modes},modes)
        changed=set(sample.git(root,'diff','--cached','--name-only','-z','--no-renames').strip('\0').split('\0'))
        if changed!={f['path'] for f in revision['files']}:
            raise workflow.Conflict('Managed changes differ from the approved file set')
        _validate(db.get('jobs',job_id),db.get('revisions',revision_id),db.get('repositories',repo['id']),expected_input_fingerprint)
        _source_is_current(db.get('repositories',repo['id']),source_id)
        _owned(operation)
        sample.git(root,'-c','user.name=Duck Tape','-c','user.email=local@duck-tape','commit','-m','Duck Tape revision '+revision_id,'-m',marker)
        return _complete(operation)
    except BaseException:
        def release(con):
            current=db.get_in(con,'applications',revision_id)
            if current and current.get('owner')==token:
                db.put_in(con,'applications',{**current,'lease_until':0})
        db.atomic(release)
        raise
    finally:
        stopped.set()
        pulse.join(timeout=1)


def export_files(revision_id):
    revision=db.get('revisions',revision_id)
    if not revision: raise ValueError('Revision not found')
    buffer=io.BytesIO()
    changes=[]
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
        for file in revision['files']:
            path=file['path']
            if repository.unsafe_path(path): raise ValueError('Unsafe export path')
            deleted=file.get('hash') is None and file.get('after') is None
            changes.append({'path':path,'operation':'delete' if deleted else 'update' if file.get('before_hash') or file.get('before') else 'create','before_hash':file.get('before_hash'),'hash':file.get('hash')})
            if not deleted:
                data=file['after'].encode() if file.get('after') is not None else (Path(revision['worktree'])/path).read_bytes()
                if file.get('hash') and _sha(data)!=file['hash']: raise workflow.Conflict('Candidate export bytes changed')
                archive.writestr('files/'+path,data)
        archive.writestr('duck-tape-changes.json',json.dumps({'revision_id':revision_id,'base_snapshot':revision['base_snapshot'],'patch_hash':revision['patch_hash'],'changes':changes},indent=2))
        archive.writestr('duck-tape.patch',revision['diff'])
    return buffer.getvalue()
