"""Wave 2 Data Prep — Agent 7: reviewer (PreferenceRow for DPO)."""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from typing import Any

from datasets import load_dataset
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from training.schemas import PreferenceRow
from training.scrub import scrub_with_count
from training.utils import (
    MODEL_TRAINING_ROOT,
    extract_diff_paths,
    extract_diff_symbols,
    load_training_env,
    split_train_eval,
    truncate_bounded,
    write_preference_jsonl,
)

TRAIN_TARGET = 5000
EVAL_TARGET = 200
TOTAL_TARGET = TRAIN_TARGET + EVAL_TARGET

_HUNK_LINE_RE = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@", re.MULTILINE)
_GENERIC_IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{3,40})\b")


def _extract_hunk_lines(diff_text: str) -> tuple[int, int]:
    """Return (start_line, end_line) from the first unified/hunk header if present."""
    match = _HUNK_LINE_RE.search(diff_text or "")
    if not match:
        return 1, 20
    start = int(match.group(3))
    count = int(match.group(4) or 10)
    return start, start + max(1, count)


def _extract_prompt_symbols(diff_text: str, context_text: str) -> list[str]:
    syms = extract_diff_symbols(diff_text, limit=6)
    if syms:
        return syms
    found: list[str] = []
    seen: set[str] = set()
    stop = {
        "return",
        "import",
        "from",
        "class",
        "const",
        "function",
        "self",
        "none",
        "true",
        "false",
        "else",
        "elif",
        "while",
        "break",
        "continue",
        "public",
        "private",
        "static",
        "void",
        "this",
        "that",
        "with",
        "into",
    }
    for tok in _GENERIC_IDENT_RE.findall(diff_text or "") + _GENERIC_IDENT_RE.findall(context_text or ""):
        if tok.lower() in stop or tok.isdigit():
            continue
        if tok not in seen:
            seen.add(tok)
            found.append(tok)
            if len(found) >= 6:
                break
    return found or ["target_function"]


def _build_reviewer_pair(
    idx: int,
    source_name: str,
    repo: str,
    file_path: str,
    diff_text: str,
    caller_context: str,
    review_comment: str,
) -> tuple[PreferenceRow, int] | None:
    c_diff, r1 = scrub_with_count(diff_text)
    c_ctx, r2 = scrub_with_count(caller_context)
    c_comment, r3 = scrub_with_count(review_comment)
    redactions = r1 + r2 + r3

    clean_comment = " ".join(c_comment.split()).strip()
    if len(clean_comment) < 20 or len(c_diff.strip()) < 30:
        return None
    # Skip trivial one-word acknowledgments
    if clean_comment.lower().rstrip(".!") in {"lgtm", "looks good", "looks good to me", "nice", "thanks", "nit"}:
        return None

    paths = extract_diff_paths(c_diff, limit=4)
    display_path = file_path or (paths[0] if paths else "src/module.py")
    symbols = _extract_prompt_symbols(c_diff, c_ctx)
    start_ln, end_ln = _extract_hunk_lines(c_diff)
    primary_sym = symbols[0]
    secondary_sym = symbols[1] if len(symbols) > 1 else primary_sym

    prompt = (
        "Perform a grounded Steward code review of this diff and caller context.\n"
        "Cite only symbols, file paths, and line ranges present in the diff; flag concrete logic defects, "
        "boundary/error-handling gaps, missing edge-case tests, or architectural regressions.\n"
        f"Source: {source_name}\n"
        f"Repository: {repo}\n"
        f"File Path: {display_path}\n\n"
        f"Diff:\n```diff\n{truncate_bounded(c_diff, 2200)}\n```\n\n"
        f"Caller / Module Context:\n```\n{truncate_bounded(c_ctx, 1600)}\n```"
    )

    chosen = (
        f"### Findings\n"
        f"- **Grounded Defect / Risk in `{display_path}` (`lines {start_ln}-{end_ln}`, symbol `{primary_sym}`):** "
        f"{truncate_bounded(clean_comment, 500)}\n"
        f"- **Edge-Case & Caller Impact:** Verify how `{primary_sym}` interacts with `{secondary_sym}` across "
        f"boundary inputs (empty, null/None, or error states) in the shown hunk before merging.\n\n"
        f"### Cited Symbols & Lines\n"
        f"- File: `{display_path}` (hunk lines `{start_ln}-{end_ln}`)\n"
        f"- Symbols observed in diff/context: {', '.join(f'`{s}`' for s in symbols[:4])}\n\n"
        f"### Required Test / Fix Verification\n"
        f"- Add a targeted unit test covering `{primary_sym}` at the modified branch (`lines {start_ln}-{end_ln}`) "
        f"and re-run the module test suite."
    )

    # Rotate across 3 realistic hard-negative failure modes
    mode = idx % 3
    if mode == 0:
        # Mode 0: Style-only nitpick approving without checking logic
        rejected = (
            f"### Findings\n"
            f"- Consider renaming local variables to follow stricter formatting preferences and adding extra blank lines "
            f"around imports for readability. Otherwise LGTM — approved without additional test checks.\n\n"
            f"### Cited Symbols & Lines\n"
            f"- Formatting style pass across the file.\n\n"
            f"### Required Test / Fix Verification\n"
            f"- Run a linter/formatter check."
        )
    elif mode == 1:
        # Mode 1: Hallucinated line numbers & non-existent symbols
        fake_start = end_ln + 420
        fake_end = fake_start + 25
        rejected = (
            f"### Findings\n"
            f"- At `lines {fake_start}-{fake_end}`, `LegacyOrchestratorCache.flush_all()` introduces a race condition "
            f"when `DistributedLockManager.acquire_sync()` times out. Also verify the SQL transaction commit at line {fake_end + 19}.\n\n"
            f"### Cited Symbols & Lines\n"
            f"- `LegacyOrchestratorCache.flush_all` (`lines {fake_start}-{fake_end}`)\n"
            f"- `DistributedLockManager.acquire_sync` (`line {fake_end + 19}`)\n\n"
            f"### Required Test / Fix Verification\n"
            f"- Rewrite `DistributedLockManager` connection pooling before merging."
        )
    else:
        # Mode 2: Unverified blanket architectural rewrite claim
        rejected = (
            f"### Findings\n"
            f"- This entire module looks tightly coupled and will likely cause severe memory leaks under load. "
            f"Please refactor the subsystem into an async event-bus microservice architecture instead of this patch.\n\n"
            f"### Cited Symbols & Lines\n"
            f"- General architectural concern across `{display_path}`.\n\n"
            f"### Required Test / Fix Verification\n"
            f"- Replace the module design and run full end-to-end benchmarks."
        )

    return PreferenceRow(prompt=prompt, chosen=chosen, rejected=rejected), redactions


def _infer_path_from_code(oldf: str, idx: int, lang: str = "") -> str:
    for line in (oldf or "").splitlines()[:15]:
        m = re.search(r"([A-Za-z0-9_./-]+\.(?:py|ts|tsx|js|jsx|java|go|cpp|c|h|rs|cs))", line)
        if m:
            return m.group(1)
    ext_map = {
        "py": "py",
        "python": "py",
        "js": "js",
        "javascript": "js",
        "ts": "ts",
        "typescript": "ts",
        "java": "java",
        "go": "go",
        "cpp": "cpp",
        "c": "c",
        "cs": "cs",
    }
    ext = ext_map.get((lang or "").lower(), "py")
    return f"src/module_{idx % 50}.{ext}"


def collect_rows() -> tuple[list[PreferenceRow], dict[str, int], int]:
    """Stream PR review diffs & comments from CodeReviewer and Python critique datasets."""
    rows: list[PreferenceRow] = []
    seen_sigs: set[str] = set()
    source_counts: dict[str, int] = {}
    total_redactions = 0

    def _add(source_key: str, item: tuple[PreferenceRow, int] | None, dedup_raw: str) -> bool:
        nonlocal total_redactions
        if item is None:
            return False
        row, red = item
        sig = hashlib.sha256(dedup_raw.encode("utf-8", errors="ignore")).hexdigest()[:24]
        if sig in seen_sigs:
            return False
        seen_sigs.add(sig)
        rows.append(row)
        source_counts[source_key] = source_counts.get(source_key, 0) + 1
        total_redactions += red
        return True

    # 1. fasterinnerlooper/codereviewer (train_generation + train_refinement)
    gen_quota = 2200
    ds_gen = load_dataset("fasterinnerlooper/codereviewer", "train_generation", split="train", streaming=True)
    pbar = tqdm(total=gen_quota, desc="reviewer <- codereviewer/train_generation", unit="rows")
    for idx, rec in enumerate(ds_gen):
        if source_counts.get("codereviewer_generation", 0) >= gen_quota:
            break
        if rec.get("y") == 0:
            continue
        patch = str(rec.get("patch") or "").strip()
        oldf = str(rec.get("oldf") or "").strip()
        msg = str(rec.get("msg") or "").strip()
        file_path = _infer_path_from_code(oldf, idx)
        built = _build_reviewer_pair(
            len(rows),
            "microsoft/CodeReviewer (train_generation)",
            "open-source/repo",
            file_path,
            patch,
            oldf,
            msg,
        )
        if _add("codereviewer_generation", built, f"{patch[:220]}|{msg[:120]}"):
            pbar.update(1)
    pbar.close()

    ref_quota = 1600
    ds_ref = load_dataset("fasterinnerlooper/codereviewer", "train_refinement", split="train", streaming=True)
    pbar = tqdm(total=ref_quota, desc="reviewer <- codereviewer/train_refinement", unit="rows")
    for idx, rec in enumerate(ds_ref):
        if source_counts.get("codereviewer_refinement", 0) >= ref_quota:
            break
        patch = str(rec.get("old_hunk") or rec.get("hunk") or "").strip()
        oldf = str(rec.get("oldf") or "").strip()
        msg = str(rec.get("comment") or "").strip()
        repo = str(rec.get("repo") or "open-source/repo").strip()
        lang = str(rec.get("lang") or "py").strip()
        file_path = _infer_path_from_code(oldf, idx, lang=lang)
        built = _build_reviewer_pair(
            len(rows),
            "microsoft/CodeReviewer (train_refinement)",
            repo,
            file_path,
            patch,
            oldf,
            msg,
        )
        if _add("codereviewer_refinement", built, f"{patch[:220]}|{msg[:120]}"):
            pbar.update(1)
    pbar.close()

    # 2. Dahoas/code-review-instruct-critique-revision-python
    dahoas_quota = min(1000, TOTAL_TARGET - len(rows))
    ds_crp = load_dataset("Dahoas/code-review-instruct-critique-revision-python", split="train", streaming=True)
    pbar = tqdm(total=dahoas_quota, desc="reviewer <- code-review-instruct-python", unit="rows")
    for idx, rec in enumerate(ds_crp):
        if source_counts.get("code_review_python", 0) >= dahoas_quota or len(rows) >= TOTAL_TARGET:
            break
        prompt_raw = str(rec.get("prompt") or "")
        resp_raw = str(rec.get("response") or "")
        comments = rec.get("comments") or []
        comment_body = ""
        if isinstance(comments, list) and comments:
            first_c = comments[0]
            if isinstance(first_c, dict):
                comment_body = str(first_c.get("body") or "").strip()
        clean_prompt_text = re.sub(r"<[^>]+>", " ", prompt_raw)
        clean_resp_text = re.sub(r"<[^>]+>", " ", resp_raw)
        review_msg = comment_body or clean_resp_text[:450]
        # Construct diff-like block from original prompt code + revised snippet
        code_lines = [ln for ln in clean_resp_text.splitlines() if ln.strip()][:25]
        if len(code_lines) < 3 or len(review_msg) < 25:
            continue
        pseudo_diff = "@@ -1,8 +1,8 @@\n" + "\n".join(f"+{ln}" for ln in code_lines[:12])
        built = _build_reviewer_pair(
            len(rows),
            "Dahoas/code-review-instruct-critique-revision-python",
            "python/code-review",
            f"src/review_{idx % 40}.py",
            pseudo_diff,
            clean_prompt_text[:1200],
            review_msg,
        )
        if _add("code_review_python", built, f"{clean_prompt_text[:180]}|{review_msg[:120]}"):
            pbar.update(1)
    pbar.close()

    # 3. Top up any remainder from codereviewer/train_generation stream (past first 2200)
    if len(rows) < TOTAL_TARGET:
        rem = TOTAL_TARGET - len(rows)
        ds_gen_extra = load_dataset("fasterinnerlooper/codereviewer", "train_generation", split="train", streaming=True)
        pbar = tqdm(total=rem, desc="reviewer <- codereviewer (top-up)", unit="rows")
        for idx, rec in enumerate(ds_gen_extra):
            if len(rows) >= TOTAL_TARGET:
                break
            if rec.get("y") == 0:
                continue
            patch = str(rec.get("patch") or "").strip()
            oldf = str(rec.get("oldf") or "").strip()
            msg = str(rec.get("msg") or "").strip()
            file_path = _infer_path_from_code(oldf, idx)
            built = _build_reviewer_pair(
                len(rows),
                "microsoft/CodeReviewer (train_generation)",
                "open-source/repo",
                file_path,
                patch,
                oldf,
                msg,
            )
            if _add("codereviewer_topup", built, f"{patch[:220]}|{msg[:120]}"):
                pbar.update(1)
        pbar.close()

    return rows, source_counts, total_redactions


def main() -> int:
    load_training_env()
    rows, source_counts, redactions = collect_rows()
    train_rows, eval_rows = split_train_eval(rows, TRAIN_TARGET, EVAL_TARGET, seed=42)

    train_path = MODEL_TRAINING_ROOT / "data" / "processed" / "reviewer_dpo.jsonl"
    eval_dpo_path = MODEL_TRAINING_ROOT / "data" / "eval" / "reviewer_dpo_eval.jsonl"
    eval_alias_path = MODEL_TRAINING_ROOT / "data" / "eval" / "reviewer_eval.jsonl"
    write_preference_jsonl(train_rows, train_path)
    write_preference_jsonl(eval_rows, eval_dpo_path)
    write_preference_jsonl(eval_rows, eval_alias_path)

    print(
        f"[reviewer] wrote {len(train_rows)} train -> {train_path} | "
        f"{len(eval_rows)} eval -> {eval_dpo_path} (and {eval_alias_path}) | "
        f"redactions={redactions} | sources={source_counts}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
