import json

import pytest

from backend import db, repository, retrieval, snapshots, providers


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path / 'store')
    monkeypatch.delenv('STEWARD_EMBEDDING_MODEL', raising=False)
    db.init()
    return tmp_path


def register(root, repo_id, files):
    source = root / repo_id
    source.mkdir(exist_ok=True)
    for path, content in files.items():
        target = source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
    snapshot = snapshots.capture(repo_id, source, {})
    index = repository.index_repository(source, repo_id=repo_id, source_snapshot_id=snapshot['id'])
    db.put('repositories', {'id': repo_id, 'root': str(source), 'source_snapshot_id': snapshot['id'], 'snapshot': index['snapshot'], 'index': index})
    db.put('snapshots', {'id': repo_id + ':' + index['snapshot'], 'repo_id': repo_id, 'snapshot': index['snapshot'], 'source_snapshot_id': snapshot['id'], 'index': index})
    return snapshot


def test_late_relevant_symbol_beats_early_decoy_and_preserves_multiple_regions(store):
    lines = ['# alpha_target omega_target early decoy', 'def alpha_target():', '    return 1'] + ['# filler'] * 240 + ['def omega_target():', '    return 2']
    snapshot = register(store, 'repo', {'large.py': '\n'.join(lines)})
    result = retrieval.retrieve('repo', snapshot['id'], 'alpha_target omega_target', 4000)
    assert any(item['start_line'] <= 244 <= item['end_line'] for item in result['items'])
    assert any(item['start_line'] <= 2 <= item['end_line'] for item in result['items'])
    assert all(item['snapshot'] == snapshot['id'] for item in result['items'])
    assert result['token_estimate'] <= 4000
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= result['token_estimate']
    persisted = db.list_chunks('repo', snapshot['id'])
    assert len(persisted) >= 4


def test_semantic_chunks_include_evidence_after_file_64(store, monkeypatch):
    snapshot = register(store, 'repo', {f'{i:03}.py': f'value = "ordinary {i}"\n' if i < 74 else 'value = "hidden relevant concept"\n' for i in range(75)})
    monkeypatch.setenv('STEWARD_EMBEDDING_MODEL', 'fixture-embedding')
    seen = []
    def embed(repo_id, snapshot_id, chunks, cancel=None):
        seen.extend(chunk['content'] for chunk in chunks)
        return [{**chunk, 'embedding': [1., 0.] if 'semantic question' in chunk['content'] or 'hidden relevant' in chunk['content'] else [0., 1.], 'embedding_model': 'fixture-embedding'} for chunk in chunks]
    monkeypatch.setattr(providers, 'embed', embed)
    result = retrieval.retrieve('repo', snapshot['id'], 'semantic question', 3000)
    assert result['items'][0]['path'] == '074.py'
    assert any('ordinary 64' in text for text in seen)
    assert any('hidden relevant' in text for text in seen)


def test_saved_embedding_configuration_enables_semantic_retrieval(store,monkeypatch):
    snapshot=register(store,'repo',{'helper.py':'value = "related concept"\n'})
    db.put('provider_settings',{'id':'default','embedding_model':'configured-embedding'})
    monkeypatch.setattr(providers,'embed',lambda repo_id,snapshot_id,chunks,cancel=None:[{**chunk,'embedding':[1.,0.]} for chunk in chunks])
    result=retrieval.retrieve('repo',snapshot['id'],'semantic query',3000)
    assert 'semantic' in result['mode'] and result['items'][0]['path']=='helper.py'


def test_namespace_isolation_and_deleted_current_evidence(store):
    first = register(store, 'first', {'old.py': 'def needle(): return 1\n'})
    second = register(store, 'second', {'other.py': 'def needle(): return 2\n'})
    result = retrieval.retrieve('first', first['id'], 'needle', 2000)
    assert {item['path'] for item in result['items']} == {'old.py'}
    assert all(item['repo_id'] == 'first' for item in result['items'])
    with pytest.raises(ValueError):
        retrieval.retrieve('first', second['id'], 'needle', 2000)
    (store / 'first' / 'old.py').unlink()
    current = register(store, 'first', {'new.py': 'def different(): return 3\n'})
    assert not any(item['path'] == 'old.py' for item in retrieval.retrieve('first', current['id'], 'needle', 2000)['items'])
    assert any(item['path'] == 'old.py' for item in retrieval.retrieve('first', first['id'], 'needle', 2000)['items'])


def test_qa_accepts_first_of_nonadjacent_ranges_and_rejects_missing_range(store, monkeypatch):
    snapshot = register(store, 'repo', {'both.py': 'def first_target(): return 1\n' + '# filler\n' * 200 + 'def last_target(): return 2\n'})
    monkeypatch.setattr(providers, 'health', lambda: {'available': True})
    from backend.contracts import ProviderResult
    def complete(request, **kwargs):
        return ProviderResult(output={'answer': 'The first target returns one.', 'citations': [{'path': 'both.py', 'start_line': 1, 'end_line': 1}]}, telemetry={'usage': None})
    monkeypatch.setattr(providers, 'complete', complete)
    result = retrieval.answer('repo', snapshot['id'], 'first_target last_target', None)
    assert result['mode'] == 'live evidence-linked answer'
    assert result['citations'][0]['start_line'] == 1
    def invalid(request, **kwargs):
        return ProviderResult(output={'answer': 'Fabricated.', 'citations': [{'path': 'both.py', 'start_line': 900, 'end_line': 901}]}, telemetry={'usage': None})
    monkeypatch.setattr(providers, 'complete', invalid)
    result = retrieval.answer('repo', snapshot['id'], 'first_target', None)
    assert result['mode'] == 'offline structural'
    assert all(item['end_line'] < 900 for item in result['citations'])


@pytest.mark.parametrize('prime_cache', [False, True])
def test_qa_retry_evicts_invalid_cached_citations_and_keeps_valid_cache(store, monkeypatch, prime_cache):
    from backend.contracts import ProviderRequest
    snapshot = register(store, 'repo', {'helper.py': 'def value(): return 1\n'})
    for name, value in {'STEWARD_MODEL': 'contract-model', 'STEWARD_MODEL_PROVIDER': 'openai',
                        'STEWARD_MODEL_ENDPOINT': 'http://fixture.invalid/v1',
                        'STEWARD_CONTEXT_LIMIT': '16000', 'STEWARD_EMBEDDING_MODEL': ''}.items():
        monkeypatch.setenv(name, value)
    context = retrieval.retrieve('repo', snapshot['id'], 'value')
    # Keep the supplied evidence identical across Retry, including timing metadata.
    monkeypatch.setattr(retrieval, 'retrieve', lambda *args, **kwargs: context)
    monkeypatch.setattr(providers, 'health', lambda: {'available': True})
    calls = []
    def request(*args, **kwargs):
        calls.append(args)
        output = {'answer': 'The function returns one.', 'citations': [
            {'path': 'helper.py', 'start_line': 1, 'end_line': 99 if len(calls) == 1 else 1}]}
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(output)}}],
                'usage': {'prompt_tokens': 12, 'completion_tokens': 8}}
    monkeypatch.setattr(providers, 'request', request)
    model_context = {key: value for key, value in context.items() if key != 'latency_ms'}
    request_input = ProviderRequest(task='qa', payload={'question': 'value', 'context': model_context},
                                    repo_id='repo', snapshot_id=snapshot['id'], max_output_tokens=1024)
    if prime_cache:
        providers.complete(request_input)
        assert len(db.list_records('model_cache')) == 1
    first = retrieval.answer('repo', snapshot['id'], 'value')
    assert first['mode'] == 'offline structural' and len(calls) == 1
    assert db.list_records('model_cache') == []
    retry = retrieval.answer('repo', snapshot['id'], 'value')
    assert retry['mode'] == 'live evidence-linked answer' and len(calls) == 2
    cached = retrieval.answer('repo', snapshot['id'], 'value')
    assert cached['provider']['cached'] is True and len(calls) == 2
    assert len(db.list_records('model_cache')) == 1


def test_invalid_qa_cache_eviction_preserves_concurrently_replaced_response(store):
    rejected = {'answer': 'Invalid', 'citations': [{'path': 'a.py', 'start_line': 99, 'end_line': 99}]}
    replacement = {'answer': 'Valid', 'citations': [{'path': 'a.py', 'start_line': 1, 'end_line': 1}]}
    db.put('model_cache', {'id': 'key', 'result': replacement, 'telemetry': {}})
    assert providers.invalidate_cached_result(rejected, {'cache_key': 'key'}) is False
    assert db.get('model_cache', 'key')['result'] == replacement


def test_explicit_paths_and_graph_keep_api_helper_config_and_tests(store):
    from backend.sample import FILES
    snapshot = register(store, 'repo', FILES)
    result = retrieval.retrieve('repo', snapshot['id'], 'A quote for two items at 100 cents should be 220 cents including tax. Trace app.py, pricing.py and config.py.', 7000)
    assert {'app.py', 'pricing.py', 'config.py', 'tests/test_quote.py'} <= {item['path'] for item in result['items']}


def test_budget_never_silently_drops_applicable_approved_constraints(store):
    snapshot = register(store, 'repo', {'a.py': 'value = 1\n'})
    knowledge = [{'repo_id': 'repo', 'status': 'team-approved', 'scope': '*', 'content': 'required policy ' * 400}]
    with pytest.raises(ValueError, match='budget'):
        retrieval.retrieve('repo', snapshot['id'], 'value', 2000, knowledge=knowledge)


def test_graph_preserves_import_and_function_ranges_in_ts_chain(store):
    snapshot = register(store, 'repo', {
        'config.ts': 'export const TAX = 10;\n',
        'helper.ts': 'import { TAX } from "./config.js";\nexport function totalTS(value: number) { return value + TAX; }\n',
        'api.ts': 'import { totalTS } from "./helper.js";\nexport function quoteTS(value: number) { return totalTS(value); }\n',
        'api.test.ts': 'import { quoteTS } from "./api.js";\nexport const observed = quoteTS(100);\n',
    })
    result = retrieval.retrieve('repo', snapshot['id'], 'quoteTS', 9000)
    for path in ['helper.ts', 'api.ts', 'api.test.ts']:
        assert any(item['path'] == path and item['start_line'] == 1 and item['end_line'] == 2 for item in result['items'])
