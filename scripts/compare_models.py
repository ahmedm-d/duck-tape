"""Run the provider smoke test (scripts/evaluate_models.py --smoke) once per model and tabulate the results.

Each model runs in its own scratch data directory under the output folder, so the real workspace is untouched.
Credentials come from the environment or the project .env (read by run.py); for Adaption, `ADAPTION_API_KEY`
falls back to `ADAPTION_API_KEY_1` from model-training/.env.training. Keys reach only the child process and are
never printed or written to the results.

The smoke test sends 8 real requests (investigation, planning, implementation, repair, an approve and a reject
review, regression and acceptance tests) and checks the answers statically; it does not execute generated code
(that is the Docker qualification gate). It hard-codes a 1024-token answer limit per request; `--max-output N`
overrides it so reasoning models are compared at a realistic budget.

Usage:
    python scripts/compare_models.py claude adaption local --max-output 8192 --output data/model-comparison
"""
import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# List price per 1M tokens (input, output), for the cost estimate only. Adaption credits and local runs are not priced.
PRICES = {'claude': (4.0, 20.0)}

MODELS = {
    'claude': {'STEWARD_MODEL_PROVIDER': 'anthropic', 'STEWARD_MODEL': 'claude-opus-5-5', 'STEWARD_MODEL_ENDPOINT': 'https://api.anthropic.com'},
    'adaption': {'STEWARD_MODEL_PROVIDER': 'openai', 'STEWARD_MODEL': 'qwen3.5-397b', 'STEWARD_MODEL_ENDPOINT': 'https://api.prod.adaptionlabs.ai/api/v1'},
    'local': {'STEWARD_MODEL_PROVIDER': 'ollama', 'STEWARD_MODEL': 'qwen3-coder:30b', 'STEWARD_MODEL_ENDPOINT': 'http://127.0.0.1:11434'},
}


def training_key(name):
    """Read one value from model-training/.env.training (dotenv-style: quotes and inline comments handled)."""
    path = REPO / 'model-training' / '.env.training'
    if not path.is_file():
        return None
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        key, sep, value = line.partition('=')
        if sep and key.strip() == name:
            value = value.strip()
            if value[:1] in ('"', "'"):
                end = value.find(value[0], 1)
                value = value[1:end] if end > 0 else value[1:]
            else:
                value = value.split(' #', 1)[0].split('\t#', 1)[0].strip()
            return value or None
    return None


def run(label, out, limit):
    env = {**{k:v for k,v in os.environ.items() if not k.startswith('STEWARD_')}, **MODELS[label],
           'STEWARD_SMALL_MODEL': MODELS[label]['STEWARD_MODEL'], 'STEWARD_CONTEXT_LIMIT': '128000',
           'STEWARD_DATA_DIR': str(out / ('store-' + label + '-' + uuid.uuid4().hex)), 'STEWARD_ATLAS_REQUIRED': '0',
           'STEWARD_EMBEDDING_MODEL': '', 'STEWARD_MODEL_TIMEOUT': '120', 'STEWARD_SPECIALIST_MODE': 'off',
           'PYTHONIOENCODING': 'utf-8'}
    env['STEWARD_MAX_OUTPUT_TOKENS'] = str(limit or 1024)
    if label == 'adaption':
        key = env.get('ADAPTION_API_KEY') or training_key('ADAPTION_API_KEY_1') or os.getenv('STEWARD_MODEL_KEY')
        if key:
            env['STEWARD_MODEL_KEY'] = key
    output = out / (f'{label}-{limit}.json' if limit else f'{label}.json')
    out.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    # Import instead of executing so child .env cannot restore unrelated routing overrides.
    boot = ('import sys; out=sys.argv[1]; sys.argv=["evaluate_models.py","--smoke","--output",out]; sys.path[:0]=[".","scripts"];'
            'import evaluate_models as m; real=m.ProviderRequest;'
            f'm.ProviderRequest=lambda **kw: real(**{{**kw,"max_output_tokens":{int(limit or 1024)}}}); raise SystemExit(m.main())')
    argv = [sys.executable, '-c', boot, str(output)]
    started = time.monotonic()
    failure = None
    try:
        proc = subprocess.run(argv, cwd=REPO, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=3600)
        # Smoke returns 1 for failed cases; only a fresh report can establish which cases ran.
        if proc.returncode not in (0, 1) or not output.is_file():
            failure = 'process_failed'
        else:
            result = json.loads(output.read_text(encoding='utf-8'))
            if not isinstance(result.get('attempts'), list) or not result['attempts']:
                raise ValueError('Missing attempts')
            return summarise(label, result, time.monotonic() - started, limit or 1024)
    except subprocess.TimeoutExpired:
        failure = 'process_timeout'
    except OSError:
        failure = 'process_failed'
    except (ValueError, TypeError, KeyError, AttributeError):
        failure = 'invalid_report'
    print(f'[{label}] failed: {failure}; no current smoke result accepted')
    return {'label': label, 'model': MODELS[label]['STEWARD_MODEL'], 'status': 'failed', 'error': failure,
            'passed': 0, 'total': 0, 'wall_s': round(time.monotonic() - started, 1), 'cases': [],
            'input_tokens': None, 'output_tokens': None, 'est_cost_usd': None}


def summarise(label, result, wall, limit):
    rows, tokens_in, tokens_out = [], 0, 0
    for item in result['attempts']:
        telemetry = item.get('telemetry') or {}
        attempts = telemetry.get('attempt_log') or [telemetry]
        for attempt in attempts:
            usage = attempt.get('usage') or {}
            for field in ('input_tokens', 'output_tokens'):
                value = usage.get(field)
                if field == 'input_tokens':
                    tokens_in = tokens_in + value if tokens_in is not None and type(value) is int and value >= 0 else None
                else:
                    tokens_out = tokens_out + value if tokens_out is not None and type(value) is int and value >= 0 else None
        expected = item.get('expected_approval')
        task = item['task'] + ('' if expected is None else (' (approve)' if expected else ' (reject)'))
        errors = [entry.get('error') for entry in telemetry.get('attempt_log', []) if entry.get('error')]
        actual_model = telemetry.get('model')
        rows.append({'case': task, 'passed': item['passed'] and actual_model == MODELS[label]['STEWARD_MODEL'], 'actual_model': actual_model,
                     'latency_s': round(telemetry.get('latency') or item.get('latency') or 0, 1),
                     'attempts': telemetry.get('attempts'), 'errors': errors})
    passed = sum(row['passed'] for row in rows)
    cost = None
    if label in PRICES and tokens_in is not None and tokens_out is not None:
        cost = tokens_in / 1e6 * PRICES[label][0] + tokens_out / 1e6 * PRICES[label][1]
    print(f'\n== {label}: {MODELS[label]["STEWARD_MODEL"]} (answer limit {limit})  {passed}/{len(rows)} passed  wall {wall:.0f}s  '
          f'tokens in {tokens_in} out {tokens_out}' + (f'  est ${cost:.2f}' if cost is not None else ''))
    for row in rows:
        status = 'PASS' if row['passed'] else 'FAIL'
        print(f"   {row['case']:<24} {status:<5} {row['latency_s']:6.1f}s  attempts={row['attempts']}  {' '.join(row['errors'])}")
    return {'label': label, 'model': MODELS[label]['STEWARD_MODEL'], 'status': 'passed' if passed == len(rows) else 'failed', 'answer_limit': limit, 'passed': passed, 'total': len(rows),
            'wall_s': round(wall, 1), 'input_tokens': tokens_in, 'output_tokens': tokens_out,
            'est_cost_usd': None if cost is None else round(cost, 4), 'cases': rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('models', nargs='*', help='any of: ' + ', '.join(MODELS) + ' (default: all)')
    parser.add_argument('--max-output', type=int, default=0, help='answer token limit per request (default: the smoke test\'s 1024)')
    parser.add_argument('--output', default='data/model-comparison', help='results folder (data/ is gitignored)')
    args = parser.parse_args(argv)
    if not 0 <= args.max_output <= 128000:
        parser.error('--max-output must be between 0 (default) and 128000')
    unknown = set(args.models) - set(MODELS)
    if unknown:
        parser.error('unknown model label(s): ' + ', '.join(sorted(unknown)))
    sys.path.insert(0, str(REPO))
    from run import load_environment
    load_environment()
    out = (REPO / args.output).resolve()
    summary = [run(label, out, args.max_output) for label in args.models or list(MODELS)]
    path = out / (f'summary-{args.max_output}.json' if args.max_output else 'summary.json')
    path.write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    return 0 if summary and all(item['status'] == 'passed' for item in summary) else 1


if __name__ == '__main__':
    raise SystemExit(main())
