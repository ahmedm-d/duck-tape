"""Deterministic public transport/cloud contracts; no live service qualification."""
from contextlib import contextmanager
from urllib.parse import urlparse
import copy
import json
from types import SimpleNamespace

import pytest

from backend import global_pipeline as pipeline


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    import os
    for key in list(os.environ):
        if key.startswith('STEWARD_GLOBAL_'):
            monkeypatch.delenv(key)


def test_disabled_never_connects(monkeypatch):
    monkeypatch.setattr(pipeline.data_platforms, '_atlas_collection', lambda config: pytest.fail('connected'))
    monkeypatch.setattr(pipeline.data_platforms, '_statement', lambda *args: pytest.fail('connected'))
    assert pipeline.status()['atlas']['state'] == 'disabled'
    with pytest.raises(ValueError, match='disabled'):
        pipeline.publish_atlas([])
    with pytest.raises(ValueError, match='disabled'):
        pipeline.curate_databricks([])


@pytest.mark.parametrize('url', ['http://api.osv.dev/x', 'https://localhost/x',
    'https://api.osv.dev.attacker.com/x', 'https://api.osv.dev/x?token=secret'])
def test_fetch_rejects_unallowlisted_urls_before_dns(monkeypatch, url):
    monkeypatch.setattr(pipeline.repository, 'validate_public_url', lambda url: pytest.fail('DNS'))
    with pytest.raises(ValueError):
        pipeline.fetch_source(url)


def test_fetch_pins_dns_rejects_redirects_and_limits(monkeypatch):
    url = 'https://api.osv.dev/v1/vulns/TEST-1'
    monkeypatch.setattr(pipeline.repository, 'validate_public_url', lambda value: (urlparse(value), ['8.8.8.8']))
    class Socket:
        expired = False
        def close(self): pass
        def do_handshake(self):
            if self.expired:
                raise OSError('transfer interrupted')
        def shutdown(self, how): self.expired = True
    class Context:
        def wrap_socket(self, raw, server_hostname, do_handshake_on_connect):
            assert server_hostname == 'api.osv.dev'
            assert do_handshake_on_connect is False
            return raw
    class Response:
        status = 200
        data = b'{}'
        def read(self, size):
            assert size == pipeline.MAX_SOURCE_BYTES + 1
            return self.data
    response = Response()
    connections = []
    class Connection:
        def __init__(self, host, **kwargs):
            assert host == 'api.osv.dev'
            connections.append(self)
        def request(self, method, path, headers): assert method == 'GET'
        def getresponse(self): return response
        def close(self): pass
    def connect(address, **kwargs):
        assert address == ('8.8.8.8', 443)
        return Socket()
    monkeypatch.setattr(pipeline.socket, 'create_connection', connect)
    monkeypatch.setattr(pipeline.ssl, 'create_default_context', Context)
    monkeypatch.setattr(pipeline.http.client, 'HTTPSConnection', Connection)
    assert pipeline.fetch_source(url) == b'{}'
    response.status = 302
    with pytest.raises(ValueError, match='redirect'):
        pipeline.fetch_source(url)
    response.status = 200
    response.data = b'x' * (pipeline.MAX_SOURCE_BYTES + 1)
    with pytest.raises(ValueError, match='size'):
        pipeline.fetch_source(url)
    response.data = b'{}'
    class Alarm:
        def __init__(self, seconds, callback):
            assert 0 < seconds <= pipeline.SOURCE_TRANSFER_SECONDS
            self.callback = callback
        def start(self):
            saved = connections[-1].sock
            connections[-1].sock = None
            self.callback()
            assert saved.expired
            connections[-1].sock = saved
        def cancel(self): pass
    with monkeypatch.context() as deadline_patch:
        deadline_patch.setattr(pipeline.threading, 'Timer', Alarm)
        with pytest.raises(ValueError, match='fetch failed'):
            pipeline.fetch_source(url)
    times = iter([0, 1, 31])
    monkeypatch.setattr(pipeline.time, 'monotonic', lambda: next(times))
    with pytest.raises(ValueError, match='deadline'):
        pipeline.fetch_source(url)


def public_entry():
    from backend.global_catalogue import normalize_improvement
    return normalize_improvement({
        'canonical_id': 'improvement:test', 'title': 'Public improvement', 'summary': 'Needs local evaluation.',
        'source_url': 'https://docs.python.org/3.13/whatsnew/3.13.html',
        'published_at': '2024-10-07T00:00:00Z', 'affected': [{'ecosystem': 'CPython', 'package': 'python'}],
        'evidence': ['https://docs.python.org/3.13/whatsnew/3.13.html'],
    }, retrieved_at='2026-11-14T12:00:00Z')


@pytest.fixture
def atlas(monkeypatch):
    monkeypatch.setenv('STEWARD_GLOBAL_ATLAS_ENABLED', '1')
    monkeypatch.setenv('STEWARD_GLOBAL_ATLAS_URI', 'mongodb+srv://user:password@cluster.mongodb.net/?w=majority')
    monkeypatch.setattr(pipeline.time, 'sleep', lambda seconds: None)
    class Collection:
        def __init__(self):
            self.documents = {}
            self.failures = 0
            self.before_replace = None
        def find_one(self, condition):
            return copy.deepcopy(self.documents.get(condition['_id']))
        def update_one(self, condition, update, upsert):
            previous = self.documents.get(condition['_id'])
            if previous and previous['material_hash'] != condition['material_hash']:
                raise ValueError('duplicate _id')
            self.documents.setdefault(condition['_id'], copy.deepcopy(update['$setOnInsert']))
        def insert_one(self, document):
            if self.before_replace:
                callback, self.before_replace = self.before_replace, None
                callback()
            if document['_id'] in self.documents:
                raise ValueError('duplicate _id')
            self.documents[document['_id']] = copy.deepcopy(document)
        def replace_one(self, condition, document, upsert):
            if self.before_replace:
                callback, self.before_replace = self.before_replace, None
                callback()
            if self.failures:
                self.failures -= 1
                raise OSError('secret connection failure')
            previous = self.documents.get(condition['_id'])
            if previous and any(previous.get(key) != value for key, value in condition.items()):
                return SimpleNamespace(matched_count=0)
            self.documents[document['_id']] = copy.deepcopy(document)
            return SimpleNamespace(matched_count=1)
        def find(self, condition):
            assert condition == {'affected': {'$elemMatch': {'ecosystem': 'CPython', 'package': 'python'}}}
            class Cursor(list):
                def sort(self, key, order): return self
                def limit(self, count): return Cursor(self[:count])
                def max_time_ms(self, value): return self
            return Cursor(copy.deepcopy(list(self.documents.values())))
    collections = {name: Collection() for name in ['global_entries', 'global_revisions']}
    @contextmanager
    def connect(config):
        assert config['database'] == 'ducktape_public'
        yield collections[config['collection']]
    monkeypatch.setattr(pipeline.data_platforms, '_atlas_collection', connect)
    return collections


def test_atlas_idempotent_retry_failure_preserves_complete_head(atlas, tmp_path):
    from backend.global_catalogue import Catalogue
    catalogue = Catalogue(tmp_path / 'catalogue.sqlite')
    entry = public_entry()
    assert pipeline.publish_atlas([entry], catalogue=catalogue)['published'] == 1
    assert pipeline.publish_atlas([entry])['published'] == 1
    assert len(atlas['global_revisions'].documents) == 1
    changed = entry.model_copy(update={'material_revision': 2, 'summary': 'Changed public facts.'})
    atlas['global_entries'].failures = 3
    result = pipeline.publish_atlas([changed], catalogue=catalogue)
    assert result['failed'] == 1 and result['results'][0]['attempts'] == 3
    assert 'secret connection failure' not in json.dumps(result)
    assert atlas['global_entries'].documents[entry.canonical_id]['material_revision'] == 1
    assert pipeline.operation_status(catalogue)[0]['state'] == 'failed'
    atlas['global_entries'].failures = 1
    result = pipeline.publish_atlas([changed], catalogue=catalogue)
    assert result['published'] == 1 and result['results'][0]['attempts'] == 2
    assert len(atlas['global_revisions'].documents) == 2
    assert atlas['global_entries'].documents[entry.canonical_id]['material_revision'] == 2
    assert pipeline.publish_atlas([entry])['failed'] == 1


def test_publication_conflicts_and_timestamp_rollback_rejected(atlas):
    entry = public_entry()
    assert pipeline.publish_atlas([entry])['published'] == 1
    assert pipeline.publish_atlas([entry.model_copy(update={'summary': 'Different same revision'})])['failed'] == 1
    assert pipeline.publish_atlas([entry.model_copy(update={'retrieved_at': '2026-11-13T12:00:00Z'})])['failed'] == 1
    assert atlas['global_entries'].documents[entry.canonical_id]['summary'] == entry.summary


def test_cloud_projection_rejects_fixture_private_fields_secrets_and_hosts(atlas):
    entry = public_entry().model_dump()
    for changes in [{'source_quality': 'fixture'}, {'repo_id': 'private'},
                    {'source_url': 'https://unknown.example.org/'},
                    {'summary': 'password=supersecretpassword123'}]:
        with pytest.raises(ValueError, match='Only validated'):
            pipeline.publish_atlas([{**entry, **changes}])
    assert not atlas['global_entries'].documents


def test_atlas_queries_verify_material_hash_and_return_unknown(atlas):
    entry = public_entry()
    pipeline.publish_atlas([entry])
    result = pipeline.query_atlas('CPython', 'python', 1)
    assert result['coverage'] == 'unknown'
    assert result['entries'][0]['version_status'] == 'unknown'
    atlas['global_entries'].documents[entry.canonical_id]['summary'] = 'Corrupted'
    with pytest.raises(ValueError, match='invalid evidence'):
        pipeline.query_atlas('CPython', 'python')


def test_databricks_bound_parameters_separate_table_and_retries(monkeypatch):
    monkeypatch.setenv('STEWARD_GLOBAL_DATABRICKS_ENABLED', '1')
    monkeypatch.setattr(pipeline, '_databricks_config', lambda: {'table': pipeline.PUBLIC_TABLE})
    monkeypatch.setattr(pipeline.time, 'sleep', lambda seconds: None)
    calls = []
    def statement(config, sql, parameters):
        calls.append((sql, parameters))
        assert 'raise_error' in sql and 'CAST(source.retrieved_at AS TIMESTAMP) >= CAST(target.retrieved_at AS TIMESTAMP)' in sql
        assert config['table'] == 'workspace.default.ducktape_global_revisions'
        assert 'Needs local evaluation.' not in sql
        assert {item['name'] for item in parameters} == {'canonical_id', 'material_revision', 'material_hash', 'retrieved_at', 'entry_json'}
        if len(calls) == 1:
            raise OSError('never-show-this-token')
    monkeypatch.setattr(pipeline.data_platforms, '_statement', statement)
    result = pipeline.curate_databricks([public_entry()])
    assert result['curated'] == 1 and result['results'][0]['attempts'] == 2
    assert len(calls) == 2 and calls[0] == calls[1]


def test_ingestion_failure_is_persistent_and_previous_entry_retained(monkeypatch, tmp_path):
    from backend.global_catalogue import Catalogue
    catalogue = Catalogue(tmp_path / 'catalogue.sqlite')
    entry = catalogue.ingest(public_entry())
    monkeypatch.setattr(pipeline, 'fetch_source', lambda url: (_ for _ in ()).throw(ValueError('source unavailable')))
    record = entry.model_dump(exclude={'retrieved_at', 'source_content_hash'})
    with pytest.raises(ValueError):
        pipeline.ingest_improvement(catalogue, record)
    assert catalogue.entries()[0] == entry
    assert pipeline.operation_status(catalogue)[0]['state'] == 'failed'


def test_concurrent_refresh_cannot_roll_back_fractional_timestamp(atlas):
    entry = public_entry()
    assert pipeline.publish_atlas([entry])['published'] == 1
    refreshed = entry.model_copy(update={'retrieved_at': '2026-11-14T12:00:00.500Z'})
    assert pipeline.publish_atlas([refreshed])['published'] == 1
    newer = entry.model_copy(update={'retrieved_at': '2026-11-14T12:00:02Z'})
    intermediate = entry.model_copy(update={'retrieved_at': '2026-11-14T12:00:01Z'})
    atlas['global_entries'].before_replace = lambda: pipeline.publish_atlas([newer])
    assert pipeline.publish_atlas([intermediate])['failed'] == 1
    assert atlas['global_entries'].documents[entry.canonical_id]['retrieved_at'] == newer.retrieved_at


def test_concurrent_first_publication_cannot_overwrite_newer_head(atlas):
    older = public_entry()
    newer = older.model_copy(update={'material_revision': 2, 'summary': 'New material facts',
                                    'retrieved_at': '2026-11-14T12:00:02Z'})
    atlas['global_entries'].before_replace = lambda: pipeline.publish_atlas([newer])
    assert pipeline.publish_atlas([older])['failed'] == 1
    assert atlas['global_entries'].documents[older.canonical_id]['material_revision'] == 2


def test_restore_public_delta_history_validates_hash_and_complete_lineage(monkeypatch, tmp_path):
    from backend.global_catalogue import Catalogue
    monkeypatch.setenv('STEWARD_GLOBAL_DATABRICKS_ENABLED', '1')
    monkeypatch.setattr(pipeline, '_databricks_config', lambda: {'table': pipeline.PUBLIC_TABLE})
    original = public_entry()
    revised = original.model_copy(update={'material_revision': 2, 'summary': 'Materially new facts'})
    docs = pipeline._documents([original]) + pipeline._documents([revised])
    result = {'manifest': {'schema': {'columns': [{'name': 'entry_json'}]}},
              'result': {'data_array': [[json.dumps({key: value for key, value in doc.items() if key != '_id'})] for doc in docs]}}
    monkeypatch.setattr(pipeline.data_platforms, '_statement', lambda *args: result)
    catalogue = Catalogue(tmp_path / 'history.sqlite')
    assert pipeline.restore_databricks(catalogue) == 2
    assert catalogue.entries()[0].material_revision == 2
    result['result']['data_array'] = result['result']['data_array'][1:]
    with pytest.raises(ValueError, match='history is invalid'):
        pipeline.restore_databricks(Catalogue(tmp_path / 'incomplete.sqlite'))


def test_databricks_job_normalizes_sources_and_only_publishes_curated(monkeypatch):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('public_job', Path(__file__).parents[1] / 'integrations/global/databricks_job.py')
    job = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(job)
    monkeypatch.setattr(pipeline, '_require', lambda provider: {})
    monkeypatch.setattr(pipeline, 'restore_databricks', lambda catalogue: 0)
    raw = {'id': 'TEST-OSV', 'published': '2026-01-01T00:00:00Z', 'summary': 'Public advisory',
           'affected': [{'package': {'ecosystem': 'PyPI', 'name': 'example'}, 'versions': ['1.0']}]}
    monkeypatch.setattr(pipeline, 'fetch_source', lambda url: json.dumps(raw).encode())
    curated = []
    def curate(entries, **kwargs):
        curated.extend(entries)
        return {'results': [{'canonical_id': entry.canonical_id, 'status': 'curated' if entry.kind == 'vulnerability' else 'failed'} for entry in entries], 'failed': 1}
    monkeypatch.setattr(pipeline, 'curate_databricks', curate)
    published = []
    monkeypatch.setattr(pipeline, 'publish_atlas', lambda entries, **kwargs: published.extend(entries) or {'published': len(entries)})
    result = job.run([{'kind': 'osv', 'id': 'TEST-OSV'}, {'kind': 'improvement', 'record': public_entry().model_dump(exclude={'retrieved_at', 'source_content_hash'})}], publish=True)
    assert len(curated) == 2
    assert [entry.kind for entry in published] == ['vulnerability']
    assert result['ingestion_failed'] == 0
    assert result['curation']['failed'] == 1
