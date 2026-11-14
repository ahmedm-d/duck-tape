import io
import json
import zipfile

import pytest

from backend import db, repository


def archive(entries, executable=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as output:
        for path, content in entries.items():
            item = zipfile.ZipInfo(path)
            item.external_attr = (0o100755 if path == executable else 0o100644) << 16
            output.writestr(item, content)
    return stream.getvalue()


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path / 'store')
    db.init()
    return tmp_path


def test_zip_and_git_provider_wrappers_produce_identical_manifest(storage, monkeypatch):
    from backend import snapshots
    uploads = archive({'project/main.py': b'x = 1\r\n', 'project/run.sh': b'echo ok'}, 'project/run.sh')
    origin = repository.import_zip(uploads, storage / 'zip')
    first = snapshots.capture('zip', storage / 'zip', origin)
    monkeypatch.setattr(repository, 'validate_public_url', lambda url: (__import__('urllib.parse', fromlist=['urlparse']).urlparse(url), ['1.1.1.1']))
    def fetch(url, max_bytes=repository.MAX_ARCHIVE):
        if '/commits?' in url:
            return json.dumps([{'sha': 'a' * 40, 'commit': {'message': 'untrusted message', 'author': {'name': 'person', 'date': '2026-01-01'}}, 'parents': []}]).encode()
        if '/commits/' in url:
            return json.dumps({'sha': 'a' * 40, 'commit': {'message': 'test'}}).encode()
        if '/zipball/' in url:
            return uploads
        return b'{"default_branch":"main"}'
    monkeypatch.setattr(repository, 'fetch_public', fetch)
    git_origin = repository.import_git('https://github.com/team/project.git', storage / 'git')
    second = snapshots.capture('git', storage / 'git', git_origin)
    assert git_origin['history_entries'][0]['commit'] == 'a' * 40
    assert git_origin['history_entries'][0]['message'] == 'untrusted message'
    assert first['manifest_hash'] == second['manifest_hash']
    assert (storage / 'zip' / 'main.py').read_bytes() == b'x = 1\r\n'


def test_zip_refresh_requires_replacement_and_preserves_old_snapshot(storage):
    from backend import snapshots
    root = storage / 'source'
    origin = repository.import_zip(archive({'a.py': b'old', 'deleted.py': b'delete'}), root)
    first = snapshots.capture('repo', root, origin)
    db.put('repositories', {'id': 'repo', 'root': str(root), 'origin': origin, 'source_snapshot_id': first['id']})
    with pytest.raises(ValueError, match='replacement ZIP'):
        snapshots.refresh('repo', None)
    second = snapshots.refresh_zip('repo', archive({'a.py': b'new'}), None)
    snapshots.materialize(second['id'], storage / 'new')
    snapshots.materialize(first['id'], storage / 'old')
    assert (storage / 'new' / 'a.py').read_bytes() == b'new'
    assert not (storage / 'new' / 'deleted.py').exists()
    assert (storage / 'old' / 'deleted.py').read_bytes() == b'delete'
    assert (root / 'a.py').read_bytes() == b'old'


def test_git_refresh_fetches_changed_and_deleted_upstream_files(storage, monkeypatch):
    from backend import snapshots
    root = storage / 'source'
    root.mkdir()
    (root / 'old.py').write_bytes(b'old')
    origin = {'source': 'https://github.com/team/project', 'branch': 'main', 'commit': 'a' * 40}
    first = snapshots.capture('repo', root, origin)
    db.put('repositories', {'id': 'repo', 'root': str(root), 'origin': origin})
    def upstream(url, destination, *, cancel=None):
        assert url == 'https://github.com/team/project'
        destination.mkdir(parents=True)
        (destination / 'new.py').write_bytes(b'new')
        return {**origin, 'commit': 'b' * 40}
    monkeypatch.setattr(repository, 'import_git', upstream)
    second = snapshots.refresh('repo', None)
    assert second['origin']['commit'] == 'b' * 40
    snapshots.materialize(second['id'], storage / 'new')
    assert (storage / 'new' / 'new.py').read_bytes() == b'new'
    assert not (storage / 'new' / 'old.py').exists()
    assert (root / 'old.py').read_bytes() == b'old'
    snapshots.materialize(first['id'], storage / 'historical')
    assert (storage / 'historical' / 'old.py').read_bytes() == b'old'


def test_refresh_cancellation_never_publishes_snapshot(storage):
    from backend import snapshots
    class Cancel:
        def raise_if_cancelled(self):
            raise RuntimeError('cancelled')
    db.put('repositories', {'id': 'repo', 'origin': {'source': 'zip'}})
    with pytest.raises(RuntimeError, match='cancelled'):
        snapshots.refresh_zip('repo', archive({'a.py': b'value'}), Cancel())
    assert not db.list_records('source_snapshots')


def test_network_boundary_has_bounded_bytes_and_provenance(monkeypatch):
    from backend import network
    monkeypatch.setattr(repository, 'fetch_public', lambda url, max_bytes: b'payload')
    result = network.fetch_public({'url': 'https://example.com/archive'}, {'max_bytes': 10})
    assert result['data'] == b'payload'
    assert result['provenance']['url'] == 'https://example.com/archive'
    with pytest.raises(ValueError):
        network.fetch_public({'url': 'https://example.com/archive'}, {'max_bytes': 0})


@pytest.mark.parametrize('wrapper', ['.git', '.AWS', '.ssh', '.env.local'])
def test_zip_root_normalization_preserves_exclusion_boundaries(storage, wrapper):
    from backend import snapshots
    root = storage / 'source'
    origin = repository.import_zip(archive({wrapper + '/notes.txt': b'private metadata'}), root)
    result = snapshots.capture('repo', root, origin)
    assert not any(entry['status'] == 'included' for entry in result['files'])


@pytest.mark.parametrize('origin', ['ZIP upload', 'zip', ''])
def test_legacy_zip_origin_requires_replacement(storage, origin):
    from backend import snapshots
    db.put('repositories', {'id': 'repo', 'origin': origin})
    with pytest.raises(ValueError, match='replacement ZIP|no refreshable'):
        snapshots.refresh('repo')


def test_legacy_https_origin_fetches_upstream(storage, monkeypatch):
    from backend import snapshots
    db.put('repositories', {'id': 'repo', 'origin': 'https://github.com/team/project'})
    def upstream(url, destination, *, cancel=None):
        assert url == 'https://github.com/team/project'
        destination.mkdir(parents=True)
        (destination / 'main.py').write_bytes(b'value = 1')
        return {'source': url}
    monkeypatch.setattr(repository, 'import_git', upstream)
    assert snapshots.refresh('repo')['files'][0]['path'] == 'main.py'
