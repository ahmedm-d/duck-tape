"""Sanitized immutable source bytes, separate from searchable text and live checkouts."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import uuid

from . import db, repository

MAX_FILES = 12000


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _check(cancel):
    if cancel is not None:
        cancel.raise_if_cancelled()


def _blob_path(digest):
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('Invalid snapshot blob hash')
    return db.DATA_DIR / 'snapshot-blobs' / digest[:2] / digest


def _origin(value):
    if isinstance(value, str):
        return {'source': 'zip' if value.casefold() in {'zip', 'zip upload'} else value}
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError('Invalid source provenance')
    return dict(value)


def capture(repo_id: str, source_dir: Path, origin: dict, cancel=None) -> dict:
    source_dir = Path(source_dir)
    if repository.is_link(source_dir) or not source_dir.is_dir():
        raise ValueError('Snapshot source must be a regular directory')
    root = source_dir.resolve()
    files, gaps = [], []
    total = 0
    origin = _origin(origin)
    modes = origin.get('file_modes', {})
    if modes and os.name == 'nt':
        gaps.append('Windows cannot observe Unix executable-bit changes; imported file modes remain authoritative')
    complete = True

    def walk_error(exc):
        nonlocal complete
        complete = False
        gaps.append('Source directory unreadable: ' + type(exc).__name__)

    for directory, dirs, names in os.walk(root, followlinks=False, onerror=walk_error):
        _check(cancel)
        dirs.sort()
        for name in list(dirs):
            path = Path(directory) / name
            rel = path.relative_to(root).as_posix()
            if name.casefold() in repository.EXCLUDED_DIRS or repository.is_link(path) or repository.secret_path(rel) or repository.unsafe_path(rel):
                if repository.is_link(path):
                    gaps.append(f'{rel}: linked directory not captured; external content unavailable')
                if len(files) >= MAX_FILES:
                    complete = False
                    break
                files.append({'path': rel + '/', 'status': 'excluded', 'reason': 'Secret/generated/vendor/VCS directory or link; children not enumerated'})
                dirs.remove(name)
        if not complete:
            break
        for name in sorted(names):
            _check(cancel)
            if len(files) >= MAX_FILES:
                complete = False
                break
            path = Path(directory) / name
            rel = path.relative_to(root).as_posix()
            entry = {'path': rel, 'status': 'excluded', 'reason': ''}
            files.append(entry)
            if repository.is_link(path) or repository.unsafe_path(rel) or repository.secret_path(rel) or not path.resolve().is_relative_to(root):
                entry['reason'] = 'Secret-bearing path, unsafe path or link'
                if repository.is_link(path):
                    gaps.append(f'{rel}: linked file not captured; external content unavailable')
                continue
            try:
                info = path.stat()
                if not stat.S_ISREG(info.st_mode):
                    entry['reason'] = 'Special file'
                    gaps.append(f'{rel}: unsupported special file not captured')
                    continue
                size = info.st_size
                entry['size'] = size
                if size > repository.MAX_EXPANDED or total + size > repository.MAX_EXPANDED:
                    entry['reason'] = 'Snapshot byte limit'
                    complete = False
                    gaps.append(f'{rel}: source exceeds snapshot byte limit; execution unavailable')
                    continue
                with path.open('rb') as stream:
                    data = stream.read(min(repository.MAX_EXPANDED - total, size) + 1)
                if len(data) != size:
                    entry['reason'] = 'Source changed while capturing'
                    complete = False
                    continue
                # Scan binary assets too: decoding must never bypass the secret boundary.
                text = data.decode('utf-8', errors='replace')
                if repository.SECRET.search(text) or repository.SECRET_ASSIGNMENT.search(text):
                    entry['reason'] = 'Potential credential material detected'
                    continue
                digest = hashlib.sha256(data).hexdigest()
                blob = _blob_path(digest)
                blob.parent.mkdir(parents=True, exist_ok=True)
                if blob.exists():
                    if hashlib.sha256(blob.read_bytes()).hexdigest() != digest:
                        raise ValueError('Snapshot blob integrity failure')
                else:
                    temporary = blob.parent / (uuid.uuid4().hex + '.tmp')
                    try:
                        temporary.write_bytes(data)
                        os.replace(temporary, blob)
                    finally:
                        temporary.unlink(missing_ok=True)
                mode = 0o755 if (int(modes.get(rel, info.st_mode)) if os.name == 'nt' else info.st_mode) & 0o111 else 0o644
                entry.update(status='included', reason='', hash=digest, mode=mode)
                total += size
                if name == '.gitmodules':
                    gaps.append('Submodules are not fetched; their external content is unavailable')
            except OSError as exc:
                entry.update(status='failed', reason=type(exc).__name__)
                complete = False
        if not complete:
            break
    if not complete:
        gaps.append('Source inventory incomplete or capped at 12000 entries; execution requires a complete snapshot')
    files.sort(key=lambda item: item['path'])
    manifest_hash = _digest(files)
    identity = _digest({'repo_id': repo_id, 'manifest_hash': manifest_hash, 'origin': origin, 'complete': complete})
    existing = db.get('source_snapshots', identity)
    if existing:
        return existing
    _check(cancel)
    snapshot = {'id': identity, 'repo_id': repo_id, 'manifest_hash': manifest_hash, 'files': files,
                'origin': origin, 'branch': origin.get('branch'), 'commit': origin.get('commit'),
                'gaps': gaps + list(origin.get('gaps', [])), 'complete': complete, 'total_bytes': total}
    return db.put('source_snapshots', snapshot)


def read_files(snapshot_id: str) -> dict[str, bytes]:
    snapshot = db.get('source_snapshots', snapshot_id)
    if not snapshot:
        raise ValueError('Source snapshot not found')
    if not snapshot.get('complete', True):
        raise ValueError('Source snapshot inventory is incomplete')
    if _digest(snapshot['files']) != snapshot['manifest_hash']:
        raise ValueError('Snapshot manifest integrity failure')
    entries = [entry for entry in snapshot['files'] if entry['status'] == 'included']
    result = {}
    total = 0
    for entry in entries:
        if (repository.unsafe_path(entry['path']) or repository.secret_path(entry['path'])
                or any(part.casefold() in repository.EXCLUDED_DIRS for part in Path(entry['path']).parts)):
            raise ValueError('Unsafe snapshot path')
        try:
            blob = _blob_path(entry['hash'])
            with blob.open('rb') as stream:
                data = stream.read(repository.MAX_EXPANDED + 1)
            if (len(data) != entry['size'] or total + len(data) > repository.MAX_EXPANDED
                    or hashlib.sha256(data).hexdigest() != entry['hash']):
                raise ValueError('Snapshot blob integrity failure')
            result[entry['path']] = data
            total += len(data)
        except OSError as exc:
            raise ValueError('Snapshot blob integrity failure: missing/unreadable blob') from exc
    return result


def materialize(snapshot_id: str, destination: Path) -> dict:
    destination = Path(destination)
    if repository.is_link(destination) or (destination.exists() and (not destination.is_dir() or any(destination.iterdir()))):
        raise ValueError('Materialization destination must be an empty regular directory')
    contents = read_files(snapshot_id)
    snapshot = db.get('source_snapshots', snapshot_id)
    entries = [entry for entry in snapshot['files'] if entry['status'] == 'included']
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    for entry in entries:
        target = destination.joinpath(*entry['path'].split('/'))
        if not target.resolve().is_relative_to(root):
            raise ValueError('Snapshot path escapes destination')
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as output:
            output.write(contents[entry['path']])
        target.chmod(entry['mode'])
    return {'snapshot_id': snapshot_id, 'manifest_hash': snapshot['manifest_hash'], 'files': len(entries),
            'destination': str(destination), 'gaps': snapshot.get('gaps', [])}


def _refresh(repo_id, cancel, replacement=None):
    _check(cancel)
    repo = db.get('repositories', repo_id)
    if not repo:
        raise ValueError('Repository not found')
    origin = _origin(repo.get('origin'))
    source = origin.get('source', '')
    if replacement is None and source == 'zip':
        raise ValueError('ZIP refresh requires an explicit replacement ZIP upload')
    if replacement is None and not source.startswith('https://'):
        raise ValueError('Repository has no refreshable public HTTPS upstream')
    scratch = db.DATA_DIR / 'ingestion' / uuid.uuid4().hex
    scratch.mkdir(parents=True)
    try:
        target = scratch / 'source'
        imported = repository.import_zip(replacement, target) if replacement is not None else repository.import_git(source, target, cancel=cancel)
        _check(cancel)
        return capture(repo_id, target, imported, cancel=cancel)
    finally:
        shutil.rmtree(scratch)


def refresh(repo_id: str, cancel=None) -> dict:
    return _refresh(repo_id, cancel)


def refresh_zip(repo_id: str, data: bytes, cancel=None) -> dict:
    return _refresh(repo_id, cancel, replacement=data)
