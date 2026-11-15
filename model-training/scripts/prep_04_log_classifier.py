"""Wave 1 Data Prep — Agent 4: log_classifier."""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

from datasets import load_dataset
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from training.schemas import InstructionRow, LogClassifierCompletion
from training.scrub import scrub_with_count
from training.utils import (
    MODEL_TRAINING_ROOT,
    load_training_env,
    split_train_eval,
    truncate_bounded,
    write_instruction_jsonl,
)

TRAIN_TARGET = 5000
EVAL_TARGET = 200
TOTAL_TARGET = TRAIN_TARGET + EVAL_TARGET

LOGHUB_FILES = (
    ("Apache", "https://raw.githubusercontent.com/logpai/loghub/master/Apache/Apache_2k.log_structured.csv"),
    ("Linux", "https://raw.githubusercontent.com/logpai/loghub/master/Linux/Linux_2k.log_structured.csv"),
    ("HDFS", "https://raw.githubusercontent.com/logpai/loghub/master/HDFS/HDFS_2k.log_structured.csv"),
    ("Spark", "https://raw.githubusercontent.com/logpai/loghub/master/Spark/Spark_2k.log_structured.csv"),
    ("Hadoop", "https://raw.githubusercontent.com/logpai/loghub/master/Hadoop/Hadoop_2k.log_structured.csv"),
    ("Zookeeper", "https://raw.githubusercontent.com/logpai/loghub/master/Zookeeper/Zookeeper_2k.log_structured.csv"),
    ("OpenSSH", "https://raw.githubusercontent.com/logpai/loghub/master/OpenSSH/OpenSSH_2k.log_structured.csv"),
    ("BGL", "https://raw.githubusercontent.com/logpai/loghub/master/BGL/BGL_2k.log_structured.csv"),
)

IDOFT_FILES = (
    ("python-pytest", "https://raw.githubusercontent.com/TestingResearchIllinois/idoft/main/py-data.csv"),
    ("java-maven", "https://raw.githubusercontent.com/TestingResearchIllinois/idoft/main/pr-data.csv"),
)

_TEST_NAME_RE = re.compile(r"([A-Za-z0-9_./-]+::[A-Za-z0-9_.:/\[\]-]+)")


def _make_row(
    source_name: str,
    log_body: str,
    exit_code: int,
    failure_category: str,
    failing_tests: list[str],
    root_cause_summary: str,
    recommended_action: str,
) -> tuple[InstructionRow, int]:
    cleaned_log, r1 = scrub_with_count(log_body)
    cleaned_cause, r2 = scrub_with_count(root_cause_summary)
    cleaned_action, r3 = scrub_with_count(recommended_action)
    redactions = r1 + r2 + r3

    completion_obj = LogClassifierCompletion(
        failure_category=failure_category,
        failing_tests=failing_tests[:10],
        root_cause_summary=cleaned_cause,
        recommended_action=cleaned_action,
    )
    completion_str = json.dumps(completion_obj.model_dump(), ensure_ascii=False)
    LogClassifierCompletion.model_validate(json.loads(completion_str))

    prompt = (
        "Classify the failure category and root cause of this bounded build/test log.\n"
        f"Source: {source_name}\n"
        f"Exit Code: {exit_code}\n\n"
        f"Log Output:\n```\n{truncate_bounded(cleaned_log, 2600)}\n```\n\n"
        "Respond with strict JSON matching: "
        '{"failure_category": "regression|baseline_failure|flaky_test|missing_dependency|syntax_error|sandbox_blocked", '
        '"failing_tests": list[str], "root_cause_summary": str, "recommended_action": str}.'
    )
    return InstructionRow(prompt=prompt, completion=completion_str), redactions


def _classify_loghub_window(system_name: str, lines: list[str]) -> tuple[str, int, str, str]:
    joined = "\n".join(lines)
    low = joined.lower()
    if any(
        kw in low
        for kw in (
            "permission denied",
            "authentication failure",
            "refused",
            "connection reset",
            "unreachable",
            "timed out",
            "timeout",
            "out of memory",
            "oom",
            "killed",
            "socket",
            "network",
            "read-only",
        )
    ):
        exit_code = 137 if ("oom" in low or "killed" in low) else 126 if "permission denied" in low else 1
        return (
            "sandbox_blocked",
            exit_code,
            f"{system_name} execution failed due to environment, network, permission, or resource isolation restriction.",
            "Check sandbox network/permission constraints or resource limits rather than modifying application logic.",
        )
    if any(
        kw in low
        for kw in (
            "no such file",
            "not found",
            "missing",
            "modulenotfound",
            "importerror",
            "cannot find",
            "failed to load",
            "could not open",
            "library",
        )
    ):
        return (
            "missing_dependency",
            127,
            f"{system_name} failed because a required runtime library, configuration file, or dependency artifact was unavailable.",
            "Verify pinned dependencies, package artifacts, and runtime environment paths before re-running.",
        )
    if any(
        kw in low
        for kw in (
            "syntax",
            "parse error",
            "invalid configuration",
            "malformed",
            "unexpected token",
            "illegalargument",
        )
    ):
        return (
            "syntax_error",
            2,
            f"{system_name} aborted due to malformed configuration, command syntax, or parse error.",
            "Inspect the referenced syntax/configuration token and validate against schema before retrying.",
        )
    return (
        "baseline_failure",
        1,
        f"{system_name} reported pre-existing service or runtime errors across logged components during baseline execution.",
        "Record baseline failure evidence separately from new patch regressions and inspect component trace.",
    )


def _format_loghub_line(rec: dict) -> str:
    ts = " ".join(
        str(rec.get(k))
        for k in ("Month", "Date", "Time")
        if rec.get(k) not in (None, "")
    ).strip()
    level = str(rec.get("Level") or "INFO").strip()
    comp = str(rec.get("Component") or "").strip()
    content = str(rec.get("Content") or "").strip()
    prefix = f"[{ts}] " if ts else ""
    comp_tag = f"{comp}: " if comp else ""
    return f"{prefix}{level.upper()} {comp_tag}{content}".strip()


def _collect_loghub(quota: int) -> tuple[list[InstructionRow], int]:
    rows: list[InstructionRow] = []
    seen: set[str] = set()
    redactions = 0
    per_system = max(150, (quota // len(LOGHUB_FILES)) + 30)
    pbar = tqdm(total=quota, desc="log_classifier <- logpai/loghub", unit="rows")

    for system_name, url in LOGHUB_FILES:
        if len(rows) >= quota:
            break
        ds = load_dataset("csv", data_files=url, split="train", streaming=True)
        buffer: list[str] = []
        sys_count = 0
        for rec in ds:
            line = _format_loghub_line(rec)
            if not line:
                continue
            buffer.append(line)
            if len(buffer) > 10:
                buffer.pop(0)
            low = line.lower()
            is_signal = any(
                tok in low
                for tok in (
                    "error",
                    "fail",
                    "fatal",
                    "exception",
                    "warn",
                    "refused",
                    "denied",
                    "timeout",
                    "timed out",
                    "not found",
                    "missing",
                    "invalid",
                    "killed",
                    "unreachable",
                    "corrupt",
                )
            )
            if len(buffer) >= 6 and (is_signal or len(buffer) == 10 and sys_count < 30):
                cat, exit_code, cause, action = _classify_loghub_window(system_name, buffer)
                log_text = f"Runner: {system_name} service/check log\n" + "\n".join(buffer)
                sig = log_text[:200]
                if sig not in seen:
                    seen.add(sig)
                    row, red = _make_row(
                        f"logpai/loghub ({system_name})",
                        log_text,
                        exit_code,
                        cat,
                        [],
                        cause,
                        action,
                    )
                    rows.append(row)
                    redactions += red
                    sys_count += 1
                    pbar.update(1)
                    buffer = buffer[-2:]
                    if len(rows) >= quota or sys_count >= per_system:
                        break
    pbar.close()
    return rows, redactions


def _collect_idoft(quota: int) -> tuple[list[InstructionRow], int]:
    rows: list[InstructionRow] = []
    seen: set[str] = set()
    redactions = 0
    category_desc = {
        "OD": "order-dependent test pollution from shared global or filesystem state",
        "ID": "implementation-dependent assertion on nondeterministic iteration or ordering",
        "NIO": "non-idempotent execution leaving persistent state across runs",
        "NOD": "nondeterministic timing, concurrency, or random seed sensitivity",
        "UD": "underdetermined execution order or external environment timing dependency",
        "BR": "brittle test dependency on external or resource ordering",
    }
    pbar = tqdm(total=quota, desc="log_classifier <- idoft", unit="rows")

    for runner, url in IDOFT_FILES:
        if len(rows) >= quota:
            break
        ds = load_dataset("csv", data_files=url, split="train", streaming=True)
        for rec in ds:
            if len(rows) >= quota:
                break
            project_url = str(rec.get("Project URL") or "").strip()
            sha = str(rec.get("SHA Detected") or "")[:12]
            cat_code = str(rec.get("Category") or "NOD").strip()
            notes = str(rec.get("Notes") or "").strip()
            test_name = str(
                rec.get("Pytest Test Name (PathToFile::TestClass::TestMethod or PathToFile::TestMethod)")
                or rec.get("Fully-Qualified Test Name (packageName.ClassName.methodName)")
                or ""
            ).strip()
            if not test_name or test_name in seen:
                continue
            seen.add(test_name)

            reason = category_desc.get(cat_code.upper(), f"intermittent {cat_code} flakiness observed across repeated runs")
            if runner == "python-pytest":
                log_body = (
                    f"$ pytest -v {test_name}\n"
                    f"Project: {project_url} @ {sha}\n"
                    f"FAILED {test_name} - AssertionError: intermittent failure ({cat_code})\n"
                    f"Re-run note: test passed in isolation but failed under suite ordering / repeated CI runs.\n"
                    + (f"Diagnostic: {notes}\n" if notes and notes.lower() != "nan" else "")
                    + "=== 1 failed in 1.42s ==="
                )
            else:
                module = str(rec.get("Module Path") or ".").strip()
                log_body = (
                    f"$ mvn test -pl {module} -Dtest={test_name}\n"
                    f"Project: {project_url} @ {sha}\n"
                    f"[ERROR] Failures:\n"
                    f"[ERROR]   {test_name} -- Flaky category={cat_code}\n"
                    f"[ERROR] Tests run: 1, Failures: 1, Errors: 0, Skipped: 0\n"
                    + (f"[INFO] Notes: {notes}\n" if notes and notes.lower() != "nan" else "")
                    + "[ERROR] BUILD FAILURE"
                )

            row, red = _make_row(
                f"TestingResearchIllinois/idoft ({runner})",
                log_body,
                1,
                "flaky_test",
                [test_name],
                f"Test `{test_name}` exhibits {reason} (IDoFT category {cat_code}).",
                "Isolate shared state/ordering assumptions and re-run the test both in isolation and under randomized ordering.",
            )
            rows.append(row)
            redactions += red
            pbar.update(1)
    pbar.close()
    return rows, redactions


def _parse_test_list(raw: str) -> list[str]:
    text = (raw or "").strip()
    if not text:
        return []
    try:
        parsed = ast.literal_eval(text)
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
    except (ValueError, SyntaxError):
        pass
    return _TEST_NAME_RE.findall(text)


def _collect_swe_gym(quota: int) -> tuple[list[InstructionRow], int]:
    rows: list[InstructionRow] = []
    seen: set[str] = set()
    redactions = 0
    pbar = tqdm(total=quota, desc="log_classifier <- SWE-Gym / SWE-smith", unit="rows")

    sources = (
        ("SWE-Gym/SWE-Gym", "train"),
        ("SWE-bench/SWE-smith", "train"),
    )
    global_idx = 0
    for ds_name, split_name in sources:
        if len(rows) >= quota:
            break
        ds = load_dataset(ds_name, split=split_name, streaming=True)
        for rec in ds:
            if len(rows) >= quota:
                break
            instance_id = str(rec.get("instance_id") or "")
            if not instance_id or instance_id in seen:
                continue
            repo = str(rec.get("repo") or "unknown/repo")
            problem = str(rec.get("problem_statement") or "")
            fail_tests = _parse_test_list(str(rec.get("FAIL_TO_PASS") or ""))
            pass_tests = _parse_test_list(str(rec.get("PASS_TO_PASS") or ""))
            test_patch = str(rec.get("test_patch") or "")
            if not fail_tests and not pass_tests:
                continue
            seen.add(instance_id)

            prob_low = problem.lower()
            # Construct realistic bounded pytest traces tied to the actual repository tests
            mode = global_idx % 5
            global_idx += 1
            if mode == 0 or any(kw in prob_low for kw in ("importerror", "modulenotfound", "no module named", "dependency")):
                category = "missing_dependency"
                exit_code = 2
                target = fail_tests[:2] or pass_tests[:2]
                log_body = (
                    f"$ pytest -v {' '.join(target[:2])}\n"
                    f"Repository: {repo} ({instance_id})\n"
                    "ERROR collecting test module\n"
                    f"E   ModuleNotFoundError: missing optional or pinned package required by {target[0]}\n"
                    "!!! Interrupted: 1 error during collection !!!"
                )
                cause = f"Pytest collection aborted in {repo} due to an unresolved import or missing environment dependency."
                action = "Verify dependency installation and virtual environment/sandbox image packages before running tests."
            elif mode == 1 or any(kw in prob_low for kw in ("syntaxerror", "indentationerror", "invalid syntax")):
                category = "syntax_error"
                exit_code = 2
                target = fail_tests[:2] or pass_tests[:2]
                log_body = (
                    f"$ pytest -v {target[0]}\n"
                    f"Repository: {repo} ({instance_id})\n"
                    "ERROR collecting test module\n"
                    f"    def patched_handler(:\n"
                    "                       ^\n"
                    "SyntaxError: invalid syntax\n"
                    "!!! Interrupted: 1 error during collection !!!"
                )
                cause = f"Python parser rejected invalid syntax while importing modules referenced by `{target[0]}`."
                action = "Fix the syntax or AST structure error in the modified source/test file and re-run collection."
            elif mode == 2 or any(kw in prob_low for kw in ("permission", "timeout", "network", "sandbox", "socket")):
                category = "sandbox_blocked"
                exit_code = 126
                target = fail_tests[:2] or pass_tests[:2]
                log_body = (
                    f"$ pytest -v {target[0]}\n"
                    f"Repository: {repo} ({instance_id})\n"
                    f"FAILED {target[0]} - PermissionError / ConnectionRefusedError: outbound network and host writes disabled in sandbox\n"
                    "=== 1 failed in 0.85s ==="
                )
                cause = f"Test `{target[0]}` attempted restricted network or filesystem access blocked by sandbox policy."
                action = "Mock external I/O or run within approved deterministic fixtures without outbound network calls."
            elif mode == 3 and pass_tests:
                category = "regression"
                exit_code = 1
                target = pass_tests[:3]
                failures = "\n".join(f"FAILED {t} - AssertionError: unexpected post-patch behavior change" for t in target)
                log_body = (
                    f"$ pytest -v {' '.join(target)}\n"
                    f"Repository: {repo} ({instance_id})\n"
                    f"{failures}\n"
                    f"=== {len(target)} failed in 2.14s ==="
                )
                cause = f"Candidate patch broke {len(target)} previously passing test(s) in {repo}, indicating a regression."
                action = "Revise the candidate patch to preserve existing contract invariants and re-run the regression suite."
            else:
                category = "baseline_failure"
                exit_code = 1
                target = fail_tests[:4] or pass_tests[:2]
                failures = "\n".join(f"FAILED {t} - AssertionError: baseline defect reproduced prior to patch" for t in target)
                patch_hint = truncate_bounded(test_patch, 450) if test_patch else ""
                log_body = (
                    f"$ pytest -v {' '.join(target[:2])}\n"
                    f"Repository: {repo} ({instance_id})\n"
                    f"{failures}\n"
                    + (f"Test excerpt:\n{patch_hint}\n" if patch_hint else "")
                    + f"=== {len(target)} failed in 1.73s ==="
                )
                cause = f"Baseline failing test(s) reproduce the reported issue in {repo} prior to applying the repair patch."
                action = "Confirm the failing-before test matches the target bug, then proceed to patch implementation."

            row, red = _make_row(
                ds_name,
                log_body,
                exit_code,
                category,
                target,
                cause,
                action,
            )
            rows.append(row)
            redactions += red
            pbar.update(1)

    pbar.close()
    return rows, redactions


def collect_rows() -> tuple[list[InstructionRow], dict[str, int], int]:
    """Stream loghub, idoft, and SWE-Gym/SWE-smith failure records up to TOTAL_TARGET."""
    loghub_rows, r1 = _collect_loghub(1200)
    idoft_rows, r2 = _collect_idoft(1800)
    remaining = TOTAL_TARGET - len(loghub_rows) - len(idoft_rows)
    swegym_rows, r3 = _collect_swe_gym(remaining)

    all_rows = loghub_rows + idoft_rows + swegym_rows
    source_counts = {
        "logpai/loghub": len(loghub_rows),
        "TestingResearchIllinois/idoft": len(idoft_rows),
        "SWE-Gym+SWE-smith": len(swegym_rows),
    }
    return all_rows, source_counts, (r1 + r2 + r3)


def main() -> int:
    load_training_env()
    rows, source_counts, redactions = collect_rows()
    train_rows, eval_rows = split_train_eval(rows, TRAIN_TARGET, EVAL_TARGET, seed=42)

    train_path = MODEL_TRAINING_ROOT / "data" / "processed" / "log_classifier.jsonl"
    eval_path = MODEL_TRAINING_ROOT / "data" / "eval" / "log_classifier_eval.jsonl"
    write_instruction_jsonl(train_rows, train_path)
    write_instruction_jsonl(eval_rows, eval_path)

    print(
        f"[log_classifier] wrote {len(train_rows)} train -> {train_path} | "
        f"{len(eval_rows)} eval -> {eval_path} | redactions={redactions} | sources={source_counts}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
