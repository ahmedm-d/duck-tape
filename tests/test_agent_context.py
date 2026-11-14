"""graph_context v1: bounded, snapshot-bound, null means unknown (fixture repository only)."""
import json
from pathlib import Path, PurePosixPath

import pytest
from fastapi.testclient import TestClient

from backend import agent_context, db, main, sample, symbols

CATALOGUE = json.loads((Path(__file__).parent / 'fixtures' / 'catalogue' / 'sample_entries.json').read_text(encoding='utf-8'))
KEYS = {'schema', 'scope', 'focal_symbols', 'edges', 'blast_radius', 'catalogue_matches', 'approved_knowledge', 'gaps', 'context_hash'}


@pytest.fixture
def parcel(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path / 'store')
    for name in ('STEWARD_EMBEDDING_MODEL', 'STEWARD_ATLAS_RETRIEVAL', 'STEWARD_GRAPH_ENGINE'):
        monkeypatch.delenv(name, raising=False)
    db.init()
    return TestClient(main.app), sample.seed_sample()


def index_of(files: dict) -> dict:
    records = []
    for path, content in files.items():
        suffix = PurePosixPath(path).suffix
        names, imports, calls, _ = symbols.parse_python(content) if suffix == '.py' else symbols.parse_js(content, suffix)
        records.append({'path': path, 'status': 'indexed', 'content': content, 'symbols': names, 'imports': imports, 'calls': calls})
    return {'files': records, 'gaps': []}


def test_signature_joins_multiline_python_and_trims_javascript_bodies():
    source = 'import x\n\ndef verify(\n    token: str,\n    leeway: int = 0,\n) -> dict:\n    return {}\n'
    assert agent_context.signature(source, 3, 'auth.py') == 'def verify( token: str, leeway: int = 0, ) -> dict:'
    assert agent_context.signature('export function load(text, opts = {}) {\n  return 1\n}\n', 1, 'a.ts') == 'export function load(text, opts = {}) {'.split('{', 1)[0].strip()
    assert agent_context.signature('x = 1\n', 9, 'a.py') is None


def test_resolved_call_to_named_affected_symbol_is_reachable():
    index = index_of({'loader.py': 'import yaml as y\nfrom yaml import load as parse\n\ndef read(t):\n    y.safe_load(t)\n    return parse(t)\n'})
    usage = agent_context.package_usage(index, 'PyPI', 'PyYAML', ['yaml.load'])
    assert usage['modules_checked'] == ['yaml'] and usage['directly_imported'] is True
    assert usage['sink_reachable'] is True and usage['sink_calls'] == [{'path': 'loader.py', 'line': 6, 'call': 'yaml.load'}]
    assert {item['call'] for item in usage['calls']} == {'yaml.safe_load', 'yaml.load'}


def test_imported_without_named_symbols_or_matching_call_stays_unknown():
    index = index_of({'loader.py': 'import yaml\n\ndef read(t):\n    return yaml.safe_load(t)\n'})
    assert agent_context.package_usage(index, 'PyPI', 'pyyaml', [])['sink_reachable'] is None
    usage = agent_context.package_usage(index, 'PyPI', 'pyyaml', ['yaml.load'])
    assert usage['directly_imported'] is True and usage['sink_reachable'] is None and 'not shown reachable' in usage['basis']


def test_missing_import_is_false_only_for_direct_use_and_dynamic_imports_make_it_unknown():
    plain = agent_context.package_usage(index_of({'app.py': 'import json\n'}), 'PyPI', 'requests', ['requests.get'])
    assert plain['directly_imported'] is False and plain['sink_reachable'] is None and 'transitive' in plain['basis']
    dynamic = agent_context.package_usage(index_of({'app.py': 'import importlib\nmod = importlib.import_module(name)\n'}), 'PyPI', 'requests')
    assert dynamic['directly_imported'] is None and 'dynamic import' in dynamic['basis']
    assert agent_context.package_usage({'files': []}, 'crates.io', 'serde')['directly_imported'] is None


def test_javascript_named_import_call_matches_symbol():
    index = index_of({'src/read.ts': "import { load } from 'js-yaml';\nexport function read(t) { return load(t); }\n"})
    usage = agent_context.package_usage(index, 'npm', 'js-yaml', ['load'])
    assert usage['directly_imported'] is True and usage['sink_reachable'] is True
    assert usage['sink_calls'][0]['call'] == 'js-yaml.load'


def test_sample_context_is_bounded_cited_and_honest(parcel):
    api, repo = parcel
    response = api.get(f'/api/repositories/{repo["id"]}/agent-context', params={'query': 'quote total tax', 'budget_tokens': 3500})
    assert response.status_code == 200
    context = response.json()
    assert set(context) == KEYS and context['schema'] == 'graph_context v1'
    assert len(json.dumps(context, ensure_ascii=False).encode()) <= 3500
    assert context['scope']['snapshot_id'] == repo['source_snapshot_id'] and context['scope']['engine'] == 'duck-tape-symbols'
    quote = next(item for item in context['focal_symbols'] if item['label'] == 'quote')
    assert quote['path'] == 'app.py' and quote['signature'] == 'def quote(items):' and quote['community'] is None
    assert 'tests/test_quote.py' in context['blast_radius']['linked_test_files']
    assert context['blast_radius']['structural_risk'] in {'low', 'medium', 'high'} and 'Heuristic' in context['blast_radius']['basis']
    assert all(edge['confidence'] in {'EXTRACTED', 'INFERRED'} for edge in context['edges'])
    assert context['catalogue_matches'] == []


def test_catalogue_match_reports_lockfile_pin_without_reachability(parcel):
    _, repo = parcel
    context = agent_context.agent_graph_context(repo['id'], repo['source_snapshot_id'], 'fixture parser dependency', 3500,
                                                entries=[{**CATALOGUE['entries'][0], 'cwe_ids': ['CWE-20', 'not-a-cwe'], 'affected_symbols': ['fixture_parser.parse']}])
    found = context['catalogue_matches'][0]
    assert found['status'] == 'matched' and found['cwe'] == ['CWE-20']
    assert found['citations'][0]['path'] == 'requirements.lock' and found['citations'][0]['start_line'] == 2
    assert found['directly_imported'] is False and found['sink_reachable'] is None
    assert any('affected-version applicability, not exploitability' in item for item in found['limitations'])


def test_job_binding_carries_public_symbols_into_context(parcel):
    api, repo = parcel
    entry = {**CATALOGUE['entries'][0], 'cwe_ids': ['CWE-502'], 'affected': [{**CATALOGUE['entries'][0]['affected'][0], 'symbols': ['fixture_parser.load']}]}
    job = api.post(f'/api/repositories/{repo["id"]}/applicability/intake', json={'entry': entry}).json()['jobs'][0]
    assert job['applicability']['cwe_ids'] == ['CWE-502'] and job['applicability']['affected_symbols'] == ['fixture_parser.load']
    context = api.get(f'/api/repositories/{repo["id"]}/agent-context', params={'query': 'fixture parser', 'job_id': job['id']}).json()
    found = context['catalogue_matches'][0]
    assert found['entry_id'] == entry['id'] and found['cwe'] == ['CWE-502'] and found['sink_reachable'] is None
    assert found['usage'][0]['modules_checked'] == ['fixture_parser']


def test_only_current_team_approved_knowledge_and_never_notes(parcel):
    api, repo = parcel
    base = {'repo_id': repo['id'], 'scope': '*', 'version': 1}
    db.put('knowledge', {**base, 'id': 'k-ok', 'status': 'team-approved', 'title': 'Money', 'content': 'APPROVED-INTEGER-CENTS'})
    db.put('knowledge', {**base, 'id': 'k-draft', 'status': 'observed', 'title': 'Draft', 'content': 'DRAFT-ONLY-RULE'})
    db.put('knowledge', {**base, 'id': 'k-stale', 'status': 'team-approved', 'stale': True, 'title': 'Old', 'content': 'STALE-RULE'})
    db.put('notes', {'id': 'n1', 'repo_id': repo['id'], 'text': 'PRIVATE-TEAM-NOTE'})
    text = api.get(f'/api/repositories/{repo["id"]}/agent-context', params={'query': 'quote total'}).text
    assert 'APPROVED-INTEGER-CENTS' in text
    assert not any(marker in text for marker in ('DRAFT-ONLY-RULE', 'STALE-RULE', 'PRIVATE-TEAM-NOTE'))


def test_budget_trims_with_visible_gap_and_rejects_bad_requests(parcel):
    api, repo = parcel
    small = agent_context.agent_graph_context(repo['id'], repo['source_snapshot_id'], 'quote total tax pricing', 1400)
    assert len(json.dumps(small, ensure_ascii=False).encode()) <= 1400
    assert any('trimmed' in gap or 'omitted' in gap for gap in small['gaps'])
    url = f'/api/repositories/{repo["id"]}/agent-context'
    assert api.get(url, params={'query': 'quote', 'budget_tokens': 100}).status_code == 422
    assert api.get(url, params={'query': 'quote', 'snapshot_id': 'old'}).status_code == 409
    other = sample.seed_sample(force_new=True)
    foreign = next(job for job in db.list_records('jobs') if job.get('repo_id') == other['id'])
    assert api.get(url, params={'query': 'quote', 'job_id': foreign['id']}).status_code == 404
