"""Exact edit operations over immutable sanitized snapshots; never execute repository code."""
import hashlib
import json
from pathlib import Path, PurePosixPath

from . import db, repository, sample, snapshots
from .contracts import CandidatePatch, EditOperation


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _path(path):
    if (not isinstance(path, str) or repository.unsafe_path(path) or repository.secret_path(path)
            or str(PurePosixPath(path)) != path or any(part.casefold() in repository.EXCLUDED_DIRS for part in PurePosixPath(path).parts)):
        raise ValueError('Unsafe edit path')
    return path


def _read_key(snapshot_id, path, expected_hash):
    return _hash((snapshot_id+'\0'+path+'\0'+expected_hash).encode())


def read_file(snapshot_id: str, path: str, expected_hash: str) -> bytes:
    """Persist the trusted fact that complete source evidence was obtained for this hash."""
    _path(path)
    contents = snapshots.read_files(snapshot_id)
    data = contents.get(path)
    if data is None or _hash(data) != expected_hash:
        raise ValueError('File evidence preimage mismatch')
    db.put('complete_file_reads', {'id': _read_key(snapshot_id, path, expected_hash), 'snapshot_id': snapshot_id,
                                  'path': path, 'hash': expected_hash, 'complete': True})
    return data


def _content(text):
    if not isinstance(text, str) or len(text.encode()) > repository.MAX_EXPANDED:
        raise ValueError('Invalid edit content')
    if repository.SECRET.search(text) or repository.SECRET_ASSIGNMENT.search(text):
        raise ValueError('Potential secret in edit content')
    return text.encode('utf-8')


def _binary(data):
    if data is None:
        return False
    try:
        data.decode('utf-8')
        return b'\0' in data
    except UnicodeDecodeError:
        return True


def _stage(root, contents, modes):
    """Stage exact bytes/modes with no attributes, conversions or executable filters."""
    paths = sorted(contents)
    for offset in range(0, len(paths), 16):
        batch = paths[offset:offset+16]
        blobs = sample.git(root, 'hash-object', '-w', '--no-filters', '--', *batch).splitlines()
        if len(blobs) != len(batch):
            raise ValueError('Git object staging mismatch')
        arguments = []
        for path, blob in zip(batch, blobs):
            arguments += ['--cacheinfo', f'{0o100000 | modes[path]:o},{blob},{path}']
        sample.git(root, 'update-index', '--add', *arguments)


def apply(snapshot, operations: list[EditOperation], destination: Path) -> CandidatePatch:
    snapshot_id = snapshot if isinstance(snapshot, str) else snapshot['id'] if isinstance(snapshot, dict) else snapshot.id
    stored = db.get('source_snapshots', snapshot_id)
    if not stored:
        raise ValueError('Source snapshot not found')
    before = snapshots.read_files(snapshot_id)
    contents = dict(before)
    modes = {entry['path']: entry['mode'] for entry in stored['files'] if entry['status'] == 'included'}
    before_modes = dict(modes)
    if not isinstance(operations, list) or not 1 <= len(operations) <= 20:
        raise ValueError('Expected 1..20 edit operations')
    touched = set()
    allowed_fields = set(EditOperation.model_fields)
    for raw in operations:
        operation = raw if isinstance(raw, EditOperation) else EditOperation.model_validate(raw)
        if set(operation.model_dump()) - allowed_fields:
            raise ValueError('Unsupported edit fields')
        path = _path(operation.path)
        if operation.op == 'create':
            if path in contents or operation.expected_hash is not None or operation.destination is not None or operation.replacements:
                raise ValueError('Create precondition mismatch for '+path+'; existing files require update with the supplied original hash')
            contents[path] = _content(operation.content)
            modes[path] = 0o644
        else:
            if path not in contents or not operation.expected_hash or _hash(contents[path]) != operation.expected_hash:
                raise ValueError('Edit preimage mismatch for '+path+'; obtain the exact current source hash')
            if operation.op == 'update':
                if operation.destination is not None:
                    raise ValueError('Update cannot declare a rename destination')
                if operation.content is not None:
                    evidence = db.get('complete_file_reads', _read_key(snapshot_id, path, operation.expected_hash))
                    if not operation.complete_file or not evidence or not evidence.get('complete') or operation.replacements:
                        raise ValueError('Whole-file replacement requires trusted complete file evidence')
                    contents[path] = _content(operation.content)
                else:
                    if not 1 <= len(operation.replacements) <= 100:
                        raise ValueError('Update needs exact anchored replacements')
                    data = contents[path]
                    for replacement in operation.replacements:
                        if set(replacement) != {'old', 'new'} or not replacement['old']:
                            raise ValueError('Invalid anchored replacement')
                        old, new = _content(replacement['old']), _content(replacement['new'])
                        if data.count(old) != 1:
                            raise ValueError('Replacement preimage must match exactly once')
                        data = data.replace(old, new, 1)
                    _content(data.decode('utf-8'))
                    contents[path] = data
            elif operation.op == 'delete':
                if operation.content is not None or operation.destination is not None or operation.replacements:
                    raise ValueError('Delete has unexpected content')
                del contents[path]
                del modes[path]
            elif operation.op == 'rename':
                target = _path(operation.destination)
                if target in contents or operation.content is not None or operation.replacements:
                    raise ValueError('Rename precondition mismatch')
                contents[target] = contents.pop(path)
                modes[target] = modes.pop(path)
                touched.add(target)
            else:
                raise ValueError('Unsupported edit operation')
        touched.add(path)
    if sum(len(data) for data in contents.values()) > repository.MAX_EXPANDED or len(contents) > snapshots.MAX_FILES:
        raise ValueError('Edited snapshot exceeds source limits')
    folded = [path.casefold() for path in contents]
    if len(folded) != len(set(folded)) or any(str(parent) in contents for path in contents for parent in PurePosixPath(path).parents if str(parent) != '.'):
        raise ValueError('Edited paths collide')
    changed = {path for path in set(before) | set(contents) if before.get(path) != contents.get(path) or before_modes.get(path) != modes.get(path)}
    if not changed or not changed <= touched:
        raise ValueError('Edit produced no declared changes')
    destination = Path(destination)
    snapshots.materialize(snapshot_id, destination)
    sample.git(destination, 'init', '-b', 'main')
    _stage(destination, before, before_modes)
    sample.git(destination, '-c', 'user.name=Duck Tape', '-c', 'user.email=duck-tape@localhost', 'commit', '--allow-empty', '-m', 'Immutable snapshot base')
    for path in sorted(changed - set(contents), reverse=True):
        target = destination.joinpath(*path.split('/'))
        target.unlink()
        sample.git(destination, 'update-index', '--force-remove', '--', path)
        parent = target.parent
        while parent != destination and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    for path in sorted(changed & set(contents)):
        target = destination.joinpath(*path.split('/'))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents[path])
        target.chmod(modes[path])
    _stage(destination, {path: contents[path] for path in changed if path in contents}, modes)
    actual = {path for path in sample.git(destination, 'diff', '--cached', '--name-only', '-z', '--no-renames').split('\0') if path}
    if actual != changed:
        raise ValueError('Actual Git changed paths differ from declared edits')
    for path, data in contents.items():
        if destination.joinpath(*path.split('/')).read_bytes() != data:
            raise ValueError('Candidate bytes differ from declared edits')
    expected_index = {}
    for path, data in contents.items():
        blob = hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        expected_index[path] = (f'{0o100000 | modes[path]:o}', blob)
    actual_index = {}
    for record in sample.git(destination, 'ls-files', '--stage', '-z').split('\0'):
        if record:
            metadata, path = record.split('\t', 1)
            mode, blob, stage = metadata.split()
            if stage != '0':
                raise ValueError('Unmerged candidate index')
            actual_index[path] = (mode, blob)
    if actual_index != expected_index:
        raise ValueError('Candidate index content/modes differ from declared edits')
    binary_paths = {path for path in changed if _binary(before.get(path)) or _binary(contents.get(path))}
    (destination/'.git'/'info'/'attributes').write_text(''.join(json.dumps('/'+path, ensure_ascii=False)+' -diff\n' for path in sorted(binary_paths)), encoding='utf-8')
    patch_path = destination/'.git'/'steward.patch'
    sample.git(destination, 'diff', '--cached', '--binary', '--full-index', '--no-ext-diff', '--no-textconv', '--no-renames', '--output='+str(patch_path.resolve()))
    diff = patch_path.read_bytes().decode('utf-8')
    files = []
    for path in sorted(changed):
        old, new = before.get(path), contents.get(path)
        binary = path in binary_paths
        files.append({'path': path, 'before': None if old is None or binary else old.decode('utf-8'),
                      'after': None if new is None or binary else new.decode('utf-8'), 'binary': binary,
                      'before_hash': None if old is None else _hash(old), 'hash': None if new is None else _hash(new),
                      'mode': modes.get(path), 'before_mode': before_modes.get(path)})
    return CandidatePatch(base_snapshot=snapshot_id, diff=diff, patch_hash=_hash(diff.encode()), files=files, worktree=str(destination))
