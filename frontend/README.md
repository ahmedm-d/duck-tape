# Steward frontend

React, TypeScript and Vite. The production bundle is served by FastAPI at the same origin. Development uses `npm run dev` with an API proxy to `http://127.0.0.1:8765`.

## Verification

- `npm ci && npm run build` checks TypeScript and emits `dist/`.
- Start the backend, then run `npm run test:browser` for a real backend/browser acceptance journey. It creates a fresh isolated sample and preserves existing work.
- Install the matching browser with `npx playwright install chromium`, or set `STEWARD_BROWSER_EXECUTABLE` to an existing Chromium executable. `STEWARD_TEST_URL` overrides the default backend URL.
- `browser-results.json` records the last successful run; PNG files capture desktop overview, job diff, narrow layout and dark theme.

The browser journey verifies preparation approval, real Git diff, baseline/regression checks, immutable captured evidence, note isolation, a second checked revision, exact-revision approval/application, feature decisions, Q&A source navigation, research intake, approved knowledge, responsive layout and browser runtime errors. These are labelled fixture-provider journeys, not live-model acceptance.

## Interface boundaries

Team notes and revision requests are separate actions. Knowledge is promoted explicitly. Imported repositories cannot use managed sample application; patches can be exported. Provider and sandbox availability come from server health. Unknown confidence and unavailable checks are shown explicitly. Text from repository content, source URLs, notes and provider responses is rendered as text, never raw HTML.

The interface supplies accessible control names, keyboard focus states, modal Escape/focus handling, light/dark themes and a narrow-screen repository switch. Diff highlighting distinguishes additions, removals and hunk headers; split view shows before/after content. Current repository sources and immutable run context are distinct views.

Skipped: generated UI previews, real authentication and public deployment. Those are deliberately outside this local prototype; live inference and arbitrary execution depend on the backend's configured capabilities.
