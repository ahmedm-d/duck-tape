import json

import pytest

from training.researcher_intake import FindingsError, parse_findings, to_intake_payloads


def finding(**overrides):
    base = {
        "title": "SQL injection in get_user",
        "description": "User input is concatenated into the query string.",
        "priority": "high",
        "cwe": "CWE-89",
        "reference": {"path": "app/db.py", "start_line": 10, "end_line": 12},
        "source_key": "researcher:app/db.py:get_user:CWE-89",
    }
    base.update(overrides)
    return base


def completion(*findings):
    return json.dumps({"findings": list(findings)})


def test_empty_findings_for_fixed_code_is_valid():
    assert parse_findings('{"findings": []}') == []


def test_parses_valid_finding():
    parsed = parse_findings(completion(finding()))
    assert len(parsed) == 1
    assert parsed[0]["cwe"] == "CWE-89"
    assert parsed[0]["reference"] == {"path": "app/db.py", "start_line": 10, "end_line": 12}


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        '{"findings": {}}',
        '{"findings": [], "extra": 1}',
        "```json\n{\"findings\": []}\n```",
        '{"results": []}',
    ],
)
def test_rejects_bad_top_level(text):
    with pytest.raises(FindingsError):
        parse_findings(text)


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": "ab"},
        {"title": "x" * 201},
        {"description": ""},
        {"priority": "urgent"},
        {"cwe": "89"},
        {"cwe": "CWE-"},
        {"cwe": "cwe-89"},
        {"source_key": ""},
        {"source_key": "k" * 513},
        {"reference": {"path": "a.py", "start_line": 0, "end_line": 3}},
        {"reference": {"path": "a.py", "start_line": 5, "end_line": 4}},
        {"reference": {"path": "", "start_line": 1, "end_line": 1}},
        {"reference": {"path": "a.py", "start_line": True, "end_line": 3}},
        {"reference": {"path": "a.py", "start_line": 1}},
        {"reference": "a.py:1"},
        {"confidence": 0.9},
    ],
)
def test_rejects_bad_finding(overrides):
    with pytest.raises(FindingsError):
        parse_findings(completion(finding(**overrides)))


def test_missing_key_rejected():
    bad = finding()
    del bad["cwe"]
    with pytest.raises(FindingsError):
        parse_findings(completion(bad))


def test_boundary_values_accepted():
    ok = finding(title="abc", source_key="k" * 512, reference={"path": "a.py", "start_line": 7, "end_line": 7})
    assert parse_findings(completion(ok))[0]["reference"]["end_line"] == 7
    assert len(parse_findings(completion(finding(title="t" * 200)))[0]["title"]) == 200


def test_payload_shape_matches_jobinput():
    payloads = to_intake_payloads(parse_findings(completion(finding())), "repo-1", "https://example.com/scan/1")
    assert payloads == [
        {
            "repo_id": "repo-1",
            "source": "security",
            "type": "security",
            "priority": "high",
            "title": "SQL injection in get_user",
            "description": "User input is concatenated into the query string.\n\nCWE-89 at app/db.py:10-12",
            "source_key": "researcher:app/db.py:get_user:CWE-89",
            "source_url": "https://example.com/scan/1",
        }
    ]
    body = payloads[0]
    assert set(body) <= {"repo_id", "title", "description", "source", "type", "priority", "scenario", "source_url", "source_key"}
    assert 3 <= len(body["title"]) <= 200 and len(body["description"]) <= 30000 and len(body["source_key"]) <= 512


def test_payload_single_line_reference_and_default_source_url():
    f = finding(reference={"path": "a.py", "start_line": 4, "end_line": 4})
    payload = to_intake_payloads([f], "repo-1")[0]
    assert payload["description"].endswith("CWE-89 at a.py:4")
    assert payload["source_url"] is None


def test_payload_validates_hand_built_findings_and_repo_id():
    with pytest.raises(FindingsError):
        to_intake_payloads([finding(priority="bogus")], "repo-1")
    with pytest.raises(FindingsError):
        to_intake_payloads([finding()], "  ")
    assert to_intake_payloads([], "repo-1") == []
