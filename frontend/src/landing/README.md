# Landing design and handoff

## Current direction

The supplied duck-and-tape still fills the hero, with the white headline
"Duck Tape holds your codebase together." on the left and artwork on the right.
The logo stays top left, with frosted Log in / Start creating buttons beneath
the headline. On phones and portrait tablets the artwork sits below the copy. The image fades into
the blue/orange glow behind the real workspace preview.

The original PNG is preserved. Video assets, playback controls and media effects
have been removed. Hero buttons use the existing `/login` and `/signup` routes;
when accounts are disabled, these routes continue to `/dashboard`.

Below the preview, Tickets / Security / Research feed glass Understand / Prepare /
Check / Review pills with coloured ribbons. Three cards summarize the team-reported
benchmark score, example count and specialist count. All 12 results follow in gain
order, with per-check counts, evaluation methods and regressions. The original
illustrative runtime, check-count and turnaround figures have been removed.

## Scope and implementation

Landing owns `frontend/src/landing/**`, `frontend/public/landing/**`, and
`frontend/tests/landing/**` under `docs/FRONTEND_PARALLEL.md`.

- `Landing.tsx`: page composition, still hero, workflow and metrics.
- `landing.css`: scoped typography, glass, gradients, crop and responsive layout.
- `WorkspacePreview.tsx`: the real `/dashboard?preview=1` iframe; applies a local
  presentation stylesheet inside that frame only. Saved theme and the actual
  workspace outside this preview are untouched.
- `assets/`: supplied hero still, logo and gradient. Vite bundles these into
  `/assets` so the production API serves the same images as the development server.
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
- Review found a portrait-tablet crop; fixed with a stacked hero at 701-900px
  for tall screens. Added 768x1024, 701x900 and 700x700 coverage; checked the
  final tablet screenshot.

Safari/Firefox and live cloud authentication/inference were not exercised.
Mocked transport checks do not qualify live provider behavior.

## Integration handoff

For the explicitly requested merge to main, Landing claims only the landing-entry
selectors in `frontend/tests/workspace.spec.ts` and `frontend/tests/auth.spec.ts`.
Update the old headline/Get started/Create account selectors to the approved
headline and Start creating CTA. Preserve the rest of both workspace-owned tests.
Hero links use main's existing `/login` and `/signup` routes, which fall through
to `/dashboard` when accounts are disabled. No shared implementation changes.
These two test files are released after the reviewed integration commit.
The earlier stale-copy failure is resolved by these selector updates.

Integration validation: 14 landing/auth browser checks passed after merging
current main. Full deterministic acceptance is recorded in the merge PR.

## Benchmark content

Results reproduce the team-supplied evaluation report shared on 15 November 2026:
600 examples, 50 per slice; 1,240/2,368 baseline rubric checks and 2,210/2,368
compared checks. The displayed 52.4% and 93.3% are pooled rubric scores across
multiple methods, not end-to-end task success rates.

Generation compares zero-shot Adaption `gemma-4-31b-it` with the same model plus
contract/few-shot prompting. Security/code review score rejected/chosen dataset
responses offline. Retrieval compares local vector and hybrid/graph pipelines.
The page discloses the test-writer diff-validity drop (80% to 62%), IaC regression
(-0.5 percentage points), pending independent reproduction and separate Claude
backend evaluation. No claim of trained-checkpoint improvement is made.
