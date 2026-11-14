"""Explicit cloud projections. SQLite remains the authoritative record store."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import re
from urllib.parse import parse_qsl, urlsplit

import httpx

from . import db, providers, repository, retrieval, verification

MAX_CHUNKS = 2000
TERMINAL_RUNS = {'complete', 'failed', 'cancelled_or_superseded', 'interrupted'}
METRIC_TYPES = {
    'run_id': 'STRING', 'repo_id': 'STRING', 'status': 'STRING', 'verified': 'BOOLEAN',
    'finished_at': 'TIMESTAMP', 'duration_ms': 'DOUBLE', 'checks_count': 'BIGINT',
    'passed_checks': 'BIGINT', 'provider_calls': 'BIGINT', 'provider_latency_ms': 'DOUBLE',
    'input_tokens': 'BIGINT', 'output_tokens': 'BIGINT', 'regression_reproduced': 'BOOLEAN',
}


class DataPlatformError(ValueError):
    """Only safe, fixed messages cross the API/CLI boundary."""

    code = "data_platform_error"


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _enabled(name):
    return os.getenv('STEWARD_' + name.upper() + '_ENABLED') == '1'


def _dimensions():
    try:
        value = int(os.getenv('STEWARD_ATLAS_DIMENSIONS', ''))
        if not 1 <= value <= 8192:
            raise ValueError()
        return value
    except ValueError:
        raise DataPlatformError('Atlas dimensions must be an integer from 1 to 8192') from None


def atlas_index_definition():
    """Generate setup JSON locally; this never provisions a cloud index."""
    return {'fields': [
        {'type': 'vector', 'path': 'embedding', 'numDimensions': _dimensions(), 'similarity': 'cosine'},
        *[{'type': 'filter', 'path': name} for name in ('repo_id', 'snapshot_id', 'embedding_space')],
    ]}


def _atlas_config():
    try:
        uri = os.environ['STEWARD_ATLAS_URI']
        parsed = urlsplit(uri)
        options = parse_qsl(parsed.query, keep_blank_values=True)
        if (parsed.scheme != 'mongodb+srv' or not parsed.hostname
                or not parsed.hostname.endswith('.mongodb.net') or parsed.port
                or not parsed.username or not parsed.password or parsed.fragment
                or parsed.path not in ('', '/')
                or any(key.lower() not in {'retrywrites', 'w', 'appname', 'authsource'}
                       for key, _ in options)
                or any(key.lower() == 'w' and value != 'majority' and not re.fullmatch(r'[1-9][0-9]*', value)
                       for key, value in options)):
            raise ValueError()
        values = {name: os.getenv('STEWARD_ATLAS_' + name.upper(), default) for name, default in (
            ('database', 'ducktape'), ('collection', 'evidence_vectors'), ('index', 'evidence_vectors'))}
        if any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', value) for value in values.values()):
            raise ValueError()
        return {**values, 'uri': uri, 'dimensions': _dimensions()}
    except (KeyError, TypeError, ValueError):
        raise DataPlatformError('Atlas configuration is incomplete or invalid') from None


def _databricks_config():
    try:
        host = os.environ['STEWARD_DATABRICKS_HOST'].rstrip('/')
        parsed = urlsplit(host)
        if (parsed.scheme != 'https' or not parsed.hostname
                or not parsed.hostname.endswith(('.cloud.databricks.com', '.azuredatabricks.net', '.gcp.databricks.com'))
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path or parsed.port not in (None, 443)):
            raise ValueError()
        token = os.environ['STEWARD_DATABRICKS_TOKEN']
        warehouse = os.environ['STEWARD_DATABRICKS_WAREHOUSE_ID']
        table = os.getenv('STEWARD_DATABRICKS_TABLE', 'workspace.default.ducktape_runs')
        if (not token.strip() or any(ord(char) < 32 for char in token)
                or not re.fullmatch(r'[A-Za-z0-9-]{1,128}', warehouse)
                or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*', table)):
            raise ValueError()
        return {'host': host, 'token': token, 'warehouse': warehouse, 'table': table}
    except (KeyError, TypeError, ValueError):
        raise DataPlatformError('Databricks configuration is incomplete or invalid') from None


def status():
    """Inspect local configuration without constructing clients or returning secrets."""
    result = {}
    for name, configure in (('atlas', _atlas_config), ('databricks', _databricks_config)):
        enabled = _enabled(name)
        try:
            configure()
            configured = True
        except DataPlatformError:
            configured = False
        state = 'disabled' if not enabled else 'configured' if configured else 'needs_configuration'
        detail = ('Disabled; no cloud requests.' if not enabled else
                  'Configured locally; connection not checked.' if configured else
                  'Required server environment settings are missing or invalid.')
        result[name] = {'enabled': enabled, 'configured': configured, 'state': state, 'detail': detail}
    return result


def _require(name):
    if not _enabled(name):
        raise DataPlatformError(name.capitalize() + ' integration is disabled')
    return _atlas_config() if name == 'atlas' else _databricks_config()


@contextmanager
def _atlas_collection(config):
    try:
        from pymongo import MongoClient
    except ImportError:
        raise DataPlatformError('Atlas requires the optional requirements-cloud.txt dependencies') from None
    from .atlas_startup import use_system_trust_store
    use_system_trust_store()  # OS trust store when truststore is installed; verification stays on (antivirus re-signs HTTPS)
    try:
        with MongoClient(config['uri'], tls=True, tlsAllowInvalidCertificates=False,
                         tlsAllowInvalidHostnames=False, serverSelectionTimeoutMS=5000,
                         connectTimeoutMS=5000, socketTimeoutMS=10000, timeoutMS=15000) as client:
            yield client[config['database']][config['collection']]
    except DataPlatformError:
        raise
    except Exception:
        raise DataPlatformError('Atlas request failed; check connectivity, credentials and index setup') from None


def _index_ready(collection, config):
    indexes = list(collection.list_search_indexes(name=config['index']))
    if len(indexes) != 1:
        raise DataPlatformError('Atlas vector index is missing; apply the documented index definition')
    index = indexes[0]
    fields = index.get('latestDefinition', {}).get('fields', [])
    expected = atlas_index_definition()['fields']
    if (index.get('type') != 'vectorSearch' or len(fields) != len(expected)
            or any(field not in fields for field in expected)):
        raise DataPlatformError('Atlas vector index does not match the configured dimensions and namespace filters')
    return index.get('queryable') is True


def _namespace(repo_id, snapshot_id, config):
    settings = providers.configuration()
    if not settings.get('embedding_model'):
        raise DataPlatformError('Configure an embedding model before using Atlas')
    space = [settings.get('kind'), settings.get('embedding_endpoint') or settings.get('endpoint'),
             settings['embedding_model'], config['dimensions']]
    return {'repo_id': _hash(repo_id), 'snapshot_id': _hash(snapshot_id), 'embedding_space': _hash(space)}


def _safe_chunks(repo_id, snapshot_id):
    chunks = retrieval.index_chunks(repo_id, snapshot_id)
    if len(chunks) > MAX_CHUNKS:
        raise DataPlatformError('Atlas prototype supports at most 2000 chunks per snapshot')
    # Exclude the whole file: a sensitive assignment may span adjacent chunks.
    index = retrieval.load_index(repo_id, snapshot_id)
    allowed = {item['path'] for item in index['files']
               if isinstance(item.get('content'), str) and not repository.unsafe_path(item['path'])
               and not repository.secret_path(item['path'])
               and not repository.SECRET.search(item['content'])
               and not repository.SECRET_ASSIGNMENT.search(item['content'])}
    safe = [item for item in chunks if item['path'] in allowed]
    return safe, len(chunks) - len(safe)


def _embeddings(repo_id, snapshot_id, chunks, dimensions):
    try:
        encoded = providers.embed(repo_id, snapshot_id, chunks)
    except Exception:
        raise DataPlatformError('Embedding provider unavailable; no Atlas upload completed for this batch') from None
    if len(encoded) != len(chunks):
        raise DataPlatformError('Incomplete embedding batch')
    vectors = []
    for chunk, item in zip(chunks, encoded):
        vector = item.get('embedding')
        if (item.get('chunk_id') != chunk['chunk_id'] or not isinstance(vector, list)
                or len(vector) != dimensions
                or any(type(value) not in (int, float) or not math.isfinite(value) for value in vector)
                or not any(vector)):
            raise DataPlatformError('Invalid embedding dimensions, values or chunk identity')
        vectors.append(vector)
    return vectors


def atlas_sync(repo_id, snapshot_id):
    config = _require('atlas')
    namespace = _namespace(repo_id, snapshot_id, config)
    chunks, excluded = _safe_chunks(repo_id, snapshot_id)
    with _atlas_collection(config) as collection:
        ready = _index_ready(collection, config)
        for start in range(0, len(chunks), 32):
            batch = chunks[start:start + 32]
            vectors = _embeddings(repo_id, snapshot_id, batch, config['dimensions'])
            for chunk, vector in zip(batch, vectors):
                document = {**namespace, 'chunk_id': chunk['chunk_id'], 'embedding': vector,
                            '_id': _hash([namespace, chunk['chunk_id']])}
                collection.replace_one({'_id': document['_id']}, document, upsert=True)
    return {'provider': 'atlas', 'uploaded': len(chunks), 'excluded': excluded, 'index_ready': ready,
            'detail': 'Vectors upserted. Search indexing is asynchronous; new vectors may not yet be searchable.'}


def atlas_search(repo_id, snapshot_id, query, limit=12):
    config = _require('atlas')
    if (not isinstance(query, str) or not query.strip() or len(query.encode()) > 12000
            or type(limit) is not int or not 1 <= limit <= 24):
        raise DataPlatformError('Atlas search requires a query of at most 12 KiB and a limit from 1 to 24')
    if repository.SECRET.search(query) or repository.SECRET_ASSIGNMENT.search(query):
        raise DataPlatformError('Potential credential material in query; Atlas search refused')
    namespace = _namespace(repo_id, snapshot_id, config)
    chunks, _ = _safe_chunks(repo_id, snapshot_id)
    local = {item['chunk_id']: item for item in chunks}
    with _atlas_collection(config) as collection:
        if not _index_ready(collection, config):
            raise DataPlatformError('Atlas vector index is not ready for queries')
        vector = _embeddings(repo_id, snapshot_id, [{'chunk_id': 'query:' + _hash(query), 'content': query}], config['dimensions'])[0]
        pipeline = [{'$vectorSearch': {'index': config['index'], 'path': 'embedding', 'queryVector': vector,
                                      'numCandidates': limit * 20, 'limit': limit, 'filter': namespace}},
                    {'$project': {'_id': 0, 'chunk_id': 1, 'repo_id': 1, 'snapshot_id': 1,
                                  'embedding_space': 1, 'score': {'$meta': 'vectorSearchScore'}}}]
        matches, seen = [], set()
        for hit in collection.aggregate(pipeline, maxTimeMS=10000):
            key, score = hit.get('chunk_id'), hit.get('score')
            if (key not in local or key in seen or any(hit.get(name) != value for name, value in namespace.items())
                    or type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1):
                continue
            matches.append({**local[key], 'score': score})
            seen.add(key)
            if len(matches) == limit:
                break
    return {'provider': 'atlas', 'repo_id': repo_id, 'snapshot_id': snapshot_id, 'matches': matches}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _metrics(run_id):
    run = db.get('runs', run_id)
    if not run:
        raise DataPlatformError('Run not found')
    if run.get('status') not in TERMINAL_RUNS:
        raise DataPlatformError('Only terminal runs can be exported')
    job = db.get('jobs', run.get('job_id'))
    if not job or not job.get('repo_id'):
        raise DataPlatformError('Run repository binding is missing')
    try:
        start, end = [datetime.fromisoformat(run[key]) for key in ('started_at', 'finished_at')]
        if start.tzinfo is None or end.tzinfo is None or end < start:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise DataPlatformError('Terminal run requires valid start and finish timestamps') from None
    checks = [item for item in db.list_records('checks') if item.get('run_id') == run_id
              and item.get('job_id') == job['id']]
    revision = db.get('revisions', run.get('revision_id')) if run.get('revision_id') else None
    bound = bool(revision and revision.get('job_id') == job['id'] and revision.get('id') == run.get('revision_id')
                 and revision.get('base_snapshot') == run.get('base_snapshot')
                 and re.fullmatch(r'[0-9a-f]{64}', revision.get('patch_hash', ''))
                 and revision.get('source_snapshot_id') == run.get('source_snapshot_id')
                 and revision.get('source_snapshot_id')
                 and revision.get('patch_hash') == run.get('candidate_patch_hash')
                 and revision.get('input_fingerprint') == run.get('workflow_key')
                 and revision.get('input_fingerprint'))
    final_checks = [item for item in checks if revision and item.get('revision_id') == revision['id']]
    evidence = [item for item in final_checks if bound
                and all(item.get(name) == revision.get(name) for name in ('patch_hash', 'base_snapshot', 'source_snapshot_id'))]
    assessed = verification.assess(evidence, [])
    required_evidence = [item for item in evidence if item.get('required', True)]
    baseline = [item for item in required_evidence if item.get('phase') == 'baseline']
    candidate = [item for item in required_evidence if item.get('phase') == 'candidate']
    verified = bool(run['status'] == 'complete' and bound and evidence and baseline and candidate
                    and len(evidence) == len(final_checks) and assessed['passed']
                    and all(item.get('status') == 'passed' for item in required_evidence
                            if item.get('phase') != 'regression_before')
                    and revision.get('assessment', {}).get('passed') is True
                    and revision.get('review', {}).get('approved') is True)
    if job.get('type') == 'fix':
        verified = verified and assessed['regression_reproduced'] is True
    if job.get('type') == 'performance':
        verified = verified and bool(assessed['benchmarks']) and all(
            item.get('valid') and item.get('improvement_percent', 0) > 0 for item in assessed['benchmarks'])
    if job.get('type') == 'security':
        verified = verified and any(item.get('kind') == 'security' and item.get('phase') == 'advisory_after'
                                    and item.get('status') == 'passed' for item in required_evidence)
    attempts = run.get('provider_attempts')
    if not isinstance(attempts, list):
        attempts = [run['provider']] if isinstance(run.get('provider'), dict) else []
    attempts = [item for item in attempts if isinstance(item, dict)]
    if isinstance(run.get('provider_failure'), dict):
        attempts.append(run['provider_failure'])
    unmatched = list(attempts)
    # Interrupted stages can be reassigned on recovery, losing their original run binding.
    unresolved = run['status'] in {'interrupted', 'cancelled_or_superseded'}
    for stage in db.list_records('run_stages'):
        same_workflow = (run.get('workflow_key') and stage.get('workflow_key') == run['workflow_key']
                         and stage.get('job_id') == job['id'])
        if stage.get('attempt_history') and (stage.get('run_id') == run_id or same_workflow):
            unresolved = True  # Historical telemetry has no per-run identity.
        if stage.get('run_id') != run_id:
            continue
        telemetry = stage.get('telemetry')
        if stage.get('status') not in {'complete', 'failed'} or not isinstance(telemetry, dict):
            unresolved = True
        elif telemetry in unmatched:
            unmatched.remove(telemetry)
        elif type(telemetry.get('attempts')) is int and _number(telemetry.get('latency')):
            # Failed output validation can leave telemetry only in the stage record.
            attempts.append(telemetry)
        else:
            unresolved = True
    attempts = [item for item in attempts if item.get('attempts') != 0 and not item.get('cached')]
    call_counts = [item.get('attempts') for item in attempts]
    call_count = sum(call_counts) if not unresolved and all(type(value) is int and value >= 0 for value in call_counts) else None
    latencies = [item.get('latency') for item in attempts]
    latency = sum(latencies) * 1000 if not unresolved and latencies and all(_number(value) for value in latencies) else None
    usage_records = []
    for item in attempts:
        log = item.get('attempt_log')
        if isinstance(log, list) and len(log) == item.get('attempts'):
            usage_records.extend(entry.get('usage') if isinstance(entry, dict) else None for entry in log)
        else:
            usage_records.append(item.get('usage') if item.get('attempts') == 1 else None)
    def token_total(field):
        values = [item.get(field) if isinstance(item, dict) else None for item in usage_records]
        if unresolved or not values or any(type(value) is not int or value < 0 for value in values):
            return None
        return sum(values)
    return {'run_id': _hash(run['id']), 'repo_id': _hash(job['repo_id']), 'status': run['status'],
            'verified': bool(verified), 'finished_at': end.astimezone(timezone.utc).isoformat(),
            'duration_ms': (end - start).total_seconds() * 1000, 'checks_count': len(checks),
            'passed_checks': sum(item.get('status') == 'passed' for item in checks),
            'provider_calls': call_count, 'provider_latency_ms': latency,
            'input_tokens': token_total('input_tokens'), 'output_tokens': token_total('output_tokens'),
            'regression_reproduced': assessed['regression_reproduced'] if bound and evidence else None}


def _statement(config, sql, parameters=None):
    payload = {'warehouse_id': config['warehouse'], 'statement': sql, 'parameters': parameters or [],
               'wait_timeout': '30s', 'on_wait_timeout': 'CANCEL', 'disposition': 'INLINE',
               'format': 'JSON_ARRAY', 'row_limit': 100, 'byte_limit': 100000}
    try:
        with httpx.Client(timeout=40, follow_redirects=False, trust_env=False) as client:
            response = client.post(config['host'] + '/api/2.0/sql/statements', json=payload,
                                   headers={'Authorization': 'Bearer ' + config['token']})
            response.raise_for_status()
            if len(response.content) > 1000000:
                raise DataPlatformError('Databricks response exceeded the result limit')
            result = response.json()
        if result.get('status', {}).get('state') != 'SUCCEEDED':
            raise DataPlatformError('Databricks statement did not succeed; inspect the warehouse statement history')
        if result.get('manifest', {}).get('truncated') or result.get('result', {}).get('next_chunk_index') is not None:
            raise DataPlatformError('Databricks result was incomplete; reduce the query result size')
        return result
    except DataPlatformError:
        raise
    except Exception:
        raise DataPlatformError('Databricks request failed; check connectivity, credentials and table setup') from None


def databricks_sync(run_id):
    config = _require('databricks')
    metrics = _metrics(run_id)
    parameters = [{'name': key, 'type': METRIC_TYPES[key],
                   'value': None if value is None else str(value).lower() if type(value) is bool else str(value)}
                  for key, value in metrics.items()]
    select = ', '.join(':' + key + ' AS ' + key for key in metrics)
    sql = (f'MERGE INTO {config["table"]} AS target USING (SELECT {select}) AS source '
           'ON target.run_id = source.run_id WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *')
    _statement(config, sql, parameters)
    return {'provider': 'databricks', 'uploaded': 1, 'status': metrics['status'], 'verified': metrics['verified']}


def databricks_summary():
    config = _require('databricks')
    sql = (f'SELECT status, COUNT(*) AS runs, SUM(CASE WHEN verified THEN 1 ELSE 0 END) AS verified_runs, '
           f'AVG(duration_ms) AS average_duration_ms, '
           f'CASE WHEN COUNT(input_tokens) = COUNT(*) THEN SUM(input_tokens) END AS input_tokens, '
           f'CASE WHEN COUNT(output_tokens) = COUNT(*) THEN SUM(output_tokens) END AS output_tokens '
           f'FROM {config["table"]} GROUP BY status ORDER BY status LIMIT 20')
    result = _statement(config, sql)
    try:
        columns = [item['name'] for item in result['manifest']['schema']['columns']]
        allowed = {'status', 'runs', 'verified_runs', 'average_duration_ms', 'input_tokens', 'output_tokens'}
        rows = result.get('result', {}).get('data_array', [])
        if not set(columns).issubset(allowed) or len(columns) != len(set(columns)) or len(rows) > 20:
            raise ValueError()
        if any(len(row) != len(columns) for row in rows):
            raise ValueError()
        return {'provider': 'databricks', 'rows': [dict(zip(columns, row)) for row in rows]}
    except (KeyError, TypeError, ValueError):
        raise DataPlatformError('Databricks returned an invalid summary') from None
