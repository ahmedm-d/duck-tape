"""Claude provider contracts through the real anthropic SDK over an in-memory transport, never a live API."""
import json

import anthropic
import httpx2
import pytest

from backend import anthropic_provider, db, providers
from backend.contracts import CancellationToken, ProviderRequest

APPROVED = '{"approved":true,"findings":[]}'


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for name in list(providers.os.environ):
        if name.startswith('STEWARD_') or name in ('HF_TOKEN', 'ADAPTION_API_KEY', 'ANTHROPIC_API_KEY'):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init()
    monkeypatch.setenv('STEWARD_MODEL_PROVIDER', 'anthropic')
    monkeypatch.setenv('STEWARD_MODEL_BACKOFF', '0')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'anthropic-test-secret')


def sse(text, stop='end_turn', thinking=False):
    blocks = ([{'type': 'thinking', 'thinking': '', 'signature': 'sig'}] if thinking else []) + [{'type': 'text', 'text': ''}]
    events = [('message_start', {'type': 'message_start', 'message': {
        'id': 'msg_test', 'type': 'message', 'role': 'assistant', 'model': 'claude-opus-5-5', 'content': [],
        'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 12, 'output_tokens': 1}}})]
    for index, block in enumerate(blocks):
        events.append(('content_block_start', {'type': 'content_block_start', 'index': index, 'content_block': block}))
        if block['type'] == 'text':
            events.append(('content_block_delta', {'type': 'content_block_delta', 'index': index,
                                                   'delta': {'type': 'text_delta', 'text': text}}))
        events.append(('content_block_stop', {'type': 'content_block_stop', 'index': index}))
    events += [('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': stop, 'stop_sequence': None},
                                  'usage': {'output_tokens': 8}}),
               ('message_stop', {'type': 'message_stop'})]
    return ''.join(f'event: {name}\ndata: {json.dumps(data)}\n\n' for name, data in events).encode()


@pytest.fixture
def api(monkeypatch):
    """Install a handler(request) -> httpx2.Response behind the real SDK clients."""
    sent = []
    def install(handler):
        def handle(request):
            body = json.loads(request.content) if request.content else None
            sent.append({'url': str(request.url), 'body': body, 'headers': dict(request.headers)})
            return handler(request, body)
        def client(endpoint, timeout, asynchronous=True):
            if asynchronous:
                return anthropic.AsyncAnthropic(base_url=endpoint, timeout=timeout, max_retries=0,
                                                http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handle)))
            return anthropic.Anthropic(base_url=endpoint, timeout=timeout, max_retries=0,
                                       http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handle)))
        monkeypatch.setattr(anthropic_provider, '_client', client)
        return sent
    return install


def stream(text=APPROVED, **kwargs):
    return lambda request, body: httpx2.Response(200, headers={'content-type': 'text/event-stream'}, content=sse(text, **kwargs))


def review(**kwargs):
    return providers.complete(ProviderRequest(task='review', payload={}, cache=False, **kwargs))


def test_structured_review_uses_messages_api_with_schema_and_validates_locally(api):
    sent = api(stream(thinking=True))
    result = review(max_output_tokens=64)
    assert result.output == {'approved': True, 'findings': []}
    assert result.telemetry['usage'] == {'input_tokens': 12, 'output_tokens': 8}
    assert result.telemetry['model'] == 'claude-opus-5-5' and result.telemetry['attempts'] == 1
    assert providers.capabilities('review')['structured_output_mode'] == 'json_schema'
    [call] = sent
    body = call['body']
    assert call['url'] == 'https://api.anthropic.com/v1/messages'
    assert body['model'] == 'claude-opus-5-5' and body['stream'] is True
    assert body['max_tokens'] == 16000  # thinking counts toward max_tokens, so the reservation covers it
    assert body['output_config']['effort'] == 'high'
    assert body['output_config']['format']['type'] == 'json_schema'
    assert 'Return JSON only' in body['system']
    assert [turn['role'] for turn in body['messages']] == ['user']
    assert not {'temperature', 'top_p', 'top_k', 'thinking'} & set(body)
    assert 'anthropic-test-secret' not in json.dumps(result.model_dump()) + json.dumps(providers.configuration())


def test_schema_sent_to_claude_drops_only_unsupported_keywords():
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['minimum', 'items'],
              'properties': {'minimum': {'type': 'integer', 'minimum': 1},
                             'items': {'type': 'array', 'minItems': 1, 'maxItems': 5,
                                       'items': {'type': 'string', 'minLength': 1, 'maxLength': 9, 'enum': ['a', 'b']}},
                             'choice': {'anyOf': [{'type': 'string', 'maxLength': 3}, {'type': 'null'}]}}}
    cleaned = anthropic_provider.api_schema(schema)
    assert cleaned == {'type': 'object', 'additionalProperties': False, 'required': ['minimum', 'items'],
                       'properties': {'minimum': {'type': 'integer'},
                                      'items': {'type': 'array', 'items': {'type': 'string', 'enum': ['a', 'b']}},
                                      'choice': {'anyOf': [{'type': 'string'}, {'type': 'null'}]}}}
    assert schema['properties']['minimum']['minimum'] == 1  # the caller's full schema is untouched


def test_every_task_schema_is_free_of_unsupported_keywords():
    from backend import model_schemas
    def keys(node):
        if isinstance(node, dict):
            for key, value in node.items():
                yield key
                yield from (keys(value) if key != 'properties' else (k for v in value.values() for k in keys(v)))
        elif isinstance(node, list):
            for item in node:
                yield from keys(item)
    for task in ('implementation', 'review', 'plan', 'investigation', 'qa', 'regression', 'summary', 'triage'):
        cleaned = anthropic_provider.api_schema(model_schemas.schema_for(task, legacy=False, payload={}))
        assert not set(keys(cleaned)) & anthropic_provider.UNSUPPORTED_KEYWORDS, task


def test_reply_still_validated_against_full_schema(api):
    api(stream('{"approved":true}'))  # parses as JSON but misses the required findings field
    with pytest.raises(providers.ProviderError) as error:
        review(max_output_tokens=64)
    assert error.value.code == 'invalid_schema'


@pytest.mark.parametrize('stop,code', [('max_tokens', 'truncated'), ('refusal', 'refusal'), ('pause_turn', 'incomplete_finish')])
def test_stop_reasons_map_to_provider_errors(api, stop, code):
    sent = api(stream('{"approved":', stop=stop))
    with pytest.raises(providers.ProviderError) as error:
        review(max_output_tokens=64)
    assert error.value.code == code and len(sent) == 1  # none of these is retried


@pytest.mark.parametrize('status', [429, 529])
def test_rate_limit_and_overload_are_retried_once(api, status):
    replies = [httpx2.Response(status, json={'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'busy'}})]
    def handler(request, body):
        return replies.pop(0) if replies else stream()(request, body)
    sent = api(handler)
    result = review(max_output_tokens=64)
    assert result.output['approved'] is True and result.telemetry['attempts'] == 2 and len(sent) == 2
    assert result.telemetry['attempt_log'][0]['error'] == f'http_{status}'


def test_bad_request_is_not_retried(api):
    sent = api(lambda request, body: httpx2.Response(400, json={'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'no'}}))
    with pytest.raises(providers.ProviderError) as error:
        review(max_output_tokens=64)
    assert error.value.code == 'http_400' and len(sent) == 1


def test_cancelled_request_never_calls_the_api(api):
    sent = api(stream())
    token = CancellationToken()
    token.cancel()
    with pytest.raises(providers.ProviderError) as error:
        providers.complete(ProviderRequest(task='review', payload={}, cache=False), token)
    assert error.value.code == 'cancelled' and sent == []


def test_effort_and_reservation_are_configurable_and_checked(api, monkeypatch):
    sent = api(stream())
    monkeypatch.setenv('STEWARD_ANTHROPIC_EFFORT', 'medium')
    monkeypatch.setenv('STEWARD_ANTHROPIC_MAX_TOKENS', '32000')
    review(max_output_tokens=64)
    assert sent[0]['body']['output_config']['effort'] == 'medium' and sent[0]['body']['max_tokens'] == 32000
    monkeypatch.setenv('STEWARD_ANTHROPIC_EFFORT', 'extreme')
    with pytest.raises(providers.ProviderError) as error:
        review(max_output_tokens=64)
    assert error.value.code == 'invalid_effort'


def test_configured_model_and_endpoint_override_defaults(api, monkeypatch):
    sent = api(stream())
    monkeypatch.setenv('STEWARD_MODEL', 'claude-sonnet-5-5')
    monkeypatch.setenv('STEWARD_MODEL_ENDPOINT', 'https://proxy.example.test/')
    result = review(max_output_tokens=64)
    assert result.telemetry['model'] == 'claude-sonnet-5-5' and sent[0]['body']['model'] == 'claude-sonnet-5-5'
    assert sent[0]['url'] == 'https://proxy.example.test/v1/messages'


def test_health_lists_models_from_the_models_api(api):
    sent = api(lambda request, body: httpx2.Response(200, json={
        'data': [{'id': 'claude-opus-5-5', 'type': 'model', 'display_name': 'Claude Opus 5.5', 'created_at': '2026-01-01T00:00:00Z'}],
        'has_more': False, 'first_id': 'claude-opus-5-5', 'last_id': 'claude-opus-5-5'}))
    health = providers.health()
    assert health['available'] is True and health['mode'] == 'live' and health['model'] == 'claude-opus-5-5'
    assert sent[0]['url'].startswith('https://api.anthropic.com/v1/models')


def test_health_reports_unavailable_model(api, monkeypatch):
    api(lambda request, body: httpx2.Response(200, json={'data': [], 'has_more': False, 'first_id': None, 'last_id': None}))
    monkeypatch.setenv('STEWARD_MODEL', 'claude-not-a-model')
    assert providers.health()['available'] is False


def test_embeddings_need_a_separate_endpoint():
    with pytest.raises(providers.ProviderError) as error:
        providers.request('/embeddings', {'model': 'x', 'input': ['y']}, embedding=True)
    assert error.value.code == 'embedding_endpoint_required'


def test_run_py_reads_anthropic_key_from_env_file(monkeypatch, tmp_path):
    import run
    monkeypatch.delenv('ANTHROPIC_API_KEY')
    (tmp_path / '.env').write_text('ANTHROPIC_API_KEY=from-env-file\n', encoding='utf-8')
    monkeypatch.setattr(run, 'ROOT', tmp_path)
    run.load_environment()
    assert providers.os.environ['ANTHROPIC_API_KEY'] == 'from-env-file'
