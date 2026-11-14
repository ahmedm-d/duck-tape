import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import db, graphify_graph, main, sample

RAW = json.loads((Path(__file__).parent / 'fixtures' / 'graphify' / 'parcel-graph.json').read_text(encoding='utf-8'))


@pytest.fixture
def parcel(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path / 'store')
    monkeypatch.delenv('STEWARD_EMBEDDING_MODEL', raising=False)
    db.init()
    repo = sample.seed_sample()
    return TestClient(main.app), repo


def files_for(repo):
    return graphify_graph._files(repo['index'])


def test_fixture_is_real_graphify_output():
    assert RAW['graph'] == {'schema_version': 1, 'graphify_version': '0.9.108'}


def test_normalize_keeps_only_snapshot_validated_nodes_and_edges(parcel):
    _, repo = parcel
    nodes, edges, gaps, meta = graphify_graph.normalize_graphify(RAW, files_for(repo))
    paths = {item['path'] for item in repo['index']['files'] if item.get('status') == 'indexed'}
    assert meta['graphify_version'] == '0.9.108'
    assert all(node['path'] in paths for node in nodes)
    ids = {node['id'] for node in nodes}
    assert all(edge['source'] in ids and edge['target'] in ids for edge in edges)
    calls = {(edge['source'], edge['target']) for edge in edges if edge['relation'] == 'calls'}
    assert ('graphify:app_quote', 'graphify:pricing_total') in calls
    quote = next(edge for edge in edges if edge['source'] == 'graphify:app_quote' and edge['relation'] == 'calls')
    assert quote['evidence'] == {'path': 'app.py', 'line': 5} and quote['confidence'] == 'EXTRACTED' and not quote['suggestion_only']
    assert any(node['id'] == 'file:requirements.lock' for node in nodes)


def test_normalize_drops_unlocated_items_and_labels_inferred_edges(parcel):
    _, repo = parcel
    raw = json.loads(json.dumps(RAW))
    raw['nodes'].append({'id': 'ghost', 'label': 'ghost()', 'source_file': 'missing.py', 'source_location': 'L1'})
    raw['nodes'].append({'id': 'far', 'label': 'far()', 'source_file': 'app.py', 'source_location': 'L999'})
    raw['links'].append({'source': 'app_quote', 'target': 'search_unique_labels', 'relation': 'uses', 'confidence': 'INFERRED',
                         'confidence_score': 0.4, 'source_file': 'app.py', 'source_location': 'L4'})
    raw['links'].append({'source': 'app_quote', 'target': 'ghost', 'relation': 'calls', 'confidence': 'EXTRACTED', 'source_file': 'app.py', 'source_location': 'L5'})
    nodes, edges, gaps, _ = graphify_graph.normalize_graphify(raw, files_for(repo))
    assert not {'graphify:ghost', 'graphify:far'} & {node['id'] for node in nodes}
    inferred = next(edge for edge in edges if edge['relation'] == 'uses')
    assert inferred['confidence'] == 'INFERRED' and inferred['suggestion_only']
    assert sum('no validated snapshot location' in gap for gap in gaps) == 2
    assert any('edges lacked validated endpoints' in gap for gap in gaps)
    with pytest.raises(graphify_graph.GraphifyUnavailable):
        graphify_graph.normalize_graphify({'graph': {'schema_version': 2}, 'nodes': []}, {})


def test_graphify_result_is_cached_per_snapshot(parcel, monkeypatch):
    _, repo = parcel
    calls = []
    monkeypatch.setattr(graphify_graph, '_run_graphify', lambda snapshot_id, cancel=None: calls.append(snapshot_id) or RAW)
    first = graphify_graph.knowledge_graph(repo['id'], repo['source_snapshot_id'])
    again = graphify_graph.knowledge_graph(repo['id'], repo['source_snapshot_id'])
    assert first['engine'] == 'graphify' and first['engine_version'] == '0.9.108' and 'Apache-2.0' in first['attribution']
    assert first == again and calls == [repo['source_snapshot_id']]
    assert first['snapshot_id'] == repo['source_snapshot_id'] and first['communities']


def test_missing_graphify_falls_back_to_labelled_builtin_graph(parcel, monkeypatch):
    api, repo = parcel
    monkeypatch.setattr(graphify_graph, 'executable', lambda: None)
    response = api.get(f'/api/repositories/{repo["id"]}/graph').json()
    assert response['engine'] == 'duck-tape-symbols'
    assert 'Graphify CLI not installed' in response['gaps'][0] and 'built-in symbol graph fallback' in response['gaps'][0]
    assert any(edge['relation'] == 'imports' and edge['source'] == 'file:app.py' for edge in response['edges'])
    assert api.get(f'/api/repositories/{repo["id"]}/graph?engine=graphify').status_code == 503
    assert api.get(f'/api/repositories/{repo["id"]}/graph?engine=other').status_code == 422


def test_graphify_environment_has_no_model_keys(parcel, monkeypatch, tmp_path):
    _, repo = parcel
    seen = {}
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'secret-value')
    monkeypatch.setenv('OPENAI_API_KEY', 'secret-value')
    monkeypatch.setattr(graphify_graph, 'executable', lambda: 'graphify')

    def fake_run(argv, cwd, env, **kwargs):
        seen.update(argv=argv, env=env)
        out = Path(argv[argv.index('--out') + 1]) / 'graphify-out'
        out.mkdir(parents=True)
        (out / 'graph.json').write_text(json.dumps(RAW), encoding='utf-8')
        return type('Done', (), {'returncode': 0})()
    monkeypatch.setattr(graphify_graph.subprocess, 'run', fake_run)
    result = graphify_graph.graphify(repo['id'], repo['source_snapshot_id'], refresh=True)
    assert seen['argv'][1:3] == ['extract', seen['argv'][2]] and '--code-only' in seen['argv']
    assert seen['env']['GRAPHIFY_BACKEND'] == 'none' and seen['env']['GRAPHIFY_QUERY_LOG_DISABLE'] == '1'
    assert not any('KEY' in name or 'TOKEN' in name for name in seen['env'])
    assert result['stats']['nodes'] > 0 and not list((Path(db.DATA_DIR) / 'graphify').glob('*'))


@pytest.mark.skipif(graphify_graph.executable() is None, reason='Graphify CLI not installed')
def test_installed_graphify_builds_the_sample_map(parcel):
    api, repo = parcel
    graph = api.get(f'/api/repositories/{repo["id"]}/graph?engine=graphify').json()
    assert graph['engine'] == 'graphify' and graph['stats']['edges'] > 0
    assert any(edge['relation'] == 'calls' and edge['evidence']['path'] == 'app.py' for edge in graph['edges'])
