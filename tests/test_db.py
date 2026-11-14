import pytest
from backend import db


def test_persistence_and_atomic_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init()
    row = db.put('jobs', {'title': 'first', 'status': 'Queued'})
    db.init()
    assert db.get('jobs', row['id'])['title'] == 'first'
    db.mutate('jobs', row['id'], lambda j: {**j, 'status': 'Investigating'})
    assert db.get('jobs', row['id'])['status'] == 'Investigating'
    with pytest.raises(ValueError):
        db.mutate('jobs', row['id'], lambda j: (_ for _ in ()).throw(ValueError('stop')))
    assert db.get('jobs', row['id'])['status'] == 'Investigating'


def test_namespaces_are_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init()
    db.put('jobs', {'id': 'same', 'title': 'job'})
    db.put('notes', {'id': 'same', 'text': 'private'})
    assert db.get('jobs', 'same')['title'] == 'job'
    assert len(db.list_records('notes')) == 1
