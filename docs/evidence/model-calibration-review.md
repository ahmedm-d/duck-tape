# Independent bounded model calibration review

Scope: the uncommitted calibration candidate in `steward/model-calibration`, specifically `backend/model_schemas.py`, `backend/providers.py`, `backend/worker.py`, and their focused tests. The frozen full-product baseline, running application and model endpoints were not changed. This review made no production edits and no live inference calls.

## Findings

No reproduced material defect was found in the calibration delta. The candidate is suitable for a separate bounded live calibration check; this is not a claim of model qualification or an improvement in task success.

- Each permitted test UPDATE has a single schema branch binding its exact supplied path to its exact immutable hash. Different files' hashes cannot be swapped. Whole-file UPDATE branches exist only when the supplied file is marked complete; anchored updates remain possible for incomplete evidence. Test roles no longer generate DELETE/RENAME branches.
- CREATE remains expressible for a new test path without an existing test file. It does not gain authority over existing files, implementation paths, secrets or repository boundaries: the unchanged controller test-path restriction and `edits.apply` preimage/path/content validation still run. The changed existing correction fixture now uses a schema-valid source CREATE to exercise that later guard; invalid source UPDATEs are rejected earlier by the request-bound schema.
- Acceptance coverage uses the current bounded criterion IDs and exact cardinality. Local validation additionally rejects duplicate/missing IDs, rather than assuming schema generation alone enforces that relationship. Executable test-reference checks, preserved acceptance bytes, sandbox verification, exact test observation and the separate model review remain downstream requirements.
- OpenAI-compatible and Ollama transports receive the same request-bound schema used for fresh local validation. Cached provider output and completed worker stages are revalidated against it. An invalid cached response is evicted through the existing compare-and-remove operation; a corrupt completed stage fails closed. The implementation does not rewrite a rejected model response into a valid one.
- The schema and prompt versions are incremented. Provider cache identity and worker stage identity include the effective schema and request data. The complete schema is included in the conservative input-budget calculation; retry/call/output budgets remain bounded. The planning prompt's decision instruction reads the existing `approved_decisions` payload and does not bypass preparation or exact-revision approval.
- The test-generation payload replaces the conflicting cumulative-source-patch instruction with a test-only contract. The implementation payload retains its cumulative patch instruction. Selected decisions, current approved knowledge and immutable source identity continue through the existing pipeline.

## Independent evidence

Used the original project's virtual-environment Python with `PYTHONPATH` set to its existing `vendor-current` directory, from the calibration worktree. Provider and verification transports in these suites are deterministic mocks; Git edit/preimage tests use actual isolated filesystem/Git operations. No fixture success is counted as live inference.

- `tests/test_test_role_schema.py`: **19 passed in 13.45 seconds**. This includes replaying all four recorded bad acceptance responses from `tests/fixtures/calibration/acceptance-failures.json`, transport parity, cache revalidation, resumed-stage invalidation and new-test compatibility.
- Independently recomputed all eight source-content SHA-256 values in those four fixtures: all match the recorded bytes. The bad response data is inspected/rejected as data and never executed.
- Focused compatibility suites (`test_provider_contracts`, `test_live_pipeline_contract`, `test_edit_operations`, `test_final_completion_acceptance`): **84 passed in 81.00 seconds**. These retain decision replay, cancellation/publication, missing feature-check rejection, skipped-target rejection, path/preimage, export and provider-budget boundary coverage.
- `git diff --check`: passed.

No extra review regression file was added because no new material gap was reproduced. No test, sandbox, edit precondition or approval requirement was relaxed.

Not checked: live grammar acceptance, actual coding-quality improvement, or a full qualification rerun. A larger request-bound schema can exhaust the existing context limit on larger inputs; that remains a visible bounded failure, not permission to omit evidence or checks.
