"""Automatic Atlas sync when the current snapshot or approved knowledge changes (Atlas-required mode).

Two independent projections are pushed for the current snapshot: the graph plus approved knowledge
(``atlas_graph.mirror``) and the hashed evidence vectors (``data_platforms.atlas_sync``). SQLite stays
authoritative; the outcome is recorded on the repository record as ``atlas_sync`` and in the events log.

Callers invoke :func:`schedule` only *after* their local commit. It never blocks, never raises and never
runs inside a SQLite transaction: one daemon thread per repository coalesces requests to the current
snapshot, and an OS lock serializes manual/background syncs across processes sharing the store.
Tests set ``RUN_INLINE = True``.
Only fixed, secret-free messages are ever recorded; Atlas credentials and URIs are never stored.
"""
import hashlib
import os
import re
import threading
import time

from . import db
from .atlas_mode import atlas_required
from .contracts import CancelledError

LEGACY_FLAG = 'STEWARD_ATLAS_GRAPH_MIRROR'
RUN_INLINE = False  # test hook: run schedule() synchronously in the caller's thread
MAX_ERROR = 200
_lock = threading.Lock()
_pending = {}  # repo_id -> newest requested snapshot id, consumed by that repository's worker
_running = set()  # repo ids with a live worker thread
_URI = re.compile(r'\b[a-z][a-z0-9+.-]*://\S+', re.I)


def should_sync() -> bool:
    """Atlas-required mode, or the legacy explicit opt-in to the graph mirror."""
    return atlas_required() or os.getenv(LEGACY_FLAG) == '1'


def _safe(label: str, exc: BaseException) -> str:
    from .data_platforms import DataPlatformError
    from .repository import SECRET, SECRET_ASSIGNMENT
    # DataPlatformError messages are fixed and safe by contract; anything else is reduced to its type.
    text = str(exc) if isinstance(exc, DataPlatformError) else type(exc).__name__
    text = _URI.sub('<uri>', text)
    if SECRET.search(text) or SECRET_ASSIGNMENT.search(text):
        text = type(exc).__name__
    return f'{label}: {text}'[:MAX_ERROR]


def _current(repo: dict) -> str:
    return repo.get('source_snapshot_id') or repo['snapshot']


class _Stale(Exception):
    pass


def _record(repo_id: str, snapshot_id: str, summary: dict) -> bool:
    """Persist the outcome only while its snapshot and approved knowledge revision remain current."""
    def update(repo):
        if (_current(repo) != snapshot_id or
                repo.get('atlas_knowledge_revision', 0) != summary.get('knowledge_revision', repo.get('atlas_knowledge_revision', 0))):
            raise _Stale()
        return {**repo, 'atlas_sync': summary}
    try:
        db.mutate('repositories', repo_id, update)
        return True
    except (_Stale, KeyError):
        return False


def run_sync(repo_id: str, snapshot_id: str) -> tuple[dict, dict]:
    """Run both projections and record the result. Returns (summary, raw) where ``raw`` holds the mirror
    result and the live exceptions per component, for callers (the manual route) that map them to HTTP."""
    from .data_platforms import DataPlatformError
    from .providers import _runner_lock
    # Share the existing OS lock across API/worker processes without holding SQLite during network I/O.
    lock_id = 'atlas-' + hashlib.sha256(repo_id.encode()).hexdigest()
    deadline = time.monotonic() + 30
    while True:
        with _runner_lock(db.DATA_DIR / 'atlas-sync', lock_id) as acquired:
            if acquired:
                return _run_sync(repo_id, snapshot_id)
        if time.monotonic() >= deadline:
            error = DataPlatformError('Atlas sync is already running; retry shortly')
            return ({'status': 'busy', 'snapshot_id': snapshot_id, 'at': db.now(), 'nodes': 0, 'edges': 0,
                     'knowledge': 0, 'chunks': 0, 'errors': [_safe('sync', error)]},
                    {'mirror': None, 'vectors': None, 'graph_error': error, 'vector_error': None})
        time.sleep(0.05)


def _run_sync(repo_id: str, snapshot_id: str) -> tuple[dict, dict]:
    from . import atlas_graph, data_platforms
    raw = {'mirror': None, 'vectors': None, 'graph_error': None, 'vector_error': None}
    repo = db.get('repositories', repo_id)
    revision = (repo or {}).get('atlas_knowledge_revision', 0)
    summary = {'status': 'syncing', 'snapshot_id': snapshot_id, 'knowledge_revision': revision,
               'at': db.now(), 'nodes': 0, 'edges': 0, 'knowledge': 0, 'chunks': 0, 'errors': []}

    def superseded():
        summary['status'] = 'superseded'
        raw['graph_error'] = atlas_graph.MirrorRefused('Atlas sync superseded by a newer snapshot or approved knowledge')
        return summary, raw

    if not _record(repo_id, snapshot_id, summary):
        return superseded()
    errors = []
    cancelled = False
    for label, field, error_field, project in [('graph', 'mirror', 'graph_error', atlas_graph.mirror),
                                              ('vectors', 'vectors', 'vector_error', data_platforms.atlas_sync)]:
        current = db.get('repositories', repo_id)
        if (not current or _current(current) != snapshot_id or current.get('atlas_knowledge_revision', 0) != revision):
            return superseded()
        try:
            result = project(repo_id, snapshot_id)
            if result.get('cancelled') or result.get('status') == 'cancelled':
                raise CancelledError('Atlas sync cancelled')
            raw[field] = result
        except CancelledError:
            raw[error_field] = atlas_graph.MirrorRefused('Atlas sync cancelled')
            errors.append(_safe(label, raw[error_field]))
            cancelled = True
            break
        except Exception as exc:  # one projection failing must not skip the other
            raw[error_field] = exc
            errors.append(_safe(label, exc))
    mirror, vectors = raw['mirror'], raw['vectors']
    status = 'cancelled' if cancelled else 'failed' if mirror is None and vectors is None else 'partial' if errors else 'synced'
    summary = {**summary, 'status': status, 'at': db.now(),
               'nodes': (mirror or {}).get('nodes', 0), 'edges': (mirror or {}).get('edges', 0),
               'knowledge': (mirror or {}).get('knowledge', 0), 'chunks': (vectors or {}).get('uploaded', 0),
               'errors': errors}
    if not _record(repo_id, snapshot_id, summary):
        return superseded()
    message = f'Atlas sync {status} for the current snapshot'
    if errors:
        message += ' (' + '; '.join(errors) + ')'
    db.event(message[:500], repo_id=repo_id)
    return summary, raw


def sync_snapshot(repo_id: str, snapshot_id: str) -> dict:
    """Mirror graph + approved knowledge and upload vectors for one snapshot; never raises on Atlas failure."""
    if not should_sync():
        return {'status': 'skipped', 'snapshot_id': snapshot_id, 'at': db.now(), 'nodes': 0, 'edges': 0,
                'knowledge': 0, 'chunks': 0, 'errors': []}
    return run_sync(repo_id, snapshot_id)[0]


def _safely_sync(repo_id: str, snapshot_id: str) -> dict | None:
    try:
        repo = db.get('repositories', repo_id)
        if repo is None:
            return
        if _current(repo) != snapshot_id:
            snapshot_id = _current(repo)  # A delayed stale request must not displace the current queued snapshot.
            if _already_synced(repo_id, snapshot_id):
                return
        return sync_snapshot(repo_id, snapshot_id)
    except Exception as exc:  # last resort: record what we can, never propagate
        try:
            summary = {'status': 'failed', 'snapshot_id': snapshot_id, 'at': db.now(), 'nodes': 0, 'edges': 0,
                       'knowledge': 0, 'chunks': 0, 'errors': [_safe('sync', exc)]}
            if _record(repo_id, snapshot_id, summary):
                db.event('Atlas sync failed for the current snapshot (' + summary['errors'][0] + ')', repo_id=repo_id)
        except Exception:
            pass


def _worker(repo_id: str) -> None:
    while True:
        with _lock:
            snapshot_id = _pending.pop(repo_id, None)
            if snapshot_id is None:
                _running.discard(repo_id)
                return
        summary = _safely_sync(repo_id, snapshot_id)
        if summary and summary.get('status') == 'busy':
            with _lock:
                _pending.setdefault(repo_id, snapshot_id)


def _already_synced(repo_id: str, snapshot_id: str) -> bool:
    try:
        repo = db.get('repositories', repo_id)
    except Exception:
        return False
    state = (repo or {}).get('atlas_sync') or {}
    return (state.get('status') == 'synced' and state.get('snapshot_id') == snapshot_id
            and state.get('knowledge_revision', 0) == (repo or {}).get('atlas_knowledge_revision', 0))


def schedule(repo_id: str, snapshot_id: str, *, force: bool = False) -> bool:
    """Request a background sync of a snapshot that is already committed locally.

    Returns whether a sync was started or queued. At most one sync runs per repository; a request that
    arrives meanwhile replaces any older queued one. Without ``force``, a snapshot already recorded as
    fully synced with the same approved knowledge revision is not uploaded again.
    """
    try:
        if not should_sync() or not repo_id or not snapshot_id:
            return False
        if not force and _already_synced(repo_id, snapshot_id):
            return False
        if RUN_INLINE:
            _safely_sync(repo_id, snapshot_id)
            return True
        with _lock:
            _pending[repo_id] = snapshot_id
            if repo_id in _running:
                return True
            _running.add(repo_id)
        try:
            threading.Thread(target=_worker, args=(repo_id,), daemon=True, name='atlas-sync-' + repo_id[:8]).start()
        except BaseException:
            with _lock:
                _running.discard(repo_id)
                _pending.pop(repo_id, None)
            raise
        return True
    except Exception:
        return False
