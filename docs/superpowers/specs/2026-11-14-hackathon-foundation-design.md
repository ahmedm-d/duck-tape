# Duck Tape hackathon foundation

## Intent
Preserve all commits unchanged during this task. Prepare three developers to improve an existing autonomous repository-maintenance product in parallel. Target the Autonomous Agents track with one evidence-backed live repair, and pursue Adaption only through demonstrated use. Hugging Face/Adaption run the product agent, Atlas holds its evidence memory, and Databricks evaluates its runs. These belong to Duck Tape itself, never to the code it maintains. No prize or eligibility guarantee.

## Review and constraints
The starting commit is `1d094be`: real local workflow, worker, SQLite, retrieval, Git, sandbox and review UI. The retained 3B baseline is not qualified (2 passes, 8 model failures, 2 incomplete). The previous aggregate recorded one test-fixture failure with a passing focused correction. See docs/hackathon/REPO_REVIEW.md for the review.

The supplied event rules require code written during the event and a public repository. Existing code and the earlier history reset must be disclosed; a new commit is not proof of creation time. Keep the repository private until the team resolves provenance and publication. Never store personal pass codes or credentials.

## Approach chosen by the implementer
Reuse the current worker/provider contracts and add explicit optional adapters. A cloud database replacement would risk transaction/lease safety; a new microservice architecture would delay the demo. Keep SQLite authoritative. Atlas stores a source-derived vector projection, resolving returned IDs against local evidence. Databricks holds only allowlisted run metrics, including failure outcomes, for measured comparison.

Adaption uses its documented Responses API, with hosted tools/memory/storage disabled and strict local response validation. Hugging Face uses its documented inference-router chat API. Availability, schema validity and task qualification are distinct. No training or live-cloud success is claimed from contract tests.

## Ownership and interfaces
- A, agent intelligence: providers, model schemas, worker, model qualification; preserve `complete(ProviderRequest)` and telemetry.
- B, repository memory/evaluation: retrieval, `backend/data_platforms.py`, `scripts/integrations.py`, `integrations/**`, data tests. Keep repository/snapshot/model boundaries.
- C, experience/integration: frontend, API composition, root config/lockfiles, CI, team docs and final integration.
- Only C changes shared files; A/B send small requested contract/dependency changes. Each developer owns a branch/worktree; no force pushes to shared history.

Data interfaces: `status()`, `atlas_index_definition()`, `atlas_sync(repo_id, snapshot_id)`, `atlas_search(repo_id, snapshot_id, query, limit=12)`, `databricks_sync(run_id)`, `databricks_summary()`. CLI exposes these explicitly. `GET /api/integrations` provides network-free readiness, never credentials. The operator CLI reads this state; Settings retains the existing agent-provider configuration. No sponsor catalogue or client-repository integration UI is added. Atlas is not automatically wired into worker retrieval in this foundation; that is B's next independently testable milestone.

## Verification and limits
Contract tests use mocked remote transports, plus actual local database records. Verify credential separation, no automatic egress, snapshot isolation, unknown usage, error handling, CLI dispatch, API validation and browser state. Run provider/data regressions and the deterministic suite/build/browser gates. Live sponsor tests require accounts, keys and provisioned resources; record them as not run when absent. Never spend credits or provision services as a side effect of startup/status.
