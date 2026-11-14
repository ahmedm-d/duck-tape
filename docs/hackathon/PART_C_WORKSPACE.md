# Part C workspace: Global Knowledge

Branch: `feat/global/knowledge`, based on `8c0a11b` from `origin/main`.
Workspace: **Duck Tape - Part C: Global Knowledge**. Keep implementation here; do not edit the main checkout or rewrite shared history.

## Objective

Build Duck Tape's public knowledge catalogue for vulnerabilities, improvements, framework notices and research. Databricks prepares and versions public-source data; MongoDB Atlas serves the curated catalogue. B matches those records to private repository evidence locally; A implements resulting jobs. Cloud services belong to Duck Tape itself, never imported repositories.

Read [the C kickoff prompt](START_HERE.md#c-global-knowledge-databricksatlas-and-global-frontend), [team plan](TEAM_PLAN.md), [knowledge contract](KNOWLEDGE_CONTRACT.md), [repository rules](../../AGENTS.md) and [data-platform guide](../integrations/DATA_PLATFORMS.md).

## Starting point

Implemented: repository OSV scanning, explicit private Atlas vector projection/search, explicit Databricks terminal-metric export/query. Missing: a shared public catalogue, its ingestion/publication pipeline and global frontend.

The new workspace passed **49 focused baseline tests** on 14 November 2026: `python -m pytest tests/test_signals.py tests/test_data_platforms.py -q -p no:cacheprovider`. This is deterministic baseline evidence, not cloud acceptance.

## Delivery checklist

- [x] Create an isolated workspace from the reviewed Duck Tape build.
- [x] Review existing boundaries and run relevant baseline tests.
- [x] Implement validated public GlobalEntry records, provenance, stable canonical identity, aliases and material revisions.
- [x] Ingest one public OSV record and one attributed improvement with bounded fetches and explicit coverage/failures.
- [x] Provide persistent local catalogue lookup and labelled fixtures using the same contract.
- [x] Add Databricks public ingestion/normalization/curation artifacts and idempotent publication to a separate Atlas catalogue (deterministic verification only).
- [x] Implement bounded package/ecosystem queries, freshness/withdrawal status and explicit unknown results.
- [x] Build and browser-test catalogue/source/freshness components; prepare the exact shared-file handoff for B.
- [ ] Mount the catalogue API route and application navigation with B, then verify the integrated browser journey.
- [x] Exercise repeat ingestion, source changes, failed publication/retry, privacy boundaries and unavailable integrations.
- [x] Record unavailable cloud configuration as an explicit external blocker.
- [ ] Verify live Databricks processing and Atlas publication/query after operator configuration.
- [x] Complete independent review, integration checks and a reproducible demo; update handoff docs and commit reviewed changes on this branch.

## First working slice

Ingest a bounded public advisory and an attributed improvement, validate and store stable revisions, then query them through the existing integrations CLI. Unchanged content or polling timestamps must not create a new revision. Keep the last complete published revision on failure. The global service returns public evidence and applicability rules; B decides whether they apply to its private snapshot.

## Ownership and boundaries

C owns `backend/signals.py`, `backend/data_platforms.py`, `scripts/integrations.py`, `integrations/**`, global catalogue modules/tests and the data-platform guide. Reuse current helpers rather than inventing a second workflow engine. A owns models/worker; B owns private profiles, repository retrieval, local matching and repository proposal invalidation.

B and C both build frontend/backend. Develop independent catalogue components here. Claim exact shared routes, types, configuration, navigation, `main.tsx`, styles and browser fixtures before changing them, then hand off a small commit with actual checks. B is the initial integration captain. Do not overwrite teammates' changes.

Public records, private repository vectors, approved team decisions and terminal metrics remain separate. Team notes never enter models, indexes, training or cloud analytics. `knowledge.py` scope `global` means repository-wide only. Public evidence cannot change permissions; unsupported versions and stale/failed ingestion are unknown, not clean scans. Catalogue publication does not approve a project policy or patch.

## Decision log

### Implementation plan and exclusive file assignments

- Catalogue agent: `backend/global_catalogue.py`, `tests/test_global_catalogue.py`. Strict public schema, normalized records, aliases, SQLite revisions, bounded exact queries. Test stable revisions, collisions, stale/withdrawn/unsupported evidence and rejected private fields.
- Pipeline agent: `backend/global_pipeline.py`, `tests/test_global_pipeline.py`, `integrations/global/**` except frontend handoff. Bounded public fetch, Databricks curation, opt-in separate Atlas publication/query. Test disabled clients, allowlists, failed writes and retries.
- Frontend agent: new `GlobalCatalogue` component/scoped styles and standalone browser harness; `integrations/global/frontend-handoff.md`. Render query, provenance, freshness and explicit coverage gaps; verify build and browser behavior.
- Coordinator: `scripts/integrations.py`, `tests/test_global_cli.py`, this checklist and `docs/integrations/DATA_PLATFORMS.md`. Wire CLI, run real public fetch demo, inspect local account configuration without secrets, then independent review and fixes.
- Shared files remain with B. Required integration handoff: `backend/main.py` GET catalogue route and `frontend/src/main.tsx` catalogue navigation/render. No shared config, API types, CSS or workflow changes are claimed here.
- Ruling: separate catalogue SQLite file, rather than altering shared workflow migrations, keeps this slice usable through the existing CLI while respecting B's ownership. Atlas remains a separate explicit public projection.
- Each implementation task writes failing regression tests, implements the minimal behavior, runs its focused checks, and commits explicit paths. Final review cross-checks all tasks and records remaining external blockers.

- Start with OSV and one curated improvement; broad autonomous research follows a working bounded flow.
- Keep SQLite workflow transactions and explicit cloud opt-in. No paid provisioning or silent uploads.
- Use labelled local fixtures when external accounts are unavailable; fixture tests do not prove live integration.
- Preserve the existing `STEWARD_*` technical compatibility settings and Duck Tape product branding.

## Verification and review evidence (14 November 2026)

All Python commands used `C:/Users/ahmed/Projects/ducktape/steward/.venv/Scripts/python.exe`, with `PYTHONPATH` set to this worktree followed by `C:/Users/ahmed/Projects/ducktape/steward/vendor-current`, and `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. Imported `backend.__file__` was checked and resolves here. No original environment file or shared application database was used.

- Baseline plus initial CLI: `-m pytest tests/test_signals.py tests/test_data_platforms.py tests/test_global_cli.py -q -p no:cacheprovider`: 52 passed.
- Broad non-live run: `-m pytest -m 'not live' -q -p no:cacheprovider`, `STEWARD_DATA_DIR=<this worktree>/data/part-c-suite`: **560 passed, 18 skipped, 5 deselected** in 413.23s. One dependency warning: Starlette TestClient's httpx deprecation. Log: ignored `data/part-c-suite-results.txt`. This run collected before the final review regressions were added; the final affected run covers those separately.
- Affected backend run: `-m pytest tests/test_global_catalogue.py tests/test_global_pipeline.py tests/test_global_cli.py tests/test_signals.py tests/test_data_platforms.py -q -p no:cacheprovider`: **79 passed** before the final initial-publication-race regression; final result recorded below.
- Frontend: `npm ci --no-audit --no-fund` installed isolated dependencies without lock changes. `npm run build`: passed. `STEWARD_BROWSER_EXECUTABLE=C:/Program Files/Google/Chrome/Application/chrome.exe`, `npx playwright test tests/global-catalogue.spec.ts --workers=1`: **3 passed**, independently rerun by coordinator. These mount the real component with fixture responses, not the future shared API route.
- `python integrations/global/demo.py --database data/part-c-fixture/catalogue.sqlite3`: labelled fixtures passed stable-revision and bounded-query assertions.
- `python integrations/global/demo.py --database data/part-c-live/catalogue.sqlite3 --live-public`: real HTTPS OSV and Python documentation ingestion passed stable revision assertions. Both records remain revision 1. Public source hashes/retrieval timestamps are recorded in `integrations/global/live-public-evidence.json`; no cloud connection was attempted.
- `catalogue-status` reported both public cloud services disabled/unconfigured. No `.env` here, no public integration environment keys, and no Atlas/Databricks connector tools available. No secrets were printed or accounts provisioned.

Independent cross-review found and fixed: future timestamps poisoning refresh guards; SQLite connections surviving transaction exit; Atlas same-revision freshness and first-publication races; fractional-second timestamp ordering; alias normalization breaking Delta restore; partial cloud failure incorrectly returning CLI success. Reviewers separately checked public schema, failed-source visibility, safe frontend rendering/accessibility, fixtures and the CLI. Core fixes are in `d97cf92`; frontend implementation and handoff are in `e98e7fe`.

## Explicit integration and external limits

- B retains `backend/main.py` and `frontend/src/main.tsx`. The component is implemented and browser-tested but unmounted. Exact requested route/navigation changes and response contract are in `integrations/global/frontend-handoff.md`. No shared route/type/config/style/workflow files were edited.
- Live Databricks execution, Atlas publication and Atlas queries are blocked by absent configuration. Mock transports and local SQLite do not qualify those services.
- Public curation is bounded to 100 restored revisions and one producer; scale/concurrent curation requires a larger controlled pipeline. Atlas-only queries cannot observe a separate ingestion machine's latest failed refresh; read job operation results and keep unknown coverage/TTL labels.
- Private profile matching, proposal invalidation and intake bindings remain B's work. A's providers/models/worker and teammates' checkouts remain untouched. The integration captain still runs the final aggregate acceptance gate on the integrated commit.
- The workspace checklist and implementation/evidence/handoff files are committed on the feature branch with explicit paths. No remote pushes, merges, deployments or history rewrites.

### Final checks

- Final affected backend command above: **80 passed in 6.84s** after all review fixes, including initial-publication races and captured-socket transfer timeout.
- Both fixture and real-public demo assertion runs passed again on final implementation; source metadata artifact refreshed, both material revisions still 1.
- Coordinator independently reran production build and Chrome browser checks: passed, 3 browser tests.
- Public pipeline committed `2d589e8`. The transfer timer captures the TLS socket even if HTTP response handling clears the connection's socket reference; slow-drip header/body timeout regression passes. OS DNS timeout remains explicitly documented.

### Lead handoff verification

- Inspected the five implementation commits through `149f6d4`, confirmed main remains at `8c0a11b`, and verified the documented API/navigation handoff is still outstanding.
- Independently ran `python integrations/global/demo.py --database data/part-c-handoff-check/catalogue.sqlite3`: passed; both labelled fixture entries stayed at material revision 1 on repeated ingestion, queries returned explicit unknown coverage, and no cloud connection was attempted.
- Saved this checklist in Git so incomplete mounting, private applicability matching and live cloud acceptance remain visible to the integration captain. Prior broad test totals above are the implementation agent's recorded runs, not a repeated lead run.
