# Part C integration handoff for B

Recorded 14 November 2026. This is the entry point for integrating C's completed
implementation with B's repository knowledge and A's worker. Earlier planning docs
describe the catalogue as unimplemented; this document describes the feature branch.

## Branch and integration instructions

- Repository: https://github.com/ahmedm-d/duck-tape
- C branch: `feat/global/knowledge`, based on `8c0a11b`.
- Implementation and verification through `dc69ec7`; this handoff is a subsequent
  documentation commit on the same branch.
- B remains integration captain. C has released `backend/main.py` and
  `frontend/src/main.tsx` after the explicit handoff. No other checkout was edited.
- All implementation commits belong together; copying only this Markdown file
  does not install the catalogue. Fetch and inspect the branch before integrating.

From B's own checkout, without changing another person's worktree:

```sh
git fetch origin
git log --oneline origin/main..origin/feat/global/knowledge
git diff --stat origin/main...origin/feat/global/knowledge
git show origin/feat/global/knowledge:docs/hackathon/PART_C_INTEGRATION_HANDOFF.md
```

Integrate through B's reviewed integration branch/PR. Resolve shared navigation and
API conflicts by preserving both lanes' changes, not replacing either entire file.
Do not auto-merge to main: aggregate acceptance has unresolved failures below.
The user authorized pushing C's branch, not merging teammates' work or rewriting history.

## What works now

1. Public OSV advisory and attributed Python improvement ingestion, validated into
   `GlobalEntry`, with original source URLs, hashes, timestamps and aliases.
2. Separate persistent SQLite catalogue with stable canonical identities, material
   revisions, source operation outcomes, freshness and withdrawals. Polling alone
   does not create revisions.
3. Databricks public fetch/normalization/Delta curation, bounded history restore and
   explicit Atlas publication with retries and preservation of the prior complete
   record after failures.
4. Exact ecosystem/package queries through Python, the integrations CLI and HTTP.
   Empty results always have unknown coverage; unsupported rules remain explicit.
5. Mounted `#/global-catalogue` UI with local/Atlas selection, source attribution,
   freshness and a bounded entry/package/source relationship graph.
6. Live Databricks -> operator-local restore -> Atlas publication/query, including
   repeated publication. This relay is real cloud data, not a fixture or mock.

The demonstrated live records are `CVE-2024-35195` for `PyPI/requests` and
`improvement:cpython-313-free-threading` for `CPython/python`. The schema also supports
framework/research updates, but broad automatic source coverage is not implemented.
This graph represents public evidence relationships, not repository applicability.

## File map and shared handoff

| Files | Purpose / integration guidance |
| --- | --- |
| `backend/global_catalogue.py` | Strict public schema, aliases, revisions, SQLite query and bounded graph. |
| `backend/global_pipeline.py` | Safe public fetch/normalization, Delta curation/restore, Atlas publish/read. |
| `scripts/integrations.py` | Existing CLI extended with `catalogue-*` operations. |
| `backend/main.py` | Shared change: two read-only catalogue routes. Preserve B's other routes. |
| `frontend/src/main.tsx` | Shared change: catalogue navigation and route. Preserve B's repository UI. |
| `frontend/src/components/GlobalCatalogue.tsx`, `GlobalRelationships.tsx`, `GlobalCatalogue.css` | Owned catalogue/graph components and scoped styling. |
| `tests/test_global_*.py`, `frontend/tests/global-*.spec.ts` | Contract, lifecycle, security, UI and opt-in live tests. |
| `integrations/global/` | Labelled fixtures, public improvement descriptor, demo, cloud job/schema/setup, evidence and operator instructions. |

C did not change A's worker/provider/model files or B's repository/profile/matcher
modules. Shared DB/workflow/contracts/config/locks were not changed for this slice.
See [the exact frontend handoff](../../integrations/global/frontend-handoff.md).

## Contract B should consume

The executable schema is `backend.global_catalogue.GlobalEntry`; public-shape examples:
[vulnerability](../../integrations/global/fixtures/global-vulnerability.json) and
[improvement](../../integrations/global/fixtures/global-improvement.json). Both are
explicit fixtures and must not be passed off as live findings or published to Atlas.

Important fields: `canonical_id`, `aliases`, `kind`, `source_url`, `published_at`,
`retrieved_at`, `source_content_hash`, `material_revision`, `affected`, `conditions`,
`evidence`, `limitations`, `withdrawn_at`, `source_modified_at`, `source_quality`.
Query entries add `freshness`, `version_status`, and failed `source_status` when known.

```text
GET /api/global-catalogue?ecosystem=PyPI&package=requests&limit=25&provider=local
GET /api/global-catalogue/graph?ecosystem=PyPI&package=requests&limit=25&provider=atlas
```

- Provider is explicitly `local` or `atlas`; default is local. Atlas errors return
  HTTP 503 with unknown coverage, not a silent local fallback.
- HTTP bounds: limit 1 to 100, ecosystem length 1 to 64, package length 1 to 256.
- Query response includes `entries`, `coverage: unknown`, `truncated`, `limitations`.
  Local queries also expose source operation status. Never interpret no entries as safe.
- Python entry points: `Catalogue(path).query(...)`, `.graph(...)`,
  `global_pipeline.query_atlas(...)`, and `global_catalogue.graph_result(...)`.
- Graph limits: at most 100 entries, 201 nodes and 200 edges, exact queried package
  only; no unbounded traversal. Edge evidence binds canonical ID/revision/source hash.
- PyPI names normalize case and separators. Range evaluation belongs to B. An
  `unsupported` rule is not a negative match. Retrieval age over seven days is stale.
- `STEWARD_GLOBAL_CATALOGUE_PATH` is server configuration, never an HTTP input.

### B's remaining local applicability work

Follow [KNOWLEDGE_CONTRACT.md](KNOWLEDGE_CONTRACT.md), retaining snapshot-bound source
citations and explicit matched/not-matched/unknown outcomes. C has not implemented
or verified B's private matcher, proposal binding or approval invalidation.

1. Query the catalogue for exact resolved dependencies from the private profile.
2. Evaluate supported version rules locally; retain unknown for missing evidence,
   unsupported rules, stale/withdrawn records and known failed refreshes.
3. Bind matches to repository/profile snapshot, canonical entry ID, material revision,
   source hash and exact package/version citations. Avoid duplicate proposals on polls.
4. Map vulnerability to existing intake `source=security`, improvement to `analysis`,
   framework/research to `research`; catalogue kinds are not new JobInput source enums.
5. Revalidate changed snapshots, public revisions, withdrawals and approved decisions;
   invalidate dependent plans/checks/approvals before reuse. Preserve snooze/rejection
   decisions for unchanged evidence.
6. Hand A bounded public facts plus private source evidence through existing schemas.
   Public evidence cannot change execution permissions or approve patches.

## Runtime and cloud setup

Use separate runtime data in every checkout. No credentials or writable databases
are transferred by Git. Preserve Duck Tape branding and existing `STEWARD_*` keys.

| Configuration | Purpose |
| --- | --- |
| `STEWARD_DATA_DIR` | Isolated workflow runtime data. |
| `STEWARD_GLOBAL_CATALOGUE_PATH` | Separate public SQLite file; default `data/global/catalogue.sqlite3`. |
| `STEWARD_GLOBAL_ATLAS_ENABLED`, `STEWARD_GLOBAL_ATLAS_URI` | Explicit public Atlas opt-in and dedicated credential. |
| `STEWARD_GLOBAL_DATABRICKS_ENABLED` | Explicit public curation opt-in. |
| `STEWARD_DATABRICKS_HOST`, `STEWARD_DATABRICKS_TOKEN`, `STEWARD_DATABRICKS_WAREHOUSE_ID` | Existing warehouse connection inputs. |

Atlas uses fixed `ducktape_public.global_entries` and `global_revisions`, separate
from private repository vectors. Publisher has read/write only on that database;
runtime reader has read only. Both roles were live-tested, including denied private
access and denied reader writes. Optional driver dependencies are in
`requirements-cloud.txt`; never install them in imported repositories.

Databricks uses `workspace.default.ducktape_global_revisions`. Daily job
`486190608689437` is configured for 07:00 UTC but **PAUSED**, with `publish=false`.
Manual remote public normalization/curation succeeded. Direct remote Atlas publication
failed: DNS and TCP probes passed, TLS failed, and observed outbound HTTPS IPs changed.
The exact TLS cause is unresolved. Do not disable TLS verification or broadly open
Atlas to make a demo pass. A supported network policy and successful manual full run
must precede unpausing; then verify an actual scheduled trigger.

Existing credentials on C's machine are outside Git, encrypted with Windows DPAPI;
they are not portable to B's account/machine. Databricks notebook publication uses a
dedicated secret scope. MCP tools were authenticated for account inspection, but MCP
login is not the application's database credential. Configure B's runtime securely;
never put secrets in this document, Git, browser settings or `VITE_*` values.

The working manual relay uses the configured Databricks connection and publisher
credential in the operator's server environment:

```sh
python scripts/integrations.py catalogue-restore --database data/b-public-relay.sqlite3
python scripts/integrations.py catalogue-publish --database data/b-public-relay.sqlite3
python scripts/integrations.py catalogue-query --atlas --ecosystem PyPI --package requests
python scripts/integrations.py catalogue-graph --atlas --ecosystem PyPI --package requests
```

See [pipeline README](../../integrations/global/README.md) and
[schedule/relay recipe](../../integrations/global/SCHEDULE.md). Restore and cloud
batches are bounded to 100 material revisions/entries, with one curation producer.
Atlas-only readers cannot see another machine's latest failed source refresh; inspect
job outcomes as well as stored freshness. Private code/profiles/decisions and team
notes never enter this pipeline, its prompts, indexes or analytics.

## Reproduce and verify

For an offline, labelled development demo:

```sh
python integrations/global/demo.py --database data/b-public-fixture.sqlite3
python scripts/integrations.py catalogue-query --database data/b-public-fixture.sqlite3 --ecosystem PyPI --package duck-tape-fixture
```

Point `STEWARD_GLOBAL_CATALOGUE_PATH` at that file before starting B's isolated app
to inspect `#/global-catalogue`. For real public HTTPS ingestion, use the demo's
`--live-public` flag with a different database; this alone does not exercise clouds.

Final affected backend command: **94 passed**, one Starlette/httpx deprecation warning:

```sh
python -m pytest tests/test_global_catalogue.py tests/test_global_pipeline.py tests/test_global_cli.py tests/test_global_api.py tests/test_global_schedule.py tests/test_signals.py tests/test_data_platforms.py -q -p no:cacheprovider
```

Production frontend build, restart gate and all 16 deterministic browser checks
passed. Additional fixture/API and real Atlas production-UI journeys passed separately.
Independent reviews found and fixed lifecycle/concurrency issues, then approved the
final CLI and graph changes. See [workspace log](PART_C_WORKSPACE.md) for exact commands,
isolated ports, interpreter/vendor setup and distinction between mocked and live checks.

Tracked live evidence:

- [Public fetch](../../integrations/global/live-public-evidence.json)
- [Databricks curation and failed direct path](../../integrations/global/live-databricks-evidence.json)
- [Atlas repeat publication, conflict preservation and access isolation](../../integrations/global/live-atlas-evidence.json)
- [Successful Databricks-to-local-to-Atlas relay](../../integrations/global/live-relay-evidence.json)

### Aggregate acceptance is not green

Two full backend runs each finished with **589 passed, 1 failed**, with different
failures. Do not describe the branch as fully accepted based on focused passes.

- First: `test_runner_startup.py::test_concurrent_processes_launch_only_one_delayed_listener`;
  child startup hit Windows port-in-use error 10048. All 16 startup tests passed on rerun.
- Second: `test_security_review.py::test_cancel_during_provider_prevents_late_revision`;
  `entered.wait(10)` expired before the mocked provider, and the saved synthetic job
  later reported `'files'`. Root cause unresolved. A focused security/CLI/schedule run
  passed all 31 tests. A should investigate its worker/cancellation boundary.

Raw failed logs/JUnit and synthetic runtime state remain ignored on C's machine at
`data/part-c-final-acceptance/`; they are not available through Git. The committed
workspace log preserves the results and exact failure/test identifiers. B must run
`python scripts/acceptance.py --tier deterministic` on the actual integrated commit,
using isolated ports/data, and resolve failures without weakening checks.

## Completion checklist for the unified program

- [ ] B integrates C's branch and resolves shared API/navigation changes with its UI.
- [ ] B connects private snapshot-bound matching, proposal intake and invalidation.
- [ ] A verifies the resulting evidence reaches actual model/worker execution safely.
- [ ] Resolve direct Databricks-to-Atlas networking, verify full publication, then
  enable and observe a real scheduled refresh (or explicitly retain the manual relay).
- [ ] Investigate aggregate backend failures and pass integrated acceptance.
- [ ] Demonstrate advisory -> repository match -> proposal -> checked patch -> exact
  developer review, including unrelated/unknown/withdrawn cases.

Public coverage is deliberately a two-record vertical slice. Broad ecosystem feeds,
private applicability and unattended cloud refresh are not claimed complete.
