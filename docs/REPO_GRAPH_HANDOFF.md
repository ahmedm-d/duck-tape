# B's Databricks graph integration boundary

B's `feat/repo/atlas-required` branch implements automatic private Atlas projection and validated `$graphLookup` traversal. It does not implement a Databricks private-graph producer. The contract below still applies if that producer is added; it is not proof that a remote Databricks graph job ran.

| Consumer | Stable interface |
| --- | --- |
| Repository UI | `GET /api/repositories/{id}/graph` returns bounded nodes and edges |
| Combined graph | `POST /api/repositories/{id}/knowledge-map` links public advisories to private manifest evidence |
| Worker retrieval | `retrieval.retrieve(repo_id, snapshot_id, query, ..., cancel=...)` returns locally resolvable chunks and approved knowledge |
| Model payload | `agent_context.agent_graph_context(...)` returns `graph_context v1` with current keys and bounds |
| Applicability | `applicability.build_profile` consumes exact dependency versions from the immutable index |

Every graph must carry the exact requested `repo_id` and `snapshot_id`. Consumers reject mismatches. Validate returned nodes, edges and line ranges against local snapshot content before publication; reuse Graphify normalization where applicable. Preserve EXTRACTED/INFERRED provenance. Missing call edges never establish non-exploitability.

Bind Databricks results to source content hashes, producer/schema version and job identity. Publish a complete validated generation only. An old completion must never replace a newer snapshot. If processing is asynchronous, expose pending/failed/ready and block dependent work until the requested generation is ready. Label any local fallback as local.

Propagate cancellation through polling and check it before publication. Do not hold SQLite transactions open during network calls. Preserve refresh/deletion invalidation, context hashes, approved knowledge versions and exact-revision approvals.

## Privacy and shared files

The private graph pipeline is separate from C's public vulnerability curation. Private source, profiles and decisions must never enter `ducktape_public`. Team notes never enter either pipeline or model context. The integration request explicitly supersedes the earlier cancelled requirement: Atlas is now required by default. Read the accepted [private projection policy](hackathon/KNOWLEDGE_CONTRACT.md); embeddings, graph metadata and approved knowledge use separate private credentials. Remote embeddings require explicit provider configuration.

B owns producer/retrieval changes. The release branch adds consumer scope checks in `agent_context.py` and `_graphify_edges` in `retrieval.py`; preserve these on merge. Coordinate `main.py`, `data_platforms.py`, `workflow.py`, config and routes with the integration captain. A's worker already consumes this context; do not apply the historical training handoff patch again.

## Acceptance

```sh
python -m pytest tests/test_graph_handoff_boundary.py tests/test_agent_context.py tests/test_worker_handoff.py tests/test_graphify_graph.py tests/test_atlas_retrieval.py tests/test_backend_knowledge_flow.py tests/test_catalogue_intake_races.py -q
```

These are deterministic contract tests. B must also supply a real cloud job ID, source/snapshot hashes, returned graph hash, freshness/outage/cancellation evidence and retrieval measurements on fixed relevant files. Rerun full acceptance after integration before claiming Databricks graph execution.
