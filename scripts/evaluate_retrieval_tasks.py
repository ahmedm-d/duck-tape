"""Paired live task/check outcomes for keyword versus graph retrieval.

Uses a fresh copy of the reviewed Parcel source for each mode, the generic live
worker, and real Docker checks. The fixture advisory lock is replaced by an empty
requirements.txt so the repository has an honest supported dependency profile.
No fixture provider, host execution, edited response, or inferred success.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import db, dependencies, execution_profiles, retrieval, sandbox, snapshots
from backend.sample import FILES
from scripts import qualification_driver as qualification
from scripts.evaluate_models import source_identity
from scripts.evaluate_retrieval import score_evidence

RELEVANT = [('app.py', 4, 5), ('pricing.py', 1, 3), ('config.py', 1, 2), ('tests/test_quote.py', 4, 8)]
EXPECTED = {'quantity_two': 220, 'default_quantity': 110, 'quantity_zero': 0, 'empty': 0, 'integer_tax': 326}
TASK = {'id': 'parcel-quantity', 'language': 'python', 'repository': 'python',
        'journey': 'bug-regression', 'source_path': 'pricing.py',
        'budget': {'max_provider_calls': 12, 'max_output_tokens': 30000, 'max_repairs': 2,
                   'max_evidence_rounds': 2, 'deadline_seconds': 1800}}


def source_files():
    return {**{path: content for path, content in FILES.items() if path != 'requirements.lock'}, 'requirements.txt': ''}


def install_mode(mode):
    if mode not in {'keyword', 'graph'}:
        raise ValueError('Unknown retrieval mode')
    original = retrieval.retrieve
    def measured(*args, **kwargs):
        result = original(*args, **{**kwargs, 'graph': mode == 'graph', 'semantic': False})
        db.put('retrieval_evaluations', {'id': uuid.uuid4().hex, 'mode': mode, 'context': result})
        return result
    retrieval.retrieve = measured


class ParcelQualification(qualification.Qualification):
    def setup(self):
        from fastapi.testclient import TestClient
        from backend.main import app, register_repo
        source = self.workspace / 'source'
        for name, content in source_files().items():
            destination = source / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content.encode())
        self.expected_source = {path: content.encode() for path, content in source_files().items()}
        self.repo = register_repo(source, 'Paired retrieval: Parcel', {'source': 'fixed Parcel evaluation', 'fixture_sources_only': True})
        if self.repo.get('is_sample'):
            raise ValueError('Paired evaluation requires the generic live pipeline')
        self.client = TestClient(app)

    def start(self):
        if self.process and self.process.poll() is None:
            return
        self.instance = uuid.uuid4().hex
        self.log = (self.workspace / ('worker-' + self.instance + '.log')).open('wb')
        environment = {**os.environ, 'STEWARD_DATA_DIR': str(self.workspace), 'STEWARD_INSTANCE_ID': self.instance}
        self.process = subprocess.Popen([sys.executable, '-m', 'scripts.evaluate_retrieval_tasks', '--worker', self.task['mode']],
                                        cwd=ROOT, env=environment, stdout=self.log, stderr=subprocess.STDOUT,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.evidence.append({'kind': 'worker_started', 'pid': self.process.pid, 'instance': self.instance, 'retrieval_mode': self.task['mode']})

    def create(self):
        description = ('quote returns 110 cents for one line with price_cents=100 and quantity=2; it must return 220 including tax. '
                       'Honor each quantity, default missing quantity to one, and preserve zero quantity and empty orders. '
                       'Keep integer-cent arithmetic and configured tax. Add regression coverage to the existing tests, '
                       'run it against unchanged implementation first, and preserve all existing tests.')
        self.job = self.api('/jobs', {'repo_id': self.repo['id'], 'title': 'quote ignores item quantity',
                                     'description': description, 'type': 'fix', 'source': 'ticket'})
        self.api('/jobs/' + self.job['id'] + '/approve', {})
        self.start()

    def holdout(self, revision, expected, label):
        files = snapshots.read_files(self.repo['source_snapshot_id'])
        for item in revision['files']:
            if item.get('hash') is None and item.get('after') is None:
                files.pop(item['path'], None)
            else:
                raw = item['after'].encode() if item.get('after') is not None else (Path(revision['worktree']) / item['path']).read_bytes()
                if item.get('hash') and hashlib.sha256(raw).hexdigest() != item['hash']:
                    raise ValueError('Changed candidate before independent holdout')
                files[item['path']] = raw
        profile = execution_profiles.discover({'files': files})[0]
        run_id = 'retrieval-holdout-' + uuid.uuid4().hex
        prepared = dependencies.prepare(run_id, {'id': self.repo['source_snapshot_id'], 'files': files}, profile, self.cancel)
        script = ('import json\nfrom app import quote\n'
                  'cases={"quantity_two":[{"price_cents":100,"quantity":2}],'
                  '"default_quantity":[{"price_cents":100}],"quantity_zero":[{"price_cents":100,"quantity":0}],'
                  '"empty":[],"integer_tax":[{"price_cents":99,"quantity":3}]}\n'
                  'values={name:quote(items)["total_cents"] for name,items in cases.items()}\n'
                  'assert all(type(value) is int for value in values.values()), "Integer cents required"\n'
                  'print("STEWARD_BEHAVIOR="+json.dumps(values,sort_keys=True))\n')
        try:
            result = sandbox.run(prepared['sandbox_id'], {'id': 'independent-quantity', 'argv': ['python', '-c', script],
                                 'timeout_seconds': 60, 'patch_hash': revision['patch_hash'], 'purpose': 'test'}, 'qualification_holdout', self.cancel)
        finally:
            sandbox.cancel(run_id)
        passed = qualification.behavior_matches(result, EXPECTED)
        self.evidence.append({'kind': 'independent_behavior', 'passed': passed, 'expected': EXPECTED, 'result': result})
        return passed

    def journey(self):
        self.create()
        revision = self.revision()
        checks = [item for item in db.list_records('checks') if item.get('revision_id') == revision['id']]
        if not any(item.get('phase') == 'regression_before' and item.get('status') == 'failed' for item in checks):
            raise ValueError('Quantity regression was not reproduced before the patch')
        return self.holdout(revision, EXPECTED, 'final')


def evaluate(destination):
    identity = source_identity()
    harness_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    records = []
    original = qualification.Qualification
    controller_data = db.DATA_DIR
    try:
        qualification.Qualification = ParcelQualification
        for mode in ('keyword', 'graph'):
            started = time.monotonic()
            result = qualification.driver({**TASK, 'id': TASK['id'] + '-' + mode, 'mode': mode})
            contexts, repositories = [], []
            if result.get('workspace'):
                previous = db.DATA_DIR
                try:
                    db.DATA_DIR = Path(result['workspace'])
                    contexts = db.list_records('retrieval_evaluations')
                    repositories = db.list_records('repositories')
                finally:
                    db.DATA_DIR = previous
            measured = [{'context': entry['context'], **score_evidence(entry['context']['items'], RELEVANT)} for entry in contexts]
            expected_mode = 'keyword only' if mode == 'keyword' else 'keyword + graph'
            namespaces = {(repo['id'], repo['source_snapshot_id']) for repo in repositories}
            measurement_valid = bool(contexts and len(namespaces) == 1 and all(
                entry.get('mode') == mode and entry['context'].get('mode') == expected_mode
                and (entry['context'].get('repo_id'), entry['context'].get('snapshot')) in namespaces for entry in contexts))
            records.append({'mode': mode, 'task_check_success': result['passed'], 'latency_seconds': time.monotonic() - started,
                            'measurement_valid': measurement_valid,
                            'measurement_error': None if measurement_valid else 'Missing retrieval or wrong mode/repository/snapshot evidence',
                            'retrieval': measured, 'result': result})
            stable = (source_identity()['aggregate_sha256'] == identity['aggregate_sha256']
                      and hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == harness_hash)
            passed = bool(len(records) == 2 and stable and all(item['task_check_success'] and item['measurement_valid'] for item in records))
            report = {'kind': 'paired_live_retrieval_task', 'recorded_at': db.now(), 'source_identity': identity,
                      'harness_sha256': harness_hash, 'source_identity_stable': stable, 'passed': passed,
                      'status': 'passed' if passed else 'blocked' if any(item['result'].get('status') == 'blocked' for item in records)
                                else 'running' if len(records) < 2 and stable else 'failed',
                      'fixture': 'Fixed Parcel quantity bug; generic live provider, no fixture inference',
                      'source_files': {name: hashlib.sha256(content.encode()).hexdigest() for name, content in source_files().items()},
                      'expected_relevant_ranges': RELEVANT, 'expected_behavior': EXPECTED, 'results': records,
                      'limits': 'One sequential pair, keyword then graph; embeddings disabled in both. Not a statistical performance claim. '
                                'All extra retrieval rounds and failed attempts retained. Unknown model usage remains null.'}
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(report, indent=2), encoding='utf-8')
            print(json.dumps({'mode': mode, 'task_check_success': result['passed'], 'error': result.get('error')}), flush=True)
            if not stable:
                break
    finally:
        qualification.Qualification = original
        db.DATA_DIR = controller_data
    return report


def main():
    from run import load_environment
    load_environment()
    # backend.db is already imported; apply the CLI's literal environment to it.
    if os.getenv('STEWARD_DATA_DIR'):
        db.DATA_DIR = Path(os.environ['STEWARD_DATA_DIR'])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', choices=['keyword', 'graph'])
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/evidence/retrieval-task-comparison.json')
    args = parser.parse_args()
    if args.worker:
        install_mode(args.worker)
        from backend.worker_main import main as worker_main
        worker_main()
        return 0
    report = evaluate(args.output)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
