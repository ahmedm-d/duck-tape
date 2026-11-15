# Project overview and review clarity

Workspace claims main.tsx, style.css, components/ProjectNav.tsx, WorkspaceNav.tsx, DashboardCharts.tsx, JobList.tsx, DiffViewer.tsx, new ProjectOverview.tsx, Investigation.tsx, demo.ts and affected frontend tests. No backend, dependencies or Landing changes. Preserve all existing work.

Implement project Overview at /owner/project/overview and use it when opening projects; keep existing base Jobs links compatible. Add /projects as a searchable all-projects list, reached by the Projects word in the sidebar; separate arrow expands project shortcuts. Dashboard shows at most five recent projects, ordered by local visits then recorded project/job activity. Both overview/dashboard reuse charts with all-time/7/30/90-day received-date cohorts; labels disclose current status, not historical transitions. Queue drill-down carries the date filter.

Use readable shared status/priority/source/type badges and an icon tile/list control at the Jobs heading's top right. Style all app select controls and supported native pickers with an accessible fallback. Tighten the wordmark/icon spacing.

Add an Investigation job tab and deep link at /owner/project/jobs/id/investigation. Link job cards to it. Show priority/risk meanings separately from recorded rationale; never fabricate a missing assessment. Prefer saved job investigation or latest recorded run investigation, preserve missing evidence and snapshot-bound citations, and link validated original source URLs. Demo-only explanations are explicitly fictional.

Add a full-file chooser/button to the shared diff viewer and a native modal for exact captured before/after content, including added/deleted/binary cases. Retain coloured aligned diff rows and synchronized scrolling.

Verify routing/reloads/isolation, recent-project limits, chart date boundaries and drill-down, readable badges and view controls, investigation provenance, full-file fidelity/modal keyboard access, dropdown keyboard use, desktop/mobile layouts, production build and affected browser suites.


## Verification and handoff

Implemented all eight UI areas above. Overview counts successfully indexed files only; failed, excluded and unsupported entries are excluded. The separate reviewer found that count issue, which was fixed with a mixed-status regression fixture; no further actionable findings.

- `npm --prefix frontend run build`: passed after the final correction (TypeScript and Vite).
- With `STEWARD_TEST_URL=http://127.0.0.1:4177` and `STEWARD_BROWSER_EXECUTABLE=C:/Program Files/Google/Chrome/Application/chrome.exe`, `npm --prefix frontend run test:browser -- --workers=1`: 60 passed, 10 opt-in live tests skipped.
- After the indexed-file correction, `npm --prefix frontend run test:browser -- tests/project-overview.spec.ts --workers=1`: 8 passed, including the new coverage-count regression. The full suite preceded that final scoped correction.
- Fixture browser checks cover scoped overview and investigation routes/reloads, five-project cap and all-project search, timeline boundaries and keyboard selection, period drilldown, saved run provenance and stale snapshot warnings, safe external links, exact before/after/empty/deleted/binary full-file content, Escape and restored focus. Existing diff alignment and synchronized-scroll regressions passed in the full suite.
- Separate read-only live-backend smoke: dashboard displayed five recent projects; /demo/parcel-6/overview displayed real scoped metrics; existing job ad988e545688d30199bfbf1c1bc19885c61e315aba40dba80a6f8dfee292ffa8 opened captured pricing.py (161 characters), and split mode rendered six panes without browser errors. No live writes or provider calls performed.
- Desktop (1440px) and mobile (390px) preview inspections: no horizontal overflow or page errors. Inspected screenshots in ignored data/. Native styled select picker and keyboard selection verified in installed Chrome.
- `git diff --check`: passed. Generated tracked navigation screenshots restored; prior source changes preserved. No commit/push/deployment performed.

Limitations: timeline shows current status for jobs received in a selected period, not historical status transitions. Missing priority/risk reasons remain explicitly absent; this UI does not generate retrospective assessments. Demo reasons are labelled fictional. Native popup styling falls back to styled closed controls on browsers without base-select support. Opt-in live mutation/provider workflows and other browsers were not rerun. Backend contracts are unchanged. Shared-file claim released for review/handoff; uncommitted changes remain in this checkout.
