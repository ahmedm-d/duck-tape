"""Explicit, opt-in mirror of the repository graph and approved knowledge into the team's private Atlas.

This is a separate consent from ``STEWARD_ATLAS_RETRIEVAL`` (hashed vectors only): enabling the mirror
sends node labels, file paths, line numbers, declaration lines and approved knowledge text to the private
database. SQLite stays authoritative; Atlas holds a read-only projection of one snapshot per repository.
Never mirrored: team notes, drafts, job data, repository profiles, file bodies, evidence text, and any
file that Duck Tape's Atlas vector upload would also exclude as secret-bearing.
"""
from contextlib import ExitStack
import hashlib
import io
import json
import os
import tokenize

from . import agent_context, data_platforms, db, graphify_graph, knowledge, repository, retrieval
from .data_platforms import DataPlatformError

FLAG = 'STEWARD_ATLAS_GRAPH_MIRROR'
COLLECTIONS = {'nodes': 'repo_nodes', 'edges': 'repo_edges', 'knowledge': 'approved_knowledge'}
BATCH = 500
MAX_KNOWLEDGE = 2000
NOT_KNOWLEDGE = {'notes', 'team-note', 'revision-request'}


class MirrorRefused(DataPlatformError):
    """The mirror is not enabled or not configured; nothing was sent."""

    code = 'atlas_graph_mirror_refused'


def _id(*parts) -> str:
    return hashlib.sha256(json.dumps(list(parts), separators=(',', ':')).encode()).hexdigest()


def _required() -> bool:
    from .atlas_mode import atlas_required
    return atlas_required()


def _config() -> dict:
    if os.getenv(FLAG) != '1' and not _required():  # Atlas-required mode needs no separate opt-in
        raise MirrorRefused(f'Atlas graph mirror is disabled; set {FLAG}=1 to opt in')
    if not data_platforms._enabled('atlas'):
        raise MirrorRefused('Atlas integration is disabled; the graph mirror needs STEWARD_ATLAS_ENABLED=1')
    try:
        config = data_platforms._atlas_config()
    except DataPlatformError:
        raise MirrorRefused('Atlas configuration is incomplete or invalid') from None
    if config['database'].casefold().endswith('_public'):
        raise MirrorRefused('The graph mirror writes only to the private database, never a public one')
    return config


def _safe_files(index: dict) -> tuple[dict, set]:
    """Same predicate as the Atlas vector upload: whole files are excluded, not redacted."""
    allowed, blocked = {}, set()
    for item in index['files']:
        path, content = item.get('path'), item.get('content')
        if not isinstance(path, str):
            continue
        if (isinstance(content, str) and not repository.unsafe_path(path) and not repository.secret_path(path)
                and not repository.SECRET.search(content) and not repository.SECRET_ASSIGNMENT.search(content)):
            allowed[path] = content
        else:
            blocked.add(path)
    return allowed, blocked


def _graph(repo_id: str, snapshot_id: str) -> dict:
    engine = os.getenv('STEWARD_GRAPH_ENGINE', 'builtin')
    if engine not in ('builtin', 'graphify'):
        raise DataPlatformError('STEWARD_GRAPH_ENGINE must be builtin or graphify')
    return graphify_graph.knowledge_graph(repo_id, snapshot_id, 'auto' if engine == 'graphify' else 'builtin')


def _declaration(content: str, start_line: int, path: str) -> str | None:
    """Return only a proven Python header; ambiguous or unsupported snippets stay local."""
    # shortcut: non-Python signatures are withheld until their parser exposes a reliable body boundary.
    lines = content.splitlines(keepends=True)
    if not path.endswith('.py') or not 1 <= start_line <= len(lines):
        return None
    lines = lines[start_line - 1:start_line + 7]
    if not lines[0].lstrip().startswith(('def ', 'async def ', 'class ')):
        return None
    depth = 0
    try:
        for token in tokenize.generate_tokens(io.StringIO(''.join(lines)).readline):
            if token.type in (tokenize.COMMENT, tokenize.ERRORTOKEN, tokenize.NEWLINE):
                return None
            if token.type != tokenize.OP:
                continue
            if token.string in '([{':
                depth += 1
            elif token.string in ')]}':
                depth -= 1
                if depth < 0:
                    return None
            elif token.string == ':' and depth == 0:
                row, column = token.end
                header = ''.join(lines[:row - 1]) + lines[row - 1][:column]
                return ' '.join(header.split())[:240]
    except (tokenize.TokenError, IndentationError):
        pass
    return None


def _documents(repo_id: str, snapshot_id: str, graph: dict, index: dict) -> tuple[dict, int]:
    base = {'repo_id': repo_id, 'snapshot_id': snapshot_id, 'engine': graph['engine'],
            'graph_hash': graph['graph_hash'], 'mirrored_at': db.now()}
    contents = _safe_files(index)[0]
    nodes = {}
    excluded = 0
    for node in graph['nodes'][:graphify_graph.MAX_NODES]:
        if node.get('path') not in contents:
            excluded += 1
            continue
        nodes[node['id']] = node
    edges = []
    for edge in graph['edges'][:graphify_graph.MAX_EDGES]:
        evidence = edge.get('evidence') or {}
        if edge['source'] not in nodes or edge['target'] not in nodes or evidence.get('path') not in contents:
            excluded += 1
            continue
        edges.append(edge)
    degree = {}
    for edge in edges:
        for end in (edge['source'], edge['target']):
            degree[end] = degree.get(end, 0) + 1
    node_docs, edge_docs = {}, {}
    for node in nodes.values():
        declaration = None if node.get('kind') == 'file' else _declaration(
            contents[node['path']], node.get('line') or 1, node['path'])
        key = _id(repo_id, snapshot_id, node['id'])
        node_docs[key] = {**base, '_id': key, 'id': node['id'], 'label': node.get('label'), 'kind': node.get('kind'),
                           'path': node['path'], 'line': node.get('line'), 'community': node.get('community'),
                           'degree': degree.get(node['id'], 0), 'signature': declaration}
    for edge in edges:
        evidence = edge['evidence']
        key = _id(repo_id, snapshot_id, edge['source'], edge['target'], edge.get('relation'), evidence.get('path'), evidence.get('line'))
        edge_docs[key] = {**base, '_id': key, 'source': edge['source'], 'target': edge['target'],
                           'relation': edge.get('relation'), 'confidence': edge.get('confidence'),
                           'suggestion_only': bool(edge.get('suggestion_only')),
                           'evidence': {'path': evidence.get('path'), 'line': evidence.get('line')}}
    return {'nodes': node_docs, 'edges': edge_docs, 'base': base}, excluded


def _knowledge_documents(repo_id: str, snapshot_id: str, base: dict, paths: list) -> tuple[dict, int]:
    conflicting = {entry for item in knowledge.conflicts(repo_id, paths) for entry in item['entries']}
    docs, excluded = {}, 0
    for record in db.list_records('knowledge'):
        if (record.get('repo_id') != repo_id or record.get('status') != 'team-approved'
                or record.get('stale') or record.get('id') in conflicting or record.get('kind') in NOT_KNOWLEDGE):
            continue
        text = f"{record.get('title', '')}\n{record.get('content', '')}"
        if repository.SECRET.search(text) or repository.SECRET_ASSIGNMENT.search(text):
            excluded += 1
            continue
        if len(docs) >= MAX_KNOWLEDGE:
            break
        key = _id(repo_id, snapshot_id, record['id'])
        docs[key] = {**base, '_id': key, 'id': record['id'], 'title': record.get('title'), 'content': record.get('content'),
                      'scope': record.get('scope', '*'), 'version': record.get('version', 1), 'updated_at': record.get('updated_at')}
    return docs, excluded


def _write(collection, documents: dict, replace_one) -> None:
    items = list(documents.values())
    for start in range(0, len(items), BATCH):
        collection.bulk_write([replace_one({'_id': item['_id']}, item, upsert=True) for item in items[start:start + BATCH]],
                              ordered=False)


def mirror(repo_id: str, snapshot_id: str) -> dict:
    """Mirror one snapshot's graph and approved knowledge; later snapshots replace earlier ones."""
    config = _config()
    try:
        from pymongo import ReplaceOne
    except ImportError:
        raise DataPlatformError('Atlas requires the optional requirements-cloud.txt dependencies') from None
    index = retrieval.load_index(repo_id, snapshot_id)
    graph = _graph(repo_id, snapshot_id)
    documents, excluded = _documents(repo_id, snapshot_id, graph, index)
    paths = sorted(path for path in _safe_files(index)[0])
    approved, withheld = _knowledge_documents(repo_id, snapshot_id, documents['base'], paths)
    groups = {'nodes': documents['nodes'], 'edges': documents['edges'], 'knowledge': approved}
    removed = 0
    with ExitStack() as stack:
        collections = {key: stack.enter_context(data_platforms._atlas_collection({**config, 'collection': name}))
                       for key, name in COLLECTIONS.items()}
        ensure_indexes(collections)
        for key, group in groups.items():
            _write(collections[key], group, ReplaceOne)
        # Reconcile only after every write succeeds, including policies revoked on the same snapshot.
        for key, collection in collections.items():
            result = collection.delete_many({'repo_id': repo_id, '_id': {'$nin': list(groups[key])}})
            removed += int(getattr(result, 'deleted_count', 0) or 0)
    return {'database': config['database'], 'collections': dict(COLLECTIONS), 'repo_id': repo_id,
            'snapshot_id': snapshot_id, 'engine': graph['engine'], 'nodes': len(documents['nodes']),
            'edges': len(documents['edges']), 'knowledge': len(approved), 'excluded': excluded + withheld,
            'removed_previous': removed}


# --- Blast radius computed in Atlas ($graphLookup over the mirrored edges) -------------------------------------------
# Reads only the repo_nodes/repo_edges projection written by mirror(); it never writes and never returns data from a
# snapshot other than the one asked for.
INDEXES = {
    'edges': [('repo_edges_target', [('repo_id', 1), ('snapshot_id', 1), ('target', 1)]),
              ('repo_edges_source', [('repo_id', 1), ('snapshot_id', 1), ('source', 1)])],
    'nodes': [('repo_nodes_path', [('repo_id', 1), ('snapshot_id', 1), ('path', 1)])],
}
MAX_START_NODES = 200
MAX_BLAST_DEPTH = 4
BLAST_MAX_TIME_MS = 10000


def ensure_indexes(collections: dict | None = None) -> dict:
    """Create the ordinary (non-search) indexes the blast-radius query needs; `create_index` is idempotent.

    `collections` maps 'nodes'/'edges' to open collections (mirror() passes its own); without it the
    collections are opened here.
    """
    with ExitStack() as stack:
        if collections is None:
            config = _read_config()
            collections = {key: stack.enter_context(data_platforms._atlas_collection({**config, 'collection': COLLECTIONS[key]}))
                           for key in INDEXES}
        for key, specs in INDEXES.items():
            for name, keys in specs:
                collections[key].create_index(keys, name=name)
    return {COLLECTIONS[key]: [name for name, _ in specs] for key, specs in INDEXES.items()}


def _read_config() -> dict:
    """Like `_config` but for reads: allowed in Atlas-required mode (or with the legacy mirror flag)."""
    from .atlas_mode import atlas_required
    if not (atlas_required() or os.getenv(FLAG) == '1'):
        raise MirrorRefused(f'Atlas graph reads are disabled; Atlas is not required and {FLAG} is not set')
    if not data_platforms._enabled('atlas'):
        raise MirrorRefused('Atlas integration is disabled; the graph needs STEWARD_ATLAS_ENABLED=1')
    try:
        config = data_platforms._atlas_config()
    except DataPlatformError:
        raise MirrorRefused('Atlas configuration is incomplete or invalid') from None
    if config['database'].casefold().endswith('_public'):
        raise MirrorRefused('The repository graph lives only in the private database, never a public one')
    return config


def _is_test_path(path: str) -> bool:
    """The same predicate as repository._category: a name containing "test" or a tests/ directory."""
    return 'test' in path.rsplit('/', 1)[-1].lower() or '/tests/' in '/' + path


def blast_radius_pipeline(repo_id: str, snapshot_id: str, start_nodes: list[str], max_depth: int = 1) -> list[dict]:
    """Aggregation over `repo_edges`: who depends on the start nodes, `max_depth` reverse hops beyond the first.

    `$match` selects the direct dependency edges into the start nodes (hop 1); `$graphLookup` then walks
    reverse edges (from each dependent's `source` to the edges whose `target` is that source). `max_depth=1`
    means one further hop, two hops in all (graphLookup `maxDepth` 0). Pure: builds the pipeline only.
    """
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or not 1 <= max_depth <= MAX_BLAST_DEPTH:
        raise ValueError(f'max_depth must be an integer from 1 to {MAX_BLAST_DEPTH}')
    starts = sorted({node for node in start_nodes if isinstance(node, str) and node})
    if not starts or len(starts) > MAX_START_NODES:
        raise ValueError(f'start_nodes must hold 1 to {MAX_START_NODES} node ids')
    scope = {'repo_id': repo_id, 'snapshot_id': snapshot_id, 'relation': {'$in': sorted(agent_context.FILE_EDGES)}}
    return [
        {'$match': {**scope, 'target': {'$in': starts}}},
        {'$graphLookup': {'from': COLLECTIONS['edges'], 'startWith': '$source', 'connectFromField': 'source',
                          'connectToField': 'target', 'as': 'upstream', 'maxDepth': max_depth - 1, 'depthField': 'depth',
                          'restrictSearchWithMatch': dict(scope)}},
        {'$project': {'_id': 0, 'dependent': '$source', 'via': '$target', 'relation': 1,
                      'upstream': {'$map': {'input': '$upstream', 'as': 'edge',
                                            'in': {'node': '$$edge.source', 'relation': '$$edge.relation', 'depth': '$$edge.depth'}}}}},
    ]


def _paths_of(nodes, repo_id: str, snapshot_id: str, ids) -> dict:
    """Node id -> file path: `file:` ids need no lookup, others are resolved through repo_nodes."""
    paths = {node_id: node_id[5:] for node_id in ids if node_id.startswith('file:')}
    rest = sorted(set(ids) - set(paths))
    for start in range(0, len(rest), BATCH):
        for doc in nodes.find({'repo_id': repo_id, 'snapshot_id': snapshot_id, 'id': {'$in': rest[start:start + BATCH]}},
                              {'_id': 0, 'id': 1, 'path': 1}):
            if isinstance(doc.get('path'), str):
                paths[doc['id']] = doc['path']
    return paths


def atlas_blast_radius(repo_id: str, snapshot_id: str, focal_paths: list[str], focal_node_ids: list[str] | None = None,
                       max_depth: int = 1) -> dict | None:
    """Compute dependents and linked tests of the focal files in Atlas, or report that the mirror is stale.

    Returns `{'served': True, ...}` with full (untruncated, sorted) path lists, or
    `{'served': False, 'stale': True, 'reason': ...}` when the mirrored snapshot is not `snapshot_id`.
    Returns None when there is nothing to ask about. Failures raise DataPlatformError.
    """
    focal_paths = sorted({path for path in focal_paths if isinstance(path, str) and path})
    if not focal_paths:
        return None
    config = _read_config()
    starts = sorted({'file:' + path for path in focal_paths} | {node for node in focal_node_ids or [] if isinstance(node, str)})
    pipeline = blast_radius_pipeline(repo_id, snapshot_id, starts, max_depth)
    collections = list(COLLECTIONS.values())[:2]
    try:
        with ExitStack() as stack:
            nodes = stack.enter_context(data_platforms._atlas_collection({**config, 'collection': COLLECTIONS['nodes']}))
            edges = stack.enter_context(data_platforms._atlas_collection({**config, 'collection': COLLECTIONS['edges']}))
            if nodes.find_one({'repo_id': repo_id, 'snapshot_id': snapshot_id}, {'_id': 1}) is None:
                held = nodes.find_one({'repo_id': repo_id}, {'_id': 0, 'snapshot_id': 1})
                found = held.get('snapshot_id') if isinstance(held, dict) else None
                return {'served': False, 'stale': True, 'snapshot_id': snapshot_id, 'mirrored_snapshot_id': found,
                        'collections': collections,
                        'reason': (f'the mirror holds snapshot {found}, not {snapshot_id}' if found
                                   else f'snapshot {snapshot_id} is not mirrored')}
            rows = list(edges.aggregate(pipeline, maxTimeMS=BLAST_MAX_TIME_MS))
            direct_ids = {row['dependent'] for row in rows if isinstance(row.get('dependent'), str)}
            hop_ids = {hop['node'] for row in rows for hop in row.get('upstream') or [] if isinstance(hop.get('node'), str)}
            paths = _paths_of(nodes, repo_id, snapshot_id, direct_ids | hop_ids)
            touched = sorted(set(focal_paths) | {paths[node] for node in direct_ids if node in paths})
            communities = {doc.get('community') for doc in nodes.find(
                {'repo_id': repo_id, 'snapshot_id': snapshot_id, 'path': {'$in': touched}}, {'_id': 0, 'community': 1})}
    except DataPlatformError:
        raise
    except Exception as exc:
        raise DataPlatformError(f'Atlas blast-radius query failed ({type(exc).__name__}); check index setup and connectivity') from None
    focal_set = set(focal_paths)
    direct = {paths[row['dependent']] for row in rows if row.get('dependent') in paths}
    tests = {paths[row['dependent']] for row in rows
             if row.get('dependent') in paths and (row.get('relation') == 'tests' or _is_test_path(paths[row['dependent']]))}
    tests |= {path for path in focal_paths if _is_test_path(path)}
    dependents = direct - focal_set
    transitive = {paths[node] for node in hop_ids if node in paths} - dependents - focal_set
    ints = {value for value in communities if type(value) is int}
    return {'served': True, 'stale': False, 'snapshot_id': snapshot_id, 'database': config['database'],
            'collections': collections, 'dependent_files': sorted(dependents), 'linked_test_files': sorted(tests),
            'transitive_dependent_files': sorted(transitive), 'communities_touched': len(ints) or None,
            'hops': max_depth + 1}
