"""Run on a configured Databricks Python job; public input only, no provisioning."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.global_catalogue import Catalogue
from backend import global_pipeline as pipeline


def run(sources: list[dict], *, publish: bool = False) -> dict:
    """Fetch/normalize inside this job, restore public lineage, curate, optionally publish."""
    pipeline._require('databricks')
    if not isinstance(sources, list) or not 1 <= len(sources) <= pipeline.MAX_BATCH:
        raise ValueError('Job requires between 1 and 100 explicit public source descriptors')
    for source in sources:
        if not isinstance(source, dict) or not (
                source.get('kind') == 'osv' and set(source) == {'kind', 'id'}
                or source.get('kind') == 'improvement' and set(source) == {'kind', 'record'}):
            raise ValueError('Only explicit public OSV IDs or attributed improvement records are accepted')
    with tempfile.TemporaryDirectory(prefix='ducktape-public-') as directory:
        catalogue = Catalogue(Path(directory) / 'catalogue.sqlite3')
        restored = pipeline.restore_databricks(catalogue)
        selected, failures = {}, 0
        for source in sources:
            try:
                entry = (pipeline.ingest_osv(catalogue, source['id']) if source['kind'] == 'osv'
                         else pipeline.ingest_improvement(catalogue, source['record']))
                selected[entry.canonical_id] = entry
            except ValueError:
                failures += 1
        entries = list(selected.values())
        curated = pipeline.curate_databricks(entries, catalogue=catalogue)
        successful = {item['canonical_id'] for item in curated['results'] if item['status'] == 'curated'}
        publication = (pipeline.publish_atlas([entry for entry in entries if entry.canonical_id in successful], catalogue=catalogue)
                       if publish else {'state': 'not_requested'})
        return {'restored_revisions': restored, 'ingestion_failed': failures, 'curation': curated,
                'publication': publication, 'operations': pipeline.operation_status(catalogue)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=Path, required=True, help='Bounded public-source descriptor JSON file')
    parser.add_argument('--publish', action='store_true', help='Explicitly publish successfully curated records to public Atlas')
    args = parser.parse_args()
    try:
        with args.sources.open('rb') as source_file:
            content = source_file.read(pipeline.MAX_SOURCE_BYTES + 1)
        if len(content) > pipeline.MAX_SOURCE_BYTES:
            raise ValueError('Public-source descriptor file exceeds size limit')
        result = run(json.loads(content), publish=args.publish)
        print(json.dumps(result, indent=2))
        return int(bool(result['ingestion_failed'] or result['curation']['failed'] or result['publication'].get('failed')))
    except (ValueError, OSError):
        print(json.dumps({'status': 'failed', 'detail': 'Public pipeline failed; inspect configuration and bounded public inputs. Prior published records retained.'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
