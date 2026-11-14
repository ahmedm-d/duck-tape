# Completion implementation checklist

Active branch: `steward/full-product-local`. The approved completion specification and 14-task plan are under `docs/superpowers/`. This checklist records integration status, not claimed final acceptance.

**Repository reset:** the user requested a fresh Git history and a private `ahmedm-d/duck-tape` repository. All current source/test changes from the active worktrees are consolidated into the initial snapshot. Earlier branch names and commit IDs below are historical development context. The previous Git database and worktree source snapshots are preserved in a local archive outside the published repository. Live-model qualification and the live paired retrieval experiment remain outstanding; publication does not turn those checks into passes.

- [x] Additive persistence migrations, typed contracts and guarded state transitions
- [x] Immutable source snapshots and exact byte-safe edits
- [x] Actual constrained Docker execution and offline dependency installation
- [x] Versioned knowledge with explicit approval and promotion
- [x] Durable import/refresh/scan operations and complete supported dependency scans
- [x] Managed Git application and recovery journal
- [x] Generic provider/worker implementation with bounded repair
- [x] Parsed graph and snapshot-isolated chunk retrieval
- [x] Polished UI and actual sample/browser integration
- [x] Complete generic public HTTPS Git transport
- [ ] Complete live Python/TypeScript pipeline and model qualification evidence
- [x] Integrate all modules, run independent reviews and fix substantive findings
- [ ] Fresh final acceptance, documentation, startup verification and source integration

## Decisions

Use the existing React/FastAPI/SQLite application. Preserve existing user data and the original running process while validating changes in a writable Git worktree. Separate the API and persistent worker processes. Use immutable source blobs independently of the search index. Only fixed reviewed sample fixtures may use the host fallback; arbitrary imports and model edits require the actively probed Docker sandbox. Use a modest officialQwen2.5-Coder3B CPU model for live qualification; availability and small smoke tests do not imply coding reliability. Public upstream content is evidence, never execution authority. Every patch approval binds its revision, input fingerprint and recorded checks. Existing managed branch advances require a new preparation rather than silently applying stale checks.

Current test/review artifacts are recorded in `docs/evidence/` and the scoped implementation ledger. Final verified totals are published only after the integrated final gate.

## Latest integration evidence

Fresh virtual-environment setup and production build passed. Actual public GitLab transport and immutable raw blob export passed. Actual supervised worker kill and full API restart preserve one checked revision. The live Python model pipeline produced a regression that fails before and passes after, then passed separate model review. TypeScript attempts failed closed on model-generated invalid regression edits; the 3B CPU model is not declared qualified. The first qualification bug task passed its independent behavior holdout, then the run was interrupted to incorporate the final review's feature acceptance checks before a stable twelve-task rerun.

Security, functional/AI and rendered UX independent reviews completed with reproduced defects fixed and rechecked. Fresh final acceptance additionally found stale decision IDs, ZIP casing aliases, one arrow-parameter binding, and legacy embedding namespace gaps; those fixes pass. Feature acceptance checks bind criteria to exact executed named tests and reject skipped targets. Actual pytest/Jest/Vitest reporter checks are the last framework validation before source freeze. Live Nomic embeddings, repository/module summaries, actual OSV before/after status and generic Docker performance comparison now have evidence in `docs/evidence/`. Graph already reached full relevant-file recall on the four fixed retrieval tasks; embeddings added latency without improving that recall.

## Release tooling checkpoint

The documented deterministic acceptance command passed 376 backend tests, actual worker/API restart, the production build and 13 fixture browser tests. A separate integrated browser run passed 22 journeys; the actual Docker suite passed 11. Offline backup/restore and conservative cleanup, acceptance accounting, and startup passed 36 focused independent review checks. An actual sandbox-unavailable regression exposed missing candidate export: the worker now persists exact candidate bytes before checks, and the UI labels and exports them as unverified without approval/application. Its backend/crash tests (4) and browser download check (1) pass. Final framework execution and complete model qualification remain pending; these counts are recorded milestones, not a full live release claim.

## Integrated gate 2026-11-14 05:07 UTC

The completion-tree acceptance command passed 387 backend tests, actual restart, production build and 15 fixture browser tests with zero skips. Fresh final-delta review passed 88 focused checks, then 47 after its reproduced Q&A timing-cache fix. Both QA and summary invalid-citation retries now evict only the exact rejected response. Actual pytest and Jest selected-target/skipped-target checks passed; Vitest remains running. Final source normalization follows the existing LF policy. The backed-up original workspace upgrade and full twelve-case model evaluation remain the only release work after framework validation.

## Installed workspace checkpoint 2026-11-14 05:20 UTC

The complete source is integrated on local `main` at `ad9a3d4`. The original workspace was backed up with verified hashes and SQLite integrity before upgrade; all prior repository/job/knowledge/note/revision IDs survive. The installed app is running on port 8765 with its persistent worker, configured Qwen 3B endpoint and Nomic embeddings. The former 8767 development preview is stopped. Machine-local model files and the evidence worktree remain available; no remote push occurred.

Actual pytest/Jest/Vitest feature-target verification passed all three framework cases, including skipped-target and similarly-named-target rejection. Installed Chrome acceptance passed the complete sample patch/revision/export/application journey and left a feature patch ready for review. Desktop, narrow and dark layouts were inspected. The installed deterministic command passed restart, build and all 15 browser contracts; its backend phase passed 387 tests with one environment-dependent budget-test failure under investigation. Production identity remains frozen while the full twelve-case live-model evaluation runs; its first Python bug case passed real regression and independent behavior checks. Model qualification is still pending.

At 05:28 UTC the installed deterministic rerun passed **388 backend tests, actual restart, build and 15 browser contracts**, with zero skips. The budget test was corrected to set its intended limits explicitly; both inherited 1536-token and default configurations pass. The original failing report remains. A final documentation audit corrected the intake example to use lowercase priority and verified local links and startup/maintenance commands. The live twelve-case corpus continues: its initial bug task passed, while feature/revision attempts exposed model-generated test/source path and hash confusion that the controller rejected. A generic request-bound schema calibration is under independent assessment; no production change is included in these passing gates.

## Bounded model calibration

Independent inspection reproduced the failing model outputs against the exact requests. Runtime guards rejected them before candidate/check/revision publication. Test generation also inherited an instruction to produce a complete cumulative patch, conflicting with its test-only role. A separate `steward/model-calibration` worktree prepared a narrow correction: a test-only edit contract, request-specific schemas pairing each editable test path with its immutable hash, acceptance criterion bounds, and explicit planning precedence for requested product choices. Existing CREATE support and every edit/verification boundary stay in force; no generated response is rewritten to conceal a failure. Independent review passed and the full isolated backend suite passed **407 tests in 321.36 seconds**. The first twelve-case run continues against unchanged production source before integration and separately recorded calibration evaluation.

At 05:58 UTC, the baseline has two passes and seven failures from nine completed cases. One failure is an actual false-safe model review of a deliberately wrong patch; schema calibration does not establish that this is solved. One modest official Qwen3 4B Q4 candidate is being downloaded and checksum-verified for a bounded comparison after the baseline, without loading it alongside the current runner. No security setting changes or further model shopping are planned.

A fresh runtime inventory also found multiple project-owned copies of the same model on its loopback port, consuming memory. Startup currently treats a health timeout as permission to launch again. A separate guarded-launch correction is being tested against occupied ports and concurrent processes; no baseline runner is stopped or modified mid-evaluation. Final traceability found the retrieval benchmark lacked paired task/check outcomes. A standalone evaluation harness now prepares the same Parcel bug with keyword and graph retrieval through the generic live worker and actual sandbox, retaining both outcomes without assuming an improvement. Its five controller tests pass; live execution remains pending.
