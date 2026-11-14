# Independent release tooling review

What this change does: Steward now has an explicit acceptance command with separate deterministic, sandbox and live gates, plus offline workspace backup, restore and conservative artifact cleanup. This review traced the scripts into their startup, persistence, CI and documentation callers. It assumed the documented local single-workspace deployment.

## Evidence

- Independently ran `tests/test_acceptance_script.py`, `tests/test_maintenance.py` and `tests/test_startup_settings.py`: **33 passed**.
- Added `tests/test_release_tooling_review.py` with an actual child-process timeout probe, interrupted-gate accounting, and an isolated runner lifecycle/reuse stub. The combined focused suite finished **36 passed in 7.54 seconds** under normal local Windows process permissions.
- No coding-model request, model shutdown, whole-suite run or browser run was made by this reviewer. The separate live qualification gate was left undisturbed.

## Findings and disposition

1. **Shared model runner lifecycle needed an explicit handoff contract (P2 documentation, fixed and rechecked).** `backend/providers.py` starts the installed runner without attaching its lifetime to an API instance. The API lifespan shutdown leaves it running. An independent stub reproduced this; a subsequent healthy API startup reused the same service without spawning another. The lead confirmed this is deliberate because multiple local apps share the endpoint. The updated configuration guide explicitly describes persistence, identifying listening PIDs from configured ports, verifying their project-local executable paths, and stopping only those verified processes after all consumers stop. I reread that change and closed the finding. Automatically killing an already healthy external/shared provider would be incorrect. This is not evidence of worker-process cleanup failure.

2. **Restricted tool process permissions initially blocked the timeout probe (environment limitation, not a product defect).** An initial test inside the tool sandbox could not use Windows `taskkill`/process inspection and left its test child alive. Only processes containing that test's unique temporary path were identified and stopped with approved normal local permissions. The exact timeout probe then passed with those permissions. The test has a stop sentinel and a finite child lifetime for failure cleanup. Acceptance subprocess cleanup requires the operating system to permit termination of its own process tree; no security setting was changed.

No reproduced critical or major defect was found in the reviewed release scripts. No production code was changed by this review.

## Reviewed boundaries

- Backup reserves a database write lock, refuses active leases/recent running services, captures committed WAL pages through SQLite backup, and copies workspace evidence with hashes. It refuses an existing destination, nested backup destination, links/junctions and unreadable paths. Its documented prerequisite remains important: keep the API/worker and unrelated file writers stopped throughout the operation.
- Restore validates inventory, paths, hashes and SQLite integrity before copying, refuses overwrite/relocation, and preserves the original absolute evidence references. Failed copies retain the backup and may leave an incomplete new target, as documented.
- Cleanup defaults to dry-run and only selects recognized old ingestion ZIP/scratch artifacts. References in both records and work items protect candidates; revisions, worktrees, snapshots and evidence are retained. The documented absence of a global storage quota is accurate.
- Acceptance gives required blocked/failed gates nonzero results, distinguishes process exit from machine-readable evidence, rejects empty/skipped required live suites, and does not count a healthy provider as model qualification. CI uses only the deterministic tier. This review did not execute GitHub Actions remotely.
- `README.md`, `scripts/setup.py`, `run.py` and `.github/workflows/check.yml` agree on the locked setup/build and supervised API/worker entrypoint. Browser installation remains an explicit test prerequisite. The `STEWARD_DATA_DIR` configuration paragraph correctly discloses Windows path-length failures and `STEWARD_QUALIFICATION_ROOT`; the workaround does not enable Windows/Git security settings or call an infrastructure failure a model pass.
- Reviewed the added `CandidatePreview.tsx`, its `main.tsx` selection branch, `DiffViewer.tsx`, and the worker's persisted candidate fields. An unverified candidate exposes its actual captured diff, base/patch identity and a Blob patch export. It does not add an approve/apply path. React/highlight escaping remains in use. This was source review; the lead owns its browser download check.

Verdict: Ship the reviewed release-tool behavior within its documented scope. The documentation finding is closed, and no critical or major finding remains from this review. Full live product qualification remains a separate gate.

Not checked: remote CI, destructive recovery after physical disk failure, and live-provider quality. Retained history/model artifacts can continue consuming disk; cleanup intentionally does not expire review evidence.

## Final-delta addendum

Independently reread the final candidate persistence/export, rejected-citation cache eviction, Jest selected-test observation/count parsing, and bounded npm cache batching changes and their callers. Ran only the associated deterministic suites (`test_chunk_retrieval`, `test_summaries`, `test_provider_contracts`, `test_feature_framework_observation`, `test_verification`, `test_dependencies`, `test_live_pipeline_contract`): **88 passed in 118.99 seconds**. No live inference, Docker workload, browser run or process termination was performed for this addendum.

The early candidate save now precedes verification and preserves real diff/files/hash on a sandbox blocker; it does not create a verified revision or grant approval. The UI also exposes a failed candidate on Runs when an older verified revision exists. Citation rejection compares and atomically evicts the exact cached response, preserving a concurrently replaced value. Jest observation requires the exact selected passing label while allowing unselected tests to be skipped; count parsing uses test totals rather than suite totals. npm cache batching keeps verified artifact identity, fixed argv, offline installation and ignored scripts unchanged.

One additional P2 cache defect was reproduced through the actual retrieval path: repeated Q&A with identical repository, snapshot, question and evidence called the provider again because `context.latency_ms` changed. The existing cache fixture froze that field. `test_repeated_qa_reuses_cache_when_only_retrieval_latency_changes` in the independent review test file initially failed with two stub provider calls instead of one. The fix excludes only runtime retrieval latency from the model-facing context in both typed and legacy Q&A calls while keeping telemetry in the returned context. I reread the shared fix and independently reran the new regression plus retrieval, summaries and provider contracts: **47 passed in 8.66 seconds**. The finding is closed; no other material issue was reproduced in the final deltas.

Addendum verdict: the reviewed final deltas are ready within their supported scope. Actual framework execution, browser journeys, full integrated acceptance and model qualification remain in the lead's separate evidence; these deterministic results do not replace them.

## Installed-environment budget test correction

The installed deterministic run recorded **387 passed and one failed** in `acceptance-installed/20261114T051133Z-83366ec6/backend.log`; that original failure report is preserved unchanged. I reproduced the single failure with `STEWARD_MAX_OUTPUT_TOKENS=1536`, using the test's stub transport only. The test expected a context rejection but had implicitly depended on the default 4096-token output reservation. The installed 1536-token reservation legitimately allowed its small request to fit.

The test-only fix explicitly sets the implementation role's context and output limits to 4096, overriding inherited settings for those intended assumptions. Production code, provider configuration and the running live qualification gate were unchanged. Independent focused rechecks: **31 provider contract tests passed in 5.81 seconds** with the installed 1536 setting, and **31 passed in 5.65 seconds** with the global output setting absent. No live provider was called.
