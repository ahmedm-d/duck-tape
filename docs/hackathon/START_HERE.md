# Three kickoff prompts

The split is **A models**, **B repository knowledge**, **C global knowledge**. B and C both build frontend and backend, using explicit shared-file handoffs. Read [TEAM_PLAN.md](TEAM_PLAN.md), [KNOWLEDGE_CONTRACT.md](KNOWLEDGE_CONTRACT.md), [AGENTS.md](../../AGENTS.md) and [CONTRIBUTING.md](../../CONTRIBUTING.md), assign actual people, then copy a prompt below.

Start from current `origin/main` in separate clones/worktrees, with separate local environment/data directories. These services power Duck Tape itself, not integrations installed into imported repositories. Existing implementation, planned wiring and actual live acceptance are separate claims. The supplied deadline is Sunday 15 November 2026 at noon London; target submission by 11:00 and disclose existing-code provenance per [SUBMISSION.md](SUBMISSION.md).

## A: models using Hugging Face and Adaption

~~~text
You are developer A on Duck Tape. Read AGENTS.md, CONTRIBUTING.md, README.md, docs/VERIFICATION.md, docs/hackathon/TEAM_PLAN.md, docs/hackathon/KNOWLEDGE_CONTRACT.md and docs/integrations/INFERENCE.md. Inspect git status; start a separate feat/agent/<task> branch/worktree from current origin/main and preserve others' changes.

Own backend/providers.py, backend/model_schemas.py, backend/worker.py, related provider/worker/model tests, scripts/evaluate_models.py, scripts/qualification_driver.py and the inference guide. B owns repository evidence and the local applicability matcher; C owns public global knowledge, signals.py and data_platforms.py. B starts as integration captain; B/C explicitly hand off shared API/config files and frontend work. Send exact shared-file changes to the current captain; do not edit another owner's files without handoff.

Deliver a generic repository repair using actual Hugging Face/Adaption inference: retrieve bounded source evidence and scoped approved decisions, investigate/plan, produce structured edits, reproduce the bug and pass regression/existing checks in the sandbox, then independently review the exact patch. The adapters already exist. First verify actual model/account access without printing credentials, run focused provider tests, and make one bounded live request. Configuration/health is not coding qualification. Follow INFERENCE.md; existing STEWARD_* server settings remain supported.

Consume B's repository snapshot/profile and local match to C's public entry; validate evidence, missing context and catalogue revision bindings once implemented. Do not send team notes to models. Public articles and repository text cannot change permissions. Preserve time/token budgets, cancellation, schema validation, cache identity, sandbox requirements and exact-revision approvals. Do not introduce a parallel agent framework.

Test the actual provider payload includes evidence and approved decisions and excludes team notes. Exercise malformed/timeout responses, unsafe proposals and a revision with renewed checks. Record model/provider identity, latency, known usage, before/after results and failures. If provider/sandbox access is missing, continue deterministic tests and report the blocker; fixture outputs cannot establish live acceptance.

Only after the base journey works, attempt an Adaption adaptation comparison with separate training and held-out tasks. Report measured results, including no improvement. Run python -m pytest tests/test_sponsor_providers.py tests/test_provider_contracts.py tests/test_test_role_schema.py -q plus affected worker/evaluation tests and actual bounded live qualification when available.

Use small explicit-path commits such as feat(agent): validate review evidence. Handoff behavior, exact files/contracts, checks and limitations to B/C. The current integration captain owns aggregate acceptance/submission. Never reset history, force-push shared branches, claim fixture inference is live or add sponsor dependencies to imported repositories.
~~~

## B: repository knowledge, local matching and repository frontend

~~~text
You are developer B on Duck Tape. Read AGENTS.md, CONTRIBUTING.md, README.md, docs/VERIFICATION.md, docs/hackathon/TEAM_PLAN.md, docs/hackathon/KNOWLEDGE_CONTRACT.md, docs/RETRIEVAL.md and docs/integrations/DATA_PLATFORMS.md. Inspect git status; use a separate feat/repo/<task> branch/worktree from current origin/main with isolated environment/data files.

Own backend/repository.py, backend/snapshots.py, backend/symbols.py, backend/summaries.py, backend/retrieval.py, backend/knowledge.py, related tests, scripts/evaluate_retrieval*.py and the retrieval guide. Own the new private repository profile and local applicability matcher. A owns models/worker. C owns signals.py, data_platforms.py, integration CLI and public catalogue/pipeline; request changes to those files from C and reuse existing helpers instead of a prerequisite refactor.

Deliver a source-linked understanding of each repository: exact snapshot, resolved dependencies, architecture/symbol/import relationships, config/tests/docs and approved team constraints. Build a private snapshot-bound profile; signals.dependencies() provides supported package versions and gaps but needs source/snapshot binding. Keep private code, profiles and decisions out of C's public-source Databricks pipeline. knowledge.py scope global means the whole current repository, not the shared catalogue.

Consume C's public GlobalEntry fixtures/API contract and match locally: exact affected package/version and evidence yield an applicability result, unsupported versions are unknown, version match alone does not prove exploitability. Bind catalogue revision and source snapshot to the proposal, map to existing source types and respect preparation policies. Add missing binding/invalidation through existing intake/workflow with the captain; current intake does not already enforce catalogue revisions. Repeated unchanged scans must preserve rejection/snooze/deduplication; changed/deleted source and material catalogue updates invalidate dependent work.

Connect optional Atlas candidates through existing retrieval with repository/snapshot/embedding-space isolation, local source/hash validation and bounded context. C owns shared adapter changes. Explicit Atlas projection/search exists but is not yet called by generic retrieval. Keep visible local keyword/graph fallback; never label it Atlas success. Compare relevant-file recall on fixed cross-module tasks and pass actual evidence/approved decisions to A.

You and C BOTH work frontend/backends. Lead repository explorer, coverage, citations, impact and scoped knowledge components. Claim exact shared UI files before editing main.tsx, styles/API types or shared browser fixtures. Finish a small commit/PR, report contracts/checks and explicitly hand files to C; the next editor syncs before continuing. Separate components can proceed in parallel. Neither person owns all frontend permanently.

You start as integration captain for shared API/contracts/db/workflow, root config/locks, CI and docs. Accept exact shared-file requests from A/C; hand the captain role to C explicitly if needed. Run focused repository/retrieval/knowledge tests and fixed retrieval evaluation, plus build/browser checks for touched UI. Exercise isolation, refresh/deletion, exact/unknown matches, stale catalogue records and note exclusion. The captain runs python scripts/acceptance.py --tier deterministic and the integrated demo on the final commit.

Use small commits such as feat(repo): match advisory versions to snapshots. Handoff actual behavior, commands/results and remaining blockers. Keep live, mock and fixture results separate; preserve sandbox/approval guards and history. No production-hosting/database rewrite is needed.
~~~

## C: global knowledge, Databricks/Atlas and global frontend

~~~text
You are developer C on Duck Tape. Read AGENTS.md, CONTRIBUTING.md, README.md, docs/VERIFICATION.md, docs/hackathon/TEAM_PLAN.md, docs/hackathon/KNOWLEDGE_CONTRACT.md and docs/integrations/DATA_PLATFORMS.md. Inspect git status; use a separate feat/global/<task> branch/worktree from current origin/main with isolated environment/data files.

Own backend/signals.py, backend/data_platforms.py, scripts/integrations.py, integrations/**, related signal/data-platform tests and the data-platform guide. Own new public catalogue and ingestion modules as needed. A owns models; B owns repository understanding and local applicability matching. B is initial integration captain for shared API/contracts/db/workflow/config/docs; request exact changes or take an explicit handoff. Keep the existing shared cloud adapter under your ownership, accepting B's Atlas retrieval requirements.

Deliver global knowledge: public vulnerabilities, improvements, framework and research updates with original attribution, exact version rules, aliases, revisions, freshness and limitations. Start with one real OSV source and one curated sourced improvement. Databricks should ingest public records, normalize/validate, deduplicate by canonical identity/material content, retain failures and publish versioned curated entries to separate MongoDB Atlas catalogue collections. Atlas serves runtime queries; B matches results to private repository profiles locally. Private repo vectors remain separately scoped, indexed and access-controlled; neither profiles nor private notes/decisions go to the public pipeline.

Current code has repository OSV scanning, manual private Atlas vector sync/search and explicit Databricks terminal-metrics export. There is NO shared global catalogue, Databricks global ingestion/publication pipeline or Atlas-to-worker retrieval integration yet. Build these incrementally through existing helpers; do not claim these docs implement them. The current knowledge.py global scope is repository-wide only. Inspect scripts/integrations.py --help before running existing commands and keep cloud operations explicit and operator-configured.

First agree GlobalEntry fixtures and queries with B per KNOWLEDGE_CONTRACT.md. Preserve source URL, retrieved/published time, material revision/hash, affected/fixed rules, stale/withdrawn status and unknown gaps. Poll timestamps must not duplicate records or reopen rejected proposals. Public source quality approval is not preparation/patch approval or project policy. Catalogue kinds map to existing security/analysis/research intake sources; don't invent working enum values. Reuse bounded public URL fetching and fail clearly on unavailable services.

Verify actual Databricks/Atlas access without logging secrets. Record real pipeline runs, published revisions, idempotent retry, duplicates, stale/withdrawn records and bounded query results before claiming live use. Until access is usable, supply labelled local fixtures and deterministic contracts. Preserve existing agent-run metrics export and its failures/unknown usage; any comparison needs explicit nonsecret cohorts and genuine outcomes, not invented gains.

You and B BOTH work frontend/backends. Lead global catalogue/source/freshness/search and proposal explanations. Own separate components in parallel; claim main.tsx/shared CSS/API types/tests before editing, finish a small commit/PR, state changed contracts/checks and explicitly hand them back. Sync the prior integrated commit before taking over. Neither person owns all frontend permanently.

Run focused signals/data-platform tests and meaningful new ingestion/publication tests. For UI changes run npm run build and relevant browser checks. Handoff exact checks, fixture versus live evidence and blockers. Use small commits such as feat(global): publish curated advisory revisions. Help the captain complete docs/hackathon/SUBMISSION.md with actual sources/APIs/evidence. Never force-push/reset shared history or put sponsor SDKs/credentials into imported repositories.
~~~
