> Historical training handoff. Do not apply its worker patch or treat its local-only graph/reachability claims as current. Runtime integration already exists. Use the [current graph contract](../docs/REPO_GRAPH_HANDOFF.md) and `backend/model_schemas.py`. Adaption training is underway; completed artifacts still need live qualification.

# B → A: update on your three requests, and your progress (14 Nov 2026, late evening)

## Your three upgrades to `agent_graph_context`

1. **Shallow reachability: done, with one change.**
   - Each matched package now reports static import and call evidence with file:line.
   - `directly_imported`: `true` (imported, with evidence), `false` (no static import in parsed files), or `null` (dynamic imports or unparsed files leave it open).
   - `sink_reachable`: `true` only when a resolved static call hits a symbol the catalogue entry names (`affected_symbols`, or OSV-style rule `symbols`); otherwise `null`. It is **never `false`**.
   - **The change:** a package in the lockfile with zero imports gets `directly_imported: false`, not `sink_reachable: false`. A transitive dependency (for example `urllib3` used through `requests`) runs without ever being imported, so `sink_reachable: false` would hide real vulnerabilities.
   - **Train on:** `sink_reachable` ∈ {`true`, `null`} and `directly_imported` ∈ {`true`, `false`, `null`}.
   - **Pitch contrast:** "called at loader.py:6" vs "not imported directly; transitive only".
2. **`signature` on `focal_symbols`: done.** It is the declaration text at `start_line`, with multi-line Python headers joined, at most 240 characters, and may be null. Treat it as untrusted source text.
3. **Atlas mirror: done, behind its own opt-in flag.**
   - `STEWARD_ATLAS_GRAPH_MIRROR=1` plus the existing Atlas configuration. It deliberately does **not** reuse `STEWARD_ATLAS_RETRIEVAL`, because anyone who enabled that flag agreed to upload hashed vectors only.
   - `POST /api/repositories/{id}/atlas-graph-mirror` upserts the current snapshot into the private `ducktape` database: `repo_nodes`, `repo_edges` (with signatures and degree) and `approved_knowledge` (team-approved, current, non-conflicting only).
   - It is idempotent and keeps one snapshot per repository. Never sent: team notes, drafts, job data, file bodies, or secret-bearing files.
   - Verified live on the public Parcel sample: 16 nodes and 16 edges, idempotent on rerun.

## What's ready for you (now on `main`, commit `398ed23`)

- **Graph context:** `backend/agent_context.py` → `agent_graph_context(repo_id, snapshot_id, query, budget_tokens=3500, job=None, entries=None)`, also `GET /api/repositories/{id}/agent-context?query=…&job_id=…`. It returns `graph_context v1` plus `signature`, `degree`, `high_degree`, and per-match `directly_imported`, `sink_reachable`, `usage` and `cwe`. Schema and rules: `docs/KNOWLEDGE_GRAPH.md` → "Agent graph context".
- **Catalogue jobs:** jobs created by catalogue intake carry `job['catalogue']` and `job['applicability']`, which now includes `cwe_ids` and `affected_symbols` when the public entry has them. Pass `job=job` to `agent_graph_context` to include the match.
- **C's catalogue is integrated:** `POST /api/repositories/{id}/applicability/catalogue` matches the snapshot's pinned packages against C's catalogue (local by default, Atlas opt-in).
- **Real data checked:** the real public record **CVE-2024-35195** (`PyPI/requests`) matches a demo repository pinning `requests==2.31.0`, citing `requirements.txt` line 2. The graph context shows `directly_imported: true` (`app.py:1`, `app.py:5`) and `sink_reachable: null`. Evidence: `docs/evidence/applicability-real-osv.json`. Use this record for the security demo.

## Your progress, as seen from GitHub (checked 14 Nov ~19:30)

Nothing from your handoff steps is on `origin` yet. Your only recent commit is `8fb1972 fix(agent): make executable edit fixture portable` (merged in PR #2). There is no `feat/agent/*` or model-training branch, and `backend/worker.py` on `main` is unchanged. If the work exists locally, please push it: `git push -u origin feat/agent/<task>`.

| Handoff step (section 5) | Status on origin |
| --- | --- |
| 1. Worker uses `retrieve(repo_id, snapshot_id, …)` | Not started: `worker.py:182` and `:376` (also `:207`) still use `retrieve(repo['index'], …)` |
| 2. `'applicability'` in the job keys sent to the model | Not started: `worker.py:188` and `:379` |
| 3. `agent_graph_context` in prompts | Not started (B's function is ready; see above) |
| 4. Keep output schemas (no `needs_decision_card` / `new_global_vuln_candidate`) | OK so far: `model_schemas.py` unchanged |
| 5–6. Training on `graph_context v1` with real null rates, data rules | Can't tell: no training code or data pushed |
| 7. Branch `feat/agent/model-training` | Not created on origin |
| 8. Worker tests (team notes never reach the model; graph_context/applicability do) | Not started |
| 9. Live-model run ID for the Databricks verified row | Not received |

## What B needs from you, in priority order

1. **Push your current branch** so B can integrate it before the demo freeze.
2. **Steps 1–3 in `worker.py`.** They are small, and the demo depends on them: catalogue match → job → worker sees the match and graph context.
3. **One live-model run ID.** B exports it to Databricks for the verified row and records the model, provider and version in SUBMISSION.md.
4. **The worker tests in step 8.**
5. **Any shared-file edits** (`main.py`, config, CI): send them to B as captain.
