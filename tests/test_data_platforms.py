"""Cloud boundaries are exercised with in-memory transports, never live accounts."""
import importlib
import json
from contextlib import contextmanager

import httpx
import pytest

from backend import db, providers, retrieval


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import os
    for key in list(os.environ):
        if key.startswith(('STEWARD_ATLAS_', 'STEWARD_DATABRICKS_')):
            monkeypatch.delenv(key)
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init()


def platform():
    return importlib.import_module('backend.data_platforms')


def atlas(monkeypatch):
    p = platform()
    for key, value in {'ENABLED': '1', 'URI': 'mongodb+srv://user:never-show-this@cluster.mongodb.net/',
                       'DIMENSIONS': '3'}.items():
        monkeypatch.setenv('STEWARD_ATLAS_' + key, value)
    monkeypatch.setattr(providers, 'configuration', lambda: {
        'kind': 'ollama', 'endpoint': 'http://127.0.0.1:11434',
        'embedding_endpoint': '', 'embedding_model': 'test-model'})
    monkeypatch.setattr(providers, 'embed', lambda repo, snap, chunks: [
        {**item, 'embedding': [1.0, 0.0, 0.0]} for item in chunks])
    class Collection:
        documents = {}
        hits = None
        queryable = True
        definition = p.atlas_index_definition()
        pipeline = None
        def list_search_indexes(self, name):
            return [{'name': name, 'type': 'vectorSearch', 'queryable': self.queryable,
                     'latestDefinition': self.definition}]
        def replace_one(self, condition, document, upsert):
            assert condition == {'_id': document['_id']} and upsert
            self.documents[document['_id']] = document
        def aggregate(self, pipeline, **kwargs):
            self.pipeline = pipeline
            return self.hits if self.hits is not None else [
                {**document, 'score': 0.75} for document in self.documents.values()]
    collection = Collection()
    collection.documents = {}
    @contextmanager
    def connect(config):
        yield collection
    monkeypatch.setattr(p, '_atlas_collection', connect)
    return p, collection


def source(files=None):
    files = files or [{'path': 'service.py', 'content': 'def calculate():\n    return 42',
                       'hash': 'a' * 64, 'status': 'indexed', 'symbols': []}]
    db.put('repositories', {'id': 'repo-private-name', 'snapshot': 'snapshot-private-name',
                           'index': {'files': files, 'edges': []}})
    return 'repo-private-name', 'snapshot-private-name'


def databricks(monkeypatch, response=None):
    p = platform()
    for key, value in {'ENABLED': '1', 'HOST': 'https://dbc-test.cloud.databricks.com',
                       'TOKEN': 'never-show-this', 'WAREHOUSE_ID': '0123456789abcdef'}.items():
        monkeypatch.setenv('STEWARD_DATABRICKS_' + key, value)
    requests = []
    def transport(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response or {'status': {'state': 'SUCCEEDED'}})
    original = httpx.Client
    monkeypatch.setattr(p.httpx, 'Client', lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(transport)))
    return p, requests


def run_records(status='complete'):
    db.put('jobs', {'id': 'private-job', 'repo_id': 'private-repo', 'type': 'fix',
                    'title': 'private title', 'description': 'private description'})
    db.put('notes', {'job_id': 'private-job', 'text': 'private team note'})
    db.put('revisions', {'id': 'revision', 'job_id': 'private-job', 'patch_hash': 'b' * 64,
                         'base_snapshot': 'base', 'source_snapshot_id': 'source',
                         'input_fingerprint': 'workflow', 'assessment': {'passed': True}, 'review': {'approved': True}})
    for phase, state in [('baseline', 'passed'), ('regression_before', 'failed'), ('candidate', 'passed')]:
        db.put('checks', {'id': phase, 'run_id': 'private-run', 'job_id': 'private-job',
                          'revision_id': 'revision', 'base_snapshot': 'base',
                          'source_snapshot_id': 'source', 'patch_hash': 'b' * 64,
                          'check_id': 'regression', 'phase': phase, 'status': state,
                          'required': True, 'stdout': 'private stdout', 'test_count': 1})
    return db.put('runs', {'id': 'private-run', 'job_id': 'private-job', 'status': status,
                           'revision_id': 'revision', 'base_snapshot': 'base',
                           'started_at': '2026-11-14T12:00:00+00:00',
                           'finished_at': '2026-11-14T12:00:02+00:00',
                           'source_snapshot_id': 'source', 'candidate_patch_hash': 'b' * 64, 'workflow_key': 'workflow',
                           'provider_attempts': [{'attempts': 1, 'latency': 0.025, 'usage': None}],
                           'candidate_diff': 'private source', 'error': 'private error'})


def parameters(request):
    return {item['name']: item.get('value') for item in request['parameters']}


def test_disabled_integrations_never_connect(monkeypatch):
    p = platform()
    monkeypatch.setattr(httpx, 'Client', lambda **kw: pytest.fail('unexpected HTTP'))
    for name in ('atlas', 'databricks'):
        assert p.status()[name]['enabled'] is False
        assert p.status()[name]['state'] == 'disabled'
    for call in (lambda: p.atlas_sync('r', 's'), lambda: p.atlas_search('r', 's', 'query'),
                 lambda: p.databricks_sync('r'), p.databricks_summary):
        with pytest.raises(ValueError, match='disabled'):
            call()


def test_atlas_upsert_never_uploads_plaintext_and_excludes_secrets(monkeypatch):
    p, collection = atlas(monkeypatch)
    repo, snapshot = source([
        {'path': 'good.py', 'content': 'print("local source")', 'hash': 'a' * 64, 'status': 'indexed', 'symbols': []},
        {'path': '.env', 'content': 'plain private value', 'hash': 'b' * 64, 'status': 'indexed', 'symbols': []},
        {'path': 'bad.py', 'content': 'api_key = "abcdefghijklmnop"', 'hash': 'c' * 64, 'status': 'indexed', 'symbols': []}])
    result = p.atlas_sync(repo, snapshot)
    p.atlas_sync(repo, snapshot)
    assert result['uploaded'] == 1 and result['excluded'] == 2
    assert len(collection.documents) == 1
    wire = json.dumps(collection.documents)
    for private in ('good.py', 'local source', 'abcdefghijklmnop', repo, snapshot, 'test-model'):
        assert private not in wire
    assert all(set(document) == {'_id', 'repo_id', 'snapshot_id', 'embedding_space', 'chunk_id', 'embedding'}
               for document in collection.documents.values())


def test_atlas_search_hydrates_only_local_matching_namespace(monkeypatch):
    p, collection = atlas(monkeypatch)
    repo, snapshot = source()
    p.atlas_sync(repo, snapshot)
    record = next(iter(collection.documents.values()))
    collection.hits = [{**record, 'score': 0.75, 'content': 'remote forged source'},
                       {**record, 'repo_id': 'another-repository', 'score': 1.0},
                       {**record, 'snapshot_id': 'another-snapshot', 'score': 1.0},
                       {**record, 'embedding_space': 'another-model', 'score': 1.0},
                       {**record, 'chunk_id': 'unknown', 'score': 1.0},
                       {**record, 'score': float('nan')}]
    result = p.atlas_search(repo, snapshot, 'calculate')
    assert len(result['matches']) == 1
    assert result['matches'][0]['content'] == 'def calculate():\n    return 42'
    assert result['matches'][0]['path'] == 'service.py'
    assert collection.pipeline[0]['$vectorSearch']['filter'] == {
        key: record[key] for key in ('repo_id', 'snapshot_id', 'embedding_space')}


@pytest.mark.parametrize('database', ['ducktape_public', 'DUCKTAPE_PUBLIC', 'another_public'])
def test_private_vector_projection_refuses_public_database_before_connect(monkeypatch, database):
    p, collection = atlas(monkeypatch)
    repo, snapshot = source()
    monkeypatch.setenv('STEWARD_ATLAS_DATABASE', database)
    with pytest.raises(p.DataPlatformError):
        p.atlas_sync(repo, snapshot)
    assert not collection.documents


@pytest.mark.parametrize('vector', [[1, 2], [1, 2, float('nan')], [0, 0, 0], [True, 0, 1]])
def test_atlas_rejects_invalid_vectors_before_upload(monkeypatch, vector):
    p, collection = atlas(monkeypatch)
    repo, snapshot = source()
    monkeypatch.setattr(providers, 'embed', lambda r, s, chunks: [{**item, 'embedding': vector} for item in chunks])
    with pytest.raises(ValueError, match='embedding'):
        p.atlas_sync(repo, snapshot)
    assert not collection.documents


def test_atlas_index_readiness_and_dimension_mismatch(monkeypatch):
    p, collection = atlas(monkeypatch)
    repo, snapshot = source()
    collection.queryable = False
    assert p.atlas_sync(repo, snapshot)['index_ready'] is False
    with pytest.raises(ValueError, match='ready'):
        p.atlas_search(repo, snapshot, 'calculate')
    collection.definition = {'fields': [{'type': 'vector', 'path': 'embedding', 'numDimensions': 99, 'similarity': 'cosine'}]}
    with pytest.raises(ValueError, match='index'):
        p.atlas_sync(repo, snapshot)


def test_status_is_local_and_redacts_credentials(monkeypatch):
    p, _ = atlas(monkeypatch)
    monkeypatch.setenv('STEWARD_DATABRICKS_ENABLED', '1')
    monkeypatch.setenv('STEWARD_DATABRICKS_HOST', 'http://user:never-show-this@localhost/')
    state = p.status()
    assert state['atlas']['configured'] is True
    assert state['databricks']['configured'] is False
    assert 'never-show-this' not in json.dumps(state)
    assert 'mongodb.net' not in json.dumps(state)


def test_databricks_terminal_metrics_keep_failures_and_unknown_usage(monkeypatch):
    p, requests = databricks(monkeypatch)
    run_records('failed')
    result = p.databricks_sync('private-run')
    values = parameters(requests[-1])
    assert result['verified'] is False
    assert values['status'] == 'failed' and values['verified'] == 'false'
    assert values['input_tokens'] is None and values['output_tokens'] is None
    assert values['duration_ms'] == '2000.0'
    wire = json.dumps(requests)
    for forbidden in ('private-run', 'private-repo', 'private title', 'private source', 'private stdout', 'private team note', 'private error'):
        assert forbidden not in wire
    assert 'MERGE INTO' in requests[-1]['statement']
    assert ':run_id' in requests[-1]['statement']
    assert requests[-1]['on_wait_timeout'] == 'CANCEL'


def test_databricks_verified_requires_exact_bound_evidence(monkeypatch):
    p, requests = databricks(monkeypatch)
    run_records()
    assert p.databricks_sync('private-run')['verified'] is True
    first_id = parameters(requests[-1])['run_id']
    db.mutate('checks', 'candidate', lambda item: {**item, 'patch_hash': 'c' * 64})
    assert p.databricks_sync('private-run')['verified'] is False
    assert parameters(requests[-1])['run_id'] == first_id


@pytest.mark.parametrize('status', ['running', 'awaiting_approval', 'needs_input'])
def test_databricks_rejects_unfinished_runs_without_network(monkeypatch, status):
    p, requests = databricks(monkeypatch)
    run_records(status)
    with pytest.raises(ValueError, match='terminal'):
        p.databricks_sync('private-run')
    assert requests == []


@pytest.mark.parametrize('state', ['FAILED', 'RUNNING', 'PENDING', 'CANCELED', 'CLOSED'])
def test_databricks_non_success_never_reports_success_or_raw_errors(monkeypatch, state):
    p, _ = databricks(monkeypatch, {'status': {'state': state, 'error': {'message': 'never-show-this'}}})
    run_records()
    with pytest.raises(ValueError) as error:
        p.databricks_sync('private-run')
    assert 'never-show-this' not in str(error.value)


def test_databricks_summary_returns_bounded_aggregate_rows(monkeypatch):
    response = {'status': {'state': 'SUCCEEDED'}, 'manifest': {'truncated': False,
                'schema': {'columns': [{'name': 'status'}, {'name': 'runs'}, {'name': 'verified_runs'}]}},
                'result': {'data_array': [['failed', '2', '0'], ['complete', '3', '2']]}}
    p, requests = databricks(monkeypatch, response)
    result = p.databricks_summary()
    assert result['rows'] == [{'status': 'failed', 'runs': '2', 'verified_runs': '0'},
                              {'status': 'complete', 'runs': '3', 'verified_runs': '2'}]
    assert 'GROUP BY status' in requests[0]['statement']
    assert requests[0]['row_limit'] <= 100


def test_databricks_rejects_table_sql_injection_without_network(monkeypatch):
    p, requests = databricks(monkeypatch)
    monkeypatch.setenv('STEWARD_DATABRICKS_TABLE', 'workspace.default.runs; DROP TABLE secrets')
    with pytest.raises(ValueError, match='configuration'):
        p.databricks_summary()
    assert requests == []

def test_verified_run_can_have_earlier_failed_candidate_without_counting_it_as_final_evidence(monkeypatch):
    p, requests = databricks(monkeypatch)
    run_records()
    db.put('checks', {'id': 'earlier', 'run_id': 'private-run', 'job_id': 'private-job',
                      'phase': 'candidate', 'patch_hash': 'c' * 64, 'status': 'failed'})
    assert p.databricks_sync('private-run')['verified'] is True
    assert parameters(requests[-1])['checks_count'] == '4'


@pytest.mark.parametrize('field,value', [('source_snapshot_id', 'other'), ('candidate_patch_hash', 'c' * 64),
                                         ('workflow_key', 'other')])
def test_run_revision_binding_cannot_be_replaced(monkeypatch, field, value):
    p, _ = databricks(monkeypatch)
    run_records()
    db.mutate('runs', 'private-run', lambda item: {**item, field: value})
    assert p.databricks_sync('private-run')['verified'] is False


def test_databricks_totals_real_retry_usage_and_latency_seconds(monkeypatch):
    p, requests = databricks(monkeypatch)
    run_records('failed')
    db.mutate('runs', 'private-run', lambda item: {**item, 'provider_attempts': [
        {'attempts': 2, 'latency': 0.025, 'usage': {'input_tokens': 4, 'output_tokens': 2},
         'attempt_log': [{'usage': {'input_tokens': 8, 'output_tokens': 1}},
                         {'usage': {'input_tokens': 4, 'output_tokens': 2}}]},
        {'attempts': 0, 'latency': 0.001, 'cached': True, 'usage': None}],
        'provider_failure': {'attempts': 1, 'latency': 0.05, 'attempt_log': [{'usage': None}]}})
    p.databricks_sync('private-run')
    values = parameters(requests[-1])
    assert values['provider_calls'] == '3'
    assert float(values['provider_latency_ms']) == pytest.approx(75)
    assert values['input_tokens'] is None and values['output_tokens'] is None
    db.mutate('runs', 'private-run', lambda item: {**item, 'provider_failure': None})
    p.databricks_sync('private-run')
    values = parameters(requests[-1])
    assert values['input_tokens'] == '12' and values['output_tokens'] == '3'


def test_required_security_check_is_part_of_verification(monkeypatch):
    p, _ = databricks(monkeypatch)
    run_records()
    db.mutate('jobs', 'private-job', lambda item: {**item, 'type': 'security'})
    check = {**db.get('checks', 'candidate'), 'id': 'security', 'phase': 'advisory_after',
             'check_id': 'osv-status', 'kind': 'security', 'status': 'failed'}
    db.put('checks', check)
    assert p.databricks_sync('private-run')['verified'] is False


def test_secret_bearing_queries_do_not_reach_embedding_provider(monkeypatch):
    p, _ = atlas(monkeypatch)
    monkeypatch.setattr(providers, 'embed', lambda *a: pytest.fail('secret query escaped'))
    with pytest.raises(ValueError, match='credential'):
        p.atlas_search('repo', 'snapshot', 'api_key = "abcdefghijklmnop"')


def test_cloud_errors_do_not_expose_credentials(monkeypatch):
    p, _ = databricks(monkeypatch)
    original = httpx.Client
    def fail(**kwargs):
        raise RuntimeError('never-show-this')
    monkeypatch.setattr(p.httpx, 'Client', fail)
    run_records()
    with pytest.raises(ValueError) as error:
        p.databricks_sync('private-run')
    assert 'never-show-this' not in str(error.value)

@pytest.mark.parametrize('dimensions', ['0', '-1', '8193', '3.5', 'not-an-integer'])
def test_atlas_dimension_configuration_is_validated_locally(monkeypatch, dimensions):
    p = platform()
    monkeypatch.setenv('STEWARD_ATLAS_DIMENSIONS', dimensions)
    with pytest.raises(ValueError, match='dimensions'):
        p.atlas_index_definition()


def test_mongo_client_exception_is_sanitized(monkeypatch):
    import sys
    from types import SimpleNamespace
    p = platform()
    monkeypatch.setenv('STEWARD_ATLAS_ENABLED', '1')
    monkeypatch.setenv('STEWARD_ATLAS_URI', 'mongodb+srv://user:never-show-this@cluster.mongodb.net/')
    monkeypatch.setenv('STEWARD_ATLAS_DIMENSIONS', '3')
    def fail(*args, **kwargs):
        raise RuntimeError('mongodb+srv://user:never-show-this@cluster.mongodb.net/')
    monkeypatch.setitem(sys.modules, 'pymongo', SimpleNamespace(MongoClient=fail))
    with pytest.raises(ValueError) as error:
        with p._atlas_collection(p._atlas_config()):
            pass
    assert 'never-show-this' not in str(error.value)
    assert error.value.code == 'data_platform_error'

def test_databricks_summary_does_not_turn_partial_token_usage_into_total(monkeypatch):
    p, requests = databricks(monkeypatch, {'status': {'state': 'SUCCEEDED'},
        'manifest': {'schema': {'columns': [{'name': 'status'}]}}, 'result': {'data_array': []}})
    p.databricks_summary()
    sql = requests[0]['statement']
    assert 'CASE WHEN COUNT(input_tokens) = COUNT(*) THEN SUM(input_tokens) END AS input_tokens' in sql
    assert 'CASE WHEN COUNT(output_tokens) = COUNT(*) THEN SUM(output_tokens) END AS output_tokens' in sql


def test_failed_validation_stage_usage_is_included_without_run_attempt_entry(monkeypatch):
    p, requests = databricks(monkeypatch)
    run_records('failed')
    db.mutate('runs', 'private-run', lambda item: {**item, 'provider_attempts': [], 'provider_failure': None})
    db.put('run_stages', {'id': 'investigation', 'run_id': 'private-run', 'status': 'failed',
        'telemetry': {'attempts': 1, 'latency': 0.25, 'usage': {'input_tokens': 100, 'output_tokens': 30},
                      'attempt_log': [{'usage': {'input_tokens': 100, 'output_tokens': 30}}]}})
    p.databricks_sync('private-run')
    values = parameters(requests[-1])
    assert values['provider_calls'] == '1'
    assert values['provider_latency_ms'] == '250.0'
    assert values['input_tokens'] == '100' and values['output_tokens'] == '30'


def test_repaired_failed_stage_does_not_duplicate_recorded_calls(monkeypatch):
    p, requests = databricks(monkeypatch)
    run_records()
    complete = {'attempts': 1, 'latency': 0.2, 'usage': {'input_tokens': 2, 'output_tokens': 1},
                'attempt_log': [{'usage': {'input_tokens': 2, 'output_tokens': 1}}]}
    failed = {'attempts': 1, 'latency': 0.1, 'usage': {'input_tokens': 4, 'output_tokens': 1},
              'attempt_log': [{'usage': {'input_tokens': 4, 'output_tokens': 1}}]}
    db.mutate('runs', 'private-run', lambda item: {**item, 'provider_attempts': [complete], 'provider_failure': failed})
    for name, state, telemetry, run_id in [('complete', 'complete', complete, 'private-run'),
                                          ('failed', 'failed', failed, 'private-run'),
                                          ('repaired', 'failed', failed, 'private-run'),
                                          ('other', 'failed', failed, 'another-run')]:
        db.put('run_stages', {'id': name, 'run_id': run_id, 'status': state, 'telemetry': telemetry})
    p.databricks_sync('private-run')
    values = parameters(requests[-1])
    assert values['provider_calls'] == '3'
    assert values['input_tokens'] == '10' and values['output_tokens'] == '3'


def test_unresolved_interrupted_stage_cannot_export_zero_or_partial_usage(monkeypatch):
    p, requests = databricks(monkeypatch)
    run_records('interrupted')
    db.put('run_stages', {'id': 'running', 'run_id': 'private-run', 'status': 'running',
                         'telemetry': {'attempts': 1, 'usage': None}})
    p.databricks_sync('private-run')
    values = parameters(requests[-1])
    for field in ('provider_calls', 'provider_latency_ms', 'input_tokens', 'output_tokens'):
        assert values[field] is None


@pytest.mark.parametrize('status', ['interrupted', 'cancelled_or_superseded'])
def test_historical_interrupted_usage_stays_unknown_after_stage_is_reassigned(monkeypatch, status):
    p, requests = databricks(monkeypatch)
    run_records(status)
    telemetry = {'attempts': 1, 'latency': 0.25, 'usage': {'input_tokens': 100, 'output_tokens': 30}}
    db.mutate('runs', 'private-run', lambda item: {**item, 'provider_attempts': [telemetry]})
    db.put('run_stages', {'id': 'reused-stage', 'run_id': 'resumed-run', 'job_id': 'private-job',
                         'workflow_key': 'workflow', 'status': 'complete', 'telemetry': telemetry,
                         'attempt_history': [telemetry]})
    p.databricks_sync('private-run')
    values = parameters(requests[-1])
    assert values['status'] == status
    for field in ('provider_calls', 'provider_latency_ms', 'input_tokens', 'output_tokens'):
        assert values[field] is None
    db.delete('run_stages', 'reused-stage')
    p.databricks_sync('private-run')
    assert parameters(requests[-1])['provider_calls'] is None


@pytest.mark.parametrize('stage_run', ['private-run', 'resumed-run'])
def test_unattributed_stage_history_prevents_known_usage_for_same_workflow(monkeypatch, stage_run):
    p, requests = databricks(monkeypatch)
    run_records()
    telemetry = {'attempts': 1, 'latency': 0.25, 'usage': {'input_tokens': 100, 'output_tokens': 30}}
    db.mutate('runs', 'private-run', lambda item: {**item, 'provider_attempts': [telemetry]})
    db.put('run_stages', {'id': 'reused-stage', 'run_id': stage_run, 'job_id': 'private-job',
                         'workflow_key': 'workflow', 'status': 'complete', 'telemetry': telemetry,
                         'attempt_history': [telemetry]})
    p.databricks_sync('private-run')
    values = parameters(requests[-1])
    assert values['verified'] == 'true'
    for field in ('provider_calls', 'provider_latency_ms', 'input_tokens', 'output_tokens'):
        assert values[field] is None


def test_unrelated_stage_history_does_not_hide_known_usage(monkeypatch):
    p, requests = databricks(monkeypatch)
    run_records()
    db.put('run_stages', {'id': 'unrelated', 'run_id': 'other-run', 'job_id': 'other-job',
                         'workflow_key': 'workflow', 'status': 'complete', 'attempt_history': [{'attempts': 9}]})
    p.databricks_sync('private-run')
    assert parameters(requests[-1])['provider_calls'] == '1'


@pytest.mark.parametrize('write_concern', ['0', '-1', 'custom'])
def test_atlas_requires_acknowledged_write_concern(monkeypatch, write_concern):
    p, collection = atlas(monkeypatch)
    monkeypatch.setenv('STEWARD_ATLAS_URI',
                      'mongodb+srv://user:password@cluster.mongodb.net/?w=' + write_concern)
    with pytest.raises(ValueError, match='configuration'):
        p.atlas_sync('repo', 'snapshot')
    assert collection.documents == {}
    assert p.status()['atlas']['configured'] is False
