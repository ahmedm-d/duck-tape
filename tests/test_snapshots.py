import hashlib
import os

import pytest

from backend import db, repository


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path / 'store')
    db.init()
    source = tmp_path / 'source'
    source.mkdir()
    return source


def test_snapshot_preserves_binary_non_utf8_crlf_and_executable(storage):
    from backend import snapshots
    (storage / 'asset.bin').write_bytes(b'\x00\xffasset\r\n')
    (storage / 'script.sh').write_bytes(b'#!/bin/sh\r\necho ok\r\n')
    (storage / 'script.sh').chmod(0o755)
    (storage / 'latin.txt').write_bytes(b'caf\xe9')
    snapshot = snapshots.capture('repo', storage, {'source': 'zip', 'file_modes': {'script.sh': 0o755}})
    destination = storage.parent / 'run'
    snapshots.materialize(snapshot['id'], destination)
    assert (destination / 'asset.bin').read_bytes() == b'\x00\xffasset\r\n'
    assert (destination / 'script.sh').read_bytes() == b'#!/bin/sh\r\necho ok\r\n'
    assert (destination / 'latin.txt').read_bytes() == b'caf\xe9'
    assert next(f for f in snapshot['files'] if f['path'] == 'script.sh')['mode'] == 0o755
    if os.name != 'nt':
        assert (destination / 'script.sh').stat().st_mode & 0o111


def test_snapshot_excludes_secret_paths_and_binary_embedded_credentials(storage):
    from backend import snapshots
    (storage / '.env').write_bytes(b'private')
    (storage / 'config.py').write_bytes(b'password = "abcdefghijklmnop123456"')
    (storage / 'asset.bin').write_bytes(b'\x00ghp_' + b'a' * 36)
    (storage / '.AWS').mkdir()
    (storage / '.AWS' / 'credentials').write_bytes(b'private')
    result = snapshots.capture('repo', storage, {})
    assert all(f['status'] == 'excluded' and 'hash' not in f for f in result['files'])
    assert not list((db.DATA_DIR / 'snapshot-blobs').glob('*/*'))


def test_snapshot_history_is_immutable_and_binary_change_changes_manifest(storage):
    from backend import snapshots
    (storage / 'asset.bin').write_bytes(b'\x00before')
    old = snapshots.capture('repo', storage, {})
    (storage / 'asset.bin').write_bytes(b'\x00after')
    new = snapshots.capture('repo', storage, {})
    assert old['manifest_hash'] != new['manifest_hash']
    snapshots.materialize(old['id'], storage.parent / 'old')
    assert (storage.parent / 'old' / 'asset.bin').read_bytes() == b'\x00before'
    assert snapshots.capture('repo', storage, {})['id'] == new['id']


def test_materialize_refuses_overwrite_and_corrupt_blob(storage):
    from backend import snapshots
    (storage / 'a.py').write_bytes(b'value = 1\n')
    result = snapshots.capture('repo', storage, {})
    with pytest.raises(ValueError, match='empty'):
        snapshots.materialize(result['id'], storage)
    digest = hashlib.sha256(b'value = 1\n').hexdigest()
    (db.DATA_DIR / 'snapshot-blobs' / digest[:2] / digest).write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='integrity'):
        snapshots.materialize(result['id'], storage.parent / 'run')
    assert not (storage.parent / 'run' / 'a.py').exists()


def test_index_inventory_cap_is_deterministic_without_half_indexed_record(storage):
    from backend import snapshots
    for number in range(12001):
        (storage / f'{number:05}.txt').write_bytes(b'x')
    result = repository.index_repository(storage)
    assert len(result['files']) == 12000
    assert result['files'][-1]['path'] == '11999.txt'
    assert any('12000' in gap for gap in result['gaps'])
    snapshot = snapshots.capture('repo', storage, {})
    assert not snapshot['complete']
    assert len(snapshot['files']) == 12000
    with pytest.raises(ValueError, match='incomplete'):
        snapshots.read_files(snapshot['id'])
    (storage / '12000.txt').unlink()
    complete = snapshots.capture('repo', storage, {})
    assert complete['complete'] and complete['id'] != snapshot['id']


def test_snapshot_preserves_files_too_large_for_text_index(storage):
    from backend import snapshots
    content = b'a' * (2 * 1024 * 1024 + 1)
    (storage / 'large.txt').write_bytes(content)
    assert repository.index_repository(storage)['files'][0]['status'] == 'excluded'
    result = snapshots.capture('repo', storage, {})
    snapshots.materialize(result['id'], storage.parent / 'run')
    assert (storage.parent / 'run' / 'large.txt').read_bytes() == content


def test_read_files_validates_manifest_and_returns_bytes(storage):
    from backend import snapshots
    (storage / 'asset.bin').write_bytes(b'\x00asset')
    result = snapshots.capture('repo', storage, {})
    assert snapshots.read_files(result['id']) == {'asset.bin': b'\x00asset'}
    db.mutate('source_snapshots', result['id'], lambda value: {**value, 'manifest_hash': '0' * 64})
    with pytest.raises(ValueError, match='integrity'):
        snapshots.read_files(result['id'])


def test_snapshot_byte_limit_blocks_execution(storage, monkeypatch):
    from backend import snapshots
    monkeypatch.setattr(repository, 'MAX_EXPANDED', 8)
    (storage / 'a.bin').write_bytes(b'123456789')
    result = snapshots.capture('repo', storage, {})
    assert not result['complete'] and result['gaps']
    with pytest.raises(ValueError, match='incomplete'):
        snapshots.read_files(result['id'])
