# Landing redesign

## Design and ownership

User brief: Adaption-inspired spacing and floating navigation, black and blue,
only “Duck Tape is on Duck Tape” in the hero, the supplied duck video at the
bottom of the first screen, then the existing workspace preview, a full job
flow, example benchmark cards, and a small footer.

Keep the existing React page and `/dashboard?preview=1` iframe. A separate
marketing application or recreated dashboard would add maintenance without
helping this brief. All styles stay within the landing page. The video uses a
blue CSS tint; the original upload is preserved. Metrics are illustrative,
explicitly labelled, and make no live model qualification claims.

This branch owns `frontend/src/landing/**`, `frontend/public/landing/**`, and
`frontend/tests/landing/**` under `docs/FRONTEND_PARALLEL.md`. There are no
shared shell, API, configuration, dependency, or backend changes.

## Implementation plan

Goal: deliver the requested scroll sequence with a usable, accessible preview.
Architecture: `Landing.tsx` composes the existing iframe and small presentational
sections; `landing.css` owns the black/blue palette and responsive layout.
Stack: existing React, TypeScript, Lucide, native video, and Playwright.

- [x] Add focused landing browser coverage and confirm it fails on the old page.
- [x] Replace the hero, add uploaded media/poster and motion controls, retain
  the workspace preview, and render the connected flow and sample metric cards.
- [x] Run the production build and landing browser tests, inspect desktop and
  mobile screenshots, and independently review the diff.

Review focus: reduced motion (including preference changes), rejected autoplay,
missing video, narrow-screen overflow, section-anchor navigation, and keeping
workspace theme preferences intact. Browser tests use the existing mocked API
fixture; a separate read-only preview check uses the local running API.

## Verification and handoff

Run from `frontend/` on 2026-11-14:

- `npm run build`: passed (TypeScript and Vite).
- With `STEWARD_TEST_URL=http://127.0.0.1:4178` and
  `STEWARD_BROWSER_EXECUTABLE=C:\Program Files\Google\Chrome\Application\chrome.exe`,
  `npm run test:browser -- tests/landing/landing.spec.ts --reporter=line --workers=2`:
  9 passed. API responses are mocked; uploaded media plays in real Chrome.
- With the same environment,
  `npm run test:browser -- tests/landing/landing.spec.ts tests/navigation.spec.ts tests/workspace.spec.ts --grep-invert 'workspace pages stay usable' --reporter=line --workers=3`:
  20 passed, 1 failed because the workspace-owned test still asserts the previous
  headline. The excluded test writes shared workspace screenshots; landing
  screenshots were checked separately without changing those artifacts.
- Read-only browser check against the existing local API: real dashboard loads;
  no page errors or horizontal overflow at 1440×1000 and 390×844.
- Additional visual checks: 320×568 and 844×390; the video remains in the opening
  screen and the mobile iframe fits its container.
- `git diff --check`: passed. Text files use UTF-8/LF.
- Independent read-only review: no actionable code findings. Implementer
  separately verified short-screen sizing and mobile preview scaling.

Shared-file handoff requested: `frontend/tests/workspace.spec.ts:4` needs only
its expected heading changed from `Your codebase. Understood and maintained.`
to `Duck Tape is on Duck Tape`. No other assertion or workspace behavior changes.
Awaiting the explicit Workspace handoff before editing that file.

No API or component-export changes. Get started still links to `/dashboard`.
Safari/Firefox, live inference, and the full backend acceptance tier were not run.
The supplied video is preserved at its original 9.3 MB; benchmark data is example
content, not measured product performance. No source or credentials were uploaded.
