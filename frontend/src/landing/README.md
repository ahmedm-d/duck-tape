# Landing design and handoff

## Current direction

The supplied references drive this revision: white headline in a clean system
font, the supplied duck-D logo at the top left, frosted Log in / Start creating
buttons, the supplied silver tape animation with its lower edge fading out,
and a blue/orange glow behind the dark workspace window.

Below the preview, Tickets / Security / Research feed glass Understand / Prepare /
Check / Review pills with coloured ribbons. The three example metric cards use
soft grey glass and lavender details, followed by a minimal footer.

The supplied 20-second, 1918x1080 tape video replaces the duck. Its original
29.2 MB file is retained without recompression; slow-network performance has not
been measured. The poster is extracted from the new clip. The logo and gradient
are kept as supplied. CSS handles the wide crop,
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
- Previous glass-layout review: no actionable findings; preview styling remains isolated.
- Tape revision: build and all 9 landing checks pass; browser loop restart verified.
  Tests also check the wide source ratio and left-side logo at each viewport.
  Independent review found no actionable issues.
- Visual checks: 1440x900, 390x844, 320x568, and 844x390. Regression coverage includes
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
