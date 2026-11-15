"""scripts/compare_models.py helpers: no model is called."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from scripts import compare_models


def test_training_key_reads_dotenv_style_values(monkeypatch, tmp_path):
    (tmp_path / 'model-training').mkdir()
    (tmp_path / 'model-training' / '.env.training').write_text(
        'ADAPTION_API_KEY_1=pt_plain  # account 1\nADAPTION_API_KEY_2="pt_quoted # not a comment"\nEMPTY=\n', encoding='utf-8')
    monkeypatch.setattr(compare_models, 'REPO', tmp_path)
    assert compare_models.training_key('ADAPTION_API_KEY_1') == 'pt_plain'
    assert compare_models.training_key('ADAPTION_API_KEY_2') == 'pt_quoted # not a comment'
    assert compare_models.training_key('EMPTY') is None
    assert compare_models.training_key('MISSING') is None


def test_unknown_model_label_is_rejected():
    with pytest.raises(SystemExit):
        compare_models.main(['claude', 'gpt'])


def test_summary_counts_passes_tokens_retries_and_cost(capsys):
    result = {'attempts': [
        {'task': 'review', 'expected_approval': True, 'passed': True,
         'telemetry': {'model': 'claude-opus-5-5', 'latency': 2.0, 'attempts': 1, 'usage': {'input_tokens': 1000, 'output_tokens': 100}, 'attempt_log': [{'error': None, 'usage': {'input_tokens': 1000, 'output_tokens': 100}}]}},
        {'task': 'regression', 'passed': False, 'error': 'ProviderError',
         'telemetry': {'model': 'claude-opus-5-5', 'latency': 9.0, 'attempts': 2, 'usage': {'input_tokens': 500, 'output_tokens': 50},
                       'attempt_log': [{'error': 'http_504', 'usage': {'input_tokens': 200, 'output_tokens': 20}}, {'error': 'truncated', 'usage': {'input_tokens': 500, 'output_tokens': 50}}]}},
    ]}
    summary = compare_models.summarise('claude', result, 11.0, 8192)
    assert (summary['passed'], summary['total'], summary['input_tokens'], summary['output_tokens']) == (1, 2, 1700, 170)
    assert summary['est_cost_usd'] == round(1700 / 1e6 * 4 + 170 / 1e6 * 20, 4)
    assert summary['cases'][0]['case'] == 'review (approve)'
    assert summary['cases'][1]['errors'] == ['http_504', 'truncated']
    assert '1/2 passed' in capsys.readouterr().out
    assert compare_models.summarise('local', result, 11.0, 1024)['est_cost_usd'] is None


def test_unknown_usage_and_wrong_model_are_not_counted_as_success():
    summary = compare_models.summarise('claude', {'attempts': [
        {'task': 'review', 'passed': True, 'telemetry': {'model': 'wrong-model', 'attempt_log': [
            {'usage': None}, {'usage': {'input_tokens': 10, 'output_tokens': 20}}]}}
    ]}, 1, 1024)
    assert summary['passed'] == 0
    assert summary['input_tokens'] is None and summary['output_tokens'] is None
    assert summary['est_cost_usd'] is None


@pytest.mark.parametrize('limit', ['-1', '128001'])
def test_invalid_output_limit_is_rejected(limit, monkeypatch):
    monkeypatch.setattr(compare_models, 'run', lambda *args: pytest.fail('Invalid arguments must not launch inference'))
    with pytest.raises(SystemExit):
        compare_models.main(['claude', '--max-output', limit])


@pytest.mark.parametrize('failure', ['exit', 'timeout', 'invalid-report'])
def test_crashed_comparison_cannot_reuse_old_report(monkeypatch, tmp_path, capsys, failure):
    old = tmp_path / 'claude-1024.json'
    old.write_text(json.dumps({'attempts': [{'task': 'review', 'passed': True}]}))
    def execute(argv, **kwargs):
        assert not old.exists()
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(argv, 3600, stderr='secret-value')
        if failure == 'invalid-report':
            old.write_text('not JSON')
        return SimpleNamespace(returncode=1, stderr='secret-value')
    monkeypatch.setattr(compare_models.subprocess, 'run', execute)
    result = compare_models.run('claude', tmp_path, 1024)
    assert result['status'] == 'failed' and result['passed'] == 0
    assert 'secret-value' not in capsys.readouterr().out


def test_child_isolates_routing_and_records_exact_model(monkeypatch, tmp_path):
    monkeypatch.setenv('STEWARD_SMALL_MODEL', 'wrong-model')
    monkeypatch.setenv('STEWARD_REVIEW_MODEL', 'wrong-review-model')
    monkeypatch.setenv('STEWARD_MODEL_KEY', 'unrelated-secret')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'claude-test-secret')
    def execute(argv, **kwargs):
        env = kwargs['env']
        assert env['STEWARD_SMALL_MODEL'] == env['STEWARD_MODEL'] == 'claude-opus-5-5'
        assert env['STEWARD_MAX_OUTPUT_TOKENS'] == '1024'
        assert 'STEWARD_REVIEW_MODEL' not in env and 'STEWARD_MODEL_KEY' not in env
        assert env['ANTHROPIC_API_KEY'] == 'claude-test-secret'
        assert 'load_environment' not in argv[2]
        assert kwargs['timeout'] == 3600
        (tmp_path / 'claude.json').write_text(json.dumps({'attempts': [
            {'task': 'review', 'passed': True, 'telemetry': {'model': 'claude-opus-5-5', 'usage': {'input_tokens': 1, 'output_tokens': 2}}}
        ]}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(compare_models.subprocess, 'run', execute)
    result = compare_models.run('claude', tmp_path, 0)
    assert result['passed'] == 1 and result['cases'][0]['actual_model'] == 'claude-opus-5-5'
