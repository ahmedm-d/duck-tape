"""Focused tests for the researcher dataset builder using tiny synthetic vulnerable/fixed pairs."""
import json

import pytest

import prep_13_researcher as prep
from training.schemas import InstructionRow

VULN_C = """int copy_name(char *dst, const char *src, int n)
{
    int i;
    for (i = 0; i <= n; i++)
        dst[i] = src[i];
    return i;
}"""

FIXED_C = """int copy_name(char *dst, const char *src, int n)
{
    int i;
    for (i = 0; i < n; i++)
        dst[i] = src[i];
    return i;
}"""

VULN_INSERT = """static int read_len(const unsigned char *p, int size)
{
    int len = p[0];
    memcpy(g_buf, p + 1, len);
    return len;
}"""

FIXED_INSERT = """static int read_len(const unsigned char *p, int size)
{
    int len = p[0];
    if (len > size - 1)
        return -1;
    memcpy(g_buf, p + 1, len);
    return len;
}"""

PROMPT_KEYS = {"scope", "focal_symbols", "edges", "blast_radius", "catalogue_matches", "approved_knowledge", "gaps", "context_hash"}


def make_pair(pair_id="t:1", project="proj", cve="CVE-2020-0001", cwe="CWE-787", vulnerable=VULN_C, fixed=FIXED_C, **kw):
    return prep.Pair(
        pair_id=pair_id, source=kw.pop("source", "synthetic/test"), language=kw.pop("language", "c"), project=project, cve=cve,
        commit_id=kw.pop("commit_id", "abc123"), file_name=kw.pop("file_name", "src/copy.c"), cwe=cwe,
        cwe_name_hint=None, vulnerable=vulnerable, fixed=fixed, **kw,
    )


def graph_block(prompt: str) -> dict:
    inner = prompt.split("<graph_context>\n", 1)[1].rsplit("\n</graph_context>", 1)[0]
    return json.loads(inner)


def test_changed_line_localization_replaced_line():
    loc = prep.localize_change(VULN_C, FIXED_C)
    assert (loc.start_line, loc.end_line, loc.mode) == (4, 4, "changed")
    assert "i <= n" in loc.excerpt


def test_localization_for_inserted_check_maps_to_surrounding_lines():
    loc = prep.localize_change(VULN_INSERT, FIXED_INSERT)
    assert loc.mode == "inserted"
    assert (loc.start_line, loc.end_line) == (3, 4)  # lines around the insertion point


def test_localization_falls_back_to_whole_function_when_only_comments_differ():
    fixed = VULN_C.replace("    int i;", "    // loop counter\n    int i;")
    loc = prep.localize_change(VULN_C, fixed)
    assert loc.mode == "whole" and (loc.start_line, loc.end_line) == (1, len(VULN_C.split("\n")))


def test_completion_shape_for_vulnerable_row_and_empty_for_fixed():
    built = prep.build_pair_rows(make_pair())
    assert built is not None
    vuln, fixed = built
    findings = json.loads(vuln.row.completion)
    assert set(findings) == {"findings"} and len(findings["findings"]) == 1
    finding = findings["findings"][0]
    assert set(finding) == {"title", "description", "priority", "cwe", "reference", "source_key"}
    assert set(finding["reference"]) == {"path", "start_line", "end_line"}
    assert finding["title"] == "Out-of-bounds Write in copy_name"
    assert finding["priority"] == "critical" and finding["cwe"] == "CWE-787"
    assert finding["reference"] == {"path": "src/copy.c", "start_line": 4, "end_line": 4}
    assert finding["source_key"] == "researcher:src/copy.c:copy_name:CWE-787"
    assert "CVE-2020-0001" in finding["description"] and "CWE-787" in finding["description"]
    assert "i <= n" in finding["description"] and "line 4" in finding["description"]
    assert fixed.row.completion == '{"findings":[]}'
    assert vuln.is_vulnerable and not fixed.is_vulnerable


def test_completion_validates_with_launcher_intake_parser_when_present():
    intake = pytest.importorskip("training.researcher_intake")
    vuln, fixed = prep.build_pair_rows(make_pair())
    assert len(intake.parse_findings(vuln.row.completion)) == 1
    assert intake.parse_findings(fixed.row.completion) == []


def test_fixed_twin_uses_real_fixed_code_without_added_comments():
    _, fixed = prep.build_pair_rows(make_pair())
    assert "i < n" in fixed.row.prompt and "i <= n" not in fixed.row.prompt
    assert "//" not in fixed.row.prompt.split("```")[1]


def test_graph_context_is_honest_stub_with_correct_nulls():
    vuln, _ = prep.build_pair_rows(make_pair())
    assert "<graph_context>" in vuln.row.prompt and "snippet-relative" in vuln.row.prompt
    ctx = graph_block(vuln.row.prompt)
    assert set(ctx) == PROMPT_KEYS
    assert ctx["scope"] == {
        "repo_id": "public-dataset:proj", "snapshot_id": "abc123", "engine": "none",
        "engine_version": None, "graph_hash": None,
    }
    assert ctx["edges"] == [] and ctx["catalogue_matches"] == [] and ctx["approved_knowledge"] == []
    (focal,) = ctx["focal_symbols"]
    assert focal["path"] == "src/copy.c" and focal["label"] == "copy_name"
    assert (focal["start_line"], focal["end_line"], focal["kind"]) == (1, 7, "callable")
    assert focal["signature"].startswith("int copy_name(char *dst")
    assert focal["community"] is None and focal["degree"] is None and focal["high_degree"] is None
    blast = ctx["blast_radius"]
    assert blast["dependent_files"] == [] and blast["linked_test_files"] == []
    assert blast["communities_touched"] is None and blast["structural_risk"] is None
    assert blast["basis"] == "function-level snippet; no repository graph"
    assert len(ctx["gaps"]) == 1 and "no repository call graph" in ctx["gaps"][0]


def test_graph_context_is_identical_for_twins_except_symbol_text():
    vuln, fixed = prep.build_pair_rows(make_pair())
    a, b = graph_block(vuln.row.prompt), graph_block(fixed.row.prompt)
    assert a["scope"] == b["scope"] and a["blast_radius"] == b["blast_radius"] and a["gaps"] == b["gaps"]


def test_rows_validate_as_instruction_rows_and_are_scrubbed():
    secret = 'char *token = "ghp_' + "a" * 30 + '";'
    pair = make_pair(vulnerable=VULN_C.replace("int i;", "int i;\n    " + secret), fixed=FIXED_C.replace("int i;", "int i;\n    " + secret))
    vuln, fixed = prep.build_pair_rows(pair)
    for built in (vuln, fixed):
        InstructionRow.model_validate(built.row.model_dump())
        assert "ghp_" not in built.row.prompt
    assert set(vuln.row.model_dump()) == {"prompt", "completion"}


def test_pair_skipped_when_code_identical_or_prompt_too_long():
    assert prep.build_pair_rows(make_pair(fixed=VULN_C.replace("    ", "  "))) is None
    huge_v = VULN_C.replace("return i;", "return i;\n" + "    x = 1;\n" * 4000)
    huge_f = FIXED_C.replace("return i;", "return i;\n" + "    x = 1;\n" * 4000)
    assert prep.build_pair_rows(make_pair(vulnerable=huge_v, fixed=huge_f)) is None


def test_title_and_source_key_limits():
    long_label = "f" * 300
    completion = json.loads(prep.build_completion(
        path="p" * 600, label=long_label, cwe="CWE-999999", cwe_name_hint=None, cve=None,
        loc=prep.Localization(1, 2, "whole", ""),
    ))
    finding = completion["findings"][0]
    assert 3 <= len(finding["title"]) <= prep.TITLE_MAX and len(finding["source_key"]) <= prep.SOURCE_KEY_MAX
    assert finding["priority"] == "medium"  # default for CWEs outside the table


def test_signature_parsing_c_python_javascript():
    assert prep.parse_signature("static void *\nfoo_bar(int a,\n   int b)\n{\n}", "c")[0] == "foo_bar"
    assert prep.parse_signature("Foo::~Foo() {\n}", "c")[0] == "Foo::~Foo"
    assert prep.parse_signature("def load(path, *, strict=False):\n    pass", "python")[0] == "load"
    assert prep.parse_signature("function handle(req, res) {\n}", "javascript")[0] == "handle"


def test_primevul_pairing_and_missing_filename_fallback():
    base = {"commit_id": "c1", "cve": "CVE-2020-1111", "project": "p", "cwe": ["CWE-125"], "file_name": "None",
            "cve_desc": "Out-of-bounds read in lib/parse.c in p before 2.0."}
    records = [
        {**base, "idx": "1", "target": 1, "func": VULN_C},
        {**base, "idx": "2", "target": 0, "func": FIXED_C},
        {**base, "idx": "3", "target": 0, "func": FIXED_C},  # orphan, ignored
    ]
    (record,) = prep.pair_primevul_records(records, "mit")
    assert prep.validate_pair(record) == []
    assert set(record) == set(prep.PAIR_KEYS)
    assert record["file_name"] == "lib/parse.c" and record["cwe"] == "CWE-125" and record["vulnerable_code"] == VULN_C
    assert record["fixed_code"] == FIXED_C and record["licence"] == "mit" and record["commit"] == "c1"


def test_group_split_has_zero_cve_and_project_overlap():
    pairs = []
    n = 0
    # Shared project, shared CVE across projects, and singletons: components must stay whole.
    for project, cve in [("a", "CVE-1"), ("a", "CVE-2"), ("b", "CVE-2"), ("c", "CVE-3"), ("d", "CVE-4"),
                         ("e", "CVE-5"), ("e", "CVE-6"), ("f", "CVE-7"), ("g", "CVE-8"), ("h", "CVE-9")] * 3:
        n += 1
        pairs.append(make_pair(pair_id=f"t:{n}", project=project, cve=cve))
    pairs.append(make_pair(pair_id="t:nocve", project="crossvul", cve=None))
    train, held = prep.split_by_group(pairs, eval_pairs=6, seed=42)
    assert len(held) == 6
    assert {p.cve for p in train} & {p.cve for p in held} == set() - {None}
    assert {p.project for p in train} & {p.project for p in held} == set()
    assert all(p.cve for p in held) and any(p.pair_id == "t:nocve" for p in train)
    assert train and not {p.pair_id for p in train} & {p.pair_id for p in held}


def test_assemble_end_to_end_balance_overlap_and_determinism():
    pairs = []
    for i in range(60):
        bump = f"\n    int pad{i} = {i};"
        pairs.append(make_pair(
            pair_id=f"t:{i}", project=f"proj{i // 3}", cve=f"CVE-2020-{i // 3:04d}",
            vulnerable=VULN_C.replace("int i;", "int i;" + bump), fixed=FIXED_C.replace("int i;", "int i;" + bump),
        ))
    first = prep.assemble(pairs, max_rows=50, eval_pairs=6, seed=42)
    second = prep.assemble(pairs, max_rows=50, eval_pairs=6, seed=42)
    train, evals, stats = first
    assert [b.row.prompt for b in train] == [b.row.prompt for b in second[0]]
    assert len(evals) == 12 and sum(b.is_vulnerable for b in evals) == 6
    assert len(train) == 50 and sum(b.is_vulnerable for b in train) == 30  # 60% vulnerable
    assert prep.group_overlap(train, evals) == {"cve_overlap": 0, "project_overlap": 0, "pair_overlap": 0}
    assert stats["usable"] == 60


def test_selection_raises_vulnerable_share_before_missing_the_row_target():
    pairs = [make_pair(pair_id=f"t:{i}") for i in range(30)]
    selection = prep.select_train_pairs(pairs, 50, seed=1)  # only 30 pairs: needs 20 twins to reach 50 rows
    assert len(selection) + sum(1 for _, twin in selection if twin) == 50
    short = prep.select_train_pairs(pairs, 100, seed=1)  # 30 pairs can only give 60 rows
    assert len(short) == 30 and all(twin for _, twin in short)


def test_dedupe_drops_pairs_with_repeated_normalised_code():
    a = make_pair(pair_id="t:a")
    b = make_pair(pair_id="t:b", vulnerable=VULN_C.replace("    ", "\t"), fixed=FIXED_C.replace("    ", "\t"))
    kept, dropped = prep.dedupe_pairs([a, b])
    assert len(kept) == 1 and dropped == 1


def pair_record(**overrides):
    record = {"source_id": "x/y", "licence": "mit", "project": "proj", "cve": "CVE-2021-0001", "cwe": "CWE-89",
              "file_name": None, "language": "c", "vulnerable_code": VULN_C, "fixed_code": FIXED_C, "commit": None}
    record.update(overrides)
    return record


def test_validate_pair_accepts_good_and_rejects_bad_records():
    assert prep.validate_pair(pair_record()) == []
    assert prep.validate_pair(pair_record(cve=None)) == []
    assert prep.validate_pair(pair_record(cve="2021-1"))
    assert prep.validate_pair(pair_record(cwe="89"))
    assert prep.validate_pair(pair_record(language="Python"))
    assert prep.validate_pair(pair_record(fixed_code=VULN_C))
    assert prep.validate_pair(pair_record(vulnerable_code="x"))
    assert prep.validate_pair({k: v for k, v in pair_record().items() if k != "licence"})
    assert prep.validate_pair({**pair_record(), "extra": 1})
    assert prep.validate_pair("not a dict")


def test_pair_files_round_trip(tmp_path):
    raw = tmp_path / "raw"
    other = pair_record(source_id="a/b", licence="apache-2.0", language="python", cve=None, cwe="CWE-78",
                        vulnerable_code="def run(p):\n    return os.system(p)\n",
                        fixed_code="def run(p):\n    return os.execv(p, [p])\n")
    assert prep.write_pair_records([pair_record()], raw / prep.pair_file_name("one")) == 1
    prep.write_pair_records([other], raw / prep.pair_file_name("two"))
    pairs, licences, stats = prep.read_pair_files(raw)
    assert len(pairs) == 2 and stats["invalid_lines"] == 0 and stats["files"] == 2
    assert licences["a/b"]["licence"] == "apache-2.0"
    assert next(p for p in pairs if p.source == "a/b").file_name == "unknown"
    with pytest.raises(ValueError):
        prep.pair_file_name("Bad Slug")


def test_selection_prefers_python_and_spreads_sources():
    c_pairs = [make_pair(pair_id=f"c:{i}", source="src/c") for i in range(20)]
    other_c = [make_pair(pair_id=f"d:{i}", source="src/d") for i in range(20)]
    py_pairs = [make_pair(pair_id=f"p:{i}", source="src/py", language="python") for i in range(3)]
    selection = prep.select_train_pairs(c_pairs + other_c + py_pairs, 20, seed=3)
    chosen = [p for p, _ in selection]
    assert all(any(p is q for q in chosen) for p in py_pairs)
    assert all(twin for p, twin in selection if p.language == "python")
    assert {"src/c", "src/d"} <= {p.source for p in chosen}


def test_python_function_extraction_for_crossvul_reduction():
    vuln_src = "import os\n\nclass A:\n    def run(self, p):\n        return os.system(p)\n\ndef safe(x):\n    return x\n"
    fixed_src = "import os\n\nclass A:\n    def run(self, p):\n        return os.execv(p, [p])\n\ndef safe(x):\n    return x\n"
    record = {"language": "python", "cwe_id": "CWE-78", "cwe_description": "OS Command Injection - bad",
              "vulnerable_code": vuln_src, "fixed_code": fixed_src, "file_pair_id": "9_0"}
    (pair,) = prep.crossvul_function_pairs(record)
    assert pair.language == "python" and pair.cve is None and "os.system" in pair.vulnerable
    built = prep.build_pair_rows(pair)
    assert built is not None
    assert json.loads(built[0].row.completion)["findings"][0]["cwe"] == "CWE-78"


def test_javascript_function_extraction():
    src = "function a(x) {\n  return x + 1;\n}\nconst b = function (y) {\n  var s = '}';\n  return y;\n};\n"
    funcs = prep.javascript_functions(src)
    assert set(funcs) == {"a", "b"} and "return y;" in funcs["b"] and "return x + 1;" in funcs["a"]
