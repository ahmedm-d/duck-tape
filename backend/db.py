"""SQLite record store. Each mutation holds a write transaction across its guard."""
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get('STEWARD_DATA_DIR', Path(__file__).resolve().parents[1] / 'data' / 'workspace'))


def now():
    return datetime.now(timezone.utc).isoformat()


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DATA_DIR / 'steward.sqlite3', timeout=30)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA busy_timeout=30000')
    return con


def init():
    with connect() as con:
        con.executescript('''
          CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY, applied_at TEXT);
          CREATE TABLE IF NOT EXISTS records(kind TEXT NOT NULL,id TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(kind,id));
          CREATE VIRTUAL TABLE IF NOT EXISTS documents USING fts5(repo_id UNINDEXED,snapshot UNINDEXED,path UNINDEXED,content);
          CREATE TABLE IF NOT EXISTS vectors(cache_key TEXT PRIMARY KEY, vector TEXT NOT NULL, metadata TEXT NOT NULL);
        ''')
        con.execute('INSERT OR IGNORE INTO migrations VALUES(1,?)', (now(),))
        from .migrations import migrate
        migrate(con, now)


def get(kind, id):
    with connect() as con:
        row = con.execute('SELECT data FROM records WHERE kind=? AND id=?', (kind, id)).fetchone()
    return json.loads(row[0]) if row else None


def list_records(kind):
    with connect() as con:
        rows = con.execute('SELECT data FROM records WHERE kind=? ORDER BY rowid', (kind,)).fetchall()
    return [json.loads(row[0]) for row in rows]


def get_in(con, kind, id):
    row = con.execute('SELECT data FROM records WHERE kind=? AND id=?', (kind, id)).fetchone()
    return json.loads(row[0]) if row else None


def put_in(con, kind, record):
    record = dict(record)
    record.setdefault('id', str(uuid.uuid4()))
    previous = get_in(con, kind, record['id'])
    record.setdefault('created_at', (previous or {}).get('created_at', now()))
    record['updated_at'] = now()
    record['record_version'] = (previous or {}).get('record_version', 0) + 1
    con.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)', (kind, record['id'], json.dumps(record)))
    return record


def atomic(fn):
    con = connect()
    try:
        con.execute('BEGIN IMMEDIATE')
        result = fn(con)
        con.commit()
        return result
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def put(kind, record):
    return atomic(lambda con: put_in(con, kind, record))


def mutate(kind, id, fn):
    con = connect()
    try:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT data FROM records WHERE kind=? AND id=?', (kind, id)).fetchone()
        if not row:
            raise KeyError(id)
        previous = json.loads(row[0])
        version = previous.get('record_version', 0)
        record = fn(previous)
        record['id'] = id
        record['updated_at'] = now()
        record['record_version'] = version + 1
        con.execute('UPDATE records SET data=? WHERE kind=? AND id=?', (json.dumps(record), kind, id))
        con.commit()
        return record
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def delete(kind, id):
    with connect() as con:
        con.execute('DELETE FROM records WHERE kind=? AND id=?', (kind, id))


def event(message, job_id=None, repo_id=None):
    return put('events', {'message': message, 'job_id': job_id, 'repo_id': repo_id, 'author': 'Duck Tape'})


def index_search(repo, con=None):
    def replace(con):
        con.execute('DELETE FROM documents WHERE repo_id=?', (repo['id'],))
        con.executemany('INSERT INTO documents VALUES(?,?,?,?)', [
            (repo['id'], repo['snapshot'], f['path'], f['content'])
            for f in repo['index']['files'] if f.get('status') == 'indexed' and f.get('content')
        ])
    return replace(con) if con is not None else atomic(replace)


def index_chunks(repo_id, snapshot_id, chunks):
    def replace(con):
        con.execute('DELETE FROM chunks WHERE repo_id=? AND snapshot=?', (repo_id, snapshot_id))
        con.executemany('INSERT INTO chunks VALUES(?,?,?,?,?,?,?)', [
            (repo_id, snapshot_id, item['path'], item.get('chunk_id', item.get('id')),
             item['start_line'], item['end_line'], item['content']) for item in chunks
        ])
    atomic(replace)


def list_chunks(repo_id, snapshot_id):
    with connect() as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute(
            'SELECT * FROM chunks WHERE repo_id=? AND snapshot=?', (repo_id, snapshot_id))]


def search_chunks(repo_id, snapshot_id, fts_query, limit=30):
    with connect() as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute(
            'SELECT *,bm25(chunks) AS rank FROM chunks WHERE chunks MATCH ? AND repo_id=? AND snapshot=? ORDER BY rank LIMIT ?',
            (fts_query, repo_id, snapshot_id, max(1, min(200, int(limit))))) ]
