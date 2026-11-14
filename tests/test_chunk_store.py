from backend import db


def test_chunk_search_is_snapshot_and_repository_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init()
    def rows(name):
        return [{'id': name, 'path': name + '.py', 'start_line': 1, 'end_line': 2, 'content': 'price total'}]
    db.index_chunks('one', 'old', rows('old'))
    db.index_chunks('one', 'current', rows('current'))
    db.index_chunks('two', 'current', rows('other'))
    found = db.search_chunks('one', 'current', 'price')
    assert [item['chunk_id'] for item in found] == ['current']
    assert isinstance(found[0]['rank'], float)
    db.index_chunks('one', 'current', [])
    assert not db.list_chunks('one', 'current')
    assert db.list_chunks('one', 'old')[0]['chunk_id'] == 'old'
    assert db.list_chunks('two', 'current')[0]['chunk_id'] == 'other'
