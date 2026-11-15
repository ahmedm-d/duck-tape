"""scripts/compare_models.py helpers: no model is called."""
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
         'telemetry': {'latency': 2.0, 'attempts': 1, 'usage': {'input_tokens': 1000, 'output_tokens': 100}, 'attempt_log': [{'error': None}]}},
        {'task': 'regression', 'passed': False, 'error': 'ProviderError',
         'telemetry': {'latency': 9.0, 'attempts': 2, 'usage': {'input_tokens': 500, 'output_tokens': 50},
                       'attempt_log': [{'error': 'http_504'}, {'error': 'truncated'}]}},
    ]}
    summary = compare_models.summarise('claude', result, 11.0, 8192)
    assert (summary['passed'], summary['total'], summary['input_tokens'], summary['output_tokens']) == (1, 2, 1500, 150)
    assert summary['est_cost_usd'] == round(1500 / 1e6 * 4 + 150 / 1e6 * 20, 4)
    assert summary['cases'][0]['case'] == 'review (approve)'
    assert summary['cases'][1]['errors'] == ['http_504', 'truncated']
    assert '1/2 passed' in capsys.readouterr().out
    assert compare_models.summarise('local', result, 11.0, 1024)['est_cost_usd'] is None
