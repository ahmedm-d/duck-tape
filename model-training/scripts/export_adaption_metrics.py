#!/usr/bin/env python3
"""Export Adaption AutoScientist run metadata and chart metrics where accessible.

The documented public API does not currently expose a dedicated metrics export.
Browser mode records JSON responses from the run's results page, then searches
those responses for time series with gradient norm / loss / learning rate data.

Install: pip install requests playwright && python -m playwright install chromium
Examples:
  python export_adaption_metrics.py --list-runs
  python export_adaption_metrics.py --run-id YOUR_RUN_ID
  python export_adaption_metrics.py --dashboard-url 'https://.../your/results/page' --browser
"""
import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

API_ROOT = 'https://api.prod.adaptionlabs.ai/api/v1'
METRIC_RE = re.compile(r'(grad(?:ient)?[_\s-]*norm|grad_norm|gradient_norm|train[/_-]loss|validation[/_-]loss|eval[/_-]loss|learning[_\s-]*rate|lr)', re.I)
GRAD_RE = re.compile(r'(grad(?:ient)?[_\s-]*norm)', re.I)
STEP_KEYS = ('step', 'global_step', 'training_step', 'iteration', 'epoch', 'x')
VALUE_KEYS = ('value', 'y', 'scalar', 'metric_value')


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
    print(f'Saved {path}')


def api_fetch(session, path):
    r = session.get(f'{API_ROOT}{path}', timeout=30)
    r.raise_for_status()
    return r.json()


def find_runs(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ('items', 'runs', 'data', 'results'):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def extract_series(obj, source):
    """Best-effort extraction; raw JSON is always retained for manual inspection."""
    found = []
    def add(metric, step, value, path):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            found.append({'source': source, 'path': path, 'metric': str(metric), 'step': step, 'value': value})

    def walk(node, path='$', metric_hint=None, step_hint=None):
        if isinstance(node, dict):
            metric_name = next((node[k] for k in ('metric', 'metric_name', 'name', 'tag', 'key', 'series') if isinstance(node.get(k), str) and METRIC_RE.search(node[k])), None)
            hint = metric_name or metric_hint
            step = next((node[k] for k in STEP_KEYS if k in node and isinstance(node[k], (int, float, str))), step_hint)
            # Common {step: 10, gradient_norm: 23.4} format.
            for k, v in node.items():
                if METRIC_RE.search(k) and isinstance(v, (int, float)):
                    add(k, step, v, f'{path}.{k}')
            # Common {metric: 'grad_norm', step: 10, value: 23.4} format.
            if hint:
                for k in VALUE_KEYS:
                    if k in node and isinstance(node[k], (int, float)):
                        add(hint, step, node[k], f'{path}.{k}')
                        break
            for k, v in node.items():
                next_hint = k if METRIC_RE.search(k) else hint
                walk(v, f'{path}.{k}', next_hint, step)
        elif isinstance(node, list):
            for i, child in enumerate(node):
                # Also supports [step, value] points inside a named metric series.
                if metric_hint and isinstance(child, list) and len(child) == 2 and isinstance(child[1], (int, float)):
                    add(metric_hint, child[0], child[1], f'{path}[{i}]')
                else:
                    walk(child, f'{path}[{i}]', metric_hint, step_hint)
    walk(obj)
    return found


def save_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['source','path','metric','step','value'])
        w.writeheader()
        w.writerows(rows)
    print(f'Saved {path} ({len(rows)} measurements)')


def browser_capture(url, output):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit('Browser mode requires: pip install playwright && python -m playwright install chromium')
    captured = []
    with sync_playwright() as p:
        # Persistent profile retains login across runs. Never export this directory.
        context = p.chromium.launch_persistent_context(str(output / '.browser_profile'), headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        def on_response(response):
            try:
                if response.request.resource_type not in ('xhr', 'fetch'):
                    return
                if not response.ok:
                    return
                ctype = response.headers.get('content-type', '').lower()
                if 'json' not in ctype:
                    return
                obj = response.json()
                # Only save candidate responses containing metric names, not every
                # account-related response returned by the dashboard.
                if not METRIC_RE.search(json.dumps(obj, ensure_ascii=False)):
                    return
                captured.append((response.url, obj))
                print(f'Captured metrics candidate #{len(captured)}')
            except Exception:
                pass
        page.on('response', on_response)
        page.goto(url, wait_until='domcontentloaded', timeout=60000)
        print('\nSign in if needed, then open the training results page.')
        print('Refresh the results page and scroll through the loss, learning-rate and gradient-norm charts.')
        input('When the charts have loaded, press ENTER here to save the captured data... ')
        context.close()
    rows = []
    for idx, (source, obj) in enumerate(captured, 1):
        write_json(output / 'raw_dashboard' / f'metrics_response_{idx:03}.json', obj)
        # Do not save response URLs (they could contain transient credentials).
        rows.extend(extract_series(obj, f'dashboard_response_{idx:03}'))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run-id', help='AutoScientist experiment ID (not necessarily the trained model name)')
    parser.add_argument('--list-runs', action='store_true', help='List runs to locate the correct experiment ID')
    parser.add_argument('--browser', action='store_true', help='Capture metrics used by the dashboard')
    parser.add_argument('--dashboard-url', help='Full URL of the AutoScientist results page')
    parser.add_argument('--output', default='adaption_export', help='Output directory')
    args = parser.parse_args()
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    all_rows = []

    if args.list_runs or args.run_id:
        key = os.environ.get('ADAPTION_API_KEY')
        if not key:
            sys.exit('Set ADAPTION_API_KEY in your environment before using the API mode.')
        try:
            import requests
        except ImportError:
            sys.exit('API mode requires: pip install requests')
        session = requests.Session()
        session.headers.update({'Authorization': f'Bearer {key}', 'Accept': 'application/json'})
        try:
            if args.list_runs:
                result = api_fetch(session, '/autoscientist')
                write_json(out / 'runs.json', result)
                for run in find_runs(result):
                    print(f"{run.get('id', 'unknown')} | {run.get('model', 'unknown')} | {run.get('status', 'unknown')}")
                if not args.run_id:
                    print('\nUse the correct ID with --run-id ID')
            if args.run_id:
                result = api_fetch(session, f'/autoscientist/{args.run_id}')
                write_json(out / 'run_metadata.json', result)
                print('Run status:', result.get('status'))
                print('Hyperparameters:', json.dumps(result.get('best_hyperparams'), indent=2))
                all_rows.extend(extract_series(result, 'run_metadata'))
        except requests.HTTPError as e:
            sys.exit(f'Adaption API error: {e.response.status_code} {e.response.text[:500]}')

    if args.browser:
        if not args.dashboard_url or not args.dashboard_url.startswith('https://'):
            sys.exit('--browser requires an https:// dashboard URL via --dashboard-url')
        all_rows.extend(browser_capture(args.dashboard_url, out))

    if not (args.list_runs or args.run_id or args.browser):
        parser.print_help()
        return
    if args.run_id or args.browser:
        save_csv(out / 'all_metrics.csv', all_rows)
        gradient_rows = [r for r in all_rows if GRAD_RE.search(r['metric'])]
        save_csv(out / 'gradient_norm.csv', gradient_rows)
        if gradient_rows:
            peak = max(gradient_rows, key=lambda r: r['value'])
            print(f"Peak gradient norm found: {peak['value']} at step {peak['step']}")
        else:
            print('No gradient-norm time series found in accessible responses.')
            print('The site may use another transport (e.g. WebSocket) or a non-JSON format.')
            print('Check the dashboard chart download button or ask Adaption support for the raw training logs.')

if __name__ == '__main__':
    main()
