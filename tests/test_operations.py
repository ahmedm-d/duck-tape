import io
import zipfile
import pytest
from backend import db, workflow


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init()


def test_archive_operation_is_durable_and_idempotent(workspace):
    from backend import operations
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('project/helper.py', 'def add(a, b):\n    return a + b\n')
    one = operations.submit('upload', '', {'name': 'Imported'}, archive=stream.getvalue(), idempotency_key='same')
    two = operations.submit('upload', '', {'name': 'Imported'}, archive=stream.getvalue(), idempotency_key='same')
    assert one['id'] == two['id'] and one['status'] == 'queued'
    operations.advance(one['id'])
    done = workflow.get_work(one['id'])
    assert done['status'] == 'complete', done
    repo = db.get('repositories', done['result']['id'])
    assert repo['source_snapshot_id'] and repo['index']['repo_id'] == repo['id']
    assert repo['index']['files'][0]['path'] == 'helper.py'
    operations.advance(one['id'])
    assert len(db.list_records('repositories')) == 1


def test_operation_failure_and_cancel_are_persisted(workspace):
    from backend import operations
    failed = operations.submit('upload', '', {'name': 'Bad'}, archive=b'bad')
    operations.advance(failed['id'])
    assert workflow.get_work(failed['id'])['status'] == 'failed'
    cancelled = operations.submit('upload', '', {'name': 'Cancelled'}, archive=b'bad')
    operations.cancel(cancelled['id'])
    operations.advance(cancelled['id'])
    assert workflow.get_work(cancelled['id'])['status'] == 'cancelled'
    assert not db.list_records('repositories')


def test_simultaneous_scan_requests_share_one_pending_operation(workspace):
    from backend import operations
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: operations.submit('scan', 'repo', {}), range(8)))
    assert len({r['id'] for r in results}) == 1


def test_health_uses_the_worker_service_heartbeat(workspace):
    from backend.main import health
    assert health()['worker']=='stopped'
    db.put('services',{'id':'worker','status':'running','heartbeat':db.now()})
    assert health()['worker']=='running'
    db.put('services',{'id':'worker','status':'running','heartbeat':'2000-01-01T00:00:00+00:00'})
    assert health()['worker']=='stopped'
