# Verification and capability status

Completion work is verified on Windows with Python 3.13.12, Node 24 and Git 2.51. This report distinguishes actual execution, fixture inference and unavailable external capabilities. Earlier prototype reports in this directory are historical; this report and `docs/evidence/` describe the completion build.

The Duck Tape initial snapshot consolidates the implementation and later schema, runtime-startup and evaluation-harness fixes. The [frozen 3B baseline](evidence/model-qualification-baseline/README.md) ended with **2 passes, 8 model-quality failures and 2 externally incomplete cases**; it is not qualified. Later calibration has deterministic coverage but has not been requalified live. The [paired retrieval-to-task harness review](evidence/retrieval-task-harness-review.md) records 32 focused passes; its actual live pair remains unmeasured. Historical “in progress” milestones below retain their original scope and do not override this status. Older commit references predate the repository's fresh initial history.

## Current internal-stack foundation

The [14 November foundation verification](evidence/hackathon-foundation/README.md) records 541 passing collected backend tests, an actual worker/API restart, a passing production build and 16 browser contract tests. A separate final 112-test run covers the latest reviewed provider/data/API/startup changes, including three Atlas tests added after aggregate collection. This is an uncommitted foundation on the existing initial commit. Adaption/Hugging Face and cloud-data adapters are contract-tested; live sponsor accounts, Atlas worker wiring, Databricks cloud execution and new model qualification remain outstanding.

## Recorded gates

- Initial-publication [consolidated check](evidence/initial-snapshot/20261114T122726Z-21f968a8/report.json): **442 backend passes and one outdated runner-fixture failure**. The fixture was corrected while that run was already collected; its [complete four-test file subsequently passed](evidence/initial-snapshot/release-fixture-recheck.xml). Actual worker/API restart, production build and **15 browser contract tests passed**. The aggregate remains recorded as failed; no full rerun had been recorded at publication time; the current foundation run above is the later follow-up. The original failure and its scheduler cleanup warning are retained in the evidence.
- Latest installed [deterministic acceptance](evidence/acceptance-installed/20261114T052114Z-f5602a6c/report.json): **388 backend tests passed, zero skips**, actual worker/API restart passed, production build passed, and **15 browser contract tests passed** with no skips or flakes. An earlier installed run exposed one test that depended on a default model output budget; the test now sets its intended limits explicitly, and both the historical failure and independent rechecks are retained. Production behavior was unchanged by that correction.
- Backend integration after review: **353 non-live tests passed**, 18 opt-in tests skipped and 2 live retrieval tests deselected. The later startup regression and release-tool tests passed their focused gates. The reproducible deterministic acceptance command records the final aggregate separately.
- Sandbox/dependency verification: **37 passed**, including **9 actual Docker tests**. Python and Node probes, network/credential/socket denial, nonroot/read-only-root/capability limits, real memory/PID/disk exhaustion, timeout/cancellation, offline dependencies and regression execution were exercised. See [sandbox report](evidence/sandbox-report.md) and [verification report](evidence/verification-report.md).
- Public import/source/security regression: **74 passed** after generic HTTPS transport hardening. Actual [GitLab](evidence/public-git-generic-gitlab.json), [generic GitHub](evidence/public-git-generic-github.json) and [GitHub archive](evidence/public-git-archive.json) imports are recorded separately.
- Real worker kill, supervisor restart and full API restart: **passed**, preserving one checked sample revision and approved knowledge. [Exact process/check evidence](evidence/restart-check.json).
- Production build and **22 browser tests passed**, with zero skips, flakes or browser errors in the [integrated report](evidence/browser-final.json). The suite distinguishes mocked contracts from actual backend imports, sample decisions/revisions/checks/exports/application and settings. Desktop, laptop and narrow layouts were inspected in both themes. The first launch found an absent bundled browser; the recorded successful run uses installed Chrome through `STEWARD_BROWSER_EXECUTABLE`.
- Independent completion review: security **80 passed**, functional/AI **13 targeted regressions passed**, rendered UX **7 actual/labelled-fixture review journeys passed** plus twelve viewport/theme checks. Reproduced defects were fixed and rechecked; [security](evidence/security-completion-review.md), [functional/AI](evidence/functional-ai-completion-review.md) and [UX](evidence/ux-completion-review.md) reports retain their scope and earlier failures. [Fresh final acceptance](evidence/final-acceptance-review.md) closed all five findings and feature follow-ups with **60 focused passes and 2 independently executed Docker passes**. The lead's final [Docker suite](evidence/sandbox-final.xml) passed **11 actual tests**.
- Release tooling: [36 independent focused checks passed](evidence/release-tooling-review.md). The first [reproducible deterministic acceptance run](evidence/acceptance-deterministic/20261114T043436Z-53bd41f2/report.json) passed 376 backend tests, a real restart, production build and 13 fixture browser tests. Later candidate-export, cache-retry and framework-reporting fixes have focused regressions; the final integrated run is recorded separately.
- [Actual health probes](evidence/doctor-live.json) confirm the configured local open-weight endpoint and Linux Docker sandbox. A restricted tool process correctly reports unavailable Docker permission; the app is launched with the environment's permitted runtime access, without changing security settings.
- Actual feature-target verification passed **3 framework cases** using pytest 8.3.5, Jest 29.7.0 and Vitest 2.1.9 in constrained Docker. Each executes a passing selected target and rejects a skipped target and a similarly named substitute. Exact locks, counts, commands and logs are retained in the [framework report](evidence/feature-frameworks/README.md).
- The original workspace upgrade preserved existing records after a verified offline backup. The [installed application browser journey](evidence/installed-browser/REPORT.md) passed actual sample preparation, failing-before/passing-after checks, revision, note privacy, patch download, managed application and feature decision; desktop, narrow and dark screenshots were inspected. [Installed health](evidence/doctor-installed.json) confirms the API/worker/provider/sandbox. These are sample-fixture edits, not live-model qualification.

## Capability table

| Capability | Status | Evidence and limit |
|---|---|---|
| Persistent jobs, legal transitions, preparation approval, retry and cancellation | Verified real backend | SQLite transactions, lease generations, guarded publication and actual worker restart |
| Sample ticket → regression → patch → revision → managed application | Verified offline sample | Fixture inference, real Git files/diffs and failing-before/passing-after checks |
| Generic live Python pipeline | **Verified live** | [Actual model attempts, source, checks and review](evidence/live-pipeline-python.json); no fixture provider |
| Generic TypeScript pipeline and 12-task model qualification | **Not qualified** | Recorded baseline: 2 passes, 8 model-quality failures, 2 externally incomplete; post-calibration live rerun outstanding |
| ZIP/public HTTPS Git ingestion and immutable refresh | Verified real | GitLab and GitHub live imports; traversal/symlink/SSRF tests; bounded history and unavailable submodule gaps |
| Repository graph, source explorer and retrieval | Verified parser/graph tests | Python AST, JS/TS Tree-sitter, fixed independently labelled tasks; dynamic relationships remain incomplete |
| Repository Q&A | Verified live helper smoke; cross-module synthesis fell back | Helper citation validated (968 input / 69 output tokens). The [cross-module attempt](evidence/live-cross-module-qa.json) retrieved all four required files but rejected an out-of-range model citation; structural evidence remained available. Invalid cached synthesis is evicted so retry can request a fresh answer. |
| Hierarchical summaries | Verified live repository/module smoke | [Actual synthesis and source citations](evidence/live-summaries.json); package grouping and fallback contract-tested |
| Embeddings | Verified live local inference | [Nomic 768-dimensional vectors, cache and reported usage](evidence/live-embeddings.json); [actual retrieval comparison](evidence/retrieval-semantic-live.json) shows no recall gain over graph on the four fixed tasks |
| Versioned knowledge, explicit promotion, note privacy | Verified real backend/browser | Exact reviewed version/snapshot approval; scoped stale/conflicting policy; notes excluded from prompts |
| Signals, advisory deduplication and monitoring | Verified backend/live query | [Actual OSV response](evidence/osv-live.json), [before/after advisory status](evidence/security-version-verification.json), supported dependency scans, persisted polling; version match is not exploitability |
| Managed application and exports | Verified actual Git | Approved bytes/checks/input freshness, crash reconciliation, source preserved, deleted/renamed files and collision-safe ZIP manifest |
| Arbitrary imported/live-generated code execution | Verified constrained Docker | No host execution, network or credentials inside checks; unsupported environments remain analysis/export only |
| Performance proposal | Verified actual Docker benchmark | [Generic before/after workload and tampered-harness rejection](evidence/generic-performance-review.md), plus reviewed sample scenario; no general gain claimed |
| Authentication and deployment | Intentionally local | Demo authors are labels; loopback single workspace, no remote pushes or public deployment |

## Reproduce checks

From an installed checkout:

```powershell
.venv/Scripts/python -m pytest -m "not live" -q
.venv/Scripts/python scripts/check_restart.py
.venv/Scripts/python scripts/doctor.py --require-live
cd frontend
npm run build
npm run test:browser
```

Set `STEWARD_LIVE_SANDBOX=1` to enable actual Docker tests in `tests/live/`; a requested live check fails visibly when its runtime is unavailable. Provider live tests require the corresponding explicit environment switches in their test modules. The generic model qualification command is:

```powershell
.venv/Scripts/python scripts/evaluate_models.py --driver scripts.qualification_driver:driver --output docs/evidence/model-qualification.json
```

The fixed 12-task corpus starts from independent Python/TypeScript repositories, uses real provider calls and workers, and checks expected behavior inside Docker. Its attempts, latency, available token usage and blockers are retained. `--smoke` is a smaller schema/edit/review probe and never counts as application qualification.

For real browser journeys, run the app and set `STEWARD_TEST_URL` to its loopback URL and `STEWARD_LIVE_UI=1`; fixture tests remain labelled separately. `frontend/browser-test.mjs` creates a fresh sample and verifies backend records as well as controls. Live inference calls should run sequentially on the small CPU runner to avoid queue contention.

## Practical limits

Only supported locked dependency environments execute automatically. Native source builds, lifecycle scripts, custom registries and unfamiliar commands remain explicit blockers. Static test-preservation checks and model review add evidence; they do not prove tests cannot be deceptive. Secret detection is heuristic. External service availability can change. The local 3B CPU model has demonstrated failures as well as successful patches; no universal coding reliability or semantic-understanding percentage is claimed.
