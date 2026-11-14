# Steward Model Training Pipeline — State & Handover

- **Current Phase:** Part 3 of 5 — Wave 3 Specialist Data Prep & Pre-Flight Quality Gate (**Completed — Pending Review of `model-training/data/inspection_report.md`**)
- **Branch:** `feat/model-training-pipeline`
- **Model Training Started:** `false` (data prep & pre-flight quality audit only in Parts 1–3)

## Shared Infrastructure (`model-training/`)
- `model-training/.gitignore` — ignores `.env.training`, `.venv/`, `__pycache__/`, `data/raw/`, `data/processed/*.jsonl`, `data/eval/*.jsonl`, `models/checkpoints/`, `*.tgz`, `*.safetensors`, `*.bin`, while keeping `data/inspection_report.md` and `data/adaption_runs_manifest.json` tracked.
- `model-training/requirements-training.txt` — pinned training & data prep dependencies (`datasets`, `huggingface_hub`, `pydantic`, `openai`, `tqdm`, `python-dotenv`, `tiktoken`, `adaption`, `databricks-sdk`).
- `model-training/src/training/schemas.py` — Pydantic v2 validators for Adaption's formats (`InstructionRow`, `ChatTrajectoryRow` with normalized `tool_calls[].function.arguments` validation, `PreferenceRow`, `VisionInstructionRow` with base64/HTTPS URI validation, and `ContrastiveTripletRow`), strict completion validators (`RouterCompletion`, `TicketParserCompletion`, `LogClassifierCompletion`), `count_chat_tokens`, and auto-detecting `compute_jsonl_token_stats` (`cl100k_base`).
- `model-training/src/training/scrub.py` — regex scrubber redacting PEM private keys, OpenAI keys, Hugging Face tokens, GitHub tokens, AWS keys, Slack tokens, Google API keys, Databricks tokens, Bearer headers, and secret assignments.
- `model-training/src/training/utils.py` — streaming environment loader (`model-training/.env.training`), generic deterministic train/eval splitter (`seed=42`), unified diff path/symbol extractors, and UTF-8 LF JSONL writers (`write_instruction_jsonl`, `write_chat_jsonl`, `write_preference_jsonl`, `write_vision_jsonl`, `write_triplet_jsonl`).
- `model-training/scripts/inspect_datasets.py` — 5-gate pre-flight quality audit script across all 12 datasets generating `model-training/data/inspection_report.md`.

## All 12 Prepared Specialist Datasets (Verified via `model-training/scripts/inspect_datasets.py`)

| # | Agent | Format | Prep Script | Train File | Train Rows | Eval File | Eval Rows | Total Tokens (Train / Eval) | Max Tokens | Uniqueness | Overlap | Secrets | `augmentation_domain_rows` |
| ---: | --- | --- | --- | --- | ---: | --- | ---: | ---: | ---: | --- | ---: | ---: | --- |
| 1 | `router` | `instruction` | `model-training/scripts/prep_01_router.py` | `data/processed/router.jsonl` | 1,200 | `data/eval/router_eval.jsonl` | 200 | 588,413 / 100,696 | 1,928 | 99.92% / 100.00% | 0 | 0 | `8,800` (→ 10,000) |
| 2 | `ticket_parser` | `instruction` | `model-training/scripts/prep_02_ticket_parser.py` | `data/processed/ticket_parser.jsonl` | 5,000 | `data/eval/ticket_parser_eval.jsonl` | 200 | 2,663,292 / 100,050 | 1,468 | 100.00% / 100.00% | 0 | 0 | `5,000` (→ 10,000) |
| 3 | `commit_pr_writer` | `instruction` | `model-training/scripts/prep_03_commit_writer.py` | `data/processed/commit_pr_writer.jsonl` | 5,000 | `data/eval/commit_pr_writer_eval.jsonl` | 200 | 1,810,768 / 74,452 | 950 | 100.00% / 100.00% | 0 | 0 | `5,000` (→ 10,000) |
| 4 | `log_classifier` | `instruction` | `model-training/scripts/prep_04_log_classifier.py` | `data/processed/log_classifier.jsonl` | 5,000 | `data/eval/log_classifier_eval.jsonl` | 200 | 1,770,597 / 70,668 | 903 | 100.00% / 100.00% | 0 | 0 | `5,000` (→ 10,000) |
| 5 | `implementer` | `chat` | `model-training/scripts/prep_05_implementer.py` | `data/processed/implementer.jsonl` | 5,000 | `data/eval/implementer_eval.jsonl` | 200 | 114,266,369 / 4,709,899 | 44,986 | 100.00% / 100.00% | 0 | 0 | `5,000` (→ 10,000) |
| 6 | `test_writer` | `instruction` | `model-training/scripts/prep_06_test_writer.py` | `data/processed/test_writer.jsonl` | 5,000 | `data/eval/test_writer_eval.jsonl` | 200 | 6,206,816 / 260,793 | 2,431 | 100.00% / 100.00% | 0 | 0 | `5,000` (→ 10,000) |
| 7 | `reviewer` | `preference` | `model-training/scripts/prep_07_reviewer_dpo.py` | `data/processed/reviewer_dpo.jsonl` | 5,000 | `data/eval/reviewer_dpo_eval.jsonl` (alias `reviewer_eval.jsonl`) | 200 | 4,338,028 / 170,442 | 1,894 | 100.00% / 100.00% | 0 | 0 | `7,000` (→ 12,000) |
| 8 | `security_reviewer` | `preference` | `model-training/scripts/prep_08_security_reviewer_dpo.py` | `data/processed/security_reviewer_dpo.jsonl` | 5,000 | `data/eval/security_reviewer_eval.jsonl` | 200 | 4,656,693 / 185,611 | 1,938 | 100.00% / 100.00% | 0 | 0 | `7,000` (→ 12,000) |
| 9 | `db_migration` | `instruction` | `model-training/scripts/prep_09_db_migration.py` | `data/processed/db_migration.jsonl` | 5,000 | `data/eval/db_migration_eval.jsonl` | 200 | 2,931,167 / 117,747 | 2,005 | 100.00% / 100.00% | 0 | 0 | `5,000` (→ 10,000) |
| 10 | `iac` | `instruction` | `model-training/scripts/prep_10_iac.py` | `data/processed/iac.jsonl` | 5,000 | `data/eval/iac_eval.jsonl` | 200 | 2,876,788 / 114,712 | 1,459 | 100.00% / 100.00% | 0 | 0 | `5,000` (→ 10,000) |
| 11 | `frontend_ui` | `vision_instruction` | `model-training/scripts/prep_11_frontend_ui.py` | `data/processed/frontend_ui.jsonl` | 2,500 | `data/eval/frontend_ui_eval.jsonl` | 100 | 2,226,608 / 89,168 | 8,911 | 100.00% / 100.00% | 0 | 0 | `7,500` (→ 10,000) |
| 12 | `retriever` | `contrastive_triplet` | `model-training/scripts/prep_12_retriever_contrastive.py` | `data/processed/retriever_triplets.jsonl` | 5,000 | `data/eval/retriever_triplets_eval.jsonl` (alias `retriever_eval.jsonl`) | 300 | 4,205,738 / 244,932 | 2,436 | 100.00% / 100.00% | 0 | 0 | `5,000` (→ 10,000) |

### Source Provenance & Scrubbing Summary
- **Waves 1 & 2 (`router` through `security_reviewer`)**: Unchanged from Part 2 (`53,700` total train + `2,400` total eval rows across all 12 datasets).
- **`db_migration`**: Streamed from `birdsql/bird_mini_dev` (500 unique schema-linked PostgreSQL/MySQL/SQLite questions + DDL schemas), `bigcode/commitpackft` (`sql`: 1,750, `python` Alembic/Django migrations: 300, `typescript` Prisma/ORM migrations: 25), and `xlangai/spider` (2,625); 2 secret redactions applied. Every completion verified to include `### 1. Forward Migration`, `### 2. Rollback / Down Migration`, and `### 3. Lock & Data-Loss Risk Assessment`.
- **`iac`**: Streamed from `autoiac-project/iac-eval` (455), `bigcode/commitpackft` (`hcl`: 382, `dockerfile`: 31, `yaml` K8s/Helm: 400), `HelloBoieeee/kubernetes_config` (1,800), `LeeSek/dockerfiles-linted` (1,200), and `loubnabnl/dockerfile_checks` (932); 50 secret redactions applied. Every completion verified to include `### Least-Privilege IAM & Network Security Notes` and `### Infrastructure Configuration`.
- **`frontend_ui`**: Collected from `SALT-NLP/Design2Code` (484 exact PNG+HTML pairs) and `HuggingFaceM4/WebSight` (2,116 streamed rows); 0 secret hits. Images resized and encoded as compact JPEG `data:image/jpeg;base64,...` data URIs (`15.6 MB` train JSONL), paired with modular React + TypeScript + Tailwind CSS components.
- **`retriever`**: Built 5,300 contrastive triplets from `princeton-nlp/SWE-bench_Verified` (499) + `SWE-Gym/SWE-Gym` (1,201) `(issue_text, files_changed)` pairs with same-repository non-modified hard negatives, `code-search-net/code_search_net` (2,100), and `gonglinyuan/CoSQA` (1,500); 6 secret redactions applied.
- **Pre-Flight Quality Gate (`model-training/scripts/inspect_datasets.py`)**: All 12 datasets passed Schema/JSONL validity, row count checks, strict JSON / zero-fence checks (`router`, `ticket_parser`, `log_classifier`), >95% prompt uniqueness, DPO non-trivial rejected length (>50 chars: min 347 on `reviewer`, min 205 on `security_reviewer`), <45,000 max tokens, 0 train/eval overlap, and 0 secret hits. Full audit written to `model-training/data/inspection_report.md`.

## Next Step (Part 4 of 5)
- Review `model-training/data/inspection_report.md`, then proceed to Part 4 (Adaption AutoScientist fine-tuning & evaluation orchestration).
