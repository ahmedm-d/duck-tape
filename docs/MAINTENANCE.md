# Offline workspace maintenance

Run maintenance from the project root. It reads the project's `.env` using the startup loader; an existing `STEWARD_DATA_DIR` environment variable takes precedence, and explicit `--data-dir` overrides both. Stop the API and worker first and keep them stopped until maintenance finishes. The tool refuses recent running-service heartbeats and unexpired leases, then reserves a SQLite write lock. A stopped process's last heartbeat may require waiting two minutes. Do not modify workspace files concurrently.

The default workspace is `data/workspace`. These PowerShell examples use the project's Windows virtual environment; on Linux/macOS replace `.venv/Scripts/python.exe` with `.venv/bin/python`.

## Backup

```powershell
.venv/Scripts/python.exe scripts/maintenance.py --data-dir data/workspace backup data/backups/before-upgrade
```

The destination must be new and outside the workspace. The backup contains every regular file and directory owned by the workspace, including repository snapshots, revisions, checks, managed branches, conversations, pending work and database migrations. SQLite's backup API captures committed WAL contents while a separate connection holds the write reservation. WAL/SHM/journal sidecars are omitted from the copy. Symbolic links, Windows junctions and special files are rejected.

`manifest.json` records the original absolute workspace path, directory inventory, file sizes and SHA-256 hashes. Keep the backup private because it contains repository content and discussions. Hashes detect accidental changes; they are not a signature against someone who can replace both payload and manifest. A failed backup can leave an incomplete destination; retain it for diagnosis and use another new destination when retrying.

## Restore to the original location

Steward stores absolute evidence paths. Restore therefore supports only the original absolute workspace location, and refuses an existing target. It never merges workspaces or rewrites references. Stop services, keep the backup, and manually rename an existing workspace aside before restoring. For example, after verifying that the old workspace is the intended directory and `workspace-kept` does not already exist:

```powershell
Move-Item -LiteralPath data/workspace -Destination data/workspace-kept
.venv/Scripts/python.exe scripts/maintenance.py --data-dir data/workspace restore data/backups/before-upgrade
```

Restore validates manifest paths, all hashes and the database before copying, then checks restored hashes. It does not overwrite the directory kept aside. A copy interrupted by disk failure may leave an incomplete target; retain the backup and move that target aside before retrying. Start Steward normally after successful restoration; pending work is recovered by its existing worker recovery process.

## Conservative artifact cleanup

Preview first:

```powershell
.venv/Scripts/python.exe scripts/maintenance.py --data-dir data/workspace cleanup --older-than-days 7
```

The command is a dry run unless `--apply` is supplied:

```powershell
.venv/Scripts/python.exe scripts/maintenance.py --data-dir data/workspace cleanup --older-than-days 7 --apply
```

Cleanup is deliberately limited to SHA-256-named `.zip` files directly inside `ingestion-input/` and UUID-named scratch directories directly inside `ingestion/`. It retains unknown names/types, recent files or directories, and artifacts referenced by any persisted record or work item. A referenced ancestor or descendant protects the whole candidate; recorded archive hashes also protect their ZIPs. The minimum age is one day.

All database records, history, revisions, checks, blobs, snapshots, managed branches, `worktrees/` and `model-smoke/` evidence are retained. Output lists eligible and retained candidates, bytes deleted and total workspace bytes retained. This is bounded orphan cleanup, **not a global storage quota**. It does not expire review evidence. The API/worker must remain stopped for the complete operation; the write lock cannot prevent an unrelated process from changing files.

## Verification

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
.venv/Scripts/python.exe -m pytest tests/test_maintenance.py -q -p no:cacheprovider
```

Tests use isolated temporary workspaces and cover committed WAL data, migrations and queued work, preserved absolute evidence paths and bytes, active-service/lease refusal, destination boundaries, link refusal, tampering/traversal, dry-run behavior, referenced and recent artifacts, unexpected file types and invalid retention ages. They do not alter the running demo workspace.
