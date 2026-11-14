import hashlib
import gc
import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from backend import db, workflow


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / 'workspace'
    monkeypatch.setattr(db, 'DATA_DIR', root)
    db.init()
    evidence = root / 'worktrees' / 'revision-one' / 'change.py'
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(b'value = 2\r\n')
    db.put('revisions', {'id': 'revision-one', 'worktree': str(evidence.parent), 'patch_hash': 'patch'})
    db.put('knowledge_versions', {'id': 'decision@1', 'version': 1, 'content': 'Preserved decision'})
    workflow.enqueue('scan', 'repo', {}, 'pending-scan')
    gc.collect()
    return root


def maintenance():
    from scripts import maintenance
    return maintenance


def test_backup_restore_preserves_wal_records_migrations_and_absolute_history(workspace):
    module = maintenance()
    backup = workspace.parent / 'backup'
    # Leave a read connection open so the latest committed record can remain in WAL.
    held = sqlite3.connect(workspace / 'steward.sqlite3')
    held.execute('SELECT * FROM records').fetchall()
    db.put('events', {'id': 'latest-wal', 'message': 'Latest committed evidence'})
    result = module.backup(workspace, backup)
    held.close()
    assert result['files'] and (backup / 'manifest.json').is_file()
    moved = workspace.with_name('workspace-kept')
    workspace.rename(moved)
    module.restore(backup, workspace)
    with sqlite3.connect(workspace / 'steward.sqlite3') as con:
        assert con.execute('SELECT MAX(version) FROM migrations').fetchone()[0] >= 2
        assert con.execute("SELECT data FROM records WHERE id='latest-wal'").fetchone()
        revision = json.loads(con.execute("SELECT data FROM records WHERE id='revision-one'").fetchone()[0])
        assert Path(revision['worktree']) == workspace / 'worktrees' / 'revision-one'
        assert con.execute('SELECT COUNT(*) FROM work_items').fetchone()[0] == 1
    assert (workspace / 'worktrees/revision-one/change.py').read_bytes() == b'value = 2\r\n'
    assert (moved / 'steward.sqlite3').exists()


@pytest.mark.parametrize('record', [
    {'kind': 'services', 'value': {'id': 'worker', 'status': 'running', 'heartbeat': db.now()}},
    {'kind': 'jobs', 'value': {'id': 'running', 'lease_until': time.time() + 300}},
])
def test_active_workspace_refuses_backup_and_cleanup(workspace, record):
    module = maintenance()
    value = {**record['value']}
    value.update(heartbeat=db.now()) if record['kind'] == 'services' else value.update(lease_until=time.time() + 300)
    db.put(record['kind'], value)
    with pytest.raises(ValueError, match='Stop|active|running'):
        module.backup(workspace, workspace.parent / 'backup')
    with pytest.raises(ValueError, match='Stop|active|running'):
        module.cleanup(workspace, apply=True)


def test_restore_rejects_overwrite_relocation_tamper_and_traversal(workspace):
    module = maintenance()
    backup = workspace.parent / 'backup'
    module.backup(workspace, backup)
    with pytest.raises(ValueError, match='exist|overwrite'):
        module.restore(backup, workspace)
    with pytest.raises(ValueError, match='original|location'):
        module.restore(backup, workspace.parent / 'relocated')
    workspace.rename(workspace.with_name('kept'))
    evidence = backup / 'workspace/worktrees/revision-one/change.py'
    evidence.write_bytes(b'tampered')
    with pytest.raises(ValueError, match='hash|integrity|manifest'):
        module.restore(backup, workspace)
    assert not workspace.exists()
    manifest = json.loads((backup / 'manifest.json').read_text())
    manifest['files']['../escape'] = {'size': 0, 'sha256': hashlib.sha256(b'').hexdigest()}
    (backup / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='path|manifest|integrity'):
        module.restore(backup, workspace)
    assert not (workspace.parent / 'escape').exists()


def test_backup_rejects_destination_inside_data_and_links(workspace, monkeypatch):
    module = maintenance()
    with pytest.raises(ValueError, match='outside|inside'):
        module.backup(workspace, workspace / 'backup')
    target = workspace / 'link'
    target.mkdir()
    original = module.is_link
    monkeypatch.setattr(module, 'is_link', lambda path: Path(path) == target or original(path))
    with pytest.raises(ValueError, match='link|junction'):
        module.backup(workspace, workspace.parent / 'backup')


def test_cleanup_is_dry_by_default_and_preserves_all_referenced_and_recent_artifacts(workspace):
    module = maintenance()
    inputs = workspace / 'ingestion-input'
    inputs.mkdir()
    old = time.time() - 20 * 86400
    retained_hash, orphan_hash, fresh_hash = 'a' * 64, 'b' * 64, 'c' * 64
    for digest in (retained_hash, orphan_hash, fresh_hash):
        (inputs / (digest + '.zip')).write_bytes(b'archive')
    for digest in (retained_hash, orphan_hash):
        os.utime(inputs / (digest + '.zip'), (old, old))
    workflow.enqueue('upload', '', {'archive_hash': retained_hash}, 'archived-request')
    ingestion = workspace / 'ingestion'
    kept, orphan = ingestion / ('d' * 32), ingestion / ('e' * 32)
    for root in (kept, orphan):
        root.mkdir(parents=True)
        (root / 'raw').write_bytes(b'raw')
        os.utime(root / 'raw', (old, old))
        os.utime(root, (old, old))
    db.put('runs', {'id': 'history', 'source': str(kept / 'raw')})
    result = module.cleanup(workspace)
    assert len(result['eligible']) == 2 and (inputs / (orphan_hash + '.zip')).exists() and orphan.exists()
    applied = module.cleanup(workspace, apply=True)
    assert applied['deleted_bytes'] > 0
    assert not (inputs / (orphan_hash + '.zip')).exists() and not orphan.exists()
    assert kept.exists() and (inputs / (retained_hash + '.zip')).exists() and (inputs / (fresh_hash + '.zip')).exists()
    assert (workspace / 'worktrees/revision-one/change.py').exists()
    assert db.get('revisions', 'revision-one') and db.get('knowledge_versions', 'decision@1')


@pytest.mark.parametrize('age', [float('nan'), float('inf'), 0])
def test_cleanup_rejects_unbounded_or_invalid_age(workspace, age):
    with pytest.raises(ValueError):
        maintenance().cleanup(workspace, apply=True, older_than_days=age)


def test_cleanup_preserves_unexpected_types_and_recent_empty_descendants(workspace):
    module = maintenance()
    old = time.time() - 20 * 86400
    unexpected = workspace / 'ingestion-input' / ('f' * 64 + '.zip')
    unexpected.mkdir(parents=True)
    os.utime(unexpected, (old, old))
    recent = workspace / 'ingestion' / ('a' * 32)
    (recent / 'new-empty-folder').mkdir(parents=True)
    os.utime(recent, (old, old))
    result = module.cleanup(workspace, apply=True)
    assert not result['eligible']
    assert unexpected.is_dir() and (recent / 'new-empty-folder').is_dir()


def test_unreadable_workspace_inventory_fails_closed(workspace, monkeypatch):
    module = maintenance()
    def unreadable_walk(*args, **kwargs):
        if kwargs.get('onerror'):
            kwargs['onerror'](PermissionError('Unreadable evidence directory'))
        return iter(())
    monkeypatch.setattr(module.os, 'walk', unreadable_walk)
    with pytest.raises(PermissionError, match='Unreadable evidence'):
        module.backup(workspace, workspace.parent / 'backup')
    assert not (workspace.parent / 'backup').exists()


def test_cli_loads_project_environment_and_explicit_data_dir_wins(tmp_path, monkeypatch):
    import run
    module = maintenance()
    monkeypatch.setattr(run, 'ROOT', tmp_path)
    monkeypatch.delenv('STEWARD_DATA_DIR', raising=False)
    configured = tmp_path / 'configured'
    (tmp_path / '.env').write_text('STEWARD_DATA_DIR=' + str(configured), encoding='utf-8')
    calls = []
    monkeypatch.setattr(module, 'backup', lambda data, destination: calls.append((data, destination)) or {'ok': True})
    assert module.main(['backup', 'backup-one']) == 0
    assert Path(calls[-1][0]) == configured
    explicit = tmp_path / 'explicit'
    assert module.main(['--data-dir', str(explicit), 'backup', 'backup-two']) == 0
    assert Path(calls[-1][0]) == explicit
