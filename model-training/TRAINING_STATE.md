# Duck Tape (formerly Steward) Model Training Pipeline — State & Handover

- **Current Phase:** Part 4 of 5 — Multi-Account Adaption AutoScientist Orchestration (**Completed**)
- **Branch:** `feat/agent/model-training`
- **Model Training Started:** `true` (multi-account Adaption AutoScientist orchestration active in Part 4)

## Shared Infrastructure (`model-training/`)
- `model-training/.gitignore` — ignores `.env.training`, `.venv/`, `__pycache__/`, `data/raw/`, `data/processed/*.jsonl`, `data/eval/*.jsonl`, `models/checkpoints/`, `*.tgz`, `*.safetensors`, `*.bin`, while keeping `data/inspection_report.md`, `data/dataset_licenses.json`, and `data/adaption_runs_manifest.json` tracked.
- `model-training/requirements-training.txt` — pinned training & data prep dependencies (`datasets`, `huggingface_hub`, `pydantic`, `openai`, `tqdm`, `python-dotenv`, `tiktoken`, `adaption`, `databricks-sdk`).
- `model-training/src/training/schemas.py` — Pydantic v2 validators for Adaption's formats (`InstructionRow`, `ChatTrajectoryRow` with normalized `tool_calls[].function.arguments` validation, `PreferenceRow`, `VisionInstructionRow` with base64/HTTPS URI validation, and `ContrastiveTripletRow`), strict completion validators (`RouterCompletion`, `TicketParserCompletion`, `DecisionCompletion`, `validate_ticket_parser_output`, `LogClassifierCompletion`), `count_chat_tokens`, and auto-detecting `compute_jsonl_token_stats` (`cl100k_base`).
- `model-training/src/training/scrub.py` — regex scrubber redacting PEM private keys, OpenAI keys, Hugging Face tokens, GitHub tokens, AWS keys, Slack tokens, Google API keys, Databricks tokens, Bearer headers, and secret assignments.
- `model-training/src/training/utils.py` — streaming environment loader (`model-training/.env.training`), generic deterministic train/eval splitter (`seed=42`), unified diff path/symbol extractors, and UTF-8 LF JSONL writers (`write_instruction_jsonl`, `write_chat_jsonl`, `write_preference_jsonl`, `write_vision_jsonl`, `write_triplet_jsonl`).
- `model-training/scripts/migrate_to_handoff_v1.py` — fast in-place dataset migrator upgrading existing `.jsonl` datasets to `HANDOFF_B_TO_A.md` (`<graph_context>` v1), `backend/model_schemas.py` (`DECISION` schema), 70%/15%/15% `sink_reachable` null-safety rules, CI/worktree rules, and generating `model-training/data/dataset_licenses.json`.
- `model-training/scripts/inspect_datasets.py` — 6-gate pre-flight quality & Handoff v1 audit script across all 12 datasets + eval aliases generating `model-training/data/inspection_report.md`.
- `model-training/data/dataset_licenses.json` — open dataset provenance & license manifest across all 12 datasets (`duck-tape-licenses-1`).
- `model-training/patches/worker_handoff_patch.md` — exact Section 5 patch snippets for `backend/worker.py` (`retrieve(repo['id'], ...)`, `job['applicability']`, `<graph_context>...</graph_context>` wrapping) and regression unit test proving team notes never reach the model.

## All 12 Prepared Specialist Datasets (Verified via `model-training/scripts/inspect_datasets.py`)

| # | Agent | Format | Prep Script | Train File | Train Rows | Eval File | Eval Rows | Total Tokens (Train / Eval) | Max Tokens | Uniqueness | Overlap | Secrets | Forbidden Hits | `augmentation_domain_rows` |
| ---: | --- | --- | --- | --- | ---: | --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- |
| 1 | `router` | `instruction` | `model-training/scripts/prep_01_router.py` | `data/processed/router.jsonl` | 1,200 | `data/eval/router_eval.jsonl` | 200 | 1,243,621 / 211,242 | 2,446 | 100.00% / 100.00% | 0 | 0 | 0 | `8,800` (→ 10,000) |
| 2 | `ticket_parser` | `instruction` | `model-training/scripts/prep_02_ticket_parser.py` | `data/processed/ticket_parser.jsonl` | 5,000 | `data/eval/ticket_parser_eval.jsonl` | 200 | 5,250,859 / 200,477 | 2,203 | 100.00% / 100.00% | 0 | 0 | 0 | `5,000` (→ 10,000) |
| 3 | `commit_pr_writer` | `instruction` | `model-training/scripts/prep_03_commit_writer.py` | `data/processed/commit_pr_writer.jsonl` | 5,000 | `data/eval/commit_pr_writer_eval.jsonl` | 200 | 5,051,877 / 204,600 | 1,828 | 100.00% / 100.00% | 0 | 0 | 0 | `5,000` (→ 10,000) |
| 4 | `log_classifier` | `instruction` | `model-training/scripts/prep_04_log_classifier.py` | `data/processed/log_classifier.jsonl` | 5,000 | `data/eval/log_classifier_eval.jsonl` | 200 | 4,868,187 / 194,818 | 1,550 | 100.00% / 100.00% | 0 | 0 | 0 | `5,000` (→ 10,000) |
| 5 | `implementer` | `chat` | `model-training/scripts/prep_05_implementer.py` | `data/processed/implementer.jsonl` | 5,000 | `data/eval/implementer_eval.jsonl` | 200 | 117,201,048 / 4,825,917 | 43,999 | 100.00% / 100.00% | 0 | 0 | 0 | `5,000` (→ 10,000) |
| 6 | `test_writer` | `instruction` | `model-training/scripts/prep_06_test_writer.py` | `data/processed/test_writer.jsonl` | 5,000 | `data/eval/test_writer_eval.jsonl` | 200 | 9,938,823 / 410,764 | 3,280 | 100.00% / 100.00% | 0 | 0 | 0 | `5,000` (→ 10,000) |
| 7 | `reviewer` | `preference` | `model-training/scripts/prep_07_reviewer_dpo.py` | `data/processed/reviewer_dpo.jsonl` | 5,000 | `data/eval/reviewer_dpo_eval.jsonl` (alias `reviewer_eval.jsonl`) | 200 | 7,551,018 / 299,426 | 2,551 | 100.00% / 100.00% | 0 | 0 | 0 | `7,000` (→ 12,000) |
| 8 | `security_reviewer` | `preference` | `model-training/scripts/prep_08_security_reviewer_dpo.py` | `data/processed/security_reviewer_dpo.jsonl` | 5,000 | `data/eval/security_reviewer_eval.jsonl` | 200 | 8,489,178 / 338,618 | 2,773 | 100.00% / 100.00% | 0 | 0 | 0 | `7,000` (→ 12,000) |
| 9 | `db_migration` | `instruction` | `model-training/scripts/prep_09_db_migration.py` | `data/processed/db_migration.jsonl` | 5,000 | `data/eval/db_migration_eval.jsonl` | 200 | 5,703,644 / 228,823 | 2,554 | 100.00% / 100.00% | 0 | 0 | 0 | `5,000` (→ 10,000) |
| 10 | `iac` | `instruction` | `model-training/scripts/prep_10_iac.py` | `data/processed/iac.jsonl` | 5,000 | `data/eval/iac_eval.jsonl` | 200 | 5,424,561 / 216,939 | 1,989 | 100.00% / 100.00% | 0 | 0 | 0 | `5,000` (→ 10,000) |
| 11 | `frontend_ui` | `vision_instruction` | `model-training/scripts/prep_11_frontend_ui.py` | `data/processed/frontend_ui.jsonl` | 2,500 | `data/eval/frontend_ui_eval.jsonl` | 100 | 2,226,608 / 89,168 | 8,911 | 100.00% / 100.00% | 0 | 0 | 0 | `7,500` (→ 10,000) |
| 12 | `retriever` | `contrastive_triplet` | `model-training/scripts/prep_12_retriever_contrastive.py` | `data/processed/retriever_triplets.jsonl` | 5,000 | `data/eval/retriever_triplets_eval.jsonl` (alias `retriever_eval.jsonl`) | 300 | 4,205,736 / 244,932 | 2,436 | 100.00% / 100.00% | 0 | 0 | 0 | `5,000` (→ 10,000) |

### Handoff v1 Migration & Verification Summary
- **`graph_context v1` (`HANDOFF_B_TO_A.md`)**: Migrated all 10 graph-aware agents (`51,200` train + `2,400` core/alias eval rows) in place via `model-training/scripts/migrate_to_handoff_v1.py` with normalized `scope` (without `tenant_id`), `focal_symbols` (`kind`, `signature`, `community`, `degree`, `high_degree`, without `is_god_node`/`leiden_community`), `edges` (`INFERRED` + `suggestion_only: true` instead of `AMBIGUOUS`), `blast_radius`, `catalogue_matches`, `approved_knowledge`, and `gaps`.
- **Security Null-Safety (`security_reviewer`)**: Enforced 70% / 15% / 15% `sink_reachable` split (`3,500` `null` / `750` `true` / `750` `false` train; `140` / `30` / `30` eval), treating `null` as unknown using the exact phrase `"not shown reachable"` with zero `"non-exploitable"` phrasing.
- **CI & Git Worktree Alignment**: Grounded `implementer`, `test_writer`, `commit_pr_writer`, and `log_classifier` in `blast_radius.linked_test_files`, isolated Git worktrees, failure reproduction before patching, post-patch CI verification, and stale-approval invalidation.
- **Strict `backend/model_schemas.py` Compliance**: Migrated `275` train + `9` eval `ticket_parser` decision-card rows to the `DECISION` alternative schema (`validate_ticket_parser_output` + `backend.model_schemas.check(..., DECISION)`), removed `needs_decision_card`, and stripped `new_global_vuln_candidate`.
- **Pre-Flight Quality & Handoff Gate (`model-training/scripts/inspect_datasets.py`)**: All 12 datasets + eval aliases passed Schema/JSONL validity, row count checks, strict JSON / zero-fence checks, `100.00%` prompt uniqueness, `<45,000` max tokens (`max 43,999` on `implementer`), `0` train/eval overlap, `0` secret hits, and `0` forbidden-term hits (`<steward_graph_context>`, `is_god_node`, `AMBIGUOUS`, `tenant_id`, `needs_decision_card`, `new_global_vuln_candidate`, `non-exploitable`). Full audit written to `model-training/data/inspection_report.md`.

<!-- ADAPTION_RUNS_START -->
## Multi-Account Adaption AutoScientist Runs (Part 4 of 5)

- **Last Polled (UTC):** `2026-11-15T08:27:53+00:00`
- **Run Summary:** `12` total | `0` active | `12` succeeded | `0` failed/cancelled
- **Orchestrator Scripts:** `model-training/scripts/launch_adaption_parallel.py`, `model-training/scripts/poll_adaption_runs.py`
- **Run Manifest:** `model-training/data/adaption_runs_manifest.json`

| Account | Agent | Model | Method | Eff. Rows | Run ID | Status | Iteration | Best Win Rate |
| --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| Acct 1 | `router` | `google/gemma-4-31B-it` | `instruction` | 10,000 | `a7c2975d-0eeb-4ad0-ae89-c7c634e540e7` | `succeeded` | `3/3` | `0.620` |
| Acct 1 | `ticket_parser` | `google/gemma-4-31B-it` | `instruction` | 10,000 | `aa06d9dd-7c82-4919-b46d-3c279a46c212` | `succeeded` | `3/3` | `0.574` |
| Acct 2 | `implementer` | `Qwen/Qwen3.6-35B-A3B` | `instruction` | 10,000 | `91d8228e-7244-400e-911f-deb4aa5e89a1` | `succeeded` | `3/3` | `0.631` |
| Acct 2 | `test_writer` | `Qwen/Qwen3.6-35B-A3B` | `instruction` | 10,000 | `eca61d3c-52df-4d6a-8565-e3aa0672c4a1` | `succeeded` | `3/3` | `0.449` |
| Acct 3 | `reviewer_dpo` | `Qwen/Qwen3.6-35B-A3B` | `alignment` | 12,000 | `f0619f20-3fdb-4c14-a895-ff0fb5045e5e` | `succeeded` | `2/2` | `0.474` |
| Acct 3 | `security_reviewer_dpo` | `Qwen/Qwen3.6-35B-A3B` | `alignment` | 12,000 | `35cf6a08-2405-4d0a-8fa1-5261e71d1cfe` | `succeeded` | `2/2` | `0.476` |
| Acct 4 | `commit_pr_writer` | `google/gemma-4-31B-it` | `instruction` | 10,000 | `50961b49-fd45-49f8-964a-06ea37361e2f` | `succeeded` | `3/3` | `0.693` |
| Acct 4 | `db_migration` | `Qwen/Qwen3.6-35B-A3B` | `instruction` | 10,000 | `2cb1904b-5e23-4c56-87e0-7aafeedfac07` | `succeeded` | `3/3` | `0.444` |
| Acct 4 | `frontend_ui` | `google/gemma-4-31B-it-VLM` | `instruction` | 2,500 | `2f6c1852-ce21-49c9-abb2-43a1e44e3854` | `succeeded` | `3/3` | `0.496` |
| Acct 4 | `iac` | `Qwen/Qwen3.6-35B-A3B` | `instruction` | 10,000 | `8d833e2a-45b8-4807-821c-a45d2142df6d` | `succeeded` | `3/3` | `0.485` |
| Acct 4 | `log_classifier` | `google/gemma-4-31B-it` | `instruction` | 10,000 | `74d2812d-b1ee-465d-b628-aca9d3a09093` | `succeeded` | `3/3` | `0.664` |
| Acct 5 | `hero_security_triage` | `Qwen/Qwen3.5-9B` | `instruction` | 1,100 | `824116fb-bf18-4ad1-911f-42cdc540c375` | `succeeded` | `2/3` | `0.711` |
- **Account 1 Web UI Router Discovery:** `Multi-Agent_Job_Router` (`612ad034-bdb6-43ff-92a7-e5b3994b1e32`, status=`succeeded`)
- **Trajectory Extension (`implementer`):** status=`succeeded`, run_id=`dataset-173bcadd-b10d-4385-a246-dfd6e2ae6ba5-1794686167184`, dataset_id=`173bcadd-b10d-4385-a246-dfd6e2ae6ba5`
- **Custom Eval (`security_reviewer_dpo`):** status=`running`, custom_eval_id=`0f51efe2-6512-4d3b-81b1-a834fd39daee`
- **Custom Eval (`hero_security_triage`):** status=`running`, custom_eval_id=`6bbb2332-3529-454f-916b-536058f0c539`

## Next Step (Part 5 of 5)
- Poll `python model-training/scripts/poll_adaption_runs.py` until runs reach terminal states (`succeeded`/`completed`), then download best checkpoints (`client.autoscientist.download()`), benchmark base vs. adapted held-out eval sets, and wire the Databricks/vLLM endpoints + `backend/worker.py` patch.
<!-- ADAPTION_RUNS_END -->
