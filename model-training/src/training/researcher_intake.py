"""Strict parser and Duck Tape intake mapper for researcher-model vulnerability findings.

The researcher model emits one JSON object per code function:

    {"findings": [{"title": ..., "description": ..., "priority": ..., "cwe": "CWE-79",
                   "reference": {"path": ..., "start_line": 1, "end_line": 3},
                   "source_key": ...}]}

An empty ``findings`` list is the valid answer for code with no vulnerability. Model output is
untrusted: nothing here executes or trusts it, it is only validated and mapped to the existing
``POST /api/intake`` ``JobInput`` body (backend/main.py), which stays human-approved downstream.
"""
from __future__ import annotations

import json
import re
from typing import Any

PRIORITIES = ("low", "medium", "high", "critical")
CWE_RE = re.compile(r"CWE-\d+")

# Limits mirror backend/main.py JobInput (title 3..200, description <=30000, source_key <=512).
TITLE_MIN, TITLE_MAX = 3, 200
SOURCE_KEY_MAX = 512
# Leave room in the 30000-char JobInput.description for the appended CWE/reference footer.
DESCRIPTION_MAX = 20000

FINDING_KEYS = frozenset({"title", "description", "priority", "cwe", "reference", "source_key"})
REFERENCE_KEYS = frozenset({"path", "start_line", "end_line"})


class FindingsError(ValueError):
    """Raised when a completion is not valid researcher findings JSON."""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_reference(ref: Any, where: str) -> dict[str, Any]:
    if not isinstance(ref, dict):
        raise FindingsError(f"{where}.reference must be an object")
    keys = set(ref)
    if keys != REFERENCE_KEYS:
        raise FindingsError(
            f"{where}.reference keys must be exactly {sorted(REFERENCE_KEYS)}, got {sorted(keys)}"
        )
    path = ref["path"]
    if not isinstance(path, str) or not path.strip():
        raise FindingsError(f"{where}.reference.path must be a non-empty string")
    start, end = ref["start_line"], ref["end_line"]
    if not _is_int(start) or not _is_int(end):
        raise FindingsError(f"{where}.reference start_line/end_line must be integers")
    if start < 1:
        raise FindingsError(f"{where}.reference.start_line must be >= 1")
    if start > end:
        raise FindingsError(f"{where}.reference.start_line must be <= end_line")
    return {"path": path, "start_line": start, "end_line": end}


def _validate_finding(item: Any, index: int) -> dict[str, Any]:
    where = f"findings[{index}]"
    if not isinstance(item, dict):
        raise FindingsError(f"{where} must be an object")
    keys = set(item)
    missing = FINDING_KEYS - keys
    extra = keys - FINDING_KEYS
    if missing:
        raise FindingsError(f"{where} missing keys: {sorted(missing)}")
    if extra:
        raise FindingsError(f"{where} has unexpected keys: {sorted(extra)}")

    title = item["title"]
    if not isinstance(title, str) or not TITLE_MIN <= len(title.strip()) <= TITLE_MAX:
        raise FindingsError(f"{where}.title must be a string of {TITLE_MIN}-{TITLE_MAX} characters")
    description = item["description"]
    if not isinstance(description, str) or not description.strip():
        raise FindingsError(f"{where}.description must be a non-empty string")
    if len(description) > DESCRIPTION_MAX:
        raise FindingsError(f"{where}.description exceeds {DESCRIPTION_MAX} characters")
    priority = item["priority"]
    if priority not in PRIORITIES:
        raise FindingsError(f"{where}.priority must be one of {list(PRIORITIES)}")
    cwe = item["cwe"]
    if not isinstance(cwe, str) or not CWE_RE.fullmatch(cwe):
        raise FindingsError(f"{where}.cwe must match CWE-<digits>")
    source_key = item["source_key"]
    if not isinstance(source_key, str) or not source_key.strip() or len(source_key) > SOURCE_KEY_MAX:
        raise FindingsError(f"{where}.source_key must be a non-empty string of at most {SOURCE_KEY_MAX} characters")

    return {
        "title": title.strip(),
        "description": description,
        "priority": priority,
        "cwe": cwe,
        "reference": _validate_reference(item["reference"], where),
        "source_key": source_key,
    }


def parse_findings(completion: str) -> list[dict[str, Any]]:
    """Strictly parse a researcher completion into a list of validated finding dicts.

    Raises FindingsError (a ValueError) on malformed JSON, a wrong top-level shape, unknown or
    missing keys, or any field outside its limits. Markdown fences and prose are rejected.
    """
    if not isinstance(completion, str):
        raise FindingsError("completion must be a string")
    try:
        data = json.loads(completion)
    except json.JSONDecodeError as exc:
        raise FindingsError(f"completion is not valid JSON: {exc.msg}") from exc
    if not isinstance(data, dict) or set(data) != {"findings"}:
        raise FindingsError('completion must be a JSON object with exactly one key, "findings"')
    findings = data["findings"]
    if not isinstance(findings, list):
        raise FindingsError("findings must be a list")
    return [_validate_finding(item, i) for i, item in enumerate(findings)]


def to_intake_payloads(
    findings: list[dict[str, Any]], repo_id: str, source_url: str | None = None
) -> list[dict[str, Any]]:
    """Map validated findings to POST /api/intake JobInput bodies.

    Each payload uses source='security' and type='security'. The description carries the model's
    text followed by the CWE and a ``path:start-end`` reference so a human reviewer sees the
    location without trusting the model. source_url, when given, must be an HTTP(S) or fixture:
    URL (JobInput rejects anything else); it is passed through unchanged.
    """
    if not isinstance(repo_id, str) or not repo_id.strip():
        raise FindingsError("repo_id must be a non-empty string")
    payloads: list[dict[str, Any]] = []
    for index, finding in enumerate(findings):
        clean = _validate_finding(finding, index)  # re-validate: callers may pass hand-built dicts
        ref = clean["reference"]
        location = f"{ref['path']}:{ref['start_line']}" + (
            f"-{ref['end_line']}" if ref["end_line"] != ref["start_line"] else ""
        )
        payloads.append(
            {
                "repo_id": repo_id,
                "source": "security",
                "type": "security",
                "priority": clean["priority"],
                "title": clean["title"],
                "description": f"{clean['description']}\n\n{clean['cwe']} at {location}",
                "source_key": clean["source_key"],
                "source_url": source_url,
            }
        )
    return payloads
