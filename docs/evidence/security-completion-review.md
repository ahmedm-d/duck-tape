# Security and reliability completion review

Date: 2026-11-14. Reviewed the working tree in `steward/.worktrees/full-product`; no original application workspace was changed. The assumed deployment is one local workspace on loopback, with trusted local controller processes and untrusted imported repository content. This is not a hostile multi-tenant security assessment.

What this change does: Steward imports sanitized repository snapshots, queues durable work, versions project knowledge, and applies an explicitly approved patch to a managed branch. This review followed the actual import, refresh, cancellation, approval, and recovery callers, then injected failures at publication and commit boundaries.

## Confirmed findings and fixes

The first run of `tests/test_completion_security_review.py` produced **6 failures in 5.41 seconds**. These are reproduced bugs, not inferred checklist risks.

1. **P1: A cancelled import can still publish a visible repository.**
   - Affected: `backend/operations.py:advance`, `backend/main.py:register_repo`.
   - Reproduction: cancel the operation immediately before the repository write, after its last cancellation check. The operation remains `cancelled`, but its repository appears in the database and search state.
   - Cause and required fix: ownership checking and repository publication use separate transactions. Check the exact operation token and lease inside the transaction that publishes the repository and related state.
   - Risk if retained: cancellation or an expired worker can publish an unwanted result, and a replacement worker can compete with that result.
   - Recheck: fixed. Repository publication and operation completion now share a transaction with an exact token/lease check. Tests cover cancellation before publication, cancellation immediately after publication, and expired-owner rollback followed by recovery.

2. **P1: A refresh crash permanently preserves stale approval and approved knowledge.**
   - Affected: `backend/main.py:refresh_repo`, its database/index publication, and `backend/knowledge.py:invalidate_snapshot`.
   - Reproduction: change `main.py`; fail `db.index_search` after the new repository record has committed; retry refresh. The retry sees the new snapshot as its starting point and never clears the old approval or marks the old source-backed knowledge stale.
   - Cause and required fix: repository publication, search publication, knowledge invalidation, and job invalidation are separate transactions. Publish these as one guarded transaction, retaining historical snapshots.
   - Risk if retained: stale policy continues to inform subsequent work, while the UI preserves an approval from a previous source state. The existing base-snapshot check may stop application of the old patch, but does not repair policy or review state.
   - Recheck: fixed. The injected index failure now rolls back the repository update; retry clears approval and marks the affected knowledge stale.

3. **P1: Binary-only refresh leaves pending approval intact.**
   - Affected: `backend/main.py:refresh_repo`; the textual identity produced by `backend/repository.py:index_repository`.
   - Reproduction: change only `asset.bin`. `source_snapshot_id` changes, while the textual snapshot stays equal. The job remains `Ready for review` with its approval.
   - Cause and required fix: refresh invalidation uses only indexed text identity. Bind invalidation and current-source comparisons to the sanitized source manifest, including bytes and modes.
   - Risk if retained: verification and approval remain associated with different build inputs. The later physical-source check currently blocks application, leaving a stale review that must be invalidated explicitly.
   - Recheck: fixed. Full source identity now participates in refresh invalidation, and changed source paths include byte hashes and modes.

4. **P1: Source drift during managed application still reaches Completed.**
   - Affected: `backend/integration.py:apply`, `_complete`.
   - Reproduction: change the repository source immediately after the managed Git commit. `integration.apply` returns `Completed`; it checked source bytes only before application started.
   - Cause and required fix: final completion rechecks database approval/check records but does not recheck the physical source manifest. Verify that manifest again before publishing completion; retain the journalled commit for explicit recovery on conflict.
   - Risk if retained: completion describes an approved result against a source checkout that has changed during the operation.
   - Recheck: fixed. Source checks now run before commit and before completion; the source-drift regression raises a conflict and leaves the job incomplete.

5. **P2: Public import and refresh discard cancellation before Git transport.**
   - Affected: `backend/operations.py:advance`, `backend/repository.py:import_git`, `backend/snapshots.py:_refresh`.
   - Reproduction: inspect the token received by the import transport after an operation is claimed. It receives `None`; the same is true for refresh.
   - Fix implemented during this review: pass the token through all callers and into generic HTTPS Git; check cancellation before and after every GitHub fetch. Regression cases also cover the public wrapper and refresh path.
   - Remaining limit: an in-flight GitHub HTTPS request finishes or hits its existing socket timeout before the new cancellation checkpoint runs. This change does not claim immediate interruption of that socket read.

6. **P2: Git history stores credential-bearing metadata.**
   - Affected: `backend/repository.py:import_git`, `backend/git_transport.py:import_https`.
   - Reproduction: return a synthetic GitHub commit message containing a recognized `ghp_` token. It is retained in `origin.history_entries`, which is persisted and returned by repository APIs. Follow-up cases reproduced the same issue in GitHub authors and a real local Git commit author passed through the generic import path.
   - Fix implemented during this review: exclude any GitHub history record or parsed generic Git history row matching the existing credential detectors and record an exclusion gap. This covers author and other history fields as well as the message. All three regressions pass.
   - Risk if retained: importing a public repository unnecessarily republishes and persists recognized credential material in local application records.

7. **P1: Unchanged imported executables fail source validation on Windows.**
   - Affected: `backend/snapshots.py:capture`; callers in source refresh and managed application.
   - Reproduction: import a ZIP containing a regular `run.sh` entry with mode `100755`, register it, then perform an unchanged source check. The initial capture preserves mode `755` but removes its authoritative metadata from the stored origin. Windows reports the later file as non-executable, so the recheck incorrectly raises `Source checkout changed`.
   - Fix implemented during this review: retain imported mode metadata and use it for Windows captures. Unix captures use actual filesystem modes so real executable-bit changes remain detectable. Snapshot gaps explicitly state the Windows limitation.
   - Recheck: the actual ZIP extraction, registration, and source-recheck case now passes on Windows. The same regression includes a Unix-only mode-drift assertion; that branch was not executed on this Windows host.

8. **P1: A displaced application worker can complete and overwrite its replacement's journal.**
   - Affected: `backend/integration.py:apply`, `_complete`, application journal writes and exception cleanup.
   - Reproduction: replace the persisted application owner immediately before completion. The original worker still completes the job and overwrites the replacement owner. Application leases also lacked a heartbeat; the fixed 180-second lease could expire during a large materialization/staging operation.
   - Required fix: renew the application lease independently, and condition journal writes, commit/completion guards, and cleanup on the current owner and lease.
   - Recheck: fixed. The displaced-owner regression now raises a conflict, leaves the job incomplete, and preserves the replacement's journal. Existing commit-before-database crash recovery also passes. Heartbeat renewal is present in the reviewed code; this review did not wait through a real 180-second application lease.

## Verification and scope

Focused regressions are in `tests/test_completion_security_review.py`. Tests use temporary workspace-local storage, real SQLite transactions, and real local Git for application; external GitHub responses are deterministic transport fixtures containing synthetic tokens. No real credential, model endpoint, or Docker daemon was accessed by this review.

The review also inspected `backend/network.py`, snapshot manifest/blob validation and materialization, versioned knowledge/evidence handling, `backend/workflow.py` leases, `run.py`, and `backend/worker_main.py`. Network tests exercise a local test proxy with controlled DNS, private destinations, credential-bearing authorities, byte limits, and environment/config isolation. This demonstrates the tested boundary behavior, not unrestricted internet interoperability.

Final focused recheck: **80 passed, 1 deselected in 35.75 seconds**. It ran all 15 new review cases plus managed integration, operations, knowledge versions, generic Git transport, import/refresh, snapshots, and startup-setting tests. The deselected 12,001-file inventory-cap case had already passed in the earlier **61 passed, 4 deselected** boundary run (106.84 seconds); it was not repeated after the mode-only snapshot edit. An intermediate broader run had 94 passes and two existing tests using superseded contracts: full index data in operation responses, and preparation approval without an exact plan. Those were reported to the owners; the latter fixture's adaptation is outside this final focused command.

Command (PowerShell, from the review worktree):

```powershell
$env:PYTHONPATH='C:/Users/ahmed/Projects/ducktape/steward/vendor-current'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest tests/test_completion_security_review.py tests/test_managed_integration.py tests/test_operations.py tests/test_knowledge_versions.py tests/test_git_transport.py tests/test_import_refresh.py tests/test_snapshots.py tests/test_startup_settings.py -k 'not test_index_inventory_cap' -q -p no:cacheprovider
```

Verdict: **the eight reproduced findings are fixed and the focused checks pass**. This is a scoped review result, not full product acceptance. Transport, history, and Windows-mode fixes were implemented by this reviewer after the initial findings; the lead still needs to review those edits independently.

Not checked: live-model quality, full Docker isolation probes, external public Git interoperability, UI rendering, and actual supervisor process death/restart in this review. Those require their separate acceptance evidence; source inspection alone does not endorse them.
