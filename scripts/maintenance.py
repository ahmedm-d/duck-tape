"""Offline workspace backup, original-location restore, and conservative cleanup."""
import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATABASE = 'steward.sqlite3'
SIDECARS = {DATABASE + '-wal', DATABASE + '-shm', DATABASE + '-journal'}


def is_link(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def regular_path(value):
    path = Path(value).absolute()
    for part in (path, *path.parents):
        if is_link(part):
            raise ValueError('Symbolic links and junctions are not allowed')
    return path.resolve()


def inventory(root):
    files, directories = {}, []
    def unreadable(error):
        raise error
    for directory, children, names in os.walk(root, followlinks=False, onerror=unreadable):
        for name in children + names:
            path = Path(directory) / name
            if is_link(path):
                raise ValueError('Workspace contains a symbolic link or junction')
            mode = path.stat().st_mode
            relative = path.relative_to(root).as_posix()
            if stat.S_ISDIR(mode):
                directories.append(relative)
            elif stat.S_ISREG(mode):
                files[relative] = path
            else:
                raise ValueError('Workspace contains an unsupported special file')
    return files, sorted(directories)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def rows(connection):
    values = [(kind, json.loads(raw)) for kind, raw in connection.execute('SELECT kind,data FROM records')]
    if connection.execute("SELECT 1 FROM sqlite_master WHERE name='work_items'").fetchone():
        values.extend(('work_item', json.loads(raw)) for raw, in connection.execute('SELECT data FROM work_items'))
    return values


@contextmanager
def offline(data):
    data = regular_path(data)
    if not data.is_dir() or not (data / DATABASE).is_file():
        raise ValueError('An existing Steward SQLite workspace is required')
    regular_path(data / DATABASE)
    connection = sqlite3.connect(data / DATABASE, timeout=2)
    try:
        connection.execute('BEGIN IMMEDIATE')
        records = rows(connection)
        now = time.time()
        for kind, record in records:
            if record.get('lease_until', 0) and float(record['lease_until']) > now:
                raise ValueError('Stop the API and worker first: active lease')
            if kind == 'services' and record.get('status') == 'running':
                try:
                    heartbeat = datetime.fromisoformat(record['heartbeat']).timestamp()
                except (ValueError, KeyError, TypeError):
                    raise ValueError('Stop the API and worker first: unknown running service heartbeat')
                if now - heartbeat < 120:
                    raise ValueError('Stop the API and worker first: recently running service')
        yield data, connection, records
    finally:
        connection.rollback()
        connection.close()


def backup(data, destination):
    destination = regular_path(destination)
    with offline(data) as (data, _, records):
        if destination.is_relative_to(data):
            raise ValueError('Backup destination must be outside the workspace')
        if destination.exists():
            raise ValueError('Backup destination already exists; refusing overwrite')
        files, directories = inventory(data)
        destination.mkdir(parents=True)
        payload = destination / 'workspace'
        payload.mkdir()
        for name in directories:
            (payload / name).mkdir(parents=True, exist_ok=True)
        for name, source in files.items():
            if name == DATABASE or name in SIDECARS:
                continue
            target = payload / name
            shutil.copy2(source, target)
            if digest(source) != digest(target):
                raise ValueError('Workspace changed during backup; stop all writers and retry')
        # The held writer reservation blocks new commits; a separate reader sees
        # all committed WAL pages without backing up an open write transaction.
        with closing(sqlite3.connect((data / DATABASE).as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(payload / DATABASE)) as target:
                source.backup(target)
                target.execute('PRAGMA journal_mode=DELETE')
                if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError('Database backup integrity check failed')
        current, _ = inventory(data)
        if set(current) - SIDECARS != set(files) - SIDECARS:
            raise ValueError('Workspace files changed during backup; stop all writers and retry')
        copied, copied_dirs = inventory(payload)
        manifest = {'version': 1, 'original_data_dir': str(data), 'created_at': datetime.now(timezone.utc).isoformat(),
                    'directories': copied_dirs,
                    'files': {name: {'size': path.stat().st_size, 'sha256': digest(path)} for name, path in copied.items()}}
        (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        return {'backup': str(destination), 'files': len(copied), 'bytes': sum(item['size'] for item in manifest['files'].values())}


def manifest_path(name):
    if not isinstance(name, str):
        raise ValueError('Unsafe backup manifest path')
    path = PurePosixPath(name)
    if (not name or '\\' in name or ':' in name or path.is_absolute()
            or any(part in ('', '.', '..') for part in name.split('/')) or str(path) != name
            or any(ord(char) < 32 for char in name)):
        raise ValueError('Unsafe backup manifest path')
    return path


def restore(source, destination):
    source, destination = regular_path(source), regular_path(destination)
    manifest_file = source / 'manifest.json'
    regular_path(manifest_file)
    if not manifest_file.is_file() or manifest_file.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('Missing or oversized backup manifest')
    manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
    if manifest.get('version') != 1 or not Path(manifest['original_data_dir']).is_absolute():
        raise ValueError('Unsupported backup manifest')
    if destination != regular_path(manifest['original_data_dir']):
        raise ValueError('Restore is restricted to the original absolute data location')
    if destination.exists():
        raise ValueError('Restore target exists; refusing overwrite')
    payload = regular_path(source / 'workspace')
    if not payload.is_dir() or destination.is_relative_to(source):
        raise ValueError('Invalid backup workspace or restore location')
    for name in [*manifest['files'], *manifest['directories']]:
        manifest_path(name)
    files, directories = inventory(payload)
    if DATABASE not in manifest['files'] or set(files) != set(manifest['files']) or directories != manifest['directories']:
        raise ValueError('Backup manifest inventory integrity mismatch')
    for name, path in files.items():
        expected = manifest['files'][name]
        if path.stat().st_size != expected['size'] or digest(path) != expected['sha256']:
            raise ValueError('Backup file hash integrity mismatch: ' + name)
    with closing(sqlite3.connect((payload / DATABASE).as_uri() + '?mode=ro', uri=True)) as connection:
        if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('Backup database integrity check failed')
    # Never merge an existing directory or rewrite evidence's absolute references.
    shutil.copytree(payload, destination)
    restored, _ = inventory(destination)
    if any(digest(path) != manifest['files'][name]['sha256'] for name, path in restored.items()):
        raise ValueError('Restored file hash integrity mismatch; retain the backup')
    return {'restored': str(destination), 'files': len(restored)}


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def cleanup(data, *, apply=False, older_than_days=7):
    if not isinstance(older_than_days, (int, float)) or not math.isfinite(older_than_days) or older_than_days < 1:
        raise ValueError('Artifacts must be at least one day old')
    with offline(data) as (data, _, records):
        inventory(data)  # Reject links/special files before making any deletion.
        references = set(text for _, record in records for text in strings(record))
        paths = []
        for reference in references:
            if not reference or len(reference) > 32000 or '\x00' in reference:
                continue
            try:
                value = Path(reference)
                value = (value if value.is_absolute() else data / value).resolve()
                if value.is_relative_to(data):
                    paths.append(value)
            except (OSError, ValueError):
                continue
        eligible, retained = [], []
        cutoff = time.time() - older_than_days * 86400
        # shortcut: retain worktrees, model-smoke and all evidence indefinitely;
        # broaden only when a complete reference/retention contract is available.
        for name, pattern in [('ingestion-input', r'[a-f0-9]{64}\.zip'), ('ingestion', r'[a-f0-9]{32}')]:
            root = regular_path(data / name)
            if not root.is_dir():
                continue
            for candidate in root.iterdir():
                if not re.fullmatch(pattern, candidate.name):
                    retained.append({'path': str(candidate), 'reason': 'Unrecognized artifact name'})
                    continue
                regular_path(candidate)
                if (name == 'ingestion-input' and not candidate.is_file()) or (name == 'ingestion' and not candidate.is_dir()):
                    retained.append({'path': str(candidate), 'reason': 'Unexpected artifact type'})
                    continue
                files, directories = inventory(candidate) if candidate.is_dir() else ({candidate.name: candidate}, [])
                size = sum(path.stat().st_size for path in files.values())
                members = [candidate, *files.values(), *(candidate / path for path in directories)]
                recent = max(path.stat().st_mtime for path in members) >= cutoff
                referenced = candidate.stem in references or any(path.is_relative_to(candidate) or candidate.is_relative_to(path) for path in paths)
                item = {'path': str(candidate), 'bytes': size}
                if recent or referenced:
                    retained.append({**item, 'reason': 'Recent artifact' if recent else 'Persisted reference'})
                    continue
                eligible.append(item)
                if apply:
                    verified = regular_path(candidate)
                    if verified.parent != root or not verified.is_relative_to(data):
                        raise ValueError('Refusing cleanup outside the explicit artifact root')
                    if verified.is_dir():
                        inventory(verified)
                        shutil.rmtree(verified)
                    else:
                        verified.unlink()
        remaining, _ = inventory(data)
        return {'dry_run': not apply, 'eligible': eligible, 'retained': retained,
                'deleted_bytes': sum(item['bytes'] for item in eligible) if apply else 0,
                'workspace_bytes_retained': sum(path.stat().st_size for path in remaining.values()),
                'limit': 'Only old unreferenced ingestion ZIPs and UUID scratch directories; no global storage quota.'}


def main(argv=None):
    from run import load_environment
    load_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', default=os.environ.get('STEWARD_DATA_DIR', str(ROOT / 'data/workspace')))
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('backup').add_argument('destination')
    commands.add_parser('restore').add_argument('source')
    clean = commands.add_parser('cleanup')
    clean.add_argument('--apply', action='store_true')
    clean.add_argument('--older-than-days', type=float, default=7)
    args = parser.parse_args(argv)
    try:
        result = backup(args.data_dir, args.destination) if args.command == 'backup' else restore(args.source, args.data_dir) if args.command == 'restore' else cleanup(args.data_dir, apply=args.apply, older_than_days=args.older_than_days)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        print(json.dumps({'error': str(exc), 'action': args.command}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
