from backend import db, sample, workflow


def test_due_monitoring_is_queued_once_and_recovers(tmp_path, monkeypatch):
    from backend import monitoring
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init()
    repo = sample.seed_sample()
    db.mutate('repositories', repo['id'], lambda r: {**r, 'monitoring': True, 'next_scan': None})
    first = monitoring.schedule_due(db.now())
    assert len(first) == 1
    assert not monitoring.schedule_due(db.now())
    work = workflow.claim_work(first[0], 'crashed')
    assert work['status'] == 'running'
    assert len(workflow.list_work()) == 1
