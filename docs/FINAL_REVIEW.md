# Fresh final acceptance review

Reviewed 2026-11-14 by the repository implementation agent acting as a fresh reviewer of the worker, API, persistence and UI. This reviewer did not implement those subsystems. Review included source inspection, actual SQLite/Git/subprocess tests, deliberately mocked provider transport for payload assertions, and rendered desktop/narrow screenshot inspection. It is not a production security certification.

## Findings and rechecks

| Severity | Concrete reproduction | Fix and recheck |
| --- | --- | --- |
| Major | Pause an approved patch application at `git apply --check`, then create a team-approved knowledge constraint concurrently. The knowledge request invalidated approval while application continued with the old in-memory job and could mark it Completed. | API owner serialized both knowledge create/edit and invalidation with the same lock as application. `test_knowledge_edit_serializes_with_approved_application` reproduced the failure before the fix and now passes. |
| Major | Ask the generic provider to revise a ready patch. Its payload contained only the old revision UUID, while checkout construction started from the baseline; the provider could not inspect the actual prior edits it was asked to retain. | Worker owner now supplies the owned previous revision's diff/files/hash and explicit cumulative revision instructions, validating job and snapshot ownership. `test_generic_revision_receives_previous_actual_patch` failed before the fix and now passes. |
| Previously reported major | Earlier desktop screenshot showed generated `__pycache__` binary changes mixed into a reviewed patch despite absence from its declared changed files. | Worker owner disables bytecode generation and stages explicit edits only. Fresh `test_diff_only_contains_declared_changed_files` verifies exact file equality and no bytecode content. |
| Defense verified | Host Git configuration could specify executable filters or credential helpers. | Current Git wrapper clears inherited configuration, uses an environment allowlist and disables external attributes/hooks/helpers. Benign host-config contamination test confirms managed Git cannot read a sentinel setting from the configured host file. No hostile filter was executed. |

All four tests in `tests/test_final_review.py` pass after fixes (11.68 seconds). These tests use actual Git application and fixture checks; generic model payload capture is explicitly a transport fixture, not a live-model quality claim.

## Visual inspection

Inspected `frontend/job-desktop.png` and `frontend/overview-narrow.png`. The desktop view provides clear revision selection, separate source/type tags, confidence and execution limitations, exports, approval controls and a future-capability preview. The narrow view collapses navigation and retains readable cards and activity without overlapping main content. The inspected desktop screenshot predates the bytecode fix; its old diff is not final patch evidence. Fresh backend regression confirms the defect is fixed. Rendered-browser interaction automation belongs to the frontend/lead evidence; screenshot inspection alone does not prove control behavior.

## Scope and honest limits

- Imported and arbitrary live-generated code is not run on the host: absent a constrained sandbox, execution is unavailable and patches are review/export-only. This reviewer did not verify a live provider plus sandbox end-to-end run.
- Sample checks and benchmarks execute reviewed fixture code; those are separate from real-model output and repository Q&A.
- This reviewer authored repository indexing earlier, so its existing parser/retrieval tests are implementation tests, not an independent endorsement of that subsystem. The earlier security review independently challenged exclusions and boundaries.
- The available screenshot inspection covered desktop and narrow layouts, not every modal or keyboard interaction.
- Starlette currently emits a TestClient/httpx deprecation warning; tests continue to pass. No failing behavior was inferred from the warning.

## Final integrated gate

After both newly reproduced major defects were fixed, the complete backend suite passed **71 tests in 68.90 seconds**. Command: `python -m pytest tests -q -p no:cacheprovider`, with `PYTHONPATH=vendor-current` and `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. The latter disables unrelated globally installed test plugins, not application checks. One nonfailing Starlette TestClient/httpx deprecation warning remains.

No known unresolved critical/major defect remains within this review's executed and inspected scope. The external execution-sandbox limitation and explicitly listed coverage limits remain; this result does not certify arbitrary model-generated patches.
