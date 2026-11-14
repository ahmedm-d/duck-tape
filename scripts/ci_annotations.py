#!/usr/bin/env python3
"""Print GitHub workflow-command annotations for a failed acceptance run.

Job logs and artifacts need an authenticated GitHub account but check-run
annotations are public, so failed gates and tests are surfaced as ``::error``
commands. Dependency-free; always exits 0 and never prints environment values.

Usage: python scripts/ci_annotations.py data/acceptance/ci
"""
from __future__ import annotations

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

MAX_ANNOTATIONS = 10  # GitHub shows at most 10 errors per step
MAX_MESSAGE_LINES = 5
MAX_MESSAGE_CHARS = 800
MAX_SUMMARY_ROWS = 50
FAILED_STATUSES = {"failed", "blocked"}
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def escape_data(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(value: str) -> str:
    return escape_data(value).replace(":", "%3A").replace(",", "%2C")


def command(level: str, message: str, **properties: object) -> str:
    props = ",".join(f"{key}={escape_property(str(value))}" for key, value in properties.items() if value not in (None, ""))
    return f"::{level} {props}::{escape_data(message)}" if props else f"::{level}::{escape_data(message)}"


def trim(text: str) -> str:
    lines = [line.rstrip() for line in ANSI.sub("", text or "").strip().splitlines() if line.strip()]
    return "\n".join(lines[:MAX_MESSAGE_LINES])[:MAX_MESSAGE_CHARS]


def latest_report(directory: Path) -> Path | None:
    candidates = [directory / "report.json"] + [path / "report.json" for path in directory.iterdir() if path.is_dir()]
    found = [path for path in candidates if path.is_file()]
    return max(found, key=lambda path: (path.stat().st_mtime, str(path))) if found else None


def junit_path(classname: str) -> str:
    parts = []
    for part in classname.split("."):
        if part[:1].isupper():
            break
        parts.append(part)
    return "/".join(parts) + ".py" if parts else ""


def junit_failures(path: Path) -> list[dict]:
    failures = []
    for case in ET.parse(path).iter("testcase"):
        node = case.find("failure")
        kind = "failure"
        if node is None:
            node, kind = case.find("error"), "error"
        if node is None:
            continue
        classname, name = case.get("classname", ""), case.get("name", "")
        message = trim("\n".join(part for part in (node.get("message", ""), node.text or "") if part))
        failures.append({"id": f"{classname}::{name}" if classname else name, "file": case.get("file") or junit_path(classname),
                         "line": case.get("line", ""), "message": message or f"test {kind}", "source": path.stem})
    return failures


def playwright_failures(path: Path) -> list[dict]:
    failures = []

    def walk(suite: dict, parents: list[str]) -> None:
        titles = parents + ([suite["title"]] if suite.get("title") else [])
        for spec in suite.get("specs", []):
            for test in spec.get("tests", []):
                for result in test.get("results", []):
                    if result.get("status") in ("failed", "timedOut", "interrupted"):
                        error = result.get("error") or (result.get("errors") or [{}])[0]
                        failures.append({"id": " > ".join(titles[1:] + [spec.get("title", "")]) if len(titles) > 1 else spec.get("title", ""),
                                         "file": "frontend/tests/" + spec.get("file", "") if spec.get("file") else "",
                                         "line": spec.get("line", ""), "message": trim(error.get("message", "")) or str(result.get("status")),
                                         "source": path.stem})
                        break
        for child in suite.get("suites", []):
            walk(child, titles)

    for suite in json.loads(path.read_text(encoding="utf-8")).get("suites", []):
        walk(suite, [])
    return failures


def collect(report_path: Path) -> tuple[list[dict], list[dict]]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") == "passed":
        return [], []
    gates, tests = [], []
    for gate in report.get("results", []):
        if not (gate.get("required") and gate.get("status") in FAILED_STATUSES):
            continue
        detail = gate.get("reason") or f"exit code {gate.get('exit_code')}"
        counts = gate.get("counts")
        if isinstance(counts, dict):
            detail += ", " + ", ".join(f"{key}={value}" for key, value in counts.items() if isinstance(value, int))
        gates.append({"id": str(gate.get("id", "unknown")), "status": gate["status"], "message": detail})
    directory = report_path.parent
    for path in sorted(directory.glob("*.xml")):
        try:
            tests.extend(junit_failures(path))
        except (OSError, ET.ParseError):
            pass
    for path in sorted(directory.glob("*.json")):
        if path.name == "report.json":
            continue
        try:
            tests.extend(playwright_failures(path))
        except (OSError, ValueError, AttributeError, TypeError):
            pass
    if not gates and not tests:
        gates.append({"id": "acceptance", "status": str(report.get("status")), "message": "report is not passed"})
    return gates, tests


def summary_markdown(gates: list[dict], tests: list[dict]) -> str:
    def cell(value: str) -> str:
        return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")[:200]

    lines = ["### Acceptance failures", "", "| Kind | Name | Status | Detail |", "| --- | --- | --- | --- |"]
    lines += [f"| gate | {cell(gate['id'])} | {cell(gate['status'])} | {cell(gate['message'])} |" for gate in gates]
    lines += [f"| test | {cell(test['id'])} | failed | {cell(test['message'])} |" for test in tests[:MAX_SUMMARY_ROWS]]
    if len(tests) > MAX_SUMMARY_ROWS:
        lines.append(f"| test | ... | failed | {len(tests) - MAX_SUMMARY_ROWS} more not listed |")
    return "\n".join(lines) + "\n"


def run(directory: Path) -> list[str]:
    if not directory.is_dir():
        return [command("notice", f"No acceptance output directory at {directory}; nothing to annotate")]
    report_path = latest_report(directory)
    if report_path is None:
        return [command("notice", f"No acceptance report.json under {directory}; nothing to annotate")]
    gates, tests = collect(report_path)
    if not gates and not tests:
        return []
    entries = [command("error", gate["message"], title=f"{gate['id']} ({gate['status']})") for gate in gates]
    entries += [command("error", test["message"], file=test["file"], line=test["line"], title=test["id"]) for test in tests]
    lines = entries[:MAX_ANNOTATIONS]
    if len(entries) > MAX_ANNOTATIONS:
        lines.append(command("notice", f"{len(entries) - MAX_ANNOTATIONS} more failures not annotated; see the job summary"))
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        try:
            with open(target, "a", encoding="utf-8") as stream:
                stream.write(summary_markdown(gates, tests))
        except OSError:
            pass
    return lines


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        lines = run(Path(args[0] if args else "data/acceptance/ci"))
    except Exception as exc:  # annotations must never mask the real failure
        lines = [command("notice", f"Could not build failure annotations: {type(exc).__name__}")]
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
