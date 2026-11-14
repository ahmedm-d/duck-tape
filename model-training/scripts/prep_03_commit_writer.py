"""Wave 1 Data Prep — Agent 3: commit_pr_writer."""
from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path

from datasets import load_dataset
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from training.schemas import InstructionRow
from training.scrub import scrub_with_count
from training.utils import (
    MODEL_TRAINING_ROOT,
    extract_diff_paths,
    extract_diff_symbols,
    load_training_env,
    split_train_eval,
    truncate_bounded,
    write_instruction_jsonl,
)

TRAIN_TARGET = 2500
EVAL_TARGET = 200
TOTAL_TARGET = TRAIN_TARGET + EVAL_TARGET

REQUIRED_HEADINGS = (
    "### Summary",
    "### Cited Symbols",
    "### Confidence Assessment",
    "### Risk Assessment",
)
NOISY_COMMIT_RE = re.compile(r"^(?:wip|merge|update|fix|tmp|test|bump|typo)\.?$", re.IGNORECASE)


def _make_unified_diff(old_file: str, new_file: str, old_text: str, new_text: str) -> str:
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    if len(old_lines) > 2500 or len(new_lines) > 2500:
        return ""
    diff_lines = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{old_file}",
            tofile=f"b/{new_file}",
            n=3,
        )
    )
    return "".join(diff_lines)


def _build_commit_row(
    source_name: str,
    repo: str,
    diff_text: str,
    commit_message: str,
) -> tuple[InstructionRow, int] | None:
    cleaned_diff, r1 = scrub_with_count(diff_text)
    cleaned_msg, r2 = scrub_with_count(commit_message)
    redactions = r1 + r2

    msg_lines = [line.strip() for line in cleaned_msg.splitlines() if line.strip()]
    if not msg_lines:
        return None
    subject = msg_lines[0]
    if len(subject) < 10 or NOISY_COMMIT_RE.fullmatch(subject):
        return None

    paths = extract_diff_paths(cleaned_diff, limit=10)
    if not paths:
        return None
    symbols = extract_diff_symbols(cleaned_diff, limit=10)

    hunks = cleaned_diff.count("@@ ")
    added_lines = sum(1 for ln in cleaned_diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
    removed_lines = sum(1 for ln in cleaned_diff.splitlines() if ln.startswith("-") and not ln.startswith("---"))
    touches_tests = any("test" in p.lower() or "spec" in p.lower() for p in paths)
    touches_config_or_core = any(
        any(tok in p.lower() for tok in ("config", "auth", "schema", "migration", "main", "index", "core", "api"))
        for p in paths
    )

    if touches_tests and symbols:
        confidence = (
            f"High — diff includes direct symbol edits ({', '.join(symbols[:3])}) alongside test coverage across "
            f"{len(paths)} file(s) and {max(1, hunks)} hunk(s)."
        )
    elif symbols:
        confidence = (
            f"Medium — diff clearly modifies {len(symbols)} named symbol(s) across {len(paths)} file(s) "
            f"(+{added_lines}/-{removed_lines} lines), though no test file diff is shown in this patch."
        )
    else:
        confidence = (
            f"Medium — localized diff across {len(paths)} file(s) (+{added_lines}/-{removed_lines} lines) "
            "affecting module-level statements or configuration without new test diffs."
        )

    if len(paths) >= 4 or touches_config_or_core or (added_lines + removed_lines) > 80:
        risk = (
            f"Medium-High — changes touch core or multi-file surfaces ({', '.join(paths[:3])}) with "
            f"+{added_lines}/-{removed_lines} lines across {max(1, hunks)} hunk(s); verify dependent callers and integration tests."
        )
    elif touches_tests and len(paths) <= 2:
        risk = (
            f"Low — narrow change scoped to {len(paths)} file(s) with direct test updates "
            f"(+{added_lines}/-{removed_lines} lines)."
        )
    else:
        risk = (
            f"Low-Medium — bounded edit to {len(paths)} file(s) (+{added_lines}/-{removed_lines} lines); "
            "run targeted unit tests for modified symbols before merging."
        )

    extra_body = " ".join(msg_lines[1:4]).strip()
    summary_text = f"{subject}." if not subject.endswith(".") else subject
    if extra_body:
        summary_text = f"{summary_text} {truncate_bounded(extra_body, 360)}"

    cited_items = [f"- `{p}`" for p in paths[:6]]
    if symbols:
        cited_items.append(f"- Modified/added symbols: {', '.join(f'`{s}`' for s in symbols[:8])}")
    else:
        cited_items.append("- Module-level or configuration lines within the listed file path(s)")

    completion = (
        f"### Summary\n{summary_text}\n\n"
        f"### Cited Symbols\n" + "\n".join(cited_items) + "\n\n"
        f"### Confidence Assessment\n{confidence}\n\n"
        f"### Risk Assessment\n{risk}"
    )
    for heading in REQUIRED_HEADINGS:
        if heading not in completion:
            raise ValueError(f"Missing required heading: {heading}")

    prompt = (
        "Write a grounded PR / commit evidence summary in Markdown for the following git diff.\n"
        f"Source: {source_name}\n"
        f"Repository: {repo}\n"
        f"Changed Files: {', '.join(paths)}\n\n"
        f"Git Diff:\n```diff\n{truncate_bounded(cleaned_diff, 3000)}\n```\n\n"
        "Include these exact Markdown sections: ### Summary, ### Cited Symbols, "
        "### Confidence Assessment, and ### Risk Assessment."
    )
    return InstructionRow(prompt=prompt, completion=completion), redactions


def collect_rows() -> tuple[list[InstructionRow], dict[str, int], int]:
    """Stream Python/TS commits from commitpackft and diffs from commitbench."""
    rows: list[InstructionRow] = []
    seen_diffs: set[str] = set()
    source_counts: dict[str, int] = {}
    total_redactions = 0

    def _add(source_key: str, item: tuple[InstructionRow, int] | None) -> bool:
        nonlocal total_redactions
        if item is None:
            return False
        row, red = item
        sig = row.prompt[:240]
        if sig in seen_diffs:
            return False
        seen_diffs.add(sig)
        rows.append(row)
        source_counts[source_key] = source_counts.get(source_key, 0) + 1
        total_redactions += red
        return True

    # 1. bigcode/commitpackft (Python & TypeScript via json builder)
    for lang, quota in (("python", 950), ("typescript", 950)):
        ds_cp = load_dataset(
            "json",
            data_files=f"hf://datasets/bigcode/commitpackft/data/{lang}/data.jsonl",
            split="train",
            streaming=True,
        )
        pbar = tqdm(total=quota, desc=f"commit_pr_writer <- commitpackft/{lang}", unit="rows")
        for rec in ds_cp:
            if source_counts.get(f"commitpackft_{lang}", 0) >= quota:
                break
            old_file = str(rec.get("old_file") or rec.get("new_file") or "")
            new_file = str(rec.get("new_file") or old_file)
            old_contents = str(rec.get("old_contents") or "")
            new_contents = str(rec.get("new_contents") or "")
            if not new_file or old_contents == new_contents:
                continue
            diff_text = _make_unified_diff(old_file, new_file, old_contents, new_contents)
            if len(diff_text) < 40:
                continue
            msg = str(rec.get("message") or rec.get("subject") or "").strip()
            repos_raw = str(rec.get("repos") or "open-source/repo")
            repo = repos_raw.split(",")[0].strip() or "open-source/repo"
            built = _build_commit_row(f"bigcode/commitpackft ({lang})", repo, diff_text, msg)
            if _add(f"commitpackft_{lang}", built):
                pbar.update(1)
        pbar.close()

    # 2. Maxscha/commitbench
    cb_quota = TOTAL_TARGET - len(rows)
    ds_cb = load_dataset("Maxscha/commitbench", split="train", streaming=True)
    pbar = tqdm(total=cb_quota, desc="commit_pr_writer <- Maxscha/commitbench", unit="rows")
    for rec in ds_cb:
        if len(rows) >= TOTAL_TARGET:
            break
        diff_langs = str(rec.get("diff_languages") or "").lower()
        if diff_langs and not any(lang in diff_langs.split(",") for lang in ("py", "ts", "tsx", "js", "jsx")):
            continue
        diff_text = str(rec.get("diff") or "")
        msg = str(rec.get("message") or "").strip()
        if len(diff_text) < 40 or len(diff_text) > 12000:
            continue
        repo = str(rec.get("project") or "unknown/project")
        built = _build_commit_row("Maxscha/commitbench", repo, diff_text, msg)
        if _add("commitbench", built):
            pbar.update(1)
    pbar.close()

    return rows, source_counts, total_redactions


def main() -> int:
    load_training_env()
    rows, source_counts, redactions = collect_rows()
    train_rows, eval_rows = split_train_eval(rows, TRAIN_TARGET, EVAL_TARGET, seed=42)

    train_path = MODEL_TRAINING_ROOT / "data" / "processed" / "commit_pr_writer.jsonl"
    eval_path = MODEL_TRAINING_ROOT / "data" / "eval" / "commit_pr_writer_eval.jsonl"
    write_instruction_jsonl(train_rows, train_path)
    write_instruction_jsonl(eval_rows, eval_path)

    print(
        f"[commit_pr_writer] wrote {len(train_rows)} train -> {train_path} | "
        f"{len(eval_rows)} eval -> {eval_path} | redactions={redactions} | sources={source_counts}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
