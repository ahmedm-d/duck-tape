"""Private repository profile and local applicability matching.

Both objects are computed locally from an immutable snapshot index and a public
catalogue entry; nothing here uploads source, dependency lists or decisions.
A version match is affected-version applicability, never proof of exploitability.
Shapes follow docs/hackathon/KNOWLEDGE_CONTRACT.md.
"""
import hashlib
import json
import re
from pathlib import PurePosixPath

from . import signals

PROFILE_VERSION = 1
ECOSYSTEMS = {'pypi': 'PyPI', 'npm': 'npm'}
CAPABILITIES = ['package-lock.json v2/v3', 'pinned requirements.txt/requirements.lock', 'uv.lock registry packages']
KINDS = {
    'vulnerability': ('security', 'security', 'high'),
    'improvement': ('analysis', 'refactor', 'medium'),
    'framework_update': ('research', 'refactor', 'medium'),
    'research_update': ('research', 'performance', 'medium'),
}
MANIFEST_ECOSYSTEMS = {
    'package.json': 'npm', 'package-lock.json': 'npm', 'yarn.lock': 'npm', 'pnpm-lock.yaml': 'npm',
    'pyproject.toml': 'pypi', 'poetry.lock': 'pypi', 'uv.lock': 'pypi',
    'requirements.txt': 'pypi', 'requirements.lock': 'pypi',
    'Cargo.lock': 'crates.io', 'go.sum': 'go',
}
VERSION_LIMIT = 'A version match establishes affected-version applicability, not exploitability.'
HYPOTHESIS_LIMIT = 'This is an improvement hypothesis; a gain needs measured before/after checks.'


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def _name(ecosystem: str, name: str) -> str:
    return re.sub(r'[-_.]+', '-', name).lower() if ecosystem == 'pypi' else name


def _locate(path: str, content: str, name: str, version: str):
    """Return the 1-based line range that pins name to version, or None."""
    quoted, exact = re.escape(name), re.escape(version)
    if path.endswith('package-lock.json'):
        first, second, window = rf'node_modules/{quoted}"\s*:', rf'"version"\s*:\s*"{exact}"', 8
    elif path.endswith('uv.lock'):
        first, second, window = rf'^\s*name\s*=\s*"{quoted}"', rf'^\s*version\s*=\s*"{exact}"', 4
    else:
        first, second, window = rf'^\s*{quoted}=={exact}(?![\w.+!-])', None, 1
    lines = content.splitlines()
    for number, line in enumerate(lines):
        if not re.search(first, line):
            continue
        if second is None:
            return number + 1, number + 1
        for offset, following in enumerate(lines[number:number + window]):
            if re.search(second, following):
                return number + 1, number + offset + 1
    return None


def build_profile(index: dict, *, repo_id: str | None = None, snapshot_id: str | None = None) -> dict:
    """Bind signals.dependencies() output to one snapshot and its manifest sources."""
    repo_id = repo_id or index.get('repo_id')
    snapshot_id = snapshot_id or index.get('source_snapshot_id') or index.get('snapshot')
    if not repo_id or not snapshot_id:
        raise ValueError('A repository profile needs a repository and an immutable snapshot identity')
    files = {item['path']: item for item in index['files'] if item.get('status') == 'indexed'}
    found, raw_gaps = signals.dependencies(index)
    packages = []
    for record in sorted(found, key=lambda item: (item['ecosystem'], item['name'], item['version'], item['path'])):
        content = files[record['path']].get('content', '')
        located = _locate(record['path'], content, record['name'], record['version'])
        packages.append({**record,
                         'manifest_hash': files[record['path']].get('hash') or hashlib.sha256(content.encode()).hexdigest(),
                         'start_line': located[0] if located else None, 'end_line': located[1] if located else None})
    gaps = []
    for gap in raw_gaps:
        path = gap.split(': ', 1)[0]
        gaps.append({'path': path if path in files else None,
                     'ecosystem': MANIFEST_ECOSYSTEMS.get(PurePosixPath(path).name) if path in files else None,
                     'detail': gap})
    body = {'kind': 'repository_profile', 'version': PROFILE_VERSION, 'repo_id': repo_id, 'snapshot_id': snapshot_id,
            'index_snapshot': index.get('snapshot'), 'packages': packages, 'gaps': gaps, 'capabilities': CAPABILITIES}
    return {**body, 'profile_hash': _hash(body)}


def profile_for(repo_id: str, snapshot_id: str) -> dict:
    """Profile a stored snapshot; rejects snapshots that belong to another repository."""
    from . import retrieval
    return build_profile(retrieval.load_index(repo_id, snapshot_id), repo_id=repo_id, snapshot_id=snapshot_id)


def _release(value):
    match = re.fullmatch(r'v?(\d+(?:\.\d+)*)', str(value).strip())
    if not match:
        return None
    parts = [int(part) for part in match[1].split('.')]
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _affected(version: str, rule: dict):
    """True/False when the rule decides this version; None when it cannot be compared."""
    versions, ranges = rule.get('versions') or [], rule.get('ranges') or []
    if version in versions:
        return True
    if not ranges:
        return False if versions else None
    current, undecided = _release(version), False
    if current is None:
        return None
    for item in ranges:
        bounds = {key: _release(item[key]) for key in ('introduced', 'fixed', 'last_affected') if item.get(key) is not None}
        if None in bounds.values() or not bounds:
            undecided = True
            continue
        if (bounds.get('introduced', ()) <= current
                and ('fixed' not in bounds or current < bounds['fixed'])
                and ('last_affected' not in bounds or current <= bounds['last_affected'])):
            return True
    return None if undecided else False


def _entry(entry: dict) -> dict:
    """Validate the fields matching depends on. Catalogue content is untrusted public data."""
    if not isinstance(entry, dict):
        raise ValueError('Global entry must be an object')
    for field in ('id', 'content_hash', 'source_url'):
        if not isinstance(entry.get(field), str) or not entry[field] or len(entry[field]) > 2000:
            raise ValueError(f'Global entry needs a bounded string {field}')
    if entry.get('kind') not in KINDS:
        raise ValueError('Global entry kind is not supported')
    if type(entry.get('revision')) is not int or entry['revision'] < 1:
        raise ValueError('Global entry needs a positive integer material revision')
    rules = entry.get('affected') or []
    if not isinstance(rules, list) or len(rules) > 200:
        raise ValueError('Global entry affected rules must be a bounded list')
    for rule in rules:
        if (not isinstance(rule, dict) or not isinstance(rule.get('ecosystem'), str) or not isinstance(rule.get('package'), str)
                or not all(isinstance(item, str) for item in rule.get('versions') or [])
                or not all(isinstance(item, dict) for item in rule.get('ranges') or [])):
            raise ValueError('Global entry has a malformed affected rule')
    return entry


def match(profile: dict, entry: dict) -> dict:
    """Decide matched / not_matched / unknown for one public entry against one private profile."""
    entry = _entry(entry)
    evidence, unknowns = [], []
    rules = entry.get('affected') or []
    if not rules:
        unknowns.append('The entry has no affected package/version rules; code-evidence matching is not implemented')
    for rule in rules:
        ecosystem = rule['ecosystem'].lower()
        if ecosystem not in ECOSYSTEMS:
            unknowns.append(f'{rule["ecosystem"]} dependency resolution is unsupported; presence of {rule["package"]} is unknown')
            continue
        wanted = _name(ecosystem, rule['package'])
        present = [item for item in profile['packages']
                   if item['ecosystem'].lower() == ecosystem and _name(ecosystem, item['name']) == wanted]
        for package in present:
            verdict = _affected(package['version'], rule)
            if verdict:
                evidence.append({**package, 'fixed_versions': sorted({item['fixed'] for item in rule.get('ranges') or [] if item.get('fixed')})})
            elif verdict is None:
                unknowns.append(f'{package["name"]} {package["version"]} in {package["path"]} cannot be compared with the affected-version rules')
        blocking = [gap for gap in profile['gaps'] if gap['ecosystem'] in (None, ecosystem)]
        if not present and blocking:
            unknowns.append(f'{rule["package"]} was not resolved, and {len(blocking)} unresolved {ECOSYSTEMS[ecosystem]} manifest gap(s) leave its presence unknown')
    status = 'matched' if evidence else 'unknown' if unknowns else 'not_matched'
    vulnerability = entry['kind'] == 'vulnerability'
    entry_status = entry.get('status', 'active')
    limitations = [VERSION_LIMIT if vulnerability else HYPOTHESIS_LIMIT, *unknowns]
    limitations += [f'Source limitation: {str(item)[:300]}' for item in (entry.get('limitations') or [])[:8]]
    if entry_status != 'active':
        limitations.append(f'Catalogue entry is {entry_status}; withheld from new proposals until revalidated')
    limitations += [f'{item["path"]}: pinned line for {item["name"]} was not located; cite the whole manifest'
                    for item in evidence if item['start_line'] is None]
    pins = ', '.join(f'{item["name"]} {item["version"]} ({item["path"]})' for item in evidence)
    rationale = {'matched': f'Snapshot {profile["snapshot_id"]} resolves {pins}, inside the affected versions of {entry["id"]} revision {entry["revision"]}.',
                 'not_matched': f'No resolved dependency in snapshot {profile["snapshot_id"]} is inside the affected versions of {entry["id"]}.',
                 'unknown': f'Applicability of {entry["id"]} to snapshot {profile["snapshot_id"]} could not be established from resolved evidence.'}[status]
    criteria = []
    for item in evidence:
        fixed = f' (known fixed: {", ".join(item["fixed_versions"])})' if item['fixed_versions'] else ''
        criteria.append(f'{item["path"]} resolves {item["name"]} to a version outside the affected rules of {entry["id"]}{fixed}')
    if evidence:
        criteria.append('Existing checks still pass on the candidate' if vulnerability
                        else 'A measured before/after comparison supports the claimed improvement')
    return {'kind': 'applicability_match', 'version': PROFILE_VERSION, 'status': status,
            'repo_id': profile['repo_id'], 'snapshot_id': profile['snapshot_id'], 'profile_hash': profile['profile_hash'],
            'entry_id': entry['id'], 'entry_revision': entry['revision'], 'entry_hash': entry['content_hash'],
            'entry_kind': entry['kind'], 'entry_status': entry_status, 'evidence': evidence,
            'citations': [{'path': item['path'], 'start_line': item['start_line'], 'end_line': item['end_line'], 'hash': item['manifest_hash']}
                          for item in evidence if item['start_line'] is not None],
            'rationale': rationale, 'acceptance_criteria': criteria, 'limitations': limitations,
            # Stable across rescans and retrieval timestamps; changes with the pin or the material catalogue revision.
            'match_key': _hash([profile['repo_id'], entry['id'], entry['revision'], entry['content_hash'],
                                sorted((item['ecosystem'], item['name'], item['version']) for item in evidence)])}


def intake_payloads(found: dict, entry: dict) -> list[dict]:
    """Map a current match to existing POST /api/intake bodies, one per pinned package.

    The description carries the catalogue revision and content hash, so an unchanged
    rescan keeps the existing intake fingerprint and a material revision changes it.
    """
    entry = _entry(entry)
    if (found.get('entry_id'), found.get('entry_revision'), found.get('entry_hash')) != (entry['id'], entry['revision'], entry['content_hash']):
        raise ValueError('Match is bound to a different catalogue revision; re-assess applicability')
    if found['status'] != 'matched':
        raise ValueError(f'Only a matched entry becomes a proposal; this match is {found["status"]}')
    if found['entry_status'] != 'active':
        raise ValueError(f'Catalogue entry is {found["entry_status"]}; withheld until revalidated')
    source, job_type, priority = KINDS[entry['kind']]
    summary = ' '.join(str(entry.get('summary') or entry.get('title') or '').split())[:500]
    note = VERSION_LIMIT if entry['kind'] == 'vulnerability' else HYPOTHESIS_LIMIT
    payloads = []
    for item in found['evidence']:
        fixed = f' Known fixed versions: {", ".join(item["fixed_versions"])}.' if item['fixed_versions'] else ''
        payloads.append({
            'repo_id': found['repo_id'], 'source': source, 'type': job_type, 'priority': priority,
            'title': f'{item["name"]}: {entry["id"]}'[:200],
            'description': (f'Catalogue match: {item["name"]} {item["version"]} in {item["path"]} is inside the affected versions of '
                            f'{entry["id"]} (catalogue revision {entry["revision"]}, content {entry["content_hash"][:16]}). '
                            f'{summary}{"." if summary and not summary.endswith(".") else ""}{fixed} {note}'),
            'source_url': entry['source_url'],
            'source_key': f'{entry["id"]}:{item["name"]}:{item["version"]}'[:512],
        })
    return payloads
