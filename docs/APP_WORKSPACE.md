# Duck Tape app workspace

Branch: `feat/ui/repository-workspace`. This work extends the existing React/TypeScript/Vite frontend and FastAPI/SQLite worker application. Main and the separate Part C worktree remain untouched.

## Implementation checklist

- [x] Inspect existing UI, APIs and working tree; create an isolated local branch.
- [x] Preserve existing import, knowledge, source, job, diff and verification APIs.
- [x] Add durable repository addresses and explicit managed apply target validation.
- [x] Complete and verify landing, dashboard, repository, job, settings and gallery screens.
- [x] Review navigation, accessibility, responsiveness and backend integration independently.
- [x] Start and inspect the app locally; record actual URL and restart command.

## Decisions

- Browser routes use persisted `route_owner` and `route_name`, derived from Git origin or `demo/parcel` / `local/<name>`. Duplicate imports get stable suffixes. Renaming or refreshing a repository preserves its address.
- Routes: `/`, `/dashboard`, `/:owner/:repo`, `/:owner/:repo/codebase`, `/:owner/:repo/jobs/:id`, `/settings`, `/screens`. FastAPI serves the SPA for direct page reloads; unknown API routes remain errors.
- Application targets the existing managed local `steward/integration` branch. Its legacy internal name is retained for persisted-workflow compatibility. Approval remains bound to the exact revision; imported source and main are not changed.
- Reuse the real worker and evidence components. Offline sample scenarios remain labelled fixtures. No claim of live AI or successful checks is inferred from UI state.
- Part C is not merged as part of this navigation change.

## Run this local workspace

Setup from a fresh checkout: `python scripts/setup.py`. It creates `.venv`, installs the locked dependencies and builds the frontend.

For this isolated worktree, run in PowerShell:

```powershell
Set-Location C:\Users\ahmed\.paseo\worktrees\38qfwmxf\duck-tape-app-navigation
$env:STEWARD_DATA_DIR="$PWD/data/app-navigation"
.\.venv\Scripts\python.exe run.py --port 8770
```

Open <http://127.0.0.1:8770>. The supervisor starts the API and persistent worker together. The worktree's `.venv` and `data/app-navigation` are local ignored files; source and dependencies are described by the checked-in setup and lockfiles. Port 8770 avoids the existing main checkout's service.

## Demo walkthrough

1. Get started from `/`, then choose the `demo/parcel` repository or connect a ZIP/HTTPS repository from Dashboard.
2. Inspect Codebase → Understanding / Files / Knowledge, then return to Jobs.
3. Open a sample job and approve its plan. The fixture provider makes real isolated edits and runs the fixed sample checks.
4. Inspect Changes, Evidence and Runs. Team notes only persist for teammates; an AI revision request produces another checked patch.
5. Confirm a feature decision before dependent work resumes. Approve & apply names the managed local branch and applies only the current revision.
6. Open `/screens` for side-by-side interactive views. These use the same persisted local workspace.

## Verification and review

Verified on 14 November 2026 against this branch and an isolated workspace database.

The required deterministic acceptance tier passed all four gates: **558 backend tests, supervised worker/API restart recovery, production build and 25 browser fixture tests** (no fixture skips or failures). [Machine-readable acceptance report](evidence/app-navigation-acceptance.json). Live cloud/model acceptance remains separate and unverified here.

| Capability | Evidence / status |
| --- | --- |
| Backend regressions | Final deterministic backend gate: 558 passed. Initial broader collection also explicitly skipped 23 opt-in live tests. Focused route and acceptance-tool checks: 22 passed, including concurrent address allocation. |
| Real local application | API and supervised worker running on port 8770; restarted with the worktree's own installed Python environment. |
| Real import/browser workflow | Python and TypeScript ZIP imports, index inspection, scans and responsive light/dark checks passed against the actual API. |
| Offline sample execution | Real isolated Git diff, failing-before/passing-after checks, team-note isolation, second checked revision, managed apply, persisted reload and explicit feature decision/resume passed through Chrome. Fixture inference only. |
| Full sample browser acceptance | `npm run test:sample` passed against port 8770: patch/ZIP exports, immutable citations, Q&A, research intake and approved knowledge in addition to the patch lifecycle. Results: [`frontend/browser-results.json`](../frontend/browser-results.json). |
| Navigation | Independent review reran 12 navigation/workspace tests, including delayed discovery, wrong-parent rejection, clean URL reload/back and exact revision/target payloads. |
| Production frontend | TypeScript/Vite build passed. Rendered landing, dashboard, repository, Codebase, Settings and gallery inspected at desktop and 390px widths. |
| Live inference / cloud | Not qualified in this worktree. Provider health reports offline sample mode. Existing adapters/configuration remain available; this work does not claim live Hugging Face, Adaption, Atlas or Databricks execution. |

Review findings fixed: premature wrong-parent errors during initial route hydration; fixture options following the wrong repository; hidden mobile Settings; cramped gallery header; and health probes blocking every embedded preview. Settings diagnostics now load only on Settings, with explicit loading, error and retry states.

Independent final visual review found no outstanding issues: desktop and 390px layouts, source selection, dialogs, job reload/back, mobile Settings and all gallery frames passed without page errors or horizontal overflow. A local timing observation after the fix measured the dashboard at 182 ms and gallery dashboard/codebase previews at 572 ms, with one health request for the Settings frame; these are local observations, not a general performance guarantee.

Focused commands:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_app_routes.py tests/test_acceptance_script.py -q
$env:STEWARD_BROWSER_EXECUTABLE='C:/Program Files/Google/Chrome/Application/chrome.exe'
.\.venv\Scripts\python.exe scripts/acceptance.py --tier deterministic --output data/app-navigation/acceptance
```

Browser tests in `frontend/tests/workspace.spec.ts` use mocked transport; `frontend/tests/live-ui.spec.ts` opt-in import and sample cases use the real API/worker. The latter's inference is explicitly the offline fixture provider. Screenshots under `frontend/ux-evidence/navigation` are fixture UI evidence, not proof of live inference.

Browser auto-opening was unavailable in this environment: automatic approval review blocked the desktop launch, and no integrated browser host was connected. Installed Chrome automation verified rendering and interactions; open the URL above manually.
