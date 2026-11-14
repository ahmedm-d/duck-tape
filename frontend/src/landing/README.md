# Landing design and handoff

## Current direction

The supplied references drive this revision: white headline in a clean system
font, the supplied duck-D logo at the top right, frosted Log in / Start creating
buttons, the duck video in its original colours with the lower half fading out,
and a blue/orange glow behind the dark workspace window.

Below the preview, Tickets / Security / Research feed glass Understand / Prepare /
Check / Review pills with coloured ribbons. The three example metric cards use
soft grey glass and lavender details, followed by a minimal footer.

The original video, logo and gradient are kept as supplied. CSS handles the crop,
fade, and white display of the black transparent logo. Numbers remain explicitly
illustrative. Both hero buttons open the existing local `/dashboard`; this change
does not introduce authentication.

## Scope and implementation

Landing owns `frontend/src/landing/**`, `frontend/public/landing/**`, and
`frontend/tests/landing/**` under `docs/FRONTEND_PARALLEL.md`.

- `Landing.tsx`: page composition, accessible video controls, workflow and metrics.
- `landing.css`: scoped typography, glass, gradients, crop and responsive layout.
- `WorkspacePreview.tsx`: the real `/dashboard?preview=1` iframe; applies a local
  presentation stylesheet inside that frame only. Saved theme and the actual
  workspace outside this preview are untouched.
- `public/landing/`: original media, extracted video poster, logo and gradient.
- `tests/landing/landing.spec.ts`: focused browser checks using the existing API
  fixture, with real Chrome media playback.

No shared app shell, API, routes, dependencies or backend files changed.

- [x] Update the focused test and observe it fail against the previous design.
- [x] Implement the supplied visual direction and preserve the real preview.
- [x] Check desktop/mobile screenshots and isolated preview styling.
- [x] Complete final build, browser checks and independent review.

## Verification

From `frontend/`, use `STEWARD_TEST_URL=http://127.0.0.1:4178` and
`STEWARD_BROWSER_EXECUTABLE=C:\Program Files\Google\Chrome\Application\chrome.exe`.

- `npm run build`: passed.
- `npm run test:browser -- tests/landing/landing.spec.ts --reporter=line --workers=3`:
  9 passed.
- Real local API preview: no page errors; iframe receives its presentation class.
- Independent review: no actionable findings; preview styling remains isolated.
- Visual checks: 1440x1000, 390x844, 320x568, and 844x390. Regression coverage includes
  320x568, reduced motion, failed media, denied autoplay, keyboard access, section
  navigation, no overflow, and preservation of workspace theme preferences.

## Shared test handoff

The previously requested handoff of `frontend/tests/workspace.spec.ts:4` remains
pending. Its old headline must become `Duck Tape is on Duck Tape`; its
`Get started` link selector must now become `Start creating`. Leave the rest of
that workspace-owned test unchanged. The prior broader run had 20 passing checks
and this one stale-copy failure. No shared test was silently skipped or changed.

Safari/Firefox, actual authentication, live inference and backend acceptance were
not exercised. This is a landing presentation change, not product qualification.
