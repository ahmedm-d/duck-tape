# Duck Tape

**Problem.** Keeping a codebase healthy means a lot of slow, repetitive work: triaging tickets, chasing dependency advisories, fixing small bugs and writing tests. A developer has to rebuild the context around the whole repository before every small change. Most AI tools see one file at a time and forget the decisions the team has already made.

**What we built.** Duck Tape is an autonomous code-maintenance agent. It indexes a repository into a code graph and stores the rules the team approves. It watches public vulnerability feeds for issues that affect the repository. It turns tickets, requests and advisories into maintenance jobs. For each job it prepares an isolated, checked Git patch with evidence that a developer can review, approve or send back. Nothing merges or pushes automatically.

## How it works

1. **Import:** you add a Git repo or ZIP. Duck Tape takes a fixed snapshot of it and builds a graph of symbols, dependencies and tests. That graph, the code embeddings and the team-approved rules are copied to MongoDB Atlas.
2. **Signals:** a Databricks job pulls public OSV advisories and cleans them into a Delta table with revision history. They're published to a public MongoDB catalogue. A local matcher checks them against the repository's dependencies and opens security jobs.
3. **Agent run:** a persistent worker gathers context using Atlas Vector Search and a graph query that finds which code a change affects. It plans the change, asks the developer about product decisions, applies structured edits in isolation and runs the checks.
4. **Review:** the developer sees the versioned diff, the check results, the agent's stated uncertainty and the evidence it used. They approve, export or request a revision. Agent runs are logged to Databricks for evaluation.
5. **Models:** Adaption AutoScientist fine-tuned specialist agents from base models, scored by win rate against the base model. Live inference runs on Adaption-hosted models, with Claude and a local Ollama model as benchmarks.

## APIs and services

- **Adaption:** the Responses API for live agent inference, and AutoScientist for fine-tuning.
- **MongoDB Atlas:** private repository knowledge (Vector Search, code graph, `$graphLookup`, approved knowledge) and the public catalogue.
- **Databricks:** jobs, Delta tables and a SQL warehouse, for curating the OSV catalogue and evaluating agent runs.
- **Anthropic Claude API** (`claude-opus-5-5`): a benchmark provider.
- **Ollama:** local inference.
- **Hugging Face:** Inference Providers (an OpenAI-compatible router) and the training datasets.
- **OSV.dev API:** public vulnerability advisories.

## Models

- **Adaption-hosted, live:** `qwen3.5-397b`.
- **Fine-tune base models:**
  - `gemma-4-31B-it`: router, ticket parser, log classifier, commit/PR writer, frontend UI.
  - `Qwen3.6-35B-A3B`: implementer, test writer, security reviewer, database migration, infrastructure-as-code.
  - `Qwen3.5-9B`: security triage.
- **Benchmarks:** Claude Opus 5.5, and `qwen3-coder:30b` (Q4_K_M) running locally in Ollama.

## Datasets (Hugging Face)

- **Agent trajectories:** SWE-bench/SWE-smith, SWE-bench/SWE-smith-trajectories, princeton-nlp/SWE-bench_Verified, SWE-Gym/SWE-Gym, nebius/SWE-rebench-openhands-trajectories, R2E-Gym/R2EGym-SFT-Trajectories.
- **Code review and commits:** fasterinnerlooper/codereviewer, Dahoas/code-review-instruct-critique-revision-python, Maxscha/commitbench.
- **Tests:** kjain14/testgeneval.
- **Security:** MickyMike/cvefixes_bigvul, colin/PrimeVul, bstee615/diversevul.
- **Tickets:** kristmh/clean_Jira_balanced, cjsanjay/jira_train_17012024.
- **SQL and code search:** xlangai/spider, gonglinyuan/CoSQA, code-search-net/code_search_net.
- **Infrastructure:** autoiac-project/iac-eval, loubnabnl/dockerfile_checks, LeeSek/dockerfiles-linted, HelloBoieeee/kubernetes_config.
- **Frontend:** HuggingFaceM4/WebSight, SALT-NLP/Design2Code.

## Public data
