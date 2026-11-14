# Three-person hackathon plan

**A builds the models. B understands each repository. C maintains what Duck Tape knows about the wider software ecosystem. B and C both build their frontend and backend, handing shared screens back and forth.**

The demo: a sourced vulnerability or improvement enters the global catalogue, matches evidence in a repository, becomes a maintenance proposal, and produces a checked patch for developer review. Target the Autonomous Agents track, with the Adaption additional prize only where actual usage qualifies.

## Where MongoDB and Databricks fit

All four services power Duck Tape itself. Imported repositories provide evidence and receive reviewed patches; they do not receive sponsor SDKs, credentials or setup flows.

| Technology | Product responsibility | Owner | Current implementation / next work |
| --- | --- | --- | --- |
| Hugging Face + Adaption | Reason about evidence, plan, edit and review; evaluate model behavior and any adaptation. | A | Provider adapters are wired into the worker. Live qualification and adaptation experiments still need actual evidence. |
| MongoDB Atlas: private repository memory | Serve snapshot-scoped semantic candidates, resolved back to local source evidence. | B consumes it; C owns the shared adapter. | Explicit vector sync/search exists. B must connect candidates to retrieval with validation and visible fallback. |
| MongoDB Atlas: public global knowledge | Serve curated vulnerabilities, improvements, framework and research updates for runtime lookup/search. | C | Planned separate catalogue collections/indexes and access policy; not implemented yet. |
| Databricks Data Intelligence Platform | Ingest public sources, normalize, deduplicate, track freshness and publish curated catalogue revisions to Atlas. Also evaluate agent-run outcomes. | C | Terminal-run metrics export/query exists. The global ingestion/curation/publication pipeline is planned. |
| SQLite | Keep local workflow transactions, leases, approvals, repository knowledge and source evidence authoritative. | Current integration captain | Already implemented. No database rewrite is needed for this split. |

**Databricks prepares knowledge; Atlas serves it; B establishes whether it applies here; A acts on the evidence.** An unavailable cloud account must remain an explicit blocker, with labelled local fixtures for development. A local fixture is not evidence that Atlas or Databricks ran.

The proposed global pipeline is: public OSV/curated source records -> Databricks ingestion and normalization -> versioned curated catalogue -> Atlas online lookup -> B's local repository matcher -> existing signal/job intake -> A's worker -> checked diff and developer review. Public catalogue processing never needs private source code. Agent evaluation is a separate Databricks dataset of allowlisted terminal metrics.

## A: models and agent execution

Own the Hugging Face/Adaption provider behavior, routing, schema validation, bounded investigation/planning/edit/review loop and model evaluation. Consume B's bounded repository evidence and C's public source records through B's applicability match. Keep approved repository decisions in scope and team notes out of prompts.

**Deliver first:** one generic cross-module repair using actual inference and sandbox execution, with failing-before/passing-after regression evidence, a separate review and exact-revision approval. Record actual model identity, latency, known usage and failed cases. Attempt an Adaption adaptation comparison only after this works, with held-out tasks and no promised improvement.

Owned files: `backend/providers.py`, `backend/model_schemas.py`, `backend/worker.py`, model/provider/worker tests, `scripts/evaluate_models.py`, `scripts/qualification_driver.py`, and [inference guide](../integrations/INFERENCE.md).

## B: repository knowledge and applicability

Own imports/snapshots, inventory, symbols/import graph, architecture summaries, approved team constraints, source citations and hybrid retrieval. Build the private snapshot-bound repository profile, including exact lockfile versions and explicit gaps. Match public catalogue records to that profile locally; distinguish an affected-version match from proven exploitability and an improvement hypothesis from a measured improvement.

**Deliver first:** trace an endpoint through its helper/config/tests; match one C-supplied advisory to an exact dependency with citations; feed the bounded evidence and approved decisions to A. Refresh modifications/deletions without retaining stale evidence. Add opt-in Atlas candidates through the existing retrieval boundary and compare relevant-file recall with the local fallback.

Owned files: `backend/repository.py`, `backend/snapshots.py`, `backend/symbols.py`, `backend/summaries.py`, `backend/retrieval.py`, `backend/knowledge.py`, related tests, `scripts/evaluate_retrieval*.py`, [retrieval guide](../RETRIEVAL.md). The repository profile/matcher are new work owned by B; use small modules only where needed. Reuse `signals.dependencies()` and request changes to that module or Atlas adapters from C.

Frontend: repository explorer, coverage, graph/impact, citations and repository knowledge editing. B is the initial integration captain, not a fourth full-time workstream.

## C: global knowledge and data pipeline

Own public vulnerabilities, improvements and framework/research updates, original attribution, aliases, affected versions, deduplication, revision history, freshness and failed ingestion visibility. Build the Databricks public-source pipeline and publish validated catalogue records to separate Atlas collections for runtime queries. Preserve the existing explicit agent metrics export as the evaluation path.

**Deliver first:** one real public OSV source plus one curated sourced improvement, normalized into the agreed record shape. Repeated ingestion must not duplicate unchanged findings. B must be able to query a specific ecosystem/package/version and receive evidence or an explicit unknown. Support a labelled local fixture until credentials/resources are usable; record live Databricks processing and Atlas publication before claiming them.

Owned files: `backend/signals.py`, `backend/data_platforms.py`, `scripts/integrations.py`, `integrations/**`, related tests, [data-platform guide](../integrations/DATA_PLATFORMS.md), and the planned global catalogue/ingestion modules. C owns the existing shared cloud adapter for both knowledge stores; B supplies its repository retrieval requirements. No prerequisite refactor of `signals.py` is required.

Frontend: global catalogue browsing/search, source attribution, freshness, applicability explanations and proposal intake. Keep source curation separate from project policy approval.

## B/C frontend and integration handoff

Both B and C work across frontend and backend. Independent components can be developed concurrently. Shared navigation, Jobs/Settings, `frontend/src/main.tsx`, shared styles/API types and browser fixtures have **one named editor at a time**, with no permanent all-frontend owner.

1. Claim exact shared files in the PR/team handoff before editing. State the route/response/component contract and who is waiting.
2. Finish a small reviewable commit/PR; report changed behavior, exact files, tests and remaining limits.
3. Release the files explicitly. The next editor syncs the integrated commit before continuing.
4. B starts as integration captain for `backend/main.py`, `backend/contracts.py`, `backend/db.py`, `backend/workflow.py`, shared config/locks, CI and docs. B/C can hand off this duty using the same process; A sends exact changes to the current captain.

Example: B builds the repository evidence component while C builds a catalogue component. B wires the repository route in `main.tsx`, hands it off, then C wires catalogue navigation and returns it. Do not both edit `main.tsx` while assuming Git will resolve it.

Use `feat/agent/<task>`, `feat/repo/<task>` and `feat/global/<task>` in separate clones/worktrees. A frontend change keeps its owner's lane. Conventional Commit examples: `feat(agent): validate review evidence`, `feat(repo): match advisory versions to snapshots`, `feat(global): publish curated advisory revisions`. One teammate reviews; the current captain integrates and runs the complete gate. Never rewrite shared history or force-push main.

## Shared contract and staged delivery

Read [KNOWLEDGE_CONTRACT.md](KNOWLEDGE_CONTRACT.md) before implementing new payloads. It defines private repository profiles, public catalogue entries and local matches, including intake mapping and invalidation. Existing `knowledge.py` scope `global` means the whole current repository; it is not the proposed cross-repository catalogue.

| Gate | A | B | C |
| --- | --- | --- | --- |
| First hour | Probe actual model access; run focused provider tests. | Inspect profile/retrieval boundaries; run fixed cross-module retrieval cases. | Check operator cloud access and existing CLI; agree public record fixtures with B. |
| First integration | Accept B's evidence with schemas; produce a real checked patch. | Produce a snapshot-bound match and wire repository evidence UI. | Return a deduplicated sourced entry; wire catalogue/source UI after handoff. |
| Complete journey | Exercise revision, decision and unsafe-review behavior. | Recheck snapshots and changed/deleted evidence; connect opt-in Atlas retrieval. | Run public ingestion/curation/publication and verify duplicate/stale handling. |
| Demo freeze | Record model/check outcomes, including failures. | Run integrated acceptance as captain or hand it to C explicitly. | Record actual cloud processing and metrics query results; fill source/API attribution. |

The supplied submission deadline is **Sunday 15 November 2026 at 12:00 Europe/London**; target submission by 11:00. The venue closes Saturday at 21:00; plan a saved handoff before leaving. Keep broad autonomous research, model adaptation and extra sources behind the first working journey.

## Verification and demo

Start from current `origin/main`, read [AGENTS.md](../../AGENTS.md), [CONTRIBUTING.md](../../CONTRIBUTING.md), [repository review](REPO_REVIEW.md) and [verification status](../VERIFICATION.md), and keep separate environment/data directories. Reuse current provider/retrieval/intake APIs rather than adding a second workflow engine. The [kickoff prompts](START_HERE.md) are ready to copy for each person.

Each owner tests their boundary: A actual provider inputs/outputs and checks; B citation/snapshot isolation, exact applicability and refresh; C source normalization, duplicates, stale/withdrawn records, failures and cloud publication. B/C run build/browser checks for touched UI. The current captain runs `python scripts/acceptance.py --tier deterministic` and the actual integrated demo. Keep mocked, fixture and live evidence separate.

In three minutes: open a sourced global finding, show why the exact repository snapshot matches, inspect retrieved helper/config/tests and an approved constraint, show failing-before/passing-after checks, then review/approve the exact patch. Show an unchanged rescan creates no duplicate. Claim cloud processing or model adaptation only with recorded results from that build.

Existing implementation and historical evidence must be disclosed; a history reset does not establish hackathon eligibility. See [SUBMISSION.md](SUBMISSION.md) for provenance, the public-repository requirement and the evidence register. Remaining external risks are account access, model quality and qualification of the existing baseline.
