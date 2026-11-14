"""Deterministic provider boundary tests; no live inference implied."""
import json
import threading
import time

import pytest

from backend import db, providers


@pytest.fixture(autouse=True)
def provider_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init()
    monkeypatch.setenv('STEWARD_MODEL', 'contract-model')
    monkeypatch.setenv('STEWARD_MODEL_ENDPOINT', 'http://127.0.0.1:11434')
    monkeypatch.setenv('STEWARD_MODEL_PROVIDER', 'openai')
    monkeypatch.setenv('STEWARD_CONTEXT_LIMIT', '16000')
    monkeypatch.setenv('STEWARD_MODEL_BACKOFF', '0')


def envelope(kind='openai', output=None, finish='stop', usage=True):
    output = output or {'edits': [{'path': 'a.py', 'content': 'x=1\n'}], 'explanation': 'Fix'}
    if kind == 'ollama':
        return {'message': {'content': json.dumps(output)}, 'done_reason': finish,
                **({'prompt_eval_count': 20, 'eval_count': 10} if usage else {})}
    return {'choices': [{'message': {'content': json.dumps(output)}, 'finish_reason': finish}],
            **({'usage': {'prompt_tokens': 20, 'completion_tokens': 10}} if usage else {})}


@pytest.mark.parametrize('kind', ['openai', 'ollama'])
def test_schema_sent_to_both_transports(monkeypatch, kind):
    monkeypatch.setenv('STEWARD_MODEL_PROVIDER', kind)
    sent = []
    monkeypatch.setattr(providers, 'request', lambda path, payload, **kw: sent.append(payload) or envelope(kind))
    providers.complete('implementation', {'job': 'fix'})
    schema = sent[0]['format'] if kind == 'ollama' else sent[0]['response_format']['json_schema']['schema']
    assert isinstance(schema, dict)
    assert 'anyOf' in schema


@pytest.mark.parametrize('output,task', [
    ({'approved': True, 'findings': [], 'extra': 'ignored'}, 'review'),
    ({'approved': True, 'findings': ['contradictory']}, 'review'),
    ({'approved': False, 'findings': [3]}, 'review'),
    ({'edits': [{'path': 'a', 'content': '', 'surprise': 1}], 'explanation': ''}, 'implementation'),
    ({'answer': 'yes', 'citations': [{'path': 'a', 'start_line': True, 'end_line': 1}]}, 'qa'),
    ({'request_context': 'x', 'edits': []}, 'implementation'),
])
def test_strict_local_validation(output, task):
    with pytest.raises(ValueError):
        providers.validate(output, task)


def test_unsupported_schema_fails_before_transport(monkeypatch):
    monkeypatch.setenv('STEWARD_MODEL_STRUCTURED_OUTPUT', 'false')
    monkeypatch.setattr(providers, 'request', lambda *a, **kw: pytest.fail('transport called'))
    with pytest.raises(ValueError, match='structured'):
        providers.complete('implementation', {})


@pytest.mark.parametrize('kind', ['openai', 'ollama'])
def test_truncated_valid_json_is_failure_with_usage(monkeypatch, kind):
    monkeypatch.setenv('STEWARD_MODEL_PROVIDER', kind)
    monkeypatch.setattr(providers, 'request', lambda *a, **kw: envelope(kind, finish='length'))
    with pytest.raises(ValueError) as error:
        providers.complete('implementation', {})
    assert error.value.telemetry['attempt_log'][0]['usage']['output_tokens'] == 10
    assert error.value.telemetry['attempt_log'][0]['error'] == 'truncated'


def test_context_budget_counts_system_and_reserved_output(monkeypatch):
    monkeypatch.setenv('STEWARD_IMPLEMENTATION_CONTEXT_LIMIT', '4096')
    monkeypatch.setenv('STEWARD_IMPLEMENTATION_MAX_OUTPUT_TOKENS', '4096')
    monkeypatch.setattr(providers, 'request', lambda *a, **kw: pytest.fail('budget exceeded before HTTP'))
    with pytest.raises(ValueError, match='budget'):
        providers.complete('implementation', {'text': 'x' * 100})


def test_missing_usage_stays_null(monkeypatch):
    monkeypatch.setattr(providers, 'request', lambda *a, **kw: envelope(usage=False))
    _, telemetry = providers.complete('implementation', {})
    assert telemetry['usage'] is None


def test_rate_limit_backoff_is_bounded_and_errors_redacted(monkeypatch):
    import urllib.error
    calls = []
    def transport(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError('http://secret:password@host', 429, 'secret body', {}, None)
        return envelope()
    monkeypatch.setattr(providers, 'request', transport)
    _, telemetry = providers.complete('implementation', {})
    assert telemetry['attempts'] == 2
    assert telemetry['attempt_log'][0]['error'] == 'http_429'
    assert 'secret' not in json.dumps(telemetry)


def test_cache_includes_model_knowledge_decisions_feedback(monkeypatch):
    calls = []
    monkeypatch.setattr(providers, 'request', lambda *a, **kw: calls.append(1) or envelope())
    payload = {'knowledge': {'version': 1}, 'decision': 'a', 'feedback': ''}
    providers.complete('implementation', payload)
    assert providers.complete('implementation', payload)[1]['cached']
    for key, value in [('knowledge', {'version': 2}), ('decision', 'b'), ('feedback', 'rejected')]:
        providers.complete('implementation', {**payload, key: value})
    monkeypatch.setenv('STEWARD_MODEL', 'other')
    providers.complete('implementation', payload)
    assert len(calls) == 5


def test_rejected_review_is_never_cached(monkeypatch):
    calls = []
    output = {'approved': False, 'findings': ['a.py:1 returns the wrong value']}
    monkeypatch.setattr(providers, 'request', lambda *a, **kw: calls.append(1) or envelope(output=output))
    providers.complete('review', {})
    providers.complete('review', {})
    assert len(calls) == 2


def test_transport_cancellation_closes_pending_http(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    started = threading.Event()
    release = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            started.set()
            release.wait(3)
            self.send_response(200)
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv('STEWARD_MODEL_ENDPOINT', f'http://127.0.0.1:{server.server_port}')
    cancel = threading.Event()
    cancelled_at = []
    def stop():
        assert started.wait(15)
        cancelled_at.append(time.monotonic())
        cancel.set()
    threading.Thread(target=stop, daemon=True).start()
    begin = time.monotonic()
    try:
        with pytest.raises(ValueError, match='cancel'):
            providers.request('/chat/completions', {}, timeout=5, cancel=cancel)
        assert cancelled_at and time.monotonic() - cancelled_at[0] < 1.5
    finally:
        release.set()
        server.shutdown()
        server.server_close()


def test_typed_operations_review_and_role_routing(monkeypatch):
    from backend.contracts import CancellationToken, ProviderRequest, ProviderResult
    monkeypatch.setenv('STEWARD_REVIEW_MODEL', 'review-only')
    monkeypatch.setenv('STEWARD_REVIEW_MAX_OUTPUT_TOKENS', '256')
    sent = []
    monkeypatch.setattr(providers, 'request', lambda path, payload, **kw: sent.append(payload) or envelope(output={'approved': True, 'findings': []}))
    result = providers.complete(ProviderRequest(task='review', payload={}, scope='repository', failure_count=1), CancellationToken())
    assert isinstance(result, ProviderResult)
    assert sent[0]['model'] == 'review-only'
    assert sent[0]['max_tokens'] == 256
    assert 'previous_failures=1' in result.telemetry['routing_reason']


def test_typed_operation_schema_and_finding_references():
    from backend import model_schemas
    operation = {'op': 'update', 'path': 'a.py', 'expected_hash': 'a'*64, 'destination': None,
                 'content': None, 'replacements': [{'old': 'x=1', 'new': 'x=2'}], 'complete_file': False}
    model_schemas.validate({'edits': [operation], 'explanation': 'Fix'})
    with pytest.raises(ValueError):
        model_schemas.validate({'edits': [{**operation, 'op': 'shell'}], 'explanation': 'Fix'})
    with pytest.raises(ValueError):
        model_schemas.validate({'approved': False, 'findings': [{'message': 'Wrong return', 'reference': {'path': 'a.py', 'start_line': 3, 'end_line': 2}}]}, 'review')


def test_embeddings_scope_dimensions_and_nonfinite(monkeypatch):
    monkeypatch.setenv('STEWARD_EMBEDDING_MODEL', 'embedding-model')
    calls = []
    monkeypatch.setattr(providers, 'request', lambda *a, **kw: calls.append(1) or {'data': [{'index': 0, 'embedding': [0.2, 0.4]}]})
    chunks = [{'path': 'a.py', 'content': 'hello'}]
    first = providers.embed('repo-a', 'snapshot', chunks)
    providers.embed('repo-a', 'snapshot', chunks)
    providers.embed('repo-b', 'snapshot', chunks)
    assert len(calls) == 2
    assert first[0]['embedding'] == [0.2, 0.4]
    monkeypatch.setattr(providers, 'request', lambda *a, **kw: {'data': [{'index': 0, 'embedding': [float('nan')]}]})
    with pytest.raises(ValueError, match='embedding'):
        providers.embed('repo-a', 'different-snapshot', chunks)


def test_embedding_endpoint_and_task_prefix_are_part_of_cache(monkeypatch):
    monkeypatch.setenv('STEWARD_EMBEDDING_MODEL','nomic-embed-text-v1.5')
    monkeypatch.setenv('STEWARD_EMBEDDING_ENDPOINT','http://127.0.0.1:11437/v1')
    sent=[]
    monkeypatch.setattr(providers,'request',lambda path,payload,**kw:sent.append((payload,kw)) or {'data':[{'index':0,'embedding':[1.,0.]}]})
    query=[{'chunk_id':'query:one','content':'needle'}]
    providers.embed('repo','snapshot',query)
    providers.embed('repo','snapshot',query)
    providers.embed('repo','snapshot',[{'chunk_id':'source','content':'needle'}])
    monkeypatch.setenv('STEWARD_EMBEDDING_ENDPOINT','http://127.0.0.1:11438/v1')
    providers.embed('repo','snapshot',query)
    assert len(sent)==3
    assert sent[0][0]['input']==['search_query: needle']
    assert sent[1][0]['input']==['search_document: needle']
    assert sent[0][1]['embedding'] is True


def test_embedding_usage_and_latency_persist_once_per_actual_call(monkeypatch):
    monkeypatch.setenv('STEWARD_EMBEDDING_MODEL','embedding-model')
    monkeypatch.setattr(providers,'request',lambda *args,**kwargs:{'data':[{'index':0,'embedding':[1.,0.]}],'usage':{'prompt_tokens':12,'total_tokens':12}})
    chunks=[{'chunk_id':'one','content':'evidence'}]
    providers.embed('repo','snapshot',chunks)
    providers.embed('repo','snapshot',chunks)
    calls=db.list_records('embedding_runs')
    assert len(calls)==1 and calls[0]['usage']['prompt_tokens']==12
    assert calls[0]['repo_id']=='repo' and calls[0]['snapshot_id']=='snapshot' and calls[0]['latency_ms']>=0


def test_separate_embedding_transport_does_not_send_coding_secret(monkeypatch):
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    received=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length','0')))
            received.append((self.path,self.headers.get('Authorization')))
            self.send_response(200);self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'{}')
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    monkeypatch.setenv('STEWARD_EMBEDDING_ENDPOINT',f'http://127.0.0.1:{server.server_port}/v1')
    monkeypatch.setenv('STEWARD_MODEL_KEY','coding-secret')
    monkeypatch.delenv('STEWARD_EMBEDDING_KEY',raising=False)
    try:
        providers.request('/embeddings',{},embedding=True)
        monkeypatch.setenv('STEWARD_EMBEDDING_KEY','embedding-secret')
        providers.request('/embeddings',{},embedding=True)
        assert received==[('/v1/embeddings',None),('/v1/embeddings','Bearer embedding-secret')]
    finally:
        server.shutdown();server.server_close()


def test_evaluation_gate_cannot_count_blocked_or_missing_attempts():
    from scripts.evaluate_models import evaluate
    manifest = {'gate': {'minimum_completed': 1}, 'tasks': [{'id': 'required', 'mandatory': True}]}
    assert not evaluate(manifest)['qualified']
    assert not evaluate(manifest, lambda task: {'passed': True, 'evidence': [], 'attempts': []})['qualified']


def test_failed_transport_error_never_contains_secret(monkeypatch):
    import urllib.error
    def fail(*a, **kw):
        raise urllib.error.URLError('Bearer sk-private-secret https://user:password@private')
    monkeypatch.setattr(providers, 'request', fail)
    with pytest.raises(providers.ProviderError) as error:
        providers.complete('implementation', {})
    assert len(error.value.telemetry['attempt_log']) == 2
    assert 'password' not in str(error.value) + json.dumps(error.value.telemetry)


def test_pre_cancelled_request_never_hits_cache_or_transport(monkeypatch):
    from backend.contracts import CancellationToken, ProviderRequest
    cancel = CancellationToken()
    cancel.cancel()
    monkeypatch.setattr(providers, 'request', lambda *a, **kw: pytest.fail('cancelled transport'))
    with pytest.raises(providers.ProviderError, match='cancel') as error:
        providers.complete(ProviderRequest(task='review', payload={}), cancel)
    assert error.value.telemetry['attempts'] == 0


def test_server_saved_settings_under_explicit_environment(monkeypatch):
    for name in ('STEWARD_MODEL_ENDPOINT','STEWARD_MODEL_PROVIDER','STEWARD_MODEL','STEWARD_CONTEXT_LIMIT'):
        monkeypatch.delenv(name,raising=False)
    db.put('provider_settings',{'id':'default','endpoint':'http://127.0.0.1:11436/v1','kind':'openai','model':'saved-model','context_limit':16384})
    assert providers.configuration()['model']=='saved-model'
    assert providers.configuration()['context_limit']==16384
    monkeypatch.setenv('STEWARD_MODEL','environment-model')
    monkeypatch.setenv('STEWARD_MODEL_KEY','private-server-key')
    config=providers.configuration()
    assert config['model']=='environment-model'
    assert 'private-server-key' not in json.dumps(config)


def test_saved_settings_tolerate_uninitialized_database(monkeypatch,tmp_path):
    monkeypatch.setattr(db,'DATA_DIR',tmp_path/'uninitialized')
    assert providers.configuration()['model']=='contract-model'


def test_installed_local_runner_only_starts_for_matching_configuration(monkeypatch,tmp_path):
    manifest={'endpoint':'http://127.0.0.1:11436/v1','alias':'installed-model','context_limit':16384,
              'executable':str(tmp_path/'llama-server.exe'),'model_file':str(tmp_path/'model.gguf'),
              'model_sha256':'unused','executable_sha256':'unused','threads':4}
    monkeypatch.setattr(providers,'installed_runtime',lambda:manifest)
    monkeypatch.setattr(providers,'health',lambda:{'available':False})
    monkeypatch.setattr(providers.subprocess,'Popen',lambda *a,**kw:pytest.fail('Mismatched configuration must not start a local process'))
    providers.start_local_runner()


def test_installed_runtime_integrity_is_checked_before_launch(monkeypatch,tmp_path):
    monkeypatch.setenv('STEWARD_MODEL_ENDPOINT','http://127.0.0.1:11436/v1')
    monkeypatch.setenv('STEWARD_MODEL','installed-model')
    monkeypatch.setenv('STEWARD_CONTEXT_LIMIT','16384')
    model=tmp_path/'model.gguf';model.write_bytes(b'corrupt')
    executable=tmp_path/'llama-server.exe';executable.write_bytes(b'fake')
    monkeypatch.setattr(providers,'installed_runtime',lambda:{'endpoint':'http://127.0.0.1:11436/v1','alias':'installed-model','context_limit':16384,
                        'executable':str(executable),'model_file':str(model),'model_sha256':'0'*64,'executable_sha256':'0'*64,'threads':4})
    monkeypatch.setattr(providers,'health',lambda:{'available':False})
    monkeypatch.setattr(providers.subprocess,'Popen',lambda *a,**kw:pytest.fail('Corrupt installation must not execute'))
    result=providers.start_local_runner()
    assert not result['started'] and 'integrity' in result['error']


def test_embedding_runtime_requires_matching_verified_installation(monkeypatch,tmp_path):
    import hashlib
    import socket
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0))
        port=probe.getsockname()[1]
    executable=tmp_path/'llama-server.exe';executable.write_bytes(b'reviewed executable fixture')
    model=tmp_path/'embedding.gguf';model.write_bytes(b'model fixture')
    manifest={'endpoint':f'http://127.0.0.1:{port}/v1','alias':'nomic-embed-text-v1.5','port':port,'context_limit':8192,'threads':2,
        'executable':str(executable),'model_file':str(model),'executable_sha256':hashlib.sha256(executable.read_bytes()).hexdigest(),
        'model_sha256':hashlib.sha256(model.read_bytes()).hexdigest()}
    monkeypatch.setenv('STEWARD_EMBEDDING_ENDPOINT',manifest['endpoint'])
    monkeypatch.setenv('STEWARD_EMBEDDING_MODEL',manifest['alias'])
    monkeypatch.setattr(providers,'installed_runtime',lambda **kwargs:manifest)
    monkeypatch.setattr(providers,'request',lambda *args,**kwargs:{'data':[]})
    launched=[]
    monkeypatch.setattr(providers,'_start_runner',lambda args,*rest:(launched.append(args) or {'started':True}))
    assert providers.start_embedding_runner()['started']
    assert '--embedding' in launched[0] and '--pooling' in launched[0]
    model.write_bytes(b'changed')
    assert not providers.start_embedding_runner()['started'] and len(launched)==1
    monkeypatch.setenv('STEWARD_EMBEDDING_MODEL','different-model')
    assert providers.start_embedding_runner() is None and len(launched)==1
