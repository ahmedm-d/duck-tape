"""Reproduce the bounded public slice; cloud services are never contacted here."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.global_catalogue import Catalogue, GlobalEntry, material_hash
from backend.global_pipeline import ingest_improvement, ingest_osv, status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True, help='Use a dedicated demo catalogue')
    parser.add_argument('--live-public', action='store_true', help='Fetch OSV and Python documentation over HTTPS')
    args = parser.parse_args()
    catalogue = Catalogue(args.database)
    directory = Path(__file__).resolve().parent

    def ingest() -> list[GlobalEntry]:
        if args.live_public:
            record = json.loads((directory / 'python313-improvement.json').read_text(encoding='utf-8'))
            return [ingest_osv(catalogue, 'GHSA-9wx4-h78v-vm56'), ingest_improvement(catalogue, record)]
        return [catalogue.ingest(GlobalEntry.model_validate_json(
            (directory / 'fixtures' / name).read_text(encoding='utf-8')))
            for name in ('global-vulnerability.json', 'global-improvement.json')]

    first = ingest()
    second = ingest()
    assert [(entry.canonical_id, entry.material_revision, material_hash(entry)) for entry in first] == [
        (entry.canonical_id, entry.material_revision, material_hash(entry)) for entry in second], 'Source changed between polls'
    results = []
    for entry in second:
        package = entry.affected[0]
        query = catalogue.query(package.ecosystem, package.package, limit=10)
        assert query['coverage'] == 'unknown'
        assert any(item['canonical_id'] == entry.canonical_id for item in query['entries'])
        results.append({'canonical_id': entry.canonical_id, 'revision': entry.material_revision,
                        'material_hash': material_hash(entry), 'source_content_hash': entry.source_content_hash,
                        'source_url': entry.source_url, 'retrieved_at': entry.retrieved_at,
                        'source_quality': entry.source_quality, 'coverage': query['coverage']})
    assert catalogue.query('npm', 'duck-tape-no-such-demo-package')['entries'] == []
    print(json.dumps({'mode': 'live-public-https' if args.live_public else 'labelled-fixtures',
                      'repeated_poll_stable': True, 'entries': results,
                      'cloud_configuration': status(), 'cloud_connections_attempted': False}, indent=2))


if __name__ == '__main__':
    main()
