# Public GitHub repository acceptance journey

Executed locally on 15 November 2026 against `http://127.0.0.1:8783`.
Live backend: `f36a7fd`; the refresh UI correction is based on integrated main
`6b2c2ed`. These results do not qualify the still-training Adaption models.

## Public fixture

[ahmedm-d/duck-tape-demo-shop](https://github.com/ahmedm-d/duck-tape-demo-shop)
contains only synthetic, reviewed test code. No credentials or customer material
were published. GitHub creation used the authenticated CLI; later pushes used
its credential helper for that command because the host's default Git helper
selected a different account. Global credentials were not changed.

- Initial commit: `8c94c542cb0013b5fee30aedabd8b3081065de60`.
- Refresh commit: `ad23868d9eacd77aa3dcf7ff89737e838eda2387`.
- Files: endpoint, pricing helper, configuration, regression tests, README,
  exact dependency pins, gitignore, and a module to remove during refresh.
- Intended bug: quantity 2 at 100 cents with 10% tax returns 110 instead of 220.
- Reviewed fixture baseline: **3 passing tests, 1 intentional failing test**.
  No dependency installation was needed. Imported/generated code was not run
  through Duck Tape during this journey.

## Results

| Check | Observed result |
| --- | --- |
| Connect repository | Imported the public HTTPS Git URL using the browser dialog. Persistent import operation completed. |
| Identity and inventory | Captured the exact initial Git commit; 8 indexed files, 0 excluded, 0 unsupported, 0 failed. GitHub acquisition reports archive-only history. |
| Graph | 19 nodes and 23 total edges; independently checked endpoint-to-helper, helper-to-config, test-to-endpoint and endpoint-to-legacy edges. The UI shows 12 source relationships, excluding symbol definition edges. |
| Atlas | Automatic sync succeeded. Live graph context reports `scope.atlas.served=true`; local embeddings and real Atlas vector candidates were available. |
| Cross-module context | Expanded 16,000-byte graph context contains endpoint, pricing, configuration and tests, with source ranges and Atlas/local graph agreement. The default bounded search returned only two files; this is not a claim of full repository semantic understanding. |
| Clickable evidence | Clicked a dependency target to open `pricing.py`; clicked retrieved `app.py:1-48` to open captured source with 48 numbered lines and the immutable source snapshot. |
| Knowledge | Created an observed integer-cent constraint, explicitly approved version 1 (stored as version 2), reloaded the browser and checked persisted team-approved status. The approved entry appears in later agent context. |
| Modification/addition/deletion | Pushed a changed currency and endpoint import, added `health.py`, deleted `legacy.py`. Browser Refresh index completed; the exact new commit, changed content and new import edge appeared. |
| Freshness | Deleted module nodes/edges are absent from the current graph and current search items. Atlas is synced to the new immutable snapshot. README-backed approved knowledge remains applicable because its evidence did not change. |
| Advisory scan 1 | Browser Run now invoked real OSV queries for 5 exact pinned packages: 5 resolved, 0 failed, no reported dependency gaps, 15 sourced security jobs. |
| Advisory scan 2 | A distinct persisted scan operation returned the same 15 job IDs. The database still contains exactly 15 jobs for this repository: **0 duplicates**. All await preparation approval. |

The live source was `https://api.osv.dev/v1/query`; stored records retain retrieval
timestamps and original advisory data, with `fixture=false`. One verified finding
is [CVE-2024-35195](https://osv.dev/vulnerability/CVE-2024-35195) for pinned
Requests 2.31.0. These are version matches, not exploitability claims. The fixture
does not call Requests at runtime. Future scans can legitimately differ if OSV
changes its advisory evidence.

## Defect found and correction

The source graph and API refreshed correctly, but the frontend retained an old
search result bundle and previous answer after refresh. Clearing/reloading the
page removed it. The correction clears evidence/answers on repository snapshot
changes and rejects late search responses after switching repository or snapshot.
Historical job evidence remains stored in the backend.

Focused browser regression tests cover refresh invalidation, a delayed search
response from another project, and commit-only source snapshot changes. Both the
refresh and source-only regressions were observed failing before their fixes.
Independent review reproduced the source-only race and verified its correction.

Validation: **15 affected Playwright tests passed** (`evidence.spec.ts` and
`imported-workflows.spec.ts`), and `npm run build` passed. These transport-fixture
tests are separate from the live GitHub/Atlas/OSV checks.

## Evidence and limits

Raw local evidence is in the running checkout's ignored `data/github-journey/`:
before/after repository and graph records, bounded and expanded context, approved
knowledge, both scan operations and the final job list. It contains no provider
credentials and is intentionally not part of this public report.

No model-generated repair, model qualification, patch approval/application,
Supabase authentication or Databricks pipeline was exercised here. A configured
local Qwen 3B Q&A request was slow and was abandoned by reloading the browser;
this journey makes no live model Q&A success claim. Structural graph retrieval
and evidence inspection work independently of those models.
