# Global catalogue frontend integration

## Shared-file ownership and release

The user authorized completion of Part C integration in this isolated feature branch. Before editing, C's frontend agent claimed `frontend/src/main.tsx`, the catalogue components and new browser tests, and this handoff. C's coordinator separately claimed `backend/main.py` for the real read-only endpoints. No shared CSS, API helper, configuration, other checkout or teammate file was edited by the frontend agent. Frontend ownership is released with this integration commit for independent review. B retains private matching and proposal lifecycle ownership.

## Implemented contract

The application navigation now includes **Global catalogue**, mounted at `#/global-catalogue`. This route survives reload/back and works without an imported repository. Queries do not include repository identities or private profiles. Existing `STEWARD_*` compatibility and Duck Tape branding are preserved.

`GlobalCatalogue` uses the existing `api` helper against:

- `GET /api/global-catalogue?ecosystem=...&package=...&limit=25&provider=local|atlas`
- `GET /api/global-catalogue/graph` with the same query parameters, explicitly requested by **View relationships**.

Local lookup is the default. Selecting Atlas does not connect; submitting Search explicitly requests the configured public Atlas catalogue. Disabled/unavailable Atlas appears as an error, never a clean result. The graph uses the parameters of the displayed search, even if form inputs have since changed. It shows finding-to-package and finding-to-source relationships, material revisions and unknown applicability. These are public claims, not private repository matches or execution permissions.

The backend controls its catalogue path using `STEWARD_GLOBAL_CATALOGUE_PATH` (default `data/global/catalogue.sqlite3`). No database path is accepted from HTTP queries. `CatalogueResult`, `CatalogueQuery` and `CatalogueGraph` live with their components; no new dependency or shared API type was needed.

## Verification

`npm run build`: passed; the mounted components now enter the production bundle.

Component/navigation checks used a dedicated Vite server on port 4187 because port 4175 belonged to a different worktree and was left untouched:

```powershell
# In frontend, start the isolated Vite server:
npm run dev -- --host 127.0.0.1 --port 4187 --strictPort
# In another terminal:
$env:STEWARD_TEST_URL='http://127.0.0.1:4187'
$env:STEWARD_BROWSER_EXECUTABLE='C:/Program Files/Google/Chrome/Application/chrome.exe'
npx playwright test tests/global-catalogue.spec.ts tests/global-navigation.spec.ts tests/navigation.spec.ts --workers=1
```

Result: **9 passed**. These use labelled synthetic responses and cover source attribution, stale/withdrawn/unsupported/unknown states, failed refresh visibility, query failures, exact bounded lookup, reload/back, repository-independent routing, no-repository startup, dark theme and 390px layout. Existing navigation regressions also pass.

The real API journey used the production bundle served by an isolated backend on port 8767, with `STEWARD_DATA_DIR=data/part-c-integrated/workspace`, `STEWARD_GLOBAL_CATALOGUE_PATH=data/part-c-integrated/catalogue.sqlite3`, and both public cloud enable flags set to `0`. The catalogue was seeded through `Catalogue.ingest(GlobalEntry.model_validate(...))` using `integrations/global/fixtures/global-vulnerability.json` and `global-improvement.json`; these retain `source_quality=fixture`.

```powershell
# Backend launched from the repository root, after setting the isolated paths:
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8767
# From frontend:
$env:STEWARD_TEST_URL='http://127.0.0.1:8767'
$env:STEWARD_GLOBAL_UI='1'
$env:STEWARD_BROWSER_EXECUTABLE='C:/Program Files/Google/Chrome/Application/chrome.exe'
npx playwright test tests/global-api.spec.ts --workers=1
```

Result: **1 passed** in 20.1 seconds. No API responses were intercepted. The journey confirmed fixture lookup through the real API, mounted navigation, original-source attribution, a 3-node/2-edge public graph, unknown empty results, disabled Atlas returning an error, and no browser runtime errors. Desktop/light and narrow/dark screenshots are written under ignored `frontend/test-results/global-api-*/`; the narrow screenshot was visually inspected and had no horizontal overflow.

Skipped: live Databricks/Atlas qualification and B's private repository applicability/proposal lifecycle. The integrated browser test proves the local API and production UI with labelled fixture data, not live public ingestion or cloud service operation.
