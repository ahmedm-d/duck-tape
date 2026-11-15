"""The Atlas graph mirror is exercised against an in-memory fake; no network, no live cluster."""
import sys
from contextlib import contextmanager
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend import atlas_graph, data_platforms, db, main, symbols

SECRET_FILE = 'settings.py'
SECRET_TEXT = 'API_KEY = "abcdefghijklmnop1234567890"\n\ndef load_settings():\n    return API_KEY\n'
FILES = {
    'app.py': 'from pricing import total\n\ndef quote(items):\n    return total(items)\n',
    'pricing.py': 'def total(items, tax_rate=10):\n    return sum(items)\n',
    SECRET_FILE: SECRET_TEXT,
    '.env.local': 'TOKEN=value\n',
}
REPO = 'repo-private'
COLLECTIONS = ('repo_nodes', 'repo_edges', 'approved_knowledge')


class FakeReplaceOne:
    """Stands in for pymongo.ReplaceOne so the tests run without the optional cloud drivers."""

    def __init__(self, filter, replacement, upsert=False):
        self.filter, self.replacement, self.upsert = filter, replacement, upsert


class FakeCollection:
    def __init__(self):
        self.documents = {}
        self.indexes = {}

    def create_index(self, keys, name=None):
        self.indexes[name] = list(keys)
        return name

    def bulk_write(self, operations, ordered=False):
        for operation in operations:
            self.documents[operation.filter['_id']] = dict(operation.replacement)

    def delete_many(self, condition):
        doomed = [key for key, doc in self.documents.items()
                  if doc['repo_id'] == condition['repo_id'] and key not in condition['_id']['$nin']]
        for key in doomed:
            del self.documents[key]
        return type('Result', (), {'deleted_count': len(doomed)})()


class FakeAtlas:
    def __init__(self):
        self.collections = {}
        self.opened = []

    def docs(self, name):
        return self.collections.get(('ducktape', name), FakeCollection()).documents


@pytest.fixture
def atlas(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path / 'store')
    for name in ('STEWARD_ATLAS_ENABLED', 'STEWARD_ATLAS_URI', 'STEWARD_ATLAS_DIMENSIONS', 'STEWARD_ATLAS_DATABASE',
                 'STEWARD_ATLAS_GRAPH_MIRROR', 'STEWARD_GRAPH_ENGINE'):
        monkeypatch.delenv(name, raising=False)
    db.init()
    monkeypatch.setitem(sys.modules, 'pymongo', SimpleNamespace(ReplaceOne=FakeReplaceOne))
    fake = FakeAtlas()

    @contextmanager
    def connect(config):
        key = (config['database'], config['collection'])
        fake.opened.append(key)
        yield fake.collections.setdefault(key, FakeCollection())

    monkeypatch.setattr(data_platforms, '_atlas_collection', connect)
    return fake


def enable(monkeypatch):
    monkeypatch.setenv('STEWARD_ATLAS_ENABLED', '1')
    monkeypatch.setenv('STEWARD_ATLAS_URI', 'mongodb+srv://user:never-show-this@cluster.mongodb.net/')
    monkeypatch.setenv('STEWARD_ATLAS_DIMENSIONS', '3')
    monkeypatch.setenv('STEWARD_ATLAS_GRAPH_MIRROR', '1')


def make_repo(snapshot='snap-1', files=None):
    records = []
    for path, content in (files or FILES).items():
        names = symbols.parse_python(content)[0] if PurePosixPath(path).suffix == '.py' else []
        records.append({'path': path, 'status': 'indexed', 'content': content, 'hash': 'h' + path, 'symbols': names})
    index = {'files': records, 'gaps': [], 'snapshot': snapshot,
             'edges': [{'source': 'app.py', 'target': 'pricing.py', 'kind': 'imports', 'line': 1},
                       {'source': 'app.py', 'target': SECRET_FILE, 'kind': 'imports', 'line': 1},
                       {'source': SECRET_FILE, 'target': 'pricing.py', 'kind': 'imports', 'line': 1}]}
    return db.put('repositories', {'id': REPO, 'snapshot': snapshot, 'index': index})


def knowledge(identifier, status='team-approved', **extra):
    return db.put('knowledge', {'id': identifier, 'repo_id': REPO, 'title': 'Title ' + identifier,
                                'content': 'Use integer cents.', 'scope': '*', 'status': status, 'version': 2, **extra})


def test_flag_off_refuses_without_any_atlas_access(atlas, monkeypatch):
    make_repo()
    knowledge('k1')
    with pytest.raises(atlas_graph.MirrorRefused, match='STEWARD_ATLAS_GRAPH_MIRROR'):
        atlas_graph.mirror(REPO, 'snap-1')
    enable(monkeypatch)
    monkeypatch.setenv('STEWARD_ATLAS_GRAPH_MIRROR', '0')
    with pytest.raises(atlas_graph.MirrorRefused):
        atlas_graph.mirror(REPO, 'snap-1')
    assert atlas.opened == [] and atlas.collections == {}


def test_incomplete_atlas_config_or_public_database_is_refused(atlas, monkeypatch):
    make_repo()
    monkeypatch.setenv('STEWARD_ATLAS_GRAPH_MIRROR', '1')
    with pytest.raises(atlas_graph.MirrorRefused, match='Atlas integration is disabled'):
        atlas_graph.mirror(REPO, 'snap-1')
    monkeypatch.setenv('STEWARD_ATLAS_ENABLED', '1')
    with pytest.raises(atlas_graph.MirrorRefused, match='incomplete'):
        atlas_graph.mirror(REPO, 'snap-1')
    enable(monkeypatch)
    monkeypatch.setenv('STEWARD_ATLAS_DATABASE', 'ducktape_public')
    with pytest.raises(atlas_graph.MirrorRefused, match='incomplete or invalid'):
        atlas_graph.mirror(REPO, 'snap-1')
    assert atlas.opened == []


def test_mirror_writes_nodes_edges_and_approved_knowledge_with_expected_fields(atlas, monkeypatch):
    enable(monkeypatch)
    make_repo()
    stored = knowledge('k1', evidence=[{'path': 'app.py'}], author='someone')
    result = atlas_graph.mirror(REPO, 'snap-1')
    assert result['database'] == 'ducktape' and result['collections'] == {
        'nodes': 'repo_nodes', 'edges': 'repo_edges', 'knowledge': 'approved_knowledge'}
    assert result['engine'] == 'duck-tape-symbols' and result['removed_previous'] == 0
    nodes, edges, entries = (atlas.docs(name) for name in COLLECTIONS)
    assert (result['nodes'], result['edges'], result['knowledge']) == (len(nodes), len(edges), 1)
    assert {database for database, _ in atlas.collections} == {'ducktape'}
    for doc in [*nodes.values(), *edges.values(), *entries.values()]:
        assert doc['repo_id'] == REPO and doc['snapshot_id'] == 'snap-1' and doc['engine'] == 'duck-tape-symbols'
        assert len(doc['graph_hash']) == 64 and doc['mirrored_at']
    total = next(node for node in nodes.values() if node['label'] == 'total')
    assert total['kind'] and total['path'] == 'pricing.py' and total['line'] == 1 and 'community' in total
    assert total['signature'] == 'def total(items, tax_rate=10):' and total['degree'] >= 1
    file_node = next(node for node in nodes.values() if node['id'] == 'file:pricing.py')
    assert file_node['signature'] is None and 'content' not in file_node
    imports = [edge for edge in edges.values() if edge['relation'] == 'imports']
    assert [(e['source'], e['target']) for e in imports] == [('file:app.py', 'file:pricing.py')]
    assert imports[0]['evidence'] == {'path': 'app.py', 'line': 1} and imports[0]['suggestion_only'] is False
    assert imports[0]['confidence']
    entry = next(iter(entries.values()))
    assert {key: entry[key] for key in ('id', 'title', 'content', 'scope', 'version', 'updated_at')} == {
        'id': 'k1', 'title': 'Title k1', 'content': 'Use integer cents.', 'scope': '*', 'version': 2,
        'updated_at': stored['updated_at']}
    assert 'evidence' not in entry and 'author' not in entry


@pytest.mark.parametrize('path,content,expected', [
    ('code.py', 'def total(items):\n    return PRIVATE_BODY(items)\n', 'def total(items):'),
    ('code.py', 'def total(items): return PRIVATE_BODY(items)\nPRIVATE_NEXT()\n', 'def total(items):'),
    ('code.py', 'async def total(\n    items: dict[str, int],\n):\n    return PRIVATE_BODY(items)\n',
     'async def total( items: dict[str, int], ):'),
    ('code.py', 'class Total: PRIVATE_BODY()\n', 'class Total:'),
    ('code.py', 'def total(\n    items, # PRIVATE_HEADER_COMMENT\n):\n    return PRIVATE_BODY(items)\n', None),
    ('code.js', 'const total = items => PRIVATE_BODY(items);\n', None),
    ('code.ts', 'export function total(items: number): number { return PRIVATE_BODY(items); }\n', None),
])
def test_graph_projection_never_uploads_function_bodies(path, content, expected):
    graph = {'engine': 'duck-tape-symbols', 'graph_hash': 'h', 'edges': [],
             'nodes': [{'id': 'symbol:total', 'kind': 'function', 'label': 'total', 'path': path, 'line': 1}]}
    index = {'files': [{'path': path, 'content': content}]}

    documents, _ = atlas_graph._documents(REPO, 'snap-1', graph, index)

    projected = next(iter(documents['nodes'].values()))
    assert projected['signature'] == expected
    assert 'PRIVATE_' not in str(projected)


def test_notes_drafts_stale_conflicting_and_other_repositories_are_never_written(atlas, monkeypatch):
    enable(monkeypatch)
    make_repo()
    knowledge('approved')
    for identifier, status in (('draft', 'observed'), ('inferred', 'inferred'), ('stale', 'stale'),
                               ('disputed', 'conflicting'), ('rejected', 'rejected')):
        knowledge(identifier, status=status)
    knowledge('note', kind='notes')
    db.put('knowledge', {'id': 'invalidated', 'repo_id': REPO, 'title': 'old', 'content': 'old rule', 'status': 'team-approved', 'stale': True})
    db.put('knowledge', {'id': 'other', 'repo_id': 'someone-else', 'title': 'x', 'content': 'y', 'status': 'team-approved'})
    knowledge('a', constraint={'key': 'money', 'value': 'cents'})
    knowledge('b', constraint={'key': 'money', 'value': 'floats'})
    db.put('notes', {'id': 'n1', 'job_id': 'j', 'text': 'private team note about pricing', 'kind': 'team-note'})
    db.put('jobs', {'id': 'j', 'repo_id': REPO, 'title': 'private job title', 'description': 'private job text'})
    result = atlas_graph.mirror(REPO, 'snap-1')
    assert sorted(doc['id'] for doc in atlas.docs('approved_knowledge').values()) == ['approved']
    assert result['knowledge'] == 1
    written = str([doc for name in COLLECTIONS for doc in atlas.docs(name).values()])
    assert 'private team note' not in written and 'private job' not in written
    assert sorted(atlas.collections) == sorted(('ducktape', name) for name in COLLECTIONS)


def test_knowledge_containing_credential_material_is_withheld(atlas, monkeypatch):
    enable(monkeypatch)
    make_repo()
    db.put('knowledge', {'id': 'leaky', 'repo_id': REPO, 'title': 't', 'status': 'team-approved',
                         'content': 'password = "hunter2hunter2hunter2"'})
    result = atlas_graph.mirror(REPO, 'snap-1')
    assert result['knowledge'] == 0 and atlas.docs('approved_knowledge') == {}


def test_secret_bearing_files_are_excluded_from_nodes_and_edges(atlas, monkeypatch):
    enable(monkeypatch)
    make_repo()
    result = atlas_graph.mirror(REPO, 'snap-1')
    nodes, edges = atlas.docs('repo_nodes'), atlas.docs('repo_edges')
    paths = {node['path'] for node in nodes.values()}
    assert paths == {'app.py', 'pricing.py'}
    assert not any('settings' in str(doc) or 'abcdefghijklmnop' in str(doc) or '.env' in str(doc)
                   for doc in [*nodes.values(), *edges.values()])
    assert all(edge['evidence']['path'] in paths for edge in edges.values())
    # Two file nodes and one symbol node of the secret files, plus every edge touching them.
    assert result['excluded'] >= 6


def test_rerun_is_an_idempotent_upsert_with_stable_ids(atlas, monkeypatch):
    enable(monkeypatch)
    make_repo()
    knowledge('k1')
    first = atlas_graph.mirror(REPO, 'snap-1')
    ids = {name: set(atlas.docs(name)) for name in COLLECTIONS}
    second = atlas_graph.mirror(REPO, 'snap-1')
    assert {name: set(atlas.docs(name)) for name in COLLECTIONS} == ids
    assert (first['nodes'], first['edges'], first['knowledge']) == (second['nodes'], second['edges'], second['knowledge'])
    assert second['removed_previous'] == 0
    assert all(len(key) == 64 for key in ids['repo_nodes'])


@pytest.mark.parametrize('change', [{'status': 'rejected'}, {'stale': True}])
@pytest.mark.parametrize('keep', [False, True])
def test_same_snapshot_removes_revoked_knowledge_and_preserves_other_repositories(atlas, monkeypatch, change, keep):
    enable(monkeypatch)
    make_repo()
    knowledge('k1')
    if keep:
        knowledge('keep')
    atlas_graph.mirror(REPO, 'snap-1')
    entries = atlas.docs('approved_knowledge')
    entries['other-repo'] = {'_id': 'other-repo', 'repo_id': 'other', 'snapshot_id': 'snap-1'}
    db.put('knowledge', {**db.get('knowledge', 'k1'), **change})

    result = atlas_graph.mirror(REPO, 'snap-1')

    assert result['knowledge'] == int(keep)
    assert sorted(doc['id'] for doc in entries.values() if doc['repo_id'] == REPO) == (['keep'] if keep else [])
    assert 'other-repo' in entries


def test_new_snapshot_replaces_previous_snapshot_documents(atlas, monkeypatch):
    enable(monkeypatch)
    make_repo('snap-1')
    knowledge('k1')
    atlas_graph.mirror(REPO, 'snap-1')
    old = {name: set(atlas.docs(name)) for name in COLLECTIONS}
    make_repo('snap-2', {**FILES, 'extra.py': 'def added():\n    return 1\n'})
    result = atlas_graph.mirror(REPO, 'snap-2')
    assert result['removed_previous'] == sum(len(ids) for ids in old.values())
    for name, previous in old.items():
        current = atlas.docs(name)
        assert current and not previous & set(current)
        assert {doc['snapshot_id'] for doc in current.values()} == {'snap-2'}
    assert any(node['label'] == 'added' for node in atlas.docs('repo_nodes').values())


def test_previous_snapshot_is_kept_when_a_write_fails(atlas, monkeypatch):
    enable(monkeypatch)
    make_repo('snap-1')
    atlas_graph.mirror(REPO, 'snap-1')
    before = set(atlas.docs('repo_nodes'))
    make_repo('snap-2')

    def boom(self, operations, ordered=False):
        raise RuntimeError('network down')

    monkeypatch.setattr(FakeCollection, 'bulk_write', boom)
    with pytest.raises(RuntimeError):
        atlas_graph.mirror(REPO, 'snap-2')
    assert set(atlas.docs('repo_nodes')) == before


def test_route_is_409_when_disabled_and_503_on_atlas_failure(atlas, monkeypatch):
    client = TestClient(main.app)
    repo = make_repo()
    knowledge('k1')
    url = f'/api/repositories/{repo["id"]}/atlas-graph-mirror'
    refused = client.post(url)
    assert refused.status_code == 409 and 'STEWARD_ATLAS_GRAPH_MIRROR' in refused.json()['detail']
    assert atlas.opened == []
    assert client.post('/api/repositories/missing/atlas-graph-mirror').status_code == 404

    enable(monkeypatch)
    done = client.post(url)
    assert done.status_code == 200 and done.json()['snapshot_id'] == 'snap-1' and done.json()['knowledge'] == 1
    assert 'never-show-this' not in done.text

    @contextmanager
    def failing(config):
        raise data_platforms.DataPlatformError('Atlas request failed; check connectivity, credentials and index setup')
        yield

    monkeypatch.setattr(data_platforms, '_atlas_collection', failing)
    broken = client.post(url)
    assert broken.status_code == 503 and 'never-show-this' not in broken.text


def test_missing_pymongo_fails_closed_with_clear_message_and_503(atlas, monkeypatch):
    enable(monkeypatch)
    repo = make_repo()
    knowledge('k1')
    monkeypatch.setitem(sys.modules, 'pymongo', None)  # makes `from pymongo import ...` raise ImportError
    with pytest.raises(data_platforms.DataPlatformError, match='requirements-cloud.txt'):
        atlas_graph.mirror(REPO, 'snap-1')
    assert atlas.opened == [] and atlas.collections == {}
    response = TestClient(main.app).post(f'/api/repositories/{repo["id"]}/atlas-graph-mirror')
    assert response.status_code == 503 and 'requirements-cloud.txt' in response.json()['detail']
    assert 'never-show-this' not in response.text
