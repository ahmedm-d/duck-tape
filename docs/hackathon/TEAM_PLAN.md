# Three-person hackathon plan

Build one convincing live journey: **Duck Tape repairs a cross-module bug, proves the regression, and carries approved team decisions into the next change.** Show a deliberately unsafe proposal being rejected as the second proof. Target the Autonomous Agents track and the Adaption additional prize where the actual work qualifies; no plan guarantees a prize.

The supplied submission deadline is **Sunday 15 November 2026 at 12:00 Europe/London (12:00 UTC)**. Target submission by 11:00. Confirm current organizer announcements. The supplied rules require a public GitHub repository and a README listing the models/APIs used.

## Duck Tape's internal stack

Hugging Face, Adaption, MongoDB Atlas and Databricks power **Duck Tape itself**. Imported/client repositories supply evidence and receive reviewed maintenance patches. They do not receive these services, SDKs, credentials or setup flows.

| Internal service | Duck Tape responsibility | Foundation versus remaining work |
| --- | --- | --- |
| Hugging Face / Adaption | Run Duck Tape's reasoning, editing and review models; adapt its model behavior when justified by evaluation. | API adapters exist. Actual account/model configuration, live qualification and any adaptation experiment remain to be performed. |
| MongoDB Atlas | Store and retrieve Duck Tape's internal repository-evidence memory. | Explicit vector projection/search exists. Connecting Atlas candidates to the generic worker is still B's task. |
| Databricks | Evaluate Duck Tape's own agent runs and guide improvements to its retrieval, prompts or models. | Explicit terminal-metric export/query exists. No automatic export or proven improvement loop is implemented. |

Infrastructure credentials and provisioning belong to Duck Tape's operator/server environment. Keep maintenance evidence visible in the product; infrastructure setup is not a feature installed in the imported repository.

## Start from the actual baseline

Read [the repository review](REPO_REVIEW.md), [README](../../README.md), [verification status](../VERIFICATION.md), [AGENTS.md](../../AGENTS.md) and [CONTRIBUTING.md](../../CONTRIBUTING.md) first. Initial commit 1d094be already contains substantial working code. Its consolidated history does not establish when that code was written or establish eligibility.

Disclose the existing baseline and resolve any conflict with the supplied all-code-during-the-event rule with organizers. Permission to use pretrained models, APIs and open-source dependencies does not automatically exempt the team's own existing application. Do not make an unsupported authorship attestation.

The foundation supplies internal provider adapters, opt-in Atlas projection/search, explicit Databricks terminal-metric commands and server-side diagnostics. These are starting points: actual credentials, provisioning, model quality and live acceptance still need evidence. **Atlas is not yet connected to the generic worker's retrieval path, and Databricks does not receive metrics automatically.**

**Team handoff:** use the reviewed foundation on `main` as the common starting point. Fetch the latest `origin/main` before creating each developer's branch/worktree; keep local environment files and runtime databases separate.

## Ownership

Assign actual names to A/B/C before editing. Start from the same reviewed foundation commit. One person owns each file; arrange a handoff before crossing a boundary.

| Owner | Exclusive files and area | Done when |
| --- | --- | --- |
| A: live agent and models | backend/providers.py, backend/model_schemas.py, backend/worker.py; provider/worker/model tests; scripts/evaluate_models.py and scripts/qualification_driver.py; docs/integrations/INFERENCE.md | Duck Tape's own agent performs a generic imported-repository repair with real inference, failing-before/passing-after checks and review. A seeded unsafe proposal is tested. Exact model, checks and failures are recorded. |
| B: retrieval and data platforms | backend/retrieval.py, backend/data_platforms.py, scripts/integrations.py, integrations/**; data-platform/retrieval tests and retrieval-evaluation scripts; docs/integrations/DATA_PLATFORMS.md | Duck Tape's worker uses its internal Atlas evidence memory with repository/snapshot/model isolation and local source validation. Terminal metrics, including failures, reach Databricks through explicit commands and an actual comparison is recorded. |
| C: frontend, integration and submission | frontend/**, backend/main.py, root configuration/dependency files and locks, README, CI, AGENTS.md, CONTRIBUTING.md and other docs/** | Evidence is clear in the demo, changes integrate, current checks and a recording exist, and public submission facts and limitations are accurate. |

A and B send C exact dependency, environment-variable, route and shared-contract changes; they do not edit C's files. Coordinate A/B contracts before changing them. A owns the inference guide, B owns the data-platform guide, and C owns shared/hackathon documentation. Only C edits frontend/src/main.tsx. Match style in touched code; avoid a formatter migration or broad refactor.

Use one separate branch/worktree or clone per person: feat/agent/live-repair, feat/data/atlas-retrieval and feat/ui/demo-evidence. Fixes use fix/<lane>/<task>. Commit subjects use Conventional Commits: type(scope): lowercase imperative, at most 72 characters, for example:

- feat(agent): add measured Adaption review
- feat(data): rank snapshot evidence from Atlas
- feat(ui): show exact regression evidence

One coherent change per commit. Every handoff states changed behavior, checks actually run and remaining limits. C integrates small reviewed commits/PRs. Do not force-push the shared branch, reset another person's work or edit a shared file concurrently.

## First hour

| Time from kickoff | Work | Concrete output |
| --- | --- | --- |
| 0-15 minutes | Read rules/current files; agree owners, common foundation commit and existing dirty changes. | Names, branches, demo scenario and provenance questions recorded. |
| 15-35 minutes | A checks model credentials/API capabilities. B checks Atlas/Databricks access and explicit CLI. C starts the app and inspects evidence screens. | Each owner reports actual availability, the next step and any blocker. |
| 35-60 minutes | Agree existing provider, retrieval and terminal-metric contracts. Run focused baseline checks in isolated test data. | One bounded live model request, a bounded Atlas capability check and a runnable local UI, or explicit blockers. |

Keep secrets out of Git, prompts and logs. A failed capability check is a reason to fix the endpoint or narrow an integration early; it is not permission to label fixture output as live.

## Through the deadline

All times are London time, Saturday 14 November and Sunday 15 November 2026. If a target has passed, take the remaining gates in order without dropping validation.

| Target | Gate |
| --- | --- |
| Saturday 16:00 | One real generic Python patch has executed tests; Atlas has an index and scoped query; the UI shows relevant check evidence. |
| Saturday 18:00 | First integrated live journey. Demonstrate an approved constraint on a subsequent request and exercise unsafe-review handling. |
| Saturday 20:00 | Compare fixed held-out tasks with identical inputs; explicitly export terminal outcomes and retain Databricks query results. Attempt adaptation only after the live base-model journey works. |
| Saturday 22:00 | Feature freeze. Resolve integration defects, record the demo and run current deterministic acceptance. |
| Sunday 09:00 | Rehearse from a fresh isolated demo workspace on the submission build. Retain a labelled recording in case services fail. |
| Sunday 10:00 | Finish public README, model/API list, provenance disclosure, limitations and submission facts. Confirm public access and requirements. |
| Sunday 11:00 | Freeze artifacts, check links/recording, submit early and retain confirmation. |
| Sunday 12:00 | Supplied submission deadline. |

## Internal implementation choices

- **Internal inference/adaptation:** use the implemented Adaption/Hugging Face adapters in Duck Tape's typed pipeline. Check actual API/model/schema support. Adaption prompt JSON is locally validated; Hugging Face structured-output support depends on the selected model/provider. Health and valid JSON do not prove coding quality. Keep adaptation secondary; separate training examples from held-out evaluation and report no gain if no gain is measured.
- **Internal evidence memory:** use Atlas semantic retrieval for Duck Tape with repository/snapshot/model scope. The foundation projects hashed namespaces, chunk IDs and vectors; Atlas receives no source text. A configured remote embedding provider still receives the text it embeds; disclose that separately. Resolve returned IDs against local source/hash evidence and obey context budgets. Keep a visible local fallback; never represent fallback as Atlas success.
- **Internal run evaluation/improvement:** explicitly export Duck Tape's bounded terminal metrics to Databricks. Compare success, independent check results, unsafe approvals, latency and known token usage; include failures and unknown usage. A completed stage is not independent correctness evidence. The foundation omits model names and retrieval labels; B must define explicit nonsecret run-to-variant mappings/cohorts before claiming across-version comparisons. Use measured results to guide changes to Duck Tape's own agent; do not imply an automatic improvement loop already exists.
- **Workflow:** preserve SQLite transactions, job leases, exact-revision approval and sandbox checks. Team notes remain outside prompts. Cloud uploads stay opt-in with accurate disclosure.

See [inference setup](../integrations/INFERENCE.md) and [data-platform setup](../integrations/DATA_PLATFORMS.md). Inspect the current CLI before running it:

~~~text
python scripts/integrations.py --help
python scripts/integrations.py status
python scripts/integrations.py atlas-index
python scripts/integrations.py atlas-sync --repo ID --snapshot ID
python scripts/integrations.py atlas-search --repo ID --snapshot ID --query TEXT --limit 12
python scripts/integrations.py databricks-sync --run ID
python scripts/integrations.py databricks-summary
~~~

Run these commands in Duck Tape's own checkout/server environment, never the imported/client repository. Replace ID/TEXT with the intended demo data. Upload/query commands require explicitly enabled services, actual credentials and provisioned resources; status is not live acceptance.

## Demo and evidence

In roughly three minutes: show the request and snapshot, relevant source/approved decision, failing regression on unchanged code, patch and passing checks, then exact-revision approval. Show the seeded unsafe-review result and the small measured comparison. Label previous recordings with their time/model; fixture scenarios stay labelled.

Retain commit, provider/model/version, scenario IDs, elapsed time, known usage, checks, retrieval mode and blockers. Keep failures in evaluation denominators. Owners run focused tests first; C runs the current integrated release gates. Use [START_HERE.md](START_HERE.md) for kickoff prompts and [SUBMISSION.md](SUBMISSION.md) for the final handoff.

Skip public multi-user hosting, a database migration, a new agent framework, a broad UI redesign and training before a working base patch. Remaining risks are service access, model quality, deadline pressure and unresolved eligibility of the existing baseline.
