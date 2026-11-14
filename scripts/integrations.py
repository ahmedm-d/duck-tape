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
    args = parser.parse_args(argv)
    try:
        load_environment()
        from backend import data_platforms
        if args.command == "status":
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
