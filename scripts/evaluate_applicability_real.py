"""Match one real public OSV record to an exact pinned dependency in a labelled local demo repository.

Offline by default and isolated in a temporary data directory: the record is the GlobalEntry C normalised
from live OSV (tests/fixtures/catalogue/real_osv_cve_2024_35195.json); the repository is synthetic demo
data. `--live-public` additionally tries one TLS-verified public fetch of the same advisory into a scratch
catalogue and records the outcome honestly (verification is never disabled).
"""
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / 'tests' / 'fixtures' / 'catalogue' / 'real_osv_cve_2024_35195.json'
OUTPUT = ROOT / 'docs' / 'evidence' / 'applicability-real-osv.json'
ADVISORY = 'GHSA-9wx4-h78v-vm56'
APP = 'import requests\n\n\ndef fetch(url):\n    return requests.get(url, timeout=5)\n'
LABEL = ('Real public OSV record (as captured by C on its stated retrieval date) matched against a LABELLED LOCAL DEMO '
         'repository; the repository is synthetic and the result is affected-version applicability, not exploitability.')


def demo(work: Path, pin: str) -> dict:
    from backend import db, repository, snapshots
    source = work / 'demo-repo'
    source.mkdir(exist_ok=True)
    (source / 'requirements.txt').write_text(f'# LABELLED LOCAL DEMO REPOSITORY (not a real project)\n{pin}\n', encoding='utf-8')
    (source / 'app.py').write_text(APP, encoding='utf-8')
    snapshot = snapshots.capture('demo', source, {})
    index = repository.index_repository(source, repo_id='demo', source_snapshot_id=snapshot['id'])
    db.put('repositories', {'id': 'demo', 'root': str(source), 'source_snapshot_id': snapshot['id'], 'snapshot': index['snapshot'], 'index': index})
    db.put('snapshots', {'id': 'demo:' + index['snapshot'], 'repo_id': 'demo', 'snapshot': index['snapshot'], 'source_snapshot_id': snapshot['id'], 'index': index})
    return snapshot


def live_fetch(scratch: Path, captured) -> dict:
    from backend.global_catalogue import Catalogue
    from backend import global_pipeline
    outcome = {'attempted': True, 'advisory': ADVISORY, 'tls_verification': 'enabled (never disabled)'}
    try:
        entry = global_pipeline.ingest_osv(Catalogue(scratch / 'live.sqlite3'), ADVISORY)
    except Exception as exc:
        return {**outcome, 'succeeded': False, 'error': f'{type(exc).__name__}: {str(exc)[:300]}'}
    return {**outcome, 'succeeded': True, 'retrieved_at': entry.retrieved_at, 'canonical_id': entry.canonical_id,
            'source_content_hash': entry.source_content_hash[:16],
            'content_equals_captured_record': entry.source_content_hash == captured.source_content_hash}


def run(live: bool) -> dict:
    with tempfile.TemporaryDirectory(prefix='duck-tape-applicability-', ignore_cleanup_errors=True) as raw:
        work = Path(raw)
        os.environ['STEWARD_DATA_DIR'] = str(work / 'data')
        os.environ['STEWARD_GLOBAL_CATALOGUE_PATH'] = str(work / 'catalogue.sqlite3')
        for name in ('STEWARD_EMBEDDING_MODEL', 'STEWARD_ATLAS_RETRIEVAL', 'STEWARD_GRAPH_ENGINE'):
            os.environ.pop(name, None)
        sys.path.insert(0, str(ROOT))
        from fastapi.testclient import TestClient
        from backend import agent_context, applicability, db, main
        from backend.global_catalogue import Catalogue, GlobalEntry
        db.init()
        captured = GlobalEntry.model_validate_json(RECORD.read_text(encoding='utf-8'))
        catalogue = Catalogue(work / 'catalogue.sqlite3')
        stored = catalogue.ingest(captured)
        api = TestClient(main.app)

        def assess(pin: str) -> dict:
            snapshot = demo(work, pin)
            response = api.post('/api/repositories/demo/applicability/catalogue', json={'snapshot_id': snapshot['id'], 'provider': 'local'})
            response.raise_for_status()
            return {'snapshot': snapshot, 'body': response.json()}

        vulnerable = assess('requests==2.31.0')
        body = vulnerable['body']
        [found] = body['matches']
        pin_line = found['citations'][0]
        pin_text = (work / 'demo-repo' / 'requirements.txt').read_text(encoding='utf-8').splitlines()[pin_line['start_line'] - 1]
        context = agent_context.agent_graph_context('demo', vulnerable['snapshot']['id'], 'fetch url requests',
                                                    entries=[applicability.from_global_entry(catalogue.query('PyPI', 'requests')['entries'][0])])
        usage = context['catalogue_matches'][0]
        queried = catalogue.query('PyPI', 'requests')['entries'][0]
        jobs_created = len(db.list_records('jobs'))
        fixed = assess('requests==2.32.3')['body']['matches'][0]
        return {
            'label': LABEL,
            'kind': 'real_public_record_local_demo_repository',
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'command': 'python scripts/evaluate_applicability_real.py' + (' --live-public' if live else ''),
            'record': {'source': 'tests/fixtures/catalogue/real_osv_cve_2024_35195.json (GlobalEntry from C\'s live-public fetch)',
                       'canonical_id': captured.canonical_id, 'aliases': captured.aliases, 'source_url': captured.source_url,
                       'retrieved_at': captured.retrieved_at, 'source_modified_at': captured.source_modified_at,
                       'freshness_at_run': queried['freshness'], 'source_quality': captured.source_quality,
                       'tracked_by_c_in': 'integrations/global/live-public-evidence.json (same source_content_hash)'},
            'entry_binding': {'entry_id': found['entry_id'], 'entry_revision': found['entry_revision'], 'entry_hash_prefix': found['entry_hash'][:16],
                              'catalogue_material_revision': stored.material_revision, 'entry_status': found['entry_status']},
            'repository': {'label': 'labelled local demo repository', 'snapshot_id': found['snapshot_id'], 'profile_hash_prefix': found['profile_hash'][:16],
                           'pinned_dependency': 'requests==2.31.0', 'app_code': 'app.py: import requests; requests.get(url, timeout=5)'},
            'match': {'status': found['status'], 'citation': {**pin_line, 'pin_line_text': pin_text, 'hash': pin_line['hash'][:16]},
                      'known_fixed_versions': found['evidence'][0]['fixed_versions'], 'limitations': found['limitations'][:3],
                      'coverage': body['coverage'], 'jobs_created_by_route': jobs_created},
            'agent_context': {'directly_imported': usage['directly_imported'], 'sink_reachable': usage['sink_reachable'],
                              'import_evidence': usage['usage'][0]['imports'], 'call_evidence': usage['usage'][0]['calls'],
                              'basis': usage['usage'][0]['basis']},
            'negative_control': {'pinned_dependency': 'requests==2.32.3', 'status': fixed['status'], 'citations': fixed['citations']},
            'live_public_fetch': live_fetch(work, captured) if live else {'attempted': False},
            'not_verified': ['Exploitability and reachability of the vulnerable behaviour (sink_reachable stays null: the catalogue names no symbols)',
                             'Atlas retrieval (needs a configured cluster; covered separately with a mocked transport in tests)',
                             'A real project: the repository is synthetic'],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-public', action='store_true', help='Also try one TLS-verified OSV fetch and record the outcome')
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    result = run(args.live_public)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
    print(json.dumps({key: result[key] for key in ('kind', 'entry_binding', 'match', 'agent_context', 'negative_control', 'live_public_fetch')}, indent=2))


if __name__ == '__main__':
    main()
