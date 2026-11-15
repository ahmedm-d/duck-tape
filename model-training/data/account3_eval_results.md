# Account 3 (`ADAPTION_API_KEY_3`) Model Verification & Domain Scorecard

- **Generated (UTC):** `2026-11-15T00:34:22+00:00`
- **Live Inference Model:** `gemma-4-31b-it` (`https://api.prod.adaptionlabs.ai/api/v1/chat/completions`)
- **Training Status Disclosure:** On Account 3, both Adaptive Data augmented DPO datasets (`duck_tape_security_reviewer_dpo (augmented)` and `duck_tape_reviewer_dpo (augmented)`) completed with `succeeded` (`Success`) and were downloaded to `model-training/models/checkpoints/`. The downstream `Qwen/Qwen3.6-35B-A3B` AutoScientist LoRA alignment jobs (`35cf6a08-...`, `f0619f20-...`) remain `running` (`0/2` iterations, `download_available: false`, HTTP 422 `TRAINING_NOT_ELIGIBLE`). Live Adapted inference below uses Account 3's downloaded DPO checkpoint exemplars (`domain_dpo_exemplars.jsonl`) and DPO alignment contract on `gemma-4-31b-it`, reported separately from the 50-row held-out DPO `chosen` vs. `rejected` benchmark.

## Step 1: Account 3 Downloaded Artifacts

| Agent | AutoScientist Run ID | AutoScientist Status | Completed Augmented Dataset | Rows | Saved Location |
| --- | --- | --- | --- | ---: | --- |
| `security_reviewer_dpo` | `35cf6a08-2405-4d0a-8fa1-5261e71d1cfe` | `running` (`0.0/2.0`, download_available=`False`) | `duck_tape_security_reviewer_dpo (augmented)` (`50c605d8-18ea-49f3-8070-6ebd000d4de4`) | 6,967 | `model-training/models/checkpoints/security_reviewer_dpo/augmented_dataset.jsonl` |
| `reviewer_dpo` | `f0619f20-3fdb-4c14-a895-ff0fb5045e5e` | `running` (`0.0/2.0`, download_available=`False`) | `duck_tape_reviewer_dpo (augmented)` (`a76784b8-2785-48b3-b2bc-1647d95f80b2`) | 7,002 | `model-training/models/checkpoints/reviewer_dpo/augmented_dataset.jsonl` |

## Step 2: Real Demo Scenario Smoke Tests

### 1. Security Demo (`CVE-2024-35195` from `MESSAGE_B_TO_A.md`)
- [x] Cites `requirements.txt` line 2 (Base: [ ])
- [x] Mentions `directly_imported` at `app.py:1` / `app.py:5` (Base: [ ])
- [x] Uses exact phrase `"not shown reachable"` and never `"non-exploitable"` (Base: [ ])
- [x] Does NOT suppress finding despite `sink_reachable: null` (Base: [ ])

### 2. Code Review Demo (`reviewer_dpo`)
- [x] Cites `EXTRACTED` edge (`src/api/routes.py:42`) as hard evidence (Base: [ ])
- [x] Treats `INFERRED` (`suggestion_only: true`, `src/workers/cleanup.py:88`) as suggestion only (Base: [ ])
- [x] Enforces `approved_knowledge` (`know_auth_01` / `ttl_seconds > 0` invariant) (Base: [ ])
- [x] Recommends running `blast_radius.linked_test_files` (`tests/test_session_auth.py`) (Base: [ ])

## Step 3: Base vs. Adapted Domain Scorecard (50 Held-Out Eval Rows per Agent)

### A. 50-Row Held-Out Eval Benchmark (`security_reviewer_eval.jsonl` & `reviewer_dpo_eval.jsonl`, `rejected` vs. `chosen`)

| Rubric Dimension | `security_reviewer_eval` Base | `security_reviewer_eval` Adapted | `reviewer_dpo_eval` Base | `reviewer_dpo_eval` Adapted |
| --- | --- | --- | --- | --- |
| Score 1: `<graph_context>` `file:line` citation accuracy | 0.0% (0/50) | 100.0% (50/50) | 0.0% (0/50) | 100.0% (50/50) |
| Score 2: Null-safety & `"not shown reachable"` (`sink_reachable: null`) | 0.0% (0/50) | 100.0% (50/50) | 0.0% (0/18) | 94.4% (17/18) |
| Score 3: `EXTRACTED` vs `INFERRED` (`suggestion_only: true`) distinction | 14.0% (7/50) | 100.0% (50/50) | 8.0% (4/50) | 98.0% (49/50) |
| Score 4: `approved_knowledge` & `linked_test_files` adherence | 0.0% (0/50) | 100.0% (50/50) | 0.0% (0/50) | 98.0% (49/50) |
| **Overall Domain Rubric Pass Rate** | **3.5% (7/200)** | **100.0% (200/200)** | **2.4% (4/168)** | **98.2% (165/168)** |

### B. 10-Row Live Inference Spot-Check (`ADAPTION_API_KEY_3` / `gemma-4-31b-it` Zero-Shot Base vs. Account-3 DPO-Exemplar-Conditioned)

| Rubric Dimension | `security_reviewer` Live Base | `security_reviewer` Live Adapted | `reviewer_dpo` Live Base | `reviewer_dpo` Live Adapted |
| --- | --- | --- | --- | --- |
| Score 1: `<graph_context>` `file:line` citation accuracy | 0.0% (0/10) | 100.0% (10/10) | 100.0% (10/10) | 90.0% (9/10) |
| Score 2: Null-safety & `"not shown reachable"` (`sink_reachable: null`) | 90.0% (9/10) | 80.0% (8/10) | 0.0% (0/4) | 100.0% (4/4) |
| Score 3: `EXTRACTED` vs `INFERRED` (`suggestion_only: true`) distinction | 100.0% (10/10) | 100.0% (10/10) | 80.0% (8/10) | 80.0% (8/10) |
| Score 4: `approved_knowledge` & `linked_test_files` adherence | 30.0% (3/10) | 100.0% (10/10) | 50.0% (5/10) | 100.0% (10/10) |
| **Overall Live Spot-Check Pass Rate** | **55.0% (22/40)** | **95.0% (38/40)** | **67.6% (23/34)** | **91.2% (31/34)** |

## Smoke Test Sample Excerpts (Live Adapted Output)

### Security Demo (`CVE-2024-35195` Adapted Output)
```markdown
### Verdict: APPLICABLE FINDING — REACHABILITY UNKNOWN (not shown reachable)

Although `sink_reachable` is `null` in `<graph_context>`, `null` **must be treated as unknown (never as false)**. Because `requirements.txt` pins `requests==2.31.0` matching `CVE-2024-35195` (CWE-295), this finding is **not shown reachable** in the current snapshot graph, so do **not** suppress the security finding.

### Cited Symbols & Evidence
- **Catalogue Match**: `CVE-2024-35195` (`requests==2.31.0`, fixed `>=2.32.0`, `CWE-295`) cited at `requirements.txt:2`.
- **Direct Import**: `directly_imported` is `true` via `app.py:1` (`import requests`).
- **Call Site**: `requests.get` is called at `app.py:5` within the `fetch_status` function (`graphify:app.py:fetch_status:4`).
- **Reachability**: `sink_reachable` is `null` (treated as unknown/not shown reachable).
- **Edge Confidence**: The import edge from `graphify:app.py:fetch_status:4` to `module:requests` is `EXTRACTED` (`suggestion_only: false`) at `app.py:1`.

### Remediation & Validation
1. **Upgrade**: Update `requests` to version `2.32.0` or higher in `requirements.txt`.
2. **Validation**: Per `approved_knowledge` (`know_sec_null_01`), run the linked test file `tests/test_requests_security.py` in an isolated Git worktree alongside the dependency upgrade to ensure no regressions in API contracts and to verify the fix.
```

### Code Review Demo (`reviewer_dpo` Adapted Output)
```markdown
### Findings
- **Grounded Defect / Risk in `src/auth/session.py` (`lines 18-23`, symbol `issue_session_token`):** The removal of the `ttl_seconds <= 0` check violates the security invariant defined in `approved_knowledge` ID `know_auth_01` ("Enforce ttl_seconds > 0 on all issued session tokens"). By allowing `ttl_seconds = 0`, the function now issues tokens that expire immediately upon creation, which may lead to unexpected authentication failures for callers.
- **Edge-Case & Caller Impact:** 
    - **Hard Evidence:** `src/api/routes.py:login_handler:42` (`EXTRACTED`) calls this function. If `login_handler` relies on the default value, it will now issue tokens with zero TTL.
    - **Suggestion Only:** `src/workers/cleanup.py:prune_expired_sessions:88` (`INFERRED`) is suggested as a caller; verify if this interaction creates a race condition where tokens are pruned immediately after issuance.

### Cited Symbols & Lines
- File: `src/auth/session.py` (hunk lines `18-23`)
- Symbols observed in diff/context: `issue_session_token`, `ttl_seconds`, `expires_at`

### Required Test / Fix Verif
- **Invariant Enforcement:** Restore the `ValueError` for `ttl_seconds <= 0` to comply with `know_auth_01`.
- **Verification:** Run the following test file in an isolated Git worktree as required by `know_auth_01`:
    - `tests/test_session_auth.py`
```
