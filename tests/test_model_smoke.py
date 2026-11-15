"""Fixture responses test smoke validation; these do not qualify a live model."""
from types import SimpleNamespace
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from backend import db
from scripts import evaluate_models


def response(request):
    task, payload = request.task, request.payload
    if task == 'investigation':
        return {'summary': 'Returns greeting', 'evidence': [], 'missing': []}
    if task == 'planning':
        return {'steps': [{'description': 'Update greeting', 'paths': ['greeting.py']}], 'acceptance': ['Returns requested greeting']}
    if task == 'review':
        good = 'Goodbye' not in str(payload)
        return {'approved': good, 'findings': [] if good else [{'message': 'Wrong greeting', 'reference': {'path': 'greeting.py', 'start_line': 2, 'end_line': 2}}]}
    file = payload['files'][0]
    content = file['content'].replace('Hello world', 'Hello Duck Tape')
    if task in ('regression', 'acceptance'):
        content += '\ndef test_requested_greeting():\n    assert greet() == "Hello Duck Tape"\n'
    result = {'edits': [{'op': 'update', 'path': file['path'], 'expected_hash': file['hash'], 'destination': None,
                         'content': content, 'replacements': [], 'complete_file': True}], 'explanation': 'Requested change'}
    if task == 'acceptance':
        result['coverage'] = [{'criterion_id': 'criterion-1', 'path': file['path'], 'test': 'test_requested_greeting'}]
    return result


def run_smoke(tmp_path, monkeypatch, corrupt=None):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init()
    def complete(request, cancel):
        output = response(request)
        if corrupt:
            corrupt(request, output)
        return SimpleNamespace(output=output, telemetry={'mode': 'fixture'})
    monkeypatch.setattr(evaluate_models.providers, 'complete', complete)
    return evaluate_models.smoke()


def test_smoke_exercises_all_worker_roles(tmp_path, monkeypatch):
    result = run_smoke(tmp_path, monkeypatch)
    assert {item['task'] for item in result['attempts']} == {'investigation', 'planning', 'regression', 'acceptance', 'implementation', 'repair', 'review'}
    assert all(item['passed'] for item in result['attempts']), result['attempts']
    assert result['qualified'] is False


@pytest.mark.parametrize('fault', ['hash', 'no_assertion', 'wrong_expected', 'coverage', 'source_edit', 'remove_existing'])
def test_test_writer_smoke_rejects_invalid_edits(tmp_path, monkeypatch, fault):
    def corrupt(request, output):
        if request.task != 'acceptance':
            return
        edit = output['edits'][0]
        if fault == 'hash': edit['expected_hash'] = '0' * 64
        if fault == 'no_assertion': edit['content'] = edit['content'].replace('assert greet() == "Hello Duck Tape"', 'pass')
        if fault == 'wrong_expected': edit['content'] = edit['content'].replace('Hello Duck Tape', 'Hello world')
        if fault == 'coverage': output['coverage'][0]['test'] = 'test_missing'
        if fault == 'source_edit': edit['path'] = 'greeting.py'
        if fault == 'remove_existing': edit['content'] = 'from greeting import greet\n\ndef test_requested_greeting():\n    assert greet() == "Hello Duck Tape"\n'
    result = run_smoke(tmp_path, monkeypatch, corrupt)
    assert next(item for item in result['attempts'] if item['task'] == 'acceptance')['passed'] is False


@pytest.mark.parametrize('override', [False, True])
def test_cli_loads_environment_before_backend_import(tmp_path, override):
    root = Path(evaluate_models.__file__).resolve().parents[1]
    (tmp_path/'scripts').mkdir()
    (tmp_path/'backend').mkdir()
    shutil.copyfile(root/'scripts/evaluate_models.py', tmp_path/'scripts/evaluate_models.py')
    shutil.copyfile(root/'run.py', tmp_path/'run.py')
    (tmp_path/'.env').write_text('STEWARD_MODEL=from-file\n', encoding='utf-8')
    expected = 'from-process' if override else 'from-file'
    (tmp_path/'backend/__init__.py').write_text(
        'import os\nassert os.environ["STEWARD_MODEL"] == ' + repr(expected) + '\ndb = providers = None\n', encoding='utf-8')
    (tmp_path/'backend/contracts.py').write_text('CancellationToken = ProviderRequest = None\n', encoding='utf-8')
    environment = {key: value for key, value in os.environ.items() if key != 'STEWARD_MODEL'}
    if override: environment['STEWARD_MODEL'] = expected
    result = subprocess.run([sys.executable, str(tmp_path/'scripts/evaluate_models.py'), '--help'],
                            env=environment, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'from-file' not in result.stdout and 'from-process' not in result.stdout
