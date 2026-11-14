# Wave 1 & Wave 2 Dataset Inspection Report

All datasets were streamed with `datasets.load_dataset(..., streaming=True)`, scrubbed via `training.scrub`,
validated with Pydantic v2 schemas in `training.schemas`, and measured with `tiktoken` (`cl100k_base`).

## Summary Table

| Agent | Format | Split | Path | Rows | Total Tokens | Prompt Avg (p95 / Max) | Completion Avg (p95 / Max) | Secret Hits | Overlap |
| --- | --- | --- | --- | ---: | ---: | --- | --- | ---: | ---: |
| `router` | `instruction` | train | `model-training/data/processed/router.jsonl` | 1,200 | 588,413 | 435.27 (728 / 1869) | 55.08 (64 / 78) | 0 | 0 |
| `router` | `instruction` | eval | `model-training/data/eval/router_eval.jsonl` | 200 | 100,696 | 449.09 (724 / 798) | 54.39 (65 / 74) | 0 | 0 |
| `ticket_parser` | `instruction` | train | `model-training/data/processed/ticket_parser.jsonl` | 5,000 | 2,663,292 | 445.75 (909 / 1350) | 86.91 (128 / 220) | 0 | 0 |
| `ticket_parser` | `instruction` | eval | `model-training/data/eval/ticket_parser_eval.jsonl` | 200 | 100,050 | 414.83 (885 / 1193) | 85.42 (132 / 198) | 0 | 0 |
| `commit_pr_writer` | `instruction` | train | `model-training/data/processed/commit_pr_writer.jsonl` | 5,000 | 1,810,768 | 251.35 (434 / 828) | 110.8 (143 / 195) | 0 | 0 |
| `commit_pr_writer` | `instruction` | eval | `model-training/data/eval/commit_pr_writer_eval.jsonl` | 200 | 74,452 | 261.25 (453 / 811) | 111.02 (142 / 169) | 0 | 0 |
| `log_classifier` | `instruction` | train | `model-training/data/processed/log_classifier.jsonl` | 5,000 | 1,770,597 | 259.16 (449 / 844) | 94.96 (139 / 283) | 0 | 0 |
| `log_classifier` | `instruction` | eval | `model-training/data/eval/log_classifier_eval.jsonl` | 200 | 70,668 | 258.15 (441 / 654) | 95.19 (144 / 177) | 0 | 0 |
| `implementer` | `chat` | train | `model-training/data/processed/implementer.jsonl` | 5,000 | 114,266,369 | 16361.43 (29995 / 41626) | 6491.85 (15035 / 23338) | 0 | 0 |
| `implementer` | `chat` | eval | `model-training/data/eval/implementer_eval.jsonl` | 200 | 4,709,899 | 17121.1 (30608 / 36882) | 6428.4 (14347 / 17926) | 0 | 0 |
| `test_writer` | `instruction` | train | `model-training/data/processed/test_writer.jsonl` | 5,000 | 6,206,816 | 836.29 (1193 / 1636) | 405.07 (825 / 1307) | 0 | 0 |
| `test_writer` | `instruction` | eval | `model-training/data/eval/test_writer_eval.jsonl` | 200 | 260,793 | 879.86 (1204 / 1293) | 424.11 (839 / 954) | 0 | 0 |
| `reviewer` | `preference` | train | `model-training/data/processed/reviewer_dpo.jsonl` | 5,000 | 4,338,028 | 585.67 (801 / 1629) | 281.94 (356 / 530) | 0 | 0 |
| `reviewer` | `preference` | eval | `model-training/data/eval/reviewer_dpo_eval.jsonl` | 200 | 170,442 | 574.55 (758 / 1217) | 277.66 (363 / 473) | 0 | 0 |
| `security_reviewer` | `preference` | train | `model-training/data/processed/security_reviewer_dpo.jsonl` | 5,000 | 4,656,693 | 612.92 (959 / 1646) | 318.42 (383 / 423) | 0 | 0 |
| `security_reviewer` | `preference` | eval | `model-training/data/eval/security_reviewer_eval.jsonl` | 200 | 185,611 | 610.49 (949 / 1196) | 317.57 (375 / 420) | 0 | 0 |

## Label & Schema Distributions

### `router`
- Train distribution: `{"DBMigration": 56, "FrontendUI": 51, "HumanDecision": 29, "IaC": 37, "Implementer": 176, "Retriever": 182, "Reviewer": 174, "SecurityReviewer": 135, "TestWriter": 172, "TicketParser": 188}`
- Eval distribution: `{"DBMigration": 10, "FrontendUI": 7, "HumanDecision": 5, "IaC": 4, "Implementer": 31, "Retriever": 25, "Reviewer": 32, "SecurityReviewer": 25, "TestWriter": 39, "TicketParser": 22}`

### `ticket_parser`
- Train distribution: `{"decision_card:False": 4725, "decision_card:True": 275, "feature": 848, "fix": 3478, "performance": 168, "priority:P0": 1236, "priority:P1": 1789, "priority:P2": 1452, "priority:P3": 523, "refactor": 59, "security": 447}`
- Eval distribution: `{"decision_card:False": 191, "decision_card:True": 9, "feature": 45, "fix": 135, "performance": 3, "priority:P0": 43, "priority:P1": 70, "priority:P2": 59, "priority:P3": 28, "refactor": 2, "security": 15}`

### `commit_pr_writer`
- Train distribution: `{"markdown_valid": 5000}`
- Eval distribution: `{"markdown_valid": 200}`

### `log_classifier`
- Train distribution: `{"baseline_failure": 1158, "flaky_test": 1735, "missing_dependency": 482, "regression": 366, "sandbox_blocked": 833, "syntax_error": 426}`
- Eval distribution: `{"baseline_failure": 56, "flaky_test": 65, "missing_dependency": 22, "regression": 14, "sandbox_blocked": 30, "syntax_error": 13}`

### `implementer`
- Train distribution: `{"chat_valid": 5000, "trajectories_with_tool_calls": 2579}`
- Eval distribution: `{"chat_valid": 200, "trajectories_with_tool_calls": 103}`

### `test_writer`
- Train distribution: `{"test_diff_valid": 5000}`
- Eval distribution: `{"test_diff_valid": 200}`

### `reviewer`
- Train distribution: `{"preference_valid": 5000}`
- Eval distribution: `{"preference_valid": 200}`

### `security_reviewer`
- Train distribution: `{"benign_patched_or_unreachable": 2500, "exploitable": 2500}`
- Eval distribution: `{"benign_patched_or_unreachable": 100, "exploitable": 100}`

