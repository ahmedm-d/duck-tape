"""Explicit, opt-in mirror of the repository graph and approved knowledge into the team's private Atlas.

This is a separate consent from ``STEWARD_ATLAS_RETRIEVAL`` (hashed vectors only): enabling the mirror
sends node labels, file paths, line numbers, declaration lines and approved knowledge text to the private
database. SQLite stays authoritative; Atlas holds a read-only projection of one snapshot per repository.
Never mirrored: team notes, drafts, job data, repository profiles, file bodies, evidence text, and any
file that Duck Tape's Atlas vector upload would also exclude as secret-bearing.
"""
from contextlib import ExitStack
import hashlib
import json
import os

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


def _config() -> dict:
    if os.getenv(FLAG) != '1':
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
        declaration = None if node.get('kind') == 'file' else agent_context.signature(
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
