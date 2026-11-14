# Independent functional and repository-AI review

Reviewed 2026-11-14. Agent concurrency limits prevented assigning a fresh reviewer identity; the independent security reviewer performed this second role. The reviewer did not implement the worker, API, provider or UI under review. Tests use real SQLite, Git, source files and fixed subprocesses; mocked provider transport is explicitly identified.

## Findings supplied to the lead

| Severity | Reproduction | Recommended fix |
| --- | --- | --- |
| Major | Scan the fresh sample twice: two jobs describe the same fixture advisory because the seeded job lacks the intake dedup identity. `test_scan_deduplicates_preseeded_advisory` failed with 2 instead of 1. | Give seeded security jobs the same source URL/key and dedup identity as scanned findings, repairing old seed metadata idempotently. |
| Major | Reject a scanned advisory, edit only README, and scan again: the unchanged finding returns as a new job. `test_rejected_advisory_stays_rejected_when_unrelated_file_changes` failed with a different ID. | Deduplicate sourced findings using the source-specific evidence key independent of the entire snapshot. Advisory keys already identify package/version and analysis keys include file content hash. |
| Medium | Fail a provider after retrieval: the failed run loses the previously persisted context. `test_failed_provider_run_retains_actual_context` failed with missing `context`. | Retain the context checkpoint in the local run object used by failure/cancellation writes. |

## Passing concrete checks in the initial round

- An expired implementation lease recovers after starting the worker and produces one actual checked fixture revision.
- Cancelling a running fixed sleep subprocess terminates that process, preserves Cancelled state, and publishes no revision.
- Approved scoped knowledge appears in a later generic provider payload; unrelated scoped knowledge and team notes do not. Editing approved knowledge invalidates the existing ready job, and retry receives the new content and context hash.
- Two prepared overlapping patches can both be reviewed, but applying the second returns a conflict after the first was integrated. The original sample source remains byte-identical.

The added acceptance cases live in `tests/test_functional_review.py`. Recheck results are recorded below once the implementation fixes are integrated.

## Recheck and final acceptance pass

All three findings were fixed by the API/worker owners and independently rechecked. The complete backend suite (`python -m pytest tests -q -p no:cacheprovider`, with `PYTHONPATH=vendor`) passed **65 tests in 58.80 seconds**. The only warning was an installed Starlette dependency's pending `python_multipart` import deprecation.

The final pass adds actual scheduled monitoring: start the scheduler, wait for its real polling interval, confirm persisted last/next timestamps, stop it, restart it with due work, and confirm a second scan without duplicate security jobs. Both scan-dedup reproductions now pass, including preservation of rejection across unrelated README changes. Failed-provider runs now retain their exact snapshot and retrieved file evidence.

A read-only check of the running app at `http://127.0.0.1:8765` returned health `ok`. At review time Settings accurately reported provider unavailable/offline sample, sandbox unavailable, and local demo identity limitations. This is a point-in-time capability observation, not a claim that subsequent model setup is unavailable or that live inference was tested by this reviewer. No known unresolved major defect remains from this review's tested scope.

## UI and capability inspection

Code inspection confirms unavailable execution is explained in Settings; provider mode is exposed; imported-repository patches are export-only on the backend; optional visual previews are labelled future capability. The renderer is React text output rather than injected HTML. This review did not perform rendered-browser UX checks; those belong to the lead/browser review. Repository Q&A remains explicitly offline structural evidence, and mock-provider tests are not proof of live inference.
