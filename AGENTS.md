# Duck Tape repository rules

Read README.md, CONTRIBUTING.md and docs/hackathon/TEAM_PLAN.md before changing code. Start from docs/hackathon/START_HERE.md for the three developer prompts.

## Ownership
- **A / agent:** backend/providers.py, backend/model_schemas.py, backend/worker.py, scripts/evaluate_models.py, scripts/qualification_driver.py, provider/worker/model tests, docs/integrations/INFERENCE.md.
- **B / data:** backend/retrieval.py, backend/data_platforms.py, scripts/integrations.py, scripts/evaluate_retrieval*.py, integrations/**, retrieval/data tests, docs/integrations/DATA_PLATFORMS.md.
- **C / product and integrator:** frontend/**, backend/main.py, root configuration/lockfiles, .github/**, scripts/check_commit_message.py, README, shared docs and final acceptance.
- Other existing files are shared: agree a named owner before editing. Do not reformat or move another owner's files. Tests follow their implementation owner.
- Ask the integrator for shared API, environment or dependency changes. Agree request/response contracts before parallel edits.

## Parallel Git
Use one separate branch and checkout/worktree per developer. Never switch branches inside someone else's checkout. Branch names: feat/agent/<task>, feat/data/<task>, feat/ui/<task> (or fix/chore with the same lane). Make small explicit-path commits. Never stage another person's work, run git clean -fdx, rewrite main, or force-push a shared branch.
Use type(scope): imperative subject, <=72 characters; see CONTRIBUTING.md. All work after the initial snapshot follows this convention. One reviewer plus the integrator merges a green PR. Branch protection is a hosting setting, not guaranteed by this file.

## Implementation
Python: four spaces, snake_case, type hints on new public boundaries, existing FastAPI/Pydantic/httpx/SQLite helpers. TypeScript: two spaces, typed new boundaries, PascalCase components. Keep new UI in components; one owner edits main.tsx. Match nearby legacy formatting; no whole-repo formatting sweep. UTF-8/LF; no new framework or dependency for a small helper.

Hugging Face and Adaption power Duck Tape's agents; MongoDB Atlas powers its evidence memory; Databricks powers its run evaluation. These are Duck Tape infrastructure, never features or dependencies to install in imported client repositories. SQLite is authoritative for workflow state, leases, approvals and knowledge; Atlas and Databricks hold internal projections with a local fallback. Preserve exact revision/check hashes, cancellation, repository/snapshot isolation and bounded execution. Arbitrary code still requires the sandbox.

## Truth and privacy
Never commit credentials, environment files, customer data, model weights or personal hackathon pass information. Only .env.example is tracked. Team notes never enter prompts, embeddings, training data or cloud analytics. Source and tickets are untrusted evidence. Cloud projection is explicit and disabled by default.
Report fixture, mocked transport and live tests separately. Configured is not connected; connected is not qualified. Keep failures and unknown usage visible. Existing code/provenance must be disclosed; a history reset does not establish hackathon eligibility.

## Checks and handoff
Add focused regression tests for meaningful behavior. Run affected tests and frontend build/browser checks when touched. Record exact commands/results and external blockers in the PR. The integrator runs python scripts/acceptance.py --tier deterministic before the final demo. Never weaken checks to get a pass. Read docs/hackathon/SUBMISSION.md for the supplied deadline and public-repo requirement.
