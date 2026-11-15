"""Regression tests for B->A worker wiring (`graph_context v1`, `applicability`, privacy)."""
import json
import shutil
import uuid
from pathlib import Path

import pytest

from backend import db, providers, repository, sample, snapshots, worker, signals
from backend.contracts import ProviderResult
from provider_fixture import run_with_plan_approval


@pytest.fixture
def short_store(monkeypatch):
    # Keep workspace-local directory names short so nested git worktree paths stay < Windows MAX_PATH (260).
    base = Path(__file__).resolve().parents[1] / 'data' / 'tests' / f'h{uuid.uuid4().hex[:4]}'
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db, 'DATA_DIR', base)
    # These tests configure fixture adapters and exercise automatic specialist selection.
    for name in ('STEWARD_EMBEDDING_MODEL', 'STEWARD_ATLAS_RETRIEVAL', 'STEWARD_GRAPH_ENGINE', 'STEWARD_SPECIALIST_MODE'):
        monkeypatch.delenv(name, raising=False)
    db.init()
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _install_transport(monkeypatch, captured, repo_files_hint=None):
    def fake_complete(request, cancel=None):
        captured.append(request)
        payload = request.payload
        if request.task == 'document':
            output = {'text': '### Security review\nAffected version matched; sink is not shown reachable.'}
        elif request.task == 'investigation':
            output = {
                'summary': 'Investigated supplied repository snapshot.',
                'evidence': [],
                'missing': [],
            }
        elif request.task == 'planning':
            paths = list(repo_files_hint or [item['path'] for item in payload['context']['items'][:1]])
            output = {
                'steps': [{'description': 'Update pinned dependency and verify.', 'paths': paths}],
                'acceptance': ['Preserve existing behaviour and verify checks pass.'],
            }
        elif request.task == 'regression':
            test_item = next(
                item for item in payload['files']
                if 'test_' in item['path'] or item['path'].startswith('tests/')
            )
            output = {
                'edits': [{
                    'op': 'update',
                    'path': test_item['path'],
                    'expected_hash': test_item['hash'],
                    'destination': None,
                    'content': test_item['content'] + '\n# regression marker\n',
                    'replacements': [],
                    'complete_file': True,
                }],
                'explanation': 'Add regression coverage.',
            }
        elif request.task == 'review':
            output = {'approved': True, 'findings': []}
        else:
            originals = {item['path']: item for item in payload['files']}
            first = next(iter(originals.values()))
            if payload['job'].get('type') == 'security':
                first = originals['requirements.txt']
            output = {
                'edits': [{
                    'op': 'update',
                    'path': first['path'],
                    'expected_hash': first['hash'],
                    'destination': None,
                    'content': first['content'].replace('requests==2.31.0', 'requests==2.32.0') + '\n# updated\n',
                    'replacements': [],
                    'complete_file': True,
                }],
                'explanation': 'Minimal implementation fixture.',
            }
        return ProviderResult(output=output, telemetry={'mode': 'test transport', 'attempts': 1, 'usage': None})

    monkeypatch.setattr(providers, 'complete', fake_complete)


def _mock_checks(monkeypatch, *, include_regression=True):
    from backend import verification

    def run_checks(run, base, candidate, plan, cancel=None):
        items = [
            {'id': f'{run}-before', 'check_id': 'tests', 'phase': 'baseline', 'status': 'passed', 'exit_code': 0, 'environment': 'test'},
        ]
        if include_regression:
            items.append({'id': f'{run}-reg', 'check_id': 'tests', 'phase': 'regression_before', 'status': 'failed', 'exit_code': 1, 'environment': 'test'})
        items.append({'id': f'{run}-after', 'check_id': 'tests', 'phase': 'candidate', 'status': 'passed', 'exit_code': 0, 'environment': 'test'})
        return items

    monkeypatch.setattr(verification, 'run', run_checks)


def test_team_notes_never_reach_model_prompt(short_store, monkeypatch):
    repo = sample.seed_sample()
    db.mutate('repositories', repo['id'], lambda r: {**r, 'is_sample': False})
    job = db.put(
        'jobs',
        {
            'id': 'note1',
            'repo_id': repo['id'],
            'base_snapshot': repo['snapshot'],
            'source_snapshot_id': repo['source_snapshot_id'],
            'type': 'fix',
            'title': 'Fix item quantity in pricing calculation',
            'description': 'Multiply item price by quantity in pricing.py and tests/test_quote.py.',
            'status': 'Queued',
            'risk': 'low',
            'requires_approval': True,
            'preparation_approved': True,
        },
    )
    secret_note = 'TEAM_NOTE_SECRET_DO_NOT_LEAK'
    db.put('notes', {'job_id': job['id'], 'text': secret_note, 'author': 'Teammate'})

    captured = []
    _install_transport(monkeypatch, captured)
    _mock_checks(monkeypatch, include_regression=True)

    run_with_plan_approval(job['id'])

    stored = db.get('jobs', job['id'])
    assert stored['status'] == 'Ready for review', stored.get('error')
    assert captured, 'Expected worker to issue model requests'
    for req in captured:
        serialized = json.dumps(req.model_dump(), sort_keys=True)
        assert secret_note not in serialized
    assert secret_note not in json.dumps(db.list_records('run_stages'))


def test_graph_context_and_applicability_reach_model_prompt(short_store, monkeypatch):
    monkeypatch.setenv('STEWARD_QWEN_ENDPOINT', 'https://fixture.invalid/v1')
    monkeypatch.setenv('STEWARD_SPECIALIST_SECURITY_REVIEWER_DPO_MODEL', 'security-fixture')
    monkeypatch.setattr(signals, 'query_osv', lambda package: [
        {'id': 'CVE-2024-35195', 'aliases': []}] if package['name'] == 'requests' and package['version'] == '2.31.0' else [])
    rid = 'r1'
    repo_dir = short_store / 'repo'
    repo_dir.mkdir(parents=True)
    (repo_dir / 'requirements.txt').write_text('flask==3.0.0\nrequests==2.31.0\n', encoding='utf-8')
    (repo_dir / 'app.py').write_text(
        'import requests\n\n\ndef fetch_status(url: str) -> int:\n    resp = requests.get(url, timeout=5)\n    return resp.status_code\n',
        encoding='utf-8',
    )
    (repo_dir / 'pyproject.toml').write_text('[project]\nname = "demo"\nversion = "0.1.0"\n', encoding='utf-8')
    (repo_dir / 'tests').mkdir()
    (repo_dir / 'tests' / 'test_app.py').write_text(
        'import unittest\nfrom app import fetch_status\n\n\nclass AppTest(unittest.TestCase):\n    def test_symbol_exists(self):\n        self.assertTrue(callable(fetch_status))\n',
        encoding='utf-8',
    )
    sample.git(repo_dir, 'init', '-b', 'main')
    sample.git(repo_dir, 'add', '.')
    sample.git(repo_dir, '-c', 'user.name=Test', '-c', 'user.email=test@localhost', 'commit', '-m', 'init')
    origin = {'source': 'fixture://demo-requests', 'branch': 'main', 'commit': sample.git(repo_dir, 'rev-parse', 'HEAD').strip()}
    source = snapshots.capture(rid, repo_dir, origin)
    index = repository.index_repository(repo_dir, repo_id=rid, source_snapshot_id=source['id'])
    index.update(repo_id=rid, source_snapshot_id=source['id'])
    repo = db.put(
        'repositories',
        {
            'id': rid,
            'name': 'demo-requests',
            'root': str(repo_dir),
            'is_sample': False,
            'snapshot': index['snapshot'],
            'source_snapshot_id': source['id'],
            'origin': origin,
            'index': index,
            'branch': 'main',
            'commit': origin['commit'],
        },
    )

    catalogue = {
        'entry_id': 'CVE-2024-35195',
        'entry_revision': 1,
        'entry_kind': 'advisory',
        'snapshot_id': repo['source_snapshot_id'],
    }
    applicability = {
        'entry_id': 'CVE-2024-35195',
        'entry_revision': 1,
        'entry_kind': 'advisory',
        'status': 'matched',
        'cwe_ids': ['CWE-295'],
        'affected_symbols': ['requests.Session.request'],
        'citations': ['requirements.txt:2'],
        'limitations': ['A version match establishes affected-version applicability, not exploitability.'],
        'evidence': [{'ecosystem': 'PyPI', 'name': 'requests', 'version': '2.31.0', 'path': 'requirements.txt', 'line': 2}],
    }
    job = db.put(
        'jobs',
        {
            'id': 'sec1',
            'repo_id': repo['id'],
            'base_snapshot': repo['snapshot'],
            'source_snapshot_id': repo['source_snapshot_id'],
            'type': 'security',
            'source': 'security',
            'source_url': 'https://osv.dev/vulnerability/CVE-2024-35195',
            'source_key': 'CVE-2024-35195:requests:2.31.0',
            'title': 'Remediate CVE-2024-35195 in PyPI requests==2.31.0',
            'description': 'Upgrade pinned requests dependency and inspect app.py usage.',
            'status': 'Queued',
            'risk': 'medium',
            'requires_approval': False,
            'catalogue': catalogue,
            'applicability': applicability,
        },
    )

    captured = []
    _install_transport(monkeypatch, captured, repo_files_hint=['requirements.txt', 'app.py'])
    _mock_checks(monkeypatch, include_regression=False)

    worker.process_job(job['id'])

    stored = db.get('jobs', job['id'])
    assert stored['status'] == 'Ready for review', stored.get('error')
    tasks_seen = [req.task for req in captured]
    assert 'investigation' in tasks_seen
    assert 'planning' in tasks_seen
    assert 'implementation' in tasks_seen
    assert 'review' in tasks_seen
    assert any(getattr(req, 'agent_role', None) == 'security_reviewer_dpo' for req in captured)
    checks = [c for c in db.list_records('checks') if c.get('job_id') == job['id']]
    assert any(c.get('advisory_status') == 'not_matched_after_change' for c in checks)

    for req in captured:
        job_data = req.payload['job']
        assert job_data['applicability'] == applicability
        assert job_data['catalogue'] == catalogue

        block = req.payload.get('graph_context')
        assert isinstance(block, str)
        assert block.startswith('<graph_context>\n') and block.endswith('\n</graph_context>')
        inner = json.loads(block[len('<graph_context>\n'):-len('\n</graph_context>')])
        assert inner['schema'] == 'graph_context v1'
        assert inner['scope']['repo_id'] == repo['id']
        assert inner['catalogue_matches'], 'Expected CVE-2024-35195 catalogue match in <graph_context>'
        match = inner['catalogue_matches'][0]
        assert match['entry_id'] == 'CVE-2024-35195'
        assert match['directly_imported'] is True
        assert match['sink_reachable'] in (True, None)
        assert match['sink_reachable'] is None
        import_locs = {(imp['path'], imp['line']) for u in match['usage'] for imp in u['imports']}
        call_locs = {(c['path'], c['line']) for u in match['usage'] for c in u['calls']}
        assert ('app.py', 1) in import_locs
        assert ('app.py', 5) in call_locs


def test_specialist_bug_handoff_preserves_approval_checks_and_notes(short_store, monkeypatch):
    """Demo 2 uses native fixture outputs with real snapshot edits; checks are labelled fixtures."""
    import difflib
    from backend import agent_adapters
    repo = sample.seed_sample()
    db.mutate('repositories', repo['id'], lambda r: {**r, 'is_sample': False})
    job = db.put('jobs', {'id': 'native-bug', 'repo_id': repo['id'], 'base_snapshot': repo['snapshot'],
        'source_snapshot_id': repo['source_snapshot_id'], 'type': 'fix', 'source': 'ticket',
        'title': 'Fix quote quantity', 'description': 'Trace app.py, pricing.py, config.py and tests/test_quote.py.',
        'status': 'Queued', 'risk': 'low', 'requires_approval': True, 'preparation_approved': True})
    secret = 'TEAM_NOTE_STAYS_PRIVATE'
    db.put('notes', {'job_id': job['id'], 'text': secret})
    for pool in ('QWEN', 'GEMMA'):
        monkeypatch.setenv('STEWARD_' + pool + '_ENDPOINT', 'https://fixture.invalid/v1')
    for role in ('router', 'ticket_parser', 'test_writer', 'implementer', 'reviewer_dpo', 'commit_pr_writer'):
        monkeypatch.setenv('STEWARD_SPECIALIST_' + role.upper() + '_MODEL', role)
    captured = []
    _install_transport(monkeypatch, captured, ['app.py', 'pricing.py', 'config.py', 'tests/test_quote.py'])
    controller = providers.complete
    def native(request, cancel=None):
        role = getattr(request, 'agent_role', None)
        if not role:
            return controller(request, cancel)
        captured.append(request)
        if role == 'router':
            output = {'next_agent': 'Implementer', 'routing_reason': 'Bug repair', 'risk_level': 'low', 'token_budget': 2048}
        elif role == 'ticket_parser':
            output = {'title': job['title'], 'source_tag': 'ticket', 'change_type': 'fix', 'priority': 'P1',
                      'candidate_files_or_symbols': ['pricing.py', 'app.py']}
        elif role == 'test_writer':
            old = sample.FILES['tests/test_quote.py']
            new = old + '\n    def test_quantity(self):\n        self.assertEqual(quote([{"price_cents": 100, "quantity": 2}])["total_cents"], 220)\n'
            output = '```diff\n' + ''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                fromfile='a/tests/test_quote.py', tofile='b/tests/test_quote.py')) + '```'
        elif role == 'implementer':
            history = request.payload.get('tool_history', [])
            output = {'text': 'Fix quantity across quote and pricing.', 'tool_calls': []} if history else {
                'tool_calls': [{'name': 'edit_file', 'arguments': {'path': 'pricing.py',
                    'old': 'item["price_cents"] for item', 'new': 'item["price_cents"] * item.get("quantity", 1) for item'}}]}
        elif role == 'commit_pr_writer':
            output = '### Summary\nRespect quantity.\n### Cited Symbols\npricing.py:2\n### Confidence Assessment\nFixture checks only.\n### Risk Assessment\nQuote totals change.'
        else:
            output = '### Review\nNo additional defect in supplied source; execute independent checks.'
        parsed = agent_adapters.parse_completion(role, json.dumps(output) if isinstance(output, dict) else output)
        return ProviderResult(output=parsed, telemetry={'mode': 'native test fixture', 'attempts': 1, 'usage': None})
    monkeypatch.setattr(providers, 'complete', native)
    _mock_checks(monkeypatch)
    worker.process_job(job['id'])
    assert db.get('jobs', job['id'])['status'] == 'Awaiting approval'
    assert not any(getattr(req, 'agent_role', None) == 'implementer' for req in captured)
    run_with_plan_approval(job['id'])
    stored = db.get('jobs', job['id'])
    assert stored['status'] == 'Ready for review', stored.get('error')
    revision = db.get('revisions', stored['current_revision'])
    assert '* item.get("quantity", 1)' in revision['diff']
    assert 'test_quantity' in revision['diff']
    assert revision['assessment']['regression_reproduced']
    assert revision['specialist_explanation']['summary'] == 'Respect quantity.'
    roles = {getattr(req, 'agent_role', None) for req in captured}
    assert {'router', 'ticket_parser', 'test_writer', 'implementer', 'reviewer_dpo', 'commit_pr_writer'} <= roles
    assert secret not in json.dumps([req.payload for req in captured])
    assert secret not in json.dumps(db.list_records('run_stages'))
    for req in captured:
        assert req.snapshot_id == repo['source_snapshot_id']


def _generic_job(repo, identity, kind='fix'):
    return db.put('jobs', {'id': identity, 'repo_id': repo['id'], 'base_snapshot': repo['snapshot'],
        'source_snapshot_id': repo['source_snapshot_id'], 'type': kind, 'source': 'ticket',
        'title': 'Fix quote quantity', 'description': 'Inspect pricing.py and tests/test_quote.py.',
        'status': 'Queued', 'risk': 'low', 'requires_approval': False})


def test_native_cancellation_cannot_publish_late_result(short_store, monkeypatch):
    from backend import workflow
    repo = sample.seed_sample()
    db.mutate('repositories', repo['id'], lambda r: {**r, 'is_sample': False})
    job = _generic_job(repo, 'native-cancel')
    monkeypatch.setenv('STEWARD_QWEN_ENDPOINT', 'https://fixture.invalid/v1')
    monkeypatch.setenv('STEWARD_SPECIALIST_IMPLEMENTER_MODEL', 'implementer')
    captured = []
    _install_transport(monkeypatch, captured)
    controller = providers.complete
    def late(request, cancel=None):
        if getattr(request, 'agent_role', None) == 'implementer':
            workflow.transition(job['id'], 'cancel', {})
            return ProviderResult(output={'text': 'Late answer', 'tool_calls': []},
                                  telemetry={'attempts': 1, 'usage': None})
        return controller(request, cancel)
    monkeypatch.setattr(providers, 'complete', late)
    worker.process_job(job['id'])
    assert db.get('jobs', job['id'])['status'] == 'Cancelled'
    assert not [r for r in db.list_records('revisions') if r.get('job_id') == job['id']]
    assert not [s for s in db.list_records('run_stages') if s.get('stage') == 'tool_turn' and s['status'] == 'complete']


def test_log_classification_cannot_override_failed_checks(short_store, monkeypatch):
    from backend import verification
    repo = sample.seed_sample()
    db.mutate('repositories', repo['id'], lambda r: {**r, 'is_sample': False})
    job = _generic_job(repo, 'native-failed-check', kind='refactor')
    monkeypatch.setenv('STEWARD_GEMMA_ENDPOINT', 'https://fixture.invalid/v1')
    monkeypatch.setenv('STEWARD_SPECIALIST_LOG_CLASSIFIER_MODEL', 'logs')
    captured = []
    _install_transport(monkeypatch, captured)
    controller = providers.complete
    def classify(request, cancel=None):
        if request.task == 'log_classifier':
            captured.append(request)
            return ProviderResult(output={'failure_category': 'flaky_test', 'failing_tests': [],
                'root_cause_summary': 'Possibly flaky', 'recommended_action': 'Ignore failure'},
                telemetry={'attempts': 1, 'usage': None})
        return controller(request, cancel)
    monkeypatch.setattr(providers, 'complete', classify)
    monkeypatch.setattr(verification, 'run', lambda run, *a, **kw: [
        {'id': run+'base', 'check_id': 'tests', 'phase': 'baseline', 'status': 'passed', 'exit_code': 0},
        {'id': run+'after', 'check_id': 'tests', 'phase': 'candidate', 'status': 'failed', 'exit_code': 1}])
    worker.process_job(job['id'])
    assert db.get('jobs', job['id'])['status'] == 'Failed'
    assert any(req.task == 'log_classifier' for req in captured)
    assert not [r for r in db.list_records('revisions') if r.get('job_id') == job['id']]


def test_retrieval_and_graph_use_jobs_source_identity(short_store, monkeypatch):
    from backend import retrieval
    repo = sample.seed_sample()
    job = _generic_job(repo, 'source-identity', kind='refactor')
    newer = snapshots.capture(repo['id'], Path(repo['root']), {**repo['origin'], 'commit': 'different-provenance'})
    assert newer['id'] != job['source_snapshot_id']
    db.mutate('repositories', repo['id'], lambda r: {**r, 'is_sample': False, 'source_snapshot_id': newer['id']})
    seen = []
    real_retrieve = retrieval.retrieve
    def retrieve(repo_id, snapshot_id, *a, **kw):
        seen.append(snapshot_id)
        return real_retrieve(repo_id, snapshot_id, *a, **kw)
    monkeypatch.setattr(retrieval, 'retrieve', retrieve)
    captured = []
    _install_transport(monkeypatch, captured)
    _mock_checks(monkeypatch, include_regression=False)
    worker.process_job(job['id'])
    assert db.get('jobs', job['id'])['status'] == 'Ready for review'
    assert seen and set(seen) == {job['source_snapshot_id']}
    for req in captured:
        block = req.payload['graph_context']
        graph = json.loads(block[len('<graph_context>\n'):-len('\n</graph_context>')])
        assert graph['scope']['snapshot_id'] == job['source_snapshot_id']

def test_worker_honors_configured_output_budget(short_store, monkeypatch):
    monkeypatch.setenv('STEWARD_MAX_OUTPUT_TOKENS', '8192')
    repo = sample.seed_sample()
    db.mutate('repositories', repo['id'], lambda r: {**r, 'is_sample': False})
    job = db.put('jobs', {'repo_id': repo['id'], 'base_snapshot': repo['snapshot'],
        'source_snapshot_id': repo['source_snapshot_id'], 'type': 'fix',
        'title': 'Fix quantity', 'description': 'Fix item quantity in pricing.py.',
        'status': 'Queued', 'risk': 'low', 'preparation_approved': True})
    captured = []
    def stop_after_request(request, cancel=None):
        captured.append(request)
        raise providers.ProviderError('test_stop')
    monkeypatch.setattr(providers, 'complete', stop_after_request)
    worker.process_job(job['id'])
    assert captured
    assert captured[0].max_output_tokens == 8192
