# Three kickoff prompts

Read [TEAM_PLAN.md](TEAM_PLAN.md), [REPO_REVIEW.md](REPO_REVIEW.md), [AGENTS.md](../../AGENTS.md) and [CONTRIBUTING.md](../../CONTRIBUTING.md). Assign actual people to A/B/C, then copy the matching prompt into that person's coding session. All three start from the same reviewed foundation commit with separate branches/worktrees or clones. Inspect current files because the foundation may have changed.

**Team handoff:** use the reviewed foundation on `main` as the common starting point. Fetch the latest `origin/main` before creating each developer's branch/worktree; keep local environment files and runtime databases separate.

These prompts authorize implementation within assigned files. They do not authorize false provenance statements, credential disclosure or representing fixture inference as live. The supplied deadline is Sunday 15 November 2026 at 12:00 Europe/London. Resolve existing-code eligibility with organizers; resetting Git history does not establish provenance.

The services below are Duck Tape's internal infrastructure. Their adapters do not install sponsor integrations into imported/client repositories. Current adapters, configured accounts, live acceptance and future automatic wiring are separate milestones.

## A: internal agent inference and Adaption/Hugging Face

~~~text
Review repository rules and current files before editing: AGENTS.md, CONTRIBUTING.md, README.md, docs/VERIFICATION.md, docs/hackathon/TEAM_PLAN.md, docs/hackathon/REPO_REVIEW.md and docs/integrations/INFERENCE.md. Inspect git status and the foundation commit. Use your own branch/worktree, for example feat/agent/live-repair, from the team's agreed foundation commit. Preserve existing changes.

You own backend/providers.py, backend/model_schemas.py, backend/worker.py, provider/worker/model tests, scripts/evaluate_models.py, scripts/qualification_driver.py and docs/integrations/INFERENCE.md. Do not edit B's retrieval/data-platform files or C's frontend, API routes, root config/locks, CI or shared docs. Send C exact shared-file changes and agree contract changes with B before implementing them.

These services are Duck Tape's internal stack: Hugging Face/Adaption run and potentially adapt its models, Atlas holds its repository-evidence memory, and Databricks evaluates its agent runs to guide improvements. Imported/client repositories only supply evidence and receive reviewed maintenance patches. Never add these services, their SDKs, keys or setup flows to an imported/client repository.

Deliver one generic imported Python cross-module repair through Duck Tape's REAL internal inference, baseline/failing-before regression/passing-after checks, and model review. Use the existing Adaption/Hugging Face adapters and typed schemas; inspect their actual implementation and documented API behavior. This is not the labelled sample-fixture path. Preserve sandboxing, validation, data-preserving edits, cancellation, exact-revision approval and telemetry. Test a deliberately unsafe proposal and record the actual outcome, including failure to reject it.

First hour: verify real endpoint credentials/model access without printing secrets, run current focused provider tests, then perform one bounded actual API request. Set STEWARD_MODEL_PROVIDER=adaption or huggingface and an explicit STEWARD_MODEL. Read INFERENCE.md before supplying keys/custom endpoints. Adaption JSON is locally validated; Hugging Face schema support depends on the selected provider/model. If credentials/runtime are missing, report that blocker and continue deterministic work; never fabricate a response or claim live acceptance. Health is not coding qualification.

After the base patch works, attempt an Adaption adaptation experiment for Duck Tape's own agent behavior only if time/access allow. The foundation contains no completed training/adaptation experiment. Separate training examples from held-out evaluation. Compare the same frozen tasks/settings, record base/adapted model identity, failures, latency and known token usage, and do not promise an uplift. Never weaken checks to make the demo pass.

Run in the installed environment:
python -m pytest tests/test_sponsor_providers.py tests/test_provider_contracts.py tests/test_test_role_schema.py -q
Then run relevant worker/model-evaluation regressions and actual live qualification cases using the existing scripts. Add the smallest regression check needed for new non-trivial behavior. Record exact commands, outcomes, evidence paths, unknown usage and external blockers. Distinguish deterministic fixtures, live inference and interrupted runs.

Deliver small reviewable commits/handoffs with behavior, tests and limitations. Use Conventional Commit subjects: type(scope): lowercase imperative, at most 72 characters; for example feat(agent): add measured Adaption review. Saturday 18:00 London is the first integrated live journey; freeze features by Saturday 22:00. C owns aggregate checks and the Sunday-before-noon submission. No cross-owner edits and no completion/sponsor claims without evidence.
~~~

## B: internal Atlas memory and Databricks evaluation

~~~text
Review repository rules and current files before editing: AGENTS.md, CONTRIBUTING.md, README.md, docs/VERIFICATION.md, docs/hackathon/TEAM_PLAN.md, docs/hackathon/REPO_REVIEW.md and docs/integrations/DATA_PLATFORMS.md. Inspect git status and the foundation commit. Use your own branch/worktree, for example feat/data/atlas-retrieval, from the team's agreed foundation commit. Preserve existing changes.

You own backend/retrieval.py, backend/data_platforms.py, scripts/integrations.py, integrations/**, retrieval/data-platform tests, retrieval evaluation scripts and docs/integrations/DATA_PLATFORMS.md. Do not edit A's provider/schema/worker/model-evaluation files or C's frontend, API routes, root config/locks, CI or shared docs. Send C exact shared-file/documentation changes. Agree contracts with A before changing them.

These services are Duck Tape's internal stack: Hugging Face/Adaption run and potentially adapt its models, Atlas holds its repository-evidence memory, and Databricks evaluates its agent runs to guide improvements. Imported/client repositories only supply evidence and receive reviewed maintenance patches. Never add these services, their SDKs, keys or setup flows to an imported/client repository.

The foundation supplies opt-in projection/search for Duck Tape's internal Atlas memory and explicit export of its own terminal-run metrics to Databricks. It does NOT yet connect Atlas to generic worker retrieval or automatically upload metrics. Complete the smallest useful integration: make the worker use Atlas candidates through the existing retrieval entry point, resolve chunk IDs to local source evidence, and preserve repository/snapshot/model isolation, hashes, citations, cancellation and context budgets. Do not add source text to Atlas projection. A configured remote embedding provider still receives text it embeds; disclose this separately. Keep SQLite authoritative for workflow transactions/knowledge approval. Preserve explicit visible fallback; do not present local fallback as Atlas success.

First hour: inspect python scripts/integrations.py --help and the guide in Duck Tape's own checkout. Provision/configure Duck Tape's operator accounts and server environment, not the imported repository. Verify credentials, permissions, vector dimensions/index and Databricks warehouse/table access without printing secrets. Use only intended demo data for explicit cloud operations. If services/credentials are unavailable, report blockers and continue deterministic contracts, not invented cloud results.

CLI commands are status; atlas-index; atlas-sync --repo ID --snapshot ID; atlas-search --repo ID --snapshot ID --query TEXT --limit 12; databricks-sync --run ID; databricks-summary. Run them through python scripts/integrations.py after inspecting current help. Explicitly enable/configure each service using the guide. Do not add automatic uploads on startup or worker completion.

Export Duck Tape's terminal evaluation metrics, including failures, using the explicit command. Run the provided Databricks comparison SQL against actual exported rows. The foundation omits model names and retrieval labels; define explicit nonsecret run-to-variant mappings/cohorts before comparing fixed task/model/retrieval versions. Distinguish complete workflows from independent verification. Keep unknown usage unknown. Reuse existing stage/check telemetry and evaluation tools instead of adding an orchestration framework. Use measured comparisons to guide improvements to Duck Tape's retrieval/prompts/models; an automatic improvement loop is future work, not a foundation capability.

Test cross-repository/snapshot/model isolation, stale or invalid returned IDs, budgets, service failures/fallback and duplicate export behavior as relevant to your changes. Run python -m pytest tests/test_data_platforms.py -q plus affected retrieval tests, then actual bounded Atlas/Databricks acceptance with credentials. Record commands, redacted service/index/model identity, row counts, query results and blockers. Connectivity alone does not prove worker usage or retrieval improvement.

Deliver small reviewable commits/handoffs with evidence. Use Conventional Commit subjects: type(scope): lowercase imperative, at most 72 characters; for example feat(data): rank snapshot evidence from Atlas. Aim for integrated journey Saturday 18:00 London and comparison by 20:00; freeze features by 22:00. C owns final gates/submission. No cross-owner edits and no integration/uplift claims without actual evidence.
~~~

## C: demo experience, integration and submission

~~~text
Review repository rules and current files before editing: AGENTS.md, CONTRIBUTING.md, README.md, docs/VERIFICATION.md, docs/hackathon/TEAM_PLAN.md, docs/hackathon/REPO_REVIEW.md, docs/hackathon/SUBMISSION.md, both integration guides and the frontend/API. Inspect git status and the foundation commit. Use your own branch/worktree, for example feat/ui/demo-evidence, from the team's agreed foundation commit. Preserve existing changes.

You own frontend/**, backend/main.py, root config/dependencies/locks, README, CI, AGENTS.md, CONTRIBUTING.md and shared docs; A/B own their respective integration guides. A owns model/provider/schema/worker code; B owns retrieval/data-platform/integration CLI code. Do not edit their files without a handoff. You are the only editor of frontend/src/main.tsx and the integrator of shared-file changes.

These services are Duck Tape's internal stack: Hugging Face/Adaption run and potentially adapt its models, Atlas holds its repository-evidence memory, and Databricks evaluates its agent runs to guide improvements. Imported/client repositories only supply evidence and receive reviewed maintenance patches. Never add these services, their SDKs, keys or setup flows to an imported/client repository.

Make one short demo understandable: live versus fixture mode; request and snapshot; retrieved source and approved decision; regression failure before the patch; success after; exact-revision approval. Show the actual seeded unsafe-review outcome and measured comparison when available. Build on existing maintenance/evidence screens. Infrastructure configuration belongs to Duck Tape's operator/server environment; do not add customer-facing sponsor setup cards or installation flows. Server/CLI status reports configuration, not connectivity, live model quality or actual Atlas worker usage. Keep current adapters, future wiring and live acceptance distinct. Do not add automatic uploads or imply unverified capabilities exist.

First hour: start the local app, inspect maintenance evidence and server diagnostics as needed, establish focused build/browser baseline and agree API/retrieval/metrics contracts with A/B. Keep unavailable services visible. Ask A/B for exact shared config/route/dependency/docs changes and integrate small reviewed commits. Keep the documented loopback/local boundary; this is not a production authenticated multi-user deployment.

Run npm run build and affected browser tests for UI changes. Run current deterministic acceptance, affected provider/data regressions and the final actual demo on the integrated commit, using isolated test/demo data and documented commands. Report actual results/blockers, not historical totals as new results. Live acceptance needs actual credentials, Docker and service access. Fixture checks do not substitute for live acceptance.

Prepare a roughly three-minute demo and labelled backup recording. Fill docs/hackathon/SUBMISSION.md with the actual public repo URL, commit, team, models/APIs, evidence and limitations. Keep credentials and personal access codes out of files/logs. Initial commit 1d094be contains substantial existing code: disclose that baseline and resolve eligibility with organizers. Never attest to all-event authorship merely because Git history was reset.

Use Conventional Commit subjects: type(scope): lowercase imperative, at most 72 characters; for example feat(ui): show exact regression evidence. Integrate small reviewed changes with test evidence; do not force-push shared branches or reset teammates' work. Target integrated demo Saturday 18:00 London, feature freeze 22:00, fresh rehearsal Sunday 09:00, public submission checks by 10:00 and submission by 11:00 before Sunday 15 November 2026 noon. Confirm requirements and retain real submission confirmation. No guarantee of winning and no claims without evidence.
~~~
