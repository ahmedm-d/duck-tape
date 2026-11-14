"""Record model smoke tests or application qualification without counting health as success.

Full evaluation requires a pipeline driver returning independently verified evidence:
    driver(task: dict) -> {passed: bool, evidence: list, attempts: list, ...}
The driver must actually run checks against manifest sources and expected behavior.
"""
import argparse
import importlib
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import db, providers
from backend.contracts import CancellationToken, ProviderRequest


def source_identity():
    root=Path(__file__).resolve().parents[1]
    paths={root/'run.py',Path(__file__).resolve(),root/'scripts/qualification_driver.py',root/'tests/fixtures/model_eval/manifest.json'}
    for directory in ('backend','sandbox','tests/fixtures/model_eval/python','tests/fixtures/model_eval/typescript'):
        paths.update(path for path in (root/directory).rglob('*') if path.is_file() and '__pycache__' not in path.parts and path.suffix!='.pyc')
    files={path.relative_to(root).as_posix():hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths) if path.is_file()}
    git_base=subprocess.run(['git','rev-parse','HEAD'],cwd=root,capture_output=True,text=True,check=True).stdout.strip()
    dirty=bool(subprocess.run(['git','status','--porcelain'],cwd=root,capture_output=True,text=True,check=True).stdout.strip())
    return {'git_base':git_base,'dirty':dirty,'files':files,'aggregate_sha256':hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest()}


def smoke():
    """Minimal live schemas and two independent, seeded review judgments."""
    original = 'def greet():\n    return "Hello world"\n'
    import hashlib
    import uuid
    from backend import edits as edit_engine, snapshots
    scratch = db.DATA_DIR/'model-smoke'/uuid.uuid4().hex
    source = scratch/'source'
    source.mkdir(parents=True)
    (source/'greeting.py').write_bytes(original.encode())
    snapshot = snapshots.capture('model-smoke', source, {})
    edit_engine.read_file(snapshot['id'], 'greeting.py', hashlib.sha256(original.encode()).hexdigest())
    cases = [
        ('investigation', {'question': 'What does greet return?', 'files': [{'path': 'greeting.py', 'content': original}]}, None),
        ('planning', {'job': 'Change greet() to return Hello Duck Tape.', 'files': [{'path': 'greeting.py', 'content': original}]}, None),
        ('implementation', {'job': 'Change greet() to return Hello Duck Tape, preserve its signature.', 'files': [{'path': 'greeting.py', 'hash': hashlib.sha256(original.encode()).hexdigest(), 'complete': True, 'content': original}]}, None),
        ('review', {'job': {'description': 'greet must remain a no-argument function and return Hello Duck Tape.'}, 'proposed_sources': [{'path': 'greeting.py', 'content': 'def greet():\n    return "Hello Duck Tape"\n'}], 'approved_knowledge': []}, True),
        ('review', {'job': {'description': 'greet must return Hello Duck Tape.'}, 'proposed_sources': [{'path': 'greeting.py', 'content': 'def greet():\n    return "Goodbye"\n'}], 'approved_knowledge': []}, False),
    ]
    records = []
    for task, payload, approved in cases:
        started = time.monotonic()
        try:
            result = providers.complete(ProviderRequest(task=task, payload=payload, cache=False, max_output_tokens=1024), CancellationToken())
            passed = result.output.get('approved') == approved if approved is not None else True
            if task == 'implementation':
                edits = result.output.get('edits', [])
                passed = bool(edits) and any('Hello Duck Tape' in str(edit.get('content')) or 'Hello Duck Tape' in str(edit.get('replacements')) for edit in edits)
                passed = passed and all(edit['path'] == 'greeting.py' and edit['expected_hash'] == hashlib.sha256(original.encode()).hexdigest() for edit in edits)
                if passed:
                    candidate = edit_engine.apply(snapshot, edits, scratch/'candidate')
                    passed = (Path(candidate.worktree)/'greeting.py').read_bytes() == b'def greet():\n    return "Hello Duck Tape"\n'
            if task == 'planning' and 'steps' in result.output:
                passed = all(path == 'greeting.py' for step in result.output['steps'] for path in step['paths'])
            references = result.output.get('evidence', []) + [finding['reference'] for finding in result.output.get('findings', [])]
            passed = passed and all(ref['path'] == 'greeting.py' and 1 <= ref['start_line'] <= ref['end_line'] <= 2 for ref in references)
            records.append({'task': task, 'expected_approval': approved, 'passed': passed, 'output': result.output, 'telemetry': result.telemetry})
        except Exception as exc:
            records.append({'task': task, 'passed': False, 'error': type(exc).__name__, 'telemetry': getattr(exc, 'telemetry', {}), 'latency': time.monotonic()-started})
    return {'kind': 'provider_schema_smoke', 'qualified': False,
            'note': 'Schema/edit/review smoke is not the 12-task application qualification gate.', 'attempts': records}


def evaluate(manifest, driver=None, on_result=None):
    records = []
    for task in manifest['tasks']:
        if driver is None:
            records.append({'id': task['id'], 'passed': False, 'status': 'blocked', 'reason': 'Generic pipeline qualification driver is not configured', 'attempts': []})
            if on_result: on_result(records)
            continue
        begin = time.monotonic()
        try:
            result = driver(task)
            blocked=isinstance(result,dict) and result.get('passed') is False and result.get('status')=='blocked'
            if not isinstance(result, dict) or type(result.get('passed')) is not bool or not isinstance(result.get('evidence'), list) or not result['evidence'] or not isinstance(result.get('attempts'),list) or (not blocked and not result['attempts']):
                raise ValueError('Driver must return actual checks/evidence and provider attempts')
            records.append({'id': task['id'], **result, 'latency': time.monotonic()-begin})
        except Exception as exc:
            records.append({'id': task['id'], 'passed': False, 'status': 'failed', 'error': type(exc).__name__, 'latency': time.monotonic()-begin})
        if on_result: on_result(records)
    passed = {record['id'] for record in records if record['passed']}
    mandatory = {task['id'] for task in manifest['tasks'] if task.get('mandatory')}
    safety = {task['id'] for task in manifest['tasks'] if task.get('seeded_unsafe')}
    qualified = len(passed) >= manifest['gate']['minimum_completed'] and mandatory <= passed and safety <= passed
    return {'kind': 'application_qualification', 'qualified': qualified, 'completed': len(passed), 'total': len(records), 'results': records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', default='tests/fixtures/model_eval/manifest.json')
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--driver', default=os.getenv('STEWARD_EVALUATION_DRIVER'), help='module:function running actual pipeline and checks')
    parser.add_argument('--output', default='data/model-evaluation.json')
    args = parser.parse_args()
    db.init()
    identity=source_identity()
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if args.smoke:
        result = smoke()
    else:
        driver = None
        if args.driver:
            module, function = args.driver.split(':', 1)
            driver = getattr(importlib.import_module(module), function)
        manifest=json.loads(Path(args.manifest).read_text())
        def checkpoint(records):
            current=source_identity()
            partial={'kind':'application_qualification','qualified':False,'status':'running','recorded_at':db.now(),
                     'completed':sum(item['passed'] for item in records),'evaluated':len(records),'total':len(manifest['tasks']),
                     'results':records,'source_identity':identity,'source_identity_stable':current['aggregate_sha256']==identity['aggregate_sha256']}
            partial_path=destination.with_suffix('.partial.json')
            temporary=partial_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(partial,indent=2),encoding='utf-8'); temporary.replace(partial_path)
            print(json.dumps({'case':records[-1]['id'],'status':records[-1]['status'],'passed':records[-1]['passed'],'evaluated':len(records),'total':len(manifest['tasks'])}),flush=True)
        result = evaluate(manifest, driver,checkpoint)
    result['recorded_at'] = db.now()
    result['configuration'] = {role: providers.capabilities(role) for role in ('investigation', 'planning', 'regression', 'acceptance', 'implementation', 'repair', 'review')}
    result['source_identity']=identity
    result['source_identity_stable']=source_identity()['aggregate_sha256']==identity['aggregate_sha256']
    if not result['source_identity_stable']: result['qualified']=False
    destination.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'output': str(destination), 'qualified': result['qualified'], 'passed': sum(item['passed'] for item in result.get('attempts', result.get('results', [])))}))
    return 0 if result['qualified'] or (args.smoke and all(item['passed'] for item in result['attempts'])) else 1


if __name__ == '__main__':
    raise SystemExit(main())
