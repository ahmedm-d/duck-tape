"""Wave 1 Data Prep — Agent 2: ticket_parser."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from datasets import load_dataset
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from training.schemas import InstructionRow, TicketParserCompletion
from training.scrub import scrub_with_count
from training.utils import (
    MODEL_TRAINING_ROOT,
    extract_symbols_and_paths,
    load_training_env,
    split_train_eval,
    truncate_bounded,
    write_instruction_jsonl,
)

TRAIN_TARGET = 2500
EVAL_TARGET = 200
TOTAL_TARGET = TRAIN_TARGET + EVAL_TARGET


def _clean_title(raw: str, fallback: str) -> str:
    for line in (raw or "").splitlines():
        candidate = re.sub(r"^[#*\-\s]+", "", line).strip()
        if len(candidate) >= 6:
            return candidate[:117] + "..." if len(candidate) > 120 else candidate
    return (fallback or "Maintenance ticket")[:120]


def _infer_priority(text_lower: str, jira_priority: str = "") -> str:
    jp = jira_priority.lower()
    if jp in ("blocker", "critical") or any(
        kw in text_lower for kw in ("security", "cve", "crash", "data loss", "panic", "corrupt", "outage", "rce")
    ):
        return "P0"
    if jp in ("major", "high") or any(
        kw in text_lower for kw in ("regression", "fails", "broken", "exception", "500", "deadlock", "incorrect")
    ):
        return "P1"
    if jp in ("trivial", "minor", "low") or any(
        kw in text_lower for kw in ("typo", "docstring", "readme", "cosmetic", "cleanup", "minor")
    ):
        return "P3"
    return "P2"


def _infer_change_and_source(
    title_lower: str, body_lower: str, issue_type: str = "", is_secops: bool = False
) -> tuple[str, str]:
    it = issue_type.lower()
    combined = f"{title_lower}\n{body_lower}"
    if is_secops or any(
        kw in combined for kw in ("cve", "vulnerability", "xss", "csrf", "injection", "auth bypass", "privilege")
    ):
        return "security", "security"
    if any(kw in combined for kw in ("benchmark", "slow", "latency", "memory leak", "o(n^2)", "performance", "speed up")):
        change_type = "performance"
    elif "bug" in it or title_lower.startswith(("bug:", "fix:", "[bug]")) or any(
        kw in title_lower for kw in ("bug", "error", "fails", "broken", "crash", "wrong", "incorrect", "does not", "doesn't")
    ):
        change_type = "fix"
    elif "refactor" in it or any(kw in title_lower for kw in ("refactor", "clean up", "simplify", "deprecate")):
        change_type = "refactor"
    elif any(tok in it for tok in ("feature", "improvement", "enhancement", "story", "wish")) or any(
        kw in combined for kw in ("feature request", "support for", "add option", "allow ", "implement ")
    ):
        change_type = "feature"
    else:
        change_type = "fix"

    if any(kw in combined for kw in ("investigate", "research", "rfc", "proposal", "evaluate")):
        source_tag = "research"
    elif any(kw in combined for kw in ("profile", "analysis", "audit", "coverage")):
        source_tag = "analysis"
    elif change_type == "feature" or "enhancement" in it:
        source_tag = "request"
    else:
        source_tag = "ticket"
    return source_tag, change_type


def _infer_decision_card(text_lower: str, change_type: str) -> tuple[bool, list[str]]:
    triggers = (
        "option",
        "alternatively",
        "should we",
        "whether to",
        "tradeoff",
        "trade-off",
        "breaking change",
        "backward compatibility",
        "backwards compatibility",
        "deprecate",
    )
    if change_type == "feature" and any(kw in text_lower for kw in triggers):
        return True, [
            "Preserve backward-compatible default behavior behind an opt-in flag",
            "Introduce breaking API change in next minor version with migration notes",
        ]
    if text_lower.count("?") >= 3 or any(kw in text_lower for kw in ("tradeoff", "trade-off", "breaking change")):
        return True, [
            "Apply minimal localized fix preserving existing public contract",
            "Refactor interface and update dependent callers and tests",
        ]
    return False, []


def _build_ticket_row(
    source_label: str,
    project: str,
    title_hint: str,
    body: str,
    patch: str = "",
    issue_type: str = "",
    jira_priority: str = "",
    components: list[str] | None = None,
    is_secops: bool = False,
) -> tuple[InstructionRow, int]:
    cleaned_body, r1 = scrub_with_count(body)
    cleaned_title_hint, r2 = scrub_with_count(title_hint)
    cleaned_patch, r3 = scrub_with_count(patch)
    redactions = r1 + r2 + r3

    title = _clean_title(cleaned_title_hint or cleaned_body, f"{project} issue")
    title_lower = title.lower()
    body_lower = cleaned_body.lower()
    combined_lower = f"{title_lower}\n{body_lower}"
    source_tag, change_type = _infer_change_and_source(
        title_lower, body_lower, issue_type=issue_type, is_secops=is_secops
    )
    priority = _infer_priority(combined_lower, jira_priority=jira_priority)
    candidates = extract_symbols_and_paths(cleaned_body, cleaned_patch, limit=8)
    if not candidates and components:
        candidates = [c for c in components if c][:4]
    needs_card, options = _infer_decision_card(combined_lower, change_type)

    completion_obj = TicketParserCompletion(
        title=title,
        source_tag=source_tag,
        change_type=change_type,
        priority=priority,
        candidate_files_or_symbols=candidates,
        needs_decision_card=needs_card,
        decision_options=options,
    )
    completion_str = json.dumps(completion_obj.model_dump(), ensure_ascii=False)
    TicketParserCompletion.model_validate(json.loads(completion_str))

    prompt = (
        "Parse this incoming software maintenance issue into structured JSON.\n"
        f"Source: {source_label}\n"
        f"Project/Repository: {project}\n"
        + (f"Reported Type: {issue_type}\n" if issue_type else "")
        + f"\nIssue Text:\n{truncate_bounded(cleaned_body, 2600)}\n\n"
        "Respond with strict JSON matching: "
        '{"title": str, "source_tag": "ticket|request|security|analysis|research", '
        '"change_type": "feature|fix|refactor|security|performance", '
        '"priority": "P0|P1|P2|P3", "candidate_files_or_symbols": list[str], '
        '"needs_decision_card": bool, "decision_options": list[str]}.'
    )
    return InstructionRow(prompt=prompt, completion=completion_str), redactions


def _extract_jira_fields(record: dict[str, Any]) -> tuple[str, str, str, str, str, list[str], bool]:
    fields = record.get("fields") or {}
    if not isinstance(fields, dict):
        fields = {}
    key = str(record.get("key") or record.get("id") or "JIRA")
    project_info = fields.get("project") if isinstance(fields.get("project"), dict) else {}
    project = str(project_info.get("name") or project_info.get("key") or key.split("-")[0])
    summary = str(fields.get("summary") or "").strip()
    description = str(fields.get("description") or "").strip()
    issuetype = str((fields.get("issuetype") or {}).get("name") or "") if isinstance(fields.get("issuetype"), dict) else ""
    priority = str((fields.get("priority") or {}).get("name") or "") if isinstance(fields.get("priority"), dict) else ""
    components = [
        str(c.get("name"))
        for c in (fields.get("components") or [])
        if isinstance(c, dict) and c.get("name")
    ]
    is_secops = str(record.get("is_secops") or "").lower() == "true"
    full_text = f"{summary}\n\n{description}".strip()
    return project, summary, full_text, issuetype, priority, components, is_secops


def collect_rows() -> tuple[list[InstructionRow], dict[str, int], int]:
    """Stream GitHub issues and Jira tickets up to TOTAL_TARGET."""
    rows: list[InstructionRow] = []
    seen_texts: set[str] = set()
    source_counts: dict[str, int] = {}
    total_redactions = 0

    def _add(source_key: str, row: InstructionRow, redactions: int) -> bool:
        nonlocal total_redactions
        sig = row.prompt[:220]
        if sig in seen_texts:
            return False
        seen_texts.add(sig)
        rows.append(row)
        source_counts[source_key] = source_counts.get(source_key, 0) + 1
        total_redactions += redactions
        return True

    # 1. princeton-nlp/SWE-bench_Verified (only 'test' split exists, 500 rows)
    ds_verified = load_dataset("princeton-nlp/SWE-bench_Verified", split="test", streaming=True)
    pbar = tqdm(total=480, desc="ticket_parser <- SWE-bench_Verified", unit="rows")
    for rec in ds_verified:
        if source_counts.get("SWE-bench_Verified", 0) >= 480:
            break
        body = str(rec.get("problem_statement") or "").strip()
        if len(body) < 40:
            continue
        row, red = _build_ticket_row(
            "GitHub Issue (SWE-bench_Verified)",
            str(rec.get("repo") or "unknown/repo"),
            "",
            body,
            patch=str(rec.get("patch") or ""),
        )
        if _add("SWE-bench_Verified", row, red):
            pbar.update(1)
    pbar.close()

    # 2. SWE-Gym/SWE-Gym
    ds_gym = load_dataset("SWE-Gym/SWE-Gym", split="train", streaming=True)
    pbar = tqdm(total=1220, desc="ticket_parser <- SWE-Gym", unit="rows")
    for rec in ds_gym:
        if source_counts.get("SWE-Gym", 0) >= 1220:
            break
        body = str(rec.get("problem_statement") or "").strip()
        if len(body) < 40:
            continue
        row, red = _build_ticket_row(
            "GitHub Issue (SWE-Gym)",
            str(rec.get("repo") or "unknown/repo"),
            "",
            body,
            patch=str(rec.get("patch") or ""),
        )
        if _add("SWE-Gym", row, red):
            pbar.update(1)
    pbar.close()

    # 3. Public Jira datasets (cjsanjay/jira_train_17012024 + kristmh/clean_Jira_balanced fallback)
    jira_quota = TOTAL_TARGET - len(rows)
    ds_jira = load_dataset("cjsanjay/jira_train_17012024", split="train", streaming=True)
    pbar = tqdm(total=jira_quota, desc="ticket_parser <- jira_train_17012024", unit="rows")
    for rec in ds_jira:
        if len(rows) >= TOTAL_TARGET:
            break
        project, summary, full_text, itype, prio, comps, secops = _extract_jira_fields(rec)
        if len(full_text) < 45:
            continue
        row, red = _build_ticket_row(
            "Public Jira Issue (cjsanjay/jira_train_17012024)",
            project,
            summary,
            full_text,
            issue_type=itype,
            jira_priority=prio,
            components=comps,
            is_secops=secops,
        )
        if _add("jira_train_17012024", row, red):
            pbar.update(1)
    pbar.close()

    if len(rows) < TOTAL_TARGET:
        rem = TOTAL_TARGET - len(rows)
        ds_jira_fb = load_dataset("kristmh/clean_Jira_balanced", split="test", streaming=True)
        pbar = tqdm(total=rem, desc="ticket_parser <- clean_Jira_balanced", unit="rows")
        for rec in ds_jira_fb:
            if len(rows) >= TOTAL_TARGET:
                break
            body = str(rec.get("text_clean") or "").strip()
            if len(body) < 30:
                continue
            row, red = _build_ticket_row("Public Jira Issue (clean_Jira_balanced)", "jira-mirror", "", body)
            if _add("clean_Jira_balanced", row, red):
                pbar.update(1)
        pbar.close()

    return rows, source_counts, total_redactions


def main() -> int:
    load_training_env()
    rows, source_counts, redactions = collect_rows()
    train_rows, eval_rows = split_train_eval(rows, TRAIN_TARGET, EVAL_TARGET, seed=42)

    train_path = MODEL_TRAINING_ROOT / "data" / "processed" / "ticket_parser.jsonl"
    eval_path = MODEL_TRAINING_ROOT / "data" / "eval" / "ticket_parser_eval.jsonl"
    write_instruction_jsonl(train_rows, train_path)
    write_instruction_jsonl(eval_rows, eval_path)

    print(
        f"[ticket_parser] wrote {len(train_rows)} train -> {train_path} | "
        f"{len(eval_rows)} eval -> {eval_path} | redactions={redactions} | sources={source_counts}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
