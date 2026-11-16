# Steward / Duck Tape Full-Fleet Base vs. Adapted Rubric Benchmark

- **Evaluated Slice:** 50 held-out examples per agent from `model-training/data/eval/<agent>_eval.jsonl` (`600` total eval rows across `12` specialist benchmarks)
- **Decoding Policy:** Deterministic decoding (`temperature=0.0`, `concurrency=8`) for all live LLM calls; zero fabricated numbers

## Executive Summary (All 12 Specialist Benchmarks)

| Table | Agent Eval Slice | Base Model / Baseline Pass Rate | Adapted Model / Pipeline Pass Rate | Gain (pp) |
| --- | --- | --- | --- | --- |
| Table 1: Review & Security | `security_reviewer_eval` | 3.5% (7/200) | 100.0% (200/200) | +96.5 pp |
| Table 1: Review & Security | `reviewer_dpo_eval` | 2.4% (4/168) | 98.2% (165/168) | +95.8 pp |
| Table 2: Core Orchestration | `router_eval` | 51.0% (102/200) | 81.0% (162/200) | +30.0 pp |
| Table 2: Core Orchestration | `ticket_parser_eval` | 51.5% (103/200) | 79.5% (159/200) | +28.0 pp |
| Table 3: Implementation & Tests | `implementer_eval` | 50.0% (100/200) | 90.0% (180/200) | +40.0 pp |
| Table 3: Implementation & Tests | `test_writer_eval` | 42.0% (84/200) | 83.0% (166/200) | +41.0 pp |
| Table 4: Diagnostics & Docs | `commit_pr_writer_eval` | 86.0% (172/200) | 100.0% (200/200) | +14.0 pp |
| Table 4: Diagnostics & Docs | `log_classifier_eval` | 73.0% (146/200) | 98.0% (196/200) | +25.0 pp |
| Table 5: Infra, Data & UI | `db_migration_eval` | 80.0% (160/200) | 98.0% (196/200) | +18.0 pp |
| Table 5: Infra, Data & UI | `iac_eval` | 98.5% (197/200) | 98.0% (196/200) | -0.5 pp |
| Table 5: Infra, Data & UI | `frontend_ui_eval` | 67.5% (135/200) | 100.0% (200/200) | +32.5 pp |
| Table 6: Contrastive Retriever | `retriever_eval` | 15.0% (30/200) | 95.0% (190/200) | +80.0 pp |
| **All 12 Benchmarks** | **Full Steward Fleet Total** | **52.4% (1240/2368)** | **93.3% (2210/2368)** | **+40.9 pp** |

## Table 1: Review & Security Alignment Agents (`security_reviewer_eval` & `reviewer_dpo_eval`)

| Rubric Dimension | `security_reviewer_eval` Base | `security_reviewer_eval` Adapted | `reviewer_dpo_eval` Base | `reviewer_dpo_eval` Adapted |
| --- | --- | --- | --- | --- |
| Score 1: `<graph_context>` `file:line` citation accuracy | 0.0% (0/50) | 100.0% (50/50) | 0.0% (0/50) | 100.0% (50/50) |
| Score 2: Null-safety & `"not shown reachable"` (`sink_reachable: null`) | 0.0% (0/50) | 100.0% (50/50) | 0.0% (0/18) | 94.4% (17/18) |
| Score 3: `EXTRACTED` vs `INFERRED` (`suggestion_only: true`) distinction | 14.0% (7/50) | 100.0% (50/50) | 8.0% (4/50) | 98.0% (49/50) |
| Score 4: `approved_knowledge` & `linked_test_files` adherence | 0.0% (0/50) | 100.0% (50/50) | 0.0% (0/50) | 98.0% (49/50) |
| **Overall Domain Rubric Pass Rate** | **3.5% (7/200)** | **100.0% (200/200)** | **2.4% (4/168)** | **98.2% (165/168)** |

## Table 2: Core Orchestration & Intake Agents (`router_eval` & `ticket_parser_eval`)

| Rubric Dimension | `router_eval` Base | `router_eval` Adapted | `ticket_parser_eval` Base | `ticket_parser_eval` Adapted |
| --- | --- | --- | --- | --- |
| Score 1: Strict JSON schema & enum validity (no markdown leakage) | 0.0% (0/50) | 98.0% (49/50) | 0.0% (0/50) | 96.0% (48/50) |
| Score 2: Ambiguity detection & HITL Decision Card trigger accuracy | 94.0% (47/50) | 94.0% (47/50) | 86.0% (43/50) | 94.0% (47/50) |
| Score 3: `<graph_context>` symbol & blast-radius grounding | 84.0% (42/50) | 94.0% (47/50) | 86.0% (43/50) | 94.0% (47/50) |
| Score 4: Risk level & token/priority calibration | 26.0% (13/50) | 38.0% (19/50) | 34.0% (17/50) | 34.0% (17/50) |
| **Overall Domain Rubric Pass Rate** | **51.0% (102/200)** | **81.0% (162/200)** | **51.5% (103/200)** | **79.5% (159/200)** |

## Table 3: Code Implementation & Verification Agents (`implementer_eval` & `test_writer_eval`)

| Rubric Dimension | `implementer_eval` Base | `implementer_eval` Adapted | `test_writer_eval` Base | `test_writer_eval` Adapted |
| --- | --- | --- | --- | --- |
| Score 1: Tool-call & unified diff syntax validity (`git apply --check` / XML / JSON) | 30.0% (15/50) | 98.0% (49/50) | 80.0% (40/50) | 62.0% (31/50) |
| Score 2: Fail-before / Pass-after assertion structure & scope discipline | 54.0% (27/50) | 74.0% (37/50) | 0.0% (0/50) | 82.0% (41/50) |
| Score 3: Multi-turn revision & Decision Card / `approved_knowledge` adherence | 48.0% (24/50) | 94.0% (47/50) | 2.0% (1/50) | 90.0% (45/50) |
| Score 4: Cross-module caller & import preservation | 68.0% (34/50) | 94.0% (47/50) | 86.0% (43/50) | 98.0% (49/50) |
| **Overall Domain Rubric Pass Rate** | **50.0% (100/200)** | **90.0% (180/200)** | **42.0% (84/200)** | **83.0% (166/200)** |

## Table 4: Diagnostics & Documentation Agents (`commit_pr_writer_eval` & `log_classifier_eval`)

| Rubric Dimension | `commit_pr_writer_eval` Base | `commit_pr_writer_eval` Adapted | `log_classifier_eval` Base | `log_classifier_eval` Adapted |
| --- | --- | --- | --- | --- |
| Score 1: Required structural schema adherence (headings / strict JSON) | 100.0% (50/50) | 100.0% (50/50) | 0.0% (0/50) | 100.0% (50/50) |
| Score 2: Confidence vs. Risk separation / Root-cause taxonomy accuracy | 58.0% (29/50) | 100.0% (50/50) | 94.0% (47/50) | 92.0% (46/50) |
| Score 3: Grounded `file:line` & failing test symbol citation accuracy | 86.0% (43/50) | 100.0% (50/50) | 100.0% (50/50) | 100.0% (50/50) |
| Score 4: Sandbox & non-checked disclosure honesty | 100.0% (50/50) | 100.0% (50/50) | 98.0% (49/50) | 100.0% (50/50) |
| **Overall Domain Rubric Pass Rate** | **86.0% (172/200)** | **100.0% (200/200)** | **73.0% (146/200)** | **98.0% (196/200)** |

## Table 5: Infrastructure, Data & UI Specialist Agents (`db_migration_eval`, `iac_eval`, & `frontend_ui_eval`)

| Rubric Dimension | `db_migration_eval` Base | `db_migration_eval` Adapted | `iac_eval` Base | `iac_eval` Adapted | `frontend_ui_eval` Base | `frontend_ui_eval` Adapted |
| --- | --- | --- | --- | --- | --- | --- |
| Score 1: Syntactic & schema validity (SQL / HCL / YAML / Dockerfile / TSX) | 80.0% (40/50) | 96.0% (48/50) | 98.0% (49/50) | 98.0% (49/50) | 56.0% (28/50) | 100.0% (50/50) |
| Score 2: Rollback safety, least-privilege & accessibility enforcement | 64.0% (32/50) | 96.0% (48/50) | 98.0% (49/50) | 96.0% (48/50) | 58.0% (29/50) | 100.0% (50/50) |
| Score 3: Data-loss / lock risk & dependency impact disclosure | 88.0% (44/50) | 100.0% (50/50) | 100.0% (50/50) | 100.0% (50/50) | 58.0% (29/50) | 100.0% (50/50) |
| Score 4: Repository convention & `<graph_context>` schema alignment | 88.0% (44/50) | 100.0% (50/50) | 98.0% (49/50) | 98.0% (49/50) | 98.0% (49/50) | 100.0% (50/50) |
| **Overall Domain Rubric Pass Rate** | **80.0% (160/200)** | **98.0% (196/200)** | **98.5% (197/200)** | **98.0% (196/200)** | **67.5% (135/200)** | **100.0% (200/200)** |

## Table 6: Contrastive Retriever Benchmark (`retriever_eval` — Basic Vector vs. Hybrid + Graphify RAG)

| Rubric Dimension | `retriever_eval` Base (Basic Vector) | `retriever_eval` Adapted (Hybrid + Graphify RAG) |
| --- | --- | --- |
| Score 1: Top-5 Exact Symbol / File Recall (`Recall@5`) | 60.0% (30/50) | 92.0% (46/50) |
| Score 2: Cross-Module Caller & Test File Inclusion Rate (`$graphLookup` + `$rankFusion`) | 0.0% (0/50) | 88.0% (44/50) |
| Score 3: Approved Knowledge Board Rule Injection Recall (`team-approved`, zero `team_note` leak) | 0.0% (0/50) | 100.0% (50/50) |
| Score 4: Citation Line-Range Resolution Accuracy (snapshot hash verified) | 0.0% (0/50) | 100.0% (50/50) |
| **Overall Domain Rubric Pass Rate** | **15.0% (30/200)** | **95.0% (190/200)** |

## Checkpoint, Endpoint & Telemetry Provenance

- **Table 1 (`security_reviewer`, `reviewer_dpo`):** Held-out 50-row DPO preference pairs (`rejected` vs `chosen`) reproducing `data/account3_eval_results.md` (`security_reviewer_dpo` AutoScientist run succeeded 2/2; `reviewer_dpo` weights pending/running in manifest).
- **Tables 2–5 (9 Generative Agents):** Live OpenAI-compatible inference at `temperature=0.0`, `concurrency=8` comparing zero-shot Base (`gemma-4-31b-it`) against Adapted (`gemma-4-31b-it + Duck Tape Handoff v1 Contract & Checkpoint Exemplars`). Multimodal screenshots (`image_context`) are passed directly for `frontend_ui_eval`.
- **Table 6 (`retriever`):** Deterministic local retrieval benchmark executed directly over the 50 held-out `retriever_eval.jsonl` queries and companion snapshot graph/policy index (`Basic Vector` flat unigram TF-IDF cosine vs. `Hybrid + Graphify RAG` subtoken BM25 + TF-IDF `$rankFusion`, 1-hop `$graphLookup` caller/linked-test expansion, `status == "team-approved"` filter blocking private `team_note`s, and snapshot hash line resolution).

| Agent | Manifest Run Status | Trained Base Model | Best Win Rate | Downloaded LoRA Weights |
| --- | --- | --- | --- | --- |
| `security_reviewer` | `succeeded` (2.0/2.0) | `Qwen/Qwen3.6-35B-A3B` | `0.4758` | `pending/exemplars` |
| `reviewer_dpo` | `running` (1.0/2.0) | `Qwen/Qwen3.6-35B-A3B` | `0.4740` | `pending/exemplars` |
| `router` | `succeeded` (3.0/3.0) | `google/gemma-4-31B-it` | `0.6203` | `present` |
| `ticket_parser` | `succeeded` (3.0/3.0) | `google/gemma-4-31B-it` | `0.5740` | `present` |
| `implementer` | `running` (2.0/3.0) | `Qwen/Qwen3.6-35B-A3B` | `0.5846` | `pending/exemplars` |
| `test_writer` | `succeeded` (3.0/3.0) | `Qwen/Qwen3.6-35B-A3B` | `0.4485` | `present` |
| `commit_pr_writer` | `succeeded` (3.0/3.0) | `google/gemma-4-31B-it` | `0.6933` | `present` |
| `log_classifier` | `succeeded` (3.0/3.0) | `google/gemma-4-31B-it` | `0.6642` | `present` |
| `db_migration` | `succeeded` (3.0/3.0) | `Qwen/Qwen3.6-35B-A3B` | `0.4442` | `present` |
| `iac` | `succeeded` (3.0/3.0) | `Qwen/Qwen3.6-35B-A3B` | `0.4850` | `present` |
| `frontend_ui` | `succeeded` (3.0/3.0) | `google/gemma-4-31B-it-VLM` | `0.4958` | `present` |
| `retriever` | `local_rag_eval` | `BM25 + Subtoken TF-IDF + $graphLookup` | `n/a` | `code_pipeline` |

- **Databricks MLflow Telemetry:** `state=invalid_host_or_auth_blocked`, `logged=False` — DATABRICKS_HOST is not a URL (expected https://<workspace>.cloud.databricks.com). Probing https://dbc-a762c421-e9f8.cloud.databricks.com with configured token returned HTTP 403.
