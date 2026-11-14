"""CLI dispatch contracts; transport and catalogue behavior have separate tests."""
import json
import sys
from types import SimpleNamespace

from scripts import integrations


def test_catalogue_query_uses_explicit_local_database(tmp_path, monkeypatch, capsys):
    calls = []
    class Catalogue:
        def __init__(self, path):
            calls.append(str(path))

        def query(self, ecosystem, package, limit=20):
            calls.append((ecosystem, package, limit))
            return {'entries': [], 'coverage': 'unknown'}

    monkeypatch.setattr(integrations, 'load_environment', lambda: None)
    monkeypatch.setitem(sys.modules, 'backend.global_catalogue', SimpleNamespace(Catalogue=Catalogue))
    path = tmp_path / 'public.sqlite3'
    assert integrations.main(['catalogue-query', '--database', str(path),
                              '--ecosystem', 'PyPI', '--package', 'requests', '--limit', '3']) == 0
    assert calls == [str(path), ('PyPI', 'requests', 3)]
    assert json.loads(capsys.readouterr().out)['coverage'] == 'unknown'


def test_catalogue_status_does_not_open_database(monkeypatch, capsys):
    monkeypatch.setattr(integrations, 'load_environment', lambda: None)
    monkeypatch.setitem(sys.modules, 'backend.global_pipeline', SimpleNamespace(
        status=lambda: {'atlas': {'enabled': False}}))
    assert integrations.main(['catalogue-status']) == 0
    assert json.loads(capsys.readouterr().out)['atlas']['enabled'] is False


def test_catalogue_failure_withholds_source_error(monkeypatch, capsys):
    def fail():
        raise ValueError('credential-secret')
    monkeypatch.setattr(integrations, 'load_environment', lambda: None)
    monkeypatch.setitem(sys.modules, 'backend.global_pipeline', SimpleNamespace(status=fail))
    assert integrations.main(['catalogue-status']) == 1
    output = capsys.readouterr()
    assert 'credential-secret' not in output.err
    assert json.loads(output.err)['status'] == 'failed'


def test_public_import_and_query_persist_across_cli_calls(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(integrations, 'load_environment', lambda: None)
    record = {
        'canonical_id': 'fixture:requests', 'kind': 'improvement', 'title': 'Fixture only',
        'summary': 'A labelled public contract fixture, not a measured improvement.',
        'source_url': 'https://docs.python.org/3.13/whatsnew/3.13.html',
        'published_at': '2024-10-07T00:00:00Z', 'retrieved_at': '2024-10-08T00:00:00Z',
        'source_content_hash': 'a' * 64, 'evidence': ['Python documentation'],
        'affected': [{'ecosystem': 'PyPI', 'package': 'requests'}], 'source_quality': 'fixture',
    }
    source = tmp_path / 'entry.json'
    source.write_text(json.dumps(record), encoding='utf-8')
    database = str(tmp_path / 'catalogue.sqlite3')
    for _ in range(2):
        assert integrations.main(['catalogue-import', '--database', database, '--record', str(source)]) == 0
        assert json.loads(capsys.readouterr().out)['material_revision'] == 1
    assert integrations.main(['catalogue-query', '--database', database, '--ecosystem', 'PyPI',
                              '--package', 'requests']) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result['entries']) == 1
    assert result['entries'][0]['source_quality'] == 'fixture'
    assert result['entries'][0]['freshness'] == 'stale'
    assert result['coverage'] == 'unknown'


def test_partial_publication_is_a_failed_cli_operation(tmp_path, monkeypatch, capsys):
    from backend import global_pipeline
    monkeypatch.setattr(integrations, 'load_environment', lambda: None)
    monkeypatch.setattr(global_pipeline, 'publish_atlas', lambda entries, catalogue: {
        'published': 1, 'failed': 1, 'results': [{'status': 'failed'}]})
    assert integrations.main(['catalogue-publish', '--database', str(tmp_path / 'c.sqlite3')]) == 1
    assert json.loads(capsys.readouterr().out)['failed'] == 1
