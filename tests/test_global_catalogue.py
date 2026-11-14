"""Labelled synthetic public catalogue fixtures; no live service evidence."""
import pytest

from backend.global_catalogue import Catalogue, GlobalEntry, default_path, graph_result, normalize_osv, normalize_improvement


def advisory(**changes):
    return dict(id='GHSA-test-1234', aliases=['CVE-2026-1234'], published='2026-01-01T00:00:00Z',
                summary='Fixture advisory', details='Synthetic test evidence',
                affected=[{'package': {'ecosystem': 'PyPI', 'name': 'Example_Package'},
                           'ranges': [{'type': 'ECOSYSTEM', 'events': [{'introduced': '0'}, {'fixed': '2.0'}]}]}], **changes)


def test_stable_revisions_aliases_and_persistence(tmp_path):
    catalogue = Catalogue(tmp_path / 'catalogue.sqlite3')
    first = catalogue.ingest(normalize_osv(advisory(), retrieved_at='2026-11-10T00:00:00Z'))
    again = catalogue.ingest(normalize_osv(advisory(), retrieved_at='2026-11-11T00:00:00Z'))
    assert again.material_revision == first.material_revision == 1
    changed = advisory(); changed['details'] = 'Changed public fact'
    changed['id'] = 'CVE-2026-1234'; changed['aliases'] = ['GHSA-test-1234']
    update = catalogue.ingest(normalize_osv(changed, retrieved_at='2026-11-12T00:00:00Z'))
    assert update.canonical_id == first.canonical_id
    assert update.material_revision == 2
    assert len(Catalogue(catalogue.path).entries()) == 1


def test_queries_expose_unknown_stale_unsupported_and_withdrawn(tmp_path):
    catalogue = Catalogue(tmp_path / 'catalogue.sqlite3')
    entry = normalize_osv(advisory(withdrawn='2026-02-01T00:00:00Z'), retrieved_at='2026-02-01T00:00:00Z')
    catalogue.ingest(entry)
    result = catalogue.query('pypi', 'example.package')
    assert result['coverage'] == 'unknown'
    assert result['entries'][0]['freshness'] == 'stale'
    assert result['entries'][0]['withdrawn_at']
    assert result['entries'][0]['version_status'] == 'unsupported'
    assert catalogue.query('npm', 'example-package')['entries'] == []
    with pytest.raises(ValueError): catalogue.query('PyPI', 'x', limit=101)


def test_strict_public_schema_and_improvement(tmp_path):
    entry = normalize_improvement(dict(canonical_id='improvement:test', title='Fixture improvement',
        summary='Attributed synthetic evidence', source_url='https://docs.python.org/3/whatsnew/3.13.html',
        published_at='2026-01-01T00:00:00Z', affected=[{'ecosystem':'PyPI', 'package':'example'}],
        evidence=['Public release documentation'], limitations=['Benefits need local measurement']),
        retrieved_at='2026-11-10T00:00:00Z')
    assert Catalogue(tmp_path / 'c.sqlite3').ingest(entry).kind == 'improvement'
    for field in ['repo_id', 'team_notes', 'permissions']:
        with pytest.raises(ValueError): GlobalEntry.model_validate({**entry.model_dump(), field:'private'})
    with pytest.raises(ValueError): GlobalEntry.model_validate({**entry.model_dump(), 'source_url':'https://127.0.0.1/a'})
    with pytest.raises(ValueError): GlobalEntry.model_validate({**entry.model_dump(), 'published_at':'2026-01-01'})


def test_alias_collision_rolls_back(tmp_path):
    catalogue = Catalogue(tmp_path / 'c.sqlite3')
    first = normalize_osv(advisory(), retrieved_at='2026-11-10T00:00:00Z')
    catalogue.ingest(first)
    second = first.model_copy(update={'canonical_id':'OSV-other', 'aliases':['OSV-other']})
    catalogue.ingest(second)
    with pytest.raises(ValueError):
        catalogue.ingest(first.model_copy(update={'aliases':first.aliases + ['OSV-other']}))
    assert len(catalogue.entries()) == 2


def test_metadata_and_alias_order_do_not_create_revision(tmp_path):
    catalogue = Catalogue(tmp_path / 'c.sqlite3')
    source = advisory(modified='2026-11-01T00:00:00Z')
    first = catalogue.ingest(normalize_osv(source, retrieved_at='2026-11-10T00:00:00Z'))
    source.update(id='CVE-2026-1234', aliases=['GHSA-test-1234'], modified='2026-11-02T00:00:00Z')
    second = catalogue.ingest(normalize_osv(source, retrieved_at='2026-11-10T00:00:00.500Z'))
    assert second.canonical_id == first.canonical_id == 'CVE-2026-1234'
    assert second.material_revision == 1
    assert second.source_content_hash != first.source_content_hash
    source['modified'] = '2026-10-01T00:00:00Z'
    with pytest.raises(ValueError, match='Older source'):
        catalogue.ingest(normalize_osv(source, retrieved_at='2026-11-11T00:00:00Z'))
    assert catalogue.entries()[0].source_modified_at == '2026-11-02T00:00:00Z'


def test_nested_validation_and_export_overflow(tmp_path):
    catalogue = Catalogue(tmp_path / 'c.sqlite3')
    first = normalize_osv(advisory(), retrieved_at='2026-11-10T00:00:00Z')
    malformed = first.model_dump()
    malformed['affected'][0]['ranges'][0]['events'] = [{'fixed':'2', 'introduced':'1'}]
    with pytest.raises(ValueError): GlobalEntry.model_validate(malformed)
    catalogue.ingest(first)
    catalogue.ingest(first.model_copy(update={'canonical_id':'other', 'aliases':['other']}))
    with pytest.raises(ValueError, match='bounded export'): catalogue.entries(limit=1)
    result = catalogue.query('PyPI', 'example-package', limit=1)
    assert result['truncated'] and len(result['entries']) == 1
    with pytest.raises(ValueError): catalogue.query('PyPI', 'example', limit=True)


def test_withdrawal_is_material_and_revision_history_is_preserved(tmp_path):
    import sqlite3
    catalogue = Catalogue(tmp_path / 'c.sqlite3')
    first = normalize_osv(advisory(), retrieved_at='2026-11-10T00:00:00Z')
    catalogue.ingest(first)
    second = catalogue.ingest(first.model_copy(update={'withdrawn_at':'2026-11-10T01:00:00Z', 'retrieved_at':'2026-11-10T02:00:00Z'}))
    assert second.material_revision == 2
    with sqlite3.connect(catalogue.path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM global_revisions').fetchone()[0] == 2
    with pytest.raises(ValueError, match='Out-of-order'):
        catalogue.ingest(first)
    assert catalogue.entries()[0].withdrawn_at == second.withdrawn_at


def test_failed_refresh_is_visible_without_replacing_evidence(tmp_path):
    catalogue = Catalogue(tmp_path / 'c.sqlite3')
    first = normalize_osv(advisory(), retrieved_at='2026-11-10T00:00:00Z')
    catalogue.ingest(first)
    url = 'https://api.osv.dev/v1/vulns/GHSA-test-1234'
    catalogue.record_operation('ingest', url, 'failed', 'Public source unavailable; prior evidence retained.')
    result = Catalogue(catalogue.path).query('PyPI', 'example-package')
    assert result['source_status'][0]['state'] == 'failed'
    assert result['entries'][0]['freshness'] == 'unknown'
    assert result['entries'][0]['material_revision'] == 1
    catalogue.record_operation('ingest', url, 'succeeded', 'Public source validated.')
    assert catalogue.query('PyPI', 'example-package')['entries'][0]['freshness'] != 'unknown'


def test_catalogue_closes_connections_after_transactions(tmp_path):
    import sqlite3
    catalogue = Catalogue(tmp_path / 'c.sqlite3')
    with catalogue._connect() as connection:
        connection.execute('SELECT 1')
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        connection.execute('SELECT 1')


def test_future_source_timestamps_cannot_poison_current_catalogue(tmp_path):
    catalogue = Catalogue(tmp_path / 'c.sqlite3')
    first = catalogue.ingest(normalize_osv(advisory(), retrieved_at='2026-11-10T00:00:00Z'))
    for changes in [{'retrieved_at':'2099-01-01T00:00:00Z'}, {'source_modified_at':'2099-01-01T00:00:00Z'}]:
        with pytest.raises(ValueError):
            catalogue.ingest(first.model_copy(update=changes))
    assert catalogue.entries()[0] == first
    refreshed = catalogue.ingest(first.model_copy(update={'retrieved_at':'2026-11-11T00:00:00Z'}))
    assert refreshed.material_revision == 1


def test_public_graph_has_stable_attributed_relationships_and_revisions(tmp_path):
    catalogue = Catalogue(tmp_path / 'graph.sqlite3')
    entry = catalogue.ingest(normalize_osv(advisory(), retrieved_at='2026-11-10T00:00:00Z'))
    before = catalogue.graph('pypi', 'Example.Package')
    assert before['coverage'] == 'unknown'
    assert {node['type'] for node in before['nodes']} == {'entry', 'package', 'source'}
    assert {edge['type'] for edge in before['edges']} == {'affects', 'supported_by'}
    ids = {node['id'] for node in before['nodes']}
    for edge in before['edges']:
        assert edge['source'] in ids and edge['target'] in ids
        assert edge['canonical_id'] == entry.canonical_id
        assert edge['source_url'] == entry.source_url
        assert edge['material_revision'] == 1
        assert edge['source_content_hash'] == entry.source_content_hash
        assert edge['applicability'] == 'unknown'
    updated = catalogue.ingest(entry.model_copy(update={'summary':'Changed public facts',
        'retrieved_at':'2026-11-11T00:00:00Z', 'withdrawn_at':'2026-11-11T00:00:00Z'}))
    after = catalogue.graph('PyPI', 'example-package')
    assert {node['id'] for node in after['nodes']} == ids
    assert {edge['id'] for edge in after['edges']} == {edge['id'] for edge in before['edges']}
    assert all(edge['material_revision'] == updated.material_revision == 2 for edge in after['edges'])
    evidence = next(node['entry'] for node in after['nodes'] if node['type'] == 'entry')
    assert evidence['withdrawn_at'] and evidence['version_status'] == 'unsupported'
    assert evidence['summary'] == 'Changed public facts'


def test_public_graph_bounds_deduplication_and_unknown_results(tmp_path):
    catalogue = Catalogue(tmp_path / 'graph.sqlite3')
    for number in range(3):
        catalogue.ingest(normalize_improvement({
            'canonical_id':f'improvement:graph-{number}', 'title':'Public improvement',
            'summary':'Public source interpretation', 'source_url':'https://docs.python.org/3/whatsnew/3.13.html',
            'published_at':'2024-10-07T00:00:00Z', 'evidence':['Public release documentation'],
            'affected':[{'ecosystem':'PyPI','package':'example'}, {'ecosystem':'npm','package':'unrelated'}],
        }, retrieved_at='2026-01-01T00:00:00Z'))
    result = catalogue.graph('PyPI', 'example', limit=2)
    assert result['truncated']
    assert len(result['nodes']) == 4  # Two entries share one queried package and primary source.
    assert len(result['edges']) == 4
    assert len([node for node in result['nodes'] if node['type'] == 'package']) == 1
    assert all(node['entry']['freshness'] == 'stale' for node in result['nodes'] if node['type'] == 'entry')
    assert {edge['type'] for edge in result['edges']} == {'applies_to', 'supported_by'}
    empty = catalogue.graph('npm', 'missing')
    assert empty['coverage'] == 'unknown' and not empty['edges']
    assert len(empty['nodes']) == 1 and empty['nodes'][0]['type'] == 'package'
    for limit in (0, 101, True):
        with pytest.raises(ValueError): catalogue.graph('PyPI', 'example', limit=limit)


def test_public_graph_preserves_failed_refresh_and_rejects_private_payload(tmp_path):
    import json
    catalogue = Catalogue(tmp_path / 'graph.sqlite3')
    entry = catalogue.ingest(normalize_osv(advisory(), retrieved_at='2026-11-10T00:00:00Z'))
    catalogue.record_operation('ingest', entry.source_url, 'failed', 'Public fetch failed.')
    result = catalogue.graph('PyPI', 'example-package')
    node = next(node for node in result['nodes'] if node['type'] == 'entry')
    assert node['entry']['freshness'] == 'unknown'
    assert result['source_status'][0]['state'] == 'failed'
    with catalogue._connect() as connection:
        connection.execute('UPDATE global_entries SET payload=? WHERE id=?',
                           (json.dumps({**entry.model_dump(), 'repo_id':'private-repository'}), entry.canonical_id))
    with pytest.raises(ValueError): catalogue.graph('PyPI', 'example-package')


def test_graph_projection_rejects_unbounded_mismatched_and_private_entries(tmp_path):
    catalogue = Catalogue(tmp_path / 'graph.sqlite3')
    catalogue.ingest(normalize_osv(advisory(), retrieved_at='2026-11-10T00:00:00Z'))
    result = catalogue.query('PyPI', 'example-package')
    with pytest.raises(ValueError): graph_result({**result, 'entries':result['entries'] * 101})
    with pytest.raises(ValueError): graph_result({**result, 'package':'unrelated'})
    with pytest.raises(ValueError):
        graph_result({**result, 'entries':[{**result['entries'][0], 'team_notes':'private'}]})


def test_default_catalogue_path_is_server_configurable_without_opening_data(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.delenv('STEWARD_GLOBAL_CATALOGUE_PATH', raising=False)
    assert default_path() == Path(__file__).resolve().parents[1] / 'data/global/catalogue.sqlite3'
    configured = tmp_path / 'isolated' / 'public.sqlite3'
    monkeypatch.setenv('STEWARD_GLOBAL_CATALOGUE_PATH', str(configured))
    assert default_path() == configured
    assert not configured.parent.exists()
