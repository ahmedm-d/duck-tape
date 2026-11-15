"""Native specialist compatibility tests; transports are fixtures, not live models."""
import hashlib
import json

import pytest

from backend import db, model_schemas, providers, snapshots
from backend.contracts import ProviderRequest


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path / 'store')
    db.init()
    monkeypatch.setenv('STEWARD_MODEL_PROVIDER', 'openai')
    monkeypatch.setenv('STEWARD_MODEL_ENDPOINT', 'http://127.0.0.1:11435/v1')
    monkeypatch.setenv('STEWARD_MODEL', 'controller')
    monkeypatch.setenv('STEWARD_CONTEXT_LIMIT', '64000')
    monkeypatch.setenv('STEWARD_MODEL_BACKOFF', '0')
    monkeypatch.delenv('STEWARD_SPECIALIST_MODE', raising=False)
    return tmp_path


def adapter():
    from backend import agent_adapters
    return agent_adapters


def test_integer_maximum_is_enforced():
    with pytest.raises(ValueError):
        model_schemas.check(65537, {'type': 'integer', 'minimum': 256, 'maximum': 65536})


@pytest.mark.parametrize('name', ['router', 'ticket_parser', 'log_classifier', 'document', 'tool_turn'])
def test_native_schemas_exist(name):
    assert model_schemas.schema_for(name)


def test_ticket_mapping_and_decision():
    result = adapter().parse_completion('ticket_parser', json.dumps({
        'title': 'Fix TLS verification', 'source_tag': 'security', 'change_type': 'security',
        'priority': 'P0', 'candidate_files_or_symbols': ['requests.Session.request'],
    }))
    assert result == {'title': 'Fix TLS verification', 'source': 'security', 'type': 'security',
                      'priority': 'critical', 'candidate_files_or_symbols': ['requests.Session.request']}
    decision = {'decision': {'title': 'Choose behavior', 'options': [
        {'id': 'a', 'label': 'A', 'tradeoff': 'Compatibility'},
        {'id': 'b', 'label': 'B', 'tradeoff': 'Stricter'}], 'recommendation': 'b'}}
    assert adapter().parse_completion('ticket_parser', json.dumps(decision)) == decision
    assert adapter().parse_completion('router', json.dumps(decision)) == decision


@pytest.mark.parametrize('native,canonical', [('TicketParser', 'ticket_parser'),
    ('SecurityReviewer', 'security_reviewer_dpo'), ('HumanDecision', 'human_decision'),
    ('Implementer', 'implementer'), ('TestWriter', 'test_writer'), ('Reviewer', 'reviewer_dpo'),
    ('Retriever', 'retriever'), ('DBMigration', 'db_migration'), ('IaC', 'iac'), ('FrontendUI', 'frontend_ui')])
def test_router_names(native, canonical):
    result = adapter().parse_completion('router', json.dumps({
        'next_agent': native, 'routing_reason': 'Relevant stage', 'risk_level': 'high', 'token_budget': 1024}))
    assert result['next_agent'] == canonical


@pytest.mark.parametrize('role', ['commit_pr_writer', 'test_writer', 'reviewer_dpo',
    'security_reviewer_dpo', 'db_migration', 'iac', 'frontend_ui'])
def test_seven_markdown_specialists(role):
    text = '### Summary\nA bounded change.\n### Cited Symbols\n- app.py:1\n### Confidence Assessment\nChecks pending.\n### Risk Assessment\nReview required.'
    assert adapter().parse_completion(role, text) == {'text': text}


def test_log_classifier_native_format():
    result = {'failure_category': 'baseline_failure', 'failing_tests': ['test_api'],
              'root_cause_summary': 'Existing failure', 'recommended_action': 'Inspect baseline'}
    assert adapter().parse_completion('log_classifier', json.dumps(result)) == result


def test_implementer_dict_and_string_arguments():
    for arguments in ({'path': 'app.py'}, '{"path":"app.py"}'):
        result = adapter().parse_completion('implementer', {'content': None, 'tool_calls': [
            {'id': 'call_1', 'type': 'function', 'function': {'name': 'read_file', 'arguments': arguments}}]})
        assert json.loads(result['tool_calls'][0]['arguments']) == {'path': 'app.py'}
        model_schemas.validate(result, 'tool_turn')


def test_graph_context_is_literal_once_without_mutating_payload(store, monkeypatch):
    block = '<graph_context>\n{"sink_reachable": null}\n</graph_context>'
    payload = {'question': 'Trace quote', 'graph_context': block}
    sent = []
    monkeypatch.setattr(providers, 'request', lambda path, body, **kw: sent.append(body) or {
        'choices': [{'finish_reason': 'stop', 'message': {'content': '{"answer":"Unknown","citations":[]}'}}]})
    providers.complete(ProviderRequest(task='qa', payload=payload))
    text = sent[0]['messages'][1]['content']
    assert text.endswith(block)
    assert text.count('<graph_context>') == 1
    assert 'graph_context' not in json.loads(text[:text.index('\n<graph_context>')])
    assert payload['graph_context'] == block
    messages = adapter().prompt_messages('security_reviewer_dpo', payload)
    assert 'not shown reachable' in messages[0]['content']


def test_unified_diff_binds_snapshot_hash(store):
    source = store / 'source'
    source.mkdir()
    (source / 'app.py').write_bytes(b'value = 1\n')
    snapshot = snapshots.capture('repo', source, {})
    patch = '### Regression\n```diff\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n```'
    result = adapter().document_operations({'text': patch}, snapshot['id'])
    model_schemas.validate(result)
    assert result['edits'][0]['expected_hash'] == hashlib.sha256(b'value = 1\n').hexdigest()
    assert result['edits'][0]['content'] == 'value = 2\n'


@pytest.mark.parametrize('path', ['../outside.py', '.env', '/tmp/app.py', '.git/config'])
def test_diff_rejects_unsafe_paths(store, path):
    source = store / 'source'
    source.mkdir()
    (source / 'app.py').write_text('value = 1\n')
    snapshot = snapshots.capture('repo', source, {})
    text = f'--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+secret = 1\n'
    with pytest.raises(ValueError):
        adapter().document_operations({'text': text}, snapshot['id'])


def test_routing_is_opt_in_and_missing_adapters_fall_back(store, monkeypatch):
    assert not providers.resolve_target('tool_turn', 'implementer')['specialist']
    monkeypatch.setenv('STEWARD_QWEN_ENDPOINT', 'https://example.test/v1')
    monkeypatch.setenv('STEWARD_SPECIALIST_IMPLEMENTER_MODEL', 'trained-implementer')
    target = providers.resolve_target('tool_turn', 'implementer')
    assert target['specialist'] and target['model'] == 'trained-implementer'
    assert target['endpoint'] == 'https://example.test/v1'
    assert not providers.resolve_target('document', 'test_writer')['specialist']
    monkeypatch.setenv('STEWARD_SPECIALIST_MODE', '0')
    assert not providers.resolve_target('tool_turn', 'implementer')['specialist']


def test_native_markdown_transport_has_no_json_constraint(store, monkeypatch):
    monkeypatch.setenv('STEWARD_QWEN_ENDPOINT', 'https://example.test/v1')
    monkeypatch.setenv('STEWARD_SPECIALIST_TEST_WRITER_MODEL', 'trained-tests')
    sent = []
    def transport(path, body, **kw):
        sent.append((body, kw))
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': '### Tests\nPending.'}}]}
    monkeypatch.setattr(providers, 'request', transport)
    req = ProviderRequest(task='document', payload={}, cache=False).model_copy(update={'agent_role': 'test_writer'})
    result = providers.complete(req)
    assert result.output == {'text': '### Tests\nPending.'}
    assert 'response_format' not in sent[0][0]
    assert sent[0][0]['model'] == 'trained-tests'
    assert result.telemetry['agent_role'] == 'test_writer'


def test_pool_credentials_and_databricks_header_are_scoped(store, monkeypatch):
    import httpx
    monkeypatch.setenv('STEWARD_MODEL_KEY', 'controller-secret')
    monkeypatch.setenv('STEWARD_QWEN_KEY', 'pool-secret')
    monkeypatch.setenv('STEWARD_QWEN_ENDPOINT', 'https://workspace.example/serving-endpoints')
    monkeypatch.setenv('STEWARD_QWEN_SERVING_ENDPOINT', 'qwen-pool')
    monkeypatch.setenv('STEWARD_SPECIALIST_IMPLEMENTER_MODEL', 'implementer')
    sent = []
    transport = httpx.MockTransport(lambda req: sent.append(req) or httpx.Response(200, json={}))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real_client(transport=transport, **kw))
    target = providers.resolve_target('tool_turn', 'implementer')
    providers.request('/chat/completions', {}, target=target)
    assert sent[0].headers['Authorization'] == 'Bearer pool-secret'
    assert sent[0].headers['Databricks-Model-Serving-Endpoint'] == 'qwen-pool'
    assert 'secret' not in json.dumps(target)


def test_commit_sections_are_separate_from_verification():
    doc = {'text': '### Summary\nFix quantity.\n### Cited Symbols\npricing.py:2\n### Confidence Assessment\nPending execution.\n### Risk Assessment\nTax rounding.'}
    result = adapter().document_sections(doc)
    assert result['summary'] == 'Fix quantity.'
    assert result['confidence_assessment'] == 'Pending execution.'


def test_tool_session_produces_real_cumulative_diff(store):
    from backend import agent_session
    source = store / 'source'
    source.mkdir()
    (source / 'helper.py').write_bytes(b'value = 1\n')
    snapshot = snapshots.capture('repo', source, {})
    responses = [
        {'text': '', 'tool_calls': [{'id': 'a', 'name': 'read_file', 'arguments': {'path': 'helper.py'}}]},
        {'text': '', 'tool_calls': [{'id': 'b', 'name': 'edit_file', 'arguments': '{"path":"helper.py","old":"value = 1","new":"value = 2"}'}]},
        {'text': 'Updated helper.', 'tool_calls': []}]
    payloads = []
    def invoke(payload, turn):
        payloads.append(payload)
        return adapter().parse_completion('implementer', responses[turn])
    result = agent_session.run(snapshot['id'], {'job': {'title': 'Fix'}}, invoke, store / 'candidate')
    assert result['edits'][0]['content'] == 'value = 2\n'
    assert (source / 'helper.py').read_bytes() == b'value = 1\n'
    assert (store / 'candidate' / 'helper.py').read_bytes() == b'value = 2\n'
    assert len(payloads) == 3
    assert 'value = 1' in payloads[1]['tool_history'][-1]['content']


@pytest.mark.parametrize('name,args', [('bash', {'command': 'whoami'}),
    ('read_file', {'path': '../outside'}), ('write_file', {'path': '.env', 'content': 'key=x'})])
def test_tool_session_rejects_execution_and_escape(store, name, args):
    from backend import agent_session
    source = store / 'source'
    source.mkdir()
    (source / 'app.py').write_bytes(b'value = 1\n')
    snapshot = snapshots.capture('repo', source, {})
    def invoke(payload, turn):
        return adapter().parse_completion('implementer', {'tool_calls': [
            {'name': name, 'arguments': args}]})
    with pytest.raises(ValueError):
        agent_session.run(snapshot['id'], {}, invoke, store / 'candidate')
    assert not (store / 'candidate').exists()


def test_session_turn_budget_and_cancellation(store):
    from backend import agent_session
    from backend.contracts import CancellationToken, CancelledError
    source = store / 'source'
    source.mkdir()
    (source / 'app.py').write_bytes(b'value = 1\n')
    snapshot = snapshots.capture('repo', source, {})
    calls = []
    def invoke(payload, turn):
        calls.append(turn)
        return adapter().parse_completion('implementer', {'tool_calls': [
            {'name': 'read_file', 'arguments': {'path': 'app.py'}}]})
    with pytest.raises(ValueError, match='turn budget'):
        agent_session.run(snapshot['id'], {}, invoke, store / 'candidate')
    assert len(calls) == 8
    token = CancellationToken()
    token.cancel()
    with pytest.raises(CancelledError):
        agent_session.run(snapshot['id'], {}, invoke, store / 'cancelled', cancel=token)
    assert len(calls) == 8


def test_session_cannot_overwrite_controller_test_edits(store):
    from backend import agent_session
    source = store / 'source'
    source.mkdir()
    (source / 'test_app.py').write_bytes(b'assert 1 == 1\n')
    snapshot = snapshots.capture('repo', source, {})
    body = {'files': [{'path': 'test_app.py', 'hash': hashlib.sha256(b'assert 1 == 1\n').hexdigest(),
                      'complete': True, 'content': 'assert 1 == 1\n'}],
            'required_regression_edits': [{'path': 'test_app.py'}]}
    def invoke(payload, turn):
        return adapter().parse_completion('implementer', {'tool_calls': [
            {'name': 'write_file', 'arguments': {'path': 'test_app.py', 'content': '# removed assertions\n'}}]})
    with pytest.raises(ValueError, match='controller test'):
        agent_session.run(snapshot['id'], body, invoke, store / 'candidate')


def test_session_cancels_after_late_provider_response(store):
    from backend import agent_session
    from backend.contracts import CancellationToken, CancelledError
    source = store / 'source'
    source.mkdir()
    snapshot = snapshots.capture('repo', source, {})
    token = CancellationToken()
    def invoke(payload, turn):
        token.cancel()
        return {'text': '', 'tool_calls': [{'id': 'late', 'name': 'write_file',
            'arguments': json.dumps({'path': 'late.py', 'content': 'x=1\n'})}]}
    with pytest.raises(CancelledError):
        agent_session.run(snapshot['id'], {}, invoke, store / 'candidate', cancel=token)
    assert not (store / 'candidate').exists()


def test_diff_rejects_mismatched_preimage_and_preserves_newlines(store):
    source = store / 'source'
    source.mkdir()
    (source / 'app.py').write_bytes(b'value = 1')
    snapshot = snapshots.capture('repo', source, {})
    patch = '--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-value = 1\n\\ No newline at end of file\n+value = 2\n'
    result = adapter().document_operations({'text': patch}, snapshot['id'])
    assert result['edits'][0]['content'] == 'value = 2\n'
    with pytest.raises(ValueError, match='preimage'):
        adapter().document_operations({'text': patch.replace('-value = 1', '-value = 999')}, snapshot['id'])


def test_cache_separates_specialist_roles_on_same_model(store, monkeypatch):
    monkeypatch.setenv('STEWARD_QWEN_ENDPOINT', 'https://fixture.invalid/v1')
    for role in ('reviewer_dpo', 'security_reviewer_dpo'):
        monkeypatch.setenv('STEWARD_SPECIALIST_' + role.upper() + '_MODEL', 'shared-model')
    sent = []
    monkeypatch.setattr(providers, 'request', lambda *a, **kw: sent.append(a) or {
        'choices': [{'finish_reason': 'stop', 'message': {'content': '### Review\nEvidence only.'}}]})
    request = ProviderRequest(task='document', payload={'snapshot': 'fixed'})
    one = request.model_copy(update={'agent_role': 'reviewer_dpo'})
    two = request.model_copy(update={'agent_role': 'security_reviewer_dpo'})
    assert not providers.complete(one).telemetry['cached']
    assert providers.complete(one).telemetry['cached']
    assert not providers.complete(two).telemetry['cached']
    assert len(sent) == 2


@pytest.mark.parametrize('provider', ['openai', 'ollama'])
def test_native_implementer_can_finish_with_plain_assistant_text(store, monkeypatch, provider):
    monkeypatch.setenv('STEWARD_QWEN_ENDPOINT', 'http://127.0.0.1:11435/v1')
    monkeypatch.setenv('STEWARD_QWEN_PROVIDER', provider)
    monkeypatch.setenv('STEWARD_SPECIALIST_IMPLEMENTER_MODEL', 'trained')
    message = {'content': 'Updated helper.', 'tool_calls': []}
    envelope = {'message': message, 'done_reason': 'stop'} if provider == 'ollama' else {
        'choices': [{'message': message, 'finish_reason': 'stop'}]}
    monkeypatch.setattr(providers, 'request', lambda *a, **kw: envelope)
    req = ProviderRequest(task='tool_turn', payload={}, cache=False).model_copy(update={'agent_role': 'implementer'})
    assert providers.complete(req).output == {'text': 'Updated helper.', 'tool_calls': []}
