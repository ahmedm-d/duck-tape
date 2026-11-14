"""Validate Wave 1 JSONL datasets, compute tiktoken stats, and write inspection_report.md."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from training.schemas import (
    InstructionRow,
    LogClassifierCompletion,
    RouterCompletion,
    TicketParserCompletion,
    compute_jsonl_token_stats,
)
from training.scrub import contains_secrets
from training.utils import MODEL_TRAINING_ROOT

AGENTS = (
    ("router", "data/processed/router.jsonl", "data/eval/router_eval.jsonl", RouterCompletion),
    ("ticket_parser", "data/processed/ticket_parser.jsonl", "data/eval/ticket_parser_eval.jsonl", TicketParserCompletion),
    ("commit_pr_writer", "data/processed/commit_pr_writer.jsonl", "data/eval/commit_pr_writer_eval.jsonl", None),
    ("log_classifier", "data/processed/log_classifier.jsonl", "data/eval/log_classifier_eval.jsonl", LogClassifierCompletion),
)

COMMIT_REQUIRED_HEADINGS = (
    "### Summary",
    "### Cited Symbols",
    "### Confidence Assessment",
    "### Risk Assessment",
)


def _validate_file(agent: str, path: Path, schema_cls: type | None) -> tuple[dict, int, dict[str, int]]:
    stats = compute_jsonl_token_stats(path)
    secret_hits = 0
    dist: Counter[str] = Counter()

    with path.open("r", encoding="utf-8") as handle:
        for idx, line in enumerate(handle, start=1):
            row = InstructionRow.model_validate_json(line)
            if contains_secrets(row.prompt) or contains_secrets(row.completion):
                secret_hits += 1
            if schema_cls is not None:
                payload = json.loads(row.completion)
                obj = schema_cls.model_validate(payload)
                if agent == "router":
                    dist[obj.next_agent] += 1
                elif agent == "ticket_parser":
                    dist[obj.change_type] += 1
                    dist[f"priority:{obj.priority}"] += 1
                    dist[f"decision_card:{obj.needs_decision_card}"] += 1
                elif agent == "log_classifier":
                    dist[obj.failure_category] += 1
            else:
                for heading in COMMIT_REQUIRED_HEADINGS:
                    if heading not in row.completion:
                        raise ValueError(f"{path}:{idx} missing required heading {heading!r}")
                dist["markdown_valid"] += 1

    return stats, secret_hits, dict(sorted(dist.items()))


def main() -> int:
    summary_rows: list[dict] = []
    md_lines = [
        "# Wave 1 Dataset Inspection Report",
        "",
        "All datasets were streamed with `datasets.load_dataset(..., streaming=True)`, scrubbed via `training.scrub`,",
        "validated with Pydantic v2 schemas in `training.schemas`, and measured with `tiktoken` (`cl100k_base`).",
        "",
        "## Summary Table",
        "",
        "| Agent | Split | Path | Rows | Total Tokens | Prompt Avg (p95 / Max) | Completion Avg (p95 / Max) | Secret Hits | Overlap |",
        "| --- | --- | --- | ---: | ---: | --- | --- | ---: | ---: |",
    ]

    details_lines: list[str] = ["", "## Label & Schema Distributions", ""]

    for agent, train_rel, eval_rel, schema_cls in AGENTS:
        train_path = MODEL_TRAINING_ROOT / train_rel
        eval_path = MODEL_TRAINING_ROOT / eval_rel

        tr_stats, tr_secrets, tr_dist = _validate_file(agent, train_path, schema_cls)
        ev_stats, ev_secrets, ev_dist = _validate_file(agent, eval_path, schema_cls)

        tr_prompts = {json.loads(ln)["prompt"] for ln in train_path.read_text(encoding="utf-8").splitlines() if ln.strip()}
        ev_prompts = {json.loads(ln)["prompt"] for ln in eval_path.read_text(encoding="utf-8").splitlines() if ln.strip()}
        overlap = len(tr_prompts & ev_prompts)
        if overlap != 0:
            raise RuntimeError(f"Train/eval overlap detected for {agent}: {overlap}")
        if tr_secrets != 0 or ev_secrets != 0:
            raise RuntimeError(f"Unscrubbed secret detected for {agent}: train={tr_secrets}, eval={ev_secrets}")

        for split_label, rel, st, sec in (
            ("train", train_rel, tr_stats, tr_secrets),
            ("eval", eval_rel, ev_stats, ev_secrets),
        ):
            p = st["prompt"]
            c = st["completion"]
            md_lines.append(
                f"| `{agent}` | {split_label} | `model-training/{rel}` | {st['rows']:,} | {st['total_tokens']:,} | "
                f"{p['mean']} ({p['p95']} / {p['max']}) | {c['mean']} ({c['p95']} / {c['max']}) | {sec} | {overlap} |"
            )
            summary_rows.append({"agent": agent, "split": split_label, "stats": st, "overlap": overlap})

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
