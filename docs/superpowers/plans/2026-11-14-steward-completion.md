# Steward Full Local Product Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the original local-workspace product with live, evidence-grounded maintenance and actual isolated verification on supported imported repositories.

**Architecture:** Extend the current React/FastAPI/SQLite application. Separate immutable source storage, model evidence, edit application and sandbox execution; use persisted work items, stage attempts and input fingerprints to connect them. Preserve the sample and existing data, and require a real live acceptance gate alongside deterministic tests.

**Tech Stack:** Existing Python/FastAPI/Pydantic/SQLite, Python AST/Tree-sitter, React/TypeScript/Vite/Playwright; a Docker-compatible Linux runtime accessed only by the trusted controller; OpenAI-compatible or Ollama open-weight inference.

**Spec:** [Completion design](../specs/2026-11-14-steward-completion-design.md). Original product request remains authoritative; [existing verification](../../VERIFICATION.md) is the baseline, not the new release gate.

## Global constraints

- Planning only in this turn. Do not install, migrate, run workloads or change product code while preparing this plan.
- Preserve existing user data and secrets; no automatic source-checkout overwrite, main-branch merge, remote push or paid service.
- Loopback, local single workspace; demo identity remains labelled. Do not add production auth or cloud infrastructure to complete this scope.
- Imported code, model edits and package lifecycle scripts execute only in a verified sandbox. No host fallback except the existing fixed reviewed sample.
- Supported execution profiles: Python unittest/pytest with pinned requirements or uv.lock; JS/TS npm package-lock with Node test/Vitest/Jest. Other languages/managers retain explicit analysis-only capability until a tested adapter exists.
- Defaults: 2 CPUs, 2 GiB RAM, 256 processes, 5 minutes/check, 10 minutes/dependency preparation, 30 minutes/job; maximum captured command log 1 MiB, with marked truncation.
- Model defaults: at most 12 calls, 30,000 output tokens, two additional evidence rounds and two repair cycles within the job deadline. Record unknown usage as null.
- Preserve all 12 existing job states and enforce transitions server-side. Preparation approval, patch approval and actual application are separate facts.
- No fabricated understanding percentage, model-quality guarantee, live result or benchmark improvement. Unavailable services are blockers, not passes.
- No whole-app rewrite. Extract modules only as responsibilities below require them. Add no mandatory Redis, vector server, orchestration framework or Kubernetes.
- Run backend commands from the project root with its dependency environment active; on this prepared workspace use `PYTHONPATH=vendor-current` and `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, or the documented fresh virtual environment. Frontend commands run from `frontend/`.

## Release shape and critical path

| Milestone | Deliverable | Hard exit gate |
|---|---|---|
| M1: trustworthy execution substrate | Durable state, immutable complete snapshots, safe edits, isolated checks, usable provider | Tasks 1–6 deterministic tests; actual sandbox probes and initial per-role model smoke tests separately recorded |
| M2: first generic live flow | Imported Python and TS tickets produce patches and executed checks | Task 7 live flow succeeds without fixture branches; failure is inspectable if a prerequisite is unavailable |
| M3: repository-wide understanding | Real resolution, chunk retrieval, summaries, scoped knowledge | Tasks 8–10 independently specified evidence tests and live synthesis/embedding tests |
| M4: continuous work and delivery | Upstream refresh, qualified signals, decisions/revisions, managed application, full UX | Tasks 11–13 integration/browser journeys |
| M5: release acceptance | Reproducible setup and independent reviews | Task 14: original 14 journeys, live gates, no unresolved critical/major findings |

Critical path: **authorized sandbox + qualified model + source/edit integrity → live vertical slice → expanded workflows → release review**. The sandbox permission and model capability gates must be addressed first; do not spend the next phase only polishing the demo.

## File ownership and contracts

Lead owns `backend/db.py`, `backend/main.py`, migration integration, API contracts and acceptance. Delegate nonoverlapping files after agreeing the types in Task 1:

- Repository owner: `snapshots.py`, `repository.py`, `symbols.py`, `retrieval.py`, `summaries.py`, `knowledge.py` and related tests.
- Execution owner: `sandbox.py`, `dependencies.py`, `verification.py`, `execution_profiles.py`, sandbox images/probes and tests.
- Workflow/model owner: `providers.py`, `edits.py`, `workflow.py`, `worker.py` and related tests.
- Frontend owner when a slot frees: `frontend/src/**`, browser suites; consume the agreed API rather than edit backend ownership files.
- Lead integrates shared changes sequentially and commissions reviewers who did not implement the reviewed behavior where available.

New types in `backend/contracts.py` are Pydantic models, exported from that module:

- `SourceSnapshot`: id/repo_id, origin/branch/commit, manifest hash, entries with path/blob hash/byte size/mode/disposition, exclusions and gaps.
- `EvidenceRef`: repo_id, snapshot_id, path, blob_hash, chunk_id, start/end lines and reason. `ContextBundle`: refs plus captured content, symbols, approved knowledge versions, missing context, token estimate and fingerprint.
- `EditOperation`: discriminated create/update/delete/rename with path, expected blob hash, destination where needed, exact replacements or explicitly complete new content. `CandidatePatch`: revision attempt, base snapshot, changed-file manifest, Git diff/hash and cumulative instructions.
- `CheckCommand`: profile-owned ID, argv list, relative cwd, purpose and timeout. `CheckPlan`: commands, runtime image digest, dependency fingerprint, limits, approval/policy reference and fingerprint. No shell string execution.
- `CheckResult`: command/phase, exit code or exit reason, times/duration, bounded logs, base/patch/environment hashes, artifact hashes and status.
- `ProviderRequest`/`ProviderResult`: task, typed payload, model/capabilities, context/output budgets, response schema, result and attempt telemetry.
- `RunInputs`: snapshot, plan, context, knowledge versions, decisions, previous revision and execution-policy fingerprints. `RunCheckpoint`: stage, ownership generation, input fingerprint, outputs and attempt counts.
- `CancellationToken`: `is_cancelled() -> bool`, `raise_if_cancelled() -> None`; `LeaseLost` has the same abort behavior. Cancellation never bypasses publication guards.

Keep compatible API response fields during migration. Existing `retrieve(index, ...)` and provider calls may have thin compatibility entry points while callers migrate; remove them only after their callers/tests migrate.

## Review focus

1. A long source file matches a header but the required implementation is much later; editing must preserve all unrelated bytes. Task 3 and Task 9 pin this.
2. A crash occurs after a Git commit or revision publication but before the associated DB update; recovery must reconcile rather than duplicate. Tasks 1, 7 and 12 pin this.
3. A model/dependency process outlives cancellation or loses its lease; all descendants stop and late output cannot publish. Tasks 4, 6 and 7 pin this.
4. A new affected file introduces a scoped team constraint absent from initial retrieval; implementation must obtain it and invalidate older approval. Tasks 7 and 10 pin this.
5. Remote content changes while the user reviews old citations or switches repositories; evidence stays snapshot-bound and stale results cannot cross repositories. Tasks 2, 9, 11 and 13 pin this.

## Task 1: durable contracts, migration and state transitions

**Files:** Create `backend/contracts.py`, `backend/workflow.py`, `backend/migrations.py`, `tests/test_workflow_state.py`, `tests/test_migrations.py`; modify `backend/db.py`, `backend/main.py`, `backend/worker.py`.

**Interfaces:** `workflow.transition(job_id: str, action: str, payload: dict, expected_version: int | None = None) -> dict`; `db.atomic(fn)` passes one transaction connection; `workflow.input_fingerprint(inputs: RunInputs) -> str`; persisted `work_items` have kind, subject, idempotency key, lease owner/generation and checkpoint.

- [ ] Add failing tests: migrate a copy of the current schema without losing IDs/history; reject every illegal state transition; stale expected version returns conflict; a repeated idempotency key returns one operation; decision selection and queued state commit together.
- [ ] Run `python -m pytest tests/test_workflow_state.py tests/test_migrations.py -q`; confirm the new guarantees fail before implementation.
- [ ] Implement incremental migration/unique operation constraints and centralized transition guards. Do not normalize the whole JSON store unnecessarily. Make run finalization, revision/check publication and audit event one transaction. Reconcile abandoned running attempts as interrupted.
- [ ] Run new tests plus `tests/test_db.py`, `tests/test_api.py`, `tests/test_final_review.py`; verify existing approval and note semantics remain intact.
- [ ] Commit the tested contract/migration change; update the milestone checklist with migration compatibility evidence.

## Task 2: immutable source snapshots and recoverable import/refresh

**Files:** Create `backend/snapshots.py`, `backend/network.py`, `tests/test_snapshots.py`, `tests/test_import_refresh.py`; modify `backend/repository.py`, `backend/main.py`, `backend/worker.py`, `backend/db.py` through lead integration.

**Interfaces:** `snapshots.capture(repo_id: str, source_dir: Path, origin: dict) -> SourceSnapshot`; `snapshots.materialize(snapshot_id: str, destination: Path) -> dict`; `snapshots.refresh(repo_id: str, cancel: CancellationToken) -> SourceSnapshot`; `network.fetch_public(request: dict, limits: dict) -> dict` returns bounded bytes plus provenance.

- [ ] Add failing tests for binary test assets, executable mode, non-UTF8/oversize exclusions, secret files, identical-content Git/ZIP wrapper handling, 12,001-file inventory without a crash, replacement ZIP, changed/deleted upstream files and immutable historical content.
- [ ] Run `python -m pytest tests/test_snapshots.py tests/test_import_refresh.py -q` and confirm missing behavior fails.
- [ ] Store content-addressed sanitized blobs independently of the search index. Make inventory limits deterministic and dispositions honest. Create persisted ingestion work items with phase/count/error, make import APIs return their operation IDs, and keep retry idempotent.
- [ ] Implement public HTTPS Git transport behind the validated fetch boundary: use safe provider archive APIs where available and a read-only Git fetch environment through an egress broker for generic HTTPS. Pin/revalidate every destination and redirect; disable hooks/config/helpers/submodules and reject credentials/options/unsafe protocols. Fetch remote branch/commit on refresh; ZIP refresh uses an explicit replacement upload. Do not rely on DNS validation followed by unconstrained `git clone`.
- [ ] Preserve bounded available Git history and commit provenance without treating messages as instructions. Explicitly report shallow/archive-only history, missing submodules and inaccessible external systems; never silently substitute the initial source for an upstream refresh.
- [ ] Run snapshot/import tests plus `tests/test_repository.py` and `tests/test_security_review.py`; capture a live public Git import separately from deterministic upstream-change fixtures. Commit.

## Task 3: exact, complete patch operations

**Files:** Create `backend/edits.py`, `tests/test_edit_operations.py`; modify `backend/providers.py`, `backend/worker.py`, `backend/sample.py` only to adapt fixture responses to the common edit contract.

**Interfaces:** `edits.apply(snapshot: SourceSnapshot, operations: list[EditOperation], destination: Path) -> CandidatePatch`; `edits.read_file(snapshot_id: str, path: str, expected_hash: str) -> bytes` is an authorized evidence request, not a free host read.

- [ ] Add failing tests that change line 900 of a 1,000-line file while preserving every other byte; reject an ambiguous replacement and wrong preimage; create/delete/rename; retain executable mode/binary fixtures; reject secret/Git-metadata/path escapes; preserve cumulative revisions and explicit reverts.
- [ ] Run `python -m pytest tests/test_edit_operations.py -q` before implementation.
- [ ] Apply only snapshot-bound operations. Require complete-file evidence for whole replacement, otherwise exact unique anchored replacements. Generate Git diffs using the existing hardened Git helper; compare actual changed paths/content/modes to the declared manifest before publication.
- [ ] Run edit tests and existing worker/export/security regressions. Verify `git apply --check` on a separately materialized base and exact expected resulting hashes, including empty/deleted files.
- [ ] Commit; keep fixture-specific choices in `sample.py` rather than the generic edit engine.

## Task 4: verified sandbox lifecycle

**Files:** Create `backend/sandbox.py`, `sandbox/probes/`, `tests/test_sandbox.py`, `tests/live/test_sandbox_live.py`; modify `backend/main.py` settings health, `.env.example`, `docs/CONFIGURATION.md`.

**Interfaces:** `sandbox.health() -> dict`; `sandbox.prepare(run_id: str, snapshot: SourceSnapshot, plan: CheckPlan, cancel: CancellationToken) -> str` returns a sandbox ID; `sandbox.run(sandbox_id: str, command: CheckCommand, phase: str, cancel: CancellationToken) -> CheckResult`; `sandbox.cancel(run_id: str) -> None`; `sandbox.reconcile() -> list[dict]`.

- [ ] Write fake-runtime contract tests asserting exact isolation controls and failure-closed behavior; live probes attempt outbound network, host-canary/credential/socket access, privilege escalation, resource exhaustion and child-process survival after cancellation.
- [ ] Run `python -m pytest tests/test_sandbox.py -q`; the absent adapter must fail. Live tests must report unavailable explicitly if the daemon is inaccessible, not silently satisfy the release gate through skip counts.
- [ ] Implement nonprivileged Linux job containers with the design limits, isolated copies/scratch volumes, no broad bind mounts and no host socket. Persist container IDs/attempt ownership before starting commands; spool bounded logs; destroy/reconcile stale job containers by verified labels.
- [ ] Run `python -m pytest tests/test_sandbox.py tests/live/test_sandbox_live.py -q` on a permitted runtime. Availability requires active probe success; permission failure leaves analysis/export usable.
- [ ] Commit adapter, probes and setup diagnostics; record runtime/image identity and remaining prerequisite. Never change host security to pass this task.

## Task 5: reproducible environments and evidence-based verification

**Files:** Create `backend/dependencies.py`, `backend/execution_profiles.py`, `backend/verification.py`, `sandbox/images/python.Dockerfile`, `sandbox/images/node.Dockerfile`, `tests/test_verification.py`, `tests/test_dependencies.py`; modify `backend/worker.py`, configuration docs.

**Interfaces:** `execution_profiles.discover(snapshot: SourceSnapshot) -> list[CheckPlan]`; `dependencies.prepare(run_id: str, snapshot: SourceSnapshot, plan: CheckPlan, cancel: CancellationToken) -> dict`; `verification.run(run_id: str, base: SourceSnapshot, candidate: CandidatePatch, plan: CheckPlan, cancel: CancellationToken) -> list[CheckResult]`; `verification.assess(results: list[CheckResult], acceptance: list[dict]) -> dict`.

- [ ] Add failing tests for locked Python/npm artifacts, same package at multiple versions, install hooks, unsupported Git/custom-index dependency, baseline failure, failing-before/passing-after regression, removed/skipped test, type/build regression, benchmark result and post-change advisory status.
- [ ] Run `python -m pytest tests/test_verification.py tests/test_dependencies.py -q` before implementation.
- [ ] Build Steward-owned pinned runtime images. Acquire only exact dependency artifacts through the trusted public-registry broker, verify recorded hashes, then install/build offline in disposable containers. Cache keys include lockfile hash, image/platform and install policy. Never use a repository Dockerfile as trusted setup.
- [ ] Discover commands as untrusted proposals and map them to supported argv profiles. The preparation plan includes commands/dependency policy; unfamiliar execution needs a product decision. Preserve baseline checks and bind every result to base, patch and environment; enforce required check failures, benchmark validity and advisory validation uniformly. Record when a regression could not be reproduced rather than claim it was.
- [ ] Run deterministic tests and real Python/TS sandbox suites. Persist raw benchmark samples/workload; compare equivalent environments. Commit with live versus transport-only results separated.

## Task 6: usable provider configuration, budgets and model qualification

**Files:** Modify `backend/providers.py`, `.env.example`, `backend/main.py` settings; create `backend/model_schemas.py`, `scripts/evaluate_models.py`, `tests/test_provider_contracts.py`, `tests/live/test_provider_live.py`, `tests/fixtures/model_eval/manifest.json`; update configuration docs.

**Interfaces:** `providers.capabilities(role: str) -> dict`; `providers.complete(request: ProviderRequest, cancel: CancellationToken) -> ProviderResult`; `providers.embed(repo_id: str, snapshot_id: str, chunks: list[dict], cancel: CancellationToken) -> list[dict]`. Keep the current transport wrappers until migrated.

- [ ] Add failing tests for strict schemas on both transports, unsupported structured-output capability, truncated finish reason, oversized context with output reservation, 429/backoff, redacted errors, missing usage, user cancellation and model/knowledge/decision/feedback cache changes.
- [ ] Run `python -m pytest tests/test_provider_contracts.py -q` before implementation.
- [ ] Add per-role model/capability/context configuration, server-only credentials and task/risk/scope/context/failure-aware routing. Use one shared schema source; pass actual schemas to Ollama. Budget whole messages, detect truncation, interrupt HTTP requests, preserve telemetry for failures and avoid cached rejection loops. Benchmark installed/configured models against observed RAM/VRAM capacity; do not assume a GPU.
- [ ] Populate a fixed 12-task evaluation manifest with expected behavior/evidence independent of fixture outputs. First run per-role schema/edit/review smoke tests using Tasks 3–5. Once Task 7 connects the pipeline, run `python scripts/evaluate_models.py --manifest tests/fixtures/model_eval/manifest.json`; require the design's success gate before recommending a default. Record all attempts and actual usage/latency. Use a modest embedding model only if resources permit and smoke-test dimensions/finite values; full retrieval measurement belongs to Task 9.
- [ ] Commit schemas, transport tests and initial smoke artifacts; full qualification is a Task 7/14 integration gate, not a circular prerequisite for implementing Task 7. If no candidate passes, report a specific provider/resource prerequisite; health alone is not model qualification.

## Task 7: generic durable workflow, bounded repair and first live slice

**Files:** Modify `backend/worker.py`, `backend/workflow.py`, `backend/main.py`, `run.py`; create `backend/worker_main.py`, `tests/test_live_pipeline_contract.py`, `tests/test_crash_recovery.py`, `tests/live/test_imported_workflows.py`, `tests/fixtures/repos/python_service/`, `tests/fixtures/repos/ts_service/`.

**Interfaces:** `worker.advance(work_item_id: str, owner: str) -> RunCheckpoint`; `workflow.publish_attempt(job_id: str, owner: str, candidate: CandidatePatch, checks: list[CheckResult], review: dict) -> dict`; `workflow.request_context(job_id: str, query: str, paths: list[str]) -> ContextBundle`.

- [ ] Add failing deterministic tests for real investigation/plan persistence; approve-first stopping before edits; automatic qualified preparation; decision resume; bad first patch repaired after actual check failure; two failed repairs stop; unsupported review claim remains inspectable; cancellation and lost lease suppress publication.
- [ ] Run `python -m pytest tests/test_live_pipeline_contract.py tests/test_crash_recovery.py -q` before implementation.
- [ ] Supervise a worker process separately from request handling. Heartbeat leases during slow calls. Persist investigation, acceptance criteria, affected areas, risk, plan and each stage/check/candidate as completed; distinguish a complete resumable checkpoint from interrupted work. Use Tasks 2–6 for materialization, edits, dependency setup, verification and review. Feed actual failing checks/review findings into bounded repairs, never weaken the test harness.
- [ ] Before editing newly affected files, retrieve their full required evidence and approved scoped constraints. Persist every model input fingerprint. Resume decisions without creating duplicate cards. Explicit revision requests preserve earlier patches/discussion but invalidate prior approval and rerun affected checks. Close abandoned runs and reconcile external containers on restart.
- [ ] Run imported Python and TS live tickets without `scenario` and without fixture provider: intake → real plan → patch → failing-before/passing-after regression → reviewable result. Crash during investigation, implementation, a check and just after publication; recover one visible result and no orphan running attempt. Commit M2 evidence; keep full-live status blocked if runtime/model gates cannot run.

## Task 8: accurate supported-language graph and inventory

**Files:** Modify `backend/repository.py`; create `backend/symbols.py`, `tests/test_symbol_resolution.py`, `tests/fixtures/repos/graph_cases/`; update repository API via lead.

**Interfaces:** `symbols.extract(snapshot: SourceSnapshot) -> dict`; `symbols.resolve(records: dict, configuration: dict) -> dict` returns nodes, exact/inferred edges and unresolved references, each linked to an `EvidenceRef`.

- [ ] Add independently reviewed expected-edge tests for Python src layout, relative package/submodule imports, aliases, JS/TS named imports/reexports, tsconfig paths, npm workspaces/package exports, emitted .js imports of .ts source, duplicate symbol names and unresolved dynamic imports.
- [ ] Run `python -m pytest tests/test_symbol_resolution.py -q` before implementation.
- [ ] Reuse AST/Tree-sitter extraction while preserving binding identities and scope; resolve only relationships supported by configuration/evidence. Inventory package/service boundaries, entries, supported FastAPI/Express-style route declarations, tests, configuration, schema/migration/CI/documentation records. Keep unsupported framework conventions explicit.
- [ ] Run exact edge assertions, including absence of false same-name call edges; assert every node/edge resolves to a real snapshot/range. Test deletions remove current edges while history remains available.
- [ ] Commit graph changes and capability documentation; never describe dynamic caller coverage as complete.

## Task 9: chunk retrieval, persistent embeddings, summaries and Q&A

**Files:** Modify `backend/retrieval.py`, `backend/db.py`, `scripts/evaluate_retrieval.py`; create `backend/summaries.py`, `tests/test_chunk_retrieval.py`, `tests/test_summaries.py`, `tests/live/test_retrieval_live.py`; update API through lead.

**Interfaces:** `retrieval.retrieve(repo_id: str, snapshot_id: str, query: str, budget_tokens: int, paths: list[str] | None = None) -> ContextBundle`; `summaries.build(repo_id: str, snapshot_id: str, cancel: CancellationToken) -> list[dict]`; `retrieval.answer(repo_id: str, snapshot_id: str, query: str, cancel: CancellationToken) -> dict`.

- [ ] Add failing tests for a late relevant function with an early decoy match, multiple nonadjacent regions in one file, semantic evidence beyond file 64, exact API/helper/config/tests ranges, no cross-repository vectors/citations, deleted evidence and invalid provider citations.
- [ ] Run `python -m pytest tests/test_chunk_retrieval.py tests/test_summaries.py -q` before implementation.
- [ ] Query persisted FTS chunks rather than scanning every file. Rank/deduplicate exact symbols/paths, text, semantic chunks and graph expansions within the model budget; preserve all selected ranges and reasons. Embed incrementally with repo/snapshot/chunk/model/dimension metadata. Do not use a first-64/prefix cutoff without an explicit documented capacity boundary.
- [ ] Add evidence-linked module/package/repository synthesis and multi-hop Q&A; require valid references for factual claims and distinguish inferred synthesis from parsed facts. Cache by dependency hashes and model/prompt version; invalidate on deletion/change. Structural fallback remains inspectable when a model is unavailable.
- [ ] Run fixed independently labelled Python/TS retrieval tasks in keyword, graph and semantic modes; report file/range recall, precision, context size, latency, live task/check success and reported usage. Run actual embedding/Q&A smoke tests separately. Commit measurements without promising improvement where none occurred.

## Task 10: versioned knowledge and meaningful decisions

**Files:** Create `backend/knowledge.py`, `tests/test_knowledge_versions.py`; modify `backend/main.py`, `backend/workflow.py`, `backend/retrieval.py`, database migration through lead.

**Interfaces:** `knowledge.revise(entry_id: str, changes: dict, expected_version: int) -> dict`; `knowledge.approve(entry_id: str, version: int, snapshot_id: str) -> dict`; `knowledge.for_paths(repo_id: str, snapshot_id: str, paths: list[str]) -> list[dict]`; `knowledge.promote(job_id: str, discussion_id: str) -> dict` creates an unapproved draft.

- [ ] Add failing tests: edit an approved entry and require renewed approval; immutable prior versions; invalid evidence range; unrelated README change preserves a rejection/scoped rule; changed cited content stales only dependent entries; additional affected paths bring their policy into the next prompt; overlapping contradictory approved constraints pause dependent work.
- [ ] Run `python -m pytest tests/test_knowledge_versions.py -q` before implementation.
- [ ] Store explicit categories, scopes, evidence/attribution, revision status and last validated snapshot; offer revalidation against current evidence. Detect deterministic contradictions and label model-inferred conflicts as hypotheses requiring resolution. Preserve rejected/snoozed knowledge. Only approved current versions affect work.
- [ ] Make decision selection persist its rationale/author/time and supersede dependent plans/checks if changed. Keep project-policy promotion separate from job revision. Verify team notes never appear in prompt fingerprints, embeddings, retrieval or summaries.
- [ ] Run new tests and existing note/knowledge/race regressions; commit with policy-influenced live follow-up evidence when provider available.

## Task 11: qualified signals, complete supported scans and durable monitoring

**Files:** Modify `backend/signals.py`, `backend/main.py`, `backend/workflow.py`; create `backend/monitoring.py`, `tests/test_signal_lifecycle.py`, `tests/test_monitoring_recovery.py`; update configuration docs and fixtures.

**Interfaces:** `signals.qualify(signal_id: str, snapshot_id: str, cancel: CancellationToken) -> dict`; `signals.scan(repo_id: str, snapshot_id: str, cursor: str | None = None) -> dict`; `monitoring.schedule_due(now: str) -> list[str]`; source identity and evidence fingerprint are separate persisted fields.

- [ ] Add failing tests for >40 dependencies, uv.lock/npm exact versions, OSV alias deduplication, timeout/partial scan, changed affected versions, unaffected versions, unchanged rejection, snooze expiry, applicable source-specific policy, duplicate simultaneous scans and interrupted polling recovery.
- [ ] Run `python -m pytest tests/test_signal_lifecycle.py tests/test_monitoring_recovery.py -q` before implementation.
- [ ] Persist original source/attribution/fetched time and content hash. Produce repository-specific rationale, evidence, measurable acceptance criteria, intended approach, priority and risk before preparation. Deterministic facts qualify where sufficient; model hypotheses remain unconfirmed. Implement Proposed and Awaiting approval meaningfully, plus reusable idempotent intake.
- [ ] Batch/cache/resume OSV across every supported resolved package, preserving full advisory provenance and scanned/unresolved/failure counts. Add a few evidence-based parser rules for swallowed errors and unresolved local imports. Support sourced framework/runtime notices and research hypotheses with a proposed benchmark; pasted research remains one-time intake, not an unrequested web crawler.
- [ ] Schedule remote refresh through Task 2, reindex/invalidate, then scan; show last attempt/success/error/next run and monitoring stopped state. Keep external fetches outside global application locks. Run restart/dedup tests and one real OSV query plus real sandbox research benchmark. Commit; mark live failures honestly.

## Task 12: exact approval and recoverable managed application

**Files:** Create `backend/integration.py`, `tests/test_managed_integration.py`; modify `backend/main.py`, `backend/workflow.py`, patch/changed-file exports.

**Interfaces:** `integration.apply(job_id: str, revision_id: str, expected_input_fingerprint: str) -> dict`; `integration.reconcile(operation_id: str) -> dict`; `integration.export_files(revision_id: str) -> bytes` includes a change manifest and patch for deleted/renamed paths.

- [ ] Add failing tests for approved imported-repository application, source checkout unchanged, stale base/knowledge/check environment, conflicting jobs, duplicate apply, deleted files, crash after patch application and crash after Git commit before DB completion.
- [ ] Run `python -m pytest tests/test_managed_integration.py -q` before implementation.
- [ ] Generalize the existing managed sample integration to supported imported snapshots. Persist an application operation before filesystem changes; commit with a revision marker and reconcile on restart. Check exact revision/patch/input fingerprints, actual base and clean target before apply; never silently overwrite overlap.
- [ ] Keep approval distinct from Completed. Export is not application. Complete only when the managed commit and its contents are verified; expose branch/commit and limitations. Return a specific reprepare/conflict action for changed bases.
- [ ] Run integration tests against real Git, validate exported patches on a fresh base, and verify no source checkout modification. Commit.

## Task 13: complete the live developer UX

**Files:** Modify `frontend/src/main.tsx`, `frontend/src/style.css`, `frontend/package.json`; extract only needed `frontend/src/api.ts`, `frontend/src/pages/Jobs.tsx`, `frontend/src/pages/Repository.tsx`, `frontend/src/pages/Knowledge.tsx`, `frontend/src/pages/Settings.tsx`, `frontend/src/components/DiffViewer.tsx`; create `frontend/tests/imported-workflows.spec.ts`, `frontend/tests/navigation.spec.ts`, `frontend/playwright.config.ts`.

**Interfaces:** Consume operation progress, typed plans/context/checks/candidates, scan reports and capability health from Tasks 1–12. Add list pagination/summary endpoints through lead; do not poll full source contents repeatedly.

- [ ] Add failing browser assertions for stable/deep routes and reload, status/source/type/priority/risk filters, independent repository/job searches, operation retry/progress, real plan/check output, failed candidate inspection, exact cited-line highlighting, repository switch during a pending Q&A and stale snapshot warnings.
- [ ] Run `npm run test:browser -- --project=chromium` after wiring a dedicated test backend and fixture database; existing sample suite remains separate.
- [ ] Implement readable syntax-aware unified/split diffs with explicit added/deleted/renamed files and complete navigation. If using a diff/highlighting package, select one maintained package after checking current official support; do not build a custom syntax parser. Show preparation/patch/application distinctions and check-derived confidence/risk explanations without percentages.
- [ ] Finish Repository/Knowledge/Settings views: paginated tree and real impact paths, disclosed graph truncation, source/scan gaps, knowledge revision/approval/conflict actions, provider qualification and sandbox blockers with recovery instructions. Preserve themes, keyboard focus, error/loading/empty states and the future-preview label.
- [ ] Run sample plus imported Python/TS browser flows, downloads and live-mode assertions; inspect desktop, laptop and 390px narrow layouts in both themes, keyboard navigation and console/network errors. Run `npm run build`; commit screenshots/results tied to tested backend evidence.

## Task 14: reproducible startup, independent review and final acceptance

**Files:** Update `run.py`, `README.md`, `.env.example`, `requirements.lock`, `frontend/package-lock.json`, `docs/ARCHITECTURE.md`, `docs/CONFIGURATION.md`, `docs/VERIFICATION.md`; create `scripts/doctor.py`, `scripts/acceptance.py`, `docs/ACCEPTANCE_MATRIX.md`, `.github/workflows/test.yml`, final review reports.

**Interfaces:** `python scripts/doctor.py` emits redacted machine-readable capability status; `python scripts/acceptance.py --tier deterministic|sandbox|live|all` emits a report with pass/fail/blocked and artifact links, nonzero for failed or blocked required gates. Do not make model/daemon availability mandatory for the fast deterministic CI job.

- [ ] Add setup tests for a clean checkout, existing database migration/backup/restore, worker/model process cleanup, bounded artifact retention and preservation of revision evidence. Pin runtime/image/artifact identity in reports; clean up only verified owned temporary paths.
- [ ] Run the full deterministic backend gate, production build and browser suites. Run sandbox and live tiers separately, with no fixture response allowed to count toward live success. Exercise all original 14 journeys and the 12-task model qualification corpus; publish every required blocker.
- [ ] Commission functional, repository/AI, security/reliability and rendered-UX reviews with exact test commands, independent repo fixtures and failure-injection points. Require severity, reproduction, affected files and recommended fixes. Review source and behavior, not just this plan or a summary.
- [ ] Fix substantive findings and rerun affected checks. Have fresh reviewers run final acceptance on the integrated branch; repeat only for new changes/failures or unresolved material concerns. Require no known critical/major defect in supported scope.
- [ ] Publish capability/status matrix and demo walkthrough covering both sample and live imports. Leave the app running if supported, report exact URL/start command and remaining external setup. Commit final evidence; do not call the full live release complete while sandbox/model gates are blocked.

## Test tiers and release accounting

| Tier | May use fixtures? | What it proves |
|---|---|---|
| Unit/contract | Yes: deterministic transports, malicious archives, synthetic process failures | State, schemas, boundaries, cache/input handling and algorithms |
| Sample integration | Explicit fixture provider only | Existing reproducible demo remains working with real Git and reviewed fixed checks |
| Sandbox integration | Fixed repository/test inputs; real container/runtime | Actual execution/environment/network/resource/cancellation boundaries |
| Live model | No model-response fixtures or cached prior successes | Generic evidence/decision/knowledge → edit → executed checks → review across independent repos |
| Browser | Fixture and live runs reported separately | Actual user journeys and rendered behavior, cross-checked against backend artifacts |

Do not rerun all expensive suites after harmless documentation changes. Every logic/security change gets its focused regression, each milestone gets its relevant integration gate, and the final integrated branch gets the full required gate.

## Planning audit and handoff

- [x] Inspect current source and retained evidence, rather than infer completeness from passing fixture tests.
- [x] Obtain independent worker/provider/execution and repository/retrieval/UX gap reviews.
- [x] Select incremental completion of the existing stack; define external gates and supported profiles.
- [x] Obtain a fresh builder check of the design; no consequential unresolved product question remained.
- [x] Map every original acceptance category to implementation and verification tasks.
- [x] Self-review task interfaces, dependency ordering, failure cases and scope; keep full model qualification after the generic pipeline exists.
- [ ] Execute Tasks 1–14 in a subsequent implementation turn; this checkbox intentionally remains open.

Original acceptance mapping: 1→2/13/14; 2→8/9; 3→1/7/11; 4→3/5/7/13; 5→7/10; 6→7/10/12; 7→11; 8→2/8/9/10; 9→1/4/6/7/12; 10→3/12; 11→2/3/4/6; 12→6/9/14; 13→5/10/11; 14→6/7/14.

The first implementation action is a read-only capability check and baseline verification, followed by Task 1's migration/state contract and parallel snapshot, sandbox and provider work. Existing autonomy/delegation preferences are preserved; routine framework, naming and colour questions are unnecessary.
