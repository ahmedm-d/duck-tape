import hashlib
from pathlib import Path
import pytest
from backend import db, edits, workflow


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    from backend.main import register_repo
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path / 'workspace')
    db.init()
    root = tmp_path / 'imported'
    root.mkdir()
    (root / 'old.py').write_bytes(b'value = 1\n')
    (root / 'keep.txt').write_bytes(b'unchanged\r\n')
    repo = register_repo(root, 'Imported', {'source': 'zip'})
    candidate = edits.apply(repo['source_snapshot_id'], [{'op':'rename','path':'old.py','expected_hash':hashlib.sha256(b'value = 1\n').hexdigest(),'destination':'new.py'}], tmp_path / 'candidate').model_dump()
    job = db.put('jobs', {'id':'job','repo_id':repo['id'],'status':'Ready for review','base_snapshot':repo['snapshot'],'current_revision':'revision','input_fingerprint':'inputs'})
    revision = db.put('revisions', {**candidate,'id':'revision','job_id':job['id'],'base_snapshot':repo['snapshot'],'source_snapshot_id':repo['source_snapshot_id'],'input_fingerprint':'inputs'})
    workflow.transition(job['id'], 'accept', {'revision_id':revision['id']})
    return repo, revision


def test_apply_imported_snapshot_and_export_deletion_manifest(prepared):
    from backend import integration, sample
    import io, zipfile, json
    repo, revision = prepared
    result = integration.apply('job', revision['id'], 'inputs')
    assert result['status'] == 'Completed' and result['applied_commit']
    assert (Path(repo['root']) / 'old.py').exists() and not (Path(repo['root']) / 'new.py').exists()
    target = Path(result['integration_root'])
    assert sample.git(target, 'log', '-1', '--format=%an').strip() == 'Duck Tape'
    assert sample.git(target, 'log', '-1', '--format=%s').strip() == 'Duck Tape revision '+revision['id']
    assert 'Steward-Revision: '+revision['id'] in sample.git(target, 'log', '-1', '--format=%B')
    assert not (target / 'old.py').exists() and (target / 'new.py').read_bytes() == b'value = 1\n'
    assert (target / 'keep.txt').read_bytes() == b'unchanged\r\n'
    assert integration.apply('job', revision['id'], 'inputs')['applied_commit'] == result['applied_commit']
    with zipfile.ZipFile(io.BytesIO(integration.export_files(revision['id']))) as archive:
        manifest = json.loads(archive.read('duck-tape-changes.json'))
        assert any(item['path'] == 'old.py' and item['operation'] == 'delete' for item in manifest['changes'])
        assert archive.read('duck-tape.patch').decode() == revision['diff']


def test_stale_source_and_approval_are_rejected(prepared):
    from backend import integration
    repo, revision = prepared
    with pytest.raises(workflow.Conflict):
        integration.apply('job', revision['id'], 'changed-inputs')
    (Path(repo['root']) / 'old.py').write_text('changed\n')
    with pytest.raises(workflow.Conflict, match='changed'):
        integration.apply('job', revision['id'], 'inputs')


@pytest.mark.parametrize('legacy_subject', [False, True])
def test_commit_before_database_completion_is_reconciled(prepared, monkeypatch, legacy_subject):
    from backend import integration, sample
    repo, revision = prepared
    original = integration._complete
    monkeypatch.setattr(integration, '_complete', lambda operation: (_ for _ in ()).throw(RuntimeError('crashed after commit')))
    with pytest.raises(RuntimeError):
        integration.apply('job', revision['id'], 'inputs')
    assert db.get('jobs', 'job')['status'] == 'Ready for review'
    if legacy_subject:
        root = Path(db.get('applications', revision['id'])['root'])
        sample.git(root, '-c', 'user.name=Steward', '-c', 'user.email=local@steward',
                   'commit', '--amend', '-m', 'Steward-Revision: '+revision['id'])
    monkeypatch.setattr(integration, '_complete', original)
    integration.recover_pending()
    recovered = db.get('jobs', 'job')
    assert recovered['status'] == 'Completed'


def test_changed_checks_at_commit_cannot_complete(prepared, monkeypatch):
    from backend import integration, sample
    repo, revision = prepared
    check = db.put('checks', {'id':'check','revision_id':revision['id'],'status':'passed'})
    workflow.transition('job', 'accept', {'revision_id':revision['id']})
    original = sample.git
    def change_at_commit(root, *args):
        result = original(root, *args)
        if 'commit' in args and any('Steward-Revision:' in arg for arg in args):
            db.put('checks', {**check, 'status':'failed'})
        return result
    monkeypatch.setattr(sample, 'git', change_at_commit)
    with pytest.raises(workflow.Conflict, match='evidence changed'):
        integration.apply('job', revision['id'], 'inputs')
    assert db.get('jobs', 'job')['status'] != 'Completed'


def test_export_metadata_names_cannot_replace_source_files(prepared):
    import io, zipfile
    from backend import integration
    _, revision = prepared
    db.put('revisions', {**revision, 'files':[
        {'path':'duck-tape.patch','before':None,'after':'user patch'},
        {'path':'duck-tape-changes.json','before':None,'after':'user manifest'}]})
    with zipfile.ZipFile(io.BytesIO(integration.export_files(revision['id']))) as archive:
        assert len(archive.namelist()) == len(set(archive.namelist()))
        assert archive.read('files/duck-tape.patch') == b'user patch'
        assert archive.read('files/duck-tape-changes.json') == b'user manifest'
