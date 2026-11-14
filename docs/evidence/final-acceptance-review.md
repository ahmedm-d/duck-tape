# Fresh final acceptance review

Date: 2026-11-14. Workspace: `steward/.worktrees/full-product`. This review independently inspected the integrated controller, state transitions, source snapshots, imports, managed application, verification, dependency advisories, retrieval, providers, summaries and symbol binding code after the first security, functional and UX fix round.

The reviewer owns `tests/test_final_completion_acceptance.py`. Production changes were not made by this reviewer. The new contract tests use temporary databases and actual indexing, Git patch creation and controller state where relevant. Provider/check HTTP transports are deterministic. A final independent run also executes the authored Python and TypeScript acceptance fixtures through actual Docker. No live inference endpoint or arbitrary imported code on the host was executed in this review.

## Reproduced findings

1. **P1 — an obsolete decision can resume a different pending question.** `backend/workflow.py:transition`, `decide` action. The worker produces Question 1, the user answers it, and the worker later pauses on Question 2. Posting the ID of Question 1 again changes its answer and queues the job while Question 2 remains unanswered. The server checks only that the card belongs to the job, not that it is the currently pending card. `test_old_decision_cannot_resume_a_different_pending_question` fails because no conflict is raised. Require the exact current unresolved `job.decision_id`, preserving completed decision history.

2. **P2 — a generic feature can publish without a requested-behavior check.** `backend/worker.py:process_job`, verification/publication branch. A feature provider changes only implementation; the unchanged check transport passes and separate model review approves. The controller publishes `Ready for review` even though it produced no check for the requested behavior. It calls `verification.assess(result, [])` and only bug, security and performance jobs add type-specific requirements. `test_feature_without_requested_behavior_check_cannot_publish` fails. Require a concrete executed feature behavior check alongside the preserved baseline suite; an existing-suite pass and model review alone do not establish the new feature.

3. **P2 — the worker retrieval overload persists unscoped vectors.** `backend/retrieval.py:retrieve`, legacy index-argument path; `backend/providers.py:embed`. The generic worker uses `retrieve(index, query)`. That path calls the legacy list-based embedding adapter, persisting `repo_id=None`, `snapshot_id=None`, and `chunk_id=None` even though retrieval has canonical repository/snapshot identities. `test_worker_style_retrieval_persists_repository_scoped_vectors` fails with real embedding persistence and deterministic vector HTTP responses. Use the same typed, namespace-bound embedding path for both retrieval calling conventions.

4. **P2 — ZIP parent case aliases silently merge source trees on Windows.** `backend/repository.py:import_zip`. The archive contains `src/one.py` and `SRC/two.py`. Full file paths differ, so validation accepts both; Windows merges the folders and wrapper normalization can flatten the merged tree. Generic Git import already rejects inconsistent path-prefix casing. `test_zip_parent_case_aliases_do_not_silently_merge_directories` fails. Validate original casing of each normalized path prefix before extraction.

5. **P2 — a single unparenthesized JavaScript arrow parameter creates a false imported caller.** `backend/symbols.py:parse_js`. With `import { value } from './helper.js'; const run = value => value(); value();`, both calls resolve to the imported helper; only the final call should resolve. `test_javascript_single_arrow_parameter_does_not_create_imported_caller` fails with lines `[2, 3]` instead of `[3]`. The parser reads Tree-sitter's `parameters` field but not the singular `parameter` field used by this arrow form. Include that field in the existing binding traversal.

All five findings were sent to the lead with reproduction names and recommended small fixes. Their original reproductions are retained above. Current recheck status is recorded below; this is not a full live-release endorsement.

## Passed boundary checks

The new acceptance file independently confirms cancellation immediately after check return cannot publish a late revision; an advisory still matching a nested second installed version fails remediation; deleting a lockfile while its dependency manifest remains produces unavailable verification; and benchmark evidence with a different patch binding cannot pass.

Independent rerun of the earlier review fixes plus the new advisory/performance implementation:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
./.venv/Scripts/python.exe -m pytest tests/test_completion_security_review.py tests/test_completion_functional_review.py tests/test_security_verification.py tests/test_verification.py tests/test_execution_profiles.py -q -p no:cacheprovider
```

Result: **53 passed in 24.45 seconds**, with one existing Starlette/httpx TestClient deprecation warning. This includes actual Windows ZIP executable-mode recapture, secret-bearing Git history exclusion, cancellation propagation, source drift at commit, displaced-owner fencing, atomic refresh/import publication, summary model-cache invalidation, and TypeScript destructured parameter regressions. The direct review confirmed that Git transport/history changes retain cancellation checks and never treat history as execution authority.

The first six new cases produced **2 failed, 4 passed in 6.21 seconds**. The embedding scope, ZIP parent case alias, and JavaScript arrow tests each subsequently failed as described above. These failures are retained explicitly instead of being counted as passed functionality.

## Independent fix recheck

The lead fixed findings 1, 3, 4 and 5. The reviewer inspected each production change and reran the affected behaviors:

- Decision submission now requires the job's exact current card and rejects a previously selected card. The old answer cannot queue a different unanswered question.
- Both retrieval overloads now use canonical repository/snapshot/chunk identities with the same typed embedding adapter. The repository index preserves these identities. Identical source and query content in two different repositories produces disjoint persisted vector IDs with correct scope.
- ZIP validation now rejects inconsistent casing at any path prefix before extraction, retaining the existing full-path duplicate and traversal checks.
- JavaScript arrow binding extraction now includes the singular `parameter` field and preserves the independent actual imported-call control.

Command: `./.venv/Scripts/python.exe -m pytest tests/test_final_completion_acceptance.py -k 'not feature_without' -q -p no:cacheprovider`. Result: **10 passed, 1 deselected in 3.29 seconds**. The deselection is the still-pending feature behavior-check finding, not an external skip.

Provider and retrieval recheck: `./.venv/Scripts/python.exe -m pytest tests/test_final_completion_acceptance.py::test_worker_style_retrieval_persists_repository_scoped_vectors tests/test_provider_contracts.py tests/test_chunk_retrieval.py -q -p no:cacheprovider`. Result: **39 passed in 6.91 seconds**. An earlier concurrent run loaded the old index metadata before the lead's final edit and returned 55 passed / 1 failed; the explicit current-source rerun above resolves that failure.

The reviewer also inspected the new separate embedding endpoint, model-specific Nomic query/document prefixes, endpoint-bound vector cache identity, server-only credential selection and project-local hash-checked startup. Added tests verify that changed model bytes cannot start a process and a cancelled embedding response cannot persist vectors. Existing transport tests verify the coding credential is not sent to a separate embedding endpoint. These are deterministic boundary checks, separate from the lead's actual Nomic embedding run.

The feature implementation now introduces a strict acceptance-test role, criterion-to-test references, controller-built runner commands, and required candidate check results. During re-review, an additional **P2 bypass** was reproduced: a mapped Python target marked skipped could borrow the passing count from another test in the same file. The first observation gate checked only total count greater than skips and the target name appearing anywhere in output. `test_skipped_feature_target_cannot_borrow_an_unrelated_passing_test` produced `Ready for review` and failed in 3.43 seconds. The owner was asked to select the target in supported runners and reject skipped acceptance evidence. This is a refinement of finding 2, not a separate speculative feature request.

The new embedding telemetry stores actual call latency, reported usage (or null), dimensions and repository/snapshot attribution once per actual request; cached vectors link back to that call. Code review found no secret-bearing endpoint or credential copied into these records. The lead's actual Nomic usage and latency evidence is separate from this review's transport tests.

During the next feature recheck, the Node passing-line parser omitted TAP's `-` separator. The reviewer ran a fixed, authored Node fixture and captured `ok 1 - feature runs`; `test_actual_node_tap_passing_target_is_observed` failed against that actual output. The model-engine owner corrected the grammar and retained the explicit SKIP/TODO rejection. The candidate-only command integration was also completed: new dedicated feature commands run only against the candidate, while the original suite still runs for baseline, regression-before and candidate phases.

The final feature implementation now:

- Maps every generated plan criterion to a changed, asserting test with a concrete function/label.
- Constructs supported runner arguments in controller code, including exact AST-derived Python selectors and literal Node test-name patterns.
- Requires observed passing target output; a skipped target cannot borrow another passing test's count.
- Preserves the separately prepared acceptance-test bytes during implementation and repair.
- Persists coverage and assessment with the exact revision, and sends executed check evidence to separate model review.

Final independent deterministic command:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
./.venv/Scripts/python.exe -m pytest tests/test_final_completion_acceptance.py tests/test_live_pipeline_contract.py::test_feature_criteria_require_executed_generated_tests tests/test_verification.py tests/test_provider_contracts.py -q -p no:cacheprovider
```

Result: **60 passed in 30.34 seconds**. The complete new acceptance file runs, including missing feature checks, skipped target, Node TAP positive output, candidate-only phase behavior, exact current decision, scoped embedding caches/cancellation, ZIP aliases and JavaScript shadowing.

Final independent actual execution command, using the environment's permitted Docker access without changing security controls:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:STEWARD_LIVE_SANDBOX='1'
./.venv/Scripts/python.exe -m pytest tests/live/test_feature_acceptance_execution.py -q -p no:cacheprovider
```

Result: **2 passed in 18.28 seconds**. The tests execute the unchanged baseline suite, regression-before suite, candidate suite and dedicated acceptance target in real constrained Linux containers for Python and TypeScript. They assert that the dedicated target ran exactly once in the candidate phase and that the final assessment passes. No model-response fixture is presented as live inference; these are reviewed source/check fixtures with real sandbox execution.

**Scoped verdict:** all five initial findings and the feature integration follow-ups are fixed and independently rechecked. No known critical or major defect remains in this review's tested scope. This verdict does not replace the lead's full integrated suite, rendered UX review or model qualification. No production code was authored by this reviewer.

## Limits

The lead owns the full integrated suite, real model qualification, complete Docker isolation probes, live provider/embedding acceptance, startup/restart evidence and final source integration. The separate UX reviewer owns rendered browser acceptance. This review does not call those capabilities verified merely because their source or prior reports were inspected. Unix-only executable-mode mutation was not run on this Windows machine. Dedicated feature targets were executed here for Python unittest and Node TypeScript; pytest, Jest and Vitest selectors were inspected but not separately run with their real frameworks in this review. Test references and model review provide evidence, not a proof that a test semantically covers every possible interpretation of a request. No production deployment or multi-user access-control assessment is claimed.
