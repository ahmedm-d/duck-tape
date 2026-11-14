# Isolated model calibration candidate

Implemented and checked on 2026-11-14 in `.worktrees/model-calibration`, branch
`steward/model-calibration`, based on
`ad9a3d46f9f5a3cbfb8c75b0e3b7b9c62e83f67d`. No commit, model inference, or edits
to the frozen `full-product` worktree were made for this candidate.

## Problem and bounded change

Actual 3B acceptance responses for the decision-feature and revision journeys
edited a read-only source path or combined one file's path with another file's
hash. The controller rejected these before publishing a revision. The model's
test-only request also inherited an implementation instruction to return the
complete cumulative patch, despite its explicit test-only instruction.

The candidate replaces that inherited instruction for acceptance/regression
only. Each supplied editable test file has its own UPDATE schema alternative
binding its path and immutable hash together. Whole-file updates require trusted
`complete: true` evidence; anchored updates remain available for partial files.
CREATE remains supported, including repositories without existing tests. Existing
controller checks still enforce test paths, creation preconditions, patch
integrity, executable assertions and runtime acceptance.

Acceptance coverage IDs and array cardinality are bound to the current criteria;
local validation additionally requires each criterion exactly once. The same
derived schema is used for constrained generation, response validation, cache-hit
validation, worker stage identity and resumed-stage validation. Invalid provider
cache entries are evicted and retried without modifying their output. Invalid
resumed worker output fails closed. Schema/prompt versions are now
`steward-schema-3` and `steward-13`.

One generic planning instruction asks for an explicitly requested product choice
before a concrete plan when `approved_decisions` has no relevant selection.
There are no fixture-specific provider branches or response corrections.

## Recorded failure provenance

[The portable fixture](../../tests/fixtures/calibration/acceptance-failures.json)
contains the four actual acceptance/acceptance-correction outputs, their original
stage IDs and the input subset needed for schema validation. The output and
retained input fields were compared with the original artifacts after the change;
all four matched exactly. Unrelated original input fields are omitted.

Original retained artifacts, outside this worktree:

- `steward/data/q3b-final/python-decision-feature-0d417f806c234fd18804a1259dc209fc/qualification-result.json`
- `steward/data/q3b-final/python-revision-507c823e74924323b9cb9b856e484dfa/qualification-result.json`

[Recorded-output results](model-calibration-recorded-output-results.json) preserve
the original artifact hashes, fixture hash, stage IDs, original conflicting
instruction and validation results. All four outputs pass the static compatibility
schema and fail the request-bound schema. The fixture SHA-256 is
`02fef1d6405118efbf78effb650e0e1c9062be6260b4a31fd83cd2dfae43177e`.

## RED and GREEN evidence

Before production changes, the 16 initial focused cases failed in 10.20 seconds.
Failures included the missing payload-bound API, transport acceptance of a
read-only UPDATE, acceptance of a poisoned cache entry, the conflicting test-role
instruction and failure to invalidate a resumed stage after schema changes. This
RED run was observed in the task console; no JUnit file was retained from it.
The four static-pass/dynamic-reject comparisons remain executable regressions,
with machine-readable results linked above.

The first broader run found one existing mock-only correction test incompatible
with earlier schema rejection: it bypassed the provider with a read-only UPDATE.
Its injected error now uses a schema-valid CREATE at a source path, preserving
the original controller preflight rejection and bounded-correction coverage.

Final checks:

| Command scope | Result | Duration | Portable evidence |
| --- | --- | --- | --- |
| New schema tests, provider contracts, generic pipeline, final completion acceptance | 84 passed; 0 failed/errors/skips | 87.92 seconds | [JUnit](calibration-contract-tests.xml) |
| Existing edit-operation guards | 19 passed; 0 failed/errors/skips | 11.53 seconds | [JUnit](calibration-edit-guards.xml) |
| `git diff --check` | Passed | — | Console check |

The 19 new focused cases include the four recorded outputs, paired hashes,
partial-file whole-update denial, anchored update support, new-test CREATE with
no existing tests, criterion membership/cardinality/uniqueness, identical transport
schemas and bounded retries for both OpenAI/Ollama mocked transports, poisoned
provider/stage cache handling, schema-sensitive stage identity, planning wording
and legacy regression compatibility.

Run from the calibration worktree using the existing environment:

```powershell
Set-Location C:/Users/ahmed/Projects/ducktape/steward/.worktrees/model-calibration
$env:PYTHONPATH='C:/Users/ahmed/Projects/ducktape/steward/vendor-current'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$python='C:/Users/ahmed/Projects/ducktape/steward/.venv/Scripts/python.exe'
& $python -m pytest tests/test_test_role_schema.py tests/test_provider_contracts.py tests/test_live_pipeline_contract.py tests/test_final_completion_acceptance.py -q -p no:cacheprovider --junitxml=data/calibration-contract-tests.xml
& $python -m pytest tests/test_edit_operations.py -q -p no:cacheprovider --junitxml=data/calibration-edit-guards.xml
git diff --check
```

## Exact delivered file scope

Production:

- `backend/model_schemas.py`
- `backend/providers.py`
- `backend/worker.py`

Tests and fixture:

- `tests/test_test_role_schema.py` (new)
- `tests/test_live_pipeline_contract.py` (one existing injected error adjusted)
- `tests/fixtures/calibration/acceptance-failures.json` (new)

Implementation evidence:

- `docs/evidence/model-calibration-implementation.md` (this report)
- `docs/evidence/model-calibration-recorded-output-results.json`
- `docs/evidence/calibration-contract-tests.xml`
- `docs/evidence/calibration-edit-guards.xml`

The independent reviewer owns its separate review test/report files; those are
outside this implementation scope.

## Limits

These are deterministic boundary and compatibility results. Neither mocked
transport tests nor replay of recorded output demonstrate improved live model
quality. The original failures remain model quality failures that were safely
blocked; the candidate removes contradictory guidance and narrows generation to
existing controller requirements. A separate, explicitly versioned live cohort
is needed to measure whether the candidate improves task success. No live model
or Docker execution was needed or performed for this isolated change.
