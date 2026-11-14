# Paired retrieval task harness review

What this change does: `scripts/evaluate_retrieval_tasks.py` runs the same reviewed
Parcel quantity bug in separate keyword-only and keyword-plus-graph workspaces.
It uses the generic worker/provider path, records every retrieved context and
provider attempt, requires real before/after checks, and runs an independent
quantity/tax holdout inside Docker. Embeddings are disabled in both arms.

Reviewed on 2026-11-14. Scope: the new harness, its focused tests, and connected
read-only qualification-driver, database, worker, retrieval, dependency and sandbox
flows. Assumed use is one sequential pair in a standalone controller process.
No provider inference, Docker execution or process changes were performed during
this review. Frozen production modules and the qualification driver were not
edited.

## Must fix — resolved

1. **Task success could be reported without proving the retrieval mode.**
   A passing driver result with no `retrieval_evaluations`, a wrong mode, or
   semantic retrieval enabled still satisfied the previous CLI success condition.
   The harness now retains the underlying task outcome separately and requires
   actual recorded contexts in the expected mode and the current workspace's
   repository/snapshot namespace. Both arms must pass this measurement check;
   otherwise the pair fails. Blocked prerequisites remain explicitly blocked,
   with no invented attempts or contexts.

2. **The harness itself was absent from the stability gate.**
   `evaluate_models.source_identity()` includes production modules and its
   qualification driver, but excludes this new standalone script. Changing it
   during the pair previously left `source_identity_stable` true. The harness now
   captures its initial hash, compares it after each arm, records that initial
   hash and stops before another arm when source stability fails.

3. **Cleanup failure could leave the controller using an arm's database.**
   If the inherited driver raised after switching `db.DATA_DIR`, the harness
   restored the temporarily substituted qualification class but not the database.
   Its outer `finally` now restores both, including exceptions from driver cleanup.

4. **The standalone CLI did not load project configuration.**
   The CLI now calls `run.load_environment()` and applies `STEWARD_DATA_DIR` to
   the already imported database module before evaluation or worker startup.
   Tests use a real temporary literal `.env` and verify that explicit process
   environment wins. Importing the harness does not load `.env`.

## Connected boundaries checked

- Both setup arms register separate generic repositories from identical bytes,
  capture immutable snapshots and leave monitoring disabled. The advisory-only
  fixture lock is replaced by empty `requirements.txt`; no fixture provider is
  selected, and source behavior is not executed on the host.
- Worker startup uses its arm's workspace through `STEWARD_DATA_DIR` and the
  explicit `--worker keyword|graph` entrypoint. The retrieval wrapper preserves
  requested paths and other arguments while setting graph mode and disabling
  semantic retrieval. Contexts from every round remain in the output.
- The inherited driver requires live provider attempts, bounded calls/output/
  repairs/evidence rounds, passing baseline/candidate evidence, patch-bound
  checks and byte-for-byte source immutability. Its full raw artifact remains in
  the workspace's `qualification-result.json`; the paired report embeds its
  result, attempts, budget and evidence.
- The journey additionally requires a failing `regression_before` record. The
  reviewed holdout checks doubled quantity, default quantity, zero quantity,
  empty input and integer tax (`326` cents for three items at `99` cents).
- The holdout rechecks candidate hashes, transfers candidate bytes through the
  controlled dependency/sandbox adapter, passes its deadline cancellation token,
  and always cancels its owned run after execution failure. A focused test checks
  these seams without executing the supplied source or contacting Docker.
- Overall CLI success requires a complete valid pair. Partial reports cannot
  claim a pass, and unknown usage remains null through the inherited budget report.

## Validation

The original five tests passed. New reproductions then produced **8 failed,
6 passed** before the harness changes, covering missing mode evidence, wrong mode,
stability, database restoration and configuration. Additional positive/negative
tests cover namespace binding, blocked prerequisites, same-source workspace setup
and sandbox cleanup.

Final focused command:

```powershell
Set-Location C:/Users/ahmed/Projects/ducktape/steward/.worktrees/full-product
$env:PYTHONPATH='C:/Users/ahmed/Projects/ducktape/steward/vendor-current'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
& C:/Users/ahmed/Projects/ducktape/steward/.venv/Scripts/python.exe -m pytest tests/test_retrieval_task_evaluation.py tests/test_qualification_driver.py -q -p no:cacheprovider --junitxml=docs/evidence/retrieval-task-harness-review-rerun.xml
```

**32 passed**, comprising 20 harness and 12 existing driver tests. The final
[bounded rerun JUnit](retrieval-task-harness-review-rerun.xml) completed in
**2.86 seconds**, with an outer 45-second process timeout. One existing vendor
FastAPI/Starlette deprecation warning was emitted.

The [first green JUnit](retrieval-task-harness-review.xml) is also retained. It
reported 5,971.28 seconds, with 5,968.832 seconds attributed to the real setup test.
Filesystem timestamps placed that gap before the first source files were written;
the same test and complete focused command then ran promptly. The cause of this
timing outlier is unconfirmed; it is not evidence of a reproducible harness speed
regression, and no speculative optimization was made.

Implementation changes are limited to `scripts/evaluate_retrieval_tasks.py` and
`tests/test_retrieval_task_evaluation.py`. This report and the two JUnit files are
the review evidence.

Verdict: Ship the harness for its separately scheduled live pair.

Not checked: live provider task success, actual Docker holdout execution for this
Parcel pair, measured keyword/graph latency or quality differences. Passing
deterministic harness tests establishes none of those outcomes. A single fixed,
ordered pair will remain descriptive evidence, not a statistical model-quality
or retrieval-performance claim.
