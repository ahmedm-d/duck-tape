# Backend demo completion

## Goal and decisions

Prepare Duck Tape's existing backend for a real GitHub import, repository and public security knowledge, sourced jobs, and a checked patch once the currently training Adaption models can be served. Frontend belongs to the teammate. Training is underway per the operator; checked-in training manifests are historical, not live status.

Extend existing FastAPI/SQLite/worker boundaries. Do not replace the workflow database with Atlas or duplicate B/C graphs. The later B integration request supersedes opt-in graph projection: private Atlas is now required by default; public catalogue processing stays free of repository source. Downloaded LoRA artifacts still need a compatible base model and inference server; a download alone is not an inference endpoint. See [Atlas integration gates](ATLAS_INTEGRATION.md).

## Plan and ownership

- [x] Fix verification file-mode loss across baseline, regression and candidate (execution subagent; verification and tests).
- [x] Fix unresolved dependency uncertainty and revoked Atlas policy reconciliation (lead; applicability/atlas_graph and tests).
- [x] Connect public catalogue matching to existing persistent scan operations, with explicit repository controls, cancellation checks and source policy enforcement (lead; main and tests).
- [x] Expose bounded local repository + security evidence for frontend graph integration; preserve citations/snapshot scope and unknown exploitability (lead; main and tests).
- [x] Exercise public GitHub ingestion through the existing operation queue and graph/context APIs; verify duplicate requests, refresh and bad-input boundaries.
- [x] Write frontend API handoff and exact MongoDB/Databricks/model setup instructions using current interfaces.
- [x] Run focused regressions and integrated backend tests; independent review where agent capacity allows; record live versus fixture evidence.

## Release checks

Public GitHub import must finish with real commit/snapshot identity. Both graphs must derive from indexed/source records. Repeating scans must preserve job deduplication and rejected decisions. Catalogue failures must remain visible; absence of catalogue records is never a clean security bill. Cloud calls follow the accepted required-Atlas policy and explicit configuration for other providers. Cancelling scan must prevent subsequent intake. Stale snapshot and catalogue changes must invalidate approvals. Source notes remain outside model evidence.

## External gate

Actual trained-model inference and held-out qualification remain pending model completion, serving resources and credentials. Existing fixture/transport tests cannot qualify those artifacts. Databricks direct Atlas publication and schedule need operator verification; local public-catalogue fallback remains usable.

## Evidence and log

Started from origin/main f53bbae in isolated feat/repo/backend-demo. Existing B/C code is on this baseline. Audit workspace and teammate frontend work remain untouched. No rewrite or reset of Git history.


## Verification (14 November 2026)

- Full backend suite before final review refinements: **674 passed, 1 skipped** (optional Graphify CLI absent), 303 seconds. Final changed boundaries were rerun separately after review.
- Final focused integration/workflow/verification suite: **93 passed**, recorded in `data/backend-final-focused.xml`, after review fixes.
- Real Docker sandbox gate: **9 passed**. Additional live executable-helper regression: **1 passed**, executing `./run.sh` in baseline, regression-before and candidate containers.
- Actual public GitHub import: `pallets/itsdangerous`, commit `672971d66a2ef9f85151e53283113f33d642dabd`; 44 indexed, 6 unsupported, 0 failed files. Built-in graph: 188 nodes, 228 relationships. Repeated idempotency key returned the same import operation.
- Actual public OSV HTTPS ingestion of GHSA-9wx4-h78v-vm56. A labelled uploaded fixture pinned requests 2.31.0; its local graph linked the cited manifest to CVE-2024-35195. Two real queued scans produced one approval-gated job. Imported fixture code was not executed in this flow.
- Actual process recovery passed: killed the worker during Investigating, observed supervisor replacement and lease recovery, then restarted the API. One fixture-provider patch revision, failing-before/passing-after checks and approved knowledge persisted. Evidence: `data/backend-restart-evidence.json`; historical checked-in evidence was preserved.
- Independent review found cancellation, stale revision/snapshot, cross-repository invalidation and invalid-input ordering defects; fixed and regression-tested. Final reviewer accepted the documented demo scope; 11 race tests passed.
- No trained Adaption model inference or new cloud-account run performed. Current checkout has no configured served model/credential. Existing Databricks/Atlas evidence remains historical. No frontend implementation or browser UI claim in this backend task.
- Existing Starlette/httpx deprecation warning remains. Source-linked local documentation links and `git diff --check` pass.

## Remaining operator/frontend work

Follow [BACKEND_DEMO_HANDOFF.md](BACKEND_DEMO_HANDOFF.md): connect the teammate's frontend to these APIs; configure Atlas reader/publisher and Databricks relay if demonstrating cloud processing; finish training, serve compatible model aliases, then run live schema and application qualification. A downloaded LoRA checkpoint alone does not make live inference available. Private GitHub OAuth and hosted multiuser authentication remain outside this local/public-import demo.
