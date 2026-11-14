"""Independent release review regressions; no live provider calls."""
import asyncio
import hashlib
import json
import subprocess
import sys
import time

import pytest

from scripts import acceptance


def test_acceptance_timeout_stops_an_owned_child_process(tmp_path):
    heartbeat = tmp_path / 'child-heartbeat'
    stop = tmp_path / 'stop-child'
    child_code = (
        'from pathlib import Path; import time\n'
        f'path = Path({str(heartbeat)!r})\n'
        f'stop = Path({str(stop)!r})\n'
        'deadline = time.monotonic() + 60\n'
        'while not stop.exists() and time.monotonic() < deadline:\n path.write_text(str(time.time_ns())); time.sleep(.05)\n'
    )
    parent_code = (
        'import subprocess,sys,time\n'
        f'child=subprocess.Popen([sys.executable,"-c",{child_code!r}])\n'
        'time.sleep(60)\n'
    )
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            acceptance.run_command([sys.executable, '-c', parent_code], timeout=2)
        assert heartbeat.is_file(), 'The child must actually run before the timeout'
        before = heartbeat.read_bytes()
        time.sleep(.3)
        assert heartbeat.read_bytes() == before, 'Owned child survived its controller timeout'
    finally:
        stop.write_text('stop')


def test_acceptance_interrupt_cannot_publish_a_success(tmp_path):
    gate = acceptance.commands('deterministic', tmp_path)[0]
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt
    result = acceptance.execute(gate, {'git': {'available': True}}, tmp_path, runner=interrupted)
    assert result['status'] == 'failed'
    assert acceptance.exit_code(acceptance.summarize('deterministic', [result])) == 1


def test_api_shutdown_preserves_shared_runner_and_next_start_reuses_it(tmp_path, monkeypatch):
    from backend import db, main, providers, sample
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path / 'workspace')
    monkeypatch.setattr(sample, 'seed_sample', lambda: {})
    monkeypatch.setattr(sample, 'seed_jobs', lambda repo: None)
    monkeypatch.delenv('STEWARD_EMBEDDED_WORKER', raising=False)
    monkeypatch.setattr(providers, '__file__', str(tmp_path / 'backend/providers.py'))
    executable, model = tmp_path / 'llama-server.exe', tmp_path / 'weights.gguf'
    executable.write_bytes(b'reviewed test executable')
    model.write_bytes(b'reviewed test weights')
    manifest = {'executable': str(executable), 'model_file': str(model),
                'executable_sha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
                'model_sha256': hashlib.sha256(model.read_bytes()).hexdigest(),
                'endpoint': 'http://127.0.0.1:49152/v1', 'port': 49152,
                'alias': 'stub-review-model', 'context_limit': 16384, 'threads': 1}
    monkeypatch.setattr(providers, 'installed_runtime', lambda **kwargs: manifest)
    monkeypatch.setattr(providers, 'configuration', lambda: {
        'kind': 'openai', 'endpoint': manifest['endpoint'], 'model': manifest['alias'],
        'context_limit': 16384, 'embedding_model': '', 'embedding_endpoint': ''})
    monkeypatch.setattr(providers, 'health', lambda: {'available': False})
    launched = []
    class Runner:
        def __init__(self, *args, **kwargs):
            self.returncode = None
            launched.append(self)
        def poll(self):
            return self.returncode
        def terminate(self):
            self.returncode = 0
        def kill(self):
            self.returncode = -1
        def wait(self, timeout=None):
            if self.returncode is None:
                raise subprocess.TimeoutExpired('stub runner', timeout)
            return self.returncode
    monkeypatch.setattr(providers.subprocess, 'Popen', Runner)
    # Model binding through the fake child's lifetime; never probe a live model port.
    def occupied(port):
        assert port == manifest['port']
        return any(child.poll() is None for child in launched)
    monkeypatch.setattr(providers, '_port_occupied', occupied)
    async def exercise():
        async with main.lifespan(main.app):
            assert len(launched) == 1 and launched[0].poll() is None
        assert launched[0].poll() is None
        assert not providers.start_local_runner()['started']
        assert len(launched) == 1, 'An occupied service with failed health must not be duplicated'
        monkeypatch.setattr(providers, 'health', lambda: {'available': True})
        async with main.lifespan(main.app):
            assert len(launched) == 1, 'A healthy shared service must be reused'
        assert launched[0].poll() is None
    asyncio.run(exercise())


def test_repeated_qa_reuses_cache_when_only_retrieval_latency_changes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from backend import db, providers, repository, retrieval, snapshots
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path / 'store')
    db.init()
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'helper.py').write_text('def value(): return 1\n')
    source = snapshots.capture('review-repo', root, {})
    index = repository.index_repository(root, repo_id='review-repo', source_snapshot_id=source['id'])
    db.put('repositories', {'id': 'review-repo', 'root': str(root), 'source_snapshot_id': source['id'],
                            'snapshot': index['snapshot'], 'index': index})
    for key, value in {'STEWARD_MODEL': 'review-stub', 'STEWARD_MODEL_PROVIDER': 'openai',
                       'STEWARD_MODEL_ENDPOINT': 'http://fixture.invalid/v1', 'STEWARD_CONTEXT_LIMIT': '16000',
                       'STEWARD_EMBEDDING_MODEL': ''}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(providers, 'health', lambda: {'available': True})
    clocks = iter([0, .001, 0, .002])
    monkeypatch.setattr(retrieval, 'time', SimpleNamespace(perf_counter=lambda: next(clocks)))
    calls = []
    def request(*args, **kwargs):
        calls.append(args)
        answer = {'answer': 'The function returns one.',
                  'citations': [{'path': 'helper.py', 'start_line': 1, 'end_line': 1}]}
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(answer)}}]}
    monkeypatch.setattr(providers, 'request', request)
    first = retrieval.answer('review-repo', source['id'], 'value')
    second = retrieval.answer('review-repo', source['id'], 'value')
    assert first['context']['latency_ms'] != second['context']['latency_ms']
    assert first['mode'] == second['mode'] == 'live evidence-linked answer'
    assert len(calls) == 1 and second['provider']['cached'] is True
