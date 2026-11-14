# Steward Pre-Flight Dataset Quality & Inspection Report

Comprehensive quality gate audit across all **12 specialist agent datasets** (Waves 1–3) in 
`model-training/data/processed/` and `model-training/data/eval/`.

## 1. Pre-Flight Quality Gate Checklist

- **1. Schema & JSONL Validity:** PASS — All 12 train/eval pairs (53,700 train + 2,400 eval rows) validated cleanly with Pydantic v2.
- **2. Row Count & Minimums:** PASS — All core SFT/DPO/contrastive sets have exactly 5,000 train rows; `router` has 1,200 (paired with Adaption `invent()` primary generation); `frontend_ui` has 2,500 (> 1,000 multimodal minimum).
- **3. Strict Output JSON Check:** PASS — 100% of `router`, `ticket_parser`, and `log_classifier` completions parse cleanly via `json.loads()` with zero markdown code fences.
- **4. Deduplication & Grounding Check:** PASS — All datasets exceed >95.0% prompt uniqueness, `train/eval overlap = 0`, and DPO `rejected` responses all exceed >50 characters.
- **5. Token & Secret Check:** PASS — Every row across all 12 datasets is strictly `< 45,000` tokens (`cl100k_base`) and `secret_hits = 0`.

## 2. Dataset Summary & AutoScientist Augmentation Plan

| # | Agent | Format | Base Model | Train Rows | Eval Rows | Total Tokens (Train / Eval) | Max Tokens | Uniqueness % | Overlap | Secret Hits | `augmentation_domain_rows` (Target) |
| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | `router` | `instruction` | Gemma 4 (26B/31B) | 1,200 | 200 | 588,413 / 100,696 | 1,928 | 99.92% / 100.00% | 0 | 0 | `8,800` (→ 10,000) |
| 2 | `ticket_parser` | `instruction` | Gemma 4 (26B/31B) | 5,000 | 200 | 2,663,292 / 100,050 | 1,468 | 100.00% / 100.00% | 0 | 0 | `5,000` (→ 10,000) |
| 3 | `commit_pr_writer` | `instruction` | Gemma 4 (26B/31B) | 5,000 | 200 | 1,810,768 / 74,452 | 950 | 100.00% / 100.00% | 0 | 0 | `5,000` (→ 10,000) |
| 4 | `log_classifier` | `instruction` | Gemma 4 (26B/31B) | 5,000 | 200 | 1,770,597 / 70,668 | 903 | 100.00% / 100.00% | 0 | 0 | `5,000` (→ 10,000) |
| 5 | `implementer` | `chat` | Qwen 3.8 (27B/35B) | 5,000 | 200 | 114,266,369 / 4,709,899 | 44,986 | 100.00% / 100.00% | 0 | 0 | `5,000` (→ 10,000) |
| 6 | `test_writer` | `instruction` | Qwen 3.8 (27B/35B) | 5,000 | 200 | 6,206,816 / 260,793 | 2,431 | 100.00% / 100.00% | 0 | 0 | `5,000` (→ 10,000) |
| 7 | `reviewer` | `preference` | Qwen 3.8 (27B/35B) | 5,000 | 200 | 4,338,028 / 170,442 | 1,894 | 100.00% / 100.00% | 0 | 0 | `7,000` (→ 12,000) |
| 8 | `security_reviewer` | `preference` | Qwen 3.8 (27B/35B) | 5,000 | 200 | 4,656,693 / 185,611 | 1,938 | 100.00% / 100.00% | 0 | 0 | `7,000` (→ 12,000) |
| 9 | `db_migration` | `instruction` | Qwen 3.8 (27B/35B) | 5,000 | 200 | 2,931,167 / 117,747 | 2,005 | 100.00% / 100.00% | 0 | 0 | `5,000` (→ 10,000) |
| 10 | `iac` | `instruction` | Qwen 3.8 (27B/35B) | 5,000 | 200 | 2,876,788 / 114,712 | 1,459 | 100.00% / 100.00% | 0 | 0 | `5,000` (→ 10,000) |
| 11 | `frontend_ui` | `vision_instruction` | Gemma 4 31B VLM / Qwen 3.5 VLM | 2,500 | 100 | 2,226,608 / 89,168 | 8,911 | 100.00% / 100.00% | 0 | 0 | `7,500` (→ 10,000) |
| 12 | `retriever` | `contrastive_triplet` | Qwen3-Embedding-4B | 5,000 | 300 | 4,205,738 / 244,932 | 2,436 | 100.00% / 100.00% | 0 | 0 | `5,000` (→ 10,000) |

## 3. Strict JSON & Grounding Checks

| Agent | Strict JSON Valid | Markdown Fence Hits | Min Rejected Chars (DPO) | Status |
| --- | --- | ---: | --- | --- |
| `router` | 100% (1,400/1,400) | 0 | N/A | PASS |
| `ticket_parser` | 100% (5,200/5,200) | 0 | N/A | PASS |
| `commit_pr_writer` | N/A (instruction) | N/A | N/A | PASS |
| `log_classifier` | 100% (5,200/5,200) | 0 | N/A | PASS |
| `implementer` | N/A (chat) | N/A | N/A | PASS |
| `test_writer` | N/A (instruction) | N/A | N/A | PASS |
| `reviewer` | N/A (DPO Markdown) | N/A | 347 chars (> 50) | PASS |
| `security_reviewer` | N/A (DPO Markdown) | N/A | 205 chars (> 50) | PASS |
| `db_migration` | N/A (instruction) | N/A | N/A | PASS |
| `iac` | N/A (instruction) | N/A | N/A | PASS |
| `frontend_ui` | N/A (vision_instruction) | N/A | N/A | PASS |
| `retriever` | N/A (contrastive_triplet) | N/A | N/A | PASS |

## 4. Label, Framework & Source Distributions

- **`router`** (`train` `{'DBMigration': 56, 'FrontendUI': 51, 'HumanDecision': 29, 'IaC': 37, 'Implementer': 176, 'Retriever': 182, 'Reviewer': 174, 'SecurityReviewer': 135, 'TestWriter': 172, 'TicketParser': 188}`, `eval` `{'DBMigration': 10, 'FrontendUI': 7, 'HumanDecision': 5, 'IaC': 4, 'Implementer': 31, 'Retriever': 25, 'Reviewer': 32, 'SecurityReviewer': 25, 'TestWriter': 39, 'TicketParser': 22}`)
- **`ticket_parser`** (`train` `{'feature': 848, 'fix': 3478, 'performance': 168, 'refactor': 59, 'security': 447}`, `eval` `{'feature': 45, 'fix': 135, 'performance': 3, 'refactor': 2, 'security': 15}`)
- **`commit_pr_writer`** (`train` `{'markdown_valid': 5000}`, `eval` `{'markdown_valid': 200}`)
- **`log_classifier`** (`train` `{'baseline_failure': 1158, 'flaky_test': 1735, 'missing_dependency': 482, 'regression': 366, 'sandbox_blocked': 833, 'syntax_error': 426}`, `eval` `{'baseline_failure': 56, 'flaky_test': 65, 'missing_dependency': 22, 'regression': 14, 'sandbox_blocked': 30, 'syntax_error': 13}`)
- **`implementer`** (`train` `{'chat_valid': 5000, 'with_tool_calls': 2579}`, `eval` `{'chat_valid': 200, 'with_tool_calls': 103}`)
- **`test_writer`** (`train` `{'markdown_valid': 5000}`, `eval` `{'markdown_valid': 200}`)
- **`reviewer`** (`train` `{'preference_valid': 5000}`, `eval` `{'preference_valid': 200}`)
- **`security_reviewer`** (`train` `{'benign_or_unreachable': 2500, 'exploitable': 2500}`, `eval` `{'benign_or_unreachable': 100, 'exploitable': 100}`)
- **`db_migration`** (`train` `{'alembic': 824, 'django': 953, 'flyway_sql': 2456, 'prisma': 767}`, `eval` `{'alembic': 35, 'django': 55, 'flyway_sql': 93, 'prisma': 17}`)
- **`iac`** (`train` `{'dockerfile': 2077, 'kubernetes': 2121, 'terraform': 802}`, `eval` `{'dockerfile': 86, 'kubernetes': 79, 'terraform': 35}`)
- **`frontend_ui`** (`train` `{'vision_valid': 2500}`, `eval` `{'vision_valid': 100}`)
- **`retriever`** (`train` `{'neg_count:2': 1437, 'neg_count:3': 3563, 'triplets_valid': 5000}`, `eval` `{'neg_count:2': 100, 'neg_count:3': 200, 'triplets_valid': 300}`)

## 5. Formatted Sample Rows (2 per Agent)

### Agent 1: `router` (`instruction`)
#### Sample 1
- **Prompt:**
```text
Route this Duck Tape maintenance job to the next specialist agent.
Source Dataset: SWE-Gym/SWE-Gym
Repository: python/mypy
Workflow Stage: parsed
State: Ticket parsed: true | Candidate hints: mypy/constraints.py, subtypes.py, constraints.py, types.py | Evidence retrieved: false

Problem Statement:
"Parameters cannot be constrained" with generic ParamSpec
**Crash Report**

Ran into this one w
... [truncated]
```
- **Completion:**
```json
{"next_agent": "Retriever", "routing_reason": "Ticket is parsed for python/mypy, but snapshot-bound call sites and file evidence have not been retrieved yet.", "risk_level": "low", "token_budget": 6144}
```

#### Sample 2
- **Prompt:**
```text
Route this Duck Tape maintenance job to the next specialist agent.
Source Dataset: SWE-bench/SWE-smith
Repository: swesmith/oauthlib__oauthlib.1fd52536
Workflow Stage: tests_ready
State: Ticket parsed: true | Modified files: oauthlib/oauth2/rfc6749/tokens.py | Target tests: ['tests/oauth2/rfc6749/clients/test_base.py::ClientTest::test_add_mac_token', 'tests/oauth2/rfc6749/test_tokens.py::Token
... [truncated]
```
- **Completion:**
```json
{"next_agent": "Reviewer", "routing_reason": "Patch and regression test targets (1 file(s)) are prepared and require an independent review pass.", "risk_level": "low", "token_budget": 6144}
```

### Agent 2: `ticket_parser` (`instruction`)
#### Sample 1
- **Prompt:**
```text
Parse this incoming software maintenance issue into structured JSON.
Source: GitHub Issue (SWE-Gym)
Project/Repository: pandas-dev/pandas

Issue Text:
BUG?: Series.any / Series.all behavior for pyarrow vs. non-pyarrow empty/all-NA
### Pandas version checks

- [X] I have checked that this issue has not already been reported.

- [X] I have confirmed this bug exists on the [latest version](https:
... [truncated]
```
- **Completion:**
```json
{"title": "BUG?: Series.any / Series.all behavior for pyarrow vs. non-pyarrow empty/all-NA", "source_tag": "ticket", "change_type": "fix", "priority": "P2", "candidate_files_or_symbols": ["doc/source/whatsnew/v2.1.0.rst", "pandas/core/arrays/arrow/array.py", "Series.any", "Series.all", "pyarrow.compute.any", "pyarrow.compute.all", "ser.any", "ser.all"], "needs_decision_card": true, "decision_options": ["Apply minimal localized fix preserving existing p
... [truncated]
```

#### Sample 2
- **Prompt:**
```text
Parse this incoming software maintenance issue into structured JSON.
Source: GitHub Issue (SWE-Gym)
Project/Repository: pandas-dev/pandas

Issue Text:
DEPR: `sort_columns` in `plot`
### Pandas version checks

- [X] I have checked that this issue has not already been reported.

- [X] I have confirmed this bug exists on the [latest version](https://pandas.pydata.org/docs/whatsnew/index.html)
... [truncated]
```
- **Completion:**
```json
{"title": "DEPR: `sort_columns` in `plot`", "source_tag": "ticket", "change_type": "fix", "priority": "P2", "candidate_files_or_symbols": ["doc/source/whatsnew/v1.5.0.rst", "pandas/plotting/_core.py", "sort_columns", "random.normal", "plot.box", "fc36.x86_64", "en_US.utf8", "en_US.UTF"], "needs_decision_card": false, "decision_options": []}
```

### Agent 3: `commit_pr_writer` (`instruction`)
#### Sample 1
- **Prompt:**
````text
Write a grounded PR / commit evidence summary in Markdown for the following git diff.
Source: bigcode/commitpackft (python)
Repository: blue-yonder/turbodbc
Changed Files: python/turbodbc/exceptions.py

Git Diff:
```diff
--- a/python/turbodbc/exceptions.py
+++ b/python/turbodbc/exceptions.py
@@ -1,12 +1,18 @@
 from __future__ import absolute_import
 
 from functools import wraps
-from exceptio
... [truncated]
````
- **Completion:**
```markdown
### Summary
Fix Python 2/3 exception base class compatibility.

### Cited Symbols
- `python/turbodbc/exceptions.py`
- Modified/added symbols: `Error`

### Confidence Assessment
Medium — diff clearly modifies 1 named symbol(s) across 1 file(s) (+8/-2 lines), though no test file diff is shown in this patch.

### Risk Assessment
Low-Medium — bounded edit to 1 file(s) (+8/-2 lines); run targeted unit tests for modified symbols before merging.
```

#### Sample 2
- **Prompt:**
````text
Write a grounded PR / commit evidence summary in Markdown for the following git diff.
Source: bigcode/commitpackft (python)
Repository: vmalloc/mailboxer
Changed Files: scripts/build_tar.py

Git Diff:
```diff
--- a/scripts/build_tar.py
+++ b/scripts/build_tar.py
@@ -8,6 +8,8 @@
 def _is_dir_newer(directory, filename):
     file_mtime = os.stat(filename).st_mtime
     for dirname, _, filenames
... [truncated]
````
- **Completion:**
```markdown
### Summary
Fix building tar in deployment.

### Cited Symbols
- `scripts/build_tar.py`
- Module-level or configuration lines within the listed file path(s)

### Confidence Assessment
Medium — localized diff across 1 file(s) (+4/-1 lines) affecting module-level statements or configuration without new test diffs.

### Risk Assessment
Low-Medium — bounded edit to 1 file(s) (+4/-1 lines); run targeted unit tests for modified symbols before merging.
```

### Agent 4: `log_classifier` (`instruction`)
#### Sample 1
- **Prompt:**
````text
Classify the failure category and root cause of this bounded build/test log.
Source: TestingResearchIllinois/idoft (python-pytest)
Exit Code: 1

Log Output:
```
$ pytest -v tests/test_main.py::test_set_item
Project: https://github.com/hefnawi/json-storage-manager @ c7521fc4a576
FAILED tests/test_main.py::test_set_item - AssertionError: intermittent failure (NIO)
Re-run note: test passed in iso
... [truncated]
````
- **Completion:**
```json
{"failure_category": "flaky_test", "failing_tests": ["tests/test_main.py::test_set_item"], "root_cause_summary": "Test `tests/test_main.py::test_set_item` exhibits non-idempotent execution leaving persistent state across runs (IDoFT category NIO).", "recommended_action": "Isolate shared state/ordering assumptions and re-run the test both in isolation and under randomized ordering."}
```

#### Sample 2
- **Prompt:**
````text
Classify the failure category and root cause of this bounded build/test log.
Source: TestingResearchIllinois/idoft (python-pytest)
Exit Code: 1

Log Output:
```
$ pytest -v tests/test_routing.py::TestRouting::test_before_middleware
Project: https://github.com/joegasewicz/Flask-JWT-Router @ 0fb1e256c814
FAILED tests/test_routing.py::TestRouting::test_before_middleware - AssertionError: intermit
... [truncated]
````
- **Completion:**
```json
{"failure_category": "flaky_test", "failing_tests": ["tests/test_routing.py::TestRouting::test_before_middleware"], "root_cause_summary": "Test `tests/test_routing.py::TestRouting::test_before_middleware` exhibits nondeterministic timing, concurrency, or random seed sensitivity (IDoFT category NOD).", "recommended_action": "Isolate shared state/ordering assumptions and re-run the test both in isolation and under randomized ordering."}
```

### Agent 5: `implementer` (`chat`)
#### Sample 1
- **Turns:** `45` (`system -> user -> assistant -> tool -> assistant -> tool -> assistant -> tool...`)
- **User Prompt Excerpt:**
```text
<uploaded_files>
/testbed
</uploaded_files>
I've uploaded a python code repository in the directory /testbed. Consider the following PR description:

<pr_description>
# Editex algorithm returns incorrect distance for ungrouped characters

I've been using the Editex algorithm from the textdistance library and noticed that it's not calculating distances co
... [truncated]
```
- **Assistant Excerpt:**
```text
I'll help you implement the necessary changes to fix the Editex algorithm in the textdistance library. Let's follow the steps you outlined.

## Step 1: Find and read code relevant to the PR description

First, let's explore the repository structure to locate the Editex algorithm implementation:
```

#### Sample 2
- **Turns:** `65` (`system -> user -> assistant -> tool -> assistant -> tool -> assistant -> tool...`)
- **User Prompt Excerpt:**
```text
<uploaded_files>
/testbed
</uploaded_files>
I've uploaded a python code repository in the directory /testbed. Consider the following PR description:

<pr_description>
# Paragraph conversion broken when using inline mode

I found an issue with the paragraph conversion in markdownify. When a paragraph is converted in inline mode, the text is not properly p
... [truncated]
```
- **Assistant Excerpt:**
```text
I'll help you implement the necessary changes to fix the paragraph conversion issue in markdownify when using inline mode. Let's follow the steps you outlined.

## Step 1: Find and read code relevant to the PR description

First, let's explore the repository structure to locate the relevant files:
```

### Agent 6: `test_writer` (`instruction`)
#### Sample 1
- **Prompt:**
```text
Write a reproduction / regression test diff and the exact verification command for this issue.
The test must fail on the buggy baseline and pass once the target fix is applied.
Source: kjain14/testgeneval (test)
Repository: sympy/sympy
Instance ID: sympy__sympy-24152

Issue Description:
Generate regression/unit tests in `sympy/physics/quantum/tests/test_tensorproduct.py` covering changes to `s
... [truncated]
```
- **Completion:**
````markdown
### Reproduction Test Diff
```diff
diff --git a/sympy/physics/quantum/tests/test_tensorproduct.py b/sympy/physics/quantum/tests/test_tensorproduct.py
--- a/sympy/physics/quantum/tests/test_tensorproduct.py
+++ b/sympy/physics/quantum/tests/test_tensorproduct.py
@@ -44,6 +44,13 @@ def test_tensor_product_abstract():
 def test_tensor_product_expand():
     assert TP(A + B, B + C).expand(tensorproduct=True) == \
         TP(A, B) + TP(A, C) + TP(B, B) + T
... [truncated]
````

#### Sample 2
- **Prompt:**
```text
Write a reproduction / regression test diff and the exact verification command for this issue.
The test must fail on the buggy baseline and pass once the target fix is applied.
Source: SWE-Gym/SWE-Gym
Repository: getmoto/moto
Instance ID: getmoto__moto-7580

Issue Description:
Using LastEvaluatedKey to page through an index

### Description

Paging using LastEvaluatedKey and ExclusiveStartK
... [truncated]
```
- **Completion:**
````markdown
### Reproduction Test Diff
```diff
diff --git a/tests/test_dynamodb/test_dynamodb_query.py b/tests/test_dynamodb/test_dynamodb_query.py
--- a/tests/test_dynamodb/test_dynamodb_query.py
+++ b/tests/test_dynamodb/test_dynamodb_query.py
@@ -191,11 +191,61 @@ def test_query_pagination(table_name=None):
 
 
 @pytest.mark.aws_verified
-@dynamodb_aws_verified(add_range=True, numeric_gsi_range=True)
+@dynamodb_aws_verified(add_gsi=True)
 def test_query_gsi_pag
... [truncated]
````

### Agent 7: `reviewer` (`preference`)
#### Sample 1
- **Prompt:**
````text
Perform a grounded Steward code review of this diff and caller context.
Cite only symbols, file paths, and line ranges present in the diff; flag concrete logic defects, boundary/error-handling gaps, missing edge-case tests, or architectural regressions.
Source: microsoft/CodeReviewer (train_generation)
Repository: open-source/repo
File Path: django.c

Diff:
```diff
@@ -63,7 +63,7 @@ LANGUAGES
... [truncated]
````
- **Chosen:**
```markdown
### Findings
- **Grounded Defect / Risk in `django.c` (`lines 63-70`, symbol `LANGUAGES`):** This is incorrect. `fa` is generic Farsi (Persian), not the Iranian variant.
- **Edge-Case & Caller Impact:** Verify how `LANGUAGES` interacts with `German` across boundary inputs (empty, null/None, or error states) in the shown hunk before merging.

### Cited Sy
... [truncated]
```
- **Rejected:**
```markdown
### Findings
- This entire module looks tightly coupled and will likely cause severe memory leaks under load. Please refactor the subsystem into an async event-bus microservice architecture instead of this patch.

### Cited Symbols & Lines
- General architectural concern across `django.c`.

###
... [truncated]
```

#### Sample 2
- **Prompt:**
````text
Perform a grounded Steward code review of this diff and caller context.
Cite only symbols, file paths, and line ranges present in the diff; flag concrete logic defects, boundary/error-handling gaps, missing edge-case tests, or architectural regressions.
Source: microsoft/CodeReviewer (train_generation)
Repository: open-source/repo
File Path: src/module_42.py

Diff:
```diff
@@ -973,7 +973,7 @@
... [truncated]
````
- **Chosen:**
```markdown
### Findings
- **Grounded Defect / Risk in `src/module_42.py` (`lines 973-980`, symbol `namespace`):** We should not receive any RstStream frame other than one containing the CANCEL. As written, this will just ignore a RstStream that doesn't contain CANCEL.
- **Edge-Case & Caller Impact:** Verify how `namespace` interacts with `System` across boundary in
... [truncated]
```
- **Rejected:**
```markdown
### Findings
- At `lines 1400-1425`, `LegacyOrchestratorCache.flush_all()` introduces a race condition when `DistributedLockManager.acquire_sync()` times out. Also verify the SQL transaction commit at line 1444.

### Cited Symbols & Lines
- `LegacyOrchestratorCache.flush_all` (`lines 1400-1425`)
... [truncated]
```

### Agent 8: `security_reviewer` (`preference`)
#### Sample 1
- **Prompt:**
```text
Assess whether this dependency advisory and function diff represent an exploitable vulnerability in the repository.
Rule: A dependency version match alone is not proof of exploitability — verify reachability from untrusted inputs in the caller graph and inspect the exact function symbols before raising a blocking alarm.
Source: MickyMike/cvefixes_bigvul
Project: openssl@openssl
CWE / Advisory:
... [truncated]
```
- **Chosen:**
```markdown
### Verdict: EXPLOITABLE (Reachable with Untrusted Input)
A dependency version match alone is not proof of exploitability, but tracing the caller graph confirms that untrusted input flows from `api_router.dispatch_request` through `openssl@openssl.controller.handle_untrusted_payload` directly into `ssl3_get_record` without validation or escaping.

### Ci
... [truncated]
```
- **Rejected:**
```markdown
### Verdict: NOT EXPLOITABLE (Low Priority Version Notice)
The repository pins `django==1.6.5`, which is an older dependency version, but dependency warnings alone do not warrant blocking the PR. No code changes are needed in `openssl@openssl`; defer `django` to the quarterly dependency bump.
```

#### Sample 2
- **Prompt:**
```text
Assess whether this dependency advisory and function diff represent an exploitable vulnerability in the repository.
Rule: A dependency version match alone is not proof of exploitability — verify reachability from untrusted inputs in the caller graph and inspect the exact function symbols before raising a blocking alarm.
Source: bstee615/diversevul
Project: gdk-pixbuf
CWE / Advisory: CWE-20 | G
... [truncated]
```
- **Chosen:**
```markdown
### Verdict: EXPLOITABLE (Reachable with Untrusted Input)
A dependency version match alone is not proof of exploitability, but tracing the caller graph confirms that untrusted input flows from `api_router.dispatch_request` through `gdk-pixbuf.controller.handle_untrusted_payload` directly into `gif_get_lzw` without validation or escaping.

### Cited Symbo
... [truncated]
```
- **Rejected:**
```markdown
### Verdict: STYLE / DEPENDENCY BUMP ONLY
Consider updating `werkzeug` in the lockfile when convenient. The function snippet looks fine aesthetically, so no changes to `gif_get_lzw` or caller validation are required.
```

### Agent 9: `db_migration` (`instruction`)
#### Sample 1
- **Prompt:**
```text
Author a forward migration, rollback plan, and lock/data-loss risk assessment for this database change.
Source: bigcode/commitpackft (sql)
Repository: nls-oskari/oskari-server
Migration Framework: Flyway / SQL Migration
Target File: content-resources/src/main/resources/flyway/oskari/V1_45_15__remove_deprecated_user_indicator_data_columns.sql

Target New Migration Path: `content-resources/src/m
... [truncated]
```
- **Completion:**
````markdown
### 1. Forward Migration
```sql
-- These have been moved to oskari_user_indicator_data (category is no longer needed)
ALTER TABLE oskari_user_indicator DROP COLUMN year;
ALTER TABLE oskari_user_indicator DROP COLUMN data;
ALTER TABLE oskari_user_indicator DROP COLUMN category;
ALTER TABLE oskari_user_indicator DROP COLUMN layer_id;
```

### 2. Rollback / Down Migration
```sql
-- Down migration reversing `content-resources/src/main/resources/flyway/oska
... [truncated]
````

#### Sample 2
- **Prompt:**
```text
Author a forward migration, rollback plan, and lock/data-loss risk assessment for this database change.
Source: bigcode/commitpackft (sql)
Repository: BeikeElectricity/ProjectX
Migration Framework: Flyway / SQL Migration
Target File: server/src/database/procedures.sql

Target New Migration Path: `server/src/database/procedures.sql` (new migration revision)

Migration Goal:
Add stored procedure
... [truncated]
```
- **Completion:**
````markdown
### 1. Forward Migration
```sql
-- Select top 10 result in total

DELIMITER //
CREATE PROCEDURE GetGlobalTopTen ()
   BEGIN 
      SELECT * 
      FROM Score
      ORDER BY score DESC
      LIMIT 10;
   END
//
DELIMITER ;

-- Select top 10 result for player

DELIMITER //
CREATE PROCEDURE GetPlayerTopTen (IN player INT)
   BEGIN 
      SELECT * 
      FROM Score s
      WHERE s.player = player
      ORDER BY score DESC
      LIMIT 10;
   END
//
DELIMITE
... [truncated]
````

### Agent 10: `iac` (`instruction`)
#### Sample 1
- **Prompt:**
```text
Generate a hardened Infrastructure-as-Code configuration with least-privilege IAM and network security notes.
Source: HelloBoieeee/kubernetes_config
IaC Stack: Kubernetes (.yaml)
Target Scope: kind=Deployment | metadata.name=insurance-deployment | namespace=insurance | spec_hash=9e6b2bf005

Specification:
Can you generate a K8S deployment config file? Configure Kubernetes `Deployment` named `i
... [truncated]
```
- **Completion:**
```markdown
### Least-Privilege IAM & Network Security Notes
- **Least-Privilege RBAC & Workload Identity:** Bind `Deployment/insurance-deployment` to a dedicated `ServiceAccount` (`automountServiceAccountToken: false` unless API access is required) and avoid `cluster-admin` or `*` verbs.
- **Pod & Container Hardening:** Set `securityContext.runAsNonRoot: true`, `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, `capabilities.drop: ["ALL"]`, and ex
... [truncated]
```

#### Sample 2
- **Prompt:**
```text
Generate a hardened Infrastructure-as-Code configuration with least-privilege IAM and network security notes.
Source: HelloBoieeee/kubernetes_config
IaC Stack: Kubernetes (.yaml)
Target Scope: kind=ConfigMap | metadata.name=cell-vars | namespace=flux-system | spec_hash=91b1ef4d29

Specification:
Generate a kubernetes configuration file. Configure Kubernetes `ConfigMap` named `cell-vars` in nam
... [truncated]
```
- **Completion:**
```markdown
### Least-Privilege IAM & Network Security Notes
- **Least-Privilege RBAC & Workload Identity:** Bind `ConfigMap/cell-vars` to a dedicated `ServiceAccount` (`automountServiceAccountToken: false` unless API access is required) and avoid `cluster-admin` or `*` verbs.
- **Pod & Container Hardening:** Set `securityContext.runAsNonRoot: true`, `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, `capabilities.drop: ["ALL"]`, and explicit CPU/m
... [truncated]
```

### Agent 11: `frontend_ui` (`vision_instruction`)
#### Sample 1
- **Prompt:**
```text
Convert the attached UI screenshot into a modular React + TypeScript + Tailwind CSS component.
Source: HuggingFaceM4/WebSight
Sample ID: 2421
Design Brief: Educational Institute Website: A two-column layout with a wide column for information about courses and programs and a narrow one for quick navigation, contacts, and calendar of events. A colorful des
... [truncated]
```
- **Image Context:** `data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDABIMDRANC... (len=5027 chars)`
- **Completion:**
````markdown
```tsx
import React from "react";

export interface EducationalInstituteWebsiteViewProps {
  title?: string;
  className?: string;
  onSelectAction?: (action: string) => void;
}

export const EducationalInstituteWebsiteView: React.FC<EducationalInstituteWebsiteViewProps> = ({
  title = "Welcome to Our Educational Institute",
  className = "",
  onSelectAction,
}) => {
  return (
    <div className={`w-full ${className}`.trim()} data-component="Educatio
... [truncated]
````

#### Sample 2
- **Prompt:**
```text
Convert the attached UI screenshot into a modular React + TypeScript + Tailwind CSS component.
Source: HuggingFaceM4/WebSight
Sample ID: 513
Design Brief: Healthcare Provider: A calming color palette with a large header image, a clear navigation menu, and a clean, easy-to-read layout with sections for services, patient testimonials, and insurance provide
... [truncated]
```
- **Image Context:** `data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDABIMDRANC... (len=6391 chars)`
- **Completion:**
````markdown
```tsx
import React from "react";

export interface HealthcareProviderViewProps {
  title?: string;
  className?: string;
  onSelectAction?: (action: string) => void;
}

export const HealthcareProviderView: React.FC<HealthcareProviderViewProps> = ({
  title = "Healthcare Provider",
  className = "",
  onSelectAction,
}) => {
  return (
    <div className={`w-full ${className}`.trim()} data-component="HealthcareProviderView">
      <div className="font-
... [truncated]
````

### Agent 12: `retriever` (`contrastive_triplet`)
#### Sample 1
- **Query:**
```text
[vmlaker/mpipe | python] Locate symbol implementing: Generate the diagrams using Dia.
```
- **Positive[0] (1 pos):**
```text
Repository: vmlaker/mpipe
File: doc/create.py
Symbol: runDia
Code:
def runDia(diagram):
    """Generate the diagrams using Dia."""
    ifname = '{}.dia'.format(diagram)
    ofname = '{}.png'.format(diagram)
    cmd = 'dia -t png-libart -e {} {}'.format(ofname, ifname)
    print('  {}'.format(cmd))
    subprocess.ca
... [truncated]
```
- **Negative[0] (3 neg):**
```text
Repository: vmlaker/mpipe
File: src/OrderedWorker.py
Symbol: OrderedWorker.init2
Code:
def init2(
        self, 
        input_tube,      # Read task from the input tube.
        output_tubes,    # Send result on all the output tubes.
        num_workers,     # Total number of workers in the stage.
        disable_
... [truncated]
```

#### Sample 2
- **Query:**
```text
[Python | CoSQA] Find function matching query: anaconda python 3 tensorflow
```
- **Positive[0] (1 pos):**
```text
Language: python
Code:
def transformer_ae_a3():
  """Set of hyperparameters."""
  hparams = transformer_ae_base()
  hparams.batch_size = 4096
  hparams.layer_prepostprocess_dropout = 0.3
  hparams.optimizer = "Adafactor"
  hparams.learning_rate = 0.25
  hparams.learning_rate_warmup_steps = 10000
  return hparams
```
- **Negative[0] (2 neg):**
```text
Language: python
Code:
def get_obj(ref):
    """Get object from string reference."""
    oid = int(ref)
    return server.id2ref.get(oid) or server.id2obj[oid]
```

