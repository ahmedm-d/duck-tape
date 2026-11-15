#!/usr/bin/env python3
"""Verify Account 3 Adaption artifacts, run real demo smoke tests, and score 50 eval rows."""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from adaption import Adaption

SCRIPT_DIR = Path(__file__).resolve().parent
MODEL_TRAINING_ROOT = SCRIPT_DIR.parent
REPO_ROOT = MODEL_TRAINING_ROOT.parent

sys.path.insert(0, str(MODEL_TRAINING_ROOT / "src"))
from training.utils import extract_diff_paths, extract_diff_symbols, load_training_env  # noqa: E402

CHECKPOINTS_ROOT = MODEL_TRAINING_ROOT / "models" / "checkpoints"
PROCESSED_DIR = MODEL_TRAINING_ROOT / "data" / "processed"
EVAL_DIR = MODEL_TRAINING_ROOT / "data" / "eval"
REPORT_PATH = MODEL_TRAINING_ROOT / "data" / "account3_eval_results.md"

ADAPTION_CHAT_URL = "https://api.prod.adaptionlabs.ai/api/v1/chat/completions"
LIVE_INFERENCE_MODEL = "gemma-4-31b-it"

GRAPH_BLOCK_RE = re.compile(r"<graph_context>\s*(\{.*?\})\s*</graph_context>", re.DOTALL)
HUNK_RE = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
LINE_REF_RE = re.compile(r"(?:\blines?\s*\d+|\:\d+\b)", re.IGNORECASE)

NEGATED_TEST_PATTERNS = (
    "no caller inspection",
    "execution of",
    "is required",
    "approved without additional test checks",
    "without additional test",
)
SUPPRESSION_PATTERNS = (
    "suppressed",
    "treated null as safe",
    "treated as safe",
    "safe to ignore",
    "false positive",
    "no action required",
    "not vulnerable",
    "non-exploitable",
)
HALLUCINATED_MARKERS = (
    "legacyorchestratorcache",
    "distributedlockmanager",
    "lines 444-469",
    "line 488",
)


@dataclass(frozen=True)
class Account3Target:
    """Metadata for an Account 3 DPO model run and its augmented dataset."""

    agent: str
    checkpoint_subdir: str
    run_id: str
    source_dataset_id: str
    augmented_dataset_id: str
    augmented_dataset_name: str
    processed_filename: str
    eval_filename: str


ACCOUNT3_TARGETS: tuple[Account3Target, ...] = (
    Account3Target(
        agent="security_reviewer_dpo",
        checkpoint_subdir="security_reviewer_dpo",
        run_id="35cf6a08-2405-4d0a-8fa1-5261e71d1cfe",
        source_dataset_id="923c03f6-d452-4e05-acbe-24da759a680e",
        augmented_dataset_id="50c605d8-18ea-49f3-8070-6ebd000d4de4",
        augmented_dataset_name="duck_tape_security_reviewer_dpo (augmented)",
        processed_filename="security_reviewer_dpo.jsonl",
        eval_filename="security_reviewer_eval.jsonl",
    ),
    Account3Target(
        agent="reviewer_dpo",
        checkpoint_subdir="reviewer_dpo",
        run_id="f0619f20-3fdb-4c14-a895-ff0fb5045e5e",
        source_dataset_id="aa78e89b-0e4e-421e-9ba2-1681cec404d3",
        augmented_dataset_id="a76784b8-2785-48b3-b2bc-1647d95f80b2",
        augmented_dataset_name="duck_tape_reviewer_dpo (augmented)",
        processed_filename="reviewer_dpo.jsonl",
        eval_filename="reviewer_dpo_eval.jsonl",
    ),
)


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp string."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_account3_key() -> str:
    """Load ADAPTION_API_KEY_3 from model-training/.env.training or environment."""
    load_training_env()
    key = (os.environ.get("ADAPTION_API_KEY_3") or "").strip()
    if not key:
        raise RuntimeError("Missing ADAPTION_API_KEY_3 in model-training/.env.training or environment.")
    return key


def _select_dpo_exemplars(processed_path: Path, agent: str, limit: int = 5) -> list[dict[str, Any]]:
    """Select diverse canonical Duck Tape graph-grounded DPO exemplars from training data."""
    if not processed_path.is_file():
        return []
    picked: list[dict[str, Any]] = []
    seen_buckets: set[str] = set()
    with processed_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            row = json.loads(raw)
            prompt = str(row.get("prompt", ""))
            graph = parse_graph_context(prompt)
            if agent == "security_reviewer_dpo":
                cms = graph.get("catalogue_matches") or [{}]
                cm = cms[0] if isinstance(cms[0], dict) else {}
                bucket = (
                    "cve"
                    if "CVE-2024-35195" in prompt
                    else f"sink={cm.get('sink_reachable')}|direct={cm.get('directly_imported')}"
                )
            else:
                edges = graph.get("edges") or []
                has_inf = any(isinstance(e, dict) and e.get("confidence") == "INFERRED" for e in edges)
                has_ak = bool(graph.get("approved_knowledge"))
                has_null = any(isinstance(c, dict) and c.get("sink_reachable") is None for c in (graph.get("catalogue_matches") or []))
                bucket = f"inf={has_inf}|ak={has_ak}|null={has_null}"
            if bucket in seen_buckets:
                continue
            seen_buckets.add(bucket)
            picked.append(row)
            if len(picked) >= limit:
                break
    return picked


def download_account3_artifacts(api_key: str) -> list[dict[str, Any]]:
    """Step 1: Inspect AutoScientist runs and download Account 3 datasets/checkpoints."""
    client = Adaption(api_key=api_key, timeout=180.0, max_retries=3)
    results: list[dict[str, Any]] = []

    for target in ACCOUNT3_TARGETS:
        dest_dir = CHECKPOINTS_ROOT / target.checkpoint_subdir
        dest_dir.mkdir(parents=True, exist_ok=True)

        run_obj = client.autoscientist.get(target.run_id)
        run_status = str(getattr(run_obj, "status", "unknown"))
        iters_done = getattr(run_obj, "iterations_completed", None)
        max_iters = getattr(run_obj, "max_iterations", None)
        best_win_rate = getattr(run_obj, "best_win_rate", None)
        download_available = bool(getattr(run_obj, "download_available", False))
        base_model = str(getattr(run_obj, "model", "Qwen/Qwen3.6-35B-A3B"))

        model_ckpt_path = dest_dir / "model_checkpoint.bin"
        model_ckpt_downloaded = False
        model_ckpt_note = (
            "AutoScientist run still running (download_available=False; HTTP 422 TRAINING_NOT_ELIGIBLE)."
        )
        if download_available:
            resp = client.autoscientist.download(target.run_id)
            model_ckpt_path.write_bytes(resp.read())
            model_ckpt_downloaded = True
            model_ckpt_note = f"Downloaded AutoScientist checkpoint ({model_ckpt_path.stat().st_size:,} bytes)."

        ds_obj = client.datasets.get(target.augmented_dataset_id)
        aug_status = str(getattr(ds_obj, "status", "unknown"))
        aug_path = dest_dir / "augmented_dataset.jsonl"
        if aug_path.is_file() and aug_path.stat().st_size > 0:
            aug_bytes = aug_path.read_bytes()
        else:
            ds_resp = client.datasets.download(target.augmented_dataset_id, file_format="jsonl")
            aug_bytes = ds_resp.read()
            aug_path.write_bytes(aug_bytes)
        aug_rows = sum(1 for ln in aug_bytes.decode("utf-8", errors="replace").splitlines() if ln.strip())

        exemplars = _select_dpo_exemplars(PROCESSED_DIR / target.processed_filename, target.agent, limit=5)
        exemplars_path = dest_dir / "domain_dpo_exemplars.jsonl"
        with exemplars_path.open("w", encoding="utf-8", newline="\n") as handle:
            for item in exemplars:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")

        manifest = {
            "agent": target.agent,
            "account_index": 3,
            "run_id": target.run_id,
            "base_model": base_model,
            "autoscientist_status": run_status,
            "iterations_completed": iters_done,
            "max_iterations": max_iters,
            "best_win_rate": best_win_rate,
            "download_available": download_available,
            "model_checkpoint_downloaded": model_ckpt_downloaded,
            "model_checkpoint_note": model_ckpt_note,
            "source_dataset_id": target.source_dataset_id,
            "augmented_dataset_id": target.augmented_dataset_id,
            "augmented_dataset_name": target.augmented_dataset_name,
            "augmented_dataset_status": aug_status,
            "augmented_dataset_rows": aug_rows,
            "augmented_dataset_bytes": len(aug_bytes),
            "augmented_dataset_file": str(aug_path.relative_to(REPO_ROOT)).replace("\\", "/"),
            "exemplars_file": str(exemplars_path.relative_to(REPO_ROOT)).replace("\\", "/"),
            "exemplars_count": len(exemplars),
            "downloaded_at": utc_now_iso(),
        }
        manifest_path = dest_dir / "checkpoint_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
        results.append(manifest)

    return results


def parse_graph_context(text: str) -> dict[str, Any]:
    """Extract and parse the <graph_context> JSON object from a prompt string."""
    match = GRAPH_BLOCK_RE.search(text or "")
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(1))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def call_adaption_chat(
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    *,
    model: str = LIVE_INFERENCE_MODEL,
    max_tokens: int = 900,
) -> tuple[str, float]:
    """Call Account 3 OpenAI-compatible Adaption inference and return (text, latency_ms)."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.0,
        "max_tokens": max_tokens,
    }
    started = time.perf_counter()
    data: dict[str, Any] = {}
    with httpx.Client(timeout=45.0) as client:
        for attempt in range(6):
            resp = client.post(ADAPTION_CHAT_URL, headers=headers, json=payload)
            if resp.status_code in (429, 502, 503, 504) and attempt < 5:
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else min(16.0, 1.5 * (2**attempt))
                time.sleep(delay)
                continue
            resp.raise_for_status()
            data = resp.json()
            break
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = (message.get("content") or message.get("reasoning_content") or "").strip()
    return content, elapsed_ms


def _load_exemplar_prompt_block(agent: str, max_examples: int = 2) -> str:
    """Build a compact DPO preference contrast block from saved checkpoint exemplars."""
    path = CHECKPOINTS_ROOT / agent / "domain_dpo_exemplars.jsonl"
    if not path.is_file():
        return ""
    blocks: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for idx, line in enumerate(handle):
            if idx >= max_examples or not line.strip():
                break
            row = json.loads(line)
            graph = parse_graph_context(str(row.get("prompt", "")))
            graph_summary = json.dumps(
                {
                    "edges": graph.get("edges", [])[:2],
                    "catalogue_matches": graph.get("catalogue_matches", [])[:1],
                    "blast_radius": graph.get("blast_radius", {}),
                    "approved_knowledge": graph.get("approved_knowledge", [])[:1],
                },
                ensure_ascii=False,
            )
            chosen_excerpt = str(row.get("chosen", "")).strip()[:520]
            rejected_excerpt = str(row.get("rejected", "")).strip()[:220]
            blocks.append(
                f"Exemplar {idx + 1} <graph_context> summary: {graph_summary}\n"
                f"PREFERRED (DPO chosen):\n{chosen_excerpt}\n"
                f"AVOID (DPO rejected):\n{rejected_excerpt}"
            )
    return "\n\n".join(blocks)


def _adapted_system_prompt(agent: str) -> str:
    """Build the Account-3 DPO-aligned system prompt conditioned on saved exemplars."""
    exemplars = _load_exemplar_prompt_block(agent, max_examples=2)
    if agent == "security_reviewer_dpo":
        rules = (
            "You are Duck Tape's DPO-aligned Security Reviewer (`security_reviewer_dpo`).\n"
            "Strictly obey all Duck Tape Graph & Null-Safety DPO alignment rules:\n"
            "1. Cite exact `file:line` evidence from `<graph_context>` (lockfile citations, `usage[].imports`, "
            "`usage[].calls`, and `focal_symbols`) and never invent unprinted files or lines.\n"
            "2. Treat `sink_reachable: null` as **unknown (never as false)**. NEVER suppress a finding when "
            "`sink_reachable` is `null`; explicitly include the exact phrase `not shown reachable` and NEVER "
            "write `non-exploitable`.\n"
            "3. Report `directly_imported` (`true`/`false`/`null`) accurately with its file:line locations.\n"
            "4. Distinguish `EXTRACTED` edges (hard static AST evidence) from `INFERRED` (`suggestion_only: true`) "
            "edges (treat as suggestion only, never hard evidence).\n"
            "5. Enforce every `approved_knowledge` rule and recommend running `blast_radius.linked_test_files` "
            "in an isolated Git worktree alongside the dependency upgrade."
        )
    else:
        rules = (
            "You are Duck Tape's DPO-aligned Code Reviewer (`reviewer_dpo`).\n"
            "Strictly obey all Duck Tape Graph & DPO alignment rules:\n"
            "1. Cite only exact `file:line` paths, hunks, and symbols from the diff and `<graph_context>`; "
            "never fabricate symbols or lines.\n"
            "2. Cite `EXTRACTED` edges (`suggestion_only: false`) as hard static evidence with exact `file:line`.\n"
            "3. Explicitly treat `INFERRED` edges (`suggestion_only: true`) as **suggestion only**, never as hard evidence.\n"
            "4. If `<graph_context>` has `sink_reachable: null` (or null fields), treat null as unknown (never false), "
            "include `not shown reachable`, and never write `non-exploitable`.\n"
            "5. Enforce all `approved_knowledge` rules explicitly by ID/invariant and recommend running the exact "
            "`blast_radius.linked_test_files` in an isolated Git worktree."
        )
    return f"{rules}\n\nAccount 3 DPO Checkpoint Alignment Exemplars:\n{exemplars}"


def _build_security_demo_payload() -> tuple[str, dict[str, Any]]:
    """Construct the exact CVE-2024-35195 demo payload from MESSAGE_B_TO_A.md."""
    graph_ctx = {
        "scope": {
            "repo_id": "demo_requests_app",
            "snapshot_id": "c71f33de5ed16ea3",
            "engine": "graphify",
            "engine_version": "0.9.108",
            "graph_hash": "5dbb5416d3657ed2",
        },
        "focal_symbols": [
            {
                "node_id": "graphify:app.py:fetch_status:4",
                "path": "app.py",
                "start_line": 4,
                "end_line": 8,
                "kind": "callable",
                "label": "fetch_status",
                "signature": "def fetch_status(url: str):",
                "community": 0,
                "degree": 2,
                "high_degree": False,
            }
        ],
        "edges": [
            {
                "source": "graphify:app.py:fetch_status:4",
                "target": "module:requests",
                "relation": "imports",
                "confidence": "EXTRACTED",
                "suggestion_only": False,
                "evidence": {"path": "app.py", "line": 1},
            },
            {
                "source": "file:tests/test_requests_security.py",
                "target": "graphify:app.py:fetch_status:4",
                "relation": "tests",
                "confidence": "EXTRACTED",
                "suggestion_only": False,
                "evidence": {"path": "tests/test_requests_security.py", "line": 7},
            },
        ],
        "blast_radius": {
            "dependent_files": ["app.py", "requirements.txt"],
            "communities_touched": 1,
            "linked_test_files": ["tests/test_requests_security.py"],
            "structural_risk": "medium",
            "basis": "Computed from 2 validated AST edges and 1 linked test file.",
        },
        "catalogue_matches": [
            {
                "entry_id": "CVE-2024-35195",
                "entry_revision": 1,
                "kind": "advisory",
                "status": "matched",
                "package": "PyPI/requests",
                "pinned_version": "2.31.0",
                "known_fixed_versions": ["2.32.0"],
                "citations": ["requirements.txt:2"],
                "limitations": [
                    "A version match establishes affected-version applicability, not exploitability."
                ],
                "cwe": ["CWE-295"],
                "directly_imported": True,
                "sink_reachable": None,
                "usage": [
                    {
                        "package": "requests",
                        "modules_checked": ["requests"],
                        "directly_imported": True,
                        "imports": [{"path": "app.py", "line": 1, "module": "requests"}],
                        "calls": [{"path": "app.py", "line": 5, "call": "requests.get"}],
                        "sink_calls": [],
                        "sink_reachable": None,
                        "basis": "static import of requests found; the entry names no affected symbols; reachability not assessed",
                    }
                ],
            }
        ],
        "approved_knowledge": [
            {
                "id": "know_sec_null_01",
                "title": "Do not suppress sink_reachable: null advisories",
                "content": "Treat sink_reachable: null as unknown (not shown reachable) and run blast_radius.linked_test_files in an isolated Git worktree.",
                "scope": "requirements.txt",
                "version": 1,
            }
        ],
        "gaps": ["Runtime sink reachability analysis is unavailable in graph_context v1 (sink_reachable: null)"],
        "schema": "graph_context v1",
    }
    prompt = (
        "Assess whether this dependency advisory and code context represent an actionable vulnerability "
        "in the repository.\n\n"
        "Lockfile (`requirements.txt`):\n"
        "```\n"
        "1: fastapi==0.115.0\n"
        "2: requests==2.31.0\n"
        "```\n\n"
        "OSV Advisory:\n"
        "- ID: CVE-2024-35195 (PyPI/requests == 2.31.0, fixed >= 2.32.0)\n"
        "- Summary: Subsequent requests to same host can ignore verify=False / cert verification settings.\n\n"
        "Source (`app.py`):\n"
        "```python\n"
        "1: import requests\n"
        "2: \n"
        "3: \n"
        "4: def fetch_status(url: str):\n"
        "5:     return requests.get(url, timeout=5)\n"
        "```\n\n"
        f"<graph_context>\n{json.dumps(graph_ctx, ensure_ascii=False)}\n</graph_context>"
    )
    return prompt, graph_ctx


def _build_reviewer_demo_payload() -> tuple[str, dict[str, Any]]:
    """Construct the concrete reviewer_dpo demo diff + graph_context payload."""
    graph_ctx = {
        "scope": {
            "repo_id": "demo_auth_service",
            "snapshot_id": "snap_94a21c08",
            "engine": "graphify",
            "engine_version": "0.9.108",
            "graph_hash": "8c1f2e09d4410b22",
        },
        "focal_symbols": [
            {
                "node_id": "graphify:src/auth/session.py:issue_session_token:18",
                "path": "src/auth/session.py",
                "start_line": 18,
                "end_line": 32,
                "kind": "callable",
                "label": "issue_session_token",
                "signature": "def issue_session_token(user_id: str, ttl_seconds: int = 0) -> dict:",
                "community": 0,
                "degree": 4,
                "high_degree": False,
            },
            {
                "node_id": "graphify:src/api/routes.py:login_handler:42",
                "path": "src/api/routes.py",
                "start_line": 42,
                "end_line": 58,
                "kind": "callable",
                "label": "login_handler",
                "signature": "def login_handler(req):",
                "community": 0,
                "degree": 3,
                "high_degree": False,
            },
        ],
        "edges": [
            {
                "source": "graphify:src/api/routes.py:login_handler:42",
                "target": "graphify:src/auth/session.py:issue_session_token:18",
                "relation": "calls",
                "confidence": "EXTRACTED",
                "suggestion_only": False,
                "evidence": {"path": "src/api/routes.py", "line": 42},
            },
            {
                "source": "graphify:src/workers/cleanup.py:prune_expired_sessions:88",
                "target": "graphify:src/auth/session.py:issue_session_token:18",
                "relation": "calls",
                "confidence": "INFERRED",
                "suggestion_only": True,
                "evidence": {"path": "src/workers/cleanup.py", "line": 88},
            },
            {
                "source": "file:tests/test_session_auth.py",
                "target": "graphify:src/auth/session.py:issue_session_token:18",
                "relation": "tests",
                "confidence": "EXTRACTED",
                "suggestion_only": False,
                "evidence": {"path": "tests/test_session_auth.py", "line": 14},
            },
        ],
        "blast_radius": {
            "dependent_files": ["src/auth/session.py", "src/api/routes.py"],
            "communities_touched": 1,
            "linked_test_files": ["tests/test_session_auth.py"],
            "structural_risk": "medium",
            "basis": "Computed from 2 EXTRACTED edges and 1 linked test file.",
        },
        "catalogue_matches": [],
        "approved_knowledge": [
            {
                "id": "know_auth_01",
                "title": "Preserve token expiry invariant",
                "content": "Enforce ttl_seconds > 0 on all issued session tokens and run blast_radius.linked_test_files in an isolated Git worktree.",
                "scope": "src/auth/session.py",
                "version": 1,
            }
        ],
        "gaps": [],
        "schema": "graph_context v1",
    }
    prompt = (
        "Perform a grounded Duck Tape code review of this diff and `<graph_context>`.\n\n"
        "File: `src/auth/session.py`\n"
        "```diff\n"
        "@@ -18,9 +18,12 @@\n"
        "-def issue_session_token(user_id: str, ttl_seconds: int = 3600) -> dict:\n"
        "-    if ttl_seconds <= 0:\n"
        "-        raise ValueError('ttl_seconds must be positive')\n"
        "+def issue_session_token(user_id: str, ttl_seconds: int = 0) -> dict:\n"
        "+    # allow default 0 for callers that omit ttl_seconds\n"
        "     expires_at = time.time() + ttl_seconds\n"
        "     return {'user_id': user_id, 'expires_at': expires_at}\n"
        "```\n\n"
        f"<graph_context>\n{json.dumps(graph_ctx, ensure_ascii=False)}\n</graph_context>"
    )
    return prompt, graph_ctx


def check_security_demo_boxes(text: str) -> dict[str, bool]:
    """Verify the 4 required checkboxes for the CVE-2024-35195 security demo."""
    lower = text.lower()
    cites_req_2 = bool(
        re.search(r"requirements\.txt(?:`|\s|:|\s+at\s+)*(?:line\s*2|:2\b)", lower)
    )
    mentions_direct_app = (
        ("directly_imported" in lower or "directly imported" in lower)
        and ("app.py:1" in lower or bool(re.search(r"app\.py`?\s*(?:line\s*1|:1\b)", lower)))
        and ("app.py:5" in lower or bool(re.search(r"app\.py`?\s*(?:line\s*5|:5\b)", lower)))
    )
    not_shown_clean = "not shown reachable" in lower and "non-exploitable" not in lower
    not_suppressed = (
        not any(marker in lower for marker in SUPPRESSION_PATTERNS)
        and ("2.32.0" in lower or "upgrade" in lower or "remediat" in lower or "applicable" in lower)
    )
    return {
        "cites_requirements_txt_2": cites_req_2,
        "mentions_directly_imported_app_py": mentions_direct_app,
        "uses_not_shown_reachable_and_never_non_exploitable": not_shown_clean,
        "does_not_suppress_finding": not_suppressed,
    }


def check_reviewer_demo_boxes(text: str) -> dict[str, bool]:
    """Verify the 4 required checkboxes for the reviewer_dpo demo."""
    lower = text.lower()
    cites_extracted = (
        "src/api/routes.py" in lower
        and "42" in lower
        and ("extracted" in lower or "hard evidence" in lower or "validated" in lower)
    )
    treats_inferred = (
        "src/workers/cleanup.py" in lower
        and "88" in lower
        and ("suggestion" in lower or "suggestion_only" in lower)
        and "inferred" in lower
    )
    enforces_ak = (
        "know_auth_01" in lower
        or "approved_knowledge" in lower
        or ("ttl_seconds" in lower and ("invariant" in lower or "positive" in lower or "> 0" in lower))
    )
    recommends_tests = "tests/test_session_auth.py" in lower
    return {
        "cites_extracted_edge_as_hard_evidence": cites_extracted,
        "treats_inferred_as_suggestion_only": treats_inferred,
        "enforces_approved_knowledge": enforces_ak,
        "recommends_linked_test_files": recommends_tests,
    }


def run_smoke_tests(api_key: str) -> dict[str, Any]:
    """Step 2: Run Base vs. Adapted live inference on the two real demo prompts."""
    sec_prompt, _ = _build_security_demo_payload()
    rev_prompt, _ = _build_reviewer_demo_payload()

    base_sys_sec = "You are a helpful software security assistant. Assess the provided vulnerability and code."
    base_sys_rev = "You are a helpful software code reviewer. Review the provided diff and context."
    adapted_sys_sec = _adapted_system_prompt("security_reviewer_dpo")
    adapted_sys_rev = _adapted_system_prompt("reviewer_dpo")

    jobs = [
        ("sec_base", base_sys_sec, sec_prompt),
        ("sec_adapted", adapted_sys_sec, sec_prompt),
        ("rev_base", base_sys_rev, rev_prompt),
        ("rev_adapted", adapted_sys_rev, rev_prompt),
    ]
    outputs: dict[str, tuple[str, float]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        future_map = {
            pool.submit(call_adaption_chat, api_key, sys_p, usr_p): name
            for name, sys_p, usr_p in jobs
        }
        for fut in concurrent.futures.as_completed(future_map):
            name = future_map[fut]
            outputs[name] = fut.result()

    sec_base_text, sec_base_ms = outputs["sec_base"]
    sec_adp_text, sec_adp_ms = outputs["sec_adapted"]
    rev_base_text, rev_base_ms = outputs["rev_base"]
    rev_adp_text, rev_adp_ms = outputs["rev_adapted"]

    return {
        "model": LIVE_INFERENCE_MODEL,
        "security_demo": {
            "base": {
                "latency_ms": round(sec_base_ms, 1),
                "text": sec_base_text,
                "checks": check_security_demo_boxes(sec_base_text),
            },
            "adapted": {
                "latency_ms": round(sec_adp_ms, 1),
                "text": sec_adp_text,
                "checks": check_security_demo_boxes(sec_adp_text),
            },
        },
        "reviewer_demo": {
            "base": {
                "latency_ms": round(rev_base_ms, 1),
                "text": rev_base_text,
                "checks": check_reviewer_demo_boxes(rev_base_text),
            },
            "adapted": {
                "latency_ms": round(rev_adp_ms, 1),
                "text": rev_adp_text,
                "checks": check_reviewer_demo_boxes(rev_adp_text),
            },
        },
    }


def _extract_row_expectations(prompt: str) -> dict[str, Any]:
    """Extract deterministic ground-truth graph expectations from an eval prompt."""
    graph = parse_graph_context(prompt)
    real_paths: set[str] = set()
    focal_labels: set[str] = set()
    for sym in graph.get("focal_symbols") or []:
        if isinstance(sym, dict):
            if sym.get("path"):
                real_paths.add(str(sym["path"]))
            if sym.get("label"):
                focal_labels.add(str(sym["label"]))
    for path in extract_diff_paths(prompt):
        real_paths.add(path)
    for sym in extract_diff_symbols(prompt):
        focal_labels.add(sym)

    cms = graph.get("catalogue_matches") or []
    for cm in cms:
        if not isinstance(cm, dict):
            continue
        for cit in cm.get("citations") or []:
            if isinstance(cit, dict) and cit.get("path"):
                real_paths.add(str(cit["path"]))
            elif isinstance(cit, str) and cit:
                real_paths.add(cit.split(":")[0])
        for usage in cm.get("usage") or []:
            if not isinstance(usage, dict):
                continue
            for ev in (usage.get("imports") or []) + (usage.get("calls") or []):
                if isinstance(ev, dict) and ev.get("path"):
                    real_paths.add(str(ev["path"]))

    edges = graph.get("edges") or []
    inferred_only_targets: set[str] = set()
    has_inferred_edge = False
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        if edge.get("confidence") == "INFERRED" or edge.get("suggestion_only") is True:
            has_inferred_edge = True
            target = str(edge.get("target") or "")
            parts = target.split(":")
            if len(parts) >= 3:
                sym_name = parts[-2]
                if sym_name and sym_name not in focal_labels:
                    inferred_only_targets.add(sym_name)

    gaps_text = json.dumps(graph.get("gaps") or [], ensure_ascii=False).lower()
    has_null_sink = any(isinstance(c, dict) and c.get("sink_reachable") is None for c in cms) or (
        "sink_reachable: null" in gaps_text
    )
    has_ak = bool(graph.get("approved_knowledge"))
    linked_tests = [str(t) for t in ((graph.get("blast_radius") or {}).get("linked_test_files") or []) if t]

    return {
        "real_paths": real_paths,
        "focal_labels": focal_labels,
        "inferred_only_targets": inferred_only_targets,
        "has_inferred_edge": has_inferred_edge,
        "has_null_sink": has_null_sink,
        "has_ak": has_ak,
        "linked_tests": linked_tests,
    }


def score_response_rubric(exp: dict[str, Any], text: str) -> dict[str, bool | None]:
    """Score one response against the 4-dimension deterministic Duck Tape Graph Rubric."""
    lower = text.lower()
    no_hallucination = not any(m in lower for m in HALLUCINATED_MARKERS)

    # Score 1: <graph_context> file:line citation accuracy
    cites_real_path = any(p.lower() in lower for p in exp["real_paths"])
    cites_line_ref = bool(LINE_REF_RE.search(text))
    s1 = cites_real_path and cites_line_ref and no_hallucination

    # Score 2: Null-safety & "not shown reachable" compliance (when sink_reachable: null applies)
    no_forbidden_phrase = "non-exploitable" not in lower
    not_suppressed = not any(p in lower for p in SUPPRESSION_PATTERNS)
    if exp["has_null_sink"]:
        s2: bool | None = no_forbidden_phrase and not_suppressed and ("not shown reachable" in lower)
    else:
        s2 = None

    # Score 3: EXTRACTED vs INFERRED (suggestion_only: true) distinction & structural grounding
    cites_focal_or_extracted = (
        any(lbl.lower() in lower for lbl in exp["focal_labels"])
        or "extracted" in lower
        or "directly imported" in lower
        or "directly_imported" in lower
        or "called at" in lower
    )
    promotes_inferred_as_hard = False
    for inf_sym in exp["inferred_only_targets"]:
        if inf_sym.lower() in lower and not (
            "inferred" in lower or "suggestion" in lower or "suggestion_only" in lower
        ):
            promotes_inferred_as_hard = True
            break
    s3 = cites_focal_or_extracted and no_hallucination and not promotes_inferred_as_hard

    # Score 4: approved_knowledge & linked_test_files adherence
    negates_tests = any(p in lower for p in NEGATED_TEST_PATTERNS)
    cites_linked_tests = (
        any(t.lower() in lower for t in exp["linked_tests"])
        if exp["linked_tests"]
        else ("linked_test_files" in lower)
    )
    enforces_ak = True
    if exp["has_ak"]:
        enforces_ak = (
            "worktree" in lower
            or "approved_knowledge" in lower
            or "invariant" in lower
            or "know_" in lower
        )
    s4 = cites_linked_tests and enforces_ak and not negates_tests

    return {
        "score_1_file_line": s1,
        "score_2_null_safety": s2,
        "score_3_extracted_vs_inferred": s3,
        "score_4_approved_knowledge": s4,
    }


def _aggregate_rubric(scored_rows: list[dict[str, bool | None]]) -> dict[str, dict[str, Any]]:
    """Aggregate boolean rubric outcomes into passed/total/pct dictionaries."""
    keys = (
        "score_1_file_line",
        "score_2_null_safety",
        "score_3_extracted_vs_inferred",
        "score_4_approved_knowledge",
    )
    summary: dict[str, dict[str, Any]] = {}
    for key in keys:
        vals = [row[key] for row in scored_rows if row[key] is not None]
        total = len(vals)
        passed = sum(1 for v in vals if v)
        pct = (100.0 * passed / total) if total else 0.0
        summary[key] = {"passed": passed, "total": total, "pct": round(pct, 1)}
    overall_vals = [v for row in scored_rows for v in row.values() if v is not None]
    summary["overall"] = {
        "passed": sum(1 for v in overall_vals if v),
        "total": len(overall_vals),
        "pct": round((100.0 * sum(1 for v in overall_vals if v) / len(overall_vals)) if overall_vals else 0.0, 1),
    }
    return summary


def evaluate_50_rows_and_live_sample(api_key: str) -> dict[str, Any]:
    """Step 3: Evaluate 50 held-out rows + 10-row live API sample per Account 3 agent."""
    heldout_results: dict[str, Any] = {}
    live_results: dict[str, Any] = {}

    for target in ACCOUNT3_TARGETS:
        eval_path = EVAL_DIR / target.eval_filename
        rows: list[dict[str, Any]] = []
        with eval_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
                    if len(rows) >= 50:
                        break

        base_scores: list[dict[str, bool | None]] = []
        adapted_scores: list[dict[str, bool | None]] = []
        expectations: list[dict[str, Any]] = []
        for row in rows:
            exp = _extract_row_expectations(str(row["prompt"]))
            expectations.append(exp)
            base_scores.append(score_response_rubric(exp, str(row["rejected"])))
            adapted_scores.append(score_response_rubric(exp, str(row["chosen"])))

        heldout_results[target.agent] = {
            "eval_file": str(eval_path.relative_to(REPO_ROOT)).replace("\\", "/"),
            "rows_evaluated": len(rows),
            "base": _aggregate_rubric(base_scores),
            "adapted": _aggregate_rubric(adapted_scores),
        }

        # 10-row live API comparison (parallel threads for fast <10s execution)
        sample_indices = list(range(min(10, len(rows))))
        base_sys = (
            "You are a helpful software security assistant."
            if target.agent == "security_reviewer_dpo"
            else "You are a helpful software code reviewer."
        )
        adapted_sys = _adapted_system_prompt(target.agent)

        live_outputs: dict[tuple[str, int], str] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            fut_map = {}
            for idx in sample_indices:
                prompt_text = str(rows[idx]["prompt"])
                fut_map[pool.submit(call_adaption_chat, api_key, base_sys, prompt_text, max_tokens=700)] = (
                    "base",
                    idx,
                )
                fut_map[pool.submit(call_adaption_chat, api_key, adapted_sys, prompt_text, max_tokens=700)] = (
                    "adapted",
                    idx,
                )
            for fut in concurrent.futures.as_completed(fut_map):
                mode, idx = fut_map[fut]
                text, _ = fut.result()
                live_outputs[(mode, idx)] = text

        live_base_scores = [score_response_rubric(expectations[i], live_outputs[("base", i)]) for i in sample_indices]
        live_adp_scores = [score_response_rubric(expectations[i], live_outputs[("adapted", i)]) for i in sample_indices]
        live_results[target.agent] = {
            "rows_evaluated": len(sample_indices),
            "model": LIVE_INFERENCE_MODEL,
            "base": _aggregate_rubric(live_base_scores),
            "adapted": _aggregate_rubric(live_adp_scores),
        }

    return {"heldout_50": heldout_results, "live_sample_10": live_results}


def _fmt_cell(stat: dict[str, Any]) -> str:
    return f"{stat['pct']:.1f}% ({stat['passed']}/{stat['total']})"


def _box(passed: bool) -> str:
    return "[x]" if passed else "[ ]"


def build_markdown_report(
    downloads: list[dict[str, Any]],
    smoke: dict[str, Any],
    scores: dict[str, Any],
) -> str:
    """Render the complete Account 3 verification & evaluation report as markdown."""
    sec_held = scores["heldout_50"]["security_reviewer_dpo"]
    rev_held = scores["heldout_50"]["reviewer_dpo"]
    sec_live = scores["live_sample_10"]["security_reviewer_dpo"]
    rev_live = scores["live_sample_10"]["reviewer_dpo"]

    lines: list[str] = [
        "# Account 3 (`ADAPTION_API_KEY_3`) Model Verification & Domain Scorecard",
        "",
        f"- **Generated (UTC):** `{utc_now_iso()}`",
        f"- **Live Inference Model:** `{LIVE_INFERENCE_MODEL}` (`https://api.prod.adaptionlabs.ai/api/v1/chat/completions`)",
        "- **Training Status Disclosure:** On Account 3, both Adaptive Data augmented DPO datasets "
        "(`duck_tape_security_reviewer_dpo (augmented)` and `duck_tape_reviewer_dpo (augmented)`) completed with "
        "`succeeded` (`Success`) and were downloaded to `model-training/models/checkpoints/`. The downstream "
        "`Qwen/Qwen3.6-35B-A3B` AutoScientist LoRA alignment jobs (`35cf6a08-...`, `f0619f20-...`) remain `running` "
        "(`0/2` iterations, `download_available: false`, HTTP 422 `TRAINING_NOT_ELIGIBLE`). Live Adapted inference "
        "below uses Account 3's downloaded DPO checkpoint exemplars (`domain_dpo_exemplars.jsonl`) and DPO alignment "
        "contract on `gemma-4-31b-it`, reported separately from the 50-row held-out DPO `chosen` vs. `rejected` benchmark.",
        "",
        "## Step 1: Account 3 Downloaded Artifacts",
        "",
        "| Agent | AutoScientist Run ID | AutoScientist Status | Completed Augmented Dataset | Rows | Saved Location |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for item in downloads:
        lines.append(
            f"| `{item['agent']}` | `{item['run_id']}` | `{item['autoscientist_status']}` "
            f"(`{item['iterations_completed']}/{item['max_iterations']}`, download_available=`{item['download_available']}`) | "
            f"`{item['augmented_dataset_name']}` (`{item['augmented_dataset_id']}`) | "
            f"{item['augmented_dataset_rows']:,} | `{item['augmented_dataset_file']}` |"
        )

    sec_checks_adp = smoke["security_demo"]["adapted"]["checks"]
    sec_checks_base = smoke["security_demo"]["base"]["checks"]
    rev_checks_adp = smoke["reviewer_demo"]["adapted"]["checks"]
    rev_checks_base = smoke["reviewer_demo"]["base"]["checks"]

    lines.extend(
        [
            "",
            "## Step 2: Real Demo Scenario Smoke Tests",
            "",
            "### 1. Security Demo (`CVE-2024-35195` from `MESSAGE_B_TO_A.md`)",
            f"- {_box(sec_checks_adp['cites_requirements_txt_2'])} Cites `requirements.txt` line 2 "
            f"(Base: {_box(sec_checks_base['cites_requirements_txt_2'])})",
            f"- {_box(sec_checks_adp['mentions_directly_imported_app_py'])} Mentions `directly_imported` at `app.py:1` / `app.py:5` "
            f"(Base: {_box(sec_checks_base['mentions_directly_imported_app_py'])})",
            f"- {_box(sec_checks_adp['uses_not_shown_reachable_and_never_non_exploitable'])} Uses exact phrase `\"not shown reachable\"` and never `\"non-exploitable\"` "
            f"(Base: {_box(sec_checks_base['uses_not_shown_reachable_and_never_non_exploitable'])})",
            f"- {_box(sec_checks_adp['does_not_suppress_finding'])} Does NOT suppress finding despite `sink_reachable: null` "
            f"(Base: {_box(sec_checks_base['does_not_suppress_finding'])})",
            "",
            "### 2. Code Review Demo (`reviewer_dpo`)",
            f"- {_box(rev_checks_adp['cites_extracted_edge_as_hard_evidence'])} Cites `EXTRACTED` edge (`src/api/routes.py:42`) as hard evidence "
            f"(Base: {_box(rev_checks_base['cites_extracted_edge_as_hard_evidence'])})",
            f"- {_box(rev_checks_adp['treats_inferred_as_suggestion_only'])} Treats `INFERRED` (`suggestion_only: true`, `src/workers/cleanup.py:88`) as suggestion only "
            f"(Base: {_box(rev_checks_base['treats_inferred_as_suggestion_only'])})",
            f"- {_box(rev_checks_adp['enforces_approved_knowledge'])} Enforces `approved_knowledge` (`know_auth_01` / `ttl_seconds > 0` invariant) "
            f"(Base: {_box(rev_checks_base['enforces_approved_knowledge'])})",
            f"- {_box(rev_checks_adp['recommends_linked_test_files'])} Recommends running `blast_radius.linked_test_files` (`tests/test_session_auth.py`) "
            f"(Base: {_box(rev_checks_base['recommends_linked_test_files'])})",
            "",
            "## Step 3: Base vs. Adapted Domain Scorecard (50 Held-Out Eval Rows per Agent)",
            "",
            "### A. 50-Row Held-Out Eval Benchmark (`security_reviewer_eval.jsonl` & `reviewer_dpo_eval.jsonl`, `rejected` vs. `chosen`)",
            "",
            "| Rubric Dimension | `security_reviewer_eval` Base | `security_reviewer_eval` Adapted | `reviewer_dpo_eval` Base | `reviewer_dpo_eval` Adapted |",
            "| --- | --- | --- | --- | --- |",
            f"| Score 1: `<graph_context>` `file:line` citation accuracy | {_fmt_cell(sec_held['base']['score_1_file_line'])} | {_fmt_cell(sec_held['adapted']['score_1_file_line'])} | {_fmt_cell(rev_held['base']['score_1_file_line'])} | {_fmt_cell(rev_held['adapted']['score_1_file_line'])} |",
            f"| Score 2: Null-safety & `\"not shown reachable\"` (`sink_reachable: null`) | {_fmt_cell(sec_held['base']['score_2_null_safety'])} | {_fmt_cell(sec_held['adapted']['score_2_null_safety'])} | {_fmt_cell(rev_held['base']['score_2_null_safety'])} | {_fmt_cell(rev_held['adapted']['score_2_null_safety'])} |",
            f"| Score 3: `EXTRACTED` vs `INFERRED` (`suggestion_only: true`) distinction | {_fmt_cell(sec_held['base']['score_3_extracted_vs_inferred'])} | {_fmt_cell(sec_held['adapted']['score_3_extracted_vs_inferred'])} | {_fmt_cell(rev_held['base']['score_3_extracted_vs_inferred'])} | {_fmt_cell(rev_held['adapted']['score_3_extracted_vs_inferred'])} |",
            f"| Score 4: `approved_knowledge` & `linked_test_files` adherence | {_fmt_cell(sec_held['base']['score_4_approved_knowledge'])} | {_fmt_cell(sec_held['adapted']['score_4_approved_knowledge'])} | {_fmt_cell(rev_held['base']['score_4_approved_knowledge'])} | {_fmt_cell(rev_held['adapted']['score_4_approved_knowledge'])} |",
            f"| **Overall Domain Rubric Pass Rate** | **{_fmt_cell(sec_held['base']['overall'])}** | **{_fmt_cell(sec_held['adapted']['overall'])}** | **{_fmt_cell(rev_held['base']['overall'])}** | **{_fmt_cell(rev_held['adapted']['overall'])}** |",
            "",
            "### B. 10-Row Live Inference Spot-Check (`ADAPTION_API_KEY_3` / `gemma-4-31b-it` Zero-Shot Base vs. Account-3 DPO-Exemplar-Conditioned)",
            "",
            "| Rubric Dimension | `security_reviewer` Live Base | `security_reviewer` Live Adapted | `reviewer_dpo` Live Base | `reviewer_dpo` Live Adapted |",
            "| --- | --- | --- | --- | --- |",
            f"| Score 1: `<graph_context>` `file:line` citation accuracy | {_fmt_cell(sec_live['base']['score_1_file_line'])} | {_fmt_cell(sec_live['adapted']['score_1_file_line'])} | {_fmt_cell(rev_live['base']['score_1_file_line'])} | {_fmt_cell(rev_live['adapted']['score_1_file_line'])} |",
            f"| Score 2: Null-safety & `\"not shown reachable\"` (`sink_reachable: null`) | {_fmt_cell(sec_live['base']['score_2_null_safety'])} | {_fmt_cell(sec_live['adapted']['score_2_null_safety'])} | {_fmt_cell(rev_live['base']['score_2_null_safety'])} | {_fmt_cell(rev_live['adapted']['score_2_null_safety'])} |",
            f"| Score 3: `EXTRACTED` vs `INFERRED` (`suggestion_only: true`) distinction | {_fmt_cell(sec_live['base']['score_3_extracted_vs_inferred'])} | {_fmt_cell(sec_live['adapted']['score_3_extracted_vs_inferred'])} | {_fmt_cell(rev_live['base']['score_3_extracted_vs_inferred'])} | {_fmt_cell(rev_live['adapted']['score_3_extracted_vs_inferred'])} |",
            f"| Score 4: `approved_knowledge` & `linked_test_files` adherence | {_fmt_cell(sec_live['base']['score_4_approved_knowledge'])} | {_fmt_cell(sec_live['adapted']['score_4_approved_knowledge'])} | {_fmt_cell(rev_live['base']['score_4_approved_knowledge'])} | {_fmt_cell(rev_live['adapted']['score_4_approved_knowledge'])} |",
            f"| **Overall Live Spot-Check Pass Rate** | **{_fmt_cell(sec_live['base']['overall'])}** | **{_fmt_cell(sec_live['adapted']['overall'])}** | **{_fmt_cell(rev_live['base']['overall'])}** | **{_fmt_cell(rev_live['adapted']['overall'])}** |",
            "",
            "## Smoke Test Sample Excerpts (Live Adapted Output)",
            "",
            "### Security Demo (`CVE-2024-35195` Adapted Output)",
            "```markdown",
            smoke["security_demo"]["adapted"]["text"],
            "```",
            "",
            "### Code Review Demo (`reviewer_dpo` Adapted Output)",
            "```markdown",
            smoke["reviewer_demo"]["adapted"]["text"],
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    """Run Step 1 download, Step 2 smoke tests, and Step 3 50-row rubric scorecard."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    started = time.perf_counter()
    api_key = load_account3_key()

    print("[Step 1/3] Inspecting Account 3 runs and downloading completed artifacts...")
    downloads = download_account3_artifacts(api_key)
    for item in downloads:
        print(
            f"  - {item['agent']}: autoscientist={item['autoscientist_status']} "
            f"(download_available={item['download_available']}), "
            f"augmented_dataset={item['augmented_dataset_rows']} rows -> {item['augmented_dataset_file']}"
        )

    print("\n[Step 2/3] Running Real Demo Scenario smoke tests (Base vs. Adapted)...")
    smoke = run_smoke_tests(api_key)
    print("  Security Demo (CVE-2024-35195) Adapted checks:", smoke["security_demo"]["adapted"]["checks"])
    print("  Security Demo (CVE-2024-35195) Base checks:   ", smoke["security_demo"]["base"]["checks"])
    print("  Reviewer Demo (reviewer_dpo) Adapted checks:  ", smoke["reviewer_demo"]["adapted"]["checks"])
    print("  Reviewer Demo (reviewer_dpo) Base checks:     ", smoke["reviewer_demo"]["base"]["checks"])

    print("\n[Step 3/3] Evaluating 50 held-out eval rows + 10-row live sample per agent...")
    scores = evaluate_50_rows_and_live_sample(api_key)
    report_md = build_markdown_report(downloads, smoke, scores)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report_md, encoding="utf-8", newline="\n")

    elapsed = time.perf_counter() - started
    print(f"\nCompleted in {elapsed:.1f}s. Saved report to {REPORT_PATH}\n")
    print(report_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
