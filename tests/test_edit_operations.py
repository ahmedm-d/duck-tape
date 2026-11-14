"""Exact snapshot-bound edits; model output is data, never executable instructions."""
import hashlib
from pathlib import Path

import pytest
from backend import db, snapshots
from backend.contracts import EditOperation


def digest(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def source(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path/'store')
    db.init()
    root = tmp_path/'source'
    root.mkdir()
    data = b''.join(f'line {number:04d}\r\n'.encode() for number in range(1, 1001))
    (root/'large.txt').write_bytes(data)
    (root/'asset.bin').write_bytes(bytes(range(256)))
    (root/'run.sh').write_bytes(b'#!/bin/sh\necho okay\n')
    snapshot = snapshots.capture('repo', root, {'file_modes': {'run.sh': 0o755}})
    return snapshot, data, tmp_path


def test_late_anchored_edit_preserves_all_other_bytes(source):
    from backend import edits
    snapshot, original, root = source
    candidate = edits.apply(snapshot, [EditOperation(path='large.txt', expected_hash=digest(original), replacements=[{'old': 'line 0900', 'new': 'changed 0900'}])], root/'candidate')
    assert (Path(candidate.worktree)/'large.txt').read_bytes() == original.replace(b'line 0900', b'changed 0900')
    assert (Path(candidate.worktree)/'asset.bin').read_bytes() == bytes(range(256))
    assert candidate.base_snapshot == snapshot['id']
    assert candidate.patch_hash == digest(candidate.diff.encode())
    assert 'changed 0900' in candidate.diff


@pytest.mark.parametrize('replacement,hash_value', [({'old': 'line ', 'new': 'changed '}, None), ({'old': 'absent', 'new': 'changed'}, None), ({'old': 'line 0900', 'new': 'changed'}, '0'*64)])
def test_rejects_ambiguous_missing_and_wrong_preimages(source, replacement, hash_value):
    from backend import edits
    snapshot, original, root = source
    with pytest.raises(ValueError):
        edits.apply(snapshot, [EditOperation(path='large.txt', expected_hash=hash_value or digest(original), replacements=[replacement])], root/'candidate')
    assert not (root/'candidate').exists()


def test_whole_file_requires_trusted_complete_read(source):
    from backend import edits
    snapshot, original, root = source
    operation = EditOperation(path='large.txt', expected_hash=digest(original), content='', complete_file=True)
    with pytest.raises(ValueError, match='complete'):
        edits.apply(snapshot, [operation], root/'candidate')
    assert edits.read_file(snapshot['id'], 'large.txt', digest(original)) == original
    candidate = edits.apply(snapshot, [operation], root/'candidate')
    assert (Path(candidate.worktree)/'large.txt').read_bytes() == b''


def test_create_delete_rename_preserve_modes_binary_and_applyable_diff(source):
    from backend import edits, sample
    snapshot, original, root = source
    operations = [EditOperation(op='create', path='empty.txt', content=''),
                  EditOperation(op='delete', path='large.txt', expected_hash=digest(original)),
                  EditOperation(op='rename', path='run.sh', destination='renamed.sh', expected_hash=digest(b'#!/bin/sh\necho okay\n')),
                  EditOperation(op='rename', path='asset.bin', destination='renamed.bin', expected_hash=digest(bytes(range(256))))]
    candidate = edits.apply(snapshot, operations, root/'candidate')
    clone = root/'separate'
    snapshots.materialize(snapshot['id'], clone)
    patch = root/'change.patch'
    patch.write_bytes(candidate.diff.encode())
    sample.git(clone, 'init', '-b', 'main')
    sample.git(clone, 'apply', '--check', str(patch))
    sample.git(clone, 'apply', str(patch))
    assert not (clone/'large.txt').exists()
    assert (clone/'empty.txt').read_bytes() == b''
    assert (clone/'renamed.bin').read_bytes() == bytes(range(256))
    assert (clone/'renamed.sh').read_bytes() == b'#!/bin/sh\necho okay\n'
    assert next(item for item in candidate.files if item['path'] == 'renamed.sh')['mode'] == 0o755


@pytest.mark.parametrize('path', ['../escape', '.git/config', '.GIT/objects/a', '.env', 'a/.aws/config', 'C:/escape', 'bad\\name', 'a/../../outside'])
def test_rejects_unsafe_metadata_secret_paths(source, path):
    from backend import edits
    snapshot, _, root = source
    with pytest.raises(ValueError):
        edits.apply(snapshot, [EditOperation(op='create', path=path, content='hello')], root/'candidate')


def test_rejects_secret_content_and_rename_collision(source):
    from backend import edits
    snapshot, _, root = source
    with pytest.raises(ValueError):
        edits.apply(snapshot, [EditOperation(op='create', path='safe.py', content='password="not-a-real-secret-but-long-enough"')], root/'secret')
    with pytest.raises(ValueError):
        edits.apply(snapshot, [EditOperation(op='rename', path='run.sh', expected_hash=digest(b'#!/bin/sh\necho okay\n'), destination='asset.bin')], root/'collision')


def test_cumulative_snapshot_revision_and_explicit_revert(source):
    from backend import edits
    snapshot, original, root = source
    first = edits.apply(snapshot, [EditOperation(path='large.txt', expected_hash=digest(original), replacements=[{'old': 'line 0900', 'new': 'changed 0900'}])], root/'first')
    newer = snapshots.capture('repo', Path(first.worktree), {})
    changed = original.replace(b'line 0900', b'changed 0900')
    second = edits.apply(newer, [EditOperation(path='large.txt', expected_hash=digest(changed), replacements=[{'old': 'line 0800', 'new': 'changed 0800'}])], root/'second')
    assert (Path(second.worktree)/'large.txt').read_bytes() == changed.replace(b'line 0800', b'changed 0800')
    reverted = edits.apply(newer, [EditOperation(path='large.txt', expected_hash=digest(changed), replacements=[{'old': 'changed 0900', 'new': 'line 0900'}])], root/'reverted')
    assert (Path(reverted.worktree)/'large.txt').read_bytes() == original


def test_complete_read_is_hash_bound_and_not_free_host_read(source):
    from backend import edits
    snapshot, original, _ = source
    with pytest.raises(ValueError):
        edits.read_file(snapshot['id'], '../outside', digest(original))
    with pytest.raises(ValueError):
        edits.read_file(snapshot['id'], 'large.txt', '0'*64)


def test_file_to_directory_and_directory_to_file(source):
    from backend import edits
    snapshot, original, root = source
    candidate = edits.apply(snapshot, [EditOperation(op='delete', path='large.txt', expected_hash=digest(original)),
                                       EditOperation(op='create', path='large.txt/child', content='child')], root/'directory')
    newer = snapshots.capture('repo', Path(candidate.worktree), {})
    final = edits.apply(newer, [EditOperation(op='delete', path='large.txt/child', expected_hash=digest(b'child')),
                               EditOperation(op='create', path='large.txt', content='parent')], root/'file')
    assert (Path(final.worktree)/'large.txt').read_bytes() == b'parent'


def test_non_utf8_asset_rename_exports_binary_patch(source):
    from backend import edits, sample
    snapshot, _, root = source
    (root/'source'/'latin.txt').write_bytes(b'caf\xe9\n')
    snapshot = snapshots.capture('repo', root/'source', {})
    candidate = edits.apply(snapshot, [EditOperation(op='rename', path='latin.txt', destination='moved.txt', expected_hash=digest(b'caf\xe9\n'))], root/'candidate')
    assert 'GIT binary patch' in candidate.diff
    separate = root/'separate'
    snapshots.materialize(snapshot['id'], separate)
    patch = root/'binary.patch'
    patch.write_bytes(candidate.diff.encode())
    sample.git(separate, 'apply', '--check', str(patch))
