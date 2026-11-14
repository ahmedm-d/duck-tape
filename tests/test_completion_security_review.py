"""Independent completion review: controlled publication and application races."""
import io
import json
import zipfile
from pathlib import Path

import pytest

from backend import db, integration, knowledge, main, operations, repository, sample, workflow
from test_managed_integration import prepared


def archive():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as output:
        output.writestr('main.py', 'value = 1\n')
    return stream.getvalue()


@pytest.fixture
def repository_record(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path / 'store')
    db.init()
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'main.py').write_bytes(b'value = 1\n')
    (root / 'asset.bin').write_bytes(b'\x00first')
    return main.register_repo(root, 'Race review', {'source': 'zip'})


def test_cancel_at_import_publication_prevents_visible_repository(repository_record, monkeypatch):
    operation = operations.submit('upload', '', {'name': 'Cancelled at publication'}, archive=archive())
    original = main.store_snapshot

    def cancel_before_write(record):
        result=original(record)
        if record['id'] == operation['id']:
            operations.cancel(operation['id'])
        return result

    monkeypatch.setattr(main, 'store_snapshot', cancel_before_write)
    result = operations.advance(operation['id'])
    assert result['status'] == 'cancelled'
    assert db.get('repositories', operation['id']) is None


def test_refresh_crash_and_retry_still_invalidates_old_approval(repository_record, monkeypatch):
    repo = repository_record
    policy = knowledge.create({'repo_id': repo['id'], 'title': 'Rule', 'content': 'Use current source',
                               'status': 'team-approved', 'scope': 'main.py', 'evidence': ['main.py']})
    db.put('jobs', {'id': 'waiting', 'repo_id': repo['id'], 'status': 'Ready for review',
                    'approval': {'revision_id': 'old'}, 'base_snapshot': repo['snapshot']})
    (Path(repo['root']) / 'main.py').write_bytes(b'value = 2\n')
    with monkeypatch.context() as crash:
        def fail_after_repository_write(*args):
            raise RuntimeError('crash after repository commit')
        crash.setattr(db, 'index_search', fail_after_repository_write)
        with pytest.raises(RuntimeError, match='crash after'):
            main.refresh_repo(repo['id'])
    assert db.get('repositories', repo['id'])['snapshot'] == repo['snapshot']
    main.refresh_repo(repo['id'])
    assert db.get('jobs', 'waiting')['approval'] is None
    assert db.get('knowledge', policy['id'])['status'] == 'stale'


def test_binary_only_refresh_invalidates_pending_revision(repository_record):
    repo = repository_record
    db.put('jobs', {'id': 'waiting', 'repo_id': repo['id'], 'status': 'Ready for review',
                    'approval': {'revision_id': 'old'}, 'base_snapshot': repo['snapshot']})
    (Path(repo['root']) / 'asset.bin').write_bytes(b'\x00second')
    refreshed = main.refresh_repo(repo['id'])
    assert refreshed['source_snapshot_id'] != repo['source_snapshot_id']
    assert db.get('jobs', 'waiting')['approval'] is None
    assert db.get('jobs', 'waiting')['status'] == 'Failed'


def test_source_change_during_managed_commit_cannot_complete(prepared, monkeypatch):
    repo, revision = prepared
    original = sample.git

    def change_at_commit(root, *args):
        result = original(root, *args)
        if 'commit' in args and any('Steward-Revision:' in arg for arg in args):
            (Path(repo['root']) / 'old.py').write_bytes(b'value = 999\n')
        return result

    monkeypatch.setattr(sample, 'git', change_at_commit)
    with pytest.raises(workflow.Conflict, match='Source|source|changed'):
        integration.apply('job', revision['id'], 'inputs')
    assert db.get('jobs', 'job')['status'] != 'Completed'


def test_git_operation_propagates_cancellation_to_transport(repository_record, monkeypatch):
    operation = operations.submit('import', '', {'name': 'Cancel transport', 'url': 'https://public.example/repo'})
    seen = []

    def transport(url, destination, *, cancel=None):
        seen.append(cancel)
        operations.cancel(operation['id'])
        if cancel:
            cancel.raise_if_cancelled()
        destination.mkdir(parents=True)
        (destination / 'main.py').write_bytes(b'value = 1\n')
        return {'source': url}

    monkeypatch.setattr(repository, 'import_git', transport)
    operations.advance(operation['id'])
    assert len(seen) == 1 and seen[0] is not None


@pytest.mark.parametrize('field', ['message', 'author'])
def test_github_history_does_not_publish_credential_material(repository_record, tmp_path, monkeypatch, field):
    from urllib.parse import urlparse
    secret = 'ghp_' + 'Q' * 36
    monkeypatch.setattr(repository, 'validate_public_url', lambda url: (urlparse(url), ['1.1.1.1']))

    def fetch(url, max_bytes=repository.MAX_ARCHIVE):
        if '/commits?' in url:
            return json.dumps([{'sha': 'a' * 40, 'commit': {'message': 'accidental token ' + secret if field == 'message' else 'message',
                                                          'author': {'name': secret if field == 'author' else 'A'}}, 'parents': []}]).encode()
        if '/commits/' in url:
            return json.dumps({'sha': 'a' * 40}).encode()
        if '/zipball/' in url:
            return archive()
        return b'{"default_branch":"main"}'

    monkeypatch.setattr(repository, 'fetch_public', fetch)
    origin = repository.import_git('https://github.com/team/project', tmp_path / 'github-import')
    assert secret not in json.dumps(origin)


def test_git_wrapper_checks_cancellation_between_github_fetches(tmp_path, monkeypatch):
    from backend.contracts import CancellationToken, CancelledError
    from urllib.parse import urlparse
    token = CancellationToken()
    requests = []
    monkeypatch.setattr(repository, 'validate_public_url', lambda url: (urlparse(url), ['1.1.1.1']))

    def fetch(url, max_bytes=repository.MAX_ARCHIVE):
        requests.append(url)
        token.cancel()
        return b'{"default_branch":"main"}'

    monkeypatch.setattr(repository, 'fetch_public', fetch)
    with pytest.raises(CancelledError):
        repository.import_git('https://github.com/team/project', tmp_path / 'github-import', cancel=token)
    assert len(requests) == 1


def test_git_wrapper_forwards_token_to_generic_transport(tmp_path, monkeypatch):
    from backend import git_transport
    from backend.contracts import CancellationToken
    from urllib.parse import urlparse
    token = CancellationToken()
    monkeypatch.setattr(repository, 'validate_public_url', lambda url: (urlparse(url), ['1.1.1.1']))

    def transport(url, destination, *, cancel=None):
        assert cancel is token
        return {'source': url}

    monkeypatch.setattr(git_transport, 'import_https', transport)
    repository.import_git('https://public.example/repo', tmp_path / 'source', cancel=token)


def test_refresh_forwards_cancellation_to_transport(repository_record, monkeypatch):
    from backend import snapshots
    from backend.contracts import CancellationToken, CancelledError
    token = CancellationToken()
    db.mutate('repositories', repository_record['id'], lambda repo: {**repo, 'origin': {'source': 'https://public.example/repo'}})

    def transport(url, destination, *, cancel=None):
        assert cancel is token
        token.cancel()
        token.raise_if_cancelled()

    monkeypatch.setattr(repository, 'import_git', transport)
    with pytest.raises(CancelledError):
        snapshots.refresh(repository_record['id'], token)


def test_imported_executable_mode_survives_source_recheck(repository_record, tmp_path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as output:
        entry = zipfile.ZipInfo('run.sh')
        entry.external_attr = 0o100755 << 16
        output.writestr(entry, '#!/bin/sh\necho ready\n')
    root = tmp_path / 'executable-source'
    origin = repository.import_zip(stream.getvalue(), root)
    repo = main.register_repo(root, 'Executable import', origin)
    source = db.get('source_snapshots', repo['source_snapshot_id'])
    assert source['files'][0]['mode'] == 0o755
    assert integration._source_is_current(repo, source['id'])['manifest_hash'] == source['manifest_hash']
    import os
    if os.name != 'nt':
        (root / 'run.sh').chmod(0o644)
        with pytest.raises(workflow.Conflict, match='Source checkout changed'):
            integration._source_is_current(repo, source['id'])


def test_generic_git_author_does_not_publish_credential_material(repository_record, tmp_path, monkeypatch):
    import shutil
    from urllib.parse import urlparse
    from backend import git_transport
    secret = 'ghp_' + 'Q' * 36
    source = tmp_path / 'git-fixture'
    source.mkdir()
    (source / 'main.py').write_bytes(b'value = 1\n')
    sample.git(source, 'init', '-b', 'main')
    sample.git(source, 'add', '.')
    sample.git(source, '-c', 'user.name=' + secret, '-c', 'user.email=fixture@localhost', 'commit', '-m', 'Fixture')
    monkeypatch.setattr(repository, 'validate_public_url', lambda url: (urlparse(url), ['1.1.1.1']))
    monkeypatch.setattr(git_transport, '_clone', lambda session, url, target: shutil.copytree(source / '.git', target))
    origin = git_transport.import_https('https://public.example/repo.git', tmp_path / 'git-import')
    assert secret not in json.dumps(origin)


def test_displaced_application_owner_cannot_publish_completion(prepared, monkeypatch):
    import time
    _, revision = prepared
    original = integration._complete

    def replace_owner_before_completion(operation):
        db.mutate('applications', operation['id'], lambda current: {
            **current, 'owner': 'replacement-worker', 'lease_until': time.time() + 180})
        return original(operation)

    monkeypatch.setattr(integration, '_complete', replace_owner_before_completion)
    with pytest.raises(workflow.Conflict, match='owner|lease|ownership'):
        integration.apply('job', revision['id'], 'inputs')
    assert db.get('jobs', 'job')['status'] != 'Completed'
    assert db.get('applications', revision['id'])['owner'] == 'replacement-worker'


def test_import_publication_completes_operation_before_return(repository_record, monkeypatch):
    operation = operations.submit('upload', '', {'name': 'Atomic completion'}, archive=archive())
    original = main.register_repo

    def cancel_after_publication(*args, **kwargs):
        result = original(*args, **kwargs)
        assert workflow.get_work(operation['id'])['status'] == 'complete'
        with pytest.raises(workflow.Conflict, match='already finished'):
            operations.cancel(operation['id'])
        return result

    monkeypatch.setattr(main, 'register_repo', cancel_after_publication)
    result = operations.advance(operation['id'])
    assert result['status'] == 'complete'
    assert db.get('repositories', operation['id']) is not None


def test_expired_import_owner_cannot_publish_and_retry_recovers(repository_record, monkeypatch):
    operation = operations.submit('upload', '', {'name': 'Expired import'}, archive=archive())
    original = main.store_snapshot

    def expire_before_publication(record):
        result = original(record)
        if record['id'] == operation['id']:
            current = workflow.get_work(operation['id'])
            db.atomic(lambda con: workflow._save_work(con, {**current, 'lease_until': 0}))
        return result

    with monkeypatch.context() as expired:
        expired.setattr(main, 'store_snapshot', expire_before_publication)
        operations.advance(operation['id'])
    assert db.get('repositories', operation['id']) is None
    assert operations.advance(operation['id'])['status'] == 'complete'
    assert db.get('repositories', operation['id']) is not None
