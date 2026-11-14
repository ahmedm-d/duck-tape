"""Automatic Atlas sync of current snapshots, against fakes: no network, no real cluster, no pymongo."""
import threading
import multiprocessing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import atlas_autosync, atlas_graph, data_platforms, db, knowledge, main

URI_SECRET = 'mongodb+srv://user:never-show-this@cluster.mongodb.net/'


class Calls:
    def __init__(self):
        self.mirror, self.vectors = [], []
        self.mirror_error = None
        self.vector_error = None


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path / 'store')
    db.init()
    monkeypatch.setattr(atlas_autosync, 'RUN_INLINE', True)
    monkeypatch.delenv('STEWARD_ATLAS_GRAPH_MIRROR', raising=False)
    calls = Calls()

    def mirror(repo_id, snapshot_id):
        calls.mirror.append((repo_id, snapshot_id, db.get('repositories', repo_id) is not None))
        if calls.mirror_error:
            raise calls.mirror_error
        return {'nodes': 5, 'edges': 4, 'knowledge': 1, 'snapshot_id': snapshot_id}

    def vectors(repo_id, snapshot_id):
        calls.vectors.append((repo_id, snapshot_id))
        if calls.vector_error:
            raise calls.vector_error
        return {'uploaded': 7}

    monkeypatch.setattr(atlas_graph, 'mirror', mirror)
    monkeypatch.setattr(data_platforms, 'atlas_sync', vectors)
    return calls


@pytest.fixture
def required(env, monkeypatch):
    monkeypatch.setenv('STEWARD_ATLAS_REQUIRED', '1')
    return env


def make_root(tmp_path, text='value = 1\n'):
    root = tmp_path / 'source'
    root.mkdir(exist_ok=True)
    (root / 'main.py').write_text(text, encoding='utf-8')
    return root


def current(repo):
    return repo.get('source_snapshot_id') or repo['snapshot']


def test_import_triggers_sync_after_commit_and_records_status(required, tmp_path):
    repo = main.register_repo(make_root(tmp_path), 'Imported', {'source': 'zip'})
    assert required.mirror == [(repo['id'], current(repo), True)]  # the repository row already existed
    assert required.vectors == [(repo['id'], current(repo))]
    state = db.get('repositories', repo['id'])['atlas_sync']
    assert state['status'] == 'synced' and state['snapshot_id'] == current(repo) and state['at']
    assert (state['nodes'], state['edges'], state['knowledge'], state['chunks'], state['errors']) == (5, 4, 1, 7, [])
    messages = [e['message'] for e in db.list_records('events') if e.get('repo_id') == repo['id']]
    assert any(m.startswith('Atlas sync synced') for m in messages)
    listed = TestClient(main.app).get('/api/repositories').json()
    assert listed[0]['atlas_sync']['status'] == 'synced'
    assert TestClient(main.app).get(f'/api/repositories/{repo["id"]}').json()['atlas_sync']['chunks'] == 7


def test_refresh_triggers_sync_with_new_snapshot_and_skips_unchanged(required, tmp_path):
    root = make_root(tmp_path)
    repo = main.register_repo(root, 'Imported', {'source': 'zip'})
    main.refresh_repo(repo['id'])  # unchanged and already synced: no second upload
    assert len(required.mirror) == 1
    (root / 'main.py').write_text('value = 2\n', encoding='utf-8')
    refreshed = main.refresh_repo(repo['id'])
    assert current(refreshed) != current(repo)
    assert required.mirror[-1][:2] == (repo['id'], current(refreshed)) and len(required.mirror) == 2
    assert required.vectors[-1] == (repo['id'], current(refreshed))
    assert db.get('repositories', repo['id'])['atlas_sync']['snapshot_id'] == current(refreshed)


def test_partial_and_failed_outcomes_are_recorded_without_secrets(required, tmp_path):
    required.mirror_error = data_platforms.DataPlatformError('Atlas request failed; check connectivity')
    required.vector_error = RuntimeError('boom ' + URI_SECRET)
    repo = main.register_repo(make_root(tmp_path), 'Imported', {'source': 'zip'})
    state = db.get('repositories', repo['id'])['atlas_sync']
    assert state['status'] == 'failed' and state['nodes'] == 0 and state['chunks'] == 0
    assert state['errors'] == ['graph: Atlas request failed; check connectivity', 'vectors: RuntimeError']
    assert required.mirror and required.vectors  # one failure never skips the other

    required.mirror_error = None
    atlas_autosync.schedule(repo['id'], current(repo))
    partial = db.get('repositories', repo['id'])['atlas_sync']
    assert partial['status'] == 'partial' and partial['nodes'] == 5 and partial['errors'] == ['vectors: RuntimeError']
    stored = str(db.list_records('repositories')) + str(db.list_records('events'))
    assert 'never-show-this' not in stored and 'mongodb+srv' not in stored


def test_dataplatform_error_with_uri_or_secret_is_redacted(required, tmp_path):
    required.mirror_error = data_platforms.DataPlatformError('cannot reach ' + URI_SECRET)
    required.vector_error = data_platforms.DataPlatformError('password = abcdefghijklmnop1234')
    repo = main.register_repo(make_root(tmp_path), 'Imported', {'source': 'zip'})
    text = str(db.get('repositories', repo['id'])['atlas_sync'])
    assert 'never-show-this' not in text and 'abcdefghijklmnop1234' not in text


def test_atlas_failure_never_breaks_import_or_refresh(required, tmp_path, monkeypatch):
    def explode(*args):
        raise RuntimeError('mirror process died')
    monkeypatch.setattr(atlas_graph, 'mirror', explode)
    monkeypatch.setattr(data_platforms, 'atlas_sync', explode)
    root = make_root(tmp_path)
    repo = main.register_repo(root, 'Imported', {'source': 'zip'})
    (root / 'main.py').write_text('value = 3\n', encoding='utf-8')
    refreshed = main.refresh_repo(repo['id'])
    saved = db.get('repositories', repo['id'])
    assert saved['snapshot'] == refreshed['snapshot'] and saved['index']['files'] and saved['name'] == 'Imported'
    assert saved['atlas_sync']['status'] == 'failed'

    monkeypatch.setattr(atlas_autosync, 'sync_snapshot', explode)  # even the orchestration itself failing
    (root / 'main.py').write_text('value = 4\n', encoding='utf-8')
    again = main.refresh_repo(repo['id'])
    assert again['snapshot'] != refreshed['snapshot']
    assert db.get('repositories', repo['id'])['atlas_sync']['status'] == 'failed'
    assert db.get('repositories', repo['id'])['atlas_sync']['errors'] == ['sync: RuntimeError']


def test_stale_result_never_overwrites_newer_snapshot_status(required, tmp_path, monkeypatch):
    root = make_root(tmp_path)
    repo = main.register_repo(root, 'Imported', {'source': 'zip'})
    old = current(repo)
    (root / 'main.py').write_text('value = 2\n', encoding='utf-8')
    newer = main.refresh_repo(repo['id'])
    newest = db.get('repositories', repo['id'])['atlas_sync']
    assert newest['snapshot_id'] == current(newer)
    # a slow sync of the old snapshot finishing late records nothing
    summary = atlas_autosync.sync_snapshot(repo['id'], old)
    assert summary['snapshot_id'] == old
    assert db.get('repositories', repo['id'])['atlas_sync'] == newest
    # and a queued request for an old snapshot is dropped before any Atlas call
    before = len(required.mirror)
    atlas_autosync.schedule(repo['id'], old, force=True)
    assert len(required.mirror) == before


def test_not_required_without_flag_skips_everything(env, monkeypatch, tmp_path):
    monkeypatch.setenv('STEWARD_ATLAS_REQUIRED', '0')
    assert atlas_autosync.should_sync() is False
    repo = main.register_repo(make_root(tmp_path), 'Imported', {'source': 'zip'})
    assert env.mirror == [] and env.vectors == []
    assert 'atlas_sync' not in db.get('repositories', repo['id'])
    assert atlas_autosync.sync_snapshot(repo['id'], current(repo))['status'] == 'skipped'
    assert atlas_autosync.schedule(repo['id'], current(repo)) is False
    assert env.mirror == [] and env.vectors == []


def test_legacy_flag_enables_sync_when_not_required(env, monkeypatch, tmp_path):
    monkeypatch.setenv('STEWARD_ATLAS_REQUIRED', '0')
    monkeypatch.setenv('STEWARD_ATLAS_GRAPH_MIRROR', '1')
    assert atlas_autosync.should_sync() is True
    repo = main.register_repo(make_root(tmp_path), 'Imported', {'source': 'zip'})
    assert len(env.mirror) == 1 and db.get('repositories', repo['id'])['atlas_sync']['status'] == 'synced'


def test_required_mode_lifts_the_separate_graph_mirror_opt_in(env, monkeypatch):
    monkeypatch.undo()  # real atlas_graph._config, not the fake mirror
    monkeypatch.delenv('STEWARD_ATLAS_GRAPH_MIRROR', raising=False)
    monkeypatch.delenv('STEWARD_ATLAS_ENABLED', raising=False)
    monkeypatch.setenv('STEWARD_ATLAS_REQUIRED', '0')
    with pytest.raises(atlas_graph.MirrorRefused, match='STEWARD_ATLAS_GRAPH_MIRROR'):
        atlas_graph._config()
    monkeypatch.setenv('STEWARD_ATLAS_REQUIRED', '1')
    with pytest.raises(atlas_graph.MirrorRefused, match='STEWARD_ATLAS_ENABLED'):  # got past the flag check
        atlas_graph._config()


def test_manual_route_records_status_and_maps_errors(required, tmp_path):
    repo = main.register_repo(make_root(tmp_path), 'Imported', {'source': 'zip'})
    required.mirror.clear()
    client = TestClient(main.app)
    url = f'/api/repositories/{repo["id"]}/atlas-graph-mirror'
    done = client.post(url)
    assert done.status_code == 200 and done.json()['nodes'] == 5 and done.json()['atlas_sync']['status'] == 'synced'
    assert len(required.mirror) == 1  # manual resync always runs, even when already synced

    required.mirror_error = atlas_graph.MirrorRefused('Atlas configuration is incomplete or invalid')
    refused = client.post(url)
    assert refused.status_code == 409
    state = db.get('repositories', repo['id'])['atlas_sync']
    assert state['status'] == 'partial' and state['errors'] == ['graph: Atlas configuration is incomplete or invalid']
    required.mirror_error = data_platforms.DataPlatformError('Atlas request failed')
    assert client.post(url).status_code == 503
    assert client.post('/api/repositories/missing/atlas-graph-mirror').status_code == 404


def test_startup_retries_unsynced_repositories_only(required, tmp_path):
    synced = main.register_repo(make_root(tmp_path), 'Imported', {'source': 'zip'})
    required.mirror.clear()
    with TestClient(main.app):  # lifespan: seeds the sample and revisits every repository
        pass
    ids = {call[0] for call in required.mirror}
    sample = next(r for r in db.list_records('repositories') if r.get('is_sample'))
    assert sample['id'] in ids and synced['id'] not in ids
    assert db.get('repositories', sample['id'])['atlas_sync']['status'] == 'synced'


def test_sample_reset_triggers_sync(required):
    repo = TestClient(main.app).post('/api/sample/reset').json()
    assert (repo['id'], current(repo), True) in required.mirror


def test_threaded_schedule_is_bounded_per_repo_and_newest_wins(required, tmp_path, monkeypatch):
    root = make_root(tmp_path)
    repo = main.register_repo(root, 'Imported', {'source': 'zip'})
    monkeypatch.setattr(atlas_autosync, 'RUN_INLINE', False)
    first = current(repo)
    seen, started, release, active, peak = [], threading.Event(), threading.Event(), [0], [0]
    guard = threading.Lock()

    def slow(repo_id, snapshot_id):
        with guard:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        seen.append(snapshot_id)
        started.set()
        release.wait(5)
        with guard:
            active[0] -= 1
        return {'nodes': 1, 'edges': 1, 'knowledge': 0}

    monkeypatch.setattr(atlas_graph, 'mirror', slow)
    assert atlas_autosync.schedule(repo['id'], first, force=True)
    assert started.wait(5)
    (root / 'main.py').write_text('value = 2\n', encoding='utf-8')
    for _ in range(3):  # queued behind the running one; superseded by the newest
        atlas_autosync.schedule(repo['id'], 'queued-and-replaced', force=True)
    refreshed = main.refresh_repo(repo['id'])  # schedules the real new snapshot, replacing the queue entry
    atlas_autosync.schedule(repo['id'], first, force=True)  # a delayed stale caller must not lose that request
    release.set()
    for _ in range(100):
        with atlas_autosync._lock:
            if repo['id'] not in atlas_autosync._running:
                break
        threading.Event().wait(0.05)
    assert peak[0] == 1
    assert seen[0] == first and seen[-1] == current(refreshed) and 'queued-and-replaced' not in seen
    assert db.get('repositories', repo['id'])['atlas_sync']['snapshot_id'] == current(refreshed)


@pytest.mark.parametrize('change', ['approve', 'create-approved', 'revoke', 'edit', 'stale'])
def test_approved_knowledge_changes_resync_the_same_snapshot(required, tmp_path, monkeypatch, change):
    repo = main.register_repo(make_root(tmp_path), 'Imported', {'source': 'zip'})
    entry = knowledge.create({'repo_id': repo['id'], 'title': 'Policy', 'content': 'Use cents',
                              'status': 'observed' if change == 'approve' else 'team-approved'})
    mirrored = []

    def mirror(repo_id, snapshot_id):
        # A write here also proves the knowledge transaction has committed before network work.
        db.event('Fake mirror reached', repo_id=repo_id)
        mirrored.append([item['content'] for item in db.list_records('knowledge')
                         if item['repo_id'] == repo_id and item['status'] == 'team-approved'])
        return {'nodes': 1, 'edges': 0, 'knowledge': len(mirrored[-1])}

    monkeypatch.setattr(atlas_graph, 'mirror', mirror)
    if change == 'approve':
        knowledge.approve(entry['id'], entry['version'], repo['snapshot'])
    elif change == 'create-approved':
        knowledge.create({'repo_id': repo['id'], 'title': 'Second', 'content': 'Keep receipts', 'status': 'team-approved'})
    elif change == 'stale':
        knowledge.invalidate_snapshot(repo['id'], repo['index'], {'files': []})
    else:
        knowledge.revise(entry['id'], {'status': 'rejected'} if change == 'revoke' else {'content': 'Use dollars'})
    expected = ['Use cents'] if change == 'approve' else ['Use cents', 'Keep receipts'] if change == 'create-approved' else []
    assert mirrored == [expected]
    state = db.get('repositories', repo['id'])['atlas_sync']
    assert state['status'] == 'synced' and state['snapshot_id'] == current(repo)


def test_knowledge_change_during_sync_cannot_restore_synced_status(required, tmp_path, monkeypatch):
    repo = main.register_repo(make_root(tmp_path), 'Imported', {'source': 'zip'})
    entry = knowledge.create({'repo_id': repo['id'], 'title': 'Policy', 'content': 'Use cents', 'status': 'team-approved'})
    monkeypatch.setattr(atlas_autosync, 'schedule', lambda *args, **kwargs: False)

    def revoke_while_uploading(repo_id, snapshot_id):
        knowledge.revise(entry['id'], {'status': 'rejected'})
        return {'nodes': 1, 'edges': 0, 'knowledge': 1}

    monkeypatch.setattr(atlas_graph, 'mirror', revoke_while_uploading)
    summary, _ = atlas_autosync.run_sync(repo['id'], current(repo))
    assert summary['status'] == 'superseded'
    assert db.get('repositories', repo['id'])['atlas_sync']['status'] != 'synced'
    assert not atlas_autosync._already_synced(repo['id'], current(repo))


def test_manual_stale_snapshot_never_reaches_atlas(required, tmp_path):
    repo = main.register_repo(make_root(tmp_path), 'Imported', {'source': 'zip'})
    required.mirror.clear()
    required.vectors.clear()
    summary, raw = atlas_autosync.run_sync(repo['id'], 'old-snapshot')
    assert summary['status'] == 'superseded'
    assert isinstance(raw['graph_error'], atlas_graph.MirrorRefused)
    assert required.mirror == [] and required.vectors == []


def test_busy_sync_times_out_without_overwriting_active_status(required, tmp_path, monkeypatch):
    from contextlib import contextmanager
    from backend import providers
    repo = main.register_repo(make_root(tmp_path), 'Imported', {'source': 'zip'})
    state = {'status': 'syncing', 'snapshot_id': current(repo), 'at': db.now()}
    db.mutate('repositories', repo['id'], lambda row: {**row, 'atlas_sync': state})
    required.mirror.clear()
    required.vectors.clear()

    @contextmanager
    def busy(*args):
        yield False

    ticks = iter([0, 31])
    monkeypatch.setattr(providers, '_runner_lock', busy)
    monkeypatch.setattr(atlas_autosync.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(atlas_autosync.time, 'sleep', lambda seconds: pytest.fail('Wait should already have expired'))
    summary, raw = atlas_autosync.run_sync(repo['id'], current(repo))
    assert summary['status'] == 'busy'
    assert isinstance(raw['graph_error'], data_platforms.DataPlatformError)
    assert db.get('repositories', repo['id'])['atlas_sync'] == state
    assert required.mirror == [] and required.vectors == []


@pytest.mark.parametrize('changed_snapshot', [False, True])
def test_background_retries_busy_sync_using_current_snapshot_and_policy(required, tmp_path, monkeypatch, changed_snapshot):
    repo = main.register_repo(make_root(tmp_path), 'Imported', {'source': 'zip'})
    monkeypatch.setattr(atlas_autosync, 'RUN_INLINE', False)
    snapshot_id = 'new-snapshot' if changed_snapshot else current(repo)
    seen = []

    def sync(repo_id, requested_snapshot):
        seen.append(requested_snapshot)
        if len(seen) == 1:
            db.mutate('repositories', repo_id, lambda row: {
                **row, 'source_snapshot_id': snapshot_id, 'atlas_knowledge_revision': 1,
                'atlas_sync': {'status': 'pending', 'snapshot_id': snapshot_id}})
            if changed_snapshot:
                atlas_autosync.schedule(repo_id, snapshot_id)
            return {'status': 'busy'}
        db.mutate('repositories', repo_id, lambda row: {
            **row, 'atlas_sync': {'status': 'synced', 'snapshot_id': requested_snapshot, 'knowledge_revision': 1}})
        return {'status': 'synced'}

    monkeypatch.setattr(atlas_autosync, 'sync_snapshot', sync)
    with atlas_autosync._lock:
        atlas_autosync._pending[repo['id']] = current(repo)
        atlas_autosync._running.add(repo['id'])
    atlas_autosync._worker(repo['id'])
    assert seen == [current(repo), snapshot_id]
    assert atlas_autosync._already_synced(repo['id'], snapshot_id)
    assert repo['id'] not in atlas_autosync._running


@pytest.mark.parametrize('cancel_result', [False, True])
def test_cancelled_projection_is_not_recorded_as_synced(required, tmp_path, monkeypatch, cancel_result):
    from backend.contracts import CancelledError
    repo = main.register_repo(make_root(tmp_path), 'Imported', {'source': 'zip'})
    required.vectors.clear()

    def cancel(repo_id, snapshot_id):
        assert db.get('repositories', repo_id)['atlas_sync']['status'] != 'synced'
        if cancel_result:
            return {'status': 'cancelled'}
        raise CancelledError('Cancelled')

    monkeypatch.setattr(atlas_graph, 'mirror', cancel)
    summary, _ = atlas_autosync.run_sync(repo['id'], current(repo))
    assert summary['status'] == 'cancelled'
    assert db.get('repositories', repo['id'])['atlas_sync']['status'] == 'cancelled'
    assert required.vectors == []


def test_manual_thread_waits_for_background_sync(required, tmp_path, monkeypatch):
    repo = main.register_repo(make_root(tmp_path), 'Imported', {'source': 'zip'})
    monkeypatch.setattr(atlas_autosync, 'RUN_INLINE', False)
    entered, release, manual_entered, requested = (threading.Event() for _ in range(4))

    def slow_mirror(repo_id, snapshot_id):
        if not entered.is_set():
            entered.set()
            assert release.wait(10)
        else:
            manual_entered.set()
        return {'nodes': 1, 'edges': 0, 'knowledge': 0}

    def manual():
        requested.set()
        atlas_autosync.run_sync(repo['id'], current(repo))

    monkeypatch.setattr(atlas_graph, 'mirror', slow_mirror)
    assert atlas_autosync.schedule(repo['id'], current(repo), force=True)
    assert entered.wait(5)
    thread = threading.Thread(target=manual)
    thread.start()
    try:
        assert requested.wait(5)
        assert not manual_entered.wait(0.2)
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive() and manual_entered.is_set()


def _manual_sync_process(store, repo_id, snapshot_id, started, entered, outcome):
    db.DATA_DIR = Path(store)

    def mirror(repo_id, snapshot_id):
        entered.set()
        (db.DATA_DIR / 'fake-remote-snapshot').write_text(snapshot_id, encoding='utf-8')
        return {'nodes': 1, 'edges': 0, 'knowledge': 0}

    atlas_graph.mirror = mirror
    data_platforms.atlas_sync = lambda *args: {'uploaded': 1}
    started.set()
    outcome.put(atlas_autosync.run_sync(repo_id, snapshot_id)[0]['status'])


def test_manual_process_waits_for_background_sync_before_replacing_remote(required, tmp_path, monkeypatch):
    repo = main.register_repo(make_root(tmp_path), 'Imported', {'source': 'zip'})
    monkeypatch.setattr(atlas_autosync, 'RUN_INLINE', False)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    remote = db.DATA_DIR / 'fake-remote-snapshot'

    def slow_mirror(repo_id, snapshot_id):
        entered.set()
        assert release.wait(15)
        remote.write_text(snapshot_id, encoding='utf-8')
        return {'nodes': 1, 'edges': 0, 'knowledge': 0}

    real_sync = atlas_autosync._safely_sync

    def track_completion(*args):
        try:
            return real_sync(*args)
        finally:
            finished.set()

    monkeypatch.setattr(atlas_graph, 'mirror', slow_mirror)
    monkeypatch.setattr(atlas_autosync, '_safely_sync', track_completion)
    assert atlas_autosync.schedule(repo['id'], current(repo), force=True)
    assert entered.wait(5)
    db.mutate('repositories', repo['id'], lambda row: {**row, 'source_snapshot_id': 'new-snapshot'})
    context = multiprocessing.get_context('spawn')
    started, manual_entered, outcome = context.Event(), context.Event(), context.Queue()
    process = context.Process(target=_manual_sync_process,
                              args=(str(db.DATA_DIR), repo['id'], 'new-snapshot', started, manual_entered, outcome))
    process.start()
    try:
        assert started.wait(10)
        assert not manual_entered.wait(0.5), 'manual sync overlapped the other process before it finished'
    finally:
        release.set()
        assert finished.wait(10)
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert process.exitcode == 0
    assert outcome.get(timeout=2) == 'synced'
    assert remote.read_text(encoding='utf-8') == 'new-snapshot'
    assert db.get('repositories', repo['id'])['atlas_sync']['snapshot_id'] == 'new-snapshot'
