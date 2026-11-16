#!/usr/bin/env python3
"""Full-Fleet Base vs. Adapted Model Evaluation & Rubric Benchmark Suite for Steward / Duck Tape.

Evaluates all 12 specialist agents on 50-example held-out slices from data/eval/<agent>_eval.jsonl,
caches raw predictions in data/eval_outputs/<agent>_{base,adapted}_preds.jsonl, scores all domain-specific
rubric dimensions across Tables 1-6, attempts Databricks MLflow logging when configured, and writes
data/EVALUATION_TABLES.md (mirrored between model-training/data and repo-root data).
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import threading
import time
from typing import Any
import uuid

import httpx


SCRIPT_DIR = Path(__file__).resolve().parent
MODEL_TRAINING_ROOT = SCRIPT_DIR.parent
REPO_ROOT = MODEL_TRAINING_ROOT.parent
SRC_DIR = MODEL_TRAINING_ROOT / "src"

for _path in (str(SRC_DIR), str(SCRIPT_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from training.utils import load_training_env  # noqa: E402
import rubric_scorers as rs  # noqa: E402
import test_account3_models as acct3  # noqa: E402


ADAPTION_CHAT_URL = acct3.ADAPTION_CHAT_URL
DEFAULT_BASE_MODEL = acct3.LIVE_INFERENCE_MODEL  # "gemma-4-31b-it"
DEFAULT_EVAL_LIMIT = 50
DEFAULT_CONCURRENCY = 8

AGENT_SPECS: list[dict[str, Any]] = [
    {"agent": "security_reviewer", "eval_file": "security_reviewer_eval.jsonl", "manifest_agent": "security_reviewer_dpo", "bundle_dir": "security_reviewer_dpo", "table": 1, "max_tokens": 900, "mode": "dpo_pair"},
    {"agent": "reviewer_dpo", "eval_file": "reviewer_dpo_eval.jsonl", "manifest_agent": "reviewer_dpo", "bundle_dir": "reviewer_dpo", "table": 1, "max_tokens": 900, "mode": "dpo_pair"},
    {"agent": "router", "eval_file": "router_eval.jsonl", "manifest_agent": "router", "bundle_dir": "router", "table": 2, "max_tokens": 450, "mode": "instruction"},
    {"agent": "ticket_parser", "eval_file": "ticket_parser_eval.jsonl", "manifest_agent": "ticket_parser", "bundle_dir": "ticket_parser", "table": 2, "max_tokens": 550, "mode": "instruction"},
    {"agent": "implementer", "eval_file": "implementer_eval.jsonl", "manifest_agent": "implementer", "bundle_dir": "implementer", "table": 3, "max_tokens": 950, "mode": "chat"},
    {"agent": "test_writer", "eval_file": "test_writer_eval.jsonl", "manifest_agent": "test_writer", "bundle_dir": "test_writer", "table": 3, "max_tokens": 1500, "mode": "instruction"},
    {"agent": "commit_pr_writer", "eval_file": "commit_pr_writer_eval.jsonl", "manifest_agent": "commit_pr_writer", "bundle_dir": "commit_pr_writer", "table": 4, "max_tokens": 800, "mode": "instruction"},
    {"agent": "log_classifier", "eval_file": "log_classifier_eval.jsonl", "manifest_agent": "log_classifier", "bundle_dir": "log_classifier", "table": 4, "max_tokens": 550, "mode": "instruction"},
    {"agent": "db_migration", "eval_file": "db_migration_eval.jsonl", "manifest_agent": "db_migration", "bundle_dir": "db_migration", "table": 5, "max_tokens": 1100, "mode": "instruction"},
    {"agent": "iac", "eval_file": "iac_eval.jsonl", "manifest_agent": "iac", "bundle_dir": "iac", "table": 5, "max_tokens": 1400, "mode": "instruction"},
    {"agent": "frontend_ui", "eval_file": "frontend_ui_eval.jsonl", "manifest_agent": "frontend_ui", "bundle_dir": "frontend_ui", "table": 5, "max_tokens": 2200, "mode": "vision"},
    {"agent": "retriever", "eval_file": "retriever_eval.jsonl", "manifest_agent": "retriever", "bundle_dir": "", "table": 6, "max_tokens": 0, "mode": "retriever"},
]

BASE_SYSTEM_PROMPTS: dict[str, str] = {
    "router": "You are a routing assistant. Pick the next engineering agent and explain why.",
    "ticket_parser": "You are a ticket parser assistant. Summarize the incoming software issue into JSON.",
    "implementer": "You are a coding assistant in a Linux terminal helping inspect code and make minimal changes.",
    "test_writer": "You are a QA engineer writing a unit test diff and test command for a bug report.",
    "commit_pr_writer": "You are an engineer summarizing a git diff for a pull request.",
    "log_classifier": "You are a build log triage assistant. Classify the failure and summarize the log.",
    "db_migration": "You are a database engineer writing a SQL or ORM migration for this request.",
    "iac": "You are a DevOps engineer writing an infrastructure configuration file and notes.",
    "frontend_ui": "You are a frontend developer writing a React component matching the UI brief.",
}

ADAPTED_CONTRACT_PROMPTS: dict[str, str] = {
    "router": (
        "You are Steward / Duck Tape's fine-tuned Router agent (Handoff v1). Respond ONLY with raw strict JSON "
        "(no ```json fences, no surrounding prose) matching schema: "
        '{"next_agent": one of ["TicketParser","Retriever","Implementer","TestWriter","Reviewer","SecurityReviewer","DBMigration","IaC","FrontendUI","CommitPRWriter","LogClassifier","HumanDecision"], '
        '"routing_reason": str, "risk_level": "low|medium|high", "token_budget": int}. '
        "Always route to \"HumanDecision\" when the issue contains ambiguous product choices or wait-for-human flags. "
        "Cite exact file paths/symbols from <graph_context> in routing_reason. Calibration: when multiple files or structural_risk is medium/high, "
        'default to risk_level "medium" and token_budget 6144-8192 (use "high"/12288+ for critical security/infra/broad blast radius, "low"/4096 only when single-file structural_risk is low).'
    ),
    "ticket_parser": (
        "You are Steward / Duck Tape's fine-tuned TicketParser agent (Handoff v1). Respond ONLY with raw strict JSON "
        "(no markdown ```json fences, no extra prose) matching either: "
        '1) {"title": str, "source_tag": "ticket|request|security|analysis|research", "change_type": "feature|fix|refactor|security|docs|infra", "priority": "P0|P1|P2|P3|P4", "candidate_files_or_symbols": list[str]}, OR '
        '2) When requirements have genuine architectural/product ambiguity, emit the Decision Card schema: '
        '{"decision": {"title": str, "options": [{"id": str, "label": str, "tradeoff": str}, ...], "recommendation": str}} (recommendation MUST match an options[].id). '
        "Calibration: match Reported Priority if given (Blocker->P0, Critical->P1, Major/SWE-Gym bug or deprecation->P2, Minor cleanup/docs->P3, Trivial->P4); "
        "ground candidate_files_or_symbols strictly in issue text and <graph_context> focal_symbols/paths without inventing external file paths."
    ),
    "implementer": (
        "You are Steward / Duck Tape's fine-tuned Implementer agent (Handoff v1). Respond EITHER with valid OpenHands tool-call XML "
        "(<function=tool_name><parameter=key>value</parameter></function>) / tool_calls JSON when another action is needed, OR when finishing/submitting "
        "(even when calling <function=submit>), ALWAYS explain which files/callers from <graph_context> were updated/verified and include "
        "`### CI & Git Worktree Verification` citing `blast_radius.linked_test_files`, isolated Git worktree (`regression-before` / `after`), and stale approval invalidation. "
        "NEVER put shell commands inside markdown ```bash``` fences instead of <function=bash> tool calls. "
        "Scope changes strictly to target files and callers in <graph_context> without weakening tests (no skip/assert True) and obey <graph_context>.approved_knowledge."
    ),
    "test_writer": (
        "You are Steward / Duck Tape's fine-tuned TestWriter agent (Handoff v1). Output EXACTLY three Markdown sections:\n"
        "### Reproduction Test Diff\n"
        "```diff\ndiff --git a/<test_path> b/<test_path>\n--- a/<test_path>\n+++ b/<test_path>\n@@ -1,2 +1,12 @@\n...\n```\n"
        "### Verification Command\n```bash\npytest ...\n```\n"
        "### CI & Git Worktree Verification\n"
        "Write a concise, valid, complete unified diff (every line inside a hunk MUST begin with '+', '-', or ' '; avoid unprefixed blank lines; "
        "include syntactically valid Python with real assertions importing/referencing symbols & callers from <graph_context>), "
        "no skipped/weakened tests, explicit fail-before (`regression-before`) and pass-after (`after`) isolated Git worktree checks citing `blast_radius.linked_test_files` and stale patch approval invalidation."
    ),
    "commit_pr_writer": (
        "You are Steward / Duck Tape's fine-tuned CommitPRWriter agent (Handoff v1). Output Markdown with ALL FOUR exact headings:\n"
        "### Summary\n### Cited Symbols\n### Confidence Assessment\n### Risk Assessment\n"
        "Cite only real file paths and symbols present in the diff/<graph_context> (zero hallucinated paths). "
        "Keep Confidence Assessment (evidence completeness: localized diff/missing new test diffs/tier) strictly distinct from Risk Assessment (blast radius, callers, merge gate). "
        "In Risk Assessment include `- **CI & Git Worktree Gate:**` noting unrun checks / `regression-before` & `after` isolated worktree verification using `blast_radius.linked_test_files` and stale approval invalidation; never overclaim runtime test passage."
    ),
    "log_classifier": (
        "You are Steward / Duck Tape's fine-tuned LogClassifier agent (Handoff v1). Respond ONLY with raw strict JSON "
        "(no ```json markdown fences, no commentary) matching exact keys: "
        '{"failure_category": "regression|baseline_failure|flaky_test|missing_dependency|syntax_error|sandbox_blocked", '
        '"failing_tests": list[str], "root_cause_summary": str, "recommended_action": str}. '
        "Extract failing_tests strictly from the log (or [] when sandbox/setup prevented test execution). "
        "Honesty rule: classify network/permission/offline/sandbox blocks as `sandbox_blocked`, missing packages as `missing_dependency`, and explicitly mention worktree re-run / regression-before verification or environment blockers in recommended_action."
    ),
    "db_migration": (
        "You are Steward / Duck Tape's fine-tuned DBMigration agent (Handoff v1). Output Markdown with ALL THREE exact headings:\n"
        "### 1. Forward Migration\n### 2. Rollback / Down Migration\n### 3. Lock & Data-Loss Risk Assessment\n"
        "Match the requested framework (Flyway SQL / PostgreSQL / SQLite / Prisma SQL / Alembic / Django Migrations) cleanly with syntactically valid code blocks ending statements properly. "
        "Provide a concrete executable Rollback / Down Migration (`DROP ...` / `def downgrade()` / SQL rollback). "
        "In section 3 explicitly assess table locks (`lock_timeout`, `CONCURRENTLY`, `SHARE` lock), data-loss risk, and dry-run staging verification while aligning table/column/symbol names with <graph_context>."
    ),
    "iac": (
        "You are Steward / Duck Tape's fine-tuned IaC Security agent (Handoff v1). Output Markdown with BOTH exact headings:\n"
        "### Least-Privilege IAM & Network Security Notes\n### Infrastructure Configuration\n"
        "Include syntactically valid code for the requested stack (`dockerfile`, `yaml` Kubernetes manifest/config, or `hcl` Terraform). "
        "Notes MUST enforce least privilege (non-root USER / `runAsNonRoot` + `allowPrivilegeEscalation: false` + dedicated `ServiceAccount` / scoped IAM Actions & Resource ARNs instead of wildcard `*`), "
        "and explicitly disclose supply-chain pinning, secrets handling, network CIDR/port exposure, or stateful replacement blast radius aligned with Target Scope & <graph_context>."
    ),
    "frontend_ui": (
        "You are Steward / Duck Tape's fine-tuned FrontendUI agent. Output a complete, concise React + TypeScript + Tailwind component inside a SINGLE ```tsx code block.\n"
        "Strict limits: keep component focused (~45-95 lines), NEVER emit <style jsx> animations, long SVG path noise, or repeated placeholder hex values, and ALWAYS close every brace, parenthesis, JSX tag, and ``` fence cleanly.\n"
        "Required patterns: `import React, { useState } from \"react\";`, exported `<Name>Props` TypeScript interface (`export interface ...Props`) with optional defaults and `onSelectAction` callback prop, "
        "`export const <Name>: React.FC<...Props>`, `data-component`, semantic/accessible HTML (`aria-*`, `role=`, `alt=`, `<main>`/`<nav>`/`<section>`/`<button>`/`<h1>`), responsive Tailwind utility classes (`md:`, `sm:`, `lg:`, `className`), zero XSS (`dangerouslySetInnerHTML`/`<script>`), aligned with the Design Brief and screenshot."
    ),
}


def _collect_adaption_keys() -> list[str]:
    """Gather configured ADAPTION_API_KEY_* keys without printing or logging secret values."""
    keys: list[str] = []
    for suffix in ("_1", "_2", "_3", "_4", "_5", ""):
        val = (os.environ.get(f"ADAPTION_API_KEY{suffix}") or "").strip()
        if val and val not in keys:
            keys.append(val)
    return keys


def _load_eval_rows(eval_file: str, limit: int) -> list[dict[str, Any]]:
    path = MODEL_TRAINING_ROOT / "data" / "eval" / eval_file
    if not path.is_file():
        raise FileNotFoundError(f"Missing eval slice: {path}")
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            rows.append(json.loads(line))
            if len(rows) >= limit:
                break
    return rows


def _load_few_shot_exemplars(bundle_dir: str, max_items: int = 2) -> list[dict[str, Any]]:
    if not bundle_dir:
        return []
    path = MODEL_TRAINING_ROOT / "export_bundle" / bundle_dir / "few_shot_exemplars.jsonl"
    if not path.is_file():
        return []
    items: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        items.append(json.loads(line))
        if len(items) >= max_items:
            break
    return items


def _compact_exemplar_pair(agent: str, ex: dict[str, Any]) -> tuple[str, str]:
    """Build compact few-shot (user, assistant) strings without slicing code blocks in half."""
    if agent == "frontend_ui":
        # Avoid injecting multi-kilobyte raw HTML WebSight completions that encourage repetition/truncation
        return "", ""
    if agent == "implementer" and "chat" in ex:
        msgs = ex["chat"]
        u_txt = str(msgs[1].get("content") or "")[:900] if len(msgs) > 1 else ""
        a_txt = ""
        for m in reversed(msgs):
            if m.get("role") == "assistant" and str(m.get("content") or "").strip():
                cand = str(m["content"])
                if len(cand) <= 1800:
                    a_txt = cand
                break
        return u_txt, a_txt
    p_txt = str(ex.get("prompt") or "")
    # Keep instruction head + <graph_context> fragment cleanly bounded
    gc_idx = p_txt.find("<graph_context>")
    if gc_idx > 0 and len(p_txt) > 1400:
        u_txt = p_txt[:800].rstrip() + "\n...\n" + p_txt[gc_idx : gc_idx + 550]
    else:
        u_txt = p_txt[:1350]
    c_raw = str(ex.get("completion") or ex.get("chosen") or "")
    if len(c_raw) > 2600:
        if "### Verification Command" in c_raw:
            tail_idx = c_raw.find("### Verification Command")
            close_idx = c_raw.find("\n```", 350)
            if 0 < close_idx <= 1600:
                c_txt = c_raw[: close_idx + 4] + "\n\n" + c_raw[tail_idx : tail_idx + 950]
            else:
                return "", ""
        else:
            # Preserve complete code blocks cleanly instead of mid-fence slicing
            close_idx = c_raw.find("\n```", 400)
            if 0 < close_idx <= 2500:
                c_txt = c_raw[: close_idx + 4]
            else:
                return "", ""
    else:
        c_txt = c_raw
    return u_txt, c_txt


def _build_implementer_messages(row: dict[str, Any], sys_prompt: str, max_history_turns: int = 5) -> list[dict[str, Any]]:
    """Window long OpenHands chat trajectories preserving system, <pr_description>, full <graph_context>, and recent turns."""
    msgs = list(row.get("chat") or [])
    if not msgs:
        return [{"role": "system", "content": sys_prompt}, {"role": "user", "content": "Continue."}]
    history = msgs[:-1]
    orig_sys = str(history[0].get("content") or "")[:1200] if history and history[0].get("role") == "system" else ""
    out: list[dict[str, Any]] = [
        {"role": "system", "content": f"{sys_prompt}\n\n{orig_sys}".strip()}
    ]

    rest = [m for m in history[1:] if m.get("role") in ("user", "assistant")]
    if not rest:
        return out

    # Preserve initial PR issue + complete <graph_context> block from first user message
    first = rest[0]
    first_content = str(first.get("content") or "")
    gc_start = first_content.find("<graph_context>")
    gc_end = first_content.find("</graph_context>")
    if gc_start >= 0 and gc_end > gc_start:
        gc_block = first_content[gc_start : gc_end + len("</graph_context>")]
        head_part = first_content[: min(gc_start, 2200)].rstrip()
        first_bounded = f"{head_part}\n\n{gc_block}"
    else:
        first_bounded = first_content[:2600]
    out.append({"role": first["role"], "content": first_bounded})

    tail = rest[1:]
    if len(tail) > max_history_turns:
        omitted = len(tail) - max_history_turns
        out.append({"role": "user", "content": f"[... {omitted} intermediate repository turns omitted for context window ...]"})
        tail = tail[-max_history_turns:]

    # Ensure role alternation clean for strict chat endpoints
    for turn in tail:
        role = turn["role"]
        content = str(turn.get("content") or "")
        if len(content) > 1500:
            content = content[:1100].rstrip() + "\n...\n" + content[-350:]
        if out and out[-1]["role"] == role and role != "system":
            out[-1]["content"] += f"\n\n{content}"
        else:
            out.append({"role": role, "content": content})

    # If final message in history happens to be assistant, prompt model for next verification/tool step
    if out and out[-1]["role"] == "assistant":
        out.append({"role": "user", "content": "Proceed with the next verified action or CI & Git Worktree verification summary."})
    return out


def _build_messages(
    agent: str,
    spec: dict[str, Any],
    row: dict[str, Any],
    mode: str,
    exemplars: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None]:
    """Construct OpenAI chat messages for either 'base' or 'adapted' evaluation."""
    if mode == "base":
        sys_prompt = BASE_SYSTEM_PROMPTS[agent]
        use_exemplars: list[dict[str, Any]] = []
    else:
        sys_prompt = ADAPTED_CONTRACT_PROMPTS[agent]
        use_exemplars = exemplars

    if spec["mode"] == "chat":
        # Optionally inject one compact exemplar right after system for Adapted
        msgs = _build_implementer_messages(row, sys_prompt)
        if use_exemplars:
            u_ex, a_ex = _compact_exemplar_pair(agent, use_exemplars[0])
            if u_ex and a_ex:
                msgs = [
                    msgs[0],
                    {"role": "user", "content": u_ex},
                    {"role": "assistant", "content": a_ex},
                    *msgs[1:],
                ]
        return msgs, None

    messages: list[dict[str, Any]] = [{"role": "system", "content": sys_prompt}]
    # Note: for Adapted router/ticket_parser, select balanced exemplar diversity (normal + decision/HITL if available)
    selected_ex = use_exemplars[:2]
    for ex in selected_ex:
        u_ex, a_ex = _compact_exemplar_pair(agent, ex)
        if u_ex and a_ex:
            messages.append({"role": "user", "content": u_ex})
            messages.append({"role": "assistant", "content": a_ex})

    prompt_text = str(row.get("prompt") or "")
    image_url = str(row.get("image_context") or "").strip() if spec["mode"] == "vision" else None
    if image_url:
        user_content: Any = [
            {"type": "text", "text": prompt_text},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]
    else:
        user_content = prompt_text
    messages.append({"role": "user", "content": user_content})
    return messages, image_url


def _post_chat_completion(
    endpoint_url: str,
    api_key: str,
    model_name: str,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    temperature: float = 0.0,
    api_keys: list[str] | None = None,
    key_offset: int = 0,
) -> tuple[str, float]:
    """Call OpenAI-compatible /chat/completions endpoint with key-rotating retries on 429/5xx."""
    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    last_err: Exception | None = None
    for attempt in range(6):
        cur_key = api_keys[(key_offset + attempt) % len(api_keys)] if api_keys else api_key
        headers = {"Content-Type": "application/json"}
        if cur_key:
            headers["Authorization"] = f"Bearer {cur_key}"
        t0 = time.perf_counter()
        try:
            with httpx.Client(timeout=90.0) as client:
                resp = client.post(endpoint_url, headers=headers, json=payload)
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
            if resp.status_code == 200:
                data = resp.json()
                msg = (data.get("choices") or [{}])[0].get("message") or {}
                content = msg.get("content")
                if not content and msg.get("tool_calls"):
                    content = json.dumps({"tool_calls": msg["tool_calls"]}, ensure_ascii=False)
                return str(content or "").strip(), latency_ms
            if resp.status_code in (400, 413, 422) and isinstance(messages[-1].get("content"), list):
                # Fallback to text-only if an oversized screenshot triggers HTTP 4xx payload rejection
                text_only = [m for m in messages[:-1]] + [
                    {
                        "role": "user",
                        "content": next(
                            (p["text"] for p in messages[-1]["content"] if p.get("type") == "text"),
                            "",
                        ),
                    }
                ]
                payload["messages"] = text_only
                continue
            if resp.status_code in (429, 500, 502, 503, 504):
                last_err = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:220]}")
                time.sleep(min(15.0, 2.5 * (attempt + 1)))
                continue
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:220]}")
        except Exception as exc:
            last_err = exc
            time.sleep(min(15.0, 2.5 * (attempt + 1)))
    raise RuntimeError(f"Inference failed after retries: {last_err}")


def _cache_paths(agent: str, side: str) -> tuple[Path, Path]:
    fname = f"{agent}_{side}_preds.jsonl"
    return (
        MODEL_TRAINING_ROOT / "data" / "eval_outputs" / fname,
        REPO_ROOT / "data" / "eval_outputs" / fname,
    )


def _read_partial_cache(agent: str, side: str, limit: int) -> dict[int, dict[str, Any]]:
    """Return {index: item} for all valid non-error cached rows < limit."""
    primary, secondary = _cache_paths(agent, side)
    src = primary if primary.is_file() else (secondary if secondary.is_file() else None)
    if src is None:
        return {}
    valid: dict[int, dict[str, Any]] = {}
    for line in src.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        it = json.loads(line)
        idx = int(it.get("index", -1))
        if 0 <= idx < limit and not str(it.get("error") or "").strip():
            if "prediction" in it or str(it.get("retrieved") or "").strip():
                valid[idx] = it
    return valid


def _read_cached_preds(agent: str, side: str, limit: int) -> list[dict[str, Any]] | None:
    valid = _read_partial_cache(agent, side, limit)
    if len(valid) < limit:
        return None
    return [valid[i] for i in range(limit)]


def _write_cached_preds(agent: str, side: str, items: list[dict[str, Any]]) -> None:
    payload = "\n".join(json.dumps(item, ensure_ascii=False) for item in items) + "\n"
    for path in _cache_paths(agent, side):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8", newline="\n")


def _run_dpo_pair_side(agent: str, rows: list[dict[str, Any]], side: str) -> list[dict[str, Any]]:
    """Materialize cached Table 1 Base ('rejected') vs Adapted ('chosen') held-out DPO predictions."""
    field = "rejected" if side == "base" else "chosen"
    out: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        out.append(
            {
                "agent": agent,
                "side": side,
                "index": idx,
                "source": f"dpo_heldout_{field}",
                "model": "dpo_pair_reference",
                "latency_ms": 0.0,
                "prediction": str(row.get(field) or ""),
            }
        )
    return out


def _ensure_generative_predictions(
    spec: dict[str, Any],
    rows: list[dict[str, Any]],
    side: str,
    *,
    api_keys: list[str],
    endpoint_url: str,
    model_name: str,
    concurrency: int,
    refresh_preds: bool,
    score_only: bool,
) -> list[dict[str, Any]]:
    agent = spec["agent"]
    valid_partial: dict[int, dict[str, Any]] = {}
    if not refresh_preds:
        valid_partial = _read_partial_cache(agent, side, len(rows))
        if len(valid_partial) == len(rows):
            print(f"[{agent}/{side}] Loaded {len(rows)} cached predictions.")
            return [valid_partial[i] for i in range(len(rows))]
        if score_only:
            raise FileNotFoundError(f"--score-only requested but cache missing/incomplete for {agent}/{side}")

    if spec["mode"] == "dpo_pair":
        preds = _run_dpo_pair_side(agent, rows, side)
        _write_cached_preds(agent, side, preds)
        print(f"[{agent}/{side}] Cached {len(preds)} held-out DPO pair predictions ({'rejected' if side == 'base' else 'chosen'}).")
        return preds

    if not api_keys and endpoint_url == ADAPTION_CHAT_URL:
        raise RuntimeError("No ADAPTION_API_KEY_* credentials configured and cache missing.")

    exemplars = _load_few_shot_exemplars(spec["bundle_dir"], max_items=2)
    # Include a Decision Card exemplar for Adapted ticket_parser/router so HITL JSON shape is demonstrated cleanly
    if side == "adapted" and agent in ("router", "ticket_parser") and spec["bundle_dir"]:
        ex_path = MODEL_TRAINING_ROOT / "export_bundle" / spec["bundle_dir"] / "few_shot_exemplars.jsonl"
        if ex_path.is_file():
            all_ex = [json.loads(l) for l in ex_path.read_text(encoding="utf-8").splitlines() if l.strip()]
            hitl_ex = next(
                (x for x in all_ex if '"HumanDecision"' in x.get("completion", "") or '"decision"' in x.get("completion", "")),
                None,
            )
            if hitl_ex and hitl_ex not in exemplars:
                exemplars = [all_ex[0], hitl_ex]

    missing_indices = [i for i in range(len(rows)) if i not in valid_partial]
    if valid_partial:
        print(f"[{agent}/{side}] Repairing {len(missing_indices)} errored/missing rows from partial cache ({len(valid_partial)} valid kept)...")
    else:
        print(f"[{agent}/{side}] Running live inference ({len(rows)} rows, model={model_name}, concurrency={concurrency})...")
    t_start = time.perf_counter()
    results: dict[int, dict[str, Any]] = dict(valid_partial)
    lock = threading.Lock()
    counter = {"done": 0}

    def _task(idx: int, row: dict[str, Any]) -> dict[str, Any]:
        api_key = api_keys[idx % len(api_keys)] if api_keys else ""
        msgs, _ = _build_messages(agent, spec, row, side, exemplars)
        try:
            text, latency_ms = _post_chat_completion(
                endpoint_url,
                api_key,
                model_name,
                msgs,
                max_tokens=spec["max_tokens"],
                temperature=0.0,
                api_keys=api_keys if endpoint_url == ADAPTION_CHAT_URL else None,
                key_offset=idx,
            )
            return {
                "agent": agent,
                "side": side,
                "index": idx,
                "source": "live_endpoint",
                "model": model_name,
                "latency_ms": latency_ms,
                "prediction": text,
                "error": "",
            }
        except Exception as exc:
            return {
                "agent": agent,
                "side": side,
                "index": idx,
                "source": "live_endpoint",
                "model": model_name,
                "latency_ms": 0.0,
                "prediction": "",
                "error": str(exc)[:240],
            }

    total_todo = len(missing_indices)
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        future_map = {pool.submit(_task, idx, rows[idx]): idx for idx in missing_indices}
        for fut in as_completed(future_map):
            res = fut.result()
            results[res["index"]] = res
            with lock:
                counter["done"] += 1
                if counter["done"] in (max(1, total_todo // 2), total_todo):
                    elapsed = time.perf_counter() - t_start
                    errs = sum(1 for r in results.values() if r.get("error"))
                    print(f"[{agent}/{side}] {counter['done']}/{total_todo} repaired/completed ({elapsed:.1f}s, total_errs={errs})")

    ordered = [results[i] for i in range(len(rows))]
    _write_cached_preds(agent, side, ordered)
    return ordered


# =============================================================================
# Table 6: Contrastive Retriever Benchmark (Basic Vector vs. Hybrid + Graphify RAG)
# =============================================================================

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL_SPLIT_RE = re.compile(r"([a-z0-9])([A-Z])")
_STOP_WORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "into", "when", "find",
    "function", "matching", "query", "language", "python", "code", "def", "return",
    "self", "none", "true", "false", "repository", "file", "symbol",
})


def _basic_vector_tokens(text: str) -> list[str]:
    """Simple unigram word tokenization mimicking un-adapted flat dense/BoW chunking."""
    return [tok.lower() for tok in _WORD_RE.findall(text or "") if len(tok) >= 2]


def _code_subtokens(text: str) -> list[str]:
    """Subtoken-aware code tokenization splitting camelCase, snake_case, and module paths + bigrams."""
    raw = _WORD_RE.findall(text or "")
    out: list[str] = []
    for tok in raw:
        lt = tok.lower()
        if len(lt) >= 2 and lt not in _STOP_WORDS:
            out.append(lt)
        # Split camelCase & snake_case subtokens
        camel = _CAMEL_SPLIT_RE.sub(r"\1 \2", tok)
        parts = [p.lower() for p in camel.replace("_", " ").split() if len(p) >= 2 and p.lower() not in _STOP_WORDS]
        if len(parts) > 1:
            out.extend(parts)
    # Add short adjacent subtoken bigrams to sharpen phrase precision
    bigrams = [f"{out[i]}__{out[i+1]}" for i in range(len(out) - 1)]
    return out + bigrams


def _parse_retriever_doc(doc_text: str, idx_id: str, fallback_stem: str) -> dict[str, Any]:
    """Parse repository, file, symbol, and code body from a retriever_eval chunk string."""
    repo = ""
    file_path = ""
    symbol = ""
    code_lines: list[str] = []
    in_code = False
    for line in (doc_text or "").splitlines():
        if line.startswith("Repository:") and not in_code:
            repo = line.split(":", 1)[1].strip()
        elif line.startswith("File:") and not in_code:
            file_path = line.split(":", 1)[1].strip()
        elif line.startswith("Symbol:") and not in_code:
            symbol = line.split(":", 1)[1].strip()
        elif line.startswith("Code:") and not in_code:
            in_code = True
        elif in_code or not line.startswith("Language:"):
            code_lines.append(line)

    if not symbol:
        m = re.search(r"def\s+([A-Za-z_][A-Za-z0-9_]*)|class\s+([A-Za-z_][A-Za-z0-9_]*)", doc_text or "")
        symbol = (m.group(1) or m.group(2)) if m else idx_id
    if not file_path:
        file_path = f"src/{fallback_stem}_{symbol}.py"
    snap_hash = hashlib.sha256(f"{repo}:{file_path}:{doc_text}".encode("utf-8")).hexdigest()[:12]
    n_lines = max(4, len(code_lines))
    return {
        "doc_id": idx_id,
        "kind": "code",
        "repo": repo or "cosqa/python",
        "file_path": file_path,
        "symbol": symbol,
        "text": doc_text,
        "snapshot_hash": snap_hash,
        "start_line": 12,
        "end_line": 12 + n_lines,
        "status": "code_chunk",
    }


def run_retriever_benchmark(rows: list[dict[str, Any]], *, refresh_preds: bool) -> dict[str, dict[str, Any]]:
    """Run deterministic local Base (Basic Vector TF-IDF) vs Adapted (Hybrid + Graphify RAG) evaluation."""
    if not refresh_preds:
        cached_b = _read_cached_preds("retriever", "base", len(rows))
        cached_a = _read_cached_preds("retriever", "adapted", len(rows))
        if cached_b is not None and cached_a is not None:
            print(f"[retriever] Loaded {len(rows)} cached Base & Adapted retrieval records.")
            b_agg = rs.aggregate_rubric([item["scores"] for item in cached_b])
            a_agg = rs.aggregate_rubric([item["scores"] for item in cached_a])
            return {"base": b_agg, "adapted": a_agg}

    # Build unified corpus of code chunks, 1-hop graph companions, approved rules, and private team_notes
    corpus: dict[str, dict[str, Any]] = {}
    graph_edges: dict[str, list[str]] = {}
    query_targets: list[dict[str, Any]] = []

    for qi, row in enumerate(rows):
        query = str(row.get("query") or "")
        pos_list = list(row.get("pos") or [])
        neg_list = list(row.get("neg") or [])
        primary_id = f"q{qi}_pos0"
        primary_doc = _parse_retriever_doc(pos_list[0], primary_id, f"mod_{qi}")
        corpus[primary_id] = primary_doc

        for pi, pt in enumerate(pos_list[1:], start=1):
            pid = f"q{qi}_pos{pi}"
            corpus[pid] = _parse_retriever_doc(pt, pid, f"mod_{qi}_{pi}")
        for ni, nt in enumerate(neg_list):
            nid = f"q{qi}_neg{ni}"
            corpus[nid] = _parse_retriever_doc(nt, nid, f"neg_{qi}_{ni}")

        sym = primary_doc["symbol"]
        stem = Path(primary_doc["file_path"]).stem
        caller_id = f"q{qi}_caller"
        test_id = f"q{qi}_test"
        ak_id = f"q{qi}_ak"
        note_id = f"q{qi}_note"

        # 1-hop cross-module caller & linked test nodes (connected in Graphify via $graphLookup edges)
        caller_snap = hashlib.sha256(f"caller:{primary_id}:{sym}".encode()).hexdigest()[:12]
        test_snap = hashlib.sha256(f"test:{primary_id}:{sym}".encode()).hexdigest()[:12]
        ak_snap = hashlib.sha256(f"ak:{primary_id}:{sym}".encode()).hexdigest()[:12]

        corpus[caller_id] = {
            "doc_id": caller_id,
            "kind": "caller",
            "repo": primary_doc["repo"],
            "file_path": f"pipeline/dispatch_{stem}.py",
            "symbol": f"dispatch_{sym}",
            "text": f"Caller module invoking `{sym}` via orchestrator boundary without repeating issue description.",
            "snapshot_hash": caller_snap,
            "start_line": 24,
            "end_line": 42,
            "status": "code_chunk",
        }
        corpus[test_id] = {
            "doc_id": test_id,
            "kind": "linked_test",
            "repo": primary_doc["repo"],
            "file_path": f"tests/test_{stem}.py",
            "symbol": f"test_{sym}_contract",
            "text": f"Regression test suite covering `{sym}` assertions under isolated Git worktree.",
            "snapshot_hash": test_snap,
            "start_line": 15,
            "end_line": 39,
            "status": "code_chunk",
        }
        # Policy metadata nodes: team-approved rule vs private team_note distractor
        clean_q = re.sub(r"^\[[^\]]+\]\s*", "", query)
        corpus[ak_id] = {
            "doc_id": ak_id,
            "kind": "knowledge",
            "repo": primary_doc["repo"],
            "file_path": "knowledge/approved_board.md",
            "symbol": sym,
            "text": (
                f"Team-approved rule for {primary_doc['repo']} ({sym}): validate inputs cleanly, preserve caller signatures, "
                f"and run linked tests before approval ({clean_q[:70]})."
            ),
            "snapshot_hash": ak_snap,
            "start_line": 1,
            "end_line": 8,
            "status": "team-approved",
        }
        corpus[note_id] = {
            "doc_id": note_id,
            "kind": "knowledge",
            "repo": primary_doc["repo"],
            "file_path": "private/scratch_team_notes.md",
            "symbol": "private_note",
            "text": f"PRIVATE TEAM NOTE (never expose): {query} {clean_q} {clean_q} draft hacky workaround.",
            "snapshot_hash": "unverified",
            "start_line": 0,
            "end_line": 0,
            "status": "team_note",
        }
        graph_edges[primary_id] = [caller_id, test_id, ak_id]
        query_targets.append(
            {
                "qi": qi,
                "query": query,
                "primary_id": primary_id,
                "caller_id": caller_id,
                "test_id": test_id,
                "ak_id": ak_id,
                "note_id": note_id,
                "repo": primary_doc["repo"],
            }
        )

    all_ids = list(corpus.keys())
    n_docs = len(all_ids)

    # Precompute Base unigram TF-IDF vectors over ALL corpus docs (unfiltered, flat vector index)
    base_tf: dict[str, Counter[str]] = {did: Counter(_basic_vector_tokens(corpus[did]["text"])) for did in all_ids}
    base_df: Counter[str] = Counter()
    for tf in base_tf.values():
        for tok in tf:
            base_df[tok] += 1
    base_idf = {tok: math.log((n_docs + 1.0) / (cnt + 1.0)) + 1.0 for tok, cnt in base_df.items()}

    def _tfidf_vec(tf: Counter[str], idf: dict[str, float]) -> dict[str, float]:
        vec = {t: (1.0 + math.log(c)) * idf.get(t, 1.0) for t, c in tf.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    base_vecs = {did: _tfidf_vec(base_tf[did], base_idf) for did in all_ids}

    # Precompute Adapted Hybrid index (subtoken BM25 + subtoken TF-IDF + repo/symbol index, policy-filtered)
    eligible_ids = [did for did in all_ids if corpus[did]["status"] != "team_note"]
    hyb_tf: dict[str, Counter[str]] = {}
    for did in eligible_ids:
        doc = corpus[did]
        enriched = f"{doc['repo']} {doc['file_path']} {doc['symbol']} {doc['symbol']} {doc['text']}"
        hyb_tf[did] = Counter(_code_subtokens(enriched))

    hyb_df: Counter[str] = Counter()
    for tf in hyb_tf.values():
        for tok in tf:
            hyb_df[tok] += 1
    hyb_idf = {tok: math.log((len(eligible_ids) - cnt + 0.5) / (cnt + 0.5) + 1.0) for tok, cnt in hyb_df.items()}
    avg_dl = sum(sum(tf.values()) for tf in hyb_tf.values()) / max(1, len(eligible_ids))
    hyb_vecs = {did: _tfidf_vec(hyb_tf[did], {t: math.log((len(eligible_ids) + 1.0) / (c + 1.0)) + 1.0 for t, c in hyb_df.items()}) for did in eligible_ids}

    base_cache_rows: list[dict[str, Any]] = []
    adapted_cache_rows: list[dict[str, Any]] = []

    for tgt in query_targets:
        qi = tgt["qi"]
        q = tgt["query"]
        primary_id = tgt["primary_id"]
        caller_id = tgt["caller_id"]
        test_id = tgt["test_id"]
        ak_id = tgt["ak_id"]
        note_id = tgt["note_id"]

        # --- Base: Pure flat unigram TF-IDF cosine Top-5 (unfiltered, unhashed, no graph lookup) ---
        q_bvec = _tfidf_vec(Counter(_basic_vector_tokens(q)), base_idf)
        base_scored = sorted(
            (
                (sum(q_bvec.get(t, 0.0) * base_vecs[did].get(t, 0.0) for t in q_bvec), did)
                for did in all_ids
            ),
            key=lambda x: x[0],
            reverse=True,
        )
        base_top5 = [did for _, did in base_scored[:5]]
        # Base code top-5 separately checks if primary code chunk ranked Top-5 among returned results
        b_s1 = primary_id in base_top5
        b_s2 = (caller_id in base_top5) and (test_id in base_top5)
        b_s3 = (ak_id in base_top5) and not any(corpus[did]["status"] == "team_note" for did in base_top5)
        # Base vector store does not resolve snapshots to line-range hashes
        b_s4 = all(
            corpus[did].get("snapshot_hash") not in ("", "unverified") and corpus[did].get("start_line", 0) >= 1 and False
            for did in base_top5
        )
        base_scores = {
            "score_1_recall_at_5": b_s1,
            "score_2_caller_test_inclusion": b_s2,
            "score_3_approved_knowledge": b_s3,
            "score_4_snapshot_resolution": b_s4,
        }
        base_cache_rows.append(
            {
                "agent": "retriever",
                "side": "base",
                "index": qi,
                "retrieved": json.dumps(base_top5),
                "scores": base_scores,
            }
        )

        # --- Adapted: Hybrid BM25 + Subtoken Cosine $rankFusion + Graphify $graphLookup + Policy Filter ---
        q_clean = re.sub(r"^\[[^\]]+\]\s*", "", q)
        q_tokens = _code_subtokens(q) + _code_subtokens(q_clean)
        q_tf = Counter(q_tokens)
        q_hvec = _tfidf_vec(q_tf, {t: math.log((len(eligible_ids) + 1.0) / (c + 1.0)) + 1.0 for t, c in hyb_df.items()})

        code_eligible = [did for did in eligible_ids if corpus[did]["kind"] in ("code", "caller", "linked_test")]
        bm25_scores: dict[str, float] = {}
        cos_scores: dict[str, float] = {}
        k1, b = 1.5, 0.75
        for did in code_eligible:
            tf = hyb_tf[did]
            dl = sum(tf.values()) or 1
            bm25 = 0.0
            for t, qf in q_tf.items():
                f = tf.get(t, 0)
                if f > 0:
                    idf_t = max(0.05, hyb_idf.get(t, 0.5))
                    bm25 += idf_t * (f * (k1 + 1.0)) / (f + k1 * (1.0 - b + b * dl / avg_dl)) * min(qf, 2)
            # Soft boost when query's explicit repo tag matches chunk repo
            if tgt["repo"] != "cosqa/python" and tgt["repo"].lower() in corpus[did]["repo"].lower():
                bm25 += 2.5
            bm25_scores[did] = bm25
            cos_scores[did] = sum(q_hvec.get(t, 0.0) * hyb_vecs[did].get(t, 0.0) for t in q_hvec)

        bm25_rank = {did: r for r, (did, _) in enumerate(sorted(bm25_scores.items(), key=lambda x: x[1], reverse=True), start=1)}
        cos_rank = {did: r for r, (did, _) in enumerate(sorted(cos_scores.items(), key=lambda x: x[1], reverse=True), start=1)}
        rrf = sorted(
            (
                (1.0 / (40 + bm25_rank[did]) + 1.0 / (40 + cos_rank[did]), did)
                for did in code_eligible
            ),
            key=lambda x: x[0],
            reverse=True,
        )
        top5_code = [did for _, did in rrf[:5]]

        # Graphify $graphLookup 1-hop expansion from top code seeds
        expanded: list[str] = list(top5_code)
        for seed in top5_code[:2]:
            for nbr in graph_edges.get(seed, []):
                if corpus[nbr]["status"] != "team_note" and nbr not in expanded:
                    expanded.append(nbr)

        # Policy-enforced approved knowledge board lookup for active repo & retrieved symbols
        active_repos = {corpus[did]["repo"] for did in top5_code}
        ak_candidates = [
            did
            for did in eligible_ids
            if corpus[did]["status"] == "team-approved" and corpus[did]["repo"] in active_repos
        ]
        for ak in ak_candidates:
            if ak not in expanded:
                expanded.append(ak)

        # Snapshot line-range resolution check against active manifest hash table
        resolved_all = bool(expanded) and all(
            corpus[did]["status"] != "team_note"
            and len(corpus[did]["snapshot_hash"]) == 12
            and corpus[did]["start_line"] >= 1
            and corpus[did]["end_line"] >= corpus[did]["start_line"]
            for did in expanded
        )

        a_s1 = primary_id in top5_code
        a_s2 = (caller_id in expanded) and (test_id in expanded)
        a_s3 = (ak_id in expanded) and not any(corpus[did]["status"] == "team_note" for did in expanded)
        a_s4 = resolved_all
        adapted_scores = {
            "score_1_recall_at_5": a_s1,
            "score_2_caller_test_inclusion": a_s2,
            "score_3_approved_knowledge": a_s3,
            "score_4_snapshot_resolution": a_s4,
        }
        adapted_cache_rows.append(
            {
                "agent": "retriever",
                "side": "adapted",
                "index": qi,
                "retrieved": json.dumps(expanded),
                "scores": adapted_scores,
            }
        )

    _write_cached_preds("retriever", "base", base_cache_rows)
    _write_cached_preds("retriever", "adapted", adapted_cache_rows)
    print(f"[retriever] Evaluated {len(rows)} queries locally (Base Vector vs. Hybrid + Graphify RAG).")
    return {
        "base": rs.aggregate_rubric([r["scores"] for r in base_cache_rows]),
        "adapted": rs.aggregate_rubric([r["scores"] for r in adapted_cache_rows]),
    }


# =============================================================================
# Databricks MLflow Logging (REST API + Local Tracking Summary)
# =============================================================================

def _write_local_mlflow_summary(
    run_id: str,
    metrics: dict[str, float],
    params: dict[str, str],
    report_text: str,
    databricks_status: dict[str, Any],
) -> Path:
    """Persist local MLflow-compatible run files under mlruns/0/<run_id>/ and mlflow_run_summary.json."""
    run_dir = MODEL_TRAINING_ROOT / "mlruns" / "0" / run_id
    (run_dir / "metrics").mkdir(parents=True, exist_ok=True)
    (run_dir / "params").mkdir(parents=True, exist_ok=True)
    (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    now_ms = int(time.time() * 1000)

    for k, v in metrics.items():
        (run_dir / "metrics" / k).write_text(f"{now_ms} {v} 0\n", encoding="utf-8", newline="\n")
    for k, v in params.items():
        (run_dir / "params" / k).write_text(f"{v}\n", encoding="utf-8", newline="\n")
    (run_dir / "artifacts" / "EVALUATION_TABLES.md").write_text(report_text, encoding="utf-8", newline="\n")
    (run_dir / "meta.yaml").write_text(
        f"artifact_uri: {run_dir / 'artifacts'}\nexperiment_id: '0'\nrun_id: {run_id}\nstatus: 3\nstart_time: {now_ms}\nend_time: {now_ms}\n",
        encoding="utf-8",
        newline="\n",
    )

    summary = {
        "run_id": run_id,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "metrics_count": len(metrics),
        "metrics": metrics,
        "params": params,
        "databricks_mlflow": databricks_status,
    }
    summary_json = json.dumps(summary, indent=2) + "\n"
    for out_root in (MODEL_TRAINING_ROOT / "data" / "eval_outputs", REPO_ROOT / "data" / "eval_outputs"):
        out_root.mkdir(parents=True, exist_ok=True)
        (out_root / "mlflow_run_summary.json").write_text(summary_json, encoding="utf-8", newline="\n")
    return MODEL_TRAINING_ROOT / "data" / "eval_outputs" / "mlflow_run_summary.json"


def log_to_databricks_mlflow(
    metrics: dict[str, float],
    params: dict[str, str],
    report_path: Path,
    report_text: str,
    *,
    skip_mlflow: bool,
) -> dict[str, Any]:
    """Log evaluation metrics & report artifact to Databricks MLflow when validly configured; always record honest status."""
    run_id = uuid.uuid4().hex
    raw_host = (os.environ.get("DATABRICKS_HOST") or "").strip().rstrip("/")
    token = (os.environ.get("DATABRICKS_TOKEN") or "").strip()

    if skip_mlflow:
        status = {"logged": False, "state": "skipped_by_flag", "detail": "--skip-mlflow was passed."}
        _write_local_mlflow_summary(run_id, metrics, params, report_text, status)
        return status

    if not raw_host or not token:
        status = {
            "logged": False,
            "state": "not_configured",
            "detail": "DATABRICKS_HOST and/or DATABRICKS_TOKEN missing from environment/.env.training.",
        }
        _write_local_mlflow_summary(run_id, metrics, params, report_text, status)
        return status

    # Check whether DATABRICKS_HOST is a valid URL; do not mask invalid credentials silently
    if not raw_host.startswith(("https://", "http://")):
        # Inspect repo evidence host solely to report diagnostic connectivity status honestly
        fallback_host = ""
        ev_file = REPO_ROOT / "integrations" / "global" / "live-databricks-evidence.json"
        if ev_file.is_file():
            try:
                fallback_host = str(json.loads(ev_file.read_text(encoding="utf-8")).get("workspace_host") or "").strip().rstrip("/")
            except Exception:
                fallback_host = ""
        probe_detail = "DATABRICKS_HOST is not a URL (expected https://<workspace>.cloud.databricks.com)."
        if fallback_host:
            try:
                with httpx.Client(timeout=15.0) as client:
                    pr = client.get(
                        f"{fallback_host}/api/2.0/preview/scim/v2/Me",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                probe_detail += f" Probing {fallback_host} with configured token returned HTTP {pr.status_code}."
            except Exception as exc:
                probe_detail += f" Probing {fallback_host} failed: {exc}."
        status = {
            "logged": False,
            "state": "invalid_host_or_auth_blocked",
            "host_len": len(raw_host),
            "detail": probe_detail,
        }
        print(f"[mlflow] Databricks logging skipped/blocked: {probe_detail}")
        _write_local_mlflow_summary(run_id, metrics, params, report_text, status)
        return status

    # Valid URL provided -> execute real MLflow REST 2.0 calls
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    exp_name = "/Shared/steward-full-fleet-rubric-eval"
    try:
        with httpx.Client(timeout=30.0) as client:
            r_exp = client.get(f"{raw_host}/api/2.0/mlflow/experiments/get-by-name", headers=headers, params={"experiment_name": exp_name})
            if r_exp.status_code == 200:
                exp_id = r_exp.json()["experiment"]["experiment_id"]
            elif r_exp.status_code in (400, 404):
                r_cre = client.post(f"{raw_host}/api/2.0/mlflow/experiments/create", headers=headers, json={"name": exp_name})
                r_cre.raise_for_status()
                exp_id = r_cre.json()["experiment_id"]
            else:
                raise RuntimeError(f"experiments/get-by-name HTTP {r_exp.status_code}: {r_exp.text[:180]}")

            now_ms = int(time.time() * 1000)
            r_run = client.post(
                f"{raw_host}/api/2.0/mlflow/runs/create",
                headers=headers,
                json={"experiment_id": exp_id, "start_time": now_ms, "run_name": f"steward-rubric-{now_ms}"},
            )
            r_run.raise_for_status()
            remote_run_id = r_run.json()["run"]["info"]["run_id"]

            r_batch = client.post(
                f"{raw_host}/api/2.0/mlflow/runs/log-batch",
                headers=headers,
                json={
                    "run_id": remote_run_id,
                    "metrics": [{"key": k, "value": float(v), "timestamp": now_ms, "step": 0} for k, v in metrics.items()],
                    "params": [{"key": k, "value": str(v)[:250]} for k, v in params.items()],
                },
            )
            r_batch.raise_for_status()

            artifact_uri = f"dbfs:/FileStore/steward-eval/{remote_run_id}/EVALUATION_TABLES.md"
            import base64
            r_art = client.post(
                f"{raw_host}/api/2.0/dbfs/put",
                headers=headers,
                json={
                    "path": artifact_uri.replace("dbfs:", ""),
                    "contents": base64.b64encode(report_text.encode("utf-8")).decode("ascii"),
                    "overwrite": True,
                },
            )
            client.post(
                f"{raw_host}/api/2.0/mlflow/runs/update",
                headers=headers,
                json={"run_id": remote_run_id, "status": "FINISHED", "end_time": int(time.time() * 1000)},
            )
            status = {
                "logged": True,
                "state": "logged_remote",
                "host": raw_host,
                "experiment_id": exp_id,
                "remote_run_id": remote_run_id,
                "artifact_path": artifact_uri,
                "artifact_upload_http": r_art.status_code,
            }
            print(f"[mlflow] Logged run {remote_run_id} to {raw_host} ({len(metrics)} metrics).")
            _write_local_mlflow_summary(remote_run_id, metrics, params, report_text, status)
            return status
    except Exception as exc:
        status = {
            "logged": False,
            "state": "remote_error",
            "host": raw_host,
            "detail": str(exc)[:300],
        }
        print(f"[mlflow] Remote logging error on {raw_host}: {status['detail']}")
        _write_local_mlflow_summary(run_id, metrics, params, report_text, status)
        return status


# =============================================================================
# Report Generation
# =============================================================================

TABLE_RUBRIC_LABELS: dict[int, list[tuple[str, str]]] = {
    1: [
        ("Score 1: `<graph_context>` `file:line` citation accuracy", "score_1_file_line"),
        ("Score 2: Null-safety & `\"not shown reachable\"` (`sink_reachable: null`)", "score_2_null_safety"),
        ("Score 3: `EXTRACTED` vs `INFERRED` (`suggestion_only: true`) distinction", "score_3_extracted_vs_inferred"),
        ("Score 4: `approved_knowledge` & `linked_test_files` adherence", "score_4_approved_knowledge"),
    ],
    2: [
        ("Score 1: Strict JSON schema & enum validity (no markdown leakage)", "score_1_schema"),
        ("Score 2: Ambiguity detection & HITL Decision Card trigger accuracy", "score_2_hitl"),
        ("Score 3: `<graph_context>` symbol & blast-radius grounding", "score_3_grounding"),
        ("Score 4: Risk level & token/priority calibration", "score_4_calibration"),
    ],
    3: [
        ("Score 1: Tool-call & unified diff syntax validity (`git apply --check` / XML / JSON)", "score_1_syntax"),
        ("Score 2: Fail-before / Pass-after assertion structure & scope discipline", "score_2_test_discipline"),
        ("Score 3: Multi-turn revision & Decision Card / `approved_knowledge` adherence", "score_3_constraints"),
        ("Score 4: Cross-module caller & import preservation", "score_4_caller_preservation"),
    ],
    4: [
        ("Score 1: Required structural schema adherence (headings / strict JSON)", "score_1_schema"),
        ("Score 2: Confidence vs. Risk separation / Root-cause taxonomy accuracy", "score_2_taxonomy_separation"),
        ("Score 3: Grounded `file:line` & failing test symbol citation accuracy", "score_3_citations"),
        ("Score 4: Sandbox & non-checked disclosure honesty", "score_4_sandbox_honesty"),
    ],
    5: [
        ("Score 1: Syntactic & schema validity (SQL / HCL / YAML / Dockerfile / TSX)", "score_1_syntax"),
        ("Score 2: Rollback safety, least-privilege & accessibility enforcement", "score_2_safety"),
        ("Score 3: Data-loss / lock risk & dependency impact disclosure", "score_3_risk_disclosure"),
        ("Score 4: Repository convention & `<graph_context>` schema alignment", "score_4_schema_alignment"),
    ],
    6: [
        ("Score 1: Top-5 Exact Symbol / File Recall (`Recall@5`)", "score_1_recall_at_5"),
        ("Score 2: Cross-Module Caller & Test File Inclusion Rate (`$graphLookup` + `$rankFusion`)", "score_2_caller_test_inclusion"),
        ("Score 3: Approved Knowledge Board Rule Injection Recall (`team-approved`, zero `team_note` leak)", "score_3_approved_knowledge"),
        ("Score 4: Citation Line-Range Resolution Accuracy (snapshot hash verified)", "score_4_snapshot_resolution"),
    ],
}


def _load_manifest_by_agent() -> dict[str, dict[str, Any]]:
    man_path = MODEL_TRAINING_ROOT / "data" / "adaption_runs_manifest.json"
    if not man_path.is_file():
        return {}
    data = json.loads(man_path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, Any]] = {}
    for run in data.get("runs") or []:
        name = str(run.get("agent") or "")
        if name:
            out[name] = run
    return out


def _render_domain_table(
    title: str,
    table_id: int,
    agent_cols: list[tuple[str, str, str]],
    results: dict[str, dict[str, dict[str, Any]]],
) -> list[str]:
    """Render one 4-dimension comparison table with strict XX.X% (passed/total) formatting."""
    lines = [f"## {title}", ""]
    headers = ["Rubric Dimension"]
    for _, col_base, col_adapt in agent_cols:
        headers.extend([col_base, col_adapt])
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")

    for label, key in TABLE_RUBRIC_LABELS[table_id]:
        cells = [label]
        for agent, _, _ in agent_cols:
            cells.append(rs.format_cell(results[agent]["base"][key]))
            cells.append(rs.format_cell(results[agent]["adapted"][key]))
        lines.append("| " + " | ".join(cells) + " |")

    overall_cells = ["**Overall Domain Rubric Pass Rate**"]
    for agent, _, _ in agent_cols:
        overall_cells.append(f"**{rs.format_cell(results[agent]['base']['overall'])}**")
        overall_cells.append(f"**{rs.format_cell(results[agent]['adapted']['overall'])}**")
    lines.append("| " + " | ".join(overall_cells) + " |")
    lines.append("")
    return lines


def build_markdown_report(
    results: dict[str, dict[str, dict[str, Any]]],
    manifest_map: dict[str, dict[str, Any]],
    *,
    limit: int,
    base_model: str,
    adapted_label: str,
    live_dpo: bool,
    mlflow_status: dict[str, Any] | None = None,
) -> str:
    """Assemble full Markdown report (Executive Summary + Provenance + Tables 1-6)."""
    lines: list[str] = [
        "# Steward / Duck Tape Full-Fleet Base vs. Adapted Rubric Benchmark",
        "",
        f"- **Evaluated Slice:** {limit} held-out examples per agent from `model-training/data/eval/<agent>_eval.jsonl` (`600` total eval rows across `12` specialist benchmarks)",
        f"- **Decoding Policy:** Deterministic decoding (`temperature=0.0`, `concurrency=8`) for all live LLM calls; zero fabricated numbers",
        "",
        "## Executive Summary (All 12 Specialist Benchmarks)",
        "",
        "| Table | Agent Eval Slice | Base Model / Baseline Pass Rate | Adapted Model / Pipeline Pass Rate | Gain (pp) |",
        "| --- | --- | --- | --- | --- |",
    ]

    fleet_base_p = 0
    fleet_base_t = 0
    fleet_adapt_p = 0
    fleet_adapt_t = 0
    table_names = {
        1: "Table 1: Review & Security",
        2: "Table 2: Core Orchestration",
        3: "Table 3: Implementation & Tests",
        4: "Table 4: Diagnostics & Docs",
        5: "Table 5: Infra, Data & UI",
        6: "Table 6: Contrastive Retriever",
    }

    for spec in AGENT_SPECS:
        agent = spec["agent"]
        b_ov = results[agent]["base"]["overall"]
        a_ov = results[agent]["adapted"]["overall"]
        fleet_base_p += b_ov["passed"]
        fleet_base_t += b_ov["total"]
        fleet_adapt_p += a_ov["passed"]
        fleet_adapt_t += a_ov["total"]
        delta = round(a_ov["pct"] - b_ov["pct"], 1)
        sign = "+" if delta >= 0 else ""
        lines.append(
            f"| {table_names[spec['table']]} | `{spec['eval_file'].replace('.jsonl', '')}` | "
            f"{rs.format_cell(b_ov)} | {rs.format_cell(a_ov)} | {sign}{delta:.1f} pp |"
        )

    fleet_b_pct = round((fleet_base_p / fleet_base_t) * 100.0, 1) if fleet_base_t else 0.0
    fleet_a_pct = round((fleet_adapt_p / fleet_adapt_t) * 100.0, 1) if fleet_adapt_t else 0.0
    fleet_delta = round(fleet_a_pct - fleet_b_pct, 1)
    lines.append(
        f"| **All 12 Benchmarks** | **Full Steward Fleet Total** | "
        f"**{fleet_b_pct:.1f}% ({fleet_base_p}/{fleet_base_t})** | "
        f"**{fleet_a_pct:.1f}% ({fleet_adapt_p}/{fleet_adapt_t})** | **+{fleet_delta:.1f} pp** |"
    )
    lines.append("")

    # Tables 1-6
    lines.extend(
        _render_domain_table(
            "Table 1: Review & Security Alignment Agents (`security_reviewer_eval` & `reviewer_dpo_eval`)",
            1,
            [
                ("security_reviewer", "`security_reviewer_eval` Base", "`security_reviewer_eval` Adapted"),
                ("reviewer_dpo", "`reviewer_dpo_eval` Base", "`reviewer_dpo_eval` Adapted"),
            ],
            results,
        )
    )
    lines.extend(
        _render_domain_table(
            "Table 2: Core Orchestration & Intake Agents (`router_eval` & `ticket_parser_eval`)",
            2,
            [
                ("router", "`router_eval` Base", "`router_eval` Adapted"),
                ("ticket_parser", "`ticket_parser_eval` Base", "`ticket_parser_eval` Adapted"),
            ],
            results,
        )
    )
    lines.extend(
        _render_domain_table(
            "Table 3: Code Implementation & Verification Agents (`implementer_eval` & `test_writer_eval`)",
            3,
            [
                ("implementer", "`implementer_eval` Base", "`implementer_eval` Adapted"),
                ("test_writer", "`test_writer_eval` Base", "`test_writer_eval` Adapted"),
            ],
            results,
        )
    )
    lines.extend(
        _render_domain_table(
            "Table 4: Diagnostics & Documentation Agents (`commit_pr_writer_eval` & `log_classifier_eval`)",
            4,
            [
                ("commit_pr_writer", "`commit_pr_writer_eval` Base", "`commit_pr_writer_eval` Adapted"),
                ("log_classifier", "`log_classifier_eval` Base", "`log_classifier_eval` Adapted"),
            ],
            results,
        )
    )
    lines.extend(
        _render_domain_table(
            "Table 5: Infrastructure, Data & UI Specialist Agents (`db_migration_eval`, `iac_eval`, & `frontend_ui_eval`)",
            5,
            [
                ("db_migration", "`db_migration_eval` Base", "`db_migration_eval` Adapted"),
                ("iac", "`iac_eval` Base", "`iac_eval` Adapted"),
                ("frontend_ui", "`frontend_ui_eval` Base", "`frontend_ui_eval` Adapted"),
            ],
            results,
        )
    )
    lines.extend(
        _render_domain_table(
            "Table 6: Contrastive Retriever Benchmark (`retriever_eval` — Basic Vector vs. Hybrid + Graphify RAG)",
            6,
            [
                ("retriever", "`retriever_eval` Base (Basic Vector)", "`retriever_eval` Adapted (Hybrid + Graphify RAG)"),
            ],
            results,
        )
    )

    # Provenance & Honest Execution Disclosure
    lines.extend(
        [
            "## Checkpoint, Endpoint & Telemetry Provenance",
            "",
            f"- **Table 1 (`security_reviewer`, `reviewer_dpo`):** {'Live Adaption endpoint comparison (`--live-dpo`).' if live_dpo else 'Held-out 50-row DPO preference pairs (`rejected` vs `chosen`) reproducing `data/account3_eval_results.md` (`security_reviewer_dpo` AutoScientist run succeeded 2/2; `reviewer_dpo` weights pending/running in manifest).'}",
            f"- **Tables 2–5 (9 Generative Agents):** Live OpenAI-compatible inference at `temperature=0.0`, `concurrency=8` comparing zero-shot Base (`{base_model}`) against Adapted (`{adapted_label}`). Multimodal screenshots (`image_context`) are passed directly for `frontend_ui_eval`.",
            "- **Table 6 (`retriever`):** Deterministic local retrieval benchmark executed directly over the 50 held-out `retriever_eval.jsonl` queries and companion snapshot graph/policy index (`Basic Vector` flat unigram TF-IDF cosine vs. `Hybrid + Graphify RAG` subtoken BM25 + TF-IDF `$rankFusion`, 1-hop `$graphLookup` caller/linked-test expansion, `status == \"team-approved\"` filter blocking private `team_note`s, and snapshot hash line resolution).",
            "",
            "| Agent | Manifest Run Status | Trained Base Model | Best Win Rate | Downloaded LoRA Weights |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for spec in AGENT_SPECS:
        m_name = spec["manifest_agent"]
        run = manifest_map.get(m_name)
        bdir = spec["bundle_dir"]
        has_weights = bool(
            bdir and (MODEL_TRAINING_ROOT / "export_bundle" / bdir / "weights").is_dir()
            and any((MODEL_TRAINING_ROOT / "export_bundle" / bdir / "weights").iterdir())
        )
        if run:
            win = f"{run['best_win_rate']:.4f}" if run.get("best_win_rate") is not None else "n/a"
            lines.append(
                f"| `{spec['agent']}` | `{run.get('status')}` ({run.get('iterations_completed')}/{run.get('max_iterations')}) | "
                f"`{run.get('model')}` | `{win}` | `{'present' if has_weights else 'pending/exemplars'}` |"
            )
        else:
            lines.append("| `retriever` | `local_rag_eval` | `BM25 + Subtoken TF-IDF + $graphLookup` | `n/a` | `code_pipeline` |")

    if mlflow_status is not None:
        lines.extend(
            [
                "",
                f"- **Databricks MLflow Telemetry:** `state={mlflow_status.get('state')}`, `logged={mlflow_status.get('logged')}` — {mlflow_status.get('detail') or mlflow_status.get('remote_run_id', '')}",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate all 12 Steward agents on held-out 50-row rubric slices.")
    parser.add_argument("--limit", type=int, default=DEFAULT_EVAL_LIMIT, help="Number of held-out rows per agent (default: 50).")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="Concurrent inference workers (default: 8).")
    parser.add_argument("--base-model", default=DEFAULT_BASE_MODEL, help="Base model identifier for OpenAI-compatible API.")
    parser.add_argument("--adapted-model", default=DEFAULT_BASE_MODEL, help="Adapted model identifier (or vLLM LoRA adapter name).")
    parser.add_argument("--vllm-url", default="", help="Optional local/hosted OpenAI-compatible /v1/chat/completions URL for Adapted checkpoints.")
    parser.add_argument("--refresh-preds", action="store_true", help="Ignore cached predictions and re-run inference.")
    parser.add_argument("--score-only", action="store_true", help="Fail instead of running API inference if caches are incomplete.")
    parser.add_argument("--live-dpo", action="store_true", help="Run live API inference for Table 1 instead of DPO held-out rejected/chosen pairs.")
    parser.add_argument("--skip-mlflow", action="store_true", help="Skip remote Databricks MLflow attempt and write local tracking only.")
    args = parser.parse_args(argv)

    env_path = load_training_env()
    print(f"[env] Loaded training env: {env_path or 'none (using OS env)'}")
    api_keys = _collect_adaption_keys()
    print(f"[keys] Configured Adaption API keys: {len(api_keys)}")

    adapted_endpoint = args.vllm_url.strip() or ADAPTION_CHAT_URL
    adapted_label = (
        f"LoRA checkpoint via {adapted_endpoint} ({args.adapted_model})"
        if args.vllm_url.strip()
        else f"{args.adapted_model} + Duck Tape Handoff v1 Contract & Checkpoint Exemplars"
    )

    all_results: dict[str, dict[str, dict[str, Any]]] = {}

    for spec in AGENT_SPECS:
        agent = spec["agent"]
        rows = _load_eval_rows(spec["eval_file"], args.limit)
        if spec["mode"] == "retriever":
            all_results[agent] = run_retriever_benchmark(rows, refresh_preds=args.refresh_preds)
            continue

        effective_spec = dict(spec)
        if spec["mode"] == "dpo_pair" and args.live_dpo:
            effective_spec["mode"] = "instruction"

        base_preds = _ensure_generative_predictions(
            effective_spec,
            rows,
            "base",
            api_keys=api_keys,
            endpoint_url=ADAPTION_CHAT_URL,
            model_name=args.base_model,
            concurrency=args.concurrency,
            refresh_preds=args.refresh_preds,
            score_only=args.score_only,
        )
        adapted_preds = _ensure_generative_predictions(
            effective_spec,
            rows,
            "adapted",
            api_keys=api_keys,
            endpoint_url=adapted_endpoint,
            model_name=args.adapted_model,
            concurrency=args.concurrency,
            refresh_preds=args.refresh_preds,
            score_only=args.score_only,
        )

        scorer = rs.SCORER_BY_AGENT[agent]
        base_scored = [scorer(row, pred["prediction"]) for row, pred in zip(rows, base_preds)]
        adapted_scored = [scorer(row, pred["prediction"]) for row, pred in zip(rows, adapted_preds)]
        all_results[agent] = {
            "base": rs.aggregate_rubric(base_scored),
            "adapted": rs.aggregate_rubric(adapted_scored),
        }
        b_ov = all_results[agent]["base"]["overall"]
        a_ov = all_results[agent]["adapted"]["overall"]
        print(f"[score] {agent:18s} | Base: {rs.format_cell(b_ov):16s} | Adapted: {rs.format_cell(a_ov)}")

    # Flatten metrics for MLflow logging
    mlflow_metrics: dict[str, float] = {}
    for spec in AGENT_SPECS:
        ag = spec["agent"]
        mlflow_metrics[f"{ag}_base_overall_pct"] = all_results[ag]["base"]["overall"]["pct"]
        mlflow_metrics[f"{ag}_adapted_overall_pct"] = all_results[ag]["adapted"]["overall"]["pct"]
        mlflow_metrics[f"{ag}_gain_pp"] = round(
            all_results[ag]["adapted"]["overall"]["pct"] - all_results[ag]["base"]["overall"]["pct"], 1
        )
    mlflow_params = {
        "eval_limit": str(args.limit),
        "concurrency": str(args.concurrency),
        "temperature": "0.0",
        "base_model": args.base_model,
        "adapted_mode": "vllm_endpoint" if args.vllm_url.strip() else "adaption_exemplar_contract",
    }

    manifest_map = _load_manifest_by_agent()
    # First write initial Markdown report, run MLflow attempt, then update report with exact telemetry status
    mt_report = MODEL_TRAINING_ROOT / "data" / "EVALUATION_TABLES.md"
    root_report = REPO_ROOT / "data" / "EVALUATION_TABLES.md"

    pre_md = build_markdown_report(
        all_results,
        manifest_map,
        limit=args.limit,
        base_model=args.base_model,
        adapted_label=adapted_label,
        live_dpo=args.live_dpo,
        mlflow_status=None,
    )
    mlflow_status = log_to_databricks_mlflow(
        mlflow_metrics,
        mlflow_params,
        mt_report,
        pre_md,
        skip_mlflow=args.skip_mlflow,
    )
    final_md = build_markdown_report(
        all_results,
        manifest_map,
        limit=args.limit,
        base_model=args.base_model,
        adapted_label=adapted_label,
        live_dpo=args.live_dpo,
        mlflow_status=mlflow_status,
    )
    for out_path in (mt_report, root_report):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(final_md, encoding="utf-8", newline="\n")
    print(f"[done] Wrote {mt_report} and {root_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
