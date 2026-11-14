"""Versioned team knowledge; discussion is never implicitly promoted into policy."""
import fnmatch
import json
import re
import uuid
from urllib.parse import urlparse
from . import db
from .workflow import Conflict

STATUSES = {'observed', 'inferred', 'team-approved', 'rejected', 'stale', 'conflicting'}
CATEGORIES = {'architecture', 'convention', 'constraint', 'product', 'decision'}
ACTIVE = {'Proposed', 'Awaiting approval', 'Queued', 'Investigating', 'Needs input', 'Implementing', 'Verifying', 'Ready for review'}


def _scoped(scope, paths):
    return scope in {'*', 'repository', 'global'} or any(fnmatch.fnmatchcase(path, scope) or path.startswith(scope.rstrip('/') + '/') for path in paths)


def _evidence(repo, items):
    if isinstance(items, str):
        items = [items]
    if not isinstance(items, list) or len(items) > 100:
        raise ValueError('Evidence must contain at most 100 sources')
    files = {f['path']: f for f in repo['index']['files'] if f.get('status') == 'indexed'}
    result = []
    for item in items:
        if isinstance(item, str):
            if not item.strip():
                continue
            cited=re.fullmatch(r'(.+):(\d+)(?:-(\d+))?',item)
            if cited and cited[1] in files:
                item={'path':cited[1],'start_line':int(cited[2]),'end_line':int(cited[3] or cited[2])}
            elif item in files:
                item = {'path': item}
            else:
                parsed = urlparse(item)
                if parsed.scheme and parsed.scheme not in {'http', 'https', 'fixture'}:
                    raise ValueError('Evidence URL must be HTTP(S) or labelled fixture')
                if parsed.username or parsed.password:
                    raise ValueError('Evidence URL cannot contain credentials')
                result.append({'source': item[:4000], 'kind': 'external' if parsed.scheme else 'team-attribution'})
                continue
        if not isinstance(item, dict):
            raise ValueError('Invalid knowledge evidence')
        path = item.get('path')
        if not path:
            if not isinstance(item.get('source'), str):
                raise ValueError('Evidence needs a path or source')
            result.extend(_evidence(repo, [item['source']]))
            continue
        file = files.get(path)
        if file is None:
            raise ValueError('Evidence path is not indexed in this repository')
        count = max(1, len(file['content'].splitlines()))
        start, end = item.get('start_line', 1), item.get('end_line', count)
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= count:
            raise ValueError('Evidence range does not resolve to this snapshot')
        result.append({'path': path, 'start_line': start, 'end_line': end,
                       'snapshot': repo['snapshot'], 'hash': file['hash'], 'kind': 'repository'})
    return result


def _validate(entry, repo):
    for field, maximum in [('title', 200), ('content', 20000), ('scope', 500)]:
        value = entry.get(field, '*' if field == 'scope' else '')
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError(f'{field} needs 1–{maximum} characters')
        entry[field] = value.strip()
    if entry.get('status', 'observed') not in STATUSES:
        raise ValueError('Invalid knowledge status')
    if entry.get('category', 'decision') not in CATEGORIES:
        raise ValueError('Invalid knowledge category')
    entry['evidence'] = _evidence(repo, entry.get('evidence', []))
    constraint = entry.get('constraint')
    if constraint is not None and (not isinstance(constraint, dict) or set(constraint) != {'key', 'value'} or not isinstance(constraint['key'], str) or len(json.dumps(constraint)) > 4000):
        raise ValueError('Constraint needs a key and bounded JSON value')
    return entry


def _save(con, entry):
    current = db.put_in(con, 'knowledge', entry)
    history_id = f"{current['id']}@{current.get('version', 1)}"
    if db.get_in(con, 'knowledge_versions', history_id) is None:
        db.put_in(con, 'knowledge_versions', {**current, 'entry_id': current['id'], 'id': history_id})
    return current


def _invalidate(con, repo_id, scopes):
    for row in con.execute("SELECT data FROM records WHERE kind='jobs'").fetchall():
        job = json.loads(row[0])
        paths = job.get('affected_areas', [])
        if job.get('repo_id') == repo_id and job.get('status') in ACTIVE and (not paths or any(_scoped(scope, paths) for scope in scopes)):
            db.put_in(con, 'jobs', {**job, 'status': 'Failed', 'approval': None, 'lease_token': None,
                                  'lease_until': 0, 'error': 'Applicable approved knowledge changed; retry with current decisions.'})


def create(body):
    repo = db.get('repositories', body.get('repo_id', ''))
    if not repo:
        raise ValueError('Repository not found')
    entry = _validate({'id': str(uuid.uuid4()), 'repo_id': repo['id'], 'title': body.get('title'),
                       'content': body.get('content'), 'scope': body.get('scope', '*'),
                       'category': body.get('category', 'decision'), 'status': body.get('status', 'observed'),
                       'evidence': body.get('evidence', []), 'constraint': body.get('constraint'),
                       'version': 1, 'last_validated_snapshot': repo['snapshot'], 'author': 'Alex · demo member'}, repo)
    if body.get('promoted_from'):
        entry['promoted_from'] = body['promoted_from']
    entry['approval_state'] = 'approved' if entry['status'] == 'team-approved' else 'pending'
    def save(con):
        result = _save(con, entry)
        if entry['status'] == 'team-approved':
            _invalidate(con, repo['id'], [entry['scope']])
        db.put_in(con, 'events', {'message': 'Knowledge created: ' + entry['title'], 'repo_id': repo['id'], 'author': entry['author']})
        return result
    return db.atomic(save)


def history(entry_id):
    return sorted([r for r in db.list_records('knowledge_versions') if r.get('entry_id') == entry_id], key=lambda r: r['version'])


def revise(entry_id, changes, expected_version=None, expected_snapshot=None):
    def save(con):
        previous = db.get_in(con, 'knowledge', entry_id)
        if not previous:
            raise LookupError('Knowledge entry not found')
        if expected_version is not None and previous.get('version', 1) != expected_version:
            raise Conflict('Knowledge changed; reload before editing')
        repo = db.get_in(con, 'repositories', previous['repo_id'])
        if changes.get('status') == 'team-approved':
            if expected_version is None or expected_snapshot != repo['snapshot']:
                raise Conflict('Approval requires the exact reviewed version and repository snapshot')
        allowed = {'title', 'content', 'scope', 'evidence', 'status', 'category', 'constraint'}
        entry = {**previous, **{key: value for key, value in changes.items() if key in allowed}}
        content_changed = any(entry.get(k) != previous.get(k) for k in ('title', 'content', 'scope', 'evidence', 'constraint'))
        if content_changed and previous.get('status') == 'team-approved':
            entry['status'] = 'observed'
        _validate(entry, repo)
        # Preserve legacy entries on their first edit as well as new entries.
        _save(con, previous)
        entry.update(version=previous.get('version', 1) + 1, last_validated_snapshot=repo['snapshot'], author='Alex · demo member',
                     approval_state='approved' if entry['status'] == 'team-approved' else 'pending')
        result = _save(con, entry)
        if previous.get('status') == 'team-approved' or entry['status'] == 'team-approved':
            _invalidate(con, entry['repo_id'], [previous.get('scope', '*'), entry['scope']])
        db.put_in(con, 'events', {'message': 'Knowledge revised: ' + entry['title'], 'repo_id': entry['repo_id'], 'author': entry['author']})
        return result
    return db.atomic(save)


def approve(entry_id, version, snapshot_id):
    return revise(entry_id, {'status': 'team-approved'}, version, snapshot_id)


def for_paths(repo_id, snapshot_id, paths):
    repo = db.get('repositories', repo_id)
    if not repo or repo['snapshot'] != snapshot_id:
        return []
    return [k for k in db.list_records('knowledge') if k.get('repo_id') == repo_id and k.get('status') == 'team-approved'
            and _scoped(k.get('scope', '*'), paths)]


def conflicts(repo_id, paths):
    entries = [k for k in db.list_records('knowledge') if k.get('repo_id') == repo_id and k.get('status') == 'team-approved'
               and k.get('constraint') and _scoped(k.get('scope', '*'), paths)]
    result = []
    for i, one in enumerate(entries):
        for two in entries[i + 1:]:
            overlap = any(_scoped(one.get('scope', '*'), [path]) and _scoped(two.get('scope', '*'), [path]) for path in paths)
            if overlap and one['constraint']['key'] == two['constraint']['key'] and one['constraint']['value'] != two['constraint']['value']:
                result.append({'key': one['constraint']['key'], 'entries': [one['id'], two['id']],
                               'kind': 'explicit conflicting constraints', 'requires_decision': True})
    return result


def invalidate_snapshot(repo_id, old_index, current_index, con=None, changed_paths=None):
    old = {f['path']: f.get('hash') for f in old_index['files'] if f.get('status') == 'indexed'}
    current = {f['path']: f.get('hash') for f in current_index['files'] if f.get('status') == 'indexed'}
    changed = changed_paths if changed_paths is not None else [path for path in old.keys() | current.keys() if old.get(path) != current.get(path)]
    def invalidate(con):
        for row in con.execute("SELECT data FROM records WHERE kind='knowledge'").fetchall():
            entry = json.loads(row[0])
            if entry.get('repo_id') != repo_id or entry.get('status') in {'rejected', 'stale'}:
                continue
            evidence = entry.get('evidence', [])
            if isinstance(evidence, str):
                evidence = [evidence]
            cited = {e.get('path') if isinstance(e, dict) else e.split(':', 1)[0] for e in evidence}
            affected = bool(cited.intersection(changed)) or (bool(changed) and _scoped(entry.get('scope', '*'), changed)
                and (entry.get('scope', '*') not in {'*', 'repository', 'global'} or not cited))
            if affected:
                _save(con, entry)
                _save(con, {**entry, 'status': 'stale', 'approval_state': 'pending', 'version': entry.get('version', 1) + 1,
                            'stale_reason': 'A scoped file or cited evidence changed'})
    return invalidate(con) if con is not None else db.atomic(invalidate)


def promote(job_id, discussion_id):
    job = db.get('jobs', job_id)
    note = db.get('notes', discussion_id)
    if not job or not note or note.get('job_id') != job_id:
        raise ValueError('Discussion does not belong to this job')
    return create({'repo_id': job['repo_id'], 'title': 'Proposed decision from job discussion', 'content': note['text'],
                   'status': 'observed', 'promoted_from': discussion_id, 'scope': '*', 'evidence': ['Explicit promotion by Alex · demo member']})
