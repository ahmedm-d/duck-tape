"""Sponsor HTTP contracts run against an in-memory transport, never a live API."""
import asyncio
import json

import httpx
import pytest

from backend import db, providers
from backend.contracts import CancellationToken, ProviderRequest


@pytest.fixture(autouse=True)
def isolated_provider(monkeypatch, tmp_path):
    for name in list(providers.os.environ):
        if name.startswith('STEWARD_') or name in ('HF_TOKEN', 'ADAPTION_API_KEY'):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init()
    monkeypatch.setenv('STEWARD_MODEL_PROVIDER', 'adaption')
    monkeypatch.setenv('STEWARD_MODEL', 'test-model')
    monkeypatch.setenv('STEWARD_MODEL_BACKOFF', '0')
    monkeypatch.setenv('ADAPTION_API_KEY', 'adaption-test-secret')
    monkeypatch.setenv('HF_TOKEN', 'hf-test-secret')


@pytest.fixture
def transport(monkeypatch):
    real_client = httpx.AsyncClient
    def install(handler):
        monkeypatch.setattr(providers.httpx, 'AsyncClient',
                            lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    return install


def adaption_response(**changes):
    text = '{"approved":true,"findings":[]}'
    return {'id': 'resp_test', 'object': 'response', 'status': 'completed', 'model': 'test-model',
            'output': [{'type': 'message', 'role': 'assistant', 'status': 'completed',
                        'content': [{'type': 'output_text', 'text': text}]}],
            'output_text': text, 'usage': {'input_tokens': 12, 'output_tokens': 8, 'total_tokens': 20},
            **changes}


def review(**kwargs):
    return providers.complete(ProviderRequest(task='review', payload={}, cache=False, **kwargs))


@pytest.mark.parametrize('kind,endpoint,key,path', [
    ('adaption', 'https://api.prod.adaptionlabs.ai/api/v1', 'adaption-test-secret', '/api/v1/responses'),
    ('huggingface', 'https://router.huggingface.co/v1', 'hf-test-secret', '/v1/chat/completions'),
])
def test_sponsor_http_contract_and_local_validation(monkeypatch, transport, kind, endpoint, key, path):
    monkeypatch.setenv('STEWARD_MODEL_PROVIDER', kind)
    monkeypatch.setenv('STEWARD_MODEL', 'org/test-model:preferred' if kind == 'huggingface' else 'test-model')
    sent = []
    def handle(request):
        assert str(request.url) == endpoint + path.removeprefix('/api/v1' if kind == 'adaption' else '/v1')
        assert request.headers['Authorization'] == 'Bearer ' + key
        body = json.loads(request.content)
        sent.append(body)
        if kind == 'adaption':
            assert body['input'] == '{}'
            assert 'Schema: ' in body['instructions']
            assert body['max_output_tokens'] == 64
            assert body['tools'] == [] and body['tool_choice'] == 'none'
            assert body['store'] is False and body['agentic_memory'] is False
            assert 'response_format' not in body
            return httpx.Response(200, json=adaption_response())
        assert body['model'] == 'org/test-model:preferred'
        assert body['response_format']['json_schema']['strict'] is True
        assert body['max_tokens'] == 64
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"approved":true,"findings":[]}'},
                                                    'finish_reason': 'stop'}],
                                        'usage': {'prompt_tokens': 12, 'completion_tokens': 8}})
    transport(handle)
    result = review(max_output_tokens=64)
    assert result.output == {'approved': True, 'findings': []}
    assert result.telemetry['usage'] == {'input_tokens': 12, 'output_tokens': 8}
    assert result.telemetry['attempts'] == 1 and len(sent) == 1
    assert providers.capabilities('review')['structured_output_mode'] == ('prompt_and_validate' if kind == 'adaption' else 'json_schema')
    assert 'secret' not in json.dumps(result.model_dump()) + json.dumps(providers.configuration())


def test_sponsor_selection_does_not_reuse_saved_local_endpoint(monkeypatch):
    db.put('provider_settings', {'id': 'default', 'kind': 'ollama', 'endpoint': 'http://127.0.0.1:11434', 'model': 'local-model'})
    monkeypatch.delenv('STEWARD_MODEL')
    assert providers.configuration()['endpoint'] == 'https://api.prod.adaptionlabs.ai/api/v1'
    assert providers.configuration()['model'] == ''


@pytest.mark.parametrize('kind,key', [('adaption', 'ADAPTION_API_KEY'), ('huggingface', 'HF_TOKEN')])
def test_missing_sponsor_key_fails_before_http(monkeypatch, transport, kind, key):
    monkeypatch.setenv('STEWARD_MODEL_PROVIDER', kind)
    monkeypatch.delenv(key)
    transport(lambda request: pytest.fail('Missing key must fail locally'))
    with pytest.raises(providers.ProviderError, match='missing_api_key'):
        review()
    assert providers.health()['available'] is False


@pytest.mark.parametrize('endpoint', ['https://custom.example/v1', 'http://api.prod.adaptionlabs.ai/api/v1',
                                     'https://api.prod.adaptionlabs.ai:444/api/v1',
                                     'https://api.prod.adaptionlabs.ai.evil.example/api/v1'])
def test_native_sponsor_key_never_follows_custom_origin(monkeypatch, transport, endpoint):
    monkeypatch.setenv('STEWARD_MODEL_ENDPOINT', endpoint)
    transport(lambda request: pytest.fail('Native key must not reach this origin'))
    with pytest.raises(providers.ProviderError, match='missing_api_key'):
        review()


def test_custom_endpoint_accepts_only_explicit_generic_key(monkeypatch, transport):
    monkeypatch.setenv('STEWARD_MODEL_ENDPOINT', 'https://custom.example/v1')
    monkeypatch.setenv('STEWARD_MODEL_KEY', 'explicit-custom-key')
    def handle(request):
        assert request.headers['Authorization'] == 'Bearer explicit-custom-key'
        return httpx.Response(200, json=adaption_response())
    transport(handle)
    assert review().output['approved']


@pytest.mark.parametrize('change,code', [
    ({'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'}}, 'truncated'),
    ({'status': 'in_progress'}, 'incomplete_finish'),
    ({'error': {'code': 'server_error', 'message': 'secret remote failure'}}, 'provider_failed'),
    ({'output': [{'type': 'function_call', 'name': 'run_shell', 'arguments': '{}'}]}, 'unexpected_tool_output'),
    ({'output': [{'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'secret refusal'}]}]}, 'refusal'),
    ({'output': [{'type': 'message', 'status': 'in_progress', 'content': []}]}, 'incomplete_finish'),
    ({'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"approved":true,"findings":[],"extra":1}'}]}]}, 'invalid_schema'),
])
def test_adaption_rejects_incomplete_untrusted_and_invalid_output(transport, change, code):
    transport(lambda request: httpx.Response(200, json=adaption_response(**change)))
    with pytest.raises(providers.ProviderError) as error:
        review()
    assert error.value.code == code
    assert 'secret' not in str(error.value) + json.dumps(error.value.telemetry)
    assert db.list_records('model_cache') == []


def test_adaption_joins_only_message_text_and_keeps_missing_usage_null(transport):
    raw = adaption_response(usage=None, output=[{'type': 'reasoning', 'summary': []},
        {'type': 'message', 'content': [{'type': 'output_text', 'text': '{"approved":'},
                                       {'type': 'output_text', 'text': 'true,"findings":[]}'}]}])
    transport(lambda request: httpx.Response(200, json=raw))
    result = review()
    assert result.output['approved'] and result.telemetry['usage'] is None


def test_adaption_retry_cache_and_redaction(transport):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(429, text='Bearer secret failure') if len(calls) == 1 else httpx.Response(200, json=adaption_response())
    transport(handle)
    task = ProviderRequest(task='review', payload={})
    first = providers.complete(task)
    second = providers.complete(task)
    assert first.telemetry['attempts'] == 2 and second.telemetry['cached']
    assert len(calls) == 2 and second.telemetry['usage'] is None
    assert 'secret' not in json.dumps(first.model_dump())


def test_adaption_cancellation_closes_transport(transport):
    cancel = CancellationToken()
    closed = []
    async def handle(request):
        cancel.cancel()
        try:
            await asyncio.sleep(5)
        finally:
            closed.append(True)
    transport(handle)
    with pytest.raises(providers.ProviderError, match='cancelled'):
        providers.complete(ProviderRequest(task='review', payload={}), cancel)
    assert closed == [True]


def test_adaption_context_limit_still_stops_transport(monkeypatch, transport):
    monkeypatch.setenv('STEWARD_CONTEXT_LIMIT', '1024')
    transport(lambda request: pytest.fail('Budget must be enforced before HTTP'))
    with pytest.raises(providers.ProviderError, match='context_budget_exceeded'):
        review()


def test_hf_health_normalizes_suffix_without_claiming_qualification(monkeypatch, transport):
    monkeypatch.setenv('STEWARD_MODEL_PROVIDER', 'huggingface')
    monkeypatch.setenv('STEWARD_MODEL', 'org/model:preferred')
    transport(lambda request: httpx.Response(200, json={'data': [{'id': 'org/model'}]}))
    result = providers.health()
    assert result['available'] is True and result['model'] == 'org/model:preferred'
    assert result['connection_check'] == 'model_catalog'
    assert result['qualification'].startswith('Not qualified')


@pytest.mark.parametrize('kind', ['adaption', 'huggingface'])
def test_sponsor_embedding_needs_separate_openai_endpoint(monkeypatch, transport, kind):
    monkeypatch.setenv('STEWARD_MODEL_PROVIDER', kind)
    monkeypatch.setenv('STEWARD_EMBEDDING_MODEL', 'local-embedding')
    transport(lambda request: pytest.fail('Sponsor chat endpoint cannot embed'))
    with pytest.raises(providers.ProviderError, match='embedding_endpoint_required'):
        providers.embed('repo', 'snapshot', [{'content': 'evidence'}])


@pytest.mark.parametrize('key', [None, 'embedding-only-key'])
def test_separate_embedding_never_receives_model_credentials(monkeypatch, transport, key):
    monkeypatch.setenv('STEWARD_EMBEDDING_MODEL', 'local-embedding')
    monkeypatch.setenv('STEWARD_EMBEDDING_ENDPOINT', 'http://127.0.0.1:11437/v1')
    monkeypatch.setenv('STEWARD_MODEL_KEY', 'model-only-key')
    if key:
        monkeypatch.setenv('STEWARD_EMBEDDING_KEY', key)
    def handle(request):
        assert str(request.url) == 'http://127.0.0.1:11437/v1/embeddings'
        assert request.headers.get('Authorization') == ('Bearer ' + key if key else None)
        return httpx.Response(200, json={'data': [{'index': 0, 'embedding': [1.0, 0.0]}]})
    transport(handle)
    result = providers.embed('repo', 'snapshot', [{'content': 'evidence'}])
    assert result[0]['embedding'] == [1.0, 0.0]


@pytest.mark.parametrize('raw,code', [
    ([], 'invalid_schema'),
    (adaption_response(usage=[]), 'invalid_usage'),
    (adaption_response(status='incomplete', incomplete_details='secret malformed detail'), 'invalid_schema'),
])
def test_malformed_provider_envelopes_have_redacted_bounded_errors(transport, raw, code):
    transport(lambda request: httpx.Response(200, json=raw))
    with pytest.raises(providers.ProviderError) as error:
        review()
    assert error.value.code == code
    assert 1 <= error.value.telemetry['attempts'] <= 2
    assert 'secret' not in str(error.value) + json.dumps(error.value.telemetry)


def test_native_key_never_follows_redirect(transport):
    calls = []
    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={'Location': 'https://custom.example/steal'})
    transport(handle)
    with pytest.raises(providers.ProviderError, match='http_302'):
        review()
    assert calls == ['https://api.prod.adaptionlabs.ai/api/v1/responses']


def test_blank_optional_small_model_falls_back_to_selected_main_model():
    db.put('provider_settings', {'id': 'default', 'kind': 'adaption', 'small_model': ''})
    assert providers.route('summary')[0] == 'test-model'


def test_no_embedding_model_keeps_existing_lexical_fallback(transport):
    transport(lambda request: pytest.fail('No embedding model means no transport'))
    assert providers.embed('repo', 'snapshot', [{'content': 'evidence'}]) == []


@pytest.mark.parametrize('kind', ['adaption', 'huggingface'])
def test_sponsor_catalog_does_not_select_a_model_implicitly(monkeypatch, transport, kind):
    monkeypatch.setenv('STEWARD_MODEL_PROVIDER', kind)
    monkeypatch.delenv('STEWARD_MODEL')
    transport(lambda request: httpx.Response(200, json={'data': [{'id': 'some-hosted-model'}]}))
    result = providers.health()
    assert result['model'] == '' and result['available'] is False
    assert result['installed_models'] == ['some-hosted-model']
