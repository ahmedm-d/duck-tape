import json

from backend import db, providers
from backend.contracts import ProviderResult
from test_chunk_retrieval import register, store


def test_structural_summaries_link_module_package_and_repository_evidence(store, monkeypatch):
    from backend import summaries
    snapshot = register(store, 'repo', {'package.json': '{"name":"fixture"}', 'module.py': 'def target(): return 1\n'})
    monkeypatch.setattr(providers, 'health', lambda: {'available': False})
    result = summaries.build('repo', snapshot['id'], None)
    assert {'module', 'package', 'repository'} <= {item['kind'] for item in result}
    assert all(item['mode'] == 'structural fallback' for item in result)
    assert all(item['evidence'] for item in result)
    assert all(ref['snapshot_id'] == snapshot['id'] for item in result for ref in item['evidence'])


def test_summary_cache_uses_model_and_dependencies_and_rejects_invalid_evidence(store, monkeypatch):
    from backend import summaries
    snapshot = register(store, 'repo', {'module.py': 'def target(): return 1\n'})
    monkeypatch.setattr(providers, 'health', lambda: {'available': True, 'model': 'fixture-model'})
    requests = []
    def complete(request, **kwargs):
        requests.append(request)
        return ProviderResult(output={'summary': 'Target returns one.', 'evidence': [{'path': 'module.py', 'start_line': 1, 'end_line': 1}], 'missing': []}, telemetry={'usage': None})
    monkeypatch.setattr(providers, 'complete', complete)
    first = summaries.build('repo', snapshot['id'], None)
    calls = len(requests)
    second = summaries.build('repo', snapshot['id'], None)
    assert len(requests) == calls
    assert {item['id'] for item in first} == {item['id'] for item in second}
    assert all(item['mode'] == 'inferred synthesis' for item in first)
    def invalid(request, **kwargs):
        return ProviderResult(output={'summary': 'Invalid claim.', 'evidence': [{'path': 'absent.py', 'start_line': 1, 'end_line': 1}], 'missing': []}, telemetry={'usage': None})
    monkeypatch.setattr(providers, 'complete', invalid)
    monkeypatch.setattr(providers, 'health', lambda: {'available': True, 'model': 'different-model'})
    changed = summaries.build('repo', snapshot['id'], None)
    assert all(item['mode'] == 'structural fallback' for item in changed)
    assert all('Invalid claim.' not in item['summary'] for item in changed)


def test_changed_and_deleted_files_invalidate_current_summaries(store, monkeypatch):
    from backend import summaries
    monkeypatch.setattr(providers, 'health', lambda: {'available': False})
    original = register(store, 'repo', {'old.py': 'def old_target(): return 1\n'})
    old = summaries.build('repo', original['id'], None)
    (store / 'repo' / 'old.py').unlink()
    current = register(store, 'repo', {'new.py': 'def new_target(): return 2\n'})
    new = summaries.build('repo', current['id'], None)
    assert not ({item['id'] for item in old} & {item['id'] for item in new})
    assert all(ref['path'] != 'old.py' for item in new for ref in item['evidence'])
    assert any(ref['path'] == 'old.py' for item in summaries.build('repo', original['id'], None) for ref in item['evidence'])


def test_summary_refresh_retries_invalid_provider_cache_and_reuses_valid_results(store, monkeypatch):
    from backend import summaries
    snapshot = register(store, 'repo', {'module.py': 'def target(): return 1\n'})
    for name, value in {'STEWARD_MODEL': 'contract-model', 'STEWARD_MODEL_PROVIDER': 'openai',
                        'STEWARD_MODEL_ENDPOINT': 'http://fixture.invalid/v1',
                        'STEWARD_CONTEXT_LIMIT': '16000'}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(providers, 'health', lambda: {'available': True, 'model': 'contract-model'})
    calls = []
    def request(*args, **kwargs):
        calls.append(args)
        output = {'summary': 'Target returns one.', 'missing': [], 'evidence': [
            {'path': 'module.py', 'start_line': 1, 'end_line': 99 if len(calls) <= 2 else 1}]}
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(output)}}],
                'usage': {'prompt_tokens': 12, 'completion_tokens': 8}}
    monkeypatch.setattr(providers, 'request', request)
    first = summaries.build('repo', snapshot['id'])
    assert len(first) == 2 and all(item['mode'] == 'structural fallback' for item in first)
    assert len(calls) == 2
    assert db.list_records('model_cache') == []
    retry = summaries.build('repo', snapshot['id'])
    assert all(item['mode'] == 'inferred synthesis' for item in retry) and len(calls) == 4
    assert len(db.list_records('model_cache')) == 2
    cached = summaries.build('repo', snapshot['id'])
    assert {item['id'] for item in cached} == {item['id'] for item in retry}
    assert all(item['mode'] == 'inferred synthesis' for item in cached) and len(calls) == 4
