# Backend demo and frontend handoff

This branch extends the existing B/C implementation. Adaption training is underway (operator confirmation); no finished checkpoint or served trained model has been tested here. No frontend files are changed.

## Start

From this checkout, with Python 3.11+ and Git installed:

```sh
python -m venv .venv
# Windows: .venv/Scripts/python; macOS/Linux: .venv/bin/python
.venv/Scripts/python -m pip install -r requirements.lock
.venv/Scripts/python run.py --port 8781
```

The API and supervised durable worker share the same local data directory. API documentation: http://127.0.0.1:8781/docs. For frontend development, point the teammate's Vite proxy to this API; the default production startup serves a built `frontend/dist` when present. No authentication is implied: this is a loopback single-workspace app.

The build-session instance uses `STEWARD_DATA_DIR=data/demo-workspace` and `STEWARD_GLOBAL_CATALOGUE_PATH=data/global-public.sqlite3` (absolute paths in its process environment). Preserve those settings to reopen the same demo. Other checkouts' databases are separate.

## Frontend API contract

All identifiers below are server-returned IDs, not owner/repo display names. Preserve them in route lookup. HTTP 409 means reload current evidence before retrying. Poll operations/jobs; don't animate invented completion.

| Action | API and payload | Result / boundary |
| --- | --- | --- |
| Import public GitHub | `POST /api/repositories/import` with `{"url":"https://github.com/pallets/itsdangerous","name":"pallets/itsdangerous"}`; optional `Idempotency-Key` header | 202 durable operation; pin exact upstream commit, fetch archive, index; no repository scripts executed |
| Poll import/scan | `GET /api/operations/{operation_id}` | `queued/running/complete/failed/cancelled`, checkpoint, error, result; imported repository ID equals import operation ID |
| Retry/cancel operation | `POST /api/operations/{id}/retry` or `/cancel` | Persisted operation lifecycle |
| Upload ZIP | `POST /api/repositories/upload`, multipart `file` | Same durable import; traversal, special files and size boundaries enforced |
| Repository inventory | `GET /api/repositories/{id}` | Index coverage, exclusions, source snapshot, commit, branch, gaps; counters are not semantic understanding percentages |
| Repository graph | `GET /api/repositories/{id}/graph?engine=builtin` | Snapshot-scoped source nodes/relationships; use `auto` for optional Graphify with explicit fallback |
| Public graph | `GET /api/global-catalogue/graph?ecosystem=PyPI&package=requests&provider=local` | Public package/advisory/source relationships, freshness and attribution; not private applicability |
| Combined knowledge | `POST /api/repositories/{id}/knowledge-map`, `{"engine":"builtin","provider":"local","limit":25}` | `repository`, `public[]`, `links`, `applicability`, `snapshot_id`. Links cite exact manifests; exploitability stays unknown. Creates no jobs. Optional `snapshot_id` rejects stale requests |
| Source evidence | `GET /api/repositories/{id}/source?path=...&snapshot=...` | Resolve citations against the graph's snapshot |
| Agent graph evidence | `GET /api/repositories/{id}/agent-context?query=...&budget_tokens=3500` | Actual bounded graph context used by worker; optional job ID includes its catalogue evidence |
| Catalogue preview | `POST /api/repositories/{id}/applicability/catalogue`, `{"provider":"local"}` | Version matches, gaps, matched entries for intake; no jobs |
| Explicit proposal | `POST /api/repositories/{id}/applicability/intake`, `{"entry":<returned entry>,"snapshot_id":"..."}` | Rechecks current snapshot and public revision; job creation and evidence binding are atomic |
| Automation | `PATCH /api/repositories/{id}`, example below | Persisted source policy and schedule |
| Scan now | `POST /api/repositories/{id}/scan` | 202 durable refresh + scan + deduplicated intake |
| Worker health | `GET /api/health` | Worker/monitoring running or stopped |
| Provider/sandbox health | `POST /api/settings/health` | Actual probes; keep off initial dashboard critical path |

Example repository settings:

```json
{
  "security_source": "catalogue_local",
  "policies": {"security": "approve_first", "ticket": "approve_first"},
  "monitoring": true,
  "poll_interval": 300
}
```

`security_source` is `osv` (existing default live lookups), `catalogue_local`, or `catalogue_atlas`. Catalogue mode replaces direct OSV scanning to avoid two jobs from the two ingestion paths during the same scan. It retains rule-based analysis. Changing modes can encounter jobs made by the earlier source path; reconcile those manually. `catalogue_atlas` explicitly sends ecosystem/package names to the public Atlas reader; it never uploads source. Atlas errors fail the operation with unknown coverage, with no silent fallback. Default `local` graph queries make no cloud calls.

Repository monitoring rematches published catalogue records; it does not refresh public OSV records itself. Databricks/CLI publication is the separate source-refresh step. `scan_report.catalogue` preserves matches and limitations. A successful bounded catalogue scan still reports incomplete security coverage. Stale/withdrawn/nonmatching evidence invalidates pending dependent jobs; observed material revisions persist even when no new job is created. Older/conflicting revisions return 409.

The two graph node namespaces remain in their separate containers. `links[].source` resolves in `repository.nodes`, and `links[].target` in `public[].nodes`. Public entries may occur in several package graphs; deduplicate IDs when rendering one combined canvas. Repository graph relationships retain parser confidence labels. A package version match is not proof that a vulnerable function is reachable.

Jobs use existing endpoints: `GET /api/jobs?repo_id=...`, `GET /api/jobs/{id}` and `POST /api/jobs/{id}/{action}`. Actions: `approve` preparation, `decide`, `retry`, `cancel`, `reject`, `note`, `revise`, `accept`, `apply`. Pass `expected_version` for guarded UI changes; `decide` needs current `decision_id` and option ID, `accept/apply` need `revision_id`. See `/docs` and `backend/workflow.py` for exact decision payload. Note/revision bodies use `text`; team notes never enter prompts. Acceptance records exact-patch approval; separate apply writes only the managed local integration branch. No remote push or original checkout modification. Export uses `/api/jobs/{id}/patch` and `/files`.

## MongoDB Atlas: operator setup

1. In your existing Atlas project, create **database users**: a publisher with `readWrite` only on `ducktape_public`, and an app reader with `read` only on that database. Keep private repository projection credentials separate.
2. Add the laptop's trusted public IP to the Atlas IP access list. Keep TLS verification enabled. Use the existing cluster's Drivers connection string; put passwords in local `.env` or secret storage, never in frontend settings or command history.
3. Install optional driver: `python -m pip install -r requirements-cloud.txt` in the backend environment. With an administrator, review/run `integrations/global/atlas-setup.js` to create the exact-package and revision indexes.
4. Set app-reader configuration in ignored `.env`:

```dotenv
STEWARD_GLOBAL_ATLAS_ENABLED=1
STEWARD_GLOBAL_ATLAS_URI=mongodb+srv://<reader>:<password>@<cluster>/
STEWARD_GLOBAL_CATALOGUE_PATH=data/global-public.sqlite3
```

5. For publication, use a separate process with the publisher URI, then restore the reader URI for the app:

```sh
python scripts/integrations.py catalogue-ingest-osv --database data/global-public.sqlite3 --id GHSA-9wx4-h78v-vm56
python scripts/integrations.py catalogue-publish --database data/global-public.sqlite3
python scripts/integrations.py catalogue-query --atlas --ecosystem PyPI --package requests
```

6. Select `catalogue_atlas` only after the query succeeds. Local graphs and local catalogue matching need no Atlas account. Optional private graph projection additionally needs `STEWARD_ATLAS_ENABLED=1`, a separate `STEWARD_ATLAS_URI`, `STEWARD_ATLAS_DATABASE=ducktape`, and `STEWARD_ATLAS_GRAPH_MIRROR=1`, then explicit `POST /api/repositories/{id}/atlas-graph-mirror`. This sends source-derived paths/symbols and approved knowledge. It is not required for local graph retrieval. Private vector search additionally requires embeddings and its own Atlas vector index; follow [data platform guide](integrations/DATA_PLATFORMS.md).

Official configuration: [database users](https://www.mongodb.com/docs/atlas/security-add-mongodb-users/) and [IP access lists](https://www.mongodb.com/docs/atlas/security/ip-access-list/).

## Databricks: operator setup

C already recorded successful manual source curation; do not recreate the workspace. Existing job `486190608689437` is documented in [SCHEDULE.md](../integrations/global/SCHEDULE.md). The recorded daily trigger is paused; direct Databricks-to-Atlas TLS connectivity was unresolved. These are historical observations, not a fresh account probe.

1. Open Jobs & Pipelines in the existing workspace and locate that job. Verify the notebook uses the intended Duck Tape source revision, public-only source descriptors, existing warehouse, and maximum concurrent runs of one.
2. Run manually with `publish=false`. Confirm success and rows in `workspace.default.ducktape_global_revisions`. Repeat and verify unchanged material revision IDs. Use `integrations/global/schema.sql` only if that table needs initial setup.
3. For the supported manual relay, configure these in the operator process (separate from app-reader credentials):

```dotenv
STEWARD_GLOBAL_DATABRICKS_ENABLED=1
STEWARD_DATABRICKS_HOST=https://<your-workspace-host>
STEWARD_DATABRICKS_WAREHOUSE_ID=<existing-warehouse-id>
STEWARD_DATABRICKS_TOKEN=<server-only-token>
STEWARD_GLOBAL_ATLAS_ENABLED=1
STEWARD_GLOBAL_ATLAS_URI=<public-publisher-uri>
```

```sh
python scripts/integrations.py catalogue-restore --database data/global-relay.sqlite3
python scripts/integrations.py catalogue-query --database data/global-relay.sqlite3 --ecosystem PyPI --package requests
python scripts/integrations.py catalogue-publish --database data/global-relay.sqlite3
python scripts/integrations.py catalogue-query --atlas --ecosystem PyPI --package requests
```

Stop on a failed command. This path uses actual Delta output; it is a manual relay, not successful scheduled publication. To demo without Atlas, point the app's `STEWARD_GLOBAL_CATALOGUE_PATH` to this restored file and use `catalogue_local`.

4. To enable direct publication later, configure scoped publisher credentials through the notebook's secret scope, establish supported outbound connectivity with your Databricks/Atlas administrators, then manually run `publish=true` and verify Atlas. Only unpause the schedule after that path passes. Do not guess a stable IP from a one-off HTTPS probe or disable TLS. [Serverless network controls](https://docs.databricks.com/aws/en/security/network/serverless-network-security/) and [job scheduling](https://docs.databricks.com/aws/en/jobs/scheduled).

The recorded public pipeline has a bounded 100-revision restore and partial source-failure visibility across machines. Keep job failure output visible; do not equate a fresh-looking Atlas record with a successful latest refresh.

## When Adaption finishes

1. Record each original/augmented implementer and test-writer run ID, exact base-model ID, artifact hash, dataset revision and evaluation results. Wait for successful completion and `download_available`; download the best checkpoint through the dashboard or [official streaming download API](https://docs.adaptionlabs.ai/autoscientist/download-the-model/). Weights stay outside Git.
2. Inspect the artifact: a LoRA adapter needs its matching base model and a compatible inference runtime. A downloaded archive is not automatically available through Adaption's hosted model catalogue. Either use an account-provided serving ID or serve the base+adapter on hardware that fits it through an OpenAI-compatible endpoint. No GPU capacity or hosted deployment is assumed here.
3. For hosted Adaption, replace old provider/endpoint values in `.env`:

```dotenv
STEWARD_MODEL_PROVIDER=adaption
STEWARD_MODEL_ENDPOINT=https://api.prod.adaptionlabs.ai/api/v1
ADAPTION_API_KEY=<server-only-key>
STEWARD_MODEL=<available-general-model-or-stack-id>
STEWARD_IMPLEMENTATION_MODEL=<served-implementer-id>
STEWARD_REPAIR_MODEL=<served-implementer-id>
STEWARD_REGRESSION_MODEL=<served-test-writer-id>
STEWARD_ACCEPTANCE_MODEL=<served-test-writer-id>
```

For a compatible local/remote server, use `STEWARD_MODEL_PROVIDER=openai`, its `/v1` endpoint, and its registered model aliases instead; custom authentication uses `STEWARD_MODEL_KEY`. All roles currently share one provider endpoint; serve both adapters there or use one qualified model for all roles. Don't set a training run ID as a serving model ID unless the service actually exposes it. Default `STEWARD_MODEL` must serve planning/investigation/review too. [Adaption inference setup](https://docs.adaptionlabs.ai/inference-stacks/quickstart/).

4. Ensure Docker is running in Linux-container mode; `python scripts/setup.py --sandbox` builds the reviewed pinned execution images. Imported/model-generated code requires that sandbox, even for a small demo. Restart `run.py` and inspect `POST /api/settings/health`.
5. Verify output compatibility: runtime expects structured JSON schemas in `backend/model_schemas.py`, not Markdown diffs or arbitrary tool calls. The training-preparation files used varied formats; the actual completed artifact must pass this check. No permissive shell/tool execution fallback was added.
6. Run the all-role schema smoke, then application qualification, using the CLI, which loads the same `.env` before backend imports:

```sh
python scripts/evaluate_models.py --smoke --output data/model-smoke.json
python scripts/evaluate_models.py --driver scripts.qualification_driver:driver --output data/model-qualification.json
```

Run original and augmented configurations separately on the same held-out cases. Require `qualified: true`; unavailable/failed cases remain open. `python scripts/acceptance.py --tier live --url http://127.0.0.1:8781` additionally exercises provider/retrieval/imported workflows/browser checks and may consume inference credits.

After those gates pass, demo import -> graph/context -> sourced proposal -> approve -> decision if needed -> patch -> checks -> note/revision -> exact-patch approval -> managed apply/export. Model quality and serving compatibility remain external gates; backend contract tests cannot guarantee them.
