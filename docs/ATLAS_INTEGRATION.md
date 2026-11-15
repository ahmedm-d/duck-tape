# Atlas-required integration gates

Integration candidate: `feat/repo/backend-demo` (backend head `6498f6c`) plus B's `feat/repo/atlas-required` (`03fd56d`), both descending from main `f53bbae`. Preserve both histories; never force-push or rewrite main. Frontend styling remains owned by the separate teammate branch.

## Checklist

- [x] Read AGENTS.md/CONTRIBUTING.md and review the new private Atlas transfer policy.
- [x] Verify [B's CI run](https://github.com/ahmedm-d/duck-tape/actions/runs/43647144209) passed at `03fd56d`.
- [x] Integrate in our isolated worktree, keeping current catalogue, retrieval and worker changes alongside B's hooks.
- [x] Install requirements.lock, including PyMongo, dnspython and truststore.
- [x] Fix reproduced privacy/lifecycle issues and add regression coverage.
- [x] Run complete deterministic backend, restart, frontend-build and browser-fixture gates on the combined candidate.
- [x] Live TLS startup, Parcel `atlas_sync.status == "synced"`, and `scope.atlas.served == true` on the integrated candidate.

All pre-publication gates passed. Publish with a normal main push and inspect [main CI](https://github.com/ahmedm-d/duck-tape/actions?query=branch%3Amain) for the resulting commit; the CI result is recorded by GitHub, not inferred from these local checks.

## Decisions and review fixes

The integration captain accepts the user's updated private Atlas policy: graph metadata, conservative declaration headers, approved knowledge and hashed vectors may be projected to the team's private database. Team notes, drafts, source file bodies, secrets, jobs and patches remain excluded. Remote embeddings need explicit provider configuration. Public catalogue reads default to Atlas in required mode and use separate public credentials; explicit local reads remain possible.

Independent review reproduced and fixed body text leaking through declaration snippets, unvalidated transitive graph paths, missing resync after policy changes, overlapping process writes, stale queue coalescing, cancellation marked as success, public-database vector misconfiguration, and offline controllers inheriting Atlas upload flags. No TLS bypass was introduced.

Graph/approved-knowledge projections reconcile after successful sync. Historical hashed vector namespaces remain stored; they cannot supply current evidence without local namespace/content validation. Provider configuration changes require explicit manual resync. Serialization covers processes sharing one local store, not independent machines writing the same projection.

## Live verification and model handoff

This isolated worktree does not automatically load the main checkout's `.env`. Its initial attempt therefore lacked configuration; an attempt using the existing settings then timed out. After the operator added this computer's IP to Atlas Network Access, the TLS-verified ping passed. Relevant private Atlas and embedding settings were copied into this worktree's ignored `.env`, preserving separate workspace data paths; no credentials were printed or committed.

The actual `run.py --port 8783` service started with Atlas required. A fresh Parcel sample synced **16 nodes, 16 edges and 14 vector chunks**; its approved-knowledge count was zero because its seeded rule remains observed. The API returned `atlas_sync.status=synced`, `scope.atlas.served=true`, running worker/monitoring, and search mode `keyword + semantic + atlas + graph`. [Sanitized live evidence](evidence/atlas-required-live.json) records the exact snapshot and query. This verifies private graph/vector integration, not new Adaption artifact quality or public Databricks publication. TLS verification remained enabled throughout.

Final local gate, 14 November 2026: **807 backend tests passed, 1 optional Graphify CLI test skipped**, restart recovery passed, production build passed, **32 browser fixtures passed**. Report: `data/atlas-merge-acceptance/20261114T225149Z-871d891e/report.json`. Backend completed in 371 seconds; the original 600-second suite deadline remains in the source. The runner's 10 unit tests passed after restoring that deadline. Fresh restart evidence/screenshots are retained beside the report; tracked historical artifacts were restored.

Final independent review found no remaining high/major code issue within the single-store prototype scope. The pre-publication fetch confirmed `origin/main=f53bbae` and B's head `03fd56d`. The reviewed merge subject is `chore(repo): merge atlas required mode`; it preserves both histories without rewriting main. See [operator setup](BACKEND_DEMO_HANDOFF.md) for private/public credentials, embedding index and the two API checks; do not paste credentials into chat.

Training completion alone is insufficient for the full demo:

1. Download the approved Adaption artifacts and record base-model, tokenizer and adapter identities.
2. Serve compatible model aliases, configure Duck Tape roles and embedding endpoint, and run the all-role schema smoke test.
3. Run the held-out application qualification against the actual provider and sandbox; fix failures rather than treating valid JSON as coding success.
4. Complete the [backend demo runbook](BACKEND_DEMO_RUNBOOK.md): real retrieved evidence, checked patch, revision/review/export and exact approval.
5. Verify this candidate's private Atlas gate and same-run Databricks/public catalogue evidence for the sponsor demonstration.

The earlier recorded 3B model baseline was not qualified. The new trained artifacts have no verified application result until those checks run.
