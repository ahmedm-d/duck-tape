# Global catalogue frontend handoff

C owns `frontend/src/components/GlobalCatalogue.tsx`, its scoped `GlobalCatalogue.css`, and `frontend/tests/global-catalogue.spec.ts`. No shared navigation, API, CSS, routes or configuration files were edited. B remains integration captain. This component is implemented and browser-tested but is not yet mounted in the application navigation.

## Shared-file changes requested from B

Claim `backend/main.py` and `frontend/src/main.tsx` before integrating. No new dependency, shared API type or global CSS edit is required.

1. Add a read-only `GET /api/global-catalogue` route, validating required `ecosystem` (1–64 characters), `package` (1–256 characters) and `limit` (1–100; default 25). Use `Catalogue(path).query(ecosystem, package, limit=limit)`; it already includes `source_status`. Use the CLI's default isolated path `ROOT / 'data' / 'global' / 'catalogue.sqlite3'` server-side (or agree a shared path helper with C). These are bounded public lookups, not repository scans. Do not accept arbitrary database paths in HTTP parameters.
2. In `frontend/src/main.tsx`, import `GlobalCatalogue` from `./components/GlobalCatalogue` and add a `Global catalogue` navigation item and matching view using the existing navigation pattern. Render:

```tsx
<GlobalCatalogue query={({ecosystem, package: packageName, limit}) =>
  api('/global-catalogue?' + new URLSearchParams({
    ecosystem, package: packageName, limit: String(limit),
  }).toString())
}/>
```

3. Keep this view independent of selected repositories; no private profile, package list or decisions are uploaded. The query callback returns the `CatalogueResult` type exported by the component, matching `Catalogue.query` plus optional `source_status`. No repository proposal creation or execution permissions are added. The component explicitly labels unsupported version rules and unknown coverage; B performs applicability matching locally.
4. Once mounted, run the production build and a navigation/API integration browser check. The standalone checks below do not prove those future shared edits.

## Checks and evidence

Local frontend dependencies installed with `npm ci --no-audit --no-fund`; no lockfile changes. `npm run build` passes (TypeScript and Vite production build). The unmounted component is typechecked; it enters the production bundle only after the shared navigation handoff.

From `frontend/`, with installed Chrome:

```powershell
$env:STEWARD_BROWSER_EXECUTABLE='C:/Program Files/Google/Chrome/Application/chrome.exe'
npx playwright test tests/global-catalogue.spec.ts --workers=1
```

Result: 3 passed. Browser checks cover exact ecosystem/package and a fixed 25-result bound; original source attribution; stale, withdrawn and unsupported labels; failed ingestion visibility; unknown freshness after failed refresh; explicit unknown empty results; clearing previous results on a failed new query; 390px layout without horizontal overflow; and dark theme. The tests mount the real component through Vite with an intercepted test-only HTML harness and synthetic responses. They do not call Databricks, Atlas or the backend API. The harness is not shipped as an application route.

No cloud credentials, public deployment, private repository data or shared application database is involved. Shared navigation/API wiring and end-to-end repository proposal lifecycle remain B/captain integration work.
