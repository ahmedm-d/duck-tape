# Landing design and handoff

## Current direction

The supplied duck-and-tape still fills the hero, with the white headline
"Duck Tape holds your codebase together." on the left and artwork on the right.
The logo stays top left, with frosted Log in / Start creating buttons beneath
the headline. On phones and portrait tablets the artwork sits below the copy. The image fades into
the blue/orange glow behind the real workspace preview.

The original PNG is preserved. Video assets, playback controls and media effects
have been removed. Both hero buttons open the existing local `/dashboard`;
authentication is not introduced.

Below the preview, Tickets / Security / Research feed glass Understand / Prepare /
Check / Review pills with coloured ribbons. The three metric cards remain
explicitly illustrative, followed by a minimal footer.

## Scope and implementation

Landing owns `frontend/src/landing/**`, `frontend/public/landing/**`, and
`frontend/tests/landing/**` under `docs/FRONTEND_PARALLEL.md`.

- `Landing.tsx`: page composition, still hero, workflow and metrics.
- `landing.css`: scoped typography, glass, gradients, crop and responsive layout.
- `WorkspacePreview.tsx`: the real `/dashboard?preview=1` iframe; applies a local
  presentation stylesheet inside that frame only. Saved theme and the actual
  workspace outside this preview are untouched.
- `public/landing/`: supplied hero still, logo and gradient.
- `tests/landing/landing.spec.ts`: focused browser checks using the existing API
  fixture in Chrome.

No shared app shell, API, routes, dependencies or backend files changed.

## Verification

From `frontend/`, use `STEWARD_TEST_URL=http://127.0.0.1:4178` and
`STEWARD_BROWSER_EXECUTABLE=C:\Program Files\Google\Chrome\Application\chrome.exe`.

- `npm run build`: passed.
- `npm run test:browser -- tests/landing/landing.spec.ts --reporter=line --workers=2`:
  8 passed. Still-image checks replace the obsolete video cases.
- Visual checks: 1440x900, 390x844, 320x568, and 844x390. Coverage includes image
  loading, left-side copy/logo, mobile copy/art separation, no video element,
  keyboard access, navigation, no overflow and preserved workspace theme.
- Review found a portrait-tablet crop; fixed with a stacked hero at 701?900px
  for tall screens. Added 768x1024, 701x900 and 700x700 coverage; checked the
  final tablet screenshot. No shared workspace files changed.

## Shared test handoff

The previously requested handoff of `frontend/tests/workspace.spec.ts:4` remains
pending. Its old headline must become `Duck Tape holds your codebase together.`; its
`Get started` link selector must now become `Start creating`. Leave the rest of
that workspace-owned test unchanged. The prior broader run had 20 passing checks
and this one stale-copy failure. No shared test was silently skipped or changed.

Safari/Firefox, actual authentication, live inference and backend acceptance were
not exercised. This is a landing presentation change, not product qualification.
