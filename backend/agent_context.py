"""Bounded graph context for agents: `graph_context v1` (docs/KNOWLEDGE_GRAPH.md).

Built locally from one immutable snapshot; nothing here is uploaded. Every
nullable field means *unknown*, never false. Reachability is deliberately
narrow: `sink_reachable` is True only for a statically resolved call to a
symbol the public entry names, and otherwise null. A missing direct import is
reported as `directly_imported: false`, which does not make a transitive
dependency unreachable.
"""
import hashlib
import json
import os
import re
from collections import Counter

from . import db, graphify_graph, retrieval
from .symbols import JS_SUFFIXES

SCHEMA = 'graph_context v1'
STRUCTURAL = {'imports', 'imports_from', 'calls', 'tests', 'reexports', 'inherits', 'uses', 'references', 'mixes_in'}
FILE_EDGES = {'imports', 'calls', 'tests', 'reexports'}
# PyPI distributions whose import name differs from the normalised project name.
PYPI_MODULES = {
    'pyyaml': ['yaml'], 'beautifulsoup4': ['bs4'], 'pillow': ['PIL'], 'python-dateutil': ['dateutil'],
    'scikit-learn': ['sklearn'], 'opencv-python': ['cv2'], 'opencv-python-headless': ['cv2'], 'pyjwt': ['jwt'],
    'protobuf': ['google.protobuf'], 'pymongo': ['pymongo', 'bson', 'gridfs'], 'attrs': ['attr', 'attrs'],
    'pycryptodome': ['Crypto'], 'pyopenssl': ['OpenSSL'], 'python-multipart': ['multipart'], 'msgpack-python': ['msgpack'],
    'python-jose': ['jose'], 'pysaml2': ['saml2'], 'django-cors-headers': ['corsheaders'],
}
DYNAMIC_PY = {'importlib.import_module', 'import_module', '__import__'}
EVIDENCE_LIMIT = 10


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode())


def signature(content: str, start_line: int, path: str) -> str | None:
    """The declaration text at `start_line` (multi-line headers joined), as untrusted source text."""
    lines = content.splitlines()
    if not 1 <= start_line <= len(lines):
        return None
    python, parts = path.endswith('.py'), []
    for line in lines[start_line - 1:start_line + 7]:
        parts.append(line.strip())
        text = ' '.join(parts)
        if python and text.endswith(':') and text.count('(') <= text.count(')'):
            break
        if not python and ('{' in line or text.endswith((';', '=>'))):
            break
    text = ' '.join(parts)
    if not python:
        text = text.split('{', 1)[0].strip()
    return ' '.join(text.split())[:240] or None


def modules_for(ecosystem: str, package: str) -> list[str]:
    """Static import names to look for; PyPI names are normalised and mapped where the import name differs."""
    if ecosystem.lower() == 'npm':
        return [package]
    name = re.sub(r'[-_.]+', '-', package.lower())
    return list(PYPI_MODULES.get(name) or [name.replace('-', '_')])


def package_usage(index: dict, ecosystem: str, package: str, affected_symbols=()) -> dict:
    """Static import/call evidence for one dependency in one snapshot index.

    `directly_imported` is True/False/None. `sink_reachable` is True only when a
    resolved static call matches an affected symbol from the public entry, else None.
    """
    ecosystem = ecosystem.lower()
    if ecosystem not in ('pypi', 'npm'):
        return {'package': package, 'modules_checked': [], 'directly_imported': None, 'imports': [], 'calls': [], 'sink_calls': [],
                'sink_reachable': None, 'basis': f'{ecosystem} import analysis is unsupported'}
    python = ecosystem == 'pypi'
    modules = modules_for(ecosystem, package)
    fold = str.lower if python else (lambda value: value)
    separator = '.' if python else '/'

    def owner(module):
        module = fold(module)
        return next((name for name in modules if module == fold(name) or module.startswith(fold(name) + separator)), None)

    imports, calls, dynamic, parse_gaps = [], [], 0, 0
    suffixes = ('.py',) if python else JS_SUFFIXES
    for record in index.get('files', []):
        path = record.get('path', '')
        if record.get('status') != 'indexed' or not path.endswith(suffixes):
            continue
        local = {}
        for imp in record.get('imports', []):
            module = str(imp.get('module') or '')
            if module.startswith('.') or not owner(module):
                continue
            imports.append({'path': path, 'line': imp.get('line'), 'module': module})
            for binding in imp.get('bindings') or []:
                if binding.get('module_binding'):
                    local[binding['local']] = binding['local'] if python and binding.get('unaliased_module') else module
                elif binding.get('imported') == 'default':
                    local[binding['local']] = module
                else:
                    local[binding['local']] = module + '.' + binding['imported']
        for call in record.get('calls', []):
            name = str(call.get('name') or '')
            if call.get('dynamic') or (python and name in DYNAMIC_PY) or (not python and name == 'require' and not call.get('route')):
                dynamic += 1
            head, _, rest = name.partition('.')
            if head in local and not call.get('shadowed'):
                calls.append({'path': path, 'line': call.get('line'), 'call': local[head] + ('.' + rest if rest else '')})
    for gap in index.get('gaps', []):
        if str(gap).split(':', 1)[0].endswith(suffixes):
            parse_gaps += 1
    wanted = list(affected_symbols or [])
    sink_calls = [item for item in calls if any(item['call'] == symbol or item['call'].endswith('.' + symbol) for symbol in wanted)]
    if imports:
        directly_imported, basis = True, f'static import of {", ".join(sorted({item["module"] for item in imports})[:4])} found'
    elif dynamic or parse_gaps:
        directly_imported = None
        basis = f'no static import of {", ".join(modules)}; {dynamic} dynamic import site(s) and {parse_gaps} unparsed file(s) leave usage unknown'
    else:
        directly_imported = False
        basis = (f'no static import of {", ".join(modules)} in parsed files; the package may still run as a transitive dependency, '
                 'so this is not evidence of unreachability')
    if sink_calls:
        reach = 'a resolved static call matches an affected symbol from the public entry'
    elif not wanted:
        reach = 'the entry names no affected symbols; reachability not assessed'
    else:
        reach = 'no resolved static call to an affected symbol; dynamic dispatch and transitive use are not analysed, so not shown reachable'
    return {'package': package, 'modules_checked': modules, 'directly_imported': directly_imported,
            'imports': imports[:EVIDENCE_LIMIT], 'calls': calls[:EVIDENCE_LIMIT], 'sink_calls': sink_calls[:EVIDENCE_LIMIT],
            'sink_reachable': True if sink_calls else None, 'basis': basis + '; ' + reach}


def _catalogue_match(index: dict, found: dict, entry_id: str, revision, kind) -> dict:
    usage = [package_usage(index, item['ecosystem'], item['name'], found.get('affected_symbols') or [])
             for item in found.get('evidence') or [] if isinstance(item, dict) and item.get('name') and item.get('ecosystem')]
    imported = [item['directly_imported'] for item in usage]
    return {'entry_id': entry_id, 'entry_revision': revision, 'kind': kind, 'status': found.get('status'),
            'citations': list(found.get('citations') or [])[:6], 'limitations': [str(item)[:300] for item in (found.get('limitations') or [])[:6]],
            'cwe': list(found.get('cwe_ids') or []) or None,
            'directly_imported': True if True in imported else None if None in imported or not usage else False,
            'sink_reachable': True if any(item['sink_reachable'] for item in usage) else None,
            'usage': usage}


def _blast(index, graph, focal_paths, focal_symbols):
    edges = [edge for edge in index.get('edges', []) if edge.get('kind') in FILE_EDGES]
    category = {item['path']: item.get('category') for item in index.get('files', []) if item.get('path')}
    dependents = sorted({edge['source'] for edge in edges if edge.get('target') in focal_paths and edge.get('source') not in focal_paths})
    tests = sorted({edge['source'] for edge in edges if edge.get('target') in focal_paths and (edge.get('kind') == 'tests' or category.get(edge['source']) == 'tests')}
                   | {path for path in focal_paths if category.get(path) == 'tests'})
    communities = None
    if graph['engine'] == 'graphify':
        touched = {node['community'] for node in graph['nodes'] if node.get('path') in set(focal_paths) | set(dependents) and type(node.get('community')) is int}
        communities = len(touched) or None
    high = any(item.get('high_degree') for item in focal_symbols)
    risk = 'high' if high or len(dependents) >= 5 or (communities or 0) > 2 else 'medium' if len(dependents) >= 2 else 'low'
    basis = (f'Heuristic from static edges in this snapshot: {len(dependents)} dependent file(s), high-degree focal symbol: {"yes" if high else "no"}'
             + (f', {communities} community(ies) touched' if communities else '') + '. Dynamic dispatch is not counted.')
    return {'dependent_files': dependents[:20], 'communities_touched': communities, 'linked_test_files': tests[:20],
            'structural_risk': risk, 'basis': basis}


def agent_graph_context(repo_id: str, snapshot_id: str, query: str, budget_tokens: int = 3500, *, job: dict | None = None,
                        entries: list | None = None, engine: str | None = None, cancel=None) -> dict:
    """Return `graph_context v1` for one query, bounded by `budget_tokens` (UTF-8 bytes, like retrieval)."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError('A query is required')
    if not 1024 <= budget_tokens <= 64000:
        raise ValueError('budget_tokens must be between 1024 and 64000')
    index = retrieval.load_index(repo_id, snapshot_id)
    gaps = []
    knowledge = [record for record in db.list_records('knowledge') if record.get('repo_id') == repo_id and record.get('status') == 'team-approved']
    try:
        context = retrieval.retrieve(repo_id, snapshot_id, query[:4000], max(budget_tokens * 4, 8000), knowledge=knowledge, limit=8, cancel=cancel)
    except ValueError as exc:
        context = {'items': [], 'knowledge': [], 'missing': [f'Retrieval unavailable: {exc}']}
    gaps += [str(item)[:240] for item in context.get('missing', [])[:6]]

    engine = engine or os.getenv('STEWARD_GRAPH_ENGINE', 'builtin')
    if engine not in ('builtin', 'graphify'):
        raise ValueError('engine must be builtin or graphify')
    graph = graphify_graph.knowledge_graph(repo_id, snapshot_id, 'auto') if engine == 'graphify' else graphify_graph.builtin(repo_id, snapshot_id, index)
    gaps += [str(item)[:240] for item in graph.get('gaps', [])[:4]]
    nodes = {node['id']: node for node in graph['nodes']}
    by_location = {(node.get('path'), node.get('line')): node for node in graph['nodes'] if node.get('kind') != 'file'}

    records = {item['path']: item for item in index.get('files', []) if item.get('status') == 'indexed'}
    callers = Counter((edge.get('target'), (edge.get('target_evidence') or {}).get('start_line'))
                      for edge in index.get('edges', []) if edge.get('kind') == 'calls')
    ranked = sorted(callers.values())
    threshold = max(3, ranked[int(len(ranked) * 0.9)] if ranked else 3)
    focal_paths, focal = [], []
    for item in context.get('items', []):
        if item['path'] not in focal_paths:
            focal_paths.append(item['path'])
        record = records.get(item['path'], {})
        for symbol in record.get('symbols', []):
            if not (item['start_line'] <= symbol['start_line'] <= item['end_line']) or len(focal) >= 12:
                continue
            node = by_location.get((item['path'], symbol['start_line']))
            degree = callers.get((item['path'], symbol['start_line']), 0)
            focal.append({'node_id': node['id'] if node else f'symbol:{item["path"]}:{symbol.get("qualified_name") or symbol["name"]}:{symbol["start_line"]}',
                          'path': item['path'], 'start_line': symbol['start_line'], 'end_line': symbol.get('end_line'),
                          'kind': 'class' if symbol['kind'] in ('ClassDef', 'class') else 'callable',
                          'label': symbol.get('qualified_name') or symbol['name'],
                          'signature': signature(record.get('content', ''), symbol['start_line'], item['path']),
                          'community': node.get('community') if node else None, 'degree': degree, 'high_degree': degree >= threshold})
    focal_paths = focal_paths[:6]
    if not focal:
        gaps.append('No parsed symbol falls inside the retrieved ranges; focal_symbols is empty')

    edges = []
    for edge in graph['edges']:
        if edge.get('relation') not in STRUCTURAL:
            continue
        ends = {nodes.get(edge['source'], {}).get('path'), nodes.get(edge['target'], {}).get('path')}
        if ends & set(focal_paths):
            edges.append({'source': edge['source'], 'target': edge['target'], 'relation': edge['relation'],
                          'confidence': 'EXTRACTED' if edge.get('confidence') == 'EXTRACTED' or str(edge.get('confidence', '')).startswith('parsed') else 'INFERRED',
                          'suggestion_only': bool(edge.get('suggestion_only')), 'evidence': edge.get('evidence')})
    edges.sort(key=lambda item: (item['confidence'] != 'EXTRACTED', item['relation'], item['source'], item['target']))

    matches = []
    if job and isinstance(job.get('catalogue'), dict) and isinstance(job.get('applicability'), dict):
        if job['catalogue'].get('snapshot_id') not in (None, snapshot_id):
            gaps.append('The job match was made on another snapshot; re-assess applicability before relying on it')
        matches.append(_catalogue_match(index, job['applicability'], job['catalogue'].get('entry_id'), job['catalogue'].get('entry_revision'), job['catalogue'].get('entry_kind')))
    if entries:
        from .applicability import build_profile, match
        profile = build_profile(index, repo_id=repo_id, snapshot_id=snapshot_id)
        for entry in entries[:20]:
            found = match(profile, entry)
            matches.append(_catalogue_match(index, found, found['entry_id'], found['entry_revision'], found['entry_kind']))

    result = {
        'schema': SCHEMA,
        'scope': {'repo_id': repo_id, 'snapshot_id': snapshot_id, 'engine': graph['engine'], 'engine_version': graph.get('engine_version'),
                  'graph_hash': graph.get('graph_hash')},
        'focal_symbols': focal, 'edges': edges[:60],
        'blast_radius': _blast(index, graph, focal_paths, focal),
        'catalogue_matches': matches,
        'approved_knowledge': [{key: record.get(key) for key in ('id', 'title', 'content', 'scope', 'version')} for record in context.get('knowledge', [])],
        'gaps': list(dict.fromkeys(gaps)),
        'context_hash': '0' * 64,
    }
    if not result['blast_radius']['linked_test_files']:
        result['gaps'].append('No test file statically imports the focal files; linked_test_files is empty, not proof of no tests')
    for key in ('edges', 'focal_symbols', 'approved_knowledge'):
        trimmed = 0
        while _size(result) > budget_tokens and result[key]:
            result[key].pop()
            trimmed += 1
        if trimmed:
            result['gaps'].append(f'{trimmed} {key} item(s) trimmed to fit the {budget_tokens}-byte budget')
    for match in result['catalogue_matches']:
        while _size(result) > budget_tokens and any(item['imports'] or item['calls'] for item in match['usage']):
            for item in match['usage']:
                for field in ('calls', 'imports'):
                    if item[field]:
                        item[field].pop()
    if _size(result) > budget_tokens and len(result['gaps']) > 2:
        dropped = len(result['gaps']) - 2
        result['gaps'] = result['gaps'][:2] + [f'{dropped} further gap(s) omitted to fit the budget']
    if _size(result) > budget_tokens:
        raise ValueError('Catalogue matches and scope alone exceed the graph-context budget')
    result['context_hash'] = _hash({key: value for key, value in result.items() if key != 'context_hash'})
    return result
