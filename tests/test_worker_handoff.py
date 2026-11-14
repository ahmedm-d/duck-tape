"""Regression tests for B->A worker wiring (`graph_context v1`, `applicability`, privacy)."""
import json
import shutil
import uuid
from pathlib import Path

import pytest

from backend import db, providers, repository, sample, snapshots, worker
from backend.contracts import ProviderResult
from provider_fixture import run_with_plan_approval


@pytest.fixture
def short_store(monkeypatch):
    # Keep workspace-local directory names short so nested git worktree paths stay < Windows MAX_PATH (260).
    base = Path(__file__).resolve().parents[1] / 'data' / 'tests' / f'h{uuid.uuid4().hex[:4]}'
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db, 'DATA_DIR', base)
    for name in ('STEWARD_EMBEDDING_MODEL', 'STEWARD_ATLAS_RETRIEVAL', 'STEWARD_GRAPH_ENGINE'):
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
        if request.task == 'investigation':
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
            output = {
                'edits': [{
                    'op': 'update',
                    'path': first['path'],
                    'expected_hash': first['hash'],
                    'destination': None,
                    'content': first['content'] + '\n# updated\n',
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
            'type': 'improvement',
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
