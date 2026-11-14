# UX completion review

What this change does: Steward exposes indexed source, maintenance jobs, checked patch revisions, explicit approvals, team knowledge and local execution capabilities. This fresh review exercised the rendered application against the dedicated preview at `http://127.0.0.1:8767`, using its isolated `data/integration-preview` data and installed Chromium 1248. The original app on 8765 was not used.

Three reproducible P2 issues were found. All three were fixed after the lead assigned the narrow frontend changes. No remaining blocking UX defect was reproduced in the reviewed flows.

## Findings and fixes

1. **P2 — Older patches appeared approved when only the latest revision was approved** (`frontend/src/main.tsx:51`). Approve revision 2, then select revision 1. The summary previously said `Revision 1 approved`, although the actual approval record remained bound to revision 2. The summary now resolves its number from `approval.revision_id`; the existing exact-revision apply restriction remains in force. A correct-behavior browser assertion failed before the fix and passed afterward. Before: [approval label](../../frontend/ux-evidence/completion-review/finding-approved-revision-mislabel.png). After: [revision 1 selected, revision 2 approved](../../frontend/ux-evidence/completion-review/approval-label-regression.png).

2. **P2 — Import errors and progress controls were hidden behind the import dialog** (`frontend/src/main.tsx:65`, `frontend/src/main.tsx:68`). Upload a file named `invalid-review.zip` containing plain text. The operation correctly failed with `Invalid ZIP archive`, but its error/retry controls and the global alert were behind the still-open modal. Accepted imports now close the modal immediately so the existing operation progress and cancel/retry controls can be used. Rejected requests keep their form open with an alert inside the dialog. The actual invalid-archive regression failed before and passed after; a separate labelled API-fixture case verifies pre-acceptance validation and cancellation. Before: [hidden error](../../frontend/ux-evidence/completion-review/finding-import-error-behind-dialog.png). After: [visible error and retry](../../frontend/ux-evidence/completion-review/import-error-after-closing-dialog.png).

3. **P2 — Activity opened jobs in the previously selected repository context** (`frontend/src/main.tsx:38`). Select imported `browser-python`, open Activity, then open an event for a sample job. The job body was correct but the sidebar and hash still named `browser-python`, so later source/knowledge navigation used the wrong repository. `openJob` now uses the existing job summary to recover its repository when the caller supplies only a job ID. The actual browser regression failed with the wrong repository hash before the fix and passed afterward. Before: [wrong repository](../../frontend/ux-evidence/completion-review/finding-activity-wrong-repository.png). After: [correct repository](../../frontend/ux-evidence/completion-review/activity-repository-context.png).

## Actual assertions and evidence

- Existing imported Python source renders its real `calc.py` content, inventory/coverage disclosure, and dependency edges from the backend. Source and completed-job pages were checked at 1440, 1100 and 390 pixels in both light and dark themes. All twelve viewport/theme assertions reported no page-level horizontal overflow; source snapshots also assert the sidebar starts at viewport y=0. Diff content remains locally scrollable at narrow widths. Inspected desktop, laptop and narrow screenshots directly.
- The fresh sample bug required preparation approval, produced a real Git diff and downloadable patch/changed-files ZIP, recorded a failing regression against the baseline and successful candidate checks, and exposed captured evidence. Keyboard Tab stayed inside the evidence dialog; Escape closed it and restored focus to its opener.
- A team note remained outside revision context. A requested second revision invalidated approval. Revision 2 was explicitly approved and applied to the managed branch. The backend reported `Completed`. [Sample records](../../frontend/ux-evidence/completion-review/sample-results.json) preserve the repository/job IDs, exact approval revision, provider mode and check results.
- The sample feature stopped for a product decision and prepared revision 1 after selection. A new knowledge entry was approved by exact version, the recorded validated snapshot matched the repository snapshot, and version history opened correctly.
- Job source/type/priority/search filters selected the intended job; its direct route survived reload. Existing contract tests also cover browser back, risk filtering, exact source lines, stale answers, pending-answer cancellation on repository changes, and avoiding repeated full-source/job downloads.
- Settings saved the unchanged effective nonsecret provider configuration and disclosed environment overrides. Health displayed the installed model, qualification separately from connection, and sandbox availability. No provider inference was requested by this review.
- An actual invalid ZIP import was rejected and its retry control exercised. Cancellation and synchronous import validation use clearly labelled API fixtures to avoid manufacturing a slow or destructive backend operation.
- No browser runtime errors were recorded in the actual source/settings or full sample bug cases.

Screenshots are under [frontend/ux-evidence/completion-review](../../frontend/ux-evidence/completion-review). Filenames identify the page, width and theme. Deliberate failed-upload banners in later captures are persisted test evidence, not failed sample jobs. Current screenshots explicitly scroll to the top before full-page capture; the original approval-label screenshot's displaced fixed sidebar is a capture artifact.

## Validation

- `npm run build`: passed after the three fixes (344.15 kB JavaScript, 105.07 kB gzip at that checkpoint).
- `STEWARD_COMPLETION_REVIEW=1`, `STEWARD_TEST_URL=http://127.0.0.1:8767`, installed Chromium executable; `npx playwright test tests/completion-review.spec.ts --workers=1 --reporter=line`: **7 passed, 41.8 seconds**.
- Added job viewport/theme assertions, then reran the approval-label case: **1 passed, 8.6 seconds**.
- Existing default browser suite against the same built app: **13 passed, 9 skipped, 19.9 seconds**. The skips are the seven dedicated-preview review cases and two older opt-in live-backend cases, not acceptance evidence.

The lead subsequently assigned the backend-supported optional `embedding_endpoint` Settings field. It now uses the existing provider form and save payload, defaults to the effective configured value, and explains that an empty value shares the main endpoint using the selected protocol. The nonsecret save-contract test was observed failing while the field was absent, then passed after adding it: **1 passed, 2.6 seconds**. This check used an API fixture; it did not change the active preview's provider environment or call an embedding model. The final build passed at **344.47 kB JavaScript, 105.17 kB gzip**. No dependency or stylesheet change was needed.

Verdict: the three reproduced UX defects are fixed and verified. The reviewed source, sample, knowledge and settings workflows are usable across the tested widths and themes.

Not checked: live model inference, repository Q&A generation, bulk semantic summary generation, cross-browser behavior, or a full assistive-technology audit. The sample edits are explicitly labelled offline fixtures with real execution/check evidence; they are not live AI acceptance. Backend security and worker completion were reviewed by other agents.
