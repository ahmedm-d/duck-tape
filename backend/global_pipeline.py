"""Explicit public-source ingestion and separate public cloud projections."""
import hashlib
import http.client
import json
import os
import re
import socket
import ssl
import time
import threading
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlsplit

from . import data_platforms, repository

MAX_SOURCE_BYTES = 1024 * 1024
MAX_BATCH = 100
SOURCE_TRANSFER_SECONDS = 30
PUBLIC_HOSTS = frozenset({'api.osv.dev', 'osv.dev', 'docs.python.org', 'www.python.org',
                          'react.dev', 'nodejs.org', 'github.com', 'raw.githubusercontent.com'})
PUBLIC_DATABASE = 'ducktape_public'
PUBLIC_TABLE = 'workspace.default.ducktape_global_revisions'
Error = data_platforms.DataPlatformError


def _public_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.hostname not in PUBLIC_HOSTS
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.port not in (None, 443) or any(ord(char) < 33 for char in url)):
            raise ValueError()
    except (TypeError, ValueError):
        raise Error('Public source URL is outside the fixed HTTPS host allowlist') from None


def fetch_source(url: str) -> bytes:
    """No redirects/proxies; pin the address validated by the existing SSRF guard."""
    _public_url(url)
    connection = raw = timer = None
    try:
        parsed, addresses = repository.validate_public_url(url)
        deadline = time.monotonic() + SOURCE_TRANSFER_SECONDS
        context = ssl.create_default_context()
        connection = http.client.HTTPSConnection(parsed.hostname, timeout=20, context=context)
        raw = socket.create_connection((addresses[0], 443), timeout=20)
        connection.sock = context.wrap_socket(raw, server_hostname=parsed.hostname, do_handshake_on_connect=False)
        tls_socket = connection.sock
        def expire():
            try:
                tls_socket.shutdown(socket.SHUT_RDWR)
            except (OSError, AttributeError):
                pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Error('Public source transfer exceeded its total deadline')
        timer = threading.Timer(remaining, expire)
        timer.daemon = True
        timer.start()
        connection.sock.do_handshake()
        connection.request('GET', parsed.path or '/', headers={
            'User-Agent': 'Duck-Tape-public-catalogue/1', 'Accept': 'application/json,text/html'})
        response = connection.getresponse()
        if 300 <= response.status < 400:
            raise Error('Public source redirects are refused; use its canonical URL')
        if response.status != 200:
            raise Error('Public source returned a non-success HTTP status')
        content = response.read(MAX_SOURCE_BYTES + 1)
        if time.monotonic() >= deadline:
            raise Error('Public source transfer exceeded its total deadline')
        if len(content) > MAX_SOURCE_BYTES:
            raise Error('Public source exceeds the size limit')
        return content
    except Error:
        raise
    except Exception:
        raise Error('Public source fetch failed; previous catalogue evidence is retained') from None
    finally:
        if timer is not None:
            timer.cancel()
        if connection is not None:
            connection.close()
        if raw is not None:
            raw.close()


def ingest_osv(catalogue, advisory_id: str):
    from .global_catalogue import normalize_osv
    if not isinstance(advisory_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', advisory_id):
        raise Error('OSV advisory ID is invalid')
    url = 'https://api.osv.dev/v1/vulns/' + advisory_id
    try:
        content = fetch_source(url)
        record = json.loads(content)
        if record.get('id') != advisory_id:
            raise ValueError()
        entry = normalize_osv(record, retrieved_at=_now())
        result = catalogue.ingest(entry)
    except (ValueError, TypeError, AttributeError, KeyError):
        _record_operation(catalogue, 'ingest', url, 'failed', 'Public OSV fetch or validation failed; prior evidence retained.')
        raise Error('OSV response is invalid or does not match the requested ID') from None
    _record_operation(catalogue, 'ingest', url, 'succeeded', 'Public OSV source retrieved and validated.')
    return result


def ingest_improvement(catalogue, record: dict):
    from .global_catalogue import normalize_improvement
    entry = normalize_improvement(record, retrieved_at=_now())
    try:
        content = fetch_source(entry.source_url)
        entry = entry.model_copy(update={'source_content_hash': hashlib.sha256(content).hexdigest()})
        result = catalogue.ingest(entry)
    except ValueError:
        _record_operation(catalogue, 'ingest', entry.source_url, 'failed', 'Public improvement fetch or validation failed; prior evidence retained.')
        raise
    _record_operation(catalogue, 'ingest', entry.source_url, 'succeeded', 'Attributed public improvement retrieved and validated.')
    return result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_operation(catalogue, operation: str, identity: str, state: str, detail: str) -> None:
    if catalogue is not None:
        catalogue.record_operation(operation, identity, state, detail)


def operation_status(catalogue) -> list[dict]:
    return catalogue.operation_status()


def _atlas_config() -> dict:
    try:
        uri = os.environ['STEWARD_GLOBAL_ATLAS_URI']
        parsed = urlsplit(uri)
        options = parse_qsl(parsed.query, keep_blank_values=True)
        if (parsed.scheme != 'mongodb+srv' or not parsed.hostname
                or not parsed.hostname.endswith('.mongodb.net') or parsed.port
                or not parsed.username or not parsed.password or parsed.fragment
                or parsed.path not in ('', '/')
                or any(key.lower() not in {'retrywrites', 'w', 'appname', 'authsource'} for key, _ in options)
                or any(key.lower() == 'w' and value != 'majority' for key, value in options)):
            raise ValueError()
        if os.getenv('STEWARD_ATLAS_DATABASE', 'ducktape') == PUBLIC_DATABASE:
            raise ValueError()
        return {'uri': uri, 'database': PUBLIC_DATABASE, 'collection': 'global_entries'}
    except (KeyError, TypeError, ValueError):
        raise Error('Public Atlas configuration is invalid or overlaps the private database') from None


def _databricks_config() -> dict:
    config = data_platforms._databricks_config()
    if config['table'] == PUBLIC_TABLE:
        raise Error('Public Databricks table must not overlap the run-metrics table')
    return {**config, 'table': PUBLIC_TABLE}


def _require(provider: str) -> dict:
    if os.getenv('STEWARD_GLOBAL_' + provider.upper() + '_ENABLED') != '1':
        raise Error('Public ' + provider + ' integration is disabled')
    return _atlas_config() if provider == 'atlas' else _databricks_config()


def status() -> dict:
    result = {}
    for provider, configure in (('atlas', _atlas_config), ('databricks', _databricks_config)):
        enabled = os.getenv('STEWARD_GLOBAL_' + provider.upper() + '_ENABLED') == '1'
        try:
            configure()
            configured = True
        except Error:
            configured = False
        result[provider] = {'enabled': enabled, 'configured': configured,
                            'state': 'disabled' if not enabled else 'configured' if configured else 'needs_configuration',
                            'connection_checked': False}
    return result


def _documents(entries: list) -> list[dict]:
    from .global_catalogue import GlobalEntry, material_hash
    if not isinstance(entries, list) or len(entries) > MAX_BATCH:
        raise Error('Public cloud batches must contain at most 100 entries')
    documents = []
    for value in entries:
        try:
            entry = GlobalEntry.model_validate(value.model_dump(mode='json') if isinstance(value, GlobalEntry) else value)
            entry.aliases = sorted(set(entry.aliases + [entry.canonical_id]))
            _public_url(entry.source_url)
            if entry.source_quality == 'fixture':
                raise ValueError()
            document = entry.model_dump(mode='json')
            encoded = json.dumps(document, sort_keys=True)
            if (len(encoded.encode()) > MAX_SOURCE_BYTES or repository.SECRET.search(encoded)
                    or repository.SECRET_ASSIGNMENT.search(encoded)):
                raise ValueError()
            documents.append({**document, '_id': entry.canonical_id, 'material_hash': material_hash(entry)})
        except (ValueError, TypeError):
            raise Error('Only validated allowlisted public entries without fixture or secret material may be published') from None
    if len({item['_id'] for item in documents}) != len(documents):
        raise Error('Public cloud batch contains duplicate canonical identities')
    return documents


def _retry(operation) -> tuple[bool, int]:
    for attempt in range(1, 4):
        try:
            operation()
            return True, attempt
        except Exception:
            if attempt < 3:
                time.sleep(0.1 * attempt)
    return False, 3


def publish_atlas(entries: list, *, catalogue=None) -> dict:
    """Atomic per-entry replacement; failed records retain their complete previous revision."""
    config = _require('atlas')
    documents = _documents(entries)
    results = []
    for document in documents:
        def publish():
            with data_platforms._atlas_collection({**config, 'collection': 'global_revisions'}) as revisions:
                revision = {**document, '_id': document['_id'] + ':' + str(document['material_revision'])}
                revisions.update_one({'_id': revision['_id'], 'material_hash': revision['material_hash']},
                                     {'$setOnInsert': revision}, upsert=True)
            with data_platforms._atlas_collection(config) as collection:
                previous = collection.find_one({'_id': document['_id']})
                if previous:
                    if previous['material_revision'] > document['material_revision']:
                        raise Error('A newer public revision is already published')
                    if (previous['material_revision'] == document['material_revision']
                            and previous['material_hash'] != document['material_hash']):
                        raise Error('Public revision content conflict')
                    if datetime.fromisoformat(previous['retrieved_at']) > datetime.fromisoformat(document['retrieved_at']):
                        raise Error('Older retrieval cannot replace the current published evidence')
                condition = {'_id': document['_id']}
                if previous:
                    condition.update(material_revision=previous['material_revision'], material_hash=previous['material_hash'],
                                     retrieved_at=previous['retrieved_at'])
                    result = collection.replace_one(condition, document, upsert=False)
                    if result.matched_count != 1:
                        raise Error('Concurrent public publication changed the current revision; retry')
                else:
                    collection.insert_one(document)
        success, attempts = _retry(publish)
        results.append({'canonical_id': document['_id'], 'material_revision': document['material_revision'],
                        'status': 'published' if success else 'failed', 'attempts': attempts,
                        'detail': 'Complete public revision stored.' if success else
                                  'Publication failed; prior complete revision retained. Check configuration and revision conflicts, then retry.'})
        _record_operation(catalogue, 'atlas', document['_id'], results[-1]['status'], results[-1]['detail'])
    return {'provider': 'atlas', 'published': sum(item['status'] == 'published' for item in results),
            'failed': sum(item['status'] == 'failed' for item in results), 'results': results}


def curate_databricks(entries: list, *, catalogue=None) -> dict:
    """Idempotently retain public material revisions in a separate Delta table."""
    config = _require('databricks')
    documents = _documents(entries)
    results = []
    for document in documents:
        sql = (f'MERGE INTO {config["table"]} AS target USING '
               '(SELECT :canonical_id AS canonical_id, :material_revision AS material_revision, '
               ':material_hash AS material_hash, :retrieved_at AS retrieved_at, :entry_json AS entry_json) AS source '
               'ON target.canonical_id = source.canonical_id AND target.material_revision = source.material_revision '
               "WHEN MATCHED AND target.material_hash <> source.material_hash THEN UPDATE SET entry_json = CAST(raise_error('Public revision content conflict') AS STRING) "
               'WHEN MATCHED AND CAST(source.retrieved_at AS TIMESTAMP) >= CAST(target.retrieved_at AS TIMESTAMP) THEN UPDATE SET * '
               'WHEN NOT MATCHED THEN INSERT *')
        values = {'canonical_id': document['_id'], 'material_revision': document['material_revision'],
                  'material_hash': document['material_hash'], 'retrieved_at': document['retrieved_at'],
                  'entry_json': json.dumps({key: value for key, value in document.items() if key != '_id'}, sort_keys=True)}
        parameters = [{'name': key, 'type': 'BIGINT' if key == 'material_revision' else 'STRING',
                       'value': str(value)} for key, value in values.items()]
        success, attempts = _retry(lambda: data_platforms._statement(config, sql, parameters))
        results.append({'canonical_id': document['_id'], 'material_revision': document['material_revision'],
                        'status': 'curated' if success else 'failed', 'attempts': attempts})
        _record_operation(catalogue, 'databricks', document['_id'], results[-1]['status'],
                          'Public revision curated.' if success else 'Curation failed; prior public revisions retained.')
    return {'provider': 'databricks', 'curated': sum(item['status'] == 'curated' for item in results),
            'failed': sum(item['status'] == 'failed' for item in results), 'results': results}


def query_atlas(ecosystem: str, package: str, limit: int = 20) -> dict:
    from .global_catalogue import GlobalEntry, package_identity, query_result
    config = _require('atlas')
    query_result([], ecosystem, package, limit)
    ecosystem, package = package_identity(ecosystem, package)
    try:
        with data_platforms._atlas_collection(config) as collection:
            documents = list(collection.find({'affected': {'$elemMatch': {'ecosystem': ecosystem, 'package': package}}})
                             .sort('_id', 1).limit(limit + 1).max_time_ms(10000))
        entries = []
        for document in documents:
            payload = {key: value for key, value in document.items() if key not in {'_id', 'material_hash'}}
            entry = GlobalEntry.model_validate(payload)
            validated = _documents([entry])[0]
            if (document.get('_id') != entry.canonical_id or document.get('material_hash') != validated['material_hash']
                    or not any((item.ecosystem, item.package) == (ecosystem, package) for item in entry.affected)):
                raise ValueError()
            entries.append(entry)
        return {**query_result(entries, ecosystem, package, limit), 'provider': 'atlas'}
    except Exception:
        raise Error('Public Atlas query failed or returned invalid evidence; coverage remains unknown') from None


def restore_databricks(catalogue) -> int:
    """Hydrate bounded public revision history before a stateless job normalizes new sources."""
    from .global_catalogue import GlobalEntry, material_hash
    config = _require('databricks')
    result = data_platforms._statement(config, f'SELECT entry_json FROM {config["table"]} '
                                     'ORDER BY canonical_id, material_revision LIMIT 101')
    try:
        if [item['name'] for item in result['manifest']['schema']['columns']] != ['entry_json']:
            raise ValueError()
        rows = result.get('result', {}).get('data_array', [])
        if len(rows) > MAX_BATCH:
            raise ValueError()
        entries = []
        for row in rows:
            if len(row) != 1:
                raise ValueError()
            payload = json.loads(row[0])
            digest = payload.pop('material_hash')
            entry = GlobalEntry.model_validate(payload)
            if digest != material_hash(entry):
                raise ValueError()
            _documents([entry])
            entries.append(entry)
        for entry in entries:
            stored = catalogue.ingest(entry)
            if stored.material_revision != entry.material_revision or material_hash(stored) != material_hash(entry):
                raise ValueError()
        return len(entries)
    except (ValueError, TypeError, KeyError):
        raise Error('Public Delta history is invalid, incomplete or exceeds 100 revisions; no publication permitted') from None
