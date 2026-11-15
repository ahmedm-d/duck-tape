import pytest

from backend import data_platforms, db, retrieval

from tests.test_chunk_retrieval import register

FILES = {'app.py': 'from pricing import quote\n\ndef handler(request):\n    return quote(request)\n',
         'pricing.py': 'def quote(order):\n    return order["price"]\n',
         'notes.md': '# Parcel\nShipping notes.\n'}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path / 'store')
    monkeypatch.delenv('STEWARD_EMBEDDING_MODEL', raising=False)
    monkeypatch.delenv('STEWARD_ATLAS_RETRIEVAL', raising=False)
    db.init()
    return tmp_path


def fake_search(calls, picks):
    def search(repo_id, snapshot_id, query, limit=12):
        calls.append((repo_id, snapshot_id, query, limit))
        local = {item['path']: item for item in retrieval.index_chunks(repo_id, snapshot_id)}
        matches = [{**local[path], 'score': score} for path, score in picks if path in local]
        return {'provider': 'atlas', 'matches': matches + [{'chunk_id': 'foreign-chunk', 'path': 'secret.py', 'score': 0.99}]}
    return search


def test_atlas_is_off_by_default(store, monkeypatch):
    snapshot = register(store, 'repo', FILES)
    calls = []
    monkeypatch.setattr(data_platforms, 'atlas_search', fake_search(calls, [('notes.md', 0.9)]))
    result = retrieval.retrieve('repo', snapshot['id'], 'shipping handler', 4000)
    assert calls == [] and 'atlas' not in result['mode']
    assert not any('Atlas' in item for item in result['missing'])


def test_atlas_candidates_are_validated_and_scored(store, monkeypatch):
    snapshot = register(store, 'repo', FILES)
    calls = []
    monkeypatch.setattr(data_platforms, 'atlas_search', fake_search(calls, [('pricing.py', 0.91)]))
    monkeypatch.setenv('STEWARD_ATLAS_RETRIEVAL', '1')
    result = retrieval.retrieve('repo', snapshot['id'], 'how is the total computed', 4000, graph=False)
    assert calls and calls[0][:2] == ('repo', snapshot['id'])
    assert 'atlas' in result['mode']
    pricing = next(item for item in result['items'] if item['path'] == 'pricing.py')
    assert 'Atlas vector candidate 0.910' in pricing['reason']
    assert pricing['content'] == 'def quote(order):\n    return order["price"]'
    assert all(item['path'] != 'secret.py' for item in result['items'])
    assert any(item.startswith('Atlas candidates: 1 validated') for item in result['missing'])


def test_atlas_failure_is_a_labelled_fallback(store, monkeypatch):
    snapshot = register(store, 'repo', FILES)

    def unavailable(*args, **kwargs):
        raise data_platforms.DataPlatformError('Atlas vector index is not ready for queries')
    monkeypatch.setattr(data_platforms, 'atlas_search', unavailable)
    result = retrieval.retrieve('repo', snapshot['id'], 'quote handler', 4000, atlas=True)
    assert 'atlas' not in result['mode'] and result['items']
    assert any('Atlas candidates unavailable (Atlas vector index is not ready for queries); local keyword/graph fallback' == item
               for item in result['missing'])


def test_atlas_respects_path_filter_and_explicit_opt_out(store, monkeypatch):
    snapshot = register(store, 'repo', FILES)
    calls = []
    monkeypatch.setattr(data_platforms, 'atlas_search', fake_search(calls, [('pricing.py', 0.9)]))
    filtered = retrieval.retrieve('repo', snapshot['id'], 'handler', 4000, paths=['app.py'], atlas=True)
    assert {item['path'] for item in filtered['items']} == {'app.py'} and 'atlas' not in filtered['mode']
    monkeypatch.setenv('STEWARD_ATLAS_RETRIEVAL', '1')
    calls.clear()
    retrieval.retrieve('repo', snapshot['id'], 'handler', 4000, atlas=False)
    assert calls == []


def test_atlas_distractors_preserve_fixed_graph_task_recall(store, monkeypatch):
    files = {
        'api.py': 'from shared import calculate\n\ndef endpoint():\n    return calculate()\n',
        'shared.py': 'from config import RATE\n\ndef calculate():\n    return RATE\n',
        'config.py': 'RATE = 2\n',
        'test_api.py': 'from api import endpoint\n\ndef test_endpoint():\n    assert endpoint() == 2\n',
        **{f'unrelated_{number}.py': f'VALUE = {number}\n' for number in range(5)},
    }
    snapshot = register(store, 'repo', files)
    calls = []
    monkeypatch.setattr(data_platforms, 'atlas_search', fake_search(calls, [(f'unrelated_{number}.py', 1.0) for number in range(5)]))
    expected = {'api.py', 'shared.py', 'config.py', 'test_api.py'}
    baseline = retrieval.retrieve('repo', snapshot['id'], 'endpoint', 8000, limit=4, semantic=False, atlas=False)
    augmented = retrieval.retrieve('repo', snapshot['id'], 'endpoint', 8000, limit=4, semantic=False, atlas=True)
    recall = lambda result: len(expected & {item['path'] for item in result['items']}) / len(expected)
    assert recall(baseline) == 1.0
    assert recall(augmented) == recall(baseline)
    assert calls and 'atlas' in augmented['mode']
    spare = retrieval.retrieve('repo', snapshot['id'], 'endpoint', 8000, limit=5, semantic=False, atlas=True)
    assert recall(spare) == 1.0
    extra = [item for item in spare['items'] if item['path'] not in expected]
    assert len(extra) == 1 and 'Atlas vector candidate' in extra[0]['reason']
    assert extra[0]['repo_id'] == 'repo' and extra[0]['snapshot_id'] == snapshot['id']
