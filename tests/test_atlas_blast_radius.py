"""Blast radius computed in Atlas with $graphLookup: pipeline shape, an in-memory Mongo that evaluates it, and fallbacks.

No network and no live cluster: Atlas is faked in memory. The fake evaluates only the stages this pipeline uses.
"""
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from backend import agent_context, atlas_graph, data_platforms, db, sample

REPO_SCOPE = ('repo_id', 'snapshot_id')


class FakeReplaceOne:
    def __init__(self, filter, replacement, upsert=False):
        self.filter, self.replacement, self.upsert = filter, replacement, upsert


def _matches(doc, condition):
    for key, wanted in condition.items():
        value = doc.get(key)
        if isinstance(wanted, dict):
            if '$in' in wanted and value not in wanted['$in']:
                return False
            if '$ne' in wanted and value == wanted['$ne']:
                return False
        elif value != wanted:
            return False
    return True


class FakeCollection:
    """Just enough pymongo: writes for mirror(), finds for the node lookups, and an aggregate() for the pipeline."""

    def __init__(self, atlas, name):
        self.atlas, self.name, self.documents, self.indexes = atlas, name, {}, {}

    def create_index(self, keys, name=None):
        self.indexes[name] = list(keys)
        return name

    def bulk_write(self, operations, ordered=False):
        for operation in operations:
            self.documents[operation.filter['_id']] = dict(operation.replacement)

    def delete_many(self, condition):
        doomed = [key for key, doc in self.documents.items()
                  if doc['repo_id'] == condition['repo_id'] and doc['snapshot_id'] != condition['snapshot_id']['$ne']]
        for key in doomed:
            del self.documents[key]
        return SimpleNamespace(deleted_count=len(doomed))

    def find(self, condition, projection=None):
        return [dict(doc) for doc in self.documents.values() if _matches(doc, condition)]

    def find_one(self, condition, projection=None):
        return next(iter(self.find(condition)), None)

    def aggregate(self, pipeline, **options):
        self.atlas.pipelines.append(pipeline)
        if self.atlas.broken:
            raise RuntimeError('boom')
        match, lookup, project = (stage for stage in pipeline)
        rows = []
        for edge in self.find(match['$match']):
            spec = lookup['$graphLookup']
            assert spec['from'] == 'repo_edges' and self.name == 'repo_edges'
            frontier, seen, upstream, depth = {edge['source']}, set(), [], 0
            while frontier and depth <= spec['maxDepth']:
                found = [doc for doc in self.documents.values()
                         if doc.get(spec['connectToField']) in frontier and _matches(doc, spec['restrictSearchWithMatch'])]
                upstream += [{**doc, spec['depthField']: depth} for doc in found if doc['_id'] not in seen]
                seen |= {doc['_id'] for doc in found}
                frontier = {doc[spec['connectFromField']] for doc in found}
                depth += 1
            fields = project['$project']
            assert fields['dependent'] == '$source' and fields['via'] == '$target'
            rows.append({'dependent': edge['source'], 'via': edge['target'], 'relation': edge['relation'],
                         'upstream': [{'node': hop['source'], 'relation': hop['relation'], 'depth': hop['depth']} for hop in upstream]})
        return iter(rows)


class FakeAtlas:
    def __init__(self):
        self.collections, self.opened, self.pipelines, self.broken = {}, [], [], False

    def docs(self, name):
        return self.collections[('ducktape', name)].documents if ('ducktape', name) in self.collections else {}


@pytest.fixture
def atlas(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path / 'store')
    for name in ('STEWARD_EMBEDDING_MODEL', 'STEWARD_ATLAS_RETRIEVAL', 'STEWARD_GRAPH_ENGINE', 'STEWARD_ATLAS_GRAPH_MIRROR',
                 'STEWARD_ATLAS_DATABASE'):
        monkeypatch.delenv(name, raising=False)
    db.init()
    monkeypatch.setitem(sys.modules, 'pymongo', SimpleNamespace(ReplaceOne=FakeReplaceOne))
    fake = FakeAtlas()

    @contextmanager
    def connect(config):
        key = (config['database'], config['collection'])
        fake.opened.append(key)
        yield fake.collections.setdefault(key, FakeCollection(fake, config['collection']))

    monkeypatch.setattr(data_platforms, '_atlas_collection', connect)
    monkeypatch.setenv('STEWARD_ATLAS_ENABLED', '1')
    monkeypatch.setenv('STEWARD_ATLAS_URI', 'mongodb+srv://user:never-show-this@cluster.mongodb.net/')
    monkeypatch.setenv('STEWARD_ATLAS_DIMENSIONS', '3')
    return fake


@pytest.fixture
def required(monkeypatch):
    monkeypatch.setenv('STEWARD_ATLAS_REQUIRED', '1')


def mirrored(atlas, monkeypatch):
    """Seed the Parcel sample and mirror its current snapshot into the fake Atlas, as the auto-sync would."""
    repo = sample.seed_sample()
    monkeypatch.setenv('STEWARD_ATLAS_GRAPH_MIRROR', '1')
    atlas_graph.mirror(repo['id'], repo['source_snapshot_id'])
    monkeypatch.delenv('STEWARD_ATLAS_GRAPH_MIRROR')
    return repo


def context(repo, query='quote total tax'):
    return agent_context.agent_graph_context(repo['id'], repo['source_snapshot_id'], query, 3500)


def local_only(repo, monkeypatch, query='quote total tax'):
    monkeypatch.setenv('STEWARD_ATLAS_REQUIRED', '0')
    result = context(repo, query)
    monkeypatch.setenv('STEWARD_ATLAS_REQUIRED', '1')
    return result


# --- pure pipeline -------------------------------------------------------------------------------------------------

def test_pipeline_matches_edges_into_start_nodes_then_walks_reverse_edges_in_the_same_snapshot():
    pipeline = atlas_graph.blast_radius_pipeline('repo-1', 'snap-1', ['file:pricing.py', 'file:app.py', 'file:app.py'], max_depth=1)
    match, lookup, project = pipeline
    assert [next(iter(stage)) for stage in pipeline] == ['$match', '$graphLookup', '$project']
    relations = sorted(agent_context.FILE_EDGES)
    assert match['$match'] == {'repo_id': 'repo-1', 'snapshot_id': 'snap-1', 'relation': {'$in': relations},
                               'target': {'$in': ['file:app.py', 'file:pricing.py']}}
    walk = lookup['$graphLookup']
    assert walk['from'] == 'repo_edges' and walk['startWith'] == '$source'
    assert walk['connectFromField'] == 'source' and walk['connectToField'] == 'target' and walk['as'] == 'upstream'
    assert walk['maxDepth'] == 0 and walk['depthField'] == 'depth'
    assert walk['restrictSearchWithMatch'] == {'repo_id': 'repo-1', 'snapshot_id': 'snap-1', 'relation': {'$in': relations}}
    assert project['$project']['dependent'] == '$source'


def test_pipeline_depth_and_input_validation():
    assert atlas_graph.blast_radius_pipeline('r', 's', ['file:a'], 3)[1]['$graphLookup']['maxDepth'] == 2
    for depth in (0, 5, True, '1'):
        with pytest.raises(ValueError):
            atlas_graph.blast_radius_pipeline('r', 's', ['file:a'], depth)
    with pytest.raises(ValueError):
        atlas_graph.blast_radius_pipeline('r', 's', [])
    assert atlas_graph.atlas_blast_radius('r', 's', []) is None


def test_test_path_predicate_matches_the_local_category():
    assert all(atlas_graph._is_test_path(path) for path in ('tests/test_quote.py', 'pkg/tests/helpers.py', 'src/app.test.ts', 'test_x.py'))
    assert not any(atlas_graph._is_test_path(path) for path in ('app.py', 'pricing.py', 'docs/readme.md'))


# --- mirror creates the indexes ------------------------------------------------------------------------------------

def test_mirror_creates_the_blast_radius_indexes_idempotently(atlas, monkeypatch):
    mirrored(atlas, monkeypatch)
    edges, nodes = atlas.collections[('ducktape', 'repo_edges')], atlas.collections[('ducktape', 'repo_nodes')]
    assert edges.indexes == {'repo_edges_target': [('repo_id', 1), ('snapshot_id', 1), ('target', 1)],
                             'repo_edges_source': [('repo_id', 1), ('snapshot_id', 1), ('source', 1)]}
    assert nodes.indexes == {'repo_nodes_path': [('repo_id', 1), ('snapshot_id', 1), ('path', 1)]}
    monkeypatch.setenv('STEWARD_ATLAS_REQUIRED', '1')
    assert atlas_graph.ensure_indexes() == {'repo_nodes': ['repo_nodes_path'],
                                            'repo_edges': ['repo_edges_target', 'repo_edges_source']}
    assert len(edges.indexes) == 2


# --- agent context -------------------------------------------------------------------------------------------------

def test_atlas_result_equal_to_local_is_served_and_labelled(atlas, required, monkeypatch):
    repo = mirrored(atlas, monkeypatch)
    expected = local_only(repo, monkeypatch)
    atlas.pipelines.clear()
    served = context(repo)
    assert atlas.pipelines, 'the blast radius must be computed through an Atlas aggregation'
    assert served['scope']['atlas'] == {'served': True, 'collections': ['repo_nodes', 'repo_edges'],
                                        'snapshot_id': repo['source_snapshot_id'], 'database': 'ducktape'}
    blast, plain = served['blast_radius'], expected['blast_radius']
    assert blast['dependent_files'] == plain['dependent_files'] and blast['linked_test_files'] == plain['linked_test_files']
    assert 'tests/test_quote.py' in blast['linked_test_files']
    assert '$graphLookup' in blast['basis'] and 'ducktape.repo_edges' in blast['basis'] and repo['source_snapshot_id'] in blast['basis']
    assert 'transitive_dependent_files' in blast and 'transitive_dependent_files' not in plain
    assert not any('Atlas' in gap for gap in served['gaps'])
    assert expected['scope']['atlas']['served'] is False


def test_second_hop_is_walked_by_graphlookup(atlas, required, monkeypatch):
    repo = mirrored(atlas, monkeypatch)
    # pricing.py is imported by app.py, which is imported by tests/test_quote.py: the test file is a second-hop dependent.
    found = atlas_graph.atlas_blast_radius(repo['id'], repo['source_snapshot_id'], ['pricing.py'], ['file:pricing.py'])
    assert found['served'] and 'app.py' in found['dependent_files']
    assert 'tests/test_quote.py' in found['transitive_dependent_files'] and 'tests/test_quote.py' not in found['dependent_files']
    one_hop_only = atlas_graph.atlas_blast_radius(repo['id'], repo['source_snapshot_id'], ['pricing.py'], None, max_depth=1)
    assert one_hop_only['hops'] == 2


def test_mismatch_uses_local_values_and_names_the_difference(atlas, required, monkeypatch):
    repo = mirrored(atlas, monkeypatch)
    expected = local_only(repo, monkeypatch)
    edges = atlas.collections[('ducktape', 'repo_edges')].documents
    sample_edge = next(edge for edge in edges.values() if edge['relation'] == 'imports')
    edges['extra'] = {**sample_edge, '_id': 'extra', 'source': 'file:search.py', 'target': 'file:pricing.py', 'relation': 'imports'}
    result = context(repo)
    assert result['scope']['atlas']['served'] is False and 'differs from local graph' in result['scope']['atlas']['reason']
    gap = next(item for item in result['gaps'] if item.startswith('Atlas blast radius differs from local graph'))
    assert 'dependent files +1/-0' in gap and gap.endswith('local used')
    assert result['blast_radius'] == expected['blast_radius'] and '$graphLookup' not in result['blast_radius']['basis']


def test_stale_mirror_is_never_served(atlas, required, monkeypatch):
    repo = mirrored(atlas, monkeypatch)
    for collection in atlas.collections.values():
        for doc in collection.documents.values():
            doc['snapshot_id'] = 'older-snapshot'
    stale = atlas_graph.atlas_blast_radius(repo['id'], repo['source_snapshot_id'], ['pricing.py'], None)
    assert stale['served'] is False and stale['stale'] is True and stale['mirrored_snapshot_id'] == 'older-snapshot'
    assert not atlas.pipelines
    expected = local_only(repo, monkeypatch)
    result = context(repo)
    assert result['scope']['atlas']['served'] is False and 'older-snapshot' in result['scope']['atlas']['reason']
    assert any(gap.startswith('Atlas blast radius unavailable (the mirror holds snapshot older-snapshot') and gap.endswith('local graph used')
               for gap in result['gaps'])
    assert result['blast_radius'] == expected['blast_radius'] and not atlas.pipelines


def test_unmirrored_repository_is_reported_unavailable(atlas, required, monkeypatch):
    repo = sample.seed_sample()
    result = context(repo)
    assert result['scope']['atlas']['served'] is False and 'is not mirrored' in result['scope']['atlas']['reason']
    assert any('Atlas blast radius unavailable' in gap for gap in result['gaps'])


def test_atlas_error_falls_back_to_local_with_a_gap_and_no_secret(atlas, required, monkeypatch):
    repo = mirrored(atlas, monkeypatch)
    expected = local_only(repo, monkeypatch)
    atlas.broken = True
    result = context(repo)
    assert result['scope']['atlas']['served'] is False
    assert any(gap.startswith('Atlas blast radius unavailable (Atlas blast-radius query failed (RuntimeError)') for gap in result['gaps'])
    assert result['blast_radius'] == expected['blast_radius'] and 'never-show-this' not in str(result)

    @contextmanager
    def failing(config):
        raise data_platforms.DataPlatformError('Atlas request failed; check connectivity, credentials and index setup')
        yield

    monkeypatch.setattr(data_platforms, '_atlas_collection', failing)
    down = context(repo)
    assert any('Atlas request failed' in gap and 'local graph used' in gap for gap in down['gaps'])
    assert down['blast_radius'] == expected['blast_radius']


def test_missing_configuration_in_required_mode_is_a_gap_not_a_crash(atlas, required, monkeypatch):
    repo = mirrored(atlas, monkeypatch)
    monkeypatch.delenv('STEWARD_ATLAS_ENABLED')
    result = context(repo)
    assert result['scope']['atlas']['served'] is False
    assert any('Atlas integration is disabled' in gap for gap in result['gaps'])


def test_not_required_and_no_flag_never_calls_atlas(atlas, monkeypatch):
    repo = sample.seed_sample()
    monkeypatch.setenv('STEWARD_ATLAS_REQUIRED', '0')

    def forbidden(*args, **kwargs):
        raise AssertionError('Atlas must not be called')

    monkeypatch.setattr(atlas_graph, 'atlas_blast_radius', forbidden)
    monkeypatch.setattr(data_platforms, '_atlas_collection', forbidden)
    result = context(repo)
    assert result['scope']['atlas']['served'] is False and result['scope']['atlas']['collections'] == ['repo_nodes', 'repo_edges']
    assert result['scope']['atlas']['snapshot_id'] == repo['source_snapshot_id']
    assert not any('Atlas' in gap for gap in result['gaps']) and '$graphLookup' not in result['blast_radius']['basis']
    assert atlas.opened == []


def test_legacy_mirror_flag_enables_the_atlas_blast_radius(atlas, monkeypatch):
    repo = mirrored(atlas, monkeypatch)
    monkeypatch.setenv('STEWARD_ATLAS_REQUIRED', '0')
    monkeypatch.setenv('STEWARD_ATLAS_GRAPH_MIRROR', '1')
    assert context(repo)['scope']['atlas']['served'] is True
