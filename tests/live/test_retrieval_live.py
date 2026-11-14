"""Actual services only; enable explicitly, never replace missing services with fixtures."""
import os
import json

import pytest

from backend import db, providers, repository, retrieval, snapshots
from backend.contracts import CancellationToken

pytestmark = [pytest.mark.live, pytest.mark.skipif(os.getenv('STEWARD_LIVE_RETRIEVAL') != '1', reason='Enable STEWARD_LIVE_RETRIEVAL=1 for actual service acceptance')]


@pytest.fixture
def live_source(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path / 'store')
    db.init()
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'helper.py').write_text('def total(value):\n    return value + 10\n', encoding='utf-8')
    snapshot = snapshots.capture('live-retrieval', root, {})
    index = repository.index_repository(root, repo_id='live-retrieval', source_snapshot_id=snapshot['id'])
    db.put('repositories', {'id': 'live-retrieval', 'source_snapshot_id': snapshot['id'], 'snapshot': index['snapshot'], 'index': index})
    return snapshot


def test_actual_embedding_chunks_are_scoped_and_dimensioned(live_source):
    assert providers.configuration()['embedding_model'], 'EMBEDDING UNAVAILABLE: no actual embedding model configured'
    chunks = retrieval.index_chunks('live-retrieval', live_source['id'])
    result = providers.embed('live-retrieval', live_source['id'], chunks, CancellationToken())
    assert len(result) == len(chunks) and result
    assert all(item['embedding'] and item['embedding_model'] for item in result)
    assert len({len(item['embedding']) for item in result}) == 1


def test_actual_qa_cites_real_source_ranges(live_source, record_property):
    assert providers.health()['available'], 'QA UNAVAILABLE: configure an actual model endpoint'
    answer = retrieval.answer('live-retrieval', live_source['id'], 'What does total return? Cite helper.py.', CancellationToken())
    record_property('actual_result', json.dumps({'mode': answer['mode'], 'citations': answer['citations'], 'provider': answer.get('provider')}))
    assert answer['mode'] == 'live evidence-linked answer', answer
    assert answer['citations'] and all(item['path'] == 'helper.py' and item['end_line'] <= 2 for item in answer['citations'])
