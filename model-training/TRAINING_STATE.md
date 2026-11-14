# Steward Model Training Pipeline — State & Handover

- **Current Phase:** Part 1 of 5 — Git Setup & Wave 1 Data Prep (**Completed**)
- **Branch:** `feat/model-training-pipeline`
- **Model Training Started:** `false` (data prep & validation only in Part 1)

## Shared Infrastructure (`model-training/`)
- `model-training/.gitignore` — ignores `.env.training`, `.venv/`, `__pycache__/`, `data/raw/`, `data/processed/*.jsonl`, `data/eval/*.jsonl`, `models/checkpoints/`, `*.tgz`, `*.safetensors`, `*.bin`, while keeping `data/inspection_report.md` and `data/adaption_runs_manifest.json` tracked.
- `model-training/requirements-training.txt` — pinned training & data prep dependencies (`datasets`, `huggingface_hub`, `pydantic`, `openai`, `tqdm`, `python-dotenv`, `tiktoken`, `adaption`, `databricks-sdk`).
- `model-training/src/training/schemas.py` — Pydantic v2 validators for Adaption's 4 formats (`InstructionRow`, `ChatTrajectoryRow`, `PreferenceRow`, `VisionInstructionRow`), strict completion validators (`RouterCompletion`, `TicketParserCompletion`, `LogClassifierCompletion`), and `tiktoken` (`cl100k_base`) token statistics helper.
- `model-training/src/training/scrub.py` — regex scrubber redacting PEM private keys, OpenAI keys, Hugging Face tokens, GitHub tokens, AWS keys, Slack tokens, Google API keys, Databricks tokens, Bearer headers, and secret assignments.
- `model-training/src/training/utils.py` — streaming environment loader (`model-training/.env.training`), deterministic train/eval splitter (`seed=42`), unified diff path/symbol extractors, and UTF-8 LF JSONL writer.

## Wave 1 Prepared Datasets (Verified via `model-training/scripts/inspect_wave1.py`)

| Agent | Prep Script | Train File | Train Rows | Eval File | Eval Rows | Total Tokens (`cl100k_base`) | Prompt Mean / p95 / Max | Completion Mean / p95 / Max | Secret Hits | Overlap |
| --- | --- | --- | ---: | --- | ---: | ---: | --- | --- | ---: | ---: |
| `router` | `model-training/scripts/prep_01_router.py` | `model-training/data/processed/router.jsonl` | 1,200 | `model-training/data/eval/router_eval.jsonl` | 200 | 588,413 (train) / 100,696 (eval) | 435.27 / 728 / 1,869 (train)<br>449.09 / 724 / 798 (eval) | 55.08 / 64 / 78 (train)<br>54.39 / 65 / 74 (eval) | 0 | 0 |
| `ticket_parser` | `model-training/scripts/prep_02_ticket_parser.py` | `model-training/data/processed/ticket_parser.jsonl` | 2,500 | `model-training/data/eval/ticket_parser_eval.jsonl` | 200 | 1,333,566 (train) / 104,416 (eval) | 445.94 / 878 / 1,333 (train)<br>435.46 / 844 / 1,132 (eval) | 87.49 / 126 / 220 (train)<br>86.61 / 124 / 177 (eval) | 0 | 0 |
| `commit_pr_writer` | `model-training/scripts/prep_03_commit_writer.py` | `model-training/data/processed/commit_pr_writer.jsonl` | 2,500 | `model-training/data/eval/commit_pr_writer_eval.jsonl` | 200 | 901,956 (train) / 70,398 (eval) | 250.17 / 422 / 828 (train)<br>241.56 / 429 / 667 (eval) | 110.61 / 143 / 193 (train)<br>110.42 / 144 / 181 (eval) | 0 | 0 |
| `log_classifier` | `model-training/scripts/prep_04_log_classifier.py` | `model-training/data/processed/log_classifier.jsonl` | 2,000 | `model-training/data/eval/log_classifier_eval.jsonl` | 200 | 724,601 (train) / 71,510 (eval) | 274.41 / 466 / 805 (train)<br>272.76 / 498 / 717 (eval) | 87.89 / 129 / 209 (train)<br>84.79 / 124 / 162 (eval) | 0 | 0 |

### Source Provenance & Scrubbing Summary
- **`router`**: Streamed from `SWE-bench/SWE-smith` (750) and `SWE-Gym/SWE-Gym` (650); 77 secret redactions applied. Covers all 10 routing destinations (`TicketParser`, `Retriever`, `Implementer`, `TestWriter`, `Reviewer`, `SecurityReviewer`, `DBMigration`, `IaC`, `FrontendUI`, `HumanDecision`) in both train and eval splits. Note: primary `router` training set is generated in the Adaption Web UI; this local dataset provides the 200-row held-out benchmark and 1,200-row fallback backup.
- **`ticket_parser`**: Streamed from `princeton-nlp/SWE-bench_Verified` (`test` split, 480), `SWE-Gym/SWE-Gym` (1,220), and `cjsanjay/jira_train_17012024` (1,000); 7 secret redactions applied.
- **`commit_pr_writer`**: Streamed from `bigcode/commitpackft` (`python` JSONL shard: 950, `typescript` JSONL shard: 950) and `Maxscha/commitbench` (800); 4 secret redactions applied. Every completion verified to contain `### Summary`, `### Cited Symbols`, `### Confidence Assessment`, and `### Risk Assessment`.
- **`log_classifier`**: Streamed from `logpai/loghub` structured CSVs (750), `TestingResearchIllinois/idoft` (`py-data.csv` & `pr-data.csv`, 700), and `SWE-Gym/SWE-Gym` (750); 0 secret hits detected.

## Next Step (Part 2 of 5)
- Proceed to Wave 1 Adaption AutoScientist fine-tuning / Wave 2 data preparation using `model-training/data/processed/*.jsonl` and `model-training/data/eval/*_eval.jsonl`.
