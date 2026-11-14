# Duck Tape Pre-Flight Dataset Quality & Inspection Report (`MESSAGE_B_TO_A.md` Aligned)

Fast deterministic audit across `model-training/data/processed/*.jsonl` and `model-training/data/eval/*.jsonl`.

## Verified Contracts
- `focal_symbols`: includes `signature` (`<= 240` chars), `degree`, `high_degree`, and `community`.
- `catalogue_matches`: includes `directly_imported` (`true`/`false`/`null`), `sink_reachable` (`true` or `null` ONLY — **0 `false` values**), `usage`, and `cwe`.
- Real OSV demo record `CVE-2024-35195` (`PyPI/requests==2.31.0`, `requirements.txt:2`, `app.py:1` & `app.py:5`, `sink_reachable: null`) embedded in `security_reviewer_dpo.jsonl` & `security_reviewer_eval.jsonl`.
- Security DPO contrast enforced across `called at <file>:<line>` vs `directly imported at <file>:<line>; call-site reachability not shown reachable` vs `not imported directly; transitive only`.
- Zero forbidden output keys (`needs_decision_card`, `new_global_vuln_candidate`) and zero legacy `<steward_graph_context>`, `is_god_node`, `AMBIGUOUS`, `tenant_id`, or `non-exploitable` terms.

| File | Rows | `CVE-2024-35195` Rows | `sink_reachable` Counts | `directly_imported` Counts |
| --- | ---: | ---: | --- | --- |
| `processed/commit_pr_writer.jsonl` | 5,000 | 0 | `N/A` | `N/A` |
| `processed/db_migration.jsonl` | 5,000 | 0 | `N/A` | `N/A` |
| `processed/frontend_ui.jsonl` | 2,500 | 0 | `N/A` | `N/A` |
| `processed/iac.jsonl` | 5,000 | 0 | `N/A` | `N/A` |
| `processed/implementer.jsonl` | 5,000 | 0 | `N/A` | `N/A` |
| `processed/log_classifier.jsonl` | 5,000 | 0 | `N/A` | `N/A` |
| `processed/retriever_triplets.jsonl` | 5,000 | 0 | `N/A` | `N/A` |
| `processed/reviewer_dpo.jsonl` | 5,000 | 0 | `N/A` | `N/A` |
| `processed/router.jsonl` | 1,200 | 0 | `N/A` | `N/A` |
| `processed/security_reviewer_dpo.jsonl` | 5,000 | 250 | `{'True': 500, 'None': 4500}` | `{'True': 3500, 'False': 750, 'None': 750}` |
| `processed/test_writer.jsonl` | 5,000 | 0 | `N/A` | `N/A` |
| `processed/ticket_parser.jsonl` | 5,000 | 0 | `N/A` | `N/A` |
| `eval/commit_pr_writer_eval.jsonl` | 200 | 0 | `N/A` | `N/A` |
| `eval/db_migration_eval.jsonl` | 200 | 0 | `N/A` | `N/A` |
| `eval/frontend_ui_eval.jsonl` | 100 | 0 | `N/A` | `N/A` |
| `eval/iac_eval.jsonl` | 200 | 0 | `N/A` | `N/A` |
| `eval/implementer_eval.jsonl` | 200 | 0 | `N/A` | `N/A` |
| `eval/log_classifier_eval.jsonl` | 200 | 0 | `N/A` | `N/A` |
| `eval/retriever_eval.jsonl` | 300 | 0 | `N/A` | `N/A` |
| `eval/retriever_triplets_eval.jsonl` | 300 | 0 | `N/A` | `N/A` |
| `eval/reviewer_dpo_eval.jsonl` | 200 | 0 | `N/A` | `N/A` |
| `eval/reviewer_eval.jsonl` | 200 | 0 | `N/A` | `N/A` |
| `eval/router_eval.jsonl` | 200 | 0 | `N/A` | `N/A` |
| `eval/security_reviewer_eval.jsonl` | 200 | 10 | `{'True': 20, 'None': 180}` | `{'True': 140, 'False': 30, 'None': 30}` |
| `eval/test_writer_eval.jsonl` | 200 | 0 | `N/A` | `N/A` |
| `eval/ticket_parser_eval.jsonl` | 200 | 0 | `N/A` | `N/A` |
