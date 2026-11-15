"""Snapshot-bound repository knowledge map built with Graphify, with a labelled built-in fallback.

Graphify (https://github.com/Graphify-Labs/graphify, Apache-2.0, PyPI ``graphifyy``) is Duck Tape
infrastructure: an isolated external CLI (``uv tool install graphifyy==0.9.108 --python 3.11``),
never installed into the application venv or into imported repositories. It runs on a copy of one
immutable snapshot with ``extract --code-only`` (tree-sitter AST), an allowlisted environment with no
model keys, ``GRAPHIFY_BACKEND=none`` and its query log disabled, so code never leaves the machine.

Every node and edge is re-validated against the snapshot's indexed files; anything that cannot be
tied to an exact path and line becomes a recorded gap. Graphify INFERRED edges are suggestions, not
evidence. The result is cached in SQLite per repository, snapshot and engine version.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path, PurePosixPath

from . import db

SCHEMA_VERSION = 1
GRAPHIFY_PIN = '0.9.108'
MAX_NODES = 20000
MAX_EDGES = 60000
TIMEOUT_SECONDS = 180
ENV_ALLOWLIST = ('PATH', 'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'HOME',
                 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'PROGRAMDATA', 'NUMBER_OF_PROCESSORS', 'LANG', 'LC_ALL')
BUILTIN_RELATIONS = {'imports': 'imports', 'calls': 'calls', 'import': 'imports', 'call': 'calls'}


class GraphifyUnavailable(RuntimeError):
    """Graphify is not installed or failed; callers may fall back to the built-in graph."""


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _check(cancel):
    if cancel is not None:
        cancel.raise_if_cancelled()


def executable() -> str | None:
    configured = os.getenv('STEWARD_GRAPHIFY_BIN')
    if configured:
        return configured if Path(configured).is_file() else None
    found = shutil.which('graphify')
    if found:
        return found
    for name in ('graphify.exe', 'graphify'):
        candidate = Path.home() / '.local' / 'bin' / name
        if candidate.is_file():
            return str(candidate)
    return None


def _files(index: dict) -> dict[str, dict]:
    return {item['path']: {'hash': item.get('hash'), 'lines': len(item.get('content', '').splitlines()) or 1,
                           'category': item.get('category', 'implementation')}
            for item in index['files'] if item.get('status') == 'indexed' and isinstance(item.get('content'), str)}


def _line(location) -> int | None:
    match = re.fullmatch(r'L(\d+)(?:-L?\d+)?', str(location or '').strip())
    return int(match[1]) if match else None


def _located(files, path, line):
    """Return a normalized (path, line) only when both exist in this snapshot."""
    path = PurePosixPath(str(path or '').replace('\\', '/')).as_posix().removeprefix('./')
    if path not in files or line is None or not 1 <= line <= files[path]['lines']:
        return None
    return path, line


def _file_nodes(files):
    return [{'id': 'file:' + path, 'label': path, 'kind': 'file', 'path': path, 'line': 1, 'community': None,
             'category': meta['category'], 'hash': meta['hash']} for path, meta in sorted(files.items())]


def normalize_graphify(raw: dict, files: dict) -> tuple[list, list, list, dict]:
    """Map Graphify graph.json into Duck Tape's node/edge schema, keeping only snapshot-validated items."""
    meta = raw.get('graph') or {}
    if meta.get('schema_version') != 1 or not isinstance(raw.get('nodes'), list):
        raise GraphifyUnavailable('Unsupported Graphify graph.json schema')
    nodes, gaps, kept, external = _file_nodes(files), [], {}, 0
    for item in raw['nodes'][:MAX_NODES]:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str):
            continue
        if item.get('external') or item.get('type') == 'external':
            external += 1
            continue
        located = _located(files, item.get('source_file'), _line(item.get('source_location')))
        if not located:
            gaps.append(f'Graphify node {str(item.get("label") or item["id"])[:80]} has no validated snapshot location; omitted')
            continue
        node = {'id': 'graphify:' + item['id'], 'label': str(item.get('label') or item['id'])[:200],
                'kind': 'class' if item.get('_callable_class') else 'callable' if item.get('_callable') else 'symbol',
                'path': located[0], 'line': located[1], 'community': item.get('community') if type(item.get('community')) is int else None}
        kept[item['id']] = node
        nodes.append(node)
    edges, dropped = [], 0
    for item in (raw.get('links') or raw.get('edges') or [])[:MAX_EDGES]:
        if not isinstance(item, dict):
            continue
        source, target = kept.get(item.get('source')), kept.get(item.get('target'))
        evidence = _located(files, item.get('source_file'), _line(item.get('source_location')))
        if not source or not target or not evidence:
            dropped += 1
            continue
        confidence = 'EXTRACTED' if item.get('confidence') == 'EXTRACTED' else 'INFERRED'
        edges.append({'source': source['id'], 'target': target['id'], 'relation': str(item.get('relation') or 'related')[:40],
                      'confidence': confidence, 'score': item.get('confidence_score') if type(item.get('confidence_score')) in (int, float) else None,
                      'evidence': {'path': evidence[0], 'line': evidence[1]}, 'suggestion_only': confidence != 'EXTRACTED'})
    for node in list(kept.values()):
        edges.append({'source': 'file:' + node['path'], 'target': node['id'], 'relation': 'defines', 'confidence': 'EXTRACTED',
                      'score': 1.0, 'evidence': {'path': node['path'], 'line': node['line']}, 'suggestion_only': False})
    if external:
        gaps.append(f'{external} external/library symbols are outside the snapshot and omitted')
    if dropped:
        gaps.append(f'{dropped} Graphify edges lacked validated endpoints or evidence and were omitted')
    if len(raw['nodes']) > MAX_NODES:
        gaps.append(f'Graph truncated to {MAX_NODES} Graphify nodes')
    return nodes, edges, gaps, {'graphify_version': meta.get('graphify_version'), 'schema_version': meta.get('schema_version')}


def _run_graphify(snapshot_id: str, cancel=None) -> dict:
    from . import snapshots
    binary = executable()
    if not binary:
        raise GraphifyUnavailable('Graphify CLI not installed (uv tool install graphifyy==%s --python 3.11)' % GRAPHIFY_PIN)
    scratch = Path(db.DATA_DIR) / 'graphify' / uuid.uuid4().hex
    try:
        source, out = scratch / 'source', scratch / 'out'
        out.mkdir(parents=True)
        snapshots.materialize(snapshot_id, source)
        _check(cancel)
        environment = {key: os.environ[key] for key in ENV_ALLOWLIST if key in os.environ}
        environment.update(GRAPHIFY_BACKEND='none', GRAPHIFY_QUERY_LOG_DISABLE='1', PYTHONIOENCODING='utf-8')
        try:
            completed = subprocess.run([binary, 'extract', str(source), '--code-only', '--out', str(out)], cwd=scratch,
                                       env=environment, capture_output=True, timeout=TIMEOUT_SECONDS, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GraphifyUnavailable('Graphify extraction did not complete: ' + type(exc).__name__) from None
        _check(cancel)
        path = out / 'graphify-out' / 'graph.json'
        if completed.returncode != 0 or not path.is_file():
            raise GraphifyUnavailable(f'Graphify extraction failed with exit code {completed.returncode}')
        if path.stat().st_size > 64 * 1024 * 1024:
            raise GraphifyUnavailable('Graphify output exceeds 64 MiB')
        return json.loads(path.read_text(encoding='utf-8'))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def builtin(repo_id: str, snapshot_id: str, index: dict | None = None) -> dict:
    """The existing parser/import-graph from symbols.py, in the same schema."""
    from . import retrieval
    index = index or retrieval.load_index(repo_id, snapshot_id)
    files = _files(index)
    nodes, edges = _file_nodes(files), []
    for record in index['files']:
        if record.get('path') not in files:
            continue
        for symbol in record.get('symbols', []):
            located = _located(files, record['path'], symbol.get('start_line'))
            if located:
                node_id = f'symbol:{record["path"]}:{symbol.get("qualified_name") or symbol["name"]}:{symbol["start_line"]}'
                nodes.append({'id': node_id, 'label': symbol.get('qualified_name') or symbol['name'], 'kind': str(symbol.get('kind', 'symbol')),
                              'path': located[0], 'line': located[1], 'community': None})
                edges.append({'source': 'file:' + record['path'], 'target': node_id, 'relation': 'defines', 'confidence': 'EXTRACTED',
                              'score': 1.0, 'evidence': {'path': located[0], 'line': located[1]}, 'suggestion_only': False})
    for edge in index.get('edges', []):
        if edge.get('source') in files and edge.get('target') in files:
            line = edge.get('line') or (edge.get('evidence') or {}).get('start_line') or 1
            located = _located(files, edge['source'], line) or (edge['source'], 1)
            edges.append({'source': 'file:' + edge['source'], 'target': 'file:' + edge['target'],
                          'relation': BUILTIN_RELATIONS.get(edge.get('kind'), str(edge.get('kind') or 'related')),
                          'confidence': str(edge.get('confidence') or 'parsed'), 'score': None,
                          'evidence': {'path': located[0], 'line': located[1]}, 'suggestion_only': False})
    return _result(repo_id, snapshot_id, 'duck-tape-symbols', None, nodes, edges, list(index.get('gaps', []))[:32])


def _result(repo_id, snapshot_id, engine, version, nodes, edges, gaps):
    communities = {}
    for node in nodes:
        if node.get('community') is not None:
            communities[node['community']] = communities.get(node['community'], 0) + 1
    body = {'kind': 'knowledge_graph', 'schema_version': SCHEMA_VERSION, 'engine': engine, 'engine_version': version,
            'repo_id': repo_id, 'snapshot_id': snapshot_id, 'nodes': nodes, 'edges': edges, 'gaps': gaps,
            'communities': [{'id': key, 'size': value} for key, value in sorted(communities.items())],
            'stats': {'nodes': len(nodes), 'edges': len(edges), 'inferred_edges': sum(edge['suggestion_only'] for edge in edges)}}
    if engine == 'graphify':
        body['attribution'] = 'Graph extracted with Graphify (Apache-2.0), https://github.com/Graphify-Labs/graphify'
    return {**body, 'graph_hash': _hash(body)}


def graphify(repo_id: str, snapshot_id: str, *, cancel=None, refresh: bool = False) -> dict:
    """Build (or reuse the cached) Graphify knowledge map for one immutable snapshot."""
    from . import retrieval
    index = retrieval.load_index(repo_id, snapshot_id)
    files = _files(index)
    key = _hash(['graphify', repo_id, snapshot_id, GRAPHIFY_PIN, SCHEMA_VERSION, sorted((path, meta['hash']) for path, meta in files.items())])
    cached = None if refresh else db.get('knowledge_graphs', key)
    if cached:
        return cached['graph']
    raw = _run_graphify(snapshot_id, cancel)
    nodes, edges, gaps, meta = normalize_graphify(raw, files)
    result = _result(repo_id, snapshot_id, 'graphify', meta['graphify_version'], nodes, edges, gaps)
    db.put('knowledge_graphs', {'id': key, 'repo_id': repo_id, 'snapshot_id': snapshot_id, 'graph': result})
    return result


def knowledge_graph(repo_id: str, snapshot_id: str, engine: str = 'auto', *, cancel=None) -> dict:
    """engine: 'graphify', 'builtin', or 'auto' (Graphify, else the labelled built-in fallback)."""
    if engine not in {'auto', 'graphify', 'builtin'}:
        raise ValueError('engine must be auto, graphify or builtin')
    if engine == 'builtin':
        return builtin(repo_id, snapshot_id)
    try:
        return graphify(repo_id, snapshot_id, cancel=cancel)
    except GraphifyUnavailable as exc:
        if engine == 'graphify':
            raise
        fallback = builtin(repo_id, snapshot_id)
        fallback['gaps'] = [f'{exc}; built-in symbol graph fallback', *fallback['gaps']]
        return fallback
