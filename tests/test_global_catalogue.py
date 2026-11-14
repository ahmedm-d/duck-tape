"""Labelled synthetic public catalogue fixtures; no live service evidence."""
import pytest

from backend.global_catalogue import Catalogue, GlobalEntry, normalize_osv, normalize_improvement


def advisory(**changes):
    return dict(id='GHSA-test-1234', aliases=['CVE-2026-1234'], published='2026-01-01T00:00:00Z',
                summary='Fixture advisory', details='Synthetic test evidence',
                affected=[{'package': {'ecosystem': 'PyPI', 'name': 'Example_Package'},
                           'ranges': [{'type': 'ECOSYSTEM', 'events': [{'introduced': '0'}, {'fixed': '2.0'}]}]}], **changes)


def test_stable_revisions_aliases_and_persistence(tmp_path):
    catalogue = Catalogue(tmp_path / 'catalogue.sqlite3')
    first = catalogue.ingest(normalize_osv(advisory(), retrieved_at='2026-11-14T00:00:00Z'))
    again = catalogue.ingest(normalize_osv(advisory(), retrieved_at='2026-11-15T00:00:00Z'))
    assert again.material_revision == first.material_revision == 1
    changed = advisory(); changed['details'] = 'Changed public fact'
    changed['id'] = 'CVE-2026-1234'; changed['aliases'] = ['GHSA-test-1234']
    update = catalogue.ingest(normalize_osv(changed, retrieved_at='2026-11-16T00:00:00Z'))
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
        retrieved_at='2026-11-14T00:00:00Z')
    assert Catalogue(tmp_path / 'c.sqlite3').ingest(entry).kind == 'improvement'
    for field in ['repo_id', 'team_notes', 'permissions']:
        with pytest.raises(ValueError): GlobalEntry.model_validate({**entry.model_dump(), field:'private'})
    with pytest.raises(ValueError): GlobalEntry.model_validate({**entry.model_dump(), 'source_url':'https://127.0.0.1/a'})
    with pytest.raises(ValueError): GlobalEntry.model_validate({**entry.model_dump(), 'published_at':'2026-01-01'})


def test_alias_collision_rolls_back(tmp_path):
    catalogue = Catalogue(tmp_path / 'c.sqlite3')
    first = normalize_osv(advisory(), retrieved_at='2026-11-14T00:00:00Z')
    catalogue.ingest(first)
    second = first.model_copy(update={'canonical_id':'OSV-other', 'aliases':['OSV-other']})
    catalogue.ingest(second)
    with pytest.raises(ValueError):
        catalogue.ingest(first.model_copy(update={'aliases':first.aliases + ['OSV-other']}))
    assert len(catalogue.entries()) == 2


def test_metadata_and_alias_order_do_not_create_revision(tmp_path):
    catalogue = Catalogue(tmp_path / 'c.sqlite3')
    source = advisory(modified='2026-11-01T00:00:00Z')
    first = catalogue.ingest(normalize_osv(source, retrieved_at='2026-11-14T00:00:00Z'))
    source.update(id='CVE-2026-1234', aliases=['GHSA-test-1234'], modified='2026-11-02T00:00:00Z')
    second = catalogue.ingest(normalize_osv(source, retrieved_at='2026-11-14T00:00:00.500Z'))
    assert second.canonical_id == first.canonical_id == 'CVE-2026-1234'
    assert second.material_revision == 1
    assert second.source_content_hash != first.source_content_hash
    source['modified'] = '2026-10-01T00:00:00Z'
    with pytest.raises(ValueError, match='Older source'):
        catalogue.ingest(normalize_osv(source, retrieved_at='2026-11-15T00:00:00Z'))
    assert catalogue.entries()[0].source_modified_at == '2026-11-02T00:00:00Z'


def test_nested_validation_and_export_overflow(tmp_path):
    catalogue = Catalogue(tmp_path / 'c.sqlite3')
    first = normalize_osv(advisory(), retrieved_at='2026-11-14T00:00:00Z')
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
    first = normalize_osv(advisory(), retrieved_at='2026-11-14T00:00:00Z')
    catalogue.ingest(first)
    second = catalogue.ingest(first.model_copy(update={'withdrawn_at':'2026-11-14T01:00:00Z', 'retrieved_at':'2026-11-14T02:00:00Z'}))
    assert second.material_revision == 2
    with sqlite3.connect(catalogue.path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM global_revisions').fetchone()[0] == 2
    with pytest.raises(ValueError, match='Out-of-order'):
        catalogue.ingest(first)
    assert catalogue.entries()[0].withdrawn_at == second.withdrawn_at


def test_failed_refresh_is_visible_without_replacing_evidence(tmp_path):
    catalogue = Catalogue(tmp_path / 'c.sqlite3')
    first = normalize_osv(advisory(), retrieved_at='2026-11-14T00:00:00Z')
    catalogue.ingest(first)
    url = 'https://api.osv.dev/v1/vulns/GHSA-test-1234'
    catalogue.record_operation('ingest', url, 'failed', 'Public source unavailable; prior evidence retained.')
    result = Catalogue(catalogue.path).query('PyPI', 'example-package')
    assert result['source_status'][0]['state'] == 'failed'
    assert result['entries'][0]['freshness'] == 'unknown'
    assert result['entries'][0]['material_revision'] == 1
    catalogue.record_operation('ingest', url, 'succeeded', 'Public source validated.')
    assert catalogue.query('PyPI', 'example-package')['entries'][0]['freshness'] != 'unknown'
