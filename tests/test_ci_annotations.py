import json
import os
from pathlib import Path

from scripts import ci_annotations


def write_report(directory: Path, status="failed", results=None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "report.json"
    path.write_text(json.dumps({"status": status, "results": results or []}), encoding="utf-8")
    return path


def junit(directory: Path, cases: str, name="backend.xml") -> None:
    (directory / name).write_text(f'<?xml version="1.0"?><testsuites><testsuite>{cases}</testsuite></testsuites>', encoding="utf-8")


def gate(identity="backend", status="failed", **extra):
    return {"id": identity, "required": True, "status": status, "exit_code": 1, **extra}


def test_failed_gate_and_junit_failure_become_error_commands(tmp_path, capsys):
    run = tmp_path / "ci" / "20260101T000000Z-aaaa"
    write_report(run, results=[gate(counts={"total": 2, "failed": 1}), gate("frontend-build", status="passed")])
    junit(run, '<testcase classname="tests.test_api" name="test_x" line="42"><failure message="assert 1 == 2">trace line 1\ntrace line 2</failure></testcase>'
               '<testcase classname="tests.test_api" name="test_ok"/>')
    assert ci_annotations.main([str(tmp_path / "ci")]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("::error title=backend (failed)::exit code 1, total=2, failed=1")
    assert lines[1] == "::error file=tests/test_api.py,line=42,title=tests.test_api%3A%3Atest_x::assert 1 == 2%0Atrace line 1%0Atrace line 2"
    assert len(lines) == 2


def test_escaping_of_data_and_properties():
    assert ci_annotations.escape_data("100%\r\nnext") == "100%25%0D%0Anext"
    assert ci_annotations.command("error", "a\nb", title="x,y:z%") == "::error title=x%2Cy%3Az%25::a%0Ab"


def test_annotations_are_capped_at_ten_with_notice(tmp_path, capsys):
    write_report(tmp_path, results=[gate()])
    junit(tmp_path, "".join(f'<testcase classname="tests.test_a" name="t{i}"><error message="boom {i}"/></testcase>' for i in range(15)))
    ci_annotations.main([str(tmp_path)])
    lines = capsys.readouterr().out.splitlines()
    assert sum(line.startswith("::error") for line in lines) == 10
    assert lines[-1].startswith("::notice::") and "6 more" in lines[-1]


def test_playwright_failure_is_annotated(tmp_path, capsys):
    write_report(tmp_path, results=[gate("browser-fixtures")])
    report = {"suites": [{"title": "nav.spec.ts", "suites": [{"title": "Nav", "specs": [
        {"title": "opens", "file": "nav.spec.ts", "line": 7, "tests": [{"results": [{"status": "failed", "error": {"message": "\x1b[31mexpected visible\x1b[39m"}}]}]},
        {"title": "fine", "file": "nav.spec.ts", "line": 9, "tests": [{"results": [{"status": "passed"}]}]}]}]}]}
    (tmp_path / "browser-fixtures.json").write_text(json.dumps(report), encoding="utf-8")
    ci_annotations.main([str(tmp_path)])
    out = capsys.readouterr().out
    assert "::error file=frontend/tests/nav.spec.ts,line=7,title=Nav > opens::expected visible" in out
    assert "fine" not in out


def test_passing_report_prints_nothing(tmp_path, capsys):
    write_report(tmp_path, status="passed", results=[gate(status="passed")])
    assert ci_annotations.main([str(tmp_path)]) == 0
    assert capsys.readouterr().out == ""


def test_missing_directory_exits_zero_with_notice(tmp_path, capsys):
    assert ci_annotations.main([str(tmp_path / "absent")]) == 0
    assert capsys.readouterr().out.startswith("::notice::No acceptance output directory")


def test_latest_report_is_used_and_summary_written(tmp_path, monkeypatch, capsys):
    old, new = tmp_path / "20260101T000000Z-old", tmp_path / "20260102T000000Z-new"
    write_report(old, results=[gate("old-gate")])
    write_report(new, results=[gate("new-gate")])
    os.utime(old / "report.json", (1, 1))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("SECRET_TOKEN", "hunter2")
    ci_annotations.main([str(tmp_path)])
    out = capsys.readouterr().out
    assert "new-gate" in out and "old-gate" not in out and "hunter2" not in out
    text = summary.read_text(encoding="utf-8")
    assert "| gate | new-gate | failed |" in text and "hunter2" not in text
