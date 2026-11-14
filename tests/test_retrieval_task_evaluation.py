"""Paired evaluation contracts; no model or imported code executes here."""
import json

import pytest

from scripts import evaluate_retrieval_tasks as evaluation


@pytest.mark.parametrize('mode, graph', [('keyword', False), ('graph', True)])
def test_mode_records_actual_context_without_changing_requested_evidence(monkeypatch, mode, graph):
    calls, saved = [], []
    result = {'items': [{'path': 'app.py', 'start_line': 4, 'end_line': 5}], 'snapshot': 'fixed', 'latency_ms': 7}
    def retrieve(*args, **kwargs):
        calls.append((args, kwargs))
        return result
    monkeypatch.setattr(evaluation.retrieval, 'retrieve', retrieve)
    monkeypatch.setattr(evaluation.db, 'put', lambda kind, value: saved.append((kind, value)))
    evaluation.install_mode(mode)
    assert evaluation.retrieval.retrieve('repo', 'snapshot', 'quote', paths=['app.py']) is result
    assert calls == [(('repo', 'snapshot', 'quote'), {'paths': ['app.py'], 'graph': graph, 'semantic': False})]
    assert saved[0][0] == 'retrieval_evaluations'
    assert saved[0][1]['context'] == result


def test_unknown_retrieval_mode_cannot_run_worker():
    with pytest.raises(ValueError):
        evaluation.install_mode('invented')


def test_sample_sources_remove_uninstallable_fixture_dependency():
    files = evaluation.source_files()
    assert files['requirements.txt'] == ''
    assert 'requirements.lock' not in files
    assert files['pricing.py'] == evaluation.FILES['pricing.py']
    assert {path for path, _, _ in evaluation.RELEVANT} == {'app.py', 'pricing.py', 'config.py', 'tests/test_quote.py'}


def test_holdout_requires_actual_values_and_successful_execution():
    from scripts.qualification_driver import behavior_matches
    expected = evaluation.EXPECTED
    result = {'status': 'passed', 'exit_code': 0, 'stdout': 'STEWARD_BEHAVIOR=' + json.dumps(expected)}
    assert behavior_matches(result, expected)
    assert not behavior_matches({**result, 'stdout': 'STEWARD_BEHAVIOR={"quantity_two":110}'}, expected)
    assert not behavior_matches({**result, 'exit_code': 1}, expected)


@pytest.fixture
def pair(monkeypatch, tmp_path):
    original = tmp_path / 'controller'
    monkeypatch.setattr(evaluation.db, 'DATA_DIR', original)
    monkeypatch.setattr(evaluation, 'source_identity', lambda: {'aggregate_sha256': 'fixed'})
    records = {mode: [{'mode': mode, 'context': {'mode': 'keyword only' if mode == 'keyword' else 'keyword + graph',
                'items': [{'path': 'app.py', 'start_line': 4, 'end_line': 5}], 'repo_id': mode, 'snapshot': 'immutable'}}]
               for mode in ('keyword', 'graph')}
    def driver(task):
        return {'passed': True, 'status': 'passed', 'workspace': str(tmp_path / task['mode']), 'attempts': [{'mode': 'live'}]}
    monkeypatch.setattr(evaluation.qualification, 'driver', driver)
    monkeypatch.setattr(evaluation.db, 'list_records', lambda kind: records[evaluation.db.DATA_DIR.name] if kind == 'retrieval_evaluations'
                        else [{'id': evaluation.db.DATA_DIR.name, 'source_snapshot_id': 'immutable'}])
    return records, tmp_path / 'report.json', original


@pytest.mark.parametrize('problem', ['missing', 'wrong_record_mode', 'wrong_context_mode', 'semantic', 'wrong_repo', 'wrong_snapshot'])
def test_passing_tasks_cannot_claim_valid_pair_without_actual_mode_evidence(pair, problem):
    records, destination, original = pair
    if problem == 'missing': records['keyword'] = []
    elif problem == 'wrong_record_mode': records['keyword'][0]['mode'] = 'graph'
    elif problem == 'wrong_context_mode': records['keyword'][0]['context']['mode'] = 'keyword + graph'
    elif problem == 'semantic': records['keyword'][0]['context']['mode'] = 'keyword + semantic'
    elif problem == 'wrong_repo': records['keyword'][0]['context']['repo_id'] = 'other'
    else: records['keyword'][0]['context']['snapshot'] = 'stale'
    report = evaluation.evaluate(destination)
    assert report['results'][0]['task_check_success'] is True
    assert report['results'][0]['measurement_valid'] is False
    assert report['passed'] is False and report['status'] == 'failed'
    assert evaluation.db.DATA_DIR == original


def test_valid_pair_requires_both_actual_modes_and_retains_retrieval_attempts(pair):
    records, destination, original = pair
    records['graph'].append(dict(records['graph'][0]))
    report = evaluation.evaluate(destination)
    assert report['passed'] and report['status'] == 'passed'
    assert [entry['mode'] for entry in report['results']] == ['keyword', 'graph']
    assert len(report['results'][1]['retrieval']) == 2
    assert json.loads(destination.read_text()) == json.loads(json.dumps(report))
    assert evaluation.db.DATA_DIR == original


def test_changed_harness_cannot_claim_stable_paired_source(pair, monkeypatch, tmp_path):
    _, destination, _ = pair
    harness = tmp_path / 'harness.py'; harness.write_bytes(b'initial reviewed harness')
    monkeypatch.setattr(evaluation, '__file__', str(harness))
    original = evaluation.qualification.driver
    def changed(task):
        result = original(task)
        if task['mode'] == 'keyword': harness.write_bytes(b'changed harness')
        return result
    monkeypatch.setattr(evaluation.qualification, 'driver', changed)
    report = evaluation.evaluate(destination)
    assert not report['source_identity_stable'] and not report['passed']


def test_driver_exception_restores_controller_database_and_class(pair, monkeypatch, tmp_path):
    _, destination, original = pair
    original_class = evaluation.qualification.Qualification
    def broken(task):
        evaluation.db.DATA_DIR = tmp_path / 'leftover'
        raise RuntimeError('Worker cleanup failed')
    monkeypatch.setattr(evaluation.qualification, 'driver', broken)
    with pytest.raises(RuntimeError, match='cleanup'): evaluation.evaluate(destination)
    assert evaluation.db.DATA_DIR == original
    assert evaluation.qualification.Qualification is original_class


def test_cli_loads_project_environment_before_evaluation(monkeypatch, tmp_path):
    import run
    loaded = []
    monkeypatch.setattr(run, 'load_environment', lambda: loaded.append(True))
    monkeypatch.setattr(evaluation.sys, 'argv', ['evaluate_retrieval_tasks', '--output', str(tmp_path / 'report.json')])
    def evaluate(destination):
        assert loaded == [True]
        return {'passed': True, 'source_identity_stable': True, 'results': [{'task_check_success': True}]}
    monkeypatch.setattr(evaluation, 'evaluate', evaluate)
    assert evaluation.main() == 0


@pytest.mark.parametrize('explicit', [False, True])
def test_cli_environment_reaches_already_imported_database_with_explicit_priority(monkeypatch, tmp_path, explicit):
    import run
    project = tmp_path / 'project'; project.mkdir()
    configured = tmp_path / 'configured'; exported = tmp_path / 'exported'
    (project / '.env').write_text('STEWARD_DATA_DIR=' + str(configured) + '\n')
    monkeypatch.setattr(run, 'ROOT', project)
    monkeypatch.setattr(evaluation.db, 'DATA_DIR', tmp_path / 'old')
    if explicit: monkeypatch.setenv('STEWARD_DATA_DIR', str(exported))
    else: monkeypatch.delenv('STEWARD_DATA_DIR', raising=False)
    monkeypatch.setattr(evaluation.sys, 'argv', ['evaluate_retrieval_tasks'])
    def evaluate(destination):
        assert evaluation.db.DATA_DIR == (exported if explicit else configured)
        return {'passed': False}
    monkeypatch.setattr(evaluation, 'evaluate', evaluate)
    assert evaluation.main() == 1
    # run.load_environment adds literal keys; restore even when the key was absent.
    monkeypatch.delenv('STEWARD_DATA_DIR', raising=False)


def test_unavailable_prerequisites_remain_blocked_without_fake_mode_measurements(pair, monkeypatch):
    _, destination, _ = pair
    monkeypatch.setattr(evaluation.qualification, 'driver', lambda task: {'passed': False, 'status': 'blocked', 'attempts': []})
    report = evaluation.evaluate(destination)
    assert not report['passed'] and report['status'] == 'blocked'
    assert len(report['results']) == 2
    assert all(not entry['measurement_valid'] and entry['result']['attempts'] == [] for entry in report['results'])


def test_pair_setup_uses_same_immutable_bytes_in_separate_generic_repositories(monkeypatch, tmp_path):
    original = evaluation.db.DATA_DIR
    contexts = []
    try:
        for mode in ('keyword', 'graph'):
            workspace = tmp_path / mode
            evaluation.db.DATA_DIR = workspace
            evaluation.db.init()
            context = evaluation.ParcelQualification({**evaluation.TASK, 'mode': mode}, workspace)
            context.setup(); contexts.append(context)
            assert not context.repo['is_sample'] and not context.repo['monitoring']
            assert evaluation.qualification.source_unchanged(workspace / 'source', context.expected_source)
            assert evaluation.snapshots.read_files(context.repo['source_snapshot_id']) == context.expected_source
        assert contexts[0].expected_source == contexts[1].expected_source
        assert contexts[0].repo['id'] != contexts[1].repo['id']
        assert contexts[0].workspace != contexts[1].workspace
    finally:
        for context in contexts: context.client.close()
        evaluation.db.DATA_DIR = original


def test_holdout_uses_bound_candidate_in_sandbox_and_cleans_up_on_failure(monkeypatch, tmp_path):
    context = evaluation.ParcelQualification(evaluation.TASK, tmp_path)
    context.repo = {'source_snapshot_id': 'base'}
    files = {'pricing.py': b'original'}
    changed = 'candidate'; calls = []; cancelled = []
    revision = {'patch_hash': 'patch', 'files': [{'path': 'pricing.py', 'after': changed,
                'hash': evaluation.hashlib.sha256(changed.encode()).hexdigest()}]}
    monkeypatch.setattr(evaluation.snapshots, 'read_files', lambda snapshot: dict(files))
    monkeypatch.setattr(evaluation.execution_profiles, 'discover', lambda snapshot: [{'profile': 'python-unittest'}])
    def prepare(run_id, snapshot, profile, cancel):
        calls.append((run_id, snapshot, profile, cancel))
        return {'sandbox_id': 'owned'}
    monkeypatch.setattr(evaluation.dependencies, 'prepare', prepare)
    def fail(sid, command, phase, cancel):
        assert sid == 'owned' and command['patch_hash'] == 'patch'
        assert command['argv'][:2] == ['python', '-c']
        assert command['timeout_seconds'] == 60 and cancel is context.cancel
        raise RuntimeError('Sandbox execution interrupted')
    monkeypatch.setattr(evaluation.sandbox, 'run', fail)
    monkeypatch.setattr(evaluation.sandbox, 'cancel', lambda run_id: cancelled.append(run_id))
    with pytest.raises(RuntimeError, match='interrupted'): context.holdout(revision, evaluation.EXPECTED, 'final')
    assert calls[0][1]['files'] == {'pricing.py': b'candidate'}
    assert calls[0][3] is context.cancel
    assert cancelled == [calls[0][0]]
    assert files == {'pricing.py': b'original'}
