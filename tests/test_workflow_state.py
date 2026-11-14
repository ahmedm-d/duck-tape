import pytest
from backend import db


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init()
    return db.put('repositories', {'id': 'repo', 'snapshot': 'base'})


def test_expected_version_and_preparation_guard(workspace):
    from backend import workflow
    job = db.put('jobs', {'id': 'job', 'repo_id': 'repo', 'status': 'Awaiting approval', 'base_snapshot': 'base'})
    with pytest.raises(ValueError, match='transition'):
        workflow.transition('job', 'accept', {})
    changed = workflow.transition('job', 'approve', {}, expected_version=job['record_version'])
    assert changed['status'] == 'Queued' and changed['preparation_approved']
    with pytest.raises(workflow.Conflict, match='changed'):
        workflow.transition('job', 'cancel', {}, expected_version=job['record_version'])


def test_decision_and_job_commit_atomically(workspace):
    from backend import workflow
    db.put('jobs', {'id': 'job', 'repo_id': 'repo', 'status': 'Needs input', 'decision_id': 'decision'})
    db.put('decisions', {'id': 'decision', 'job_id': 'job', 'options': [{'id': 'reject'}, {'id': 'truncate'}]})
    workflow.transition('job', 'decide', {'decision_id': 'decision', 'option': 'reject'})
    assert db.get('jobs', 'job')['selected_decisions'] == {'decision': 'reject'}
    assert db.get('decisions', 'decision')['selected'] == 'reject'


def test_operation_idempotency_and_ownership(workspace):
    from backend import workflow
    first = workflow.enqueue('scan', 'repo', {'repo_id': 'repo'}, 'scan:repo:base')
    duplicate = workflow.enqueue('scan', 'repo', {'repo_id': 'repo'}, 'scan:repo:base')
    assert first['id'] == duplicate['id']
    claimed = workflow.claim_work(first['id'], 'worker-a')
    assert claimed['status'] == 'running'
    assert workflow.claim_work(first['id'], 'worker-b') is None


def test_expired_same_owner_attempt_cannot_checkpoint(workspace):
    from backend import workflow
    import json
    operation = workflow.enqueue('scan', 'repo', {}, 'scan-one')
    previous = workflow.claim_work(operation['id'], 'same-owner')
    previous['lease_until'] = 0
    with db.connect() as con:
        con.execute('UPDATE work_items SET data=? WHERE id=?', (json.dumps(previous), operation['id']))
    with pytest.raises(workflow.Conflict):
        workflow.checkpoint_work(operation['id'], 'same-owner', {'stage': 'late'}, lease_token=previous.get('lease_token'))
    latest = workflow.claim_work(operation['id'], 'same-owner')
    with pytest.raises(workflow.Conflict):
        workflow.checkpoint_work(operation['id'], 'same-owner', {'stage': 'late'}, lease_token=previous.get('lease_token'))
    assert workflow.checkpoint_work(operation['id'], 'same-owner', {'stage': 'current'}, lease_token=latest['lease_token'])['checkpoint']['stage'] == 'current'


def test_snooze_requires_real_timezone_aware_future_timestamp(workspace):
    from backend import workflow
    db.put('jobs', {'id': 'job', 'repo_id': 'repo', 'status': 'Proposed'})
    for invalid in ['tomorrow', '9999-99-99', '2099-01-01', '2099-01-01T00:00:00', '2000-01-01T00:00:00Z']:
        with pytest.raises(ValueError, match='timestamp'):
            workflow.transition('job', 'snooze', {'until': invalid})


def test_recovery_closes_abandoned_run_atomically(workspace):
    from backend import workflow
    db.put('jobs', {'id': 'job', 'repo_id': 'repo', 'status': 'Implementing', 'lease_token': 'dead', 'lease_until': 0})
    db.put('runs', {'id': 'run', 'job_id': 'job', 'lease_token': 'dead', 'status': 'running'})
    workflow.recover_job('job')
    assert db.get('jobs', 'job')['status'] == 'Queued'
    assert db.get('runs', 'run')['status'] == 'interrupted'


def test_cancel_invalidates_lease_and_approval(workspace):
    from backend import workflow
    db.put('jobs', {'id': 'job', 'repo_id': 'repo', 'status': 'Implementing', 'lease_token': 'owner', 'approval': {'revision_id': 'r'}})
    cancelled = workflow.transition('job', 'cancel', {})
    assert cancelled['status'] == 'Cancelled'
    assert cancelled['lease_token'] is None and cancelled['approval'] is None
    with pytest.raises(ValueError, match='transition'):
        workflow.transition('job', 'approve', {})


def test_atomic_rollback_preserves_all_records(workspace):
    def fail(con):
        db.put_in(con, 'notes', {'id': 'note', 'text': 'not committed'})
        raise ValueError('interrupted')
    with pytest.raises(ValueError):
        db.atomic(fail)
    assert db.get('notes', 'note') is None


def test_publication_closes_run_with_the_revision(workspace):
    from backend import worker
    import time
    db.put('jobs', {'id': 'job', 'repo_id': 'repo', 'status': 'Verifying', 'lease_token': 'owner', 'lease_until': time.time()+90})
    db.put('runs', {'id': 'run', 'job_id': 'job', 'lease_token': 'owner', 'status': 'running'})
    worker.publish('job', 'owner', {'id': 'revision', 'job_id': 'job'}, [])
    assert db.get('jobs', 'job')['current_revision'] == 'revision'
    assert db.get('runs', 'run')['status'] == 'complete'
    assert db.get('runs', 'run')['revision_id'] == 'revision'
