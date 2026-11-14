# Architecture

FastAPI serves the compiled React application and `/api` on one loopback origin. `run.py` supervises a separate `backend.worker_main` process with bounded restart backoff. The API queues work; workers own imports, refreshes, scans, summaries and preparation. SQLite WAL stores additive migrations, records, leases, full-text chunks and persistent vectors.

## Durable work and review

`workflow.py` enforces the persisted state machine under `BEGIN IMMEDIATE`. Every claim has a unique lease generation. Heartbeats, cancellation predicates and guarded publication prevent an expired worker from publishing late revisions. Interrupted work remains visible and is reclaimed after lease expiry.

`worker.py` retrieves immutable evidence, investigates, plans, pauses for decisions, applies exact structured edits, verifies and requests a separate model review. Stage inputs and outputs are persisted. A job has bounded model calls, repair cycles, time and output budgets. Decisions and revisions invalidate dependent artifacts. Team notes are a separate store and are never model inputs.

The last generated candidate is captured before verification starts. If execution is unavailable or fails, its unverified diff remains inspectable and exportable; it is not a reviewable revision and cannot be approved or applied. Feature acceptance maps each criterion to a concrete test selected and observed by the controller. The implementation must preserve those prepared acceptance-test bytes.

Approval binds the exact revision, diff hash, input fingerprint and check evidence. `integration.py` journals a managed application before touching its isolated checkout. It checks source freshness, approved bytes, managed HEAD and overlaps, applies and commits, then atomically marks Completed. Startup reconciliation recovers a crash after filesystem/Git work; conflicts remain actionable. An advanced integration base needs refreshed preparation. Export alone never marks Completed.

## Repository model

`snapshots.py` captures immutable source blobs independently of the search index. Repository identity, source origin, branch/commit where available, manifest hash, modes and content hashes are retained. Refresh captures upstream content into a new managed directory rather than overwriting the imported source checkout. ZIP replacement follows the same pipeline.

`repository.py` inventories source, tests, manifests, configuration, documentation, CI and gaps. `symbols.py` uses Python AST and TypeScript/JavaScript Tree-sitter parsing for scoped symbols, imports, static references, routes and relationships. Resolution capabilities and inferred/dynamic gaps are explicit.

`retrieval.py` queries persisted chunks, combines exact path/symbol and full-text scores with optional real vectors, expands the actual graph and deduplicates a bounded evidence bundle. Every source range binds repository, immutable snapshot and content hash. Historical evidence is accessible as historical; deleted files never remain current. Approved scoped knowledge is reserved in the context budget. `summaries.py` stores source-linked module/package/repository summaries with a structural fallback.

## Execution boundary

A checkout isolates changes from the source; it is not the execution sandbox. `sandbox.py` requires Linux Docker and actively probes the pinned runtime images. Checks run as UID/GID 10001, with network disabled, read-only root, all capabilities dropped, no-new-privileges, CPU/memory/PID/time/log limits and bounded tmpfs storage. There are no host source mounts, host credentials or Docker socket inside containers. Files are copied by bounded archive. Cancellation removes the owned execution containers.

`dependencies.py` fetches exact public registry artifacts through validated controller-side HTTPS, checks integrity, then installs them offline inside the sandbox. npm lifecycle hooks and Python source builds are unsupported. Dependency policy gaps become explicit decisions/blockers, not host commands. `verification.py` preserves baseline results, overlays regression tests on the unchanged base, checks the candidate, and binds records to source, patch, image and commands.

When the sandbox is unavailable, arbitrary imports and live model edits are never executed on the host. The fallback permits only fixed reviewed sample source, fixture patches and authored test/benchmark commands.

## Models and untrusted evidence

`providers.py` implements Adaption Responses, Hugging Face Inference Providers, Ollama and OpenAI-compatible inference. Hosted modes use a separate compatible endpoint for embeddings. It validates schemas, bounds transport and retries, routes by task/context/risk, and records actual model, latency and available usage. Missing usage remains unknown. Cache inputs include source/context, policy versions, instructions, decisions, revision and model/prompt versions. Fixtures remain explicitly sample-only.

Repository text, tickets, articles and model output are data, never permission to run commands or reveal credentials. Public fetches validate and pin DNS, recheck redirects and reject private addresses. Git transport uses a restricted local broker, a bare fetch and raw blob export; it does not check out or run imported hooks/filters. ZIP extraction rejects traversal, symlinks, device names and expansion limits. React renders imported text as text. Provider credentials are server-only. Demo member labels are not access control.

## Internal memory and evaluation services

MongoDB Atlas and Databricks belong to Duck Tape's infrastructure, not its imported repositories. `data_platforms.py` projects snapshot-scoped chunk vectors to Atlas and resolves query results against local source evidence. It exports bounded terminal-run metrics to Databricks using parameterized SQL; failures and unknown usage remain visible. `scripts/integrations.py` exposes explicit operator commands. These paths are not yet automatically called by worker retrieval/completion. SQLite retains workflow transactions, leases and approvals. See the [internal stack guide](integrations/README.md) for current capability and live acceptance boundaries.

The planned global knowledge layer adds public-source ingestion, normalization, deduplication and freshness processing in Databricks, publishing curated revisions to separate Atlas catalogue collections. B owns private repository profiles and local applicability matching; C owns the public catalogue/pipeline and shared cloud adapter; A consumes the bounded evidence in the existing worker. Private profiles and approved decisions stay out of the public catalogue. This layer is new work, described in the [knowledge contract](hackathon/KNOWLEDGE_CONTRACT.md), not a current runtime capability.
