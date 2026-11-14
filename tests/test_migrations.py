import json
import sqlite3
from backend import db


def test_existing_database_migrates_without_losing_history(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    with sqlite3.connect(tmp_path / 'steward.sqlite3') as con:
        con.execute('CREATE TABLE records(kind TEXT,id TEXT,data TEXT,PRIMARY KEY(kind,id))')
        con.execute('INSERT INTO records VALUES(?,?,?)', ('jobs', 'original', json.dumps({'id': 'original', 'status': 'Ready for review', 'current_revision': 'patch'})))
    db.init()
    db.init()
    assert db.get('jobs', 'original')['current_revision'] == 'patch'
    with db.connect() as con:
        assert con.execute('SELECT MAX(version) FROM migrations').fetchone()[0] >= 2
        assert con.execute("SELECT name FROM sqlite_master WHERE name='work_items'").fetchone()
