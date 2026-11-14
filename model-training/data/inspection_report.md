# Wave 1 Dataset Inspection Report

All datasets were streamed with `datasets.load_dataset(..., streaming=True)`, scrubbed via `training.scrub`,
validated with Pydantic v2 schemas in `training.schemas`, and measured with `tiktoken` (`cl100k_base`).

## Summary Table

| Agent | Split | Path | Rows | Total Tokens | Prompt Avg (p95 / Max) | Completion Avg (p95 / Max) | Secret Hits | Overlap |
| --- | --- | --- | ---: | ---: | --- | --- | ---: | ---: |
| `router` | train | `model-training/data/processed/router.jsonl` | 1,200 | 588,413 | 435.27 (728 / 1869) | 55.08 (64 / 78) | 0 | 0 |
| `router` | eval | `model-training/data/eval/router_eval.jsonl` | 200 | 100,696 | 449.09 (724 / 798) | 54.39 (65 / 74) | 0 | 0 |
| `ticket_parser` | train | `model-training/data/processed/ticket_parser.jsonl` | 2,500 | 1,333,566 | 445.94 (878 / 1333) | 87.49 (126 / 220) | 0 | 0 |
| `ticket_parser` | eval | `model-training/data/eval/ticket_parser_eval.jsonl` | 200 | 104,416 | 435.46 (844 / 1132) | 86.61 (124 / 177) | 0 | 0 |
| `commit_pr_writer` | train | `model-training/data/processed/commit_pr_writer.jsonl` | 2,500 | 901,956 | 250.17 (422 / 828) | 110.61 (143 / 193) | 0 | 0 |
| `commit_pr_writer` | eval | `model-training/data/eval/commit_pr_writer_eval.jsonl` | 200 | 70,398 | 241.56 (429 / 667) | 110.42 (144 / 181) | 0 | 0 |
| `log_classifier` | train | `model-training/data/processed/log_classifier.jsonl` | 2,000 | 724,601 | 274.41 (466 / 805) | 87.89 (129 / 209) | 0 | 0 |
| `log_classifier` | eval | `model-training/data/eval/log_classifier_eval.jsonl` | 200 | 71,510 | 272.76 (498 / 717) | 84.79 (124 / 162) | 0 | 0 |

## Label & Schema Distributions

### `router`
- Train distribution: `{"DBMigration": 56, "FrontendUI": 51, "HumanDecision": 29, "IaC": 37, "Implementer": 176, "Retriever": 182, "Reviewer": 174, "SecurityReviewer": 135, "TestWriter": 172, "TicketParser": 188}`
- Eval distribution: `{"DBMigration": 10, "FrontendUI": 7, "HumanDecision": 5, "IaC": 4, "Implementer": 31, "Retriever": 25, "Reviewer": 32, "SecurityReviewer": 25, "TestWriter": 39, "TicketParser": 22}`

### `ticket_parser`
- Train distribution: `{"decision_card:False": 2359, "decision_card:True": 141, "feature": 430, "fix": 1755, "performance": 74, "priority:P0": 708, "priority:P1": 772, "priority:P2": 783, "priority:P3": 237, "refactor": 38, "security": 203}`
- Eval distribution: `{"decision_card:False": 180, "decision_card:True": 20, "feature": 47, "fix": 134, "performance": 6, "priority:P0": 49, "priority:P1": 69, "priority:P2": 61, "priority:P3": 21, "security": 13}`

### `commit_pr_writer`
- Train distribution: `{"markdown_valid": 2500}`
- Eval distribution: `{"markdown_valid": 200}`

### `log_classifier`
- Train distribution: `{"baseline_failure": 586, "flaky_test": 639, "missing_dependency": 179, "regression": 108, "sandbox_blocked": 356, "syntax_error": 132}`
- Eval distribution: `{"baseline_failure": 53, "flaky_test": 61, "missing_dependency": 23, "regression": 8, "sandbox_blocked": 37, "syntax_error": 18}`

