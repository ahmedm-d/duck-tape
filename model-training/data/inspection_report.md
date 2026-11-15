# Stage 4 Pre-Flight Data Audit Report

Audited 12 datasets (`processed` + `eval`) in **21.08s** using `ProcessPoolExecutor(max_workers=8)`.

| Dataset | Train Rows | Eval Rows | Schema Valid % | Graph Contract Check | Augmentation Needed for Stage 4 | Status (PASS/FIXED) |
| --- | ---: | --- | ---: | --- | --- | --- |
| `router` | 1200 | 200 | 100.00% | PASS (1400 blocks valid JSON) | None | PASS |
| `ticket_parser` | 5000 | 200 | 100.00% | PASS (5200 blocks valid JSON) | None | PASS |
| `implementer` | 5000 | 200 | 100.00% | PASS (5200 blocks: focal_sig/edges/blast OK) | None | FIXED (truncated oversized chat tool outputs (<45k est tokens)) |
| `test_writer` | 5000 | 200 | 100.00% | PASS (5200 blocks: focal_sig/edges/blast OK) | None | PASS |
| `security_reviewer_dpo` | 5000 | 200 | 100.00% | PASS (5200 blocks: sink_reachable!=false OK) | None | PASS |
| `reviewer_dpo` | 5000 | 200 (reviewer_eval.jsonl:200) | 100.00% | PASS (5400 blocks valid JSON) | None | PASS |
| `log_classifier` | 5000 | 200 | 100.00% | PASS (5200 blocks valid JSON) | None | PASS |
| `commit_pr_writer` | 5000 | 200 | 100.00% | PASS (5200 blocks valid JSON) | None | PASS |
| `db_migration` | 5000 | 200 | 100.00% | PASS (5200 blocks valid JSON) | None | PASS |
| `iac` | 5000 | 200 | 100.00% | PASS (5200 blocks valid JSON) | None | PASS |
| `frontend_ui` | 2500 | 100 | 100.00% | N/A (no graph blocks) | None | PASS |
| `retriever_triplets` | 5000 | 300 (retriever_eval.jsonl:300) | 100.00% | N/A (no graph blocks) | None | PASS |

## Sample Rows (< 300 chars)

- **`ticket_parser`**: `{"prompt": "Parse this incoming software maintenance issue into structured JSON.\nSource: GitHub Issue (SWE-Gym)\nProject/Repository: pandas-dev/pandas\n\nIssue Text:\nBUG?: Series.any / Series.all behavior for pyarrow vs. non-pyarrow empty/all-NA\n### Pandas version checks\n\n- ...`
- **`implementer`**: `{"chat": [{"role": "system", "content": "You are a helpful assistant that can interact with a computer to solve tasks."}, {"role": "user", "content": "<uploaded_files>\n/testbed\n</uploaded_files>\nI've uploaded a python code repository in the directory /testbed. Consider the fol...`
- **`security_reviewer_dpo`**: `{"prompt": "Assess whether this dependency advisory and code context represent an actionable vulnerability in the repository.\nRules:\n1. A dependency version match establishes affected-version applicability, never automatic exploitability.\n2. Treat 'sink_reachable: null' in '<g...`
