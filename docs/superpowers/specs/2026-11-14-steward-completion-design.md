# Steward: complete the live local product

Status: planning deliverable; no implementation authorized by this document alone. This design interprets the user's request using the original detailed product requirements and their instruction to make routine decisions without questions.

## Outcome and scope

A developer can import a supported real repository, understand it through source-linked evidence, submit maintenance work, resolve decisions, and receive a real patch with executed verification. Revision requests, knowledge changes, refresh, cancellation and restart must work throughout that live flow. Approval is tied to one exact revision; completion requires actual application to a Steward-managed branch or independently verified application.

Keep React/TypeScript/Vite, FastAPI/Python, SQLite, a lightweight durable queue, and loopback single-workspace operation. Keep the existing sample as an explicitly labelled demonstration and regression suite. Preserve existing databases, discussions, snapshots and revision history through migrations. Demo membership remains explicit; production authentication, cloud hosting, remote pushes, automatic main-branch merges, and mandatory paid services remain outside the original scope.

Initial fully verified execution profiles are Python unittest/pytest with pinned requirements or uv.lock, and JavaScript/TypeScript with npm package-lock and Node test/Vitest/Jest commands. Other languages and package managers remain importable for inventory/search; unsupported execution or unresolved dependencies must be visible, never silently approximated. Public HTTPS Git and ZIP remain supported source forms. Broad autonomous web research and generated UI previews remain optional, as in the original request.

## Current evidence and confirmed gaps

Baseline is commit `5072cf3`. The preceding build recorded 72 backend tests, 15 browser checks, a production build and actual queued-work restart recovery. Those results establish the existing prototype, not a live arbitrary-repository release. See ../../VERIFICATION.md and ../../LIVE_PROVIDER.json.

| Area | What exists | Work needed |
|---|---|---|
| Execution | Reviewed sample tests and benchmark actually run | No sandbox adapter exists; imported/live edits always get an unavailable check |
| Live inference | OpenAI-compatible/Ollama transports; real local edit generation | Bundled 1.5B review failed acceptance; routing is shallow; no repair cycle or provider cancellation; retries can reuse rejected cached responses |
| Working copy | Real isolated Git diff | Reconstructed from indexed text only; full-file replacements from partial evidence; no delete/rename operation |
| State and persistence | SQLite transactions, leases and state guards | Several multi-record actions are separate transactions; slow calls can outlive leases; Git application and DB completion need crash reconciliation |
| Repository understanding | Python AST/Tree-sitter, file-level imports and inferred calls | Common alias/workspace/src-layout resolution missing; symbols, packages, routes, schemas, CI and test relationships need richer records; summaries remain fixed structural text |
| Retrieval | Keyword ranking, graph expansion, optional embedding transport | FTS is populated but not queried; one snippet per file; embeddings cover prefixes of at most 64 files; no full persistent chunk index |
| Freshness and import | Safe ZIP and GitHub commit archive import | Refresh reindexes the stored directory, not upstream Git; ingest blocks requests; snapshot identity excludes some execution-relevant content |
| Knowledge | Editable scoped entries and approval | Version history/evidence validation/conflict resolution and selective invalidation need completion; edited approved content must require explicit renewed approval |
| Signals | Manual/research intake, bounded OSV and one analysis rule | Every submission is immediately called qualified; missing sourced investigation/plan/risk; first-40 dependency limit; no durable snooze or advisory lifecycle |
| Review UX | Functional sample journeys and real diffs | Needs live failure/candidate visibility, stable routes, complete filters, readable syntax-aware diff and better large-repository navigation |
| Application | Sample managed integration and export | Imported repositories cannot complete through managed application; lack application reconciliation and explicit removed-file export manifest |

These are source-inspection findings, not fresh runtime test results.

## Approaches and decision

1. **Extend the current application, with a safe execution boundary and one live vertical slice first. Recommended and selected for this plan.** Reuses the existing UI, persistence and tests; makes each missing capability independently reviewable.
2. Replace it with an agent framework and separate queue/vector/database services. Would add migration and integration work without resolving sandbox availability or model quality; rejected.
3. Keep analysis and patch export only. Useful fallback, but would not meet the requested executed verification and complete maintenance loop; retained only as an explicit degraded mode.

Split files only at new responsibilities or when touching unwieldy code. Do not undertake an unrelated framework migration or cosmetic rewrite.

## Execution and dependency boundary

Add a Docker-compatible Linux-container adapter using an already authorized local daemon. On Windows this requires a usable Linux container runtime. Prefer rootless operation where already available; do not alter host privileges, ACLs, daemon settings or security controls to obtain access. The existing permission denial is a real release prerequisite, not an implementation detail that code can bypass.

The trusted controller alone talks to the daemon. Job containers receive a disposable copy of the sanitized source snapshot, never the host workspace, home directory, provider secrets or daemon socket. Use non-root users, a read-only root filesystem, dropped capabilities, no-new-privileges, default seccomp, bounded writable scratch space, CPU/memory/process limits and an external wall-clock watchdog. Checks run with networking disabled. Default limits: 2 CPUs, 2 GiB RAM, 256 processes, 5 minutes/check, 10 minutes/dependency preparation, and 30 minutes/job; server configuration may change bounded values. Store at most 1 MiB per command, render a bounded excerpt, record truncation.

Dependency acquisition is distinct from execution. A trusted fetch broker downloads exact lockfile-resolved artifacts from configured public registries, validates destinations/redirects, records hashes and supplies a read-only artifact cache. Untrusted lifecycle scripts/build steps run only inside an offline dependency container. No repository Dockerfile, package-manager plugin, install hook, custom index or model command gains host privileges. Unsupported Git/path dependencies and missing artifacts become specific blockers. The first release may refuse unsupported dependency forms rather than installing them on the host.

The runtime must pass active probes for network denial, absent host canary/credentials/socket, non-root identity, resource limits, and process-tree termination before advertising execution as available. Container controls reduce risk; they do not justify a hostile multi-tenant hosting claim. Docker documents the required [run controls](https://docs.docker.com/reference/cli/docker/container/run/), [resource limits](https://docs.docker.com/engine/containers/resource_constraints/) and [network isolation](https://docs.docker.com/engine/network/drivers/none/).

## Durable workflow and safe edits

Use the existing states, centralized in one transition service. Persist signals, plans, decisions, stage attempts, check plans and idempotency keys. Preparation approval binds the current proposed approach. Patch approval binds the revision, base snapshot, patch hash, applicable approved knowledge/decisions, and verification environment. Inputs changing during work supersede that attempt and invalidate approval.

Use a supervised worker process and persisted work items for ingestion, refresh, scans and maintenance. Worker ownership is conditional and heartbeated independently of slow inference/checks. A checkpoint records the stage, input fingerprint, attempts and outputs. All multi-record state transitions publish transactionally. Exactly-once externally visible results are achieved through idempotency and reconciliation, not a claim that every external action runs once.

Separate immutable sanitized source storage from searchable text. Keep byte hashes, paths, executable modes and non-secret assets needed for builds; expose all exclusions and missing dependencies. The index can omit binary content while the sandbox receives permitted assets. Reject unsafe links and paths and surface the resulting gap. Never present a partial reconstruction as a complete source checkout.

Use structured create/update/delete/rename edits with expected original hashes and explicit preconditions. Updates use exact anchored replacements; whole-file replacement is allowed only after obtaining the complete file. No fuzzy patch application. Persist previous versions and verify declared changes against the actual Git diff, including deletions, mode changes and unchanged byte preservation.

Preserve baseline results. Reproduce a bug with a new failing test where feasible, execute it against the base, then against the patch; detect test removals/skips or harness weakening. Features have explicit behavior checks. Performance uses identical workloads and environment before/after, stores raw samples and variability. Security work rechecks the actual resolved dependency and advisory after the patch without inferring exploitability from a version match. A model review consumes the patch plus evidence and executed checks and remains additional evidence.

## Model capability and bounded repair

Keep both existing open-weight transports. Add explicit per-role model configuration, capabilities, context/output limits, embedding configuration and safe health/setup diagnostics. Choose models by measured hardware fit and an application-specific evaluation, not by promising a particular model size will solve quality. Never download large weights automatically or add a paid dependency.

Use strict shared schemas for investigation, planning, context requests, decisions, edits and review findings. Ollama supports a schema in its `format` field, not just JSON mode; use that capability and local validation. See [structured outputs](https://docs.ollama.com/capabilities/structured-outputs). A review finding must identify a concrete mismatch with a source/patch/check reference; an unsupported claim is uncertainty and may trigger one adjudication, never a silent override.

Implement bounded investigate → plan → edit → verify → review → repair. Default maximum: 12 provider calls, 30,000 output tokens, two additional evidence rounds and two repair cycles within the job deadline. Context budgeting reserves output space; schema errors, 429s and transient errors have bounded backoff. Cancellation interrupts provider requests and containers. Persist actual attempts, model IDs, latency and reported usage, including failed attempts; unknown usage stays null.

Cache inputs include repository/snapshot, exact evidence, prompt/schema version, provider/model identity, approved knowledge versions, plan, selected decisions, previous revision and repair feedback. Failed review and revision requests cannot replay the same stale successful-stage cache without the changed input. A separate review call is labelled as such; the UI must not imply a different model when the same model fills both roles.

## Repository intelligence and maintenance sources

Store symbols and chunk records with stable IDs, file hashes and actual line/byte ranges. Resolve Python package/src-layout/relative imports and JS/TS aliases, named imports, re-exports and package workspaces. Resolve statically supported references/callers; mark heuristic relationships inferred and unresolved references unknown. Inventory services/packages, entry points, common supported routes, tests, dependencies, configuration, schemas, migrations, CI and documentation. Generic unsupported framework behavior remains a stated gap.

Query SQLite FTS over chunks, combine exact symbols/paths and optional semantic rank, then expand graph neighbors to related implementation/tests/configuration/approved decisions. Embed all eligible chunks incrementally with model/dimension/hash metadata, not just file prefixes; embed query and documents with the same model as described in [Ollama embeddings](https://docs.ollama.com/capabilities/embeddings). Store historical evidence used by a job; delete obsolete current-index records and invalidate dependent summaries/jobs.

Build source-linked symbol/module, package/service and repository summaries with deterministic facts separated from model synthesis. Q&A can request additional evidence, trace cross-module behavior, cite real ranges and admit gaps. Inventory coverage remains a count; no invented understanding percentage.

Knowledge stores immutable revisions, evidence links, scope, approval and validation snapshot. A changed approved note becomes a draft until explicitly approved. Conflicts in overlapping approved rules block only dependent work and produce a decision card. Team notes remain completely outside retrieval, embeddings and model payloads. Job revisions become project policy only by explicit promotion and approval.

Signals preserve source identity and evidence version separately. Triage establishes repository-specific applicability, acceptance criteria, intended approach, priority and change risk. Automatic preparation starts only qualified work; approve-first presents that plan first. Durable rejection/snooze survives unrelated repository changes; a changed advisory/package version or relevant evidence can reopen investigation. Scheduled Git refresh fetches upstream, while ZIP refresh requires a replacement archive. Persist last success, error, cursor and next run. Framework notices use attributed advisory intake; research uses source snapshots and measurable hypotheses, not automatic claims.

## UX and release criteria

Preserve the six navigation areas and themes. Add stable job/repository links, all requested filters, visible ingest stages and monitoring health, chunk/source navigation, real impact paths, full check details and failed-candidate inspection. Show separate preparation approval, revision approval and managed application. Candidate or export-only states never imply verified completion. Show removed files in downloads through a patch plus manifest. Reserved UI preview remains explicitly unavailable.

Release requires all original 14 acceptance journeys mapped to evidence, and five generic live journeys on both a Python and a TypeScript repository: bug/regression, decision-backed feature, revision after approval, knowledge-informed change, and cancellation/restart. Neither repository may use fixture-provider scenario branches. Add live dependency remediation and benchmark/research checks in the supported profiles. Missing provider/sandbox access is recorded as blocked and prevents the full-live release label.

Model qualification uses a fixed 12-task corpus separate from fixture responses: at least 10 tasks complete within the declared budget, all five mandatory live journeys pass, and seeded unsafe/incorrect patches are not falsely presented as verified safe. Report every attempt, failure, token count when available and latency. This threshold is a proposed release gate, not a guarantee of success on future tickets. Failures must leave recoverable, reviewable records.

Independent functional, repository/AI, security/reliability and rendered-UX reviewers reproduce behavior. Fix material findings, recheck affected flows, then run fresh full acceptance. No replacement of execution with model opinion, fixture success or screenshots.

## Ownership and sequencing

Lead owns storage/state contracts, integration and acceptance. Parallel owners: (1) sandbox/dependencies/verification, (2) repository snapshots/graph/retrieval/knowledge, (3) provider/worker workflows, with frontend work assigned when a slot frees. Agree contracts before concurrent edits; the lead owns `main.py` and `db.py`, and agents do not compete for shared files. The critical path is usable sandbox + qualified provider + complete snapshot → one live vertical slice → remaining workflows → final acceptance.

## Explicit external gates

- A permitted, usable Linux container daemon is required for live execution. Do not change security controls to obtain one.
- A sufficiently capable open-weight model and enough permitted resources are required for the live quality gate. An existing authorized endpoint is acceptable; a paid account is not assumed.
- Registry, public Git and advisory connectivity are needed for their live checks. Pinned local artifacts enable deterministic offline regression tests but do not count as live integration verification.

The plan can implement and test the adapters under deterministic transports while these gates are unavailable. It must keep the full-live release blocked until actual integration checks pass.
