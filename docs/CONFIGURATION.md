# Configuration

Duck Tape retains the existing `STEWARD_*` configuration names and internal storage/runtime identifiers for compatibility. Historical evidence and existing managed branches keep their recorded identities. New patch/ZIP downloads use `duck-tape-*` names; export automation should use the response filename rather than the old prefix.

Settings can persist non-secret provider endpoint, protocol, model IDs, context limit and embedding model. Explicit process environment overrides those settings. `run.py` also reads the **project's own** `.env` as literal `STEWARD_NAME=value` entries plus `ADAPTION_API_KEY` and `HF_TOKEN`. It does not evaluate shell expressions or read imported repository environment files.

| Variable | Meaning |
|---|---|
| `STEWARD_DATA_DIR` | Persistent workspace; default `data/workspace/` |
| `STEWARD_ATLAS_REQUIRED` | Defaults to `1`; `run.py` requires a TLS-verified private Atlas ping; use `0` only for isolated tests/demo controllers |
| `STEWARD_ATLAS_ENABLED`, `STEWARD_ATLAS_URI`, `STEWARD_ATLAS_DIMENSIONS` | Required private Atlas enablement, secret connection URI and exact embedding dimensions; see the data-platform guide for roles/index setup |
| `STEWARD_AUTH_REQUIRED` | `run.py` sets `1` unless given: every `/api` request except `/api/health` and `/api/auth/config` needs a valid Supabase session, and startup fails unless Supabase Auth accepts the key. Importing `backend.main` directly (tests, in-process controllers) leaves it off; `0` only for tests/CI |
| `STEWARD_SUPABASE_URL`, `STEWARD_SUPABASE_PUBLISHABLE_KEY` | Supabase project URL (`https://<ref>.supabase.co`; a copied `/rest/v1` suffix is stripped) and **publishable** browser key. The secret key is refused. Tokens are checked with `GET /auth/v1/user` and cached for at most 60 s |
| `STEWARD_MODEL_PROVIDER` | `ollama`, `openai`, `adaption` or `huggingface` protocol |
| `STEWARD_MODEL_ENDPOINT` | Base URL for the selected protocol; hosted defaults are documented in the inference guide |
| `STEWARD_MODEL` | Default model ID |
| `STEWARD_SMALL_MODEL` | Optional smaller triage/summary model |
| `STEWARD_CONTEXT_LIMIT` | Complete prompt/context budget; default 16000 |
| `STEWARD_MAX_OUTPUT_TOKENS` | Output reservation; default 4096 |
| `STEWARD_MODEL_TIMEOUT` | Per-attempt provider timeout in seconds |
| `STEWARD_EMBEDDING_MODEL` | Real embedding model; absent means keyword/graph fallback |
| `STEWARD_EMBEDDING_ENDPOINT` | Optional separate embedding URL; Adaption/Hugging Face require a separate OpenAI-compatible endpoint |
| `STEWARD_EMBEDDING_KEY` | Optional credential for a separate embedding endpoint; coding credentials are never forwarded there |
| `STEWARD_MODEL_KEY` | Optional server-only credential; never saved by UI |
| `STEWARD_SANDBOX_PYTHON_IMAGE` / `STEWARD_SANDBOX_NODE_IMAGE` | Locally built runtime tags; resolved to immutable image IDs for checks |

Roles can override defaults with `STEWARD_<ROLE>_MODEL`, `_CONTEXT_LIMIT`, `_MAX_OUTPUT_TOKENS` and `_STRUCTURED_OUTPUT`. Roles include INVESTIGATION, PLANNING, REGRESSION, ACCEPTANCE, IMPLEMENTATION and REVIEW. One installed model may fill multiple roles. Capability health is separate from the application qualification results.

Example compatible open-weight endpoint:

```dotenv
STEWARD_MODEL_PROVIDER=openai
STEWARD_MODEL_ENDPOINT=http://127.0.0.1:11436/v1
STEWARD_MODEL=qwen-coder-3b
STEWARD_CONTEXT_LIMIT=16384
STEWARD_MAX_OUTPUT_TOKENS=1536
STEWARD_MODEL_TIMEOUT=240
```

For an existing Ollama installation, select its installed model in Settings with endpoint `http://127.0.0.1:11434` and protocol `ollama`. No proprietary inference API is required. HTTP provider endpoints are permitted only on loopback; remote endpoints must use HTTPS. A healthy small model may still fail coding tasks; failures are retained rather than bypassing checks.

This workspace also has the 146 MB [official Nomic Embed v1.5 Q8 model](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF) installed at a pinned revision with a verified SHA-256. Set `STEWARD_EMBEDDING_MODEL=nomic-embed-text-v1.5` and `STEWARD_EMBEDDING_ENDPOINT=http://127.0.0.1:11437/v1` to use its separate CPU endpoint. Query/document prefixes follow the model's task format. Vectors persist with repository, snapshot, endpoint, model and content identity; failures visibly fall back to keyword/graph retrieval.

Project-local `data/model-runtime/installed.json` and `embedding-installed.json` describe explicitly installed runners and weights. Startup only launches matching configured endpoints after checking file hashes; it does not download weights. Installations are machine-local and ignored by Git. On another machine, configure an existing compatible endpoint or install a runner and model explicitly.

Local model runners are persistent shared services. Stopping an API instance stops its supervised worker, but leaves coding and embedding runners available for other local instances. Startup reuses a healthy matching endpoint. To reclaim their memory, first stop all Duck Tape instances using them, identify the listening PID for each configured loopback port, and verify that PID's executable is the project's `data/model-runtime/runner/llama-server.exe` before stopping it. On Windows, `Get-NetTCPConnection -LocalPort 11436,11437 -State Listen | Select-Object LocalPort,OwningProcess` identifies the default installed ports; inspect each PID with `Get-Process -Id <PID> | Select-Object Id,Path`, then use `Stop-Process -Id <verified-PID>`. Do not stop processes by a generic name or stop externally managed Ollama/remote endpoints. Ports may differ from these examples; use the saved manifests and Settings.

On Windows, keep the project and `STEWARD_DATA_DIR` paths short. Git initialization can still encounter the Windows path limit in deeply nested candidate directories even with Git long-path support enabled. The qualification harness accepts `STEWARD_QUALIFICATION_ROOT` for a short, separate evaluation workspace. A path failure is an infrastructure failure, not a successful model result. Existing absolute evidence references are why [backup restoration](MAINTENANCE.md) uses the original workspace location.

## Supported execution

Run `python scripts/setup.py --sandbox` to build the checked-in, digest-pinned Python 3.12 and Node 22 runtime images using an existing Linux Docker daemon. No privileged Docker installation is attempted.

- Python: standard-library unittest; pytest when supplied by exact supported dependencies. Dependencies use pinned `requirements.txt` or `uv.lock` registry wheels compatible with Linux x86_64 CPython 3.12.
- Node: npm `package-lock.json` v2/v3 with exact versions and strong artifact integrity. Recognized commands include Node tests, Vitest, Jest, TypeScript checks and Vite builds.
- Feature acceptance selectors for Jest/Vitest currently support root-level literal test names. Nested `describe` paths are not qualified; an unmatched or skipped target fails closed. Actual pytest, Jest and Vitest selected-target checks are recorded in [framework evidence](evidence/feature-frameworks/README.md).
- Artifacts are downloaded through the controller and installed offline. Source builds, custom registries, Git dependencies, links, lifecycle hooks and unfamiliar commands remain explicit unsupported policy decisions.
- The fixed sample fallback needs only Python and Git. Arbitrary source/model output always needs the sandbox.

Use `scripts/doctor.py --output docs/evidence/doctor-local.json` to record actual health. `--require-live` requires both provider and sandbox availability; it does not certify model quality.

## Monitoring and intake

Repository settings choose automatic or approve-first preparation independently for ticket, request, security, analysis and research signals. Poll intervals range from 30 seconds to 24 hours. Last attempt/success/error/next run are persisted; monitoring stops when the backend is stopped and resumes on startup.

`POST /api/intake` accepts:

```json
{"repo_id":"...","title":"Quantity is ignored","description":"Two items should cost twice one item.","source":"ticket","type":"fix","priority":"high","source_key":"tracker:123","source_url":"https://example.com/tickets/123"}
```

A stable source key deduplicates unchanged evidence; changed underlying evidence can create a new proposal. Rejection and snooze survive unrelated scans. OSV scans use actual resolved versions from supported npm locks, pinned requirements and uv locks, retaining advisory provenance, timestamps, cache and incomplete/error counts. A version match is not proof of exploitability.

`POST /api/repositories/{id}/research` accepts `{title,text,url?}`. Public URL content is fetched safely and attributed; pasted text is one-time intake. Research findings are hypotheses until checks or comparable benchmarks support a claim.

Imports, refresh, scans and summary refresh return persistent operation records. Poll `GET /api/operations/{id}`; failed work supports retry and pending work supports cancellation. Source refresh invalidates dependent jobs, summaries, approvals and stale knowledge. Applying an approved revision writes only to its managed `steward/integration` branch.

## Duck Tape internal services

Hugging Face and Adaption run Duck Tape's models; MongoDB Atlas projects its evidence memory; Databricks curates public knowledge and evaluates agent runs. These are operator/server settings, never additions to imported client repositories. See the [internal stack guide](integrations/README.md), [inference configuration](integrations/INFERENCE.md), and [Atlas/Databricks setup](integrations/DATA_PLATFORMS.md). Required mode automatically projects private repository snapshots and approved knowledge; public catalogue reads use separate credentials. Metrics export remains explicit. Live cloud/model acceptance remains separate from configuration and offline tests.
