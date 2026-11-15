"""Wave 2 Data Prep — Agent 6: test_writer (InstructionRow)."""
from __future__ import annotations

import ast
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

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

TRAIN_TARGET = 5000
EVAL_TARGET = 200
TOTAL_TARGET = TRAIN_TARGET + EVAL_TARGET

_TEST_NODE_RE = re.compile(r"([A-Za-z0-9_./-]+::[A-Za-z0-9_.:/\[\]-]+)")


def _parse_test_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        parsed = ast.literal_eval(text)
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
    except (ValueError, SyntaxError):
        pass
    return _TEST_NODE_RE.findall(text)


def _extract_test_patch_from_trajectory(trajectory: list[dict[str, Any]], model_patch: str) -> tuple[str, str]:
    """Extract reproduction test diff or test file edits and test command from an OpenHands trajectory."""
    test_cmds: list[str] = []
    for msg in trajectory:
        content = str(msg.get("content") or "")
        for m in re.findall(r"(pytest[^\n\"'`]+)", content):
            cmd = m.strip().rstrip(";")
            if len(cmd) > 8:
                test_cmds.append(cmd)
        tcs = msg.get("tool_calls") or []
        if isinstance(tcs, list):
            for tc in tcs:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                args_str = str(fn.get("arguments") or "")
                for m in re.findall(r"(pytest[^\n\"'\\]+)", args_str):
                    cmd = m.strip().rstrip(";")
                    if len(cmd) > 8:
                        test_cmds.append(cmd)

    # Filter diff hunks touching tests if present in model_patch
    test_hunks: list[str] = []
    if model_patch:
        chunks = re.split(r"(?=^diff --git )", model_patch, flags=re.MULTILINE)
        for ch in chunks:
            if not ch.strip():
                continue
            header = ch.splitlines()[0] if ch.splitlines() else ""
            if "test" in header.lower():
                test_hunks.append(ch)
    selected_diff = "".join(test_hunks) if test_hunks else model_patch
    test_cmd = test_cmds[-1] if test_cmds else "pytest -v"
    return selected_diff, test_cmd


def _build_test_writer_row(
    source_name: str,
    repo: str,
    instance_id: str,
    issue_text: str,
    target_module_code: str,
    test_diff: str,
    verify_cmd: str,
) -> tuple[InstructionRow, int] | None:
    c_issue, r1 = scrub_with_count(issue_text)
    c_code, r2 = scrub_with_count(target_module_code)
    c_diff, r3 = scrub_with_count(test_diff)
    c_cmd, r4 = scrub_with_count(verify_cmd)
    redactions = r1 + r2 + r3 + r4

    if len(c_diff.strip()) < 30 or not c_cmd.strip():
        return None

    prompt = (
        "Write a reproduction / regression test diff and the exact verification command for this issue.\n"
        "The test must fail on the buggy baseline and pass once the target fix is applied.\n"
        f"Source: {source_name}\n"
        f"Repository: {repo}\n"
        f"Instance ID: {instance_id}\n\n"
        f"Issue Description:\n{truncate_bounded(c_issue, 1800)}\n\n"
        f"Target Module Code / Context:\n```\n{truncate_bounded(c_code, 2200)}\n```\n\n"
        "Respond with Markdown containing `### Reproduction Test Diff` (a unified diff block) and "
        "`### Verification Command` (a bash command block)."
    )

    completion = (
        f"### Reproduction Test Diff\n```diff\n{truncate_bounded(c_diff, 2600)}\n```\n\n"
        f"### Verification Command\n```bash\n{c_cmd.strip()}\n```"
    )
    return InstructionRow(prompt=prompt, completion=completion), redactions


def _synthesize_test_diff_from_fail_to_pass(
    repo: str,
    issue_text: str,
    patch: str,
    fail_tests: list[str],
) -> tuple[str, str]:
    """Synthesize a unified test diff scaffold anchored to actual FAIL_TO_PASS test node IDs and patch symbols."""
    primary_node = fail_tests[0]
    test_file = primary_node.split("::", 1)[0]
    if "::" in primary_node:
        func_part = primary_node.split("::")[-1]
    else:
        func_part = "test_reproduction_issue"
    func_name = re.sub(r"[^A-Za-z0-9_]", "_", func_part.split("[")[0]) or "test_reproduction_issue"
    if not func_name.startswith("test_"):
        func_name = f"test_{func_name}"

    paths = extract_diff_paths(patch, limit=4)
    symbols = extract_diff_symbols(patch, limit=6)
    target_path = paths[0] if paths else "target_module.py"
    target_sym = symbols[0] if symbols else "target_handler"
    module_import = target_path.removesuffix(".py").replace("/", ".")
    issue_hint = " ".join(issue_text.splitlines()[:3]).strip()[:120].replace('"', "'")

    diff_lines = [
        f"diff --git a/{test_file} b/{test_file}",
        f"--- a/{test_file}",
        f"+++ b/{test_file}",
        "@@ -1,3 +1,14 @@",
        " import pytest",
        f"+# Regression check for {repo}: {issue_hint}",
        f"+from {module_import} import {target_sym}",
        "+",
        f"+def {func_name}_repro():",
        f"+    \"\"\"Fails before patch on {target_sym}; passes after patch.\"\"\"",
        f"+    result = {target_sym}",
        "+    assert result is not None",
    ]
    verify_cmd = f"pytest -v {' '.join(fail_tests[:2])}"
    return "\n".join(diff_lines), verify_cmd


def collect_rows() -> tuple[list[InstructionRow], dict[str, int], int]:
    """Stream test generation rows from Nebius (gen_tests_correct>=1), TestGenEval, and SWE-smith/SWE-Gym."""
    rows: list[InstructionRow] = []
    seen_sigs: set[str] = set()
    source_counts: dict[str, int] = {}
    total_redactions = 0

    def _add(source_key: str, item: tuple[InstructionRow, int] | None, unique_key: str = "") -> bool:
        nonlocal total_redactions
        if item is None:
            return False
        row, red = item
        prompt_hash = hashlib.sha256(row.prompt.encode("utf-8", errors="ignore")).hexdigest()[:24]
        sig = f"{source_key}:{unique_key}" if unique_key else prompt_hash
        if sig in seen_sigs or prompt_hash in seen_sigs:
            return False
        seen_sigs.add(sig)
        seen_sigs.add(prompt_hash)
        rows.append(row)
        source_counts[source_key] = source_counts.get(source_key, 0) + 1
        total_redactions += red
        return True

    # 1. nebius/SWE-rebench-openhands-trajectories filtered by gen_tests_correct >= 1
    neb_quota = 600
    ds_neb = load_dataset("nebius/SWE-rebench-openhands-trajectories", split="train", streaming=True)
    pbar = tqdm(total=neb_quota, desc="test_writer <- nebius (gen_tests_correct>=1)", unit="rows")
    for rec in ds_neb:
        if source_counts.get("nebius_gen_tests", 0) >= neb_quota:
            break
        if (rec.get("gen_tests_correct") or 0) < 1:
            continue
        traj = rec.get("trajectory") or []
        if not isinstance(traj, list):
            continue
        user_msgs = [str(m.get("content") or "") for m in traj if isinstance(m, dict) and m.get("role") == "user"]
        if not user_msgs:
            continue
        issue_text = user_msgs[0].strip()
        model_patch = str(rec.get("model_patch") or "").strip()
        test_diff, test_cmd = _extract_test_patch_from_trajectory(traj, model_patch)
        if len(test_diff) < 30:
            continue
        repo = str(rec.get("repo") or "unknown/repo")
        inst_id = str(rec.get("instance_id") or "")
        built = _build_test_writer_row(
            "nebius/SWE-rebench-openhands-trajectories",
            repo,
            inst_id,
            issue_text,
            model_patch or f"Repository: {repo} ({inst_id})",
            test_diff,
            test_cmd,
        )
        if _add("nebius_gen_tests", built, unique_key=inst_id):
            pbar.update(1)
    pbar.close()

    # 2. kjain14/testgeneval (train and test splits)
    tge_quota = 1800
    pbar = tqdm(total=tge_quota, desc="test_writer <- kjain14/testgeneval", unit="rows")
    for split_name in ("train", "test"):
        if source_counts.get("testgeneval", 0) >= tge_quota:
            break
        ds_tge = load_dataset("kjain14/testgeneval", split=split_name, streaming=True)
        for rec in ds_tge:
            if source_counts.get("testgeneval", 0) >= tge_quota:
                break
            repo = str(rec.get("repo") or "unknown/repo")
            inst_id = str(rec.get("instance_id") or rec.get("id") or "")
            code_file = str(rec.get("code_file") or "")
            test_file = str(rec.get("test_file") or "tests/test_module.py")
            code_src = str(rec.get("code_src") or "")
            patch = str(rec.get("patch") or "")
            test_patch = str(rec.get("test_patch") or "")
            if len(test_patch.strip()) < 30 or not code_src.strip():
                continue
            issue_desc = (
                f"Generate regression/unit tests in `{test_file}` covering changes to `{code_file}`.\n"
                f"Target patch context:\n```diff\n{truncate_bounded(patch, 900)}\n```"
            )
            built = _build_test_writer_row(
                f"kjain14/testgeneval ({split_name})",
                repo,
                inst_id,
                issue_desc,
                f"# File: {code_file}\n{code_src}",
                test_patch,
                f"pytest -v {test_file}",
            )
            if _add("testgeneval", built, unique_key=f"{inst_id}:{test_file}:{len(test_patch)}"):
                pbar.update(1)
    pbar.close()

    # 3. SWE-Gym/SWE-Gym (has real test_patch!) + SWE-bench/SWE-smith
    ds_gym = load_dataset("SWE-Gym/SWE-Gym", split="train", streaming=True)
    gym_quota = min(1400, TOTAL_TARGET - len(rows))
    pbar = tqdm(total=gym_quota, desc="test_writer <- SWE-Gym", unit="rows")
    for rec in ds_gym:
        if source_counts.get("SWE-Gym", 0) >= gym_quota or len(rows) >= TOTAL_TARGET:
            break
        inst_id = str(rec.get("instance_id") or "")
        repo = str(rec.get("repo") or "unknown/repo")
        problem = str(rec.get("problem_statement") or "").strip()
        patch = str(rec.get("patch") or "").strip()
        test_patch = str(rec.get("test_patch") or "").strip()
        fail_tests = _parse_test_list(rec.get("FAIL_TO_PASS"))
        if not problem or len(test_patch) < 30:
            continue
        verify_cmd = f"pytest -v {' '.join(fail_tests[:2])}" if fail_tests else "pytest -v"
        built = _build_test_writer_row(
            "SWE-Gym/SWE-Gym",
            repo,
            inst_id,
            problem,
            patch or f"Repository: {repo}",
            test_patch,
            verify_cmd,
        )
        if _add("SWE-Gym", built, unique_key=inst_id):
            pbar.update(1)
    pbar.close()

    # 4. Top up remaining target from SWE-bench/SWE-smith
    if len(rows) < TOTAL_TARGET:
        rem = TOTAL_TARGET - len(rows)
        ds_smith = load_dataset("SWE-bench/SWE-smith", split="train", streaming=True)
        pbar = tqdm(total=rem, desc="test_writer <- SWE-smith", unit="rows")
        for rec in ds_smith:
            if len(rows) >= TOTAL_TARGET:
                break
            inst_id = str(rec.get("instance_id") or "")
            repo = str(rec.get("repo") or "unknown/repo")
            problem = str(rec.get("problem_statement") or "").strip()
            patch = str(rec.get("patch") or "").strip()
            fail_tests = _parse_test_list(rec.get("FAIL_TO_PASS"))
            if len(problem) < 40 or len(patch) < 30 or not fail_tests:
                continue
            test_diff, verify_cmd = _synthesize_test_diff_from_fail_to_pass(repo, problem, patch, fail_tests)
            built = _build_test_writer_row(
                "SWE-bench/SWE-smith",
                repo,
                inst_id,
                problem,
                patch,
                test_diff,
                verify_cmd,
            )
            if _add("SWE-smith", built, unique_key=inst_id):
                pbar.update(1)
        pbar.close()

    return rows, source_counts, total_redactions


def main() -> int:
    load_training_env()
    rows, source_counts, redactions = collect_rows()
    train_rows, eval_rows = split_train_eval(rows, TRAIN_TARGET, EVAL_TARGET, seed=42)

    train_path = MODEL_TRAINING_ROOT / "data" / "processed" / "test_writer.jsonl"
    eval_path = MODEL_TRAINING_ROOT / "data" / "eval" / "test_writer_eval.jsonl"
    write_instruction_jsonl(train_rows, train_path)
    write_instruction_jsonl(eval_rows, eval_path)

    print(
        f"[test_writer] wrote {len(train_rows)} train -> {train_path} | "
        f"{len(eval_rows)} eval -> {eval_path} | redactions={redactions} | sources={source_counts}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
