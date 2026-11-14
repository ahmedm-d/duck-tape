"""Additive migrations preserve the prototype's record IDs and history."""


def migrate(con, now):
    current = con.execute('SELECT COALESCE(MAX(version),0) FROM migrations').fetchone()[0]
    if current < 2:
        con.executescript('''
          CREATE TABLE IF NOT EXISTS work_items(
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, subject TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE, data TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS work_items_subject ON work_items(kind,subject);
          CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
            repo_id UNINDEXED,snapshot UNINDEXED,path UNINDEXED,chunk_id UNINDEXED,
            start_line UNINDEXED,end_line UNINDEXED,content);
        ''')
        con.execute('INSERT INTO migrations VALUES(2,?)', (now(),))
