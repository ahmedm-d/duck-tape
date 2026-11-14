"""Validate Wave 1 & Wave 2 JSONL datasets, compute tiktoken stats, and write inspection_report.md."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from training.schemas import (
    ChatTrajectoryRow,
    InstructionRow,
    LogClassifierCompletion,
    PreferenceRow,
    RouterCompletion,
    TicketParserCompletion,
    compute_jsonl_token_stats,
    count_chat_tokens,
)
from training.scrub import contains_secrets
from training.utils import MODEL_TRAINING_ROOT

AGENTS: tuple[tuple[str, str, str, str, type | None], ...] = (
    ("router", "instruction", "data/processed/router.jsonl", "data/eval/router_eval.jsonl", RouterCompletion),
    ("ticket_parser", "instruction", "data/processed/ticket_parser.jsonl", "data/eval/ticket_parser_eval.jsonl", TicketParserCompletion),
    ("commit_pr_writer", "instruction", "data/processed/commit_pr_writer.jsonl", "data/eval/commit_pr_writer_eval.jsonl", None),
    ("log_classifier", "instruction", "data/processed/log_classifier.jsonl", "data/eval/log_classifier_eval.jsonl", LogClassifierCompletion),
    ("implementer", "chat", "data/processed/implementer.jsonl", "data/eval/implementer_eval.jsonl", None),
    ("test_writer", "instruction", "data/processed/test_writer.jsonl", "data/eval/test_writer_eval.jsonl", None),
    ("reviewer", "preference", "data/processed/reviewer_dpo.jsonl", "data/eval/reviewer_dpo_eval.jsonl", None),
    ("security_reviewer", "preference", "data/processed/security_reviewer_dpo.jsonl", "data/eval/security_reviewer_eval.jsonl", None),
)

COMMIT_REQUIRED_HEADINGS = (
    "### Summary",
    "### Cited Symbols",
    "### Confidence Assessment",
    "### Risk Assessment",
)
TEST_WRITER_REQUIRED_HEADINGS = (
    "### Reproduction Test Diff",
    "### Verification Command",
)
MAX_IMPLEMENTER_TOKENS = 45000


def _row_signature(record: dict[str, Any], fmt: str) -> str:
    if fmt == "chat":
        return json.dumps(record.get("chat", [])[:2], sort_keys=True, ensure_ascii=False)
    return str(record.get("prompt") or "")


def _check_secrets_in_obj(obj: Any) -> int:
    if isinstance(obj, str):
        return 1 if contains_secrets(obj) else 0
    if isinstance(obj, dict):
        return sum(_check_secrets_in_obj(v) for v in obj.values())
    if isinstance(obj, list):
        return sum(_check_secrets_in_obj(v) for v in obj)
    return 0


def _validate_file(
    agent: str,
    fmt: str,
    path: Path,
    schema_cls: type | None,
) -> tuple[dict[str, Any], int, dict[str, int], set[str]]:
    if not path.is_file():
        raise FileNotFoundError(f"Missing expected dataset file: {path}")

    stats = compute_jsonl_token_stats(path)
    secret_hits = 0
    dist: Counter[str] = Counter()
    sigs: set[str] = set()

    with path.open("r", encoding="utf-8") as handle:
        for idx, line in enumerate(handle, start=1):
            raw_record = json.loads(line)
            sigs.add(_row_signature(raw_record, fmt))

            if fmt == "chat":
                row = ChatTrajectoryRow.model_validate(raw_record)
                if _check_secrets_in_obj(row.chat) > 0:
                    secret_hits += 1
                p_toks, c_toks = count_chat_tokens(row.chat)
                if (p_toks + c_toks) > MAX_IMPLEMENTER_TOKENS:
                    raise ValueError(
                        f"{path}:{idx} exceeds {MAX_IMPLEMENTER_TOKENS} token ceiling ({p_toks + c_toks} tokens)"
                    )
                tc_count = 0
                for msg in row.chat:
                    for tc in msg.get("tool_calls") or []:
                        fn = tc.get("function") or {}
                        if not isinstance(fn.get("arguments"), dict):
                            raise ValueError(
                                f"{path}:{idx} tool_call function.arguments is not normalized as dict"
                            )
                        tc_count += 1
                dist["chat_valid"] += 1
                if tc_count > 0:
                    dist["trajectories_with_tool_calls"] += 1
            elif fmt == "preference":
                row_pref = PreferenceRow.model_validate(raw_record)
                if (
                    contains_secrets(row_pref.prompt)
                    or contains_secrets(row_pref.chosen)
                    or contains_secrets(row_pref.rejected)
                ):
                    secret_hits += 1
                if agent == "security_reviewer":
                    if "### Verdict: EXPLOITABLE" in row_pref.chosen:
                        dist["exploitable"] += 1
                    elif "### Verdict: NOT EXPLOITABLE" in row_pref.chosen:
                        dist["benign_patched_or_unreachable"] += 1
                    else:
                        raise ValueError(f"{path}:{idx} missing Verdict header in security_reviewer chosen")
                else:
                    dist["preference_valid"] += 1
            else:
                row_inst = InstructionRow.model_validate(raw_record)
                if contains_secrets(row_inst.prompt) or contains_secrets(row_inst.completion):
                    secret_hits += 1
                if schema_cls is not None:
                    payload = json.loads(row_inst.completion)
                    obj = schema_cls.model_validate(payload)
                    if agent == "router":
                        dist[obj.next_agent] += 1
                    elif agent == "ticket_parser":
                        dist[obj.change_type] += 1
                        dist[f"priority:{obj.priority}"] += 1
                        dist[f"decision_card:{obj.needs_decision_card}"] += 1
                    elif agent == "log_classifier":
                        dist[obj.failure_category] += 1
                elif agent == "commit_pr_writer":
                    for heading in COMMIT_REQUIRED_HEADINGS:
                        if heading not in row_inst.completion:
                            raise ValueError(f"{path}:{idx} missing required heading {heading!r}")
                    dist["markdown_valid"] += 1
                elif agent == "test_writer":
                    for heading in TEST_WRITER_REQUIRED_HEADINGS:
                        if heading not in row_inst.completion:
                            raise ValueError(f"{path}:{idx} missing required heading {heading!r}")
                    dist["test_diff_valid"] += 1

    return stats, secret_hits, dict(sorted(dist.items())), sigs


def main() -> int:
    summary_rows: list[dict[str, Any]] = []
    md_lines = [
        "# Wave 1 & Wave 2 Dataset Inspection Report",
        "",
        "All datasets were streamed with `datasets.load_dataset(..., streaming=True)`, scrubbed via `training.scrub`,",
        "validated with Pydantic v2 schemas in `training.schemas`, and measured with `tiktoken` (`cl100k_base`).",
        "",
        "## Summary Table",
        "",
        "| Agent | Format | Split | Path | Rows | Total Tokens | Prompt Avg (p95 / Max) | Completion Avg (p95 / Max) | Secret Hits | Overlap |",
        "| --- | --- | --- | --- | ---: | ---: | --- | --- | ---: | ---: |",
    ]

    details_lines: list[str] = ["", "## Label & Schema Distributions", ""]

    for agent, fmt, train_rel, eval_rel, schema_cls in AGENTS:
        train_path = MODEL_TRAINING_ROOT / train_rel
        eval_path = MODEL_TRAINING_ROOT / eval_rel

        tr_stats, tr_secrets, tr_dist, tr_sigs = _validate_file(agent, fmt, train_path, schema_cls)
        ev_stats, ev_secrets, ev_dist, ev_sigs = _validate_file(agent, fmt, eval_path, schema_cls)

        overlap = len(tr_sigs & ev_sigs)
        if overlap != 0:
            raise RuntimeError(f"Train/eval overlap detected for {agent}: {overlap}")
        if tr_secrets != 0 or ev_secrets != 0:
            raise RuntimeError(f"Unscrubbed secret detected for {agent}: train={tr_secrets}, eval={ev_secrets}")

        expected_train = 1200 if agent == "router" else 5000
        if tr_stats["rows"] != expected_train:
            raise RuntimeError(f"Expected {expected_train} train rows for {agent}, got {tr_stats['rows']}")
        if ev_stats["rows"] != 200:
            raise RuntimeError(f"Expected 200 eval rows for {agent}, got {ev_stats['rows']}")

        if agent == "security_reviewer":
            if ev_dist.get("exploitable") != 100 or ev_dist.get("benign_patched_or_unreachable") != 100:
                raise RuntimeError(f"security_reviewer eval split is not 100/100 balanced: {ev_dist}")

        if agent == "reviewer":
            alias_eval = MODEL_TRAINING_ROOT / "data" / "eval" / "reviewer_eval.jsonl"
            if not alias_eval.is_file():
                raise FileNotFoundError(f"Missing alias eval file: {alias_eval}")
            _validate_file(agent, fmt, alias_eval, schema_cls)

        for split_label, rel, st, sec in (
            ("train", train_rel, tr_stats, tr_secrets),
            ("eval", eval_rel, ev_stats, ev_secrets),
        ):
            p = st["prompt"]
            c = st["completion"]
            md_lines.append(
                f"| `{agent}` | `{fmt}` | {split_label} | `model-training/{rel}` | {st['rows']:,} | {st['total_tokens']:,} | "
                f"{p['mean']} ({p['p95']} / {p['max']}) | {c['mean']} ({c['p95']} / {c['max']}) | {sec} | {overlap} |"
            )
            summary_rows.append({"agent": agent, "format": fmt, "split": split_label, "stats": st, "overlap": overlap})

        details_lines.append(f"### `{agent}`")
        details_lines.append(f"- Train distribution: `{json.dumps(tr_dist)}`")
        details_lines.append(f"- Eval distribution: `{json.dumps(ev_dist)}`")
        details_lines.append("")

    report_path = MODEL_TRAINING_ROOT / "data" / "inspection_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(md_lines + details_lines) + "\n")
    print(json.dumps(summary_rows, indent=2))
    print(f"Wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
