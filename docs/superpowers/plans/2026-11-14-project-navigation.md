# Project navigation implementation plan

**Goal:** Rename the workspace's repositories to Projects and show engineering navigation scoped to the selected project.

**Architecture:** Reuse existing jobs, investigations, review evidence, codebase, knowledge and activity data. Keep the workspace navigation separate from a new project sidebar. Preserve backend repository identifiers and APIs.

**Stack:** Existing React, TypeScript, CSS, Lucide and Playwright.

**Scope:** User-requested project terminology and contextual sidebar. Preserve the black theme and white logo. Real hosted PR metadata is outside the existing integration.

1. Add focused browser coverage for project-scoped queues, switching projects, review shortcuts and direct-route reloads. Update existing label selectors.
2. Rename workspace labels in `WorkspaceNav.tsx` and `main.tsx`. Add `ProjectNav.tsx` with project switcher, direct Jobs and Investigations links with in-page filters, Reviews, Codebase, Knowledge, Activity and current-job shortcuts. Extend existing route parsing and scoped lists; retain existing actions and APIs.
3. Run the production build, affected fixture browser tests and local desktop/mobile checks. Verify empty projects, cross-project isolation, back/reload navigation, job review controls and keyboard-accessible navigation. Report live integration checks separately.

## Verification

Implemented in the existing workspace checkout. `npm --prefix frontend run build` passed. From `frontend`, with `STEWARD_TEST_URL=http://127.0.0.1:4177` and the installed Chrome executable, `npx playwright test --workers=1` passed 29 fixture tests; 10 opt-in checks were skipped. Local read-only Chrome checks covered the project sidebar, review evidence shortcuts and the mobile Jobs dropdown without overflow. Backend tests and live import/approval workflows were not rerun. Hosted pull-request metadata remains unconnected.

Follow-up refinement: removed the project Jobs/Investigations dropdowns and styled 6px rounded scrollbars (thin fallback outside WebKit-style scrollbar support). Research proposals remain available within Investigations. Production build and eight focused browser tests passed; local Chrome checks confirmed scrollability and no mobile overflow.


## Knowledge map and visual cleanup

Workspace ownership: main.tsx, style.css, KnowledgeGraph.tsx, ThemePicker.tsx and affected frontend tests. Backend and Landing unchanged.

Knowledge opens an interactive file graph of existing indexed imports/calls/tests. Search includes neighbouring files; relationship filters, keyboard selection, pan/zoom and source links work locally. Decisions preserve the existing version/snapshot approval flow. Graphify supplies visual inspiration only; no Graphify engine is installed or invoked. Visible graphs are capped at 250 files with search and an explicit limit notice.

Removed redundant project headers/navigation, consolidated optional job filters, and added Black/Forest/Midnight/Violet/Light. User corrected the initial full-surface tint: all dark themes now retain black/charcoal surfaces and change accents only.

Validation: `npm --prefix frontend run build` passed; full fixture browser suite (`STEWARD_TEST_URL=http://127.0.0.1:4177`, local Chrome, `npx playwright test --workers=1`) 32 passed, 10 opt-in live tests skipped. After neutral-surface correction, knowledge-map and navigation specs: 8 passed. Read-only local backend browser check: 8 files/8 edges in demo/parcel, source navigation works, no page errors or 390px overflow. No provider or sandbox mutation tests run.


## SWE UI review and concise workspace

Review findings: manual New entry did not match the supplied-knowledge workflow; job review navigation appeared twice; queue pages repeated headings; Settings mixed unrelated setup tasks; static explanatory copy competed with code and check results.

Changes: remove the blank knowledge creation action/form branch while preserving existing decision review/history/editing. Keep project navigation in the sidebar and job tabs in the main pane; label execution evidence Checks. Place Codebase Overview/Files tabs after the page heading. Split Settings into Providers, Automation, Execution and General without discarding unsaved provider fields. Use compact project rows, shorten static instructions, remove the unavailable UI preview placeholder, and preserve dynamic source gaps, errors, exact-revision approvals and privacy/credential boundaries. Landing and backend are unchanged.

Browser review: actual local Dashboard, Codebase, Knowledge and Settings rendered with one page heading, no desktop overflow or page errors; Settings also fits 390px. The actual provider timeout remains visible. Future recommendation: source branch, commit and CI metadata on review rows once real PR data is available; no placeholder integrations added.

Final validation: `npm --prefix frontend run build` passed without CSS warnings. `STEWARD_TEST_URL=http://127.0.0.1:4177` with local Chrome, `npm --prefix frontend run test:browser -- --workers=1`: 35 passed, 10 opt-in live tests skipped. `git diff --check` passed. Generated tracked screenshots restored; new visual inspection captures remain in ignored data/.


## Demo repository, GitHub dark and live pull requests

Frontend ownership: main.tsx, api.ts, style.css, ProjectNav.tsx, ThemePicker.tsx, new Reviews.tsx/github.ts/demo.ts and affected browser tests. No backend or Landing changes and no dependencies added.

- Explicit `?demo=1` opens a fictional northstar/commerce-api workspace, with code graph, maintenance jobs and four illustrative PRs. A persistent demo banner and read-only API adapter prevent real project reads or mutations. Existing `?preview=1` screen-gallery embeds continue using actual project data.
- Default dark uses GitHub-style charcoal surfaces, blue links, green primary actions, semantic check colours and coloured diff syntax. Other dark accent choices and light mode remain available.
- Reviews separates GitHub Pull requests from local Agent changes. Public GitHub imports use their recorded origin; ZIP/local projects can associate a validated public GitHub URL, stored only in browser localStorage per project. Five PRs per page; explicit refresh, pagination, source/refresh time and external PR/commit links.
- Read-only browser requests use fixed api.github.com endpoints with credentials omitted and no referrer. No private repo authentication or tokens. PR head/base refs and SHA come from GitHub. CI combines checks and commit statuses for that exact SHA, validates response identity, and reports failed/running/skipped/none/partial/unavailable states without inventing success. Required branch protection and mergeability are not evaluated.
- Live verification: a disposable Chrome context loaded vitejs/vite through the actual application; the PR list and all ten CI requests returned HTTP 200. Five real PRs showed their GitHub head refs/SHAs and passing/failing recorded checks. No GitHub writes or existing browser settings were changed. Demo desktop and 390px views had no horizontal overflow or page errors.

Final validation: `npm --prefix frontend run build` passed. With `STEWARD_TEST_URL=http://127.0.0.1:4177` and `STEWARD_BROWSER_EXECUTABLE=C:/Program Files/Google/Chrome/Application/chrome.exe`, `npm --prefix frontend run test:browser -- --workers=1` completed with 46 passed, 10 opt-in live workflow checks skipped and one pagination fixture failure. The fixture needed GitHub's `Access-Control-Expose-Headers: Link` header; after correcting it, `npm --prefix frontend run test:browser -- tests/reviews.spec.ts --workers=1` passed all 12 tests, including pagination. The full suite was not rerun after that fixture-only correction. Generated tracked screenshots restored. Backend/provider/sandbox workflows were not rerun. Live public GitHub verification is reported separately above.
