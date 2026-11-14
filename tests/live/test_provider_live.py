"""Opt-in actual inference checks; run STEWARD_LIVE_TESTS=1 against a configured server."""
import os
import pytest

from backend import db, providers
from backend.contracts import CancellationToken, ProviderRequest

pytestmark = pytest.mark.skipif(os.getenv('STEWARD_LIVE_TESTS') != '1', reason='Live provider tests require explicit opt-in')


@pytest.mark.parametrize('task,payload', [
    ('investigation', {'source': {'path': 'greeting.py', 'content': 'def greet():\n    return "Hello world"\n'}, 'question': 'What does greet return?'}),
    ('planning', {'job': 'Change greeting.py greet to return Hello Duck Tape, preserve no-argument function.', 'source': 'def greet():\n    return "Hello world"\n'}),
    ('implementation', {'job': 'Change greet() to return Hello Duck Tape.', 'files': [{'path': 'greeting.py', 'hash': '55c51d764e28cc72c4499ae46c90e37fa1aed2ec03bbb7dd717b190ed08c0743a7', 'complete': True, 'content': 'def greet():\n    return "Hello world"\n'}]}),
    ('review', {'job': {'description': 'greet must return Hello Duck Tape and remain a no-argument function.'}, 'proposed_sources': [{'path': 'greeting.py', 'content': 'def greet():\n    return "Hello Duck Tape"\n'}], 'approved_knowledge': []}),
])
def test_live_role_schema(task, payload, monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init()
    result = providers.complete(ProviderRequest(task=task, payload=payload, cache=False, max_output_tokens=1024), CancellationToken())
    assert result.telemetry['mode'] == 'live'
    assert not result.telemetry['cached']
    if task == 'review':
        assert result.output['approved'], 'Seeded valid patch falsely rejected'
    if task == 'implementation':
        assert 'edits' in result.output, 'Model did not produce a concrete edit'


def test_live_rejects_seeded_incorrect_patch(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init()
    result = providers.complete(ProviderRequest(task='review', cache=False, max_output_tokens=1024, payload={
        'job': {'description': 'greet must return Hello Duck Tape.'},
        'proposed_sources': [{'path': 'greeting.py', 'content': 'def greet():\n    return "Goodbye"\n'}],
        'approved_knowledge': []}), CancellationToken())
    assert not result.output['approved']
    assert result.output['findings'][0]['reference']['path'] == 'greeting.py'
