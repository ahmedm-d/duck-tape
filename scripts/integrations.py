"""Explicit cloud operations. Status is local; sync/search commands can send data."""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if (ROOT / "vendor-current").is_dir():
    sys.path.insert(0, str(ROOT / "vendor-current"))
from run import load_environment


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show local configuration; no remote requests")
    commands.add_parser("atlas-index", help="Print the vector-index definition; does not create it")
    for name in ("atlas-sync", "atlas-search"):
        command = commands.add_parser(name, help="Explicit source-derived cloud operation")
        command.add_argument("--repo", required=True)
        command.add_argument("--snapshot", required=True)
        if name == "atlas-search":
            command.add_argument("--query", required=True)
            command.add_argument("--limit", type=int, default=12, choices=range(1, 25), metavar="1..24")
    commands.add_parser("databricks-sync", help="Upload one terminal run's allowlisted metrics").add_argument("--run", required=True)
    commands.add_parser("databricks-summary", help="Query measured metrics in the configured table")
    commands.add_parser("catalogue-status", help="Inspect public pipeline configuration without connecting")
    for name in ("catalogue-query", "catalogue-graph", "catalogue-ingest-osv", "catalogue-ingest-improvement",
                 "catalogue-import", "catalogue-publish", "catalogue-curate", "catalogue-operations"):
        command = commands.add_parser(name, help="Explicit public catalogue operation")
        command.add_argument("--database", type=Path)
        if name in ("catalogue-query", "catalogue-graph"):
            command.add_argument("--ecosystem", required=True)
            command.add_argument("--package", required=True)
            command.add_argument("--limit", type=int, default=20, choices=range(1, 101), metavar="1..100")
            command.add_argument("--atlas", action="store_true", help="Query the explicitly enabled public Atlas catalogue")
        elif name == "catalogue-ingest-osv":
            command.add_argument("--id", required=True)
        elif name in ("catalogue-ingest-improvement", "catalogue-import"):
            command.add_argument("--record", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        load_environment()
        from backend import data_platforms
        if args.command.startswith("catalogue-"):
            result = catalogue_command(args)
        elif args.command == "status":
            from backend.main import integration_status
            result = integration_status()
        elif args.command == "atlas-index":
            result = data_platforms.atlas_index_definition()
        elif args.command == "atlas-sync":
            result = data_platforms.atlas_sync(args.repo, args.snapshot)
        elif args.command == "atlas-search":
            result = data_platforms.atlas_search(args.repo, args.snapshot, args.query, limit=args.limit)
        elif args.command == "databricks-sync":
            result = data_platforms.databricks_sync(args.run)
        else:
            result = data_platforms.databricks_summary()
    except Exception as exc:
        code = getattr(exc, "code", "")
        code = code if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{1,80}", code) else type(exc).__name__
        print(json.dumps({"status": "failed", "code": code, "detail": "Check integration status and docs/integrations; credentials and raw service errors are withheld."}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, allow_nan=False))
    return 1 if result.get('failed', 0) else 0


def catalogue_command(args: argparse.Namespace) -> dict:
    """Public-only operations; never load repository records or workflow state."""
    if args.command == "catalogue-status":
        from backend.global_pipeline import status
        return status()
    if args.command in ("catalogue-query", "catalogue-graph") and args.atlas:
        from backend.global_pipeline import query_atlas
        result = query_atlas(args.ecosystem, args.package, limit=args.limit)
        if args.command == "catalogue-graph":
            from backend.global_catalogue import graph_result
            return graph_result(result)
        return result
    from backend.global_catalogue import Catalogue
    if args.database is None:
        from backend.global_catalogue import default_path
        args.database = default_path()
    catalogue = Catalogue(args.database)
    if args.command == "catalogue-operations":
        from backend.global_pipeline import operation_status
        return {'operations': operation_status(catalogue)}
    if args.command == "catalogue-query":
        return catalogue.query(args.ecosystem, args.package, limit=args.limit)
    if args.command == "catalogue-graph":
        return catalogue.graph(args.ecosystem, args.package, limit=args.limit)
    if args.command in ("catalogue-import", "catalogue-ingest-improvement"):
        with args.record.open('rb') as source:
            raw = source.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('Public record exceeds size limit')
        record = json.loads(raw)
        if args.command == "catalogue-import":
            from backend.global_catalogue import GlobalEntry
            entry = catalogue.ingest(GlobalEntry.model_validate(record))
        else:
            from backend.global_pipeline import ingest_improvement
            entry = ingest_improvement(catalogue, record)
        return entry.model_dump(mode='json')
    from backend import global_pipeline
    if args.command == "catalogue-ingest-osv":
        return global_pipeline.ingest_osv(catalogue, args.id).model_dump(mode='json')
    entries = catalogue.entries(limit=100)
    if args.command == "catalogue-publish":
        return global_pipeline.publish_atlas(entries, catalogue=catalogue)
    return global_pipeline.curate_databricks(entries, catalogue=catalogue)


if __name__ == "__main__":
    raise SystemExit(main())
