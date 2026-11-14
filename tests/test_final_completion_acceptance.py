"""Fresh acceptance boundary cases; deterministic transports, no live model calls."""
import pytest

from backend import db, providers, signals, verification, worker, workflow
from backend.contracts import ProviderResult
from test_live_pipeline_contract import generic
from test_chunk_retrieval import register, store


def test_feature_without_requested_behavior_check_cannot_publish(generic):
    job, _, _ = generic
    db.mutate('jobs', job['id'], lambda item: {**item, 'type': 'feature'})
    worker.process_job(job['id'])
    # The transport changes app.py only; the unchanged baseline checks cannot
    # establish the newly requested return-value behavior.
    current = db.get('jobs', job['id'])
    assert current['status'] != 'Ready for review', current
    assert not db.list_records('revisions')


def test_old_decision_cannot_resume_a_different_pending_question(generic, monkeypatch):
    job, calls, complete = generic
    planning = 0

    def two_questions(request, cancel=None):
        nonlocal planning
        if request.task == 'planning':
            planning += 1
            if planning <= 2:
                calls.append(request)
                return ProviderResult(output={'decision': {
                    'title': f'Question {planning}',
                    'options': [
                        {'id': 'one', 'label': 'First', 'tradeoff': 'First behavior'},
                        {'id': 'two', 'label': 'Second', 'tradeoff': 'Second behavior'},
                    ],
                    'recommendation': 'one',
                }}, telemetry={'attempts': 1, 'usage': None})
        return complete(request, cancel)

    monkeypatch.setattr(providers, 'complete', two_questions)
    worker.process_job(job['id'])
    first = db.get('jobs', job['id'])['decision_id']
    workflow.transition(job['id'], 'decide', {'decision_id': first, 'option': 'one'})
    worker.process_job(job['id'])
    second = db.get('jobs', job['id'])['decision_id']
    assert second != first and db.get('decisions', second)['selected'] is None
    with pytest.raises(workflow.Conflict, match='current|pending|changed'):
        workflow.transition(job['id'], 'decide', {'decision_id': first, 'option': 'two'})
    assert db.get('jobs', job['id'])['status'] == 'Needs input'


def test_cancellation_after_checks_cannot_publish_late_revision(generic, monkeypatch):
    job, _, _ = generic
    original = verification.run

    def cancelled_after_checks(*args, **kwargs):
        result = original(*args, **kwargs)
        workflow.transition(job['id'], 'cancel', {})
        return result

    monkeypatch.setattr(verification, 'run', cancelled_after_checks)
    worker.process_job(job['id'])
    assert db.get('jobs', job['id'])['status'] == 'Cancelled'
    assert not db.list_records('revisions')


def test_security_duplicate_resolved_versions_cannot_hide_remaining_match(monkeypatch):
    import json
    original = {'id': 'GHSA-test-1234', 'aliases': ['CVE-2026-1234']}
    monkeypatch.setattr(signals, 'query_osv', lambda package: [original] if package['version'] == '1.0.0' else [])
    lock = lambda packages: json.dumps({'lockfileVersion': 3, 'packages': packages})
    before = lock({'node_modules/example': {'version': '1.0.0'}})
    after = lock({'node_modules/example': {'version': '2.0.0'},
                  'node_modules/other/node_modules/example': {'version': '1.0.0'}})
    result = signals.verify_security(
        {'source_url': 'https://osv.dev/vulnerability/GHSA-test-1234', 'source_key': 'CVE-2026-1234:example:1.0.0'},
        {'id': 'base', 'files': {'package-lock.json': before}},
        {'patch_hash': 'patch', 'files': [{'path': 'package-lock.json', 'after': after}]})
    assert result['status'] == 'failed'
    assert {item['package']['version'] for item in result['queries'] if item['phase'] == 'after'} == {'1.0.0', '2.0.0'}


def test_security_removed_lock_with_remaining_manifest_is_unavailable(monkeypatch):
    monkeypatch.setattr(signals, 'query_osv', lambda package: [{'id': 'GHSA-test-1234'}])
    result = signals.verify_security(
        {'source_url': 'https://osv.dev/vulnerability/GHSA-test-1234', 'source_key': 'GHSA-test-1234:example:1.0.0'},
        {'id': 'base', 'files': {'pyproject.toml': '[project]\nname="package"\ndependencies=["example"]',
                               'requirements.txt': 'example==1.0.0\n'}},
        {'patch_hash': 'patch', 'files': [{'path': 'requirements.txt', 'after': None}]})
    assert result['status'] == 'unavailable'
    assert result['resolution_gaps']['after']


def test_benchmark_patch_and_base_bindings_are_required():
    ordinary = [{'phase': phase, 'check_id': 'tests', 'status': 'passed'} for phase in ('baseline', 'candidate')]
    before = {'phase': 'benchmark_before', 'check_id': 'benchmark', 'status': 'passed',
              'base_snapshot': 'base-one', 'patch_hash': 'patch-one', 'environment': {'image': 'same'},
              'benchmark_harness': {'benchmark.py': 'same'},
              'benchmark': {'workload': 'fixed workload', 'samples_seconds': [2.0, 2.1, 2.2]}}
    after = {**before, 'phase': 'benchmark_after', 'patch_hash': 'patch-two',
             'benchmark': {'workload': 'fixed workload', 'samples_seconds': [1.0, 1.1, 1.2]}}
    assert not verification.assess([*ordinary, before, after], [])['passed']


def test_worker_style_retrieval_persists_repository_scoped_vectors(store, monkeypatch):
    from backend import retrieval
    snapshot = register(store, 'scoped', {'app.py': 'def value(): return 2\n'})
    monkeypatch.setenv('STEWARD_EMBEDDING_MODEL', 'fixture-embedding')
    monkeypatch.setenv('STEWARD_MODEL_PROVIDER', 'openai')

    def transport(path, payload, **kwargs):
        return {'data': [{'index': index, 'embedding': [1.0, 0.0]}
                         for index, _ in enumerate(payload['input'])]}

    monkeypatch.setattr(providers, 'request', transport)
    index = db.get('repositories', 'scoped')['index']
    result = retrieval.retrieve(index, 'value behavior', semantic=True)
    assert 'semantic' in result['mode']
    vectors = db.list_records('vectors')
    assert vectors and all(item['repo_id'] == 'scoped' and item['snapshot_id'] == snapshot['id'] for item in vectors)
    second = register(store, 'other-scope', {'app.py': 'def value(): return 2\n'})
    retrieval.retrieve(db.get('repositories', 'other-scope')['index'], 'value behavior', semantic=True)
    other = [item for item in db.list_records('vectors') if item['repo_id'] == 'other-scope']
    assert other and all(item['snapshot_id'] == second['id'] for item in other)
    assert {item['id'] for item in vectors}.isdisjoint(item['id'] for item in other)


def test_zip_parent_case_aliases_do_not_silently_merge_directories(tmp_path):
    import io
    import zipfile
    from backend import repository
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('src/one.py', 'value = 1\n')
        archive.writestr('SRC/two.py', 'value = 2\n')
    with pytest.raises(ValueError, match='case|alias|duplicate|Duplicate'):
        repository.import_zip(stream.getvalue(), tmp_path / 'case-alias')


def test_javascript_single_arrow_parameter_does_not_create_imported_caller(tmp_path):
    from backend import repository
    (tmp_path / 'helper.js').write_text('export function value() { return 1; }\n', encoding='utf-8')
    (tmp_path / 'app.js').write_text('import { value } from "./helper.js";\nconst run = value => value();\nvalue();\n', encoding='utf-8')
    index = repository.index_repository(tmp_path)
    calls = [edge for edge in index['edges'] if edge['kind'] == 'calls' and edge['target'] == 'helper.js']
    assert [edge['line'] for edge in calls] == [3], calls


def test_embedding_runtime_integrity_failure_never_starts_process(tmp_path, monkeypatch):
    import hashlib
    executable = tmp_path / 'runner' / 'llama-server.exe'
    executable.parent.mkdir()
    executable.write_bytes(b'reviewed fixture executable')
    model = tmp_path / 'model.gguf'
    model.write_bytes(b'changed model bytes')
    manifest = {'executable': str(executable), 'model_file': str(model),
                'executable_sha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
                'model_sha256': '0' * 64}
    monkeypatch.setattr(providers.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('Unverified runtime started'))
    result = providers._launch_installed(manifest, embedding=True)
    assert result['started'] is False and 'integrity' in result['error']


def test_cancelled_embedding_response_never_persists_vectors(store, monkeypatch):
    from backend.contracts import CancellationToken
    monkeypatch.setenv('STEWARD_EMBEDDING_MODEL', 'fixture-embedding')
    monkeypatch.setenv('STEWARD_MODEL_PROVIDER', 'openai')
    token = CancellationToken()

    def late_response(*args, **kwargs):
        token.cancel()
        return {'data': [{'index': 0, 'embedding': [1.0, 0.0]}]}

    monkeypatch.setattr(providers, 'request', late_response)
    with pytest.raises(providers.ProviderError, match='cancelled'):
        providers.embed('repo', 'snapshot', [{'chunk_id': 'source', 'content': 'evidence'}], token)
    assert not db.list_records('vectors')


def test_skipped_feature_target_cannot_borrow_an_unrelated_passing_test(generic, monkeypatch):
    job, calls, complete = generic
    db.mutate('jobs', job['id'], lambda item: {**item, 'type': 'feature'})

    def acceptance(request, cancel=None):
        if request.task != 'acceptance':
            return complete(request, cancel)
        calls.append(request)
        return ProviderResult(output={
            'edits': [{'op': 'create', 'path': 'tests/test_feature.py', 'expected_hash': None,
                       'destination': None, 'content': 'import unittest\nfrom app import value\nclass FeatureTests(unittest.TestCase):\n    @unittest.skip("pending")\n    def test_requested_value(self):\n        self.assertEqual(value(), 2)\n    def test_unrelated(self):\n        self.assertTrue(True)\n',
                       'replacements': [], 'complete_file': False}],
            'coverage': [{'criterion_id': 'criterion-1', 'path': 'tests/test_feature.py', 'test': 'test_requested_value'}],
            'explanation': 'Feature checks',
        }, telemetry={'attempts': 1, 'usage': None})

    monkeypatch.setattr(providers, 'complete', acceptance)
    ordinary = verification.run

    def checked(run, base, candidate, plan, cancel=None):
        results = ordinary(run, base, candidate, plan, cancel)
        results.extend({'id': run + command['id'], 'check_id': command['id'], 'phase': 'candidate',
                        'status': 'passed', 'exit_code': 0, 'test_count': 2, 'skipped_count': 1,
                        'stdout': 'test_requested_value (test_feature.FeatureTests) ... skipped "pending"\ntest_unrelated (test_feature.FeatureTests) ... ok\nRan 2 tests\nOK (skipped=1)'}
                       for command in plan['commands'] if command.get('candidate_only'))
        return results

    monkeypatch.setattr(verification, 'run', checked)
    worker.process_job(job['id'])
    assert db.get('jobs', job['id'])['status'] == 'Failed'
    assert not db.list_records('revisions')


def test_actual_node_tap_passing_target_is_observed():
    # Captured from Node 24.13.0's TAP reporter running the fixed review fixture.
    record = {'status': 'passed', 'test_count': 1, 'skipped_count': 0,
              'stdout': 'TAP version 13\n# Subtest: feature runs\nok 1 - feature runs\n1..1\n# tests 1\n# pass 1\n# fail 0\n# skipped 0\n'}
    profile = {'profile': 'node-npm', 'commands': [{'argv': ['node', '--test'], 'kind': 'test'}]}
    assert worker.acceptance_observed(record, {'test': 'feature runs'}, profile)


def test_feature_only_command_does_not_invalidate_unchanged_baseline(monkeypatch):
    from backend import dependencies, sandbox
    seen = []
    monkeypatch.setattr(dependencies, 'prepare', lambda *args, **kwargs: {
        'sandbox_id': 'fixed', 'environment': 'image', 'cache_key': 'same'})
    monkeypatch.setattr(sandbox, 'cancel', lambda *args: None)

    def run(sid, command, phase, cancel=None):
        seen.append((phase, command['id']))
        return {'phase': phase, 'check_id': command['id'], 'status': 'passed',
                'stdout': 'Ran 1 test\nOK', 'exit_code': 0}

    monkeypatch.setattr(sandbox, 'run', run)
    plan = {'commands': [
        {'id': 'existing-tests', 'argv': ['python', '-m', 'unittest'], 'purpose': 'test'},
        {'id': 'feature-target', 'argv': ['python', '-m', 'unittest'], 'purpose': 'test', 'candidate_only': True},
    ]}
    verification.run('run', {'id': 'base', 'files': {'app.py': 'value = 1'}},
                     {'base_snapshot': 'base', 'patch_hash': 'patch', 'files': [
                         {'path': 'tests/test_new.py', 'after': 'def test_new(): assert True'}]}, plan)
    assert [phase for phase, name in seen if name == 'feature-target'] == ['candidate']
    assert [phase for phase, name in seen if name == 'existing-tests'] == ['baseline', 'regression_before', 'candidate']
