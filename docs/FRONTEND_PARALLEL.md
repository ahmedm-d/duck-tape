# Two parallel frontend workspaces

Both Paseo sections branch from the same verified UI foundation. Keep separate checkouts and commits; never switch branches inside the other section. Main and Part C are unaffected.

| Paseo section | Branch | Scope and owned files | Preview port |
| --- | --- | --- | --- |
| Duck Tape — Workspace & Dashboard | `feat/ui/workspace-dashboard` | Dashboard, sidebar, repository Jobs/Codebase, job review, Settings, gallery; `frontend/src/main.tsx`, `frontend/src/style.css`, `frontend/src/components/**`, `frontend/src/api.ts`, existing frontend tests and workspace-specific new tests | 4177 |
| Duck Tape — Landing Page | `feat/ui/landing-page` | Public `/` page, hero, messaging, preview, responsive landing design; `frontend/src/landing/**`, `frontend/public/landing/**`, `frontend/tests/landing/**` | 4178 |

## Boundaries

- Landing imports its own stylesheet. Keep new selectors inside `.landing` or landing-specific classes; use existing shared theme variables. Do not change global element styles from this lane.
- Workspace owns shared shell, routing, global styles, API client, Vite config and package/lockfiles. Request a specific shared change rather than editing those files from the landing branch.
- Landing exports `Landing` from `frontend/src/landing/Landing.tsx`; `/` stays sidebar-free and Get started points to `/dashboard`. Its preview may embed `/dashboard?preview=1`, which remains workspace-owned.
- `/screens` belongs to Workspace and consumes the actual landing route; it must not duplicate the landing implementation.
- Backend changes require an explicit handoff. The default parallel previews share the existing API on port 8770, so mutations are visible in both previews. Use separate backend data directories if testing independent data mutations.
- Commit only your lane: `feat(landing): ...`, `fix(landing): ...`, `feat(workspace): ...` or `fix(workspace): ...`. Do not rewrite shared history or merge the other lane without coordination.
- Integrate both branches into `feat/ui/repository-workspace` after reviewing their diffs and passing build/browser checks. The extraction commit is their common base; do not cherry-pick it again.

## Start previews

From either section's `frontend` directory, PowerShell:

```powershell
npm ci
$env:DUCK_TAPE_API_URL='http://127.0.0.1:8770'
# Workspace section:
npm run dev -- --host 127.0.0.1 --port 4177 --strictPort
# Landing section uses 4178 instead.
```

`npm run build` validates each branch. Existing API is the isolated app from [the app guide](APP_WORKSPACE.md); it must be running for real data. No credentials are copied into the new sections.

## Workspace section kickoff

Work only on Duck Tape's app workspace: Dashboard, sidebar, repository Jobs/Codebase, job review, Settings and gallery. Read AGENTS.md and this document, inspect existing APIs and retain real workflow behavior. Own main.tsx, global styles, app components, API client and app tests. Do not edit src/landing, public/landing or tests/landing. Preserve clean URL reloads, explicit decisions, exact-revision approval and separate team notes/revision requests. Use port 4177. Make small commits, run the production build and relevant browser tests, and report remaining limitations honestly.

## Landing section kickoff

Work only on Duck Tape's public landing page at /. Read AGENTS.md and this document. Own src/landing, public/landing and tests/landing. Improve presentation, copy, responsive behavior and accessibility while retaining branding, a real application preview and Get started linking to /dashboard. The existing headline is “Your codebase. Understood and maintained.” Keep styles scoped and reuse theme variables; do not edit the router, global stylesheet, app components or package files without a handoff. Use port 4178. Make small commits, run the production build and landing browser checks, and do not present fixture AI as live.
