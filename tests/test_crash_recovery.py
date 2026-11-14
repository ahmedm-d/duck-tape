"""Completed provider stages survive a worker death; incomplete calls are never success."""
from backend import db,worker
from test_live_pipeline_contract import generic
import pytest


def test_completed_investigation_plan_resume_without_duplicate_calls(generic,monkeypatch):
    job,calls,complete=generic
    def crash(request,cancel=None):
        if request.task=='implementation': raise KeyboardInterrupt('simulated worker process death')
        return complete(request,cancel)
    monkeypatch.setattr('backend.providers.complete',crash)
    try: worker.process_job(job['id'])
    except KeyboardInterrupt: pass
    db.mutate('jobs',job['id'],lambda item:{**item,'status':'Queued','lease_token':None,'lease_until':0})
    monkeypatch.setattr('backend.providers.complete',complete)
    worker.process_job(job['id'])
    assert db.get('jobs',job['id'])['status']=='Ready for review'
    assert len([request for request in calls if request.task=='investigation'])==1
    assert len([request for request in calls if request.task=='planning'])==1
    assert len(db.list_records('revisions'))==1


@pytest.mark.parametrize('where',['verification','after_publication'])
def test_crash_around_checks_and_atomic_publication_keeps_one_revision(generic,monkeypatch,where):
    job,calls,_=generic
    if where=='verification':
        from backend import verification
        original=verification.run
        monkeypatch.setattr(verification,'run',lambda *a,**kw: (_ for _ in ()).throw(KeyboardInterrupt('during sandbox check')))
    else:
        original=worker.publish
        def crash(*args):
            original(*args)
            raise KeyboardInterrupt('after atomic publication')
        monkeypatch.setattr(worker,'publish',crash)
    with pytest.raises(KeyboardInterrupt): worker.process_job(job['id'])
    if where=='verification':
        monkeypatch.setattr(verification,'run',original)
        db.mutate('jobs',job['id'],lambda item:{**item,'status':'Queued','lease_token':None,'lease_until':0})
    else:
        monkeypatch.setattr(worker,'publish',original)
    worker.process_job(job['id'])
    assert db.get('jobs',job['id'])['status']=='Ready for review'
    assert len(db.list_records('revisions'))==1
    assert len([request for request in calls if request.task=='implementation'])==1
