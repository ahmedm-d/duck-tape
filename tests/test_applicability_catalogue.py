"""Public catalogue (C) -> private local matcher (B): adapter, route and one real OSV record.

Real data: `fixtures/catalogue/real_osv_cve_2024_35195.json` is the GlobalEntry that C's
`integrations/global/demo.py --live-public` normalised from the public OSV record GHSA-9wx4-h78v-vm56
(CVE-2024-35195, PyPI/requests) on 14 Nov 2026. Its source_content_hash equals the hash C tracked in
integrations/global/live-public-evidence.json. The repository is a labelled local demo; Atlas is mocked.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import agent_context, applicability, db, global_pipeline, main
from backend.global_catalogue import Catalogue, GlobalEntry, query_result

from tests.test_chunk_retrieval import register

REAL = GlobalEntry.model_validate_json((Path(__file__).parent / 'fixtures' / 'catalogue' / 'real_osv_cve_2024_35195.json').read_text(encoding='utf-8'))
APP = 'import requests\n\n\ndef fetch(url):\n    return requests.get(url, timeout=5)\n'


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def fresh(entry: GlobalEntry = REAL, **changes) -> GlobalEntry:
    """Same real content with the retrieval time moved to now: freshness is a query-time clock, not content."""
    return entry.model_copy(update={'retrieved_at': now(), **changes})


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path / 'store')
    for name in ('STEWARD_EMBEDDING_MODEL', 'STEWARD_ATLAS_RETRIEVAL', 'STEWARD_GRAPH_ENGINE'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('STEWARD_GLOBAL_CATALOGUE_PATH', str(tmp_path / 'catalogue.sqlite3'))
    db.init()
    pin = '# LABELLED LOCAL DEMO REPOSITORY (not a real project)\nrequests==2.31.0\n'
    snapshot = register(tmp_path, 'demo', {'requirements.txt': pin, 'app.py': APP})
    return TestClient(main.app), snapshot, Catalogue(tmp_path / 'catalogue.sqlite3'), tmp_path


def catalogue_match(api, **body):
    return api.post('/api/repositories/demo/applicability/catalogue', json=body)


def test_adapter_binds_identity_ranges_and_lifecycle_status():
    dump = REAL.model_dump(mode='json')
    converted = applicability.from_global_entry(dump)
    assert (converted['id'], converted['revision'], converted['content_hash']) == ('CVE-2024-35195', 1, REAL.source_content_hash)
    assert converted['kind'] == 'vulnerability' and converted['source_url'] == 'https://osv.dev/vulnerability/GHSA-9wx4-h78v-vm56'
    assert converted['affected'][0]['ranges'] == [{'introduced': '0', 'fixed': '2.32.0'}]
    assert '2.31.0' in converted['affected'][0]['versions'] and converted['status'] == 'active'
    assert 'GHSA-9wx4-h78v-vm56' in converted['aliases'] and 'cwe_ids' not in converted
    assert applicability._entry(converted) is converted
    assert applicability.from_global_entry({**dump, 'withdrawn_at': now(), 'freshness': 'stale'})['status'] == 'withdrawn'
    assert applicability.from_global_entry({**dump, 'freshness': 'stale'})['status'] == 'stale'
    assert applicability.from_global_entry({**dump, 'freshness': 'unknown', 'source_status': 'failed'})['status'] == 'stale'
    conditional = applicability.from_global_entry({**dump, 'conditions': ['Only with verify=False on the first request']})
    assert 'Public condition: Only with verify=False on the first request' in conditional['limitations']
    assert applicability.catalogue_entry(conditional) is conditional and applicability.catalogue_entry(dump)['id'] == 'CVE-2024-35195'


def test_adapter_flattens_event_pairs_and_marks_unreadable_ranges_unsupported():
    def converted(*ranges):
        entry = {**REAL.model_dump(mode='json'), 'affected': [{'ecosystem': 'PyPI', 'package': 'requests', 'versions': [], 'ranges': list(ranges)}]}
        return applicability.from_global_entry(entry)['affected'][0]['ranges']
    pairs = {'type': 'SEMVER', 'events': [{'introduced': '1.0'}, {'fixed': '1.5'}, {'introduced': '2.0'}, {'last_affected': '2.2'}]}
    assert converted(pairs) == [{'introduced': '1.0', 'fixed': '1.5'}, {'introduced': '2.0', 'last_affected': '2.2'}]
    assert converted({'type': 'ECOSYSTEM', 'events': [{'introduced': '3.0'}]}) == [{'introduced': '3.0'}]
    for unreadable in ({'type': 'GIT', 'events': [{'introduced': '0'}, {'fixed': 'abc123'}]},
                       {'type': 'ECOSYSTEM', 'events': [{'fixed': '1.0'}]},
                       {'type': 'ECOSYSTEM', 'events': [{'introduced': '0'}, {'limit': '9'}]}):
        [only] = converted(unreadable)
        assert set(only) == {'unsupported'}


def test_real_record_matches_exact_pin_with_citation_binding_and_no_jobs(env):
    api, snapshot, catalogue, root = env
    stored = catalogue.ingest(fresh())
    response = catalogue_match(api, snapshot_id=snapshot['id'])
    assert response.status_code == 200
    body = response.json()
    assert (body['provider'], body['coverage'], body['errors']) == ('local', 'unknown', [])
    assert body['profile']['snapshot_id'] == snapshot['id'] and body['profile']['package_count'] == 1
    assert body['queried'] == [{'ecosystem': 'PyPI', 'package': 'requests', 'entries': 1, 'truncated': False}]
    [found] = body['matches']
    assert found['status'] == 'matched' and found['entry_status'] == 'active'
    assert (found['entry_id'], found['entry_revision'], found['entry_hash']) == ('CVE-2024-35195', stored.material_revision, REAL.source_content_hash)
    manifest = next(item for item in db.get('repositories', 'demo')['index']['files'] if item['path'] == 'requirements.txt')
    assert found['citations'] == [{'path': 'requirements.txt', 'start_line': 2, 'end_line': 2, 'hash': manifest['hash']}]
    assert (root / 'demo' / 'requirements.txt').read_text(encoding='utf-8').splitlines()[1] == 'requests==2.31.0'
    assert found['evidence'][0]['version'] == '2.31.0' and found['evidence'][0]['fixed_versions'] == ['2.32.0']
    assert applicability.VERSION_LIMIT in found['limitations']
    assert any('Version applicability does not establish exploitability' in item for item in found['limitations'])
    assert 'Catalogue coverage is partial; no match does not mean unaffected.' in body['limitations']
    assert [item['id'] for item in body['entries']] == ['CVE-2024-35195']
    assert db.list_records('jobs') == []


def test_fixed_pin_is_not_matched_and_unmatched_entries_are_not_offered(env):
    api, snapshot, catalogue, root = env
    catalogue.ingest(fresh())
    (root / 'demo' / 'requirements.txt').write_text('requests==2.32.3\n', encoding='utf-8')
    main.refresh_repo('demo')
    body = catalogue_match(api).json()
    assert [item['status'] for item in body['matches']] == ['not_matched'] and not body['matches'][0]['citations']
    assert body['entries'] == [] and body['coverage'] == 'unknown'


def test_unsupported_rule_gives_unknown_never_not_matched(env):
    api, _, catalogue, root = env
    git_only = GlobalEntry.model_validate({**fresh().model_dump(mode='json'), 'canonical_id': 'GIT-RANGE-ONLY', 'aliases': [],
                                           'source_url': 'https://osv.dev/vulnerability/GIT-RANGE-ONLY',
                                           'affected': [{'ecosystem': 'PyPI', 'package': 'requests', 'versions': [],
                                                         'ranges': [{'type': 'GIT', 'events': [{'introduced': '0'}, {'fixed': '1' * 40}]}]}]})
    catalogue.ingest(git_only)
    body = catalogue_match(api).json()
    assert [(item['entry_id'], item['status']) for item in body['matches']] == [('GIT-RANGE-ONLY', 'unknown')]
    assert any('cannot be compared' in item for item in body['matches'][0]['limitations'])
    assert body['entries'] == []
    # Same through the either-shape entry route: a raw catalogue record is converted, still unknown.
    direct = api.post('/api/repositories/demo/applicability', json={'entries': [git_only.model_dump(mode='json')]}).json()
    assert direct['matches'][0]['status'] == 'unknown'


def test_withdrawn_and_stale_records_match_but_are_withheld_from_intake(env):
    api, _, catalogue, _ = env
    catalogue.ingest(fresh(withdrawn_at=now()))
    body = catalogue_match(api).json()
    assert body['matches'][0]['status'] == 'matched' and body['matches'][0]['entry_status'] == 'withdrawn'
    assert any('withheld' in item for item in body['matches'][0]['limitations'])
    [entry] = body['entries']
    response = api.post('/api/repositories/demo/applicability/intake', json={'entry': entry})
    assert response.status_code == 200 and response.json()['jobs'] == [] and 'withdrawn' in response.json()['detail']
    stale = {**REAL.model_dump(mode='json'), 'freshness': 'stale'}
    result = api.post('/api/repositories/demo/applicability/intake', json={'entry': stale}).json()
    assert result['jobs'] == [] and 'stale' in result['detail'] and result['match']['entry_status'] == 'stale'
    assert db.list_records('jobs') == []


def test_as_captured_real_record_is_active_until_seven_days_old(env):
    api, _, catalogue, _ = env
    catalogue.ingest(REAL)
    age = datetime.now(timezone.utc) - datetime.fromisoformat(REAL.retrieved_at.replace('Z', '+00:00'))
    expected = 'active' if age <= timedelta(days=7) else 'stale'
    [found] = catalogue_match(api).json()['matches']
    assert found['status'] == 'matched' and found['entry_status'] == expected


def test_catalogue_record_is_accepted_by_intake_and_feeds_agent_context(env):
    api, snapshot, catalogue, _ = env
    catalogue.ingest(fresh())
    raw = catalogue.query('PyPI', 'requests')['entries'][0]
    first = api.post('/api/repositories/demo/applicability/intake', json={'entry': raw, 'snapshot_id': snapshot['id']}).json()
    again = api.post('/api/repositories/demo/applicability/intake', json={'entry': raw}).json()
    [job] = first['jobs']
    assert again['jobs'][0]['id'] == job['id'] and len(db.list_records('jobs')) == 1
    assert (job['source'], job['status'], job['source_url']) == ('security', 'Awaiting approval', 'https://osv.dev/vulnerability/GHSA-9wx4-h78v-vm56')
    stored = db.get('jobs', job['id'])
    assert (stored['catalogue']['entry_id'], stored['catalogue']['entry_revision'], stored['catalogue']['entry_hash']) == (
        'CVE-2024-35195', 1, REAL.source_content_hash)
    assert stored['applicability']['citations'][0]['path'] == 'requirements.txt'
    for context in (agent_context.agent_graph_context('demo', snapshot['id'], 'fetch url requests', job=stored),
                    agent_context.agent_graph_context('demo', snapshot['id'], 'fetch url requests', entries=[applicability.from_global_entry(raw)])):
        [item] = context['catalogue_matches']
        assert item['entry_id'] == 'CVE-2024-35195' and item['status'] == 'matched'
        assert item['directly_imported'] is True and item['sink_reachable'] is None
        [usage] = item['usage']
        assert usage['imports'] == [{'path': 'app.py', 'line': 1, 'module': 'requests'}]
        assert usage['calls'] == [{'path': 'app.py', 'line': 5, 'call': 'requests.get'}] and usage['sink_calls'] == []


def test_atlas_is_explicit_unknown_on_failure_and_never_falls_back_to_local(env, monkeypatch):
    api, _, catalogue, _ = env
    catalogue.ingest(fresh())

    def unavailable(*args, **kwargs):
        raise global_pipeline.Error('Public Atlas query failed or returned invalid evidence; coverage remains unknown')
    monkeypatch.setattr(global_pipeline, 'query_atlas', unavailable)
    response = catalogue_match(api, provider='atlas')
    assert response.status_code == 503 and 'coverage remains unknown' in response.json()['detail']
    assert db.list_records('jobs') == []

    # Mocked transport: Atlas returns the same public shape; the provider and Atlas limitation are reported.
    calls = []

    def answer(ecosystem, package, limit=20):
        calls.append((ecosystem, package, limit))
        return {**query_result([fresh()], ecosystem, package, limit), 'provider': 'atlas'}
    monkeypatch.setattr(global_pipeline, 'query_atlas', answer)
    body = catalogue_match(api, provider='atlas', limit=5).json()
    assert calls == [('PyPI', 'requests', 5)] and body['provider'] == 'atlas'
    assert body['matches'][0]['status'] == 'matched' and body['coverage'] == 'unknown'
    assert any('Atlas readers' in item for item in body['limitations'])


def test_route_validation_stale_snapshot_and_empty_profile(env, tmp_path):
    api, snapshot, _, _ = env
    assert catalogue_match(api, snapshot_id='old').status_code == 409
    assert catalogue_match(api, provider='remote').status_code == 422
    for limit in (0, 101, True, '5'):
        assert catalogue_match(api, limit=limit).status_code == 422
    register(tmp_path, 'bare', {'main.py': 'print(1)\n'})
    empty = api.post('/api/repositories/bare/applicability/catalogue', json={}).json()
    assert empty['queried'] == [] and empty['matches'] == [] and empty['coverage'] == 'unknown'
    assert db.list_records('jobs') == []
