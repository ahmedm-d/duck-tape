import copy
import hashlib
import json
from pathlib import Path

import pytest

from backend import db, providers, sandbox


def runner():
    from scripts import demo_backend
    return demo_backend


def test_prepare_isolated_generic_sources_without_inference_or_execution(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Preparation without explicit execution must not call inference or Docker')
    monkeypatch.setattr(providers, 'complete', forbidden)
    monkeypatch.setattr(providers, 'embed', forbidden)
    monkeypatch.setattr(sandbox, 'prepare', forbidden)
    previous = db.DATA_DIR
    root = tmp_path / 'demo'
    report = runner().prepare(root)
    assert db.DATA_DIR == previous
    assert report['status'] == 'blocked' and report['inference'] == 'not_run'
    assert set(report['cases']) == {'bug', 'security'}
    for case in report['cases'].values():
        assert case['fixture_hashes'] and not case['is_sample']
        assert case['baseline']['status'] == 'not_run'
    assert report['cases']['bug']['retrieval']['required_paths_present']
    assert report['cases']['security']['dependencies']['packages'][0]['version'] == '2.31.0'
    assert all(p['hashes'] for p in report['cases']['security']['dependencies']['packages'])
    assert report['catalogue']['provenance'] == 'captured_public_record'
    assert 'prompts' not in json.dumps(report)
    assert runner().inspect(root)['cases']['bug']['job_id'] == report['cases']['bug']['job_id']


def test_prepare_refuses_existing_directory_and_inspect_refuses_foreign_store(tmp_path):
    root = tmp_path / 'existing'
    root.mkdir()
    sentinel = root / 'keep.txt'
    sentinel.write_text('keep')
    with pytest.raises(ValueError, match='empty|exist'):
        runner().prepare(root)
    assert sentinel.read_text() == 'keep'
    with pytest.raises(ValueError, match='demo'):
        runner().inspect(root)


def test_inspect_detects_fixture_drift_without_replacing_source(tmp_path):
    root = tmp_path / 'demo'
    runner().prepare(root)
    source = root / 'sources' / 'bug' / 'pricing.py'
    source.write_text('changed source')
    with pytest.raises(ValueError, match='source|fixture'):
        runner().inspect(root)
    assert source.read_text() == 'changed source'


def evidence():
    digest = hashlib.sha256(b'patch').hexdigest()
    revision = {'id': 'revision', 'job_id': 'job', 'diff': 'patch', 'patch_hash': digest,
                'source_snapshot_id': 'source', 'assessment': {'passed': True, 'regression_reproduced': True},
                'review': {'approved': True, 'findings': []}}
    checks = [{'id': phase, 'revision_id': 'revision', 'patch_hash': digest, 'source_snapshot_id': 'source',
               'phase': phase, 'status': status, 'exit_code': 0 if status == 'passed' else 1,
               'environment': {'image': 'sha256:image', 'dependencies': 'locked'}}
              for phase, status in [('baseline', 'passed'), ('regression_before', 'failed'), ('candidate', 'passed')]]
    stages = [{'stage': role, 'job_id': 'job', 'status': 'complete', 'telemetry': {'mode': 'live', 'model': 'served-model', 'attempts': 1}}
              for role in ('implementation', 'review', 'regression')]
    run = {'id': 'run', 'job_id': 'job', 'revision_id': 'revision', 'status': 'complete'}
    return revision, checks, stages, run


def test_evidence_requires_live_model_actual_checks_and_bound_revision():
    good = evidence()
    assert runner().validate_evidence('bug', *good)['run_id'] == 'run'
    for index, mutate in [
        (0, lambda value: value.update(diff='changed')),
        (1, lambda value: value[2].update(status='failed')),
        (1, lambda value: value[0].update(patch_hash='wrong')),
        (1, lambda value: value[0].update(environment=None)),
        (2, lambda value: value[0]['telemetry'].update(mode='offline sample')),
        (2, lambda value: value[1]['telemetry'].update(attempts=0)),
        (3, lambda value: value.update(revision_id='other')),
    ]:
        bad = copy.deepcopy(good)
        mutate(bad[index])
        with pytest.raises(ValueError):
            runner().validate_evidence('bug', *bad)


def test_run_missing_provider_blocks_before_job_approval(tmp_path, monkeypatch):
    root = tmp_path / 'demo'
    prepared = runner().prepare(root)
    monkeypatch.setattr(providers, 'health', lambda: {'available': False, 'mode': 'offline sample'})
    monkeypatch.setattr(sandbox, 'health', lambda: {'available': False})
    result = runner().run(root, 'bug')
    assert result['status'] == 'blocked'
    assert result['job_status'] == 'Awaiting approval'
    assert result['job_id'] == prepared['cases']['bug']['job_id']
    assert result['inference'] == 'not_run'


def test_plan_approval_requires_current_exact_hash():
    plan = {'status': 'Awaiting approval', 'plan_hash': 'current', 'approach': [{'description': 'Fix', 'paths': ['pricing.py']}]}
    assert not runner().plan_approved(plan, None)
    assert runner().plan_approved(plan, 'current')
    with pytest.raises(ValueError, match='plan'):
        runner().plan_approved(plan, 'old')
    with pytest.raises(ValueError, match='plan'):
        runner().plan_approved({**plan, 'approach': []}, 'current')


@pytest.mark.parametrize('terminal', ['Awaiting approval', 'Needs input', 'Failed'])
def test_live_run_stops_at_plan_decision_or_failure(tmp_path, monkeypatch, terminal):
    demo = runner()
    root = tmp_path / 'demo'
    prepared = demo.prepare(root)
    job_id = prepared['cases']['bug']['job_id']
    monkeypatch.setattr(providers, 'health', lambda: {'available': True})
    monkeypatch.setattr(sandbox, 'health', lambda: {'available': True})
    monkeypatch.setattr(demo, 'baseline', lambda *args, **kwargs: {'status': 'passed', 'checks': ['actual-check']})
    def start(context):
        db.mutate('jobs', job_id, lambda job: {**job, 'status': terminal, 'plan_hash': 'exact-plan',
                                             'approach': [{'description': 'Fix quantity', 'paths': ['pricing.py']}]})
        if terminal == 'Failed':
            db.put('run_stages', {'job_id': job_id, 'status': 'failed', 'telemetry': {'mode': 'live', 'attempts': 1}})
    monkeypatch.setattr(demo.Qualification, 'start', start)
    monkeypatch.setattr(demo.Qualification, 'stop', lambda context: None)
    result = demo.run(root, 'bug')
    assert result['status'] == ('failed' if terminal == 'Failed' else 'blocked')
    assert result['job_status'] == terminal
    assert result.get('plan_hash') == ('exact-plan' if terminal == 'Awaiting approval' else None)
    assert not result.get('verified')
    if terminal == 'Failed':
        assert result['inference'] == 'live'
    if terminal == 'Awaiting approval':
        with pytest.raises(ValueError, match='plan'):
            demo.run(root, 'bug', approve_plan='wrong')


def test_dependency_downloads_require_explicit_opt_in_and_baseline_cleans_up(tmp_path, monkeypatch):
    demo = runner()
    root = tmp_path / 'demo'
    prepared = demo.prepare(root)
    from backend import dependencies
    calls = []
    def prepare(run_id, snapshot, plan, cancel):
        calls.append(run_id)
        return {'sandbox_id': 'sandbox', 'environment': 'sha256:image', 'cache_key': 'deps', 'artifacts': []}
    monkeypatch.setattr(sandbox, 'health', lambda: {'available': True})
    monkeypatch.setattr(dependencies, 'prepare', prepare)
    monkeypatch.setattr(sandbox, 'run', lambda *args: {'id': 'check', 'status': 'passed', 'exit_code': 0})
    cleanup = []
    monkeypatch.setattr(sandbox, 'cancel', cleanup.append)
    with demo.workspace(root):
        blocked = demo.baseline(prepared['cases']['security'])
        assert blocked['status'] == 'blocked' and not calls
        actual = demo.baseline(prepared['cases']['security'], allow_dependency_downloads=True)
    assert actual['status'] == 'passed' and cleanup == calls


def test_evidence_rejects_unrelated_stages_and_nonpassing_integrity():
    revision, checks, stages, record = evidence()
    checks.append({**checks[0], 'id': 'integrity', 'phase': 'test_integrity', 'status': 'failed'})
    with pytest.raises(ValueError):
        runner().validate_evidence('bug', revision, checks, stages, record)


def test_atlas_demo_requires_actual_worker_selected_evidence():
    args = evidence()
    with pytest.raises(ValueError, match='Atlas'):
        runner().validate_evidence('bug', *args, require_atlas=True)
    args[2][0]['input'] = {'payload': {'context': {'items': [{'reason': 'Atlas vector candidate 0.9'}]}}}
    assert runner().validate_evidence('bug', *args, require_atlas=True)['worker_atlas_selected_count'] == 1


def test_bug_requires_actual_test_writer():
    revision, checks, stages, record = evidence()
    with pytest.raises(ValueError, match='regression'):
        runner().validate_evidence('bug', revision, checks, stages[:2], record)


def test_atlas_opt_in_persists_without_claiming_failed_sync_as_retrieval(tmp_path, monkeypatch):
    from backend import data_platforms
    demo = runner()
    def unavailable(*args):
        raise data_platforms.DataPlatformError('unavailable')
    monkeypatch.setattr(data_platforms, 'atlas_sync', unavailable)
    monkeypatch.setenv('STEWARD_EMBEDDING_MODEL', 'configured-embedding')
    root = tmp_path / 'demo'
    report = demo.prepare(root, atlas_retrieval=True)
    assert report['atlas_retrieval'] is True
    assert report['cases']['bug']['retrieval']['atlas_selected_count'] == 0
    assert report['cases']['bug']['retrieval']['atlas_sync']['status'] == 'blocked'
    with demo.workspace(root):
        import os
        assert os.environ['STEWARD_ATLAS_RETRIEVAL'] == '1'
        assert os.environ['STEWARD_EMBEDDING_MODEL'] == 'configured-embedding'


def test_atomic_report_retries_transient_windows_file_lock(tmp_path, monkeypatch):
    demo = runner()
    original = Path.replace
    calls = []
    def replace(path, target):
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError('temporarily open by filesystem scanner')
        return original(path, target)
    monkeypatch.setattr(Path, 'replace', replace)
    demo.save(tmp_path, {'status': 'blocked'})
    assert json.loads((tmp_path / demo.MANIFEST).read_text()) == {'status': 'blocked'}


def test_cli_reports_blocked_with_exit_two(tmp_path, monkeypatch, capsys):
    demo = runner()
    monkeypatch.setattr(demo, 'inspect', lambda root: {'status': 'blocked', 'inference': 'not_run'})
    assert demo.main(['inspect', '--workspace', str(tmp_path)]) == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'blocked'
