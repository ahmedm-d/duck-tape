# Duck Tape internal data infrastructure

MongoDB Atlas provides Duck Tape's internal repository-memory projection and evidence retrieval; Databricks provides Duck Tape's internal agent evaluation and terminal-run analytics. Server operators configure this infrastructure for Duck Tape itself. These adapters do not install features into client repositories. SQLite remains authoritative for snapshots, checks, approvals, jobs, and leases. Importing this module, inspecting local status, and running jobs never uploads data. Each manual bootstrap operation requires its own enabled integration.

## Environment

Duck Tape operators set these in the Duck Tape server/CLI process environment. Do not put credentials in saved UI settings, source files, command-line arguments, or screenshots.

| Variable | Default / requirement |
| --- | --- |
| `STEWARD_ATLAS_ENABLED` | Disabled unless exactly `1` |
| `STEWARD_ATLAS_URI` | Required `mongodb+srv://user:password@cluster.mongodb.net/` Atlas URI; percent-encode credentials |
| `STEWARD_ATLAS_DATABASE` | `ducktape` |
| `STEWARD_ATLAS_COLLECTION` | `evidence_vectors` |
| `STEWARD_ATLAS_INDEX` | `evidence_vectors` |
| `STEWARD_ATLAS_DIMENSIONS` | Required integer, 1–8192; match the configured embedding model |
| `STEWARD_DATABRICKS_ENABLED` | Disabled unless exactly `1` |
| `STEWARD_DATABRICKS_HOST` | Required HTTPS workspace origin, without an API path |
| `STEWARD_DATABRICKS_TOKEN` | Required server-side bearer token |
| `STEWARD_DATABRICKS_WAREHOUSE_ID` | Required existing SQL warehouse ID |
| `STEWARD_DATABRICKS_TABLE` | `workspace.default.ducktape_runs`; three identifier components |

Atlas reuses the existing embedding-provider configuration. Start with local embeddings when source privacy matters. Existing configured remote embedding providers still receive the source text they embed. Configured integration status is a local configuration check, not a successful cloud connection.

## Atlas setup and operation

Install the optional pinned driver with `python -m pip install -r requirements-cloud.txt`. This uses PyMongo 4.20.0 (Python 3.11+); the core local application does not require it. The driver enforces TLS verification, bounded connection/request timeouts, and an Atlas SRV hostname. URI options are limited to `retryWrites`, `w`, `appName`, and `authSource`. Write concern must be `majority` or a positive integer when provided; unacknowledged writes (`w=0`) are rejected.

Create the database/collection and a database user with only the needed access in your Atlas account. Restrict its network access to your development machine. Provision one vector index manually on that collection, using `integrations/atlas/vector-index.json` as a 768-dimension example. Generate the exact definition for your configured dimension using the local function below; the adapter validates the existing index instead of creating or changing it.

```python
from backend import data_platforms

print(data_platforms.status())  # Local only; no endpoints or credentials returned.
print(data_platforms.atlas_index_definition())  # Copy this definition into Atlas.
print(data_platforms.atlas_sync(repo_id, snapshot_id))
print(data_platforms.atlas_search(repo_id, snapshot_id, 'where is input validated?', limit=12))
```

`atlas_sync` uploads only SHA-256 repository/snapshot/embedding-space identifiers, deterministic chunk IDs, and finite nonzero embedding vectors. Embedding-space identity includes the configured model, provider endpoint, and dimensions, but those values themselves are not uploaded. Source text, paths, symbol names, team notes, prompts, credentials, and provider configuration are not stored in Atlas by this adapter. Source files matching the application's secret-path or credential patterns are excluded in full before embedding. These patterns are heuristic: embeddings are source-derived data, so explicitly choose repositories that may be shared with Atlas.

The adapter upserts by a deterministic ID, so rerunning a partial upload is safe. The initial limit is 2,000 chunks per snapshot and embedding batches of 32. It never deletes cloud records. Index creation and subsequent indexing are asynchronous: `index_ready` indicates the existing index is queryable, not that every newly written vector is already searchable. An index still building can receive vectors, but search reports it as unavailable. A mismatched index definition fails before upload. [PyMongo indexes](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/indexes/)

Search applies repository, snapshot, and embedding-space filters before nearest-neighbor matching. It returns at most 24 results; query text is limited to 12 KiB and credential-pattern matches are rejected. Returned IDs must resolve to locally allowed immutable chunks; all displayed source content comes from those local chunks. A remote ID, namespace, or score mismatch is discarded. The optional driver must be installed for actual Atlas calls. [Vector query API](https://www.mongodb.com/docs/vector-search/query/aggregation-stages/vector-search-stage/)

Atlas Free supports at most three total Search/Vector Search indexes, so this setup uses one. The former Atlas Data API reached end of life on September 30, 2025; this adapter uses the supported database driver. [Atlas limits](https://www.mongodb.com/docs/search/deployment/feature-compatibility/), [Data API retirement](https://www.mongodb.com/docs/api/doc/atlas-app-services-admin-api-v3)

## Databricks setup and operation

Use an existing SQL warehouse and run `integrations/databricks/schema.sql` yourself in the SQL editor. Adapt its fully qualified table name if you change `STEWARD_DATABRICKS_TABLE`. No schema, table, warehouse, account, or cloud infrastructure is provisioned by the application.

```python
from backend import data_platforms

print(data_platforms.databricks_sync(run_id))
print(data_platforms.databricks_summary())
```

Each sync exports one terminal run: `complete`, `failed`, `cancelled_or_superseded`, or `interrupted`. Active and awaiting-input/approval runs are rejected. Run/repository identifiers are hashed; only controlled status values, timestamps, booleans, and numeric counts/timings leave the machine. No title, description, source, patch, log, error text, model name, team note, or arbitrary metadata is included. The operation requires valid timezone-aware start/end times.

A completed run is not automatically verified. `verified=true` requires matching run/revision workflow, source and patch identities, a passed assessment and review, and required baseline/candidate evidence bound to that exact run and revision. The evidence is reassessed locally; required security checks and performance evidence apply where relevant, and bug fixes require a failing-before/passing-after regression. Earlier failed candidate attempts count toward checks but do not invalidate a later properly verified revision. Older fixture runs without these bindings remain complete and unverified. No improvement percentage is manufactured or exported.

Provider calls include retries and recorded failed stages, including failures caught before a later repair. Stage and run telemetry are reconciled without double counting terminal provider failures. Interrupted or cancelled/superseded runs always have unknown provider totals because recovery can reassign their stage records. Stage history without per-run identity in the same job/workflow also makes call, latency, and token totals unknown, even when the final patch remains verified. Cached/resumed zero-call stages do not count as provider calls. Latency uses recorded seconds converted to milliseconds. Token totals remain SQL `NULL` if any contributing request has unknown usage; a missing value is never converted to zero. See `evaluation.sql` for denominator-aware examples. Manually selected exports are a selected subset of runs: export all relevant terminal runs before interpreting an aggregate as an overall success rate.

Sync uses a parameterized `MERGE` keyed by hashed run ID so retries update the same row. SQL identifiers are strictly validated, and all row values are typed named parameters. The installed `httpx` client calls `POST /api/2.0/sql/statements` with `INLINE` JSON results, a 30-second wait, and `on_wait_timeout=CANCEL`. Only `SUCCEEDED` is treated as success; failed, incomplete, pending, or canceled results raise a safe error. No automatic retries or external result URLs are followed. A timeout may leave the outcome uncertain; inspect statement history and retry the idempotent sync. [Statement Execution API](https://docs.databricks.com/api/statement-execution/v1/execute-statement), [authentication and warehouse prerequisites](https://docs.databricks.com/aws/en/dev-tools/sql-execution-tutorial)

Databricks Free Edition is suitable for non-commercial experiments, has one 2X-Small SQL warehouse and fair-use limits, and does not provide production guarantees. Its published comparison says Databricks reserves the right to train on uploaded data. Use nonsensitive demonstration metrics in Free Edition. A supported warehouse/API and token are required; account-specific availability is not confirmed by this local implementation. [Free Edition limitations](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations), [Free Edition versus trial](https://docs.databricks.com/aws/en/getting-started/free-trial-vs-free-edition)

## Local validation and next scope

## Public global catalogue (Part C)

The public catalogue is separate from repository vectors and run metrics. Its strict
`GlobalEntry` contract is implemented in `backend/global_catalogue.py`; local state
lives in `data/global/catalogue.sqlite3` by default. No repository profile, approved
decision or team note is an ingestion input. The CLI does not create proposals or
change execution permissions. B owns local applicability matching and snapshot-bound
proposal invalidation.

```sh
python scripts/integrations.py catalogue-status
python scripts/integrations.py catalogue-ingest-osv --id GHSA-9wx4-h78v-vm56
python scripts/integrations.py catalogue-ingest-improvement --record integrations/global/python313-improvement.json
python scripts/integrations.py catalogue-query --ecosystem PyPI --package requests --limit 10
python scripts/integrations.py catalogue-operations
```

For a repeatable two-record demonstration, use a dedicated database:

```sh
python integrations/global/demo.py --database data/global-fixture-demo/catalogue.sqlite3
python integrations/global/demo.py --database data/global-live-demo/catalogue.sqlite3 --live-public
```

The first command uses labelled fixtures. The second fetches real public OSV and
Python documentation, ingests each twice and asserts stable material revisions,
then performs bounded exact-package lookups. Neither command contacts cloud services.
`integrations/global/live-public-evidence.json` records the actual public-source
run, hashes and retrieval timestamps; it is not Databricks or Atlas evidence.

Each local command accepts `--database PATH` for an isolated catalogue (except
configuration-only `catalogue-status`). Repeat ingestion keeps the material revision
when only retrieval time or source bytes change without changing normalized facts.
Aliases resolve to the existing canonical identity; conflicting identities fail
instead of silently merging unrelated records. Queries return bounded exact package
results, partial/unknown coverage, freshness and unsupported version-rule status.
An empty result never establishes that a dependency is safe. Withdrawal remains
visible evidence for B to re-assess, not permission to prepare another patch.

`catalogue-import --record PATH` imports one validated public `GlobalEntry`, including
labelled development fixtures. It does not fetch or verify that record's source.
Cloud publication rejects fixture-labelled entries. Live source ingestion performs
bounded HTTPS fetches against a fixed public host allowlist, refuses redirects,
validates public DNS and pins the connection to a validated address. Failures retain
previous valid catalogue content and are available through `catalogue-operations`.

Public cloud operations require separate explicit opt-in:

| Variable | Meaning |
| --- | --- |
| `STEWARD_GLOBAL_ATLAS_ENABLED` | Exactly `1` permits explicit public Atlas operations |
| `STEWARD_GLOBAL_ATLAS_URI` | Dedicated Atlas credential; never reuse a broad private-vector role |
| `STEWARD_GLOBAL_DATABRICKS_ENABLED` | Exactly `1` permits explicit public Databricks operations |

Public Atlas uses `ducktape_public.global_entries`, separate from the configured
private database. Public Databricks uses `workspace.default.ducktape_global_revisions`,
separate from the metrics table, with the existing server-side Databricks host/token/
warehouse settings. See `integrations/global/` for public pipeline artifacts and
least-privilege setup. Nothing provisions a paid service or creates accounts.

```sh
python scripts/integrations.py catalogue-curate
python scripts/integrations.py catalogue-publish
python scripts/integrations.py catalogue-query --atlas --ecosystem PyPI --package requests --limit 10
```

These are explicit cloud operations, not startup hooks. Batches are bounded and
publication retries are idempotent; failed writes preserve the previous complete
published entry. Partial publication returns a nonzero CLI exit status with safe
per-record failures. The
frontend component and exact integration instructions live in
`frontend/src/components/GlobalCatalogue.tsx` and
`integrations/global/frontend-handoff.md`; shared application routes/navigation
remain B's integration handoff.

Configuration inspection in the isolated Part C workspace found both services
disabled/unconfigured, with no local `.env`. Live public-source fetches succeeded;
Databricks execution and Atlas publication/query remain unqualified without actual
account evidence. Local SQLite, fixture and mocked transport tests do not establish
cloud connectivity or qualification. The maintained workspace checklist records
exact checks and remaining blockers.

### Part C verification, 14 November 2026

Python used the existing interpreter at
`C:/Users/ahmed/Projects/ducktape/steward/.venv/Scripts/python.exe`, with
`PYTHONPATH=<this worktree>;C:/Users/ahmed/Projects/ducktape/steward/vendor-current`
and `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. Imports were verified to resolve to this
worktree. Test/runtime data stayed under this worktree's ignored `data/` directory.

| Check | Actual result |
| --- | --- |
| `python -m pytest tests/test_global_catalogue.py tests/test_global_pipeline.py tests/test_global_cli.py tests/test_signals.py tests/test_data_platforms.py -q -p no:cacheprovider` | 80 passed after all review fixes |
| `python -m pytest -m 'not live' -q -p no:cacheprovider` | 560 passed, 18 skipped, 5 deselected; one existing Starlette/httpx deprecation warning. Collected before the final review regressions; the affected run above covers the final changes. |
| `npm run build` in `frontend/` | Passed TypeScript and production build |
| `npx playwright test tests/global-catalogue.spec.ts --workers=1`, with `STEWARD_BROWSER_EXECUTABLE=C:/Program Files/Google/Chrome/Application/chrome.exe` | 3 passed; fixture-response component/browser checks |
| `python integrations/global/demo.py --database data/part-c-fixture/catalogue.sqlite3` | Two labelled fixtures; repeat revisions and bounded queries passed |
| Same demo with `--database data/part-c-live/catalogue.sqlite3 --live-public` | Two real public HTTPS sources; repeated ingestion stayed at revision 1; no cloud calls |

Independent reviewers checked catalogue lifecycle, cloud adapters, CLI behavior
and frontend contracts. Their fixes cover connection closure, future timestamp
rejection, alias-safe Delta restore, fractional timestamp ordering, concurrent
publication, a transfer deadline and nonzero CLI exit on partial publication.
HTTP transfer is capped at 30 seconds after public DNS validation; the existing OS
resolver has its own timeout, so total invocation time can exceed that deadline.

Unqualified external behavior: live Databricks processing/publication and Atlas
queries. Integration limits: one curation producer, at most 100 restored revisions,
and Atlas-only readers must consult job outcomes for recent source failures.
The shared route/navigation mount and private applicability lifecycle remain B's
handoff; the captain still runs aggregate acceptance on the integrated commit.

### Existing adapter validation

Run `python -m pytest tests/test_data_platforms.py -q`. The tests use local SQLite, a fake MongoDB collection, and `httpx.MockTransport`. They cover disabled/no-network behavior, upload privacy, namespace separation, invalid embeddings, index readiness, exact evidence binding, failed-run inclusion, unknown usage, retries, SQL parameters, and safe cloud errors.

No live cloud accounts are exercised by these tests. Worker retrieval integration, automatic exports, retention/deletion, cloud scheduling, and database migration are outside this initial adapter. Snapshot chunking and operational state remain local. The optional driver pin follows the [official PyMongo 4.20.0 release notes](https://pymongo.readthedocs.io/en/stable/changelog.html).
