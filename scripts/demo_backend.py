"""Isolated backend demo. No fixture inference and no host execution of demo source.

prepare is offline by default; --refresh-public fetches actual OSV evidence.
run authorizes live inference, pauses at a plan, and resumes only with its exact
--approve-plan hash. --allow-dependency-downloads permits verified PyPI artifacts.
Reports contain evidence IDs/metrics, not prompts, source, credentials or raw logs.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if __name__ == '__main__':
    from run import load_environment
    load_environment()

from backend import db, dependencies, execution_profiles, providers, sandbox, snapshots
from scripts.qualification_driver import Qualification

FIXTURES = ROOT / 'tests' / 'fixtures' / 'backend_demo'
MANIFEST = 'backend-demo.json'
SCHEMA = 'duck-tape-backend-demo-1'
QUERY = 'api.py pricing.py config.py tests/test_quote.py quote quantity total UNIT_PRICE'


def hashes(root: Path) -> dict:
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob('*')) if path.is_file() and '.git' not in path.relative_to(root).parts}


def save(root: Path, report: dict) -> dict:
    temporary = root / (MANIFEST + '.tmp')
    temporary.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    for attempt in range(3):
        try:
            temporary.replace(root / MANIFEST)
            break
        except PermissionError:
            if attempt == 2:
                raise
            time.sleep(.05)
    return report


@contextmanager
def workspace(root: Path):
    """Select only this marked store; serialize runner invocations and restore callers."""
    lock = root / '.demo-runner.lock'
    with lock.open('x') as handle:
        handle.write(str(os.getpid()))
    previous = db.DATA_DIR
    atlas = json.loads((root / MANIFEST).read_text(encoding='utf-8')).get('atlas_retrieval', False)
    changes = {'STEWARD_DATA_DIR': str(root / 'store'), 'STEWARD_SANDBOX_STATE': str(root / 'store' / 'sandbox'),
               'STEWARD_GLOBAL_CATALOGUE_PATH': str(root / 'catalogue.sqlite3'),
               # This controller performs only the explicitly requested, synchronous vector sync.
               'STEWARD_ATLAS_REQUIRED': '0', 'STEWARD_ATLAS_GRAPH_MIRROR': '0',
               'STEWARD_ATLAS_RETRIEVAL': '1' if atlas else '0', 'STEWARD_GRAPH_ENGINE': 'builtin'}
    if not atlas:
        changes['STEWARD_EMBEDDING_MODEL'] = ''
    before = {name: os.environ.get(name) for name in changes}
    try:
        os.environ.update(changes)
        db.DATA_DIR = root / 'store'
        db.init()
        yield
    finally:
        db.DATA_DIR = previous
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        lock.unlink()


def inspect(root: Path) -> dict:
    root = Path(root).resolve()
    path = root / MANIFEST
    if not path.is_file():
        raise ValueError('Not a prepared backend demo workspace')
    report = json.loads(path.read_text(encoding='utf-8'))
    if report.get('schema') != SCHEMA or report.get('workspace') != str(root):
        raise ValueError('Backend demo workspace identity mismatch')
    for name, case in report['cases'].items():
        if name not in ('bug', 'security') or hashes(root / 'sources' / name) != case['fixture_hashes']:
            raise ValueError('Demo source fixture changed; prepare a new workspace')
    return report


def retrieval_evidence(repo: dict, case: str, *, atlas: bool = False) -> dict:
    from backend import retrieval, data_platforms
    sync = {'status': 'not_run'}
    if atlas:
        try:
            sync = {'status': 'complete', **data_platforms.atlas_sync(repo['id'], repo['source_snapshot_id'])}
        except Exception as exc:
            sync = {'status': 'blocked', 'error': type(exc).__name__}
    usable_atlas = atlas and sync['status'] == 'complete'
    evidence = retrieval.retrieve(repo['id'], repo['source_snapshot_id'], QUERY if case == 'bug' else 'requests Session requirements.txt',
                                  semantic=usable_atlas, atlas=usable_atlas, graph_engine='builtin')
    paths = sorted({item['path'] for item in evidence['items']})
    required = {'api.py', 'pricing.py', 'config.py', 'tests/test_quote.py'} if case == 'bug' else {'client.py', 'requirements.txt'}
    return {'paths': paths, 'required_paths_present': required.issubset(paths), 'mode': evidence['mode'],
            'gaps': evidence['missing'], 'atlas_sync': sync,
            'atlas_selected_count': sum('Atlas vector candidate' in item.get('reason', '') for item in evidence['items']),
            'citations': [{key: item.get(key) for key in ('path', 'start_line', 'end_line', 'hash', 'reason')}
                          for item in evidence['items']]}


def prepare(root: Path, *, provider: str = 'local', refresh_public: bool = False, atlas_retrieval: bool = False,
            verify_baseline: bool = False, allow_dependency_downloads: bool = False) -> dict:
    from fastapi.testclient import TestClient
    from backend import main, global_pipeline
    from backend.global_catalogue import Catalogue, GlobalEntry
    from scripts.evaluate_models import source_identity
    if provider not in ('local', 'atlas'):
        raise ValueError('Catalogue provider must be local or atlas')
    root = Path(root).resolve()
    if root.exists():
        raise ValueError('Demo workspace already exists; choose a new directory')
    root.mkdir(parents=True)
    report = {'schema': SCHEMA, 'workspace': str(root), 'created_at': db.now(), 'status': 'blocked',
              'scope': 'backend generic model/check/export demo; not full application qualification or scheduled cloud delivery',
              'source_identity': source_identity(),
              'demo_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'inference': 'not_run', 'atlas_retrieval': atlas_retrieval,
              'reason': 'Live model and Docker verification have not run', 'cases': {},
              'catalogue': {'provider': provider, 'provenance': 'captured_public_record'}}
    save(root, report)
    with workspace(root):
        context = Qualification({'id': 'backend-demo'}, root / 'store')
        context.client = TestClient(main.app)  # No app lifespan: no sample seeding or background worker.
        try:
            catalogue = Catalogue(root / 'catalogue.sqlite3')
            if refresh_public:
                if provider != 'local':
                    raise ValueError('Public refresh writes only the isolated local catalogue')
                entry = global_pipeline.ingest_osv(catalogue, 'GHSA-9wx4-h78v-vm56')
                report['catalogue']['provenance'] = 'live_osv_fetch'
            else:
                path = ROOT / 'tests/fixtures/catalogue/real_osv_cve_2024_35195.json'
                entry = catalogue.ingest(GlobalEntry.model_validate_json(path.read_text(encoding='utf-8')))
            report['catalogue'].update(entry_id=entry.canonical_id, retrieved_at=entry.retrieved_at,
                                       source_content_hash=entry.source_content_hash)
            if provider == 'atlas':
                report['catalogue'] = {'provider': 'atlas', 'provenance': 'live_atlas_query',
                                       'detail': 'Selected public record provenance is stored with the security case'}
            for name in ('bug', 'security'):
                source = root / 'sources' / name
                shutil.copytree(FIXTURES / name, source, ignore=shutil.ignore_patterns('__pycache__'))
                repo = main.register_repo(source, 'Backend demo: ' + name,
                                          {'source': 'pinned backend demo fixture', 'fixture_sources_only': True})
                if repo.get('is_sample'):
                    raise ValueError('Demo must use generic provider routing')
                snapshot = db.get('source_snapshots', repo['source_snapshot_id'])
                record = {'repo_id': repo['id'], 'snapshot_id': snapshot['id'], 'is_sample': False,
                          'fixture_hashes': hashes(source), 'job_id': None,
                          'dependencies': dependencies.artifacts(snapshot), 'baseline': {'status': 'not_run'},
                          'retrieval': retrieval_evidence(repo, name, atlas=atlas_retrieval)}
                if name == 'bug':
                    job = context.api('/jobs', {'repo_id': repo['id'], 'title': 'Quote ignores quantity across modules', 'type': 'fix', 'source': 'request',
                        'description': 'Trace api.py quote through pricing.py quote_total and config.py UNIT_PRICE, preserving tests/test_quote.py. '
                        'Fix quote({"quantity": 3}) to return {"quantity": 3, "total": 36}; zero quantity returns zero, '
                        'and omitted quantity remains one with total 12. Add a regression that fails against unchanged pricing; preserve existing tests.'})
                    record['job_id'] = job['id']
                else:
                    assessment = context.api('/repositories/' + repo['id'] + '/applicability/catalogue', {'provider': provider})
                    record['applicability'] = {key: assessment.get(key) for key in ('provider', 'coverage', 'matches', 'limitations', 'errors')}
                    chosen = next((value for value in assessment['entries'] if value['id'] == 'CVE-2024-35195'), None)
                    if chosen:
                        record['catalogue_entry'] = {key: chosen.get(key) for key in ('id', 'revision', 'content_hash', 'source_url', 'retrieved_at', 'status')}
                        outcome = context.api('/repositories/' + repo['id'] + '/applicability/intake', {'entry': chosen, 'snapshot_id': snapshot['id']})
                        repeated = context.api('/repositories/' + repo['id'] + '/applicability/intake', {'entry': chosen, 'snapshot_id': snapshot['id']})
                        if [job['id'] for job in outcome['jobs']] != [job['id'] for job in repeated['jobs']]:
                            raise ValueError('Repeated advisory intake created different jobs')
                        if outcome['jobs']:
                            record['job_id'] = outcome['jobs'][0]['id']
                    if not record['job_id']:
                        record['blocker'] = 'No current active advisory available for intake; refresh public evidence in a new workspace'
                report['cases'][name] = record
                if verify_baseline:
                    record['baseline'] = baseline(record, allow_dependency_downloads=allow_dependency_downloads)
                save(root, report)
        finally:
            context.client.close()
    return report


def baseline(case: dict, *, allow_dependency_downloads: bool = False) -> dict:
    from backend.contracts import CancellationToken
    if case['dependencies']['packages'] and not allow_dependency_downloads:
        return {'status': 'blocked', 'reason': 'Dependency downloads require --allow-dependency-downloads'}
    if not sandbox.health().get('available'):
        return {'status': 'blocked', 'reason': 'Actively verified Linux Docker is unavailable'}
    source = db.get('source_snapshots', case['snapshot_id'])
    plans = execution_profiles.discover(source)
    if not plans:
        return {'status': 'blocked', 'reason': 'No supported execution profile'}
    run_id = 'demo-baseline-' + uuid.uuid4().hex
    cancel = CancellationToken(deadline=time.monotonic() + 900)
    try:
        prepared = dependencies.prepare(run_id, source, plans[0], cancel)
        checks = [sandbox.run(prepared['sandbox_id'], command, 'baseline', cancel) for command in plans[0]['commands']]
        for check in checks:
            db.put('demo_checks', check)
        return {'status': 'passed' if checks and all(check.get('status') == 'passed' and check.get('exit_code') == 0 for check in checks) else 'failed',
                'checks': [check['id'] for check in checks], 'image': prepared['environment'], 'dependencies': prepared['cache_key'],
                'artifacts': [{key: item.get(key) for key in ('name', 'version', 'sha256')} for item in prepared['artifacts']]}
    except Exception as exc:
        return {'status': 'blocked', 'reason': 'Baseline sandbox/dependency preparation failed', 'error': type(exc).__name__}
    finally:
        sandbox.cancel(run_id)


def plan_approved(job: dict, supplied_hash: str | None) -> bool:
    if supplied_hash is None:
        return False
    if (job.get('status') != 'Awaiting approval' or job.get('plan_hash') != supplied_hash
            or not isinstance(job.get('approach'), list) or not job['approach']):
        raise ValueError('Approval must name the current concrete plan hash')
    return True


def validate_evidence(case: str, revision: dict, checks: list, stages: list, run_record: dict, *, require_atlas: bool = False) -> dict:
    if (hashlib.sha256(revision['diff'].encode()).hexdigest() != revision['patch_hash']
            or run_record.get('revision_id') != revision['id'] or run_record.get('job_id') != revision['job_id']
            or run_record.get('status') != 'complete' or revision.get('assessment', {}).get('passed') is not True
            or revision.get('review') != {'approved': True, 'findings': []}):
        raise ValueError('No complete exact-patch revision and approved model review')
    for phase in ('baseline', 'candidate'):
        required = [item for item in checks if item.get('phase') == phase and item.get('required', True)]
        if not required or any(item.get('status') != 'passed' or item.get('exit_code') != 0 for item in required):
            raise ValueError('Missing passing required ' + phase + ' checks')
        if any(not isinstance(item.get('environment'), dict) or not item['environment'].get('image')
               or not item['environment'].get('dependencies') for item in required):
            raise ValueError('Checks lack actual sandbox image/dependency identity')
    if any(item.get('patch_hash') != revision['patch_hash'] or item.get('revision_id') != revision['id']
           or item.get('source_snapshot_id') != revision['source_snapshot_id'] for item in checks):
        raise ValueError('Checks are not bound to the current revision')
    if any(item.get('required', True) and item.get('phase') != 'regression_before' and item.get('status') != 'passed' for item in checks):
        raise ValueError('Required verification evidence did not pass')
    if case == 'bug' and (revision['assessment'].get('regression_reproduced') is not True
                          or not any(item.get('phase') == 'regression_before' and item.get('status') == 'failed' for item in checks)):
        raise ValueError('Bug regression was not reproduced')
    if case == 'security' and not any(item.get('phase') == 'advisory_after' and item.get('kind') == 'security'
                                     and item.get('status') == 'passed' for item in checks):
        raise ValueError('Missing actual advisory recheck')
    models = []
    for role in (('implementation', 'review', 'regression') if case == 'bug' else ('implementation', 'review')):
        matching = [stage['telemetry'] for stage in stages if stage.get('stage') == role and stage.get('status') == 'complete'
                    and stage.get('job_id') == revision['job_id']]
        if not matching or any(item.get('mode') != 'live' or not item.get('model') or item.get('attempts', 0) < 1 for item in matching):
            raise ValueError('Missing actual generic live ' + role + ' inference')
        models.extend({'role': role, 'model': item['model'], 'usage': item.get('usage')} for item in matching)
    atlas_selected = sum('Atlas vector candidate' in item.get('reason', '') for stage in stages
                         if stage.get('job_id') == revision['job_id'] and stage.get('stage') in ('implementation', 'repair')
                         for item in stage.get('input', {}).get('payload', {}).get('context', {}).get('items', []))
    if require_atlas and not atlas_selected:
        raise ValueError('Actual worker implementation inputs contain no selected Atlas evidence')
    return {'run_id': run_record['id'], 'revision_id': revision['id'], 'patch_hash': revision['patch_hash'],
            'checks': [item['id'] for item in checks], 'models': models, 'worker_atlas_selected_count': atlas_selected}


def holdout(case: dict, revision: dict) -> dict:
    """Independent fixed behavior assertions execute only in a fresh sandbox."""
    from backend.contracts import CancellationToken
    source = db.get('source_snapshots', case['snapshot_id'])
    files = snapshots.read_files(source['id'])
    for item in revision['files']:
        if item.get('hash') is None:
            files.pop(item['path'], None)
        else:
            raw = (Path(revision['worktree']) / item['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != item['hash']:
                raise ValueError('Candidate source changed before independent holdout')
            files[item['path']] = raw
    run_id = 'demo-holdout-' + uuid.uuid4().hex
    cancel = CancellationToken(deadline=time.monotonic() + 900)
    try:
        plan = execution_profiles.discover({'files': files})[0]
        prepared = dependencies.prepare(run_id, {'id': source['id'], 'files': files}, plan, cancel)
        script = ('from api import quote\nassert quote({}) == {"quantity": 1, "total": 12}\n'
                  'assert quote({"quantity": 3}) == {"quantity": 3, "total": 36}\n'
                  'assert quote({"quantity": 0}) == {"quantity": 0, "total": 0}\n'
                  'assert quote({"quantity": 7}) == {"quantity": 7, "total": 84}\n')
        result = sandbox.run(prepared['sandbox_id'], {'id': 'independent-quote-behavior', 'argv': ['python', '-c', script],
                             'timeout_seconds': 60, 'patch_hash': revision['patch_hash'], 'purpose': 'test'}, 'holdout', cancel)
        db.put('demo_checks', result)
        return {'status': result['status'], 'check_id': result['id'], 'exit_code': result['exit_code'], 'patch_hash': revision['patch_hash']}
    finally:
        sandbox.cancel(run_id)


def run(root: Path, case: str, *, approve_plan: str | None = None, allow_dependency_downloads: bool = False,
        atlas_retrieval: bool = False,
        export_metrics: bool = False) -> dict:
    from fastapi.testclient import TestClient
    from backend.main import app
    from scripts.evaluate_models import source_identity
    root = Path(root).resolve()
    report = inspect(root)
    if case not in report['cases']:
        raise ValueError('Unknown demo case')
    if atlas_retrieval:
        report['atlas_retrieval'] = True
        save(root, report)
    selected = report['cases'][case]
    source_before = source_identity()
    with workspace(root):
        if not selected['job_id']:
            return {'status': 'blocked', 'reason': selected.get('blocker', 'No current job')}
        current = db.get('jobs', selected['job_id'])
        if approve_plan is not None:
            plan_approved(current, approve_plan)
        if report.get('atlas_retrieval'):
            selected['retrieval'] = retrieval_evidence(db.get('repositories', selected['repo_id']), case, atlas=True)
            save(root, report)
            if not selected['retrieval']['atlas_selected_count']:
                return {'status': 'blocked', 'reason': 'No selected source evidence came from Atlas; inspect retrieval gaps/index readiness',
                        'retrieval': selected['retrieval']}
        provider_health, sandbox_health = providers.health(), sandbox.health()
        result = {'status': 'blocked', 'job_id': current['id'], 'job_status': current['status'], 'inference': 'not_run',
                  'provider_available': bool(provider_health.get('available')), 'sandbox_available': bool(sandbox_health.get('available'))}
        if not result['provider_available'] or not result['sandbox_available']:
            result['reason'] = 'Configure a real served model and actively verified Linux Docker sandbox'
            report['cases'][case]['latest_run'] = result
            save(root, report)
            return result
        if case == 'security' and not allow_dependency_downloads:
            result['reason'] = 'Security execution requires --allow-dependency-downloads (PyPI artifacts and actual OSV recheck)'
            selected['latest_run'] = result
            save(root, report)
            return result
        selected['baseline'] = baseline(selected, allow_dependency_downloads=allow_dependency_downloads)
        if selected['baseline']['status'] != 'passed':
            result['reason'] = 'Baseline did not pass; see baseline evidence'
            selected['latest_run'] = result
            save(root, report)
            return result
        context = Qualification({'id': 'backend-demo-' + case}, root / 'store')
        context.client = TestClient(app)
        context.job = current
        try:
            if current['status'] == 'Awaiting approval':
                if current.get('plan_hash'):
                    if not plan_approved(current, approve_plan):
                        result.update(plan_hash=current['plan_hash'], plan=current.get('approach'), reason='Inspect the plan; resume with --approve-plan HASH')
                        return result
                    context.approve_plan(current)
                else:
                    context.api('/jobs/' + current['id'] + '/approve', {'expected_version': current['record_version']})
                    context.evidence.append({'kind': 'investigation_consent'})
            if current['status'] not in {'Failed', 'Needs input', 'Cancelled', 'Rejected', 'Ready for review'}:
                context.start()
                while time.monotonic() < context.deadline:
                    current = db.get('jobs', current['id'])
                    if current['status'] not in {'Queued', 'Investigating', 'Implementing', 'Verifying'}:
                        break
                    if context.process and context.process.poll() is not None:
                        raise ValueError('Worker exited before a terminal or paused stage')
                    time.sleep(.2)
                else:
                    raise TimeoutError('Demo exceeded its bounded deadline')
            result['job_status'] = current['status']
            if current['status'] == 'Ready for review':
                revision = db.get('revisions', current['current_revision'])
                checks = [item for item in db.list_records('checks') if item.get('revision_id') == revision['id']]
                completed = [item for item in db.list_records('runs') if item.get('revision_id') == revision['id'] and item.get('status') == 'complete']
                if len(completed) != 1:
                    raise ValueError('Revision needs one completed run')
                exact_run = completed[0]
                stages = [item for item in db.list_records('run_stages') if item.get('job_id') == current['id']
                          and item.get('workflow_key') == exact_run.get('workflow_key')]
                result.update(validate_evidence(case, revision, checks, stages, exact_run, require_atlas=report.get('atlas_retrieval', False)))
                if case == 'bug':
                    result['holdout'] = holdout(selected, revision)
                    if result['holdout']['status'] != 'passed' or result['holdout']['exit_code'] != 0:
                        raise ValueError('Independent behavior holdout failed')
                inspect(root)
                if source_identity()['aggregate_sha256'] != source_before['aggregate_sha256']:
                    raise ValueError('Backend source changed during the run')
                response = context.client.get('/api/jobs/' + current['id'] + '/patch', params={'revision_id': revision['id']})
                if response.status_code != 200 or hashlib.sha256(response.content).hexdigest() != revision['patch_hash']:
                    raise ValueError('Export did not match the exact verified patch')
                patch = root / ('patch-' + uuid.uuid4().hex + '.diff')
                with patch.open('xb') as handle:
                    handle.write(response.content)
                result.update(status='passed', verified=True, patch_file=patch.name, inference='live',
                              reason='Verified revision and export; human patch approval/application remain separate')
                if export_metrics:
                    from backend.data_platforms import databricks_sync
                    try:
                        result['metrics_export'] = databricks_sync(exact_run['id'])
                    except Exception as exc:
                        result.update(status='blocked', reason='Patch verified; requested Databricks metrics export failed')
                        result['metrics_export'] = {'status': 'failed', 'error': type(exc).__name__, 'run_id': exact_run['id']}
            elif current['status'] == 'Awaiting approval':
                result.update(plan_hash=current.get('plan_hash'), plan=current.get('approach'),
                              reason='Inspect the plan; resume with --approve-plan HASH')
            elif current['status'] == 'Failed':
                result.update(status='failed', reason='Worker failed; inspect the local job and run evidence')
            else:
                result['reason'] = 'Job paused or stopped; resolve it explicitly through existing job APIs'
        except Exception as exc:
            result.update(status='failed', verified=False, error=type(exc).__name__,
                          reason='Demo did not establish verified completion; inspect local job/run evidence')
        finally:
            latest = db.get('jobs', selected['job_id'])
            try:
                if latest['status'] in {'Queued', 'Investigating', 'Implementing', 'Verifying'}:
                    context.api('/jobs/' + latest['id'] + '/cancel', {})
            finally:
                try:
                    context.stop()
                finally:
                    context.client.close()
            attempts = [item for item in db.list_records('runs') if item.get('job_id') == latest['id']]
            result['run_ids'] = [item['id'] for item in attempts]
            stage_attempts = [item.get('telemetry', {}) for item in db.list_records('run_stages') if item.get('job_id') == latest['id']]
            if any(telemetry.get('mode') == 'live' and telemetry.get('attempts', 0) > 0
                   for telemetry in stage_attempts + [telemetry for attempt in attempts for telemetry in attempt.get('provider_attempts', [])]):
                result['inference'] = 'live'
            elif stage_attempts:
                result['inference'] = 'attempted_or_unknown'
            result['approvals'] = context.evidence
            result['source_identity'] = {key: source_before[key] for key in ('git_base', 'dirty', 'aggregate_sha256')}
            result['demo_script_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            selected['latest_run'] = result
            report['status'] = 'passed' if all(value.get('latest_run', {}).get('status') == 'passed' for value in report['cases'].values()) else 'blocked'
            save(root, report)
            artifact = root / ('run-' + uuid.uuid4().hex + '.json')
            with artifact.open('x', encoding='utf-8') as handle:
                json.dump(result, handle, indent=2)
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('prepare', 'inspect', 'run'))
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--case', choices=('bug', 'security'), default='bug')
    parser.add_argument('--catalogue', choices=('local', 'atlas'), default='local')
    parser.add_argument('--refresh-public', action='store_true')
    parser.add_argument('--atlas-retrieval', action='store_true', help='Persist consent to embedding fixture source and syncing/querying private Atlas vectors')
    parser.add_argument('--verify-baseline', action='store_true', help='Run actual Docker baselines during preparation; no model calls')
    parser.add_argument('--approve-plan')
    parser.add_argument('--allow-dependency-downloads', action='store_true')
    parser.add_argument('--export-metrics', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.stage == 'prepare':
            result = prepare(args.workspace, provider=args.catalogue, refresh_public=args.refresh_public, atlas_retrieval=args.atlas_retrieval,
                             verify_baseline=args.verify_baseline, allow_dependency_downloads=args.allow_dependency_downloads)
        elif args.stage == 'inspect':
            result = inspect(args.workspace)
        else:
            result = run(args.workspace, args.case, approve_plan=args.approve_plan,
                         allow_dependency_downloads=args.allow_dependency_downloads, export_metrics=args.export_metrics,
                         atlas_retrieval=args.atlas_retrieval)
        print(json.dumps(result, indent=2))
        return 0 if result.get('status') == 'passed' else 2
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'error': type(exc).__name__,
                          'reason': 'Demo stopped; inspect its local workspace and configuration. No pass is claimed.'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
