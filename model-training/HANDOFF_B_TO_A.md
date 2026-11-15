> Historical training handoff. Do not apply its worker patch or treat its local-only graph/reachability claims as current. Runtime integration already exists. Use the [current graph contract](../docs/REPO_GRAPH_HANDOFF.md) and `backend/model_schemas.py`. Adaption training is underway; completed artifacts still need live qualification.

# Duck Tape: B → A handoff, agreed graph-context architecture (14 Nov 2026, evening)

Paste this whole file into your coding agent as context. It is the **final, agreed** contract between B (repository knowledge and graph) and A (models and worker). Repository paths are relative to the `duck-tape` root. B's code is on branch `feat/repo/applicability` (PR #1, not yet merged into `main` because of a CI pipeline problem C is fixing). Pull that branch to see it.

---

## 1. What B has already built that A will consume

| Area | Where | What A gets |
| --- | --- | --- |
| Snapshot retrieval | `backend/retrieval.py` → `retrieve(repo_id, snapshot_id, query, budget_tokens, ...)` | Ranked, cited file ranges with hashes, approved team knowledge and explicit gaps. Opt-in Graphify edges (`STEWARD_GRAPH_ENGINE=graphify`) and Atlas candidates (`STEWARD_ATLAS_RETRIEVAL=1`) work **only in this form**, not the legacy `retrieve(index, ...)` form. |
| Knowledge map built with [Graphify](https://github.com/Graphify-Labs/graphify) 0.9.108 | `backend/graphify_graph.py`, `GET /api/repositories/{id}/graph` | Nodes and edges checked against the snapshot. `EXTRACTED` edges are evidence; anything else is `INFERRED` with `suggestion_only: true`. Each node has a community ID. Falls back to the built-in symbol graph with a visible gap. |
| Applicability match | `backend/applicability.py`, `POST /api/repositories/{id}/applicability[/intake]` | `matched`, `not_matched` or `unknown` for each catalogue entry, with lockfile line citations, rationale, acceptance criteria and limitations. Jobs created by intake store this as `job['applicability']` plus `job['catalogue']`. |
| Approved knowledge | `backend/knowledge.py` → `for_paths` | Only team-approved, current, non-conflicting rules with versions. Team notes are never included. |

A version match means the repository **uses an affected version**. It does not mean the code is exploitable or reachable.

---

## 2. Agreed architecture

- **One local workspace, no tenants.** Isolation is by `repo_id` + `snapshot_id`. There is no `tenant_id`.
- **The repository graph stays local:** SQLite plus the B cache. It is never uploaded to Atlas: no `repo_nodes`/`repo_edges`, and no knowledge board in Atlas. The only Atlas data for a repository is the opt-in `evidence_vectors` (hashed IDs plus vectors).
- **The global vulnerability knowledge is C's public catalogue** (`GlobalEntry`), built from public sources only. **No write-back** of patterns derived from client repositories.
- **B builds the agent graph context in B's lane** (section 4). A does not create `backend/graph_engine/` or `backend/api/`.
- **The product name is Duck Tape** ("Steward" is the old name, kept only in `STEWARD_*` env vars). Graphify is cited as `Graphify-Labs/graphify`.
- **Communities come from Graphify's Louvain fallback.** Leiden needs `graspologic`, which isn't installed, so call them "communities", not "Leiden".

---

## 3. The agent graph context: `graph_context v1` (final schema)

This replaces `<steward_graph_context>`. Wrap it in `<graph_context>…</graph_context>` in prompts. Any field marked `?` **may be null**. The model must treat null as **unknown**, never as false.

```json
{
  "scope": {"repo_id": "", "snapshot_id": "", "engine": "graphify|duck-tape-symbols", "engine_version": "?", "graph_hash": ""},
  "focal_symbols": [{"node_id": "", "path": "", "start_line": 1, "end_line": "?", "kind": "class|callable|symbol|file", "label": "", "community": "?", "degree": "?", "high_degree": "?"}],
  "edges": [{"source": "", "target": "", "relation": "calls|imports|imports_from|contains|method|tests|...", "confidence": "EXTRACTED|INFERRED", "suggestion_only": false, "evidence": {"path": "", "line": 1}}],
  "blast_radius": {"dependent_files": [], "communities_touched": "?", "linked_test_files": [], "structural_risk": "?", "basis": "how this was computed"},
  "catalogue_matches": [{"entry_id": "", "entry_revision": 1, "kind": "", "status": "matched|not_matched|unknown", "citations": [], "limitations": [], "cwe": "?", "sink_reachable": null}],
  "approved_knowledge": [{"id": "", "title": "", "content": "", "scope": "", "version": 1}],
  "gaps": ["each missing or unavailable piece, stated plainly"]
}
```

What the runtime will actually contain:

| Field | Runtime reality |
| --- | --- |
| `confidence` | `EXTRACTED` or `INFERRED` only. **`AMBIGUOUS` is never produced:** Graphify's AST pass doesn't emit it, and its LLM pass is disabled. |
| `degree`, `high_degree` | `degree` is computed from local edges. `high_degree` is a labelled heuristic (top degree in this snapshot). There is no `is_god_node`. |
| `structural_risk` | A labelled heuristic (`low`/`medium`/`high`, explained in `basis`), or null. |
| `linked_test_files` | From test-file imports and test boundaries. It may be empty, and then `gaps` says so. |
| `cwe` | Null until C's catalogue supplies CWE IDs. |
| `sink_reachable` | **Always null for the hackathon.** There is no reachability analysis. Wording when it is unknown: "not shown reachable", never "non-exploitable". |
| `signature`, `summary` | Not in v1. |
| Size | Bounded by `budget_tokens`, counted as UTF-8 bytes like retrieval (default 3500). Trimmed in this order: edges, then focal symbols, then knowledge. Trimming is recorded in `gaps`. |

---

## 4. What B delivers

- `backend/agent_context.py` with `agent_graph_context(repo_id, snapshot_id, query, budget_tokens=3500, job=None) -> dict` returning the schema above. It uses `retrieve()`, the cached knowledge graph (engine `auto`), test edges, `job['applicability']` and approved knowledge.
- `GET /api/repositories/{id}/agent-context?query=…&budget_tokens=…` for inspection and the UI.
- Fixture tests: null handling, the budget limit, no team notes, and a stale snapshot returning 409.

B will send the function signature as soon as it is merged. A calls it from the worker (step 5.3).

---

## 5. What A does

1. **Use the current retrieval form:** in `backend/worker.py` lines 182 and 376, replace `retrieve(repo['index'], ...)` with `retrieve(repo['id'], repo.get('source_snapshot_id') or repo['snapshot'], query, ...)`.
2. **Pass match evidence to the model:** add `'applicability'` to the job keys sent to the model at `backend/worker.py` lines 188 and 379.
3. **Add the graph context to prompts:** once B's function lands, call `agent_graph_context(...)` in the worker for the investigation, planning, implementation and review tasks, and put it in the prompt as `<graph_context>…</graph_context>`.
4. **Keep the output schemas.** Output schemas allow no extra or missing fields (`backend/model_schemas.py`), so don't add new keys:
   - For "needs a human decision", use the existing `DECISION` alternative instead of `needs_decision_card`.
   - Drop `new_global_vuln_candidate`, because there is no write-back.
5. **Train on the real schema:**
   - Synthesize `graph_context v1` blocks exactly as above, with nulls and `gaps[]` at the rates the runtime produces.
   - Use no `AMBIGUOUS` edges and no `is_god_node`, and always set `sink_reachable: null`.
   - Teach the model that null means unknown, so it never suppresses a security finding because a field is null.
6. **Training data rules:**
   - Synthetic or public datasets only, with their licences recorded.
   - No team notes, and no imported-repository code or knowledge.
   - Don't commit model weights.
   - Keep a held-out evaluation set, and claim adaptation gains only with recorded base-vs-adapted results.
7. **Git:**
   - Branch `feat/agent/model-training` (not `feat/model-training-pipeline`), in its own worktree.
   - Commits use `feat(agent): ...`, at most 72 characters.
   - Send B the exact edits for any shared file (`backend/main.py`, `backend/contracts.py`, config, CI); B is integration captain.
8. **Tests:** add a worker test proving team notes never reach the model, and one proving `graph_context` and `applicability` do.
9. **Live run:** when a live-model run works, send B its run ID. B will export it to Databricks (`python scripts/integrations.py databricks-sync --run <id>`) for a verified row, and record the model, provider, version, latency and results in `docs/hackathon/SUBMISSION.md`.

## 6. What A sends back to B

- Confirmation that steps 1–3 are merged, with the commit SHA.
- The model, provider and version used in the demo, and which tasks receive `graph_context`.
- Any field you need added to `graph_context v1`. B decides whether the runtime can fill it honestly before it goes into training.
