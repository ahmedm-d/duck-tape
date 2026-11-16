"""Deterministic domain rubric scorers (Tables 1-5) for the Duck Tape / Steward agent fleet."""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import tempfile
import textwrap
from pathlib import Path
from typing import Any

import yaml

from training.schemas import (
    LogClassifierCompletion,
    RouterCompletion,
    validate_ticket_parser_output,
)
import test_account3_models as acct3


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL | re.IGNORECASE)
_CODE_FENCE_RE = re.compile(r"```([A-Za-z0-9_+-]*)\s*\n(.*?)```", re.DOTALL)
_PATH_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_./-])([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.(?:py|ts|tsx|js|jsx|sql|yml|yaml|tf|json|md|sh|go|rs|java))\b"
)
_FILE_TOKEN_RE = re.compile(
    r"\b([A-Za-z0-9_.-]+\.(?:py|ts|tsx|js|jsx|sql|yml|yaml|tf|Dockerfile))\b"
)
_FUNC_XML_RE = re.compile(
    r"<function=([A-Za-z0-9_.-]+)>\s*(.*?)\s*</function>",
    re.DOTALL,
)
_PARAM_XML_RE = re.compile(
    r"<parameter=([A-Za-z0-9_.-]+)>(.*?)</parameter>",
    re.DOTALL,
)
_NODEID_RE = re.compile(r"([A-Za-z0-9_./-]+\.py::[A-Za-z0-9_.\[\]-]+)")
_TEST_FN_RE = re.compile(r"\b(test_[A-Za-z0-9_]+)\b")
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "into", "your", "when",
    "have", "will", "should", "must", "each", "only", "more", "than", "also",
    "none", "true", "false", "null", "code", "file", "files", "line", "lines",
    "component", "section", "button", "page", "site", "website", "layout",
    "users", "user", "using", "design", "brief", "preserve", "clean", "modern",
    "responsive", "tailwind", "react", "typescript", "props", "style", "styled",
})


def aggregate_rubric(scored_rows: list[dict[str, bool | None]]) -> dict[str, dict[str, Any]]:
    """Aggregate boolean/None score flags across rows into {score_key: {passed, total, pct}} + overall."""
    if not scored_rows:
        return {"overall": {"passed": 0, "total": 0, "pct": 0.0}}
    keys = list(scored_rows[0].keys())
    summary: dict[str, dict[str, Any]] = {}
    overall_passed = 0
    overall_total = 0
    for key in keys:
        vals = [row[key] for row in scored_rows if row.get(key) is not None]
        passed = sum(1 for v in vals if v)
        total = len(vals)
        pct = round((passed / total) * 100.0, 1) if total else 0.0
        summary[key] = {"passed": passed, "total": total, "pct": pct}
        overall_passed += passed
        overall_total += total
    summary["overall"] = {
        "passed": overall_passed,
        "total": overall_total,
        "pct": round((overall_passed / overall_total) * 100.0, 1) if overall_total else 0.0,
    }
    return summary


def format_cell(stat: dict[str, Any]) -> str:
    """Format a score summary strictly as 'XX.X% (passed/total)'."""
    return f"{stat['pct']:.1f}% ({stat['passed']}/{stat['total']})"


def extract_json_strict(text: str) -> tuple[Any | None, bool]:
    """Parse JSON cleanly without markdown fence or prose wrapper leakage."""
    stripped = (text or "").strip()
    is_clean = (
        bool(stripped)
        and not stripped.startswith("```")
        and stripped.startswith(("{", "["))
        and stripped.endswith(("}", "]"))
    )
    if is_clean:
        try:
            return json.loads(stripped), True
        except Exception:
            pass
    return extract_json_lenient(stripped), False


def extract_json_lenient(text: str) -> Any | None:
    """Best-effort JSON extraction for semantic scoring even when wrapped in fences/prose."""
    stripped = (text or "").strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except Exception:
        pass
    fence = _JSON_FENCE_RE.search(stripped)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except Exception:
            pass
    first_brace = stripped.find("{")
    last_brace = stripped.rfind("}")
    if 0 <= first_brace < last_brace:
        try:
            return json.loads(stripped[first_brace : last_brace + 1])
        except Exception:
            pass
    return None


def _collect_context_identifiers(prompt: str) -> tuple[set[str], set[str], set[str], list[str]]:
    """Extract grounded file paths, basenames, symbol names, and linked tests from prompt/<graph_context>."""
    gc = acct3.parse_graph_context(prompt)
    real_paths: set[str] = set()
    basenames: set[str] = set()
    symbols: set[str] = set()

    def _add_path(raw: str) -> None:
        cleaned = (raw or "").strip().strip("`'\"").lstrip("ab/")
        if not cleaned or cleaned == "/dev/null":
            return
        real_paths.add(cleaned)
        base = cleaned.rsplit("/", 1)[-1]
        if base:
            basenames.add(base)

    for node in gc.get("focal_symbols", []) or []:
        _add_path(str(node.get("file_path") or node.get("path") or ""))
        label = str(node.get("label") or node.get("symbol") or "").strip()
        if label and len(label) >= 3:
            symbols.add(label)
            if "." in label:
                symbols.add(label.rsplit(".", 1)[-1])

    for node in gc.get("nodes", []) or []:
        _add_path(str(node.get("file_path") or node.get("path") or ""))
        label = str(node.get("label") or node.get("symbol") or "").strip()
        if label and len(label) >= 3:
            symbols.add(label)
            if "." in label:
                symbols.add(label.rsplit(".", 1)[-1])

    for edge in gc.get("edges", []) or []:
        ev = edge.get("evidence")
        if isinstance(ev, dict):
            _add_path(str(ev.get("path") or ev.get("file_path") or ""))
        for key in ("source_label", "target_label", "caller_symbol", "target_symbol", "source", "target"):
            val = str(edge.get(key) or "").strip()
            if val.startswith(("graphify:", "file:")):
                parts = val.split(":")
                if len(parts) >= 2 and parts[1]:
                    _add_path(parts[1])
                if len(parts) >= 3 and parts[2]:
                    symbols.add(parts[2])
            elif val and len(val) >= 3:
                symbols.add(val)
                if "." in val:
                    symbols.add(val.rsplit(".", 1)[-1])

    blast = gc.get("blast_radius") if isinstance(gc.get("blast_radius"), dict) else {}
    for dep in (blast.get("dependent_files") or []):
        _add_path(str(dep))
    linked_tests = [str(x).strip() for x in (blast.get("linked_test_files") or []) if str(x).strip()]
    for test_path in linked_tests:
        _add_path(test_path)

    for match in _PATH_TOKEN_RE.findall(prompt or ""):
        _add_path(match)
    for match in _FILE_TOKEN_RE.findall(prompt or ""):
        _add_path(match)
    for m in re.findall(r"`([A-Za-z_][A-Za-z0-9_.]{2,64})`", prompt or ""):
        symbols.add(m)
        if "." in m:
            symbols.add(m.rsplit(".", 1)[-1])

    return real_paths, basenames, symbols, linked_tests


def _text_grounds_any_identifier(
    text: str,
    real_paths: set[str],
    basenames: set[str],
    symbols: set[str],
) -> bool:
    """Verify at least one path, basename, or non-trivial identifier from context is cited."""
    lower = (text or "").lower()
    for path in real_paths:
        if path.lower() in lower:
            return True
    for base in basenames:
        if len(base) >= 4 and base.lower() in lower:
            return True
    for sym in symbols:
        if len(sym) >= 4 and sym.lower() in lower:
            return True
    return False


def _has_hallucinated_paths(text: str, prompt: str, allowed_paths: set[str], allowed_bases: set[str]) -> bool:
    """Detect cited file paths absent from prompt/<graph_context>."""
    prompt_lower = (prompt or "").lower()
    allowed_lower = {p.lower() for p in allowed_paths} | {b.lower() for b in allowed_bases}
    for path in _PATH_TOKEN_RE.findall(text or ""):
        pl = path.lstrip("ab/").lower()
        base = pl.rsplit("/", 1)[-1]
        if pl in allowed_lower or base in allowed_lower or pl in prompt_lower or base in prompt_lower:
            continue
        return True
    return False


def _check_approved_knowledge(prompt: str, text: str) -> bool:
    """Verify approved_knowledge guidelines (worktree, non-stale approval, or custom rules) are respected."""
    gc = acct3.parse_graph_context(prompt)
    ak_list = [str(item) for item in (gc.get("approved_knowledge") or []) if str(item).strip()]
    lower = (text or "").lower()
    if not ak_list:
        return True
    # Core Duck Tape safety invariants embedded across approved_knowledge entries
    for rule in ak_list:
        rl = rule.lower()
        if "worktree" in rl and "worktree" in lower:
            return True
        if "stale" in rl and ("stale" in lower or "invalidat" in lower or "revision" in lower):
            return True
        if "sink_reachable" in rl and ("not shown reachable" in lower or "unknown" in lower):
            return True
        if "regression-before" in rl and "regression-before" in lower:
            return True
    # General overlap with salient keywords of approved_knowledge rules
    rule_words = {
        tok
        for rule in ak_list
        for tok in re.findall(r"[a-z][a-z0-9_-]{4,}", rule.lower())
        if tok not in _STOPWORDS
    }
    hits = sum(1 for tok in rule_words if tok in lower)
    return hits >= min(2, max(1, len(rule_words) // 4))


def _extract_code_blocks(text: str) -> list[tuple[str, str]]:
    """Return list of (lang_tag, body) pairs for markdown code fences."""
    return [(lang.strip().lower(), body) for lang, body in _CODE_FENCE_RE.findall(text or "")]


def _pick_code_block(text: str, preferred_langs: tuple[str, ...]) -> tuple[str, str]:
    """Select the most matching fenced code block or fallback to largest block / raw text."""
    blocks = _extract_code_blocks(text)
    if blocks:
        for lang, body in blocks:
            if any(p in lang for p in preferred_langs):
                return lang, body
        blocks_sorted = sorted(blocks, key=lambda item: len(item[1]), reverse=True)
        return blocks_sorted[0]
    return "", (text or "")


def _split_markdown_sections(text: str) -> dict[str, str]:
    """Parse '### Heading' sections into {heading_lower: body}."""
    sections: dict[str, str] = {}
    current_head = ""
    buf: list[str] = []
    for line in (text or "").splitlines():
        if line.strip().startswith("### "):
            if current_head:
                sections[current_head] = "\n".join(buf).strip()
            current_head = line.strip()[4:].strip().lower()
            buf = []
        else:
            buf.append(line)
    if current_head:
        sections[current_head] = "\n".join(buf).strip()
    return sections


# =============================================================================
# Table 1: Security Reviewer & Reviewer DPO
# =============================================================================

def score_table1_row(row: dict[str, Any], pred_text: str) -> dict[str, bool | None]:
    """Evaluate security_reviewer_eval / reviewer_dpo_eval using existing test_account3_models rubric."""
    expectations = acct3._extract_row_expectations(row["prompt"])
    return acct3.score_response_rubric(expectations, pred_text)


# =============================================================================
# Table 2: Router & Ticket Parser
# =============================================================================

def score_router_row(row: dict[str, Any], pred_text: str) -> dict[str, bool | None]:
    """Evaluate router_eval on strict JSON, HITL trigger accuracy, graph grounding, and risk/token calibration."""
    gold = json.loads(row["completion"])
    parsed_strict, is_clean_json = extract_json_strict(pred_text)
    parsed = parsed_strict if parsed_strict is not None else extract_json_lenient(pred_text)

    # Score 1: Strict JSON schema & enum validity without markdown wrapper leakage
    s1 = False
    if is_clean_json and isinstance(parsed, dict):
        try:
            RouterCompletion.model_validate(parsed)
            s1 = True
        except Exception:
            s1 = False

    # Score 2: Ambiguity detection & HITL Decision Card trigger accuracy
    gold_hitl = gold.get("next_agent") == "HumanDecision"
    if isinstance(parsed, dict):
        pred_agent = str(parsed.get("next_agent") or "")
        pred_hitl = pred_agent == "HumanDecision" or bool(parsed.get("needs_decision_card"))
        s2 = pred_hitl == gold_hitl
    else:
        s2 = False

    # Score 3: <graph_context> symbol & blast-radius grounding
    real_paths, basenames, symbols, linked_tests = _collect_context_identifiers(row["prompt"])
    reason = str(parsed.get("routing_reason") or "") if isinstance(parsed, dict) else pred_text
    blast = acct3.parse_graph_context(row["prompt"]).get("blast_radius") or {}
    dep_count = len(blast.get("dependent_files") or [])
    mod_match = re.search(r"Modified files:\s*([^\n|]+)", row["prompt"])
    mod_count = len([x for x in mod_match.group(1).split(",") if x.strip() and x.strip().lower() != "none"]) if mod_match else 0
    blast_ref_ok = any(
        f"{cnt} file(s)" in reason.lower()
        for cnt in {dep_count, mod_count}
        if cnt > 0
    ) or any(lt.rsplit("/", 1)[-1].lower() in reason.lower() for lt in linked_tests)
    s3 = (
        (_text_grounds_any_identifier(reason, real_paths, basenames, symbols) or blast_ref_ok)
        and not _has_hallucinated_paths(reason, row["prompt"], real_paths, basenames)
    )

    # Score 4: Risk level & token/priority calibration
    s4 = False
    if isinstance(parsed, dict):
        pred_risk = str(parsed.get("risk_level") or "").strip().lower()
        gold_risk = str(gold.get("risk_level") or "").strip().lower()
        raw_budget = parsed.get("token_budget")
        gold_budget = int(gold.get("token_budget", 4096))
        try:
            budget_val = int(raw_budget)
            ratio = max(budget_val, 1) / max(gold_budget, 1)
            budget_ok = 256 <= budget_val <= 65536 and 0.35 <= ratio <= 2.5
        except Exception:
            budget_ok = False
        s4 = (pred_risk == gold_risk) and budget_ok

    return {
        "score_1_schema": s1,
        "score_2_hitl": s2,
        "score_3_grounding": s3,
        "score_4_calibration": s4,
    }


def score_ticket_parser_row(row: dict[str, Any], pred_text: str) -> dict[str, bool | None]:
    """Evaluate ticket_parser_eval on strict JSON, HITL Decision accuracy, symbol grounding, and priority calibration."""
    gold = json.loads(row["completion"])
    parsed_strict, is_clean_json = extract_json_strict(pred_text)
    parsed = parsed_strict if parsed_strict is not None else extract_json_lenient(pred_text)

    # Score 1: Strict JSON schema & enum validity without markdown leakage
    s1 = False
    if is_clean_json and isinstance(parsed, dict):
        try:
            validate_ticket_parser_output(parsed)
            s1 = True
        except Exception:
            s1 = False

    # Score 2: Ambiguity detection & HITL Decision Card trigger accuracy
    gold_is_decision = "decision" in gold
    if isinstance(parsed, dict):
        pred_is_decision = "decision" in parsed or bool(parsed.get("needs_decision_card"))
        if gold_is_decision:
            dec = parsed.get("decision")
            valid_dec = (
                isinstance(dec, dict)
                and isinstance(dec.get("options"), list)
                and len(dec.get("options", [])) >= 2
                and bool(dec.get("recommendation"))
            )
            s2 = valid_dec
        else:
            s2 = not pred_is_decision
    else:
        s2 = False

    # Score 3: <graph_context> symbol & blast-radius grounding
    real_paths, basenames, symbols, _ = _collect_context_identifiers(row["prompt"])
    gold_cands = [str(x) for x in (gold.get("candidate_files_or_symbols") or [])]
    allowed_paths = set(real_paths)
    allowed_bases = set(basenames)
    for gc_item in gold_cands:
        cleaned = gc_item.strip().lstrip("ab/")
        if cleaned:
            allowed_paths.add(cleaned)
            allowed_bases.add(cleaned.rsplit("/", 1)[-1])
    if isinstance(parsed, dict):
        if "decision" in parsed and isinstance(parsed["decision"], dict):
            ground_text = json.dumps(parsed["decision"], ensure_ascii=False)
        else:
            cands = parsed.get("candidate_files_or_symbols") or []
            ground_text = f"{parsed.get('title', '')} {' '.join(map(str, cands))}"
    else:
        ground_text = pred_text
    issue_tokens = {
        tok for tok in re.findall(r"\b[A-Za-z0-9_.-]{4,}\b", row["prompt"][:1200])
        if ("_" in tok or "-" in tok or "." in tok or any(c.isupper() for c in tok[1:]))
    }
    s3 = (
        _text_grounds_any_identifier(ground_text, real_paths, basenames, symbols | issue_tokens)
        and not _has_hallucinated_paths(ground_text, row["prompt"], allowed_paths, allowed_bases)
    )

    # Score 4: Risk/priority & taxonomy calibration
    s4 = False
    if isinstance(parsed, dict):
        if gold_is_decision:
            dec = parsed.get("decision") if isinstance(parsed.get("decision"), dict) else {}
            opts = dec.get("options") if isinstance(dec.get("options"), list) else []
            opt_ids = {str(o.get("id")) for o in opts if isinstance(o, dict) and o.get("id")}
            rec = str(dec.get("recommendation") or "")
            s4 = bool(opt_ids) and rec in opt_ids
        else:
            pred_prio = str(parsed.get("priority") or "").strip().upper()
            gold_prio = str(gold.get("priority") or "").strip().upper()
            pred_src = str(parsed.get("source_tag") or "").strip().lower()
            pred_chg = str(parsed.get("change_type") or "").strip().lower()
            valid_enums = pred_src in {"ticket", "request", "security", "analysis", "research"} and pred_chg in {
                "feature", "fix", "refactor", "security", "docs", "infra"
            }
            s4 = (pred_prio == gold_prio) and valid_enums

    return {
        "score_1_schema": s1,
        "score_2_hitl": s2,
        "score_3_grounding": s3,
        "score_4_calibration": s4,
    }


# =============================================================================
# Table 3: Implementer & Test Writer
# =============================================================================

def _validate_tool_calls_or_action(pred_text: str, target_is_tool: bool) -> bool:
    """Validate OpenAI tool_calls JSON or OpenHands <function=...><parameter=...> XML syntax."""
    text = (pred_text or "").strip()
    if not text:
        return False

    # Case A: OpenAI-style JSON tool_calls wrapper
    obj = extract_json_lenient(text)
    if isinstance(obj, dict) and "tool_calls" in obj:
        calls = obj.get("tool_calls")
        if not isinstance(calls, list) or not calls:
            return False
        for call in calls:
            if not isinstance(call, dict):
                return False
            fn = call.get("function")
            if not isinstance(fn, dict) or not fn.get("name"):
                return False
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    parsed_args = json.loads(args)
                except Exception:
                    return False
                if not isinstance(parsed_args, dict):
                    return False
            elif not isinstance(args, dict):
                return False
        return True

    # Case B: OpenHands XML function tags
    open_cnt = len(re.findall(r"<function=[A-Za-z0-9_.-]+>", text))
    close_cnt = len(re.findall(r"</function>", text))
    if open_cnt > 0:
        if open_cnt != close_cnt:
            return False
        matches = _FUNC_XML_RE.findall(text)
        if len(matches) != open_cnt:
            return False
        for _fn_name, body in matches:
            p_open = len(re.findall(r"<parameter=[A-Za-z0-9_.-]+>", body))
            p_close = len(re.findall(r"</parameter>", body))
            if p_open != p_close:
                return False
            if p_open > 0 and len(_PARAM_XML_RE.findall(body)) != p_open:
                return False
        return True

    # Case C: Target expects final verification summary rather than a tool call
    if not target_is_tool:
        has_header = "### CI & Git Worktree Verification" in text
        has_diff = "--- " in text and "+++ " in text and "@@" in text
        if has_diff:
            return validate_unified_diff_git_apply(text)
        return has_header and ("```" not in text or "```bash" not in text)

    return False


def _reconstruct_preimage_from_diff(diff_text: str) -> tuple[dict[str, str], bool]:
    """Reconstruct minimal pre-image files for git apply --check and verify hunk line counts."""
    lines = diff_text.splitlines()
    file_hunks: dict[str, list[tuple[int, int, int, int, list[str]]]] = {}
    cur_path: str | None = None
    i = 0
    saw_hunk = False

    while i < len(lines):
        ln = lines[i]
        if ln.startswith("--- "):
            raw_old = ln[4:].strip().split("\t")[0]
            if i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
                raw_new = lines[i + 1][4:].strip().split("\t")[0]
                target = raw_new[2:] if raw_new.startswith("b/") else raw_new
                if target == "/dev/null":
                    target = raw_old[2:] if raw_old.startswith("a/") else raw_old
                cur_path = target.strip()
                file_hunks.setdefault(cur_path, [])
                i += 2
                continue
        m = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", ln)
        if m and cur_path:
            saw_hunk = True
            old_start = int(m.group(1))
            old_count = int(m.group(2)) if m.group(2) is not None else 1
            new_start = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) is not None else 1
            body: list[str] = []
            i += 1
            obs_old = 0
            obs_new = 0
            while i < len(lines):
                bln = lines[i]
                if bln.startswith(("diff --git ", "--- ", "@@ ")):
                    break
                if bln.startswith("\\ No newline"):
                    i += 1
                    continue
                if bln.startswith("+"):
                    obs_new += 1
                elif bln.startswith("-"):
                    obs_old += 1
                elif bln.startswith(" ") or bln == "":
                    # Tolerate blank spacer normalized to empty context
                    obs_old += 1
                    obs_new += 1
                    bln = " " + bln[1:] if bln.startswith(" ") else " "
                else:
                    # Synthetic hunk with wrapped or unprefixed line: break cleanly instead of aborting valid earlier hunks
                    break
                body.append(bln)
                i += 1
            if obs_old == 0 and obs_new == 0:
                continue
            # If an LLM emitted pure '+' additions and left bare whitespace blank lines without '+', normalize them to '+'
            if obs_new > 0 and all(
                bln.startswith("+") or not bln.strip()
                for bln in body
            ):
                body = [bln if bln.startswith("+") else "+" for bln in body]
            file_hunks[cur_path].append((old_start, max(old_count, obs_old), new_start, max(new_count, obs_new), body))
            continue
        i += 1

    if not saw_hunk or not file_hunks:
        return {}, False

    preimages: dict[str, str] = {}
    for path, hunks in file_hunks.items():
        if not hunks:
            continue
        max_line = 1
        for old_start, old_count, _, _, body in hunks:
            ctx_or_del = sum(1 for bln in body if bln.startswith((" ", "-")))
            max_line = max(max_line, old_start + max(old_count, ctx_or_del) + 4)
        buf = [f"# stub line {n}" for n in range(max_line)]
        for old_start, old_count, _, _, body in hunks:
            ctx_or_del = sum(1 for bln in body if bln.startswith((" ", "-")))
            if ctx_or_del == 0 and old_start == 0:
                continue
            pos = max(0, old_start - 1)
            for bln in body:
                if bln.startswith((" ", "-")):
                    if pos < len(buf):
                        buf[pos] = bln[1:]
                    pos += 1
        if all(sum(1 for bln in body if bln.startswith((" ", "-"))) == 0 for _, _, _, _, body in hunks):
            # New file creation (--- /dev/null)
            continue
        preimages[path] = "\n".join(buf) + "\n"
    return preimages, True


def validate_unified_diff_git_apply(raw_text: str) -> bool:
    """Extract unified diff, verify Python added lines compile, and run `git apply --check` in scratch git repo."""
    _, diff_body = _pick_code_block(raw_text, ("diff", "patch"))
    diff_clean = diff_body.strip("\r\n")
    if "--- " not in diff_clean or "+++ " not in diff_clean or "@@ " not in diff_clean:
        return False

    preimages, struct_ok = _reconstruct_preimage_from_diff(diff_clean + "\n")
    if not struct_ok:
        return False

    # Verify added Python code snippets parse syntactically (dedent or wrap indented blocks in def _stub)
    added_py_lines = [
        ln[1:]
        for ln in diff_clean.splitlines()
        if ln.startswith("+") and not ln.startswith("+++")
    ]
    if added_py_lines and (any(".py" in p for p in preimages) or "def test_" in diff_clean):
        snippet = "\n".join(added_py_lines).rstrip()
        if snippet.strip():
            dedented = textwrap.dedent(snippet)
            parsed_py = False
            candidates = [
                dedented,
                snippet,
                "def _stub():\n" + textwrap.indent(dedented, "    "),
                "class _Stub:\n" + textwrap.indent(dedented, "    "),
            ]
            for cand in candidates:
                try:
                    ast.parse(cand)
                    parsed_py = True
                    break
                except SyntaxError:
                    continue
            if not parsed_py:
                return False

    with tempfile.TemporaryDirectory(prefix="ducktape_diff_") as tmpdir:
        tmp_path = Path(tmpdir)
        for rel, content in preimages.items():
            # Prevent path traversal outside temp root
            safe_rel = Path(*[part for part in Path(rel).parts if part not in ("..", ".", "")])
            if not safe_rel.parts:
                return False
            target = tmp_path / safe_rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")

        subprocess.run(["git", "init", "-q"], cwd=tmpdir, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        patch_path = tmp_path / "_candidate.patch"
        patch_path.write_text(diff_clean + "\n", encoding="utf-8", newline="\n")
        for flags in (["--check", "--recount"], ["--check", "--unidiff-zero", "--recount"]):
            res = subprocess.run(
                ["git", "apply", *flags, str(patch_path)],
                cwd=tmpdir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if res.returncode == 0:
                return True
        return False


def _collect_implementer_prompt_text(row: dict[str, Any]) -> str:
    """Combine system + issue + recent turns of an implementer trajectory for grounding checks."""
    msgs = row.get("chat") or []
    return "\n".join(str(m.get("content") or "") for m in msgs[:-1])


def score_implementer_row(row: dict[str, Any], pred_text: str) -> dict[str, bool | None]:
    """Evaluate implementer_eval on tool/diff syntax, scope/non-weakening, constraint adherence, and caller preservation."""
    msgs = row.get("chat") or []
    target_text = str((msgs[-1] if msgs else {}).get("content") or "")
    target_is_tool = "<function=" in target_text or '"tool_calls"' in target_text
    prompt_blob = _collect_implementer_prompt_text(row)
    real_paths, basenames, symbols, linked_tests = _collect_context_identifiers(prompt_blob)

    # Score 1: Tool-call & unified diff syntax validity
    s1 = _validate_tool_calls_or_action(pred_text, target_is_tool=target_is_tool)

    # Score 2: Fail-before / Pass-after discipline & in-scope non-weakening
    lower = (pred_text or "").lower()
    weakens_tests = any(
        bad in lower
        for bad in ("pytest.mark.skip", "unittest.skip", "assert true", "-k 'not ", "git checkout -- tests", "--no-verify")
    )
    if target_is_tool:
        # Must stay in scope & inspect/test/edit repository files without test weakening
        targets_in_scope = _text_grounds_any_identifier(pred_text, real_paths, basenames, symbols) or any(
            cmd in lower for cmd in ("/testbed", "/workspace", "pytest", "python ", "grep ", "find ", "sed ", "cat ", "git ")
        )
        s2 = (not weakens_tests) and targets_in_scope and len((pred_text or "").strip()) >= 20
    else:
        has_before_after = (
            ("regression-before" in lower or ("before" in lower and "after" in lower) or "reproduc" in lower)
            and ("worktree" in lower or "pytest" in lower or "verified" in lower or "linked_test_files" in lower)
        )
        s2 = (not weakens_tests) and has_before_after

    # Score 3: Multi-turn revision & Decision Card / approved_knowledge constraint adherence
    ak_ok = _check_approved_knowledge(prompt_blob, pred_text)
    has_revision_directive = any(
        kw in prompt_blob.lower() for kw in ("ask ai to revise", "revise", "do not modify", "keep", "constraint", "without breaking")
    )
    if not target_is_tool:
        s3 = ak_ok or ("invalidat" in lower and "worktree" in lower)
    else:
        # Tool step shouldn't violate constraints or emit stale unverified completion prematurely
        s3 = ak_ok if not has_revision_directive else (ak_ok or _text_grounds_any_identifier(pred_text, real_paths, basenames, symbols))
        if not s1:
            s3 = s3 and ("worktree" in lower or "stale" in lower)

    # Score 4: Cross-module caller & import preservation
    caller_targets = [
        sym for sym in symbols if len(sym) >= 4
    ] + [lt.rsplit("/", 1)[-1] for lt in linked_tests]
    s4 = _text_grounds_any_identifier(pred_text, real_paths, basenames, set(caller_targets))

    return {
        "score_1_syntax": s1,
        "score_2_test_discipline": s2,
        "score_3_constraints": s3,
        "score_4_caller_preservation": s4,
    }


def score_test_writer_row(row: dict[str, Any], pred_text: str) -> dict[str, bool | None]:
    """Evaluate test_writer_eval on git apply --check diff validity, before/after assertions, AK constraints, and caller preservation."""
    prompt = row["prompt"]
    lower = (pred_text or "").lower()
    real_paths, basenames, symbols, linked_tests = _collect_context_identifiers(prompt)

    # Score 1: Unified diff syntax validity + git apply --check
    s1 = validate_unified_diff_git_apply(pred_text)

    # Score 2: Fail-before / Pass-after assertion structure without test weakening
    _, diff_body = _pick_code_block(pred_text, ("diff", "patch"))
    added_lines = [ln[1:] for ln in diff_body.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    added_blob = "\n".join(added_lines)
    has_real_assert = bool(
        re.search(
            r"\b(assert(?:_[A-Za-z0-9_]+)?|assertEqual|assertRaises|assertIn|assertTrue|assertFalse|assertIs|pytest\.raises|expect\(|EXPECT_)\b",
            added_blob,
        )
    )
    weakens = any(bad in added_blob.lower() for bad in ("pytest.mark.skip", "unittest.skip", "assert true\n", "pass  # todo"))
    has_cmd = "### Verification Command" in pred_text and any(
        runner in lower for runner in ("pytest", "unittest", "nosetests", "npm test", "vitest", "cargo test", "go test", "python -m")
    )
    has_before_after = ("regression-before" in lower or ("before" in lower and "after" in lower)) and (
        "### CI & Git Worktree Verification" in pred_text or "worktree" in lower
    )
    s2 = has_real_assert and (not weakens) and has_cmd and has_before_after

    # Score 3: Multi-turn revision & Decision Card / approved_knowledge adherence
    s3 = _check_approved_knowledge(prompt, pred_text) and ("stale" in lower or "invalidat" in lower or "worktree" in lower)

    # Score 4: Cross-module caller & import preservation
    import_or_sym = (
        "import " in added_blob
        or "from " in added_blob
        or _text_grounds_any_identifier(added_blob, real_paths, basenames, symbols)
    )
    grounds_ctx = _text_grounds_any_identifier(pred_text, real_paths, basenames, symbols) or any(
        lt.rsplit("/", 1)[-1].lower() in lower for lt in linked_tests
    )
    s4 = import_or_sym and grounds_ctx

    return {
        "score_1_syntax": s1,
        "score_2_test_discipline": s2,
        "score_3_constraints": s3,
        "score_4_caller_preservation": s4,
    }


# =============================================================================
# Table 4: Commit/PR Writer & Log Classifier
# =============================================================================

def score_commit_pr_writer_row(row: dict[str, Any], pred_text: str) -> dict[str, bool | None]:
    """Evaluate commit_pr_writer_eval on sections, confidence vs risk separation, citations, and unverified check disclosure."""
    prompt = row["prompt"]
    sections = _split_markdown_sections(pred_text)
    required = ("summary", "cited symbols", "confidence assessment", "risk assessment")

    # Score 1: Required 4 Markdown headings with non-empty bodies
    s1 = all(sections.get(head) and len(sections[head]) >= 5 for head in required)

    # Score 2: Confidence vs Risk separation
    conf = sections.get("confidence assessment", "")
    risk = sections.get("risk assessment", "")
    conf_l = conf.lower()
    risk_l = risk.lower()
    conf_tier = any(t in conf_l for t in ("high", "medium", "low"))
    risk_tier = any(t in risk_l for t in ("high", "medium", "low"))
    distinct_focus = (
        bool(conf)
        and bool(risk)
        and conf_l.strip() != risk_l.strip()
        and any(w in conf_l for w in ("evidence", "diff", "line", "localized", "test", "symbol", "missing", "static"))
        and any(w in risk_l for w in ("blast", "impact", "regression", "breaking", "caller", "merge", "unit", "test", "bounded", "scope"))
    )
    s2 = conf_tier and risk_tier and distinct_focus

    # Score 3: Grounded file:line & symbol citation accuracy (zero hallucinated paths)
    real_paths, basenames, symbols, _ = _collect_context_identifiers(prompt)
    cited_body = sections.get("cited symbols", "") or pred_text
    s3 = _text_grounds_any_identifier(cited_body, real_paths, basenames, symbols) and not _has_hallucinated_paths(
        cited_body, prompt, real_paths, basenames
    )

    # Score 4: Sandbox & non-checked disclosure honesty
    lower = (pred_text or "").lower()
    notes_unverified = any(
        phrase in lower
        for phrase in (
            "regression-before",
            "worktree",
            "not run",
            "unverified",
            "not executed",
            "without new test",
            "before merging",
            "ci & git worktree",
            "run targeted",
            "did not run",
        )
    )
    overclaims = any(bad in lower for bad in ("all tests pass", "verified passing", "100% tested"))
    s4 = notes_unverified and not overclaims

    return {
        "score_1_schema": s1,
        "score_2_taxonomy_separation": s2,
        "score_3_citations": s3,
        "score_4_sandbox_honesty": s4,
    }


def score_log_classifier_row(row: dict[str, Any], pred_text: str) -> dict[str, bool | None]:
    """Evaluate log_classifier_eval on strict JSON schema, category accuracy, test symbol citations, and sandbox honesty."""
    prompt = row["prompt"]
    gold = json.loads(row["completion"])
    parsed_strict, is_clean_json = extract_json_strict(pred_text)
    parsed = parsed_strict if parsed_strict is not None else extract_json_lenient(pred_text)

    # Score 1: Strict JSON schema without markdown leakage
    s1 = False
    if is_clean_json and isinstance(parsed, dict):
        try:
            LogClassifierCompletion.model_validate(parsed)
            s1 = True
        except Exception:
            s1 = False

    # Score 2: Root-cause taxonomy accuracy
    gold_cat = str(gold.get("failure_category") or "").strip().lower()
    pred_cat = str(parsed.get("failure_category") or "").strip().lower() if isinstance(parsed, dict) else ""
    s2 = pred_cat == gold_cat

    # Score 3: Grounded failing test symbol & file:line citation accuracy
    log_nodeids = set(_NODEID_RE.findall(prompt or ""))
    log_test_fns = set(_TEST_FN_RE.findall(prompt or ""))
    real_paths, basenames, symbols, _ = _collect_context_identifiers(prompt)
    if isinstance(parsed, dict):
        ftests = [str(x).strip() for x in (parsed.get("failing_tests") or []) if str(x).strip()]
        summary_str = str(parsed.get("root_cause_summary") or "")
        all_cited = " ".join(ftests) + " " + summary_str
        no_hallucinations = not _has_hallucinated_paths(all_cited, prompt, real_paths, basenames)
        for item in ftests:
            if "::" in item and item not in log_nodeids and item.split("::", 1)[-1] not in log_test_fns:
                if item.lower() not in prompt.lower():
                    no_hallucinations = False
                    break
        gold_ftests = gold.get("failing_tests") or []
        if gold_ftests:
            matched_test = any(
                (t in prompt or any(gt in t or t in gt for gt in gold_ftests))
                for t in ftests
            ) or _text_grounds_any_identifier(summary_str, real_paths, basenames, symbols | log_test_fns)
            s3 = no_hallucinations and matched_test
        else:
            s3 = no_hallucinations and (len(ftests) == 0 or all(t.lower() in prompt.lower() for t in ftests))
    else:
        s3 = False

    # Score 4: Sandbox & non-checked disclosure honesty
    if isinstance(parsed, dict):
        blob = f"{parsed.get('root_cause_summary', '')} {parsed.get('recommended_action', '')}".lower()
    else:
        blob = (pred_text or "").lower()
    if gold_cat == "sandbox_blocked":
        s4 = (
            pred_cat == "sandbox_blocked"
            or any(w in blob for w in ("sandbox", "network", "offline", "permission", "blocked", "environment"))
        )
    elif gold_cat == "missing_dependency":
        s4 = any(w in blob for w in ("depend", "package", "install", "module", "missing", "worktree", "sandbox"))
    else:
        s4 = (
            any(w in blob for w in ("worktree", "re-run", "rerun", "regression-before", "verify", "baseline", "flaky", "import", "syntax"))
            and "all tests passed" not in blob
        )

    return {
        "score_1_schema": s1,
        "score_2_taxonomy_separation": s2,
        "score_3_citations": s3,
        "score_4_sandbox_honesty": s4,
    }


# =============================================================================
# Table 5: DB Migration, IaC & Frontend UI
# =============================================================================

def _balanced_delimiters(text: str) -> bool:
    """Verify parentheses, brackets, and braces are balanced outside quoted strings."""
    stack: list[str] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    in_sq = False
    in_dq = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == "\\" and (in_sq or in_dq):
            escape = True
            continue
        if ch == "'" and not in_dq:
            in_sq = not in_sq
            continue
        if ch == '"' and not in_sq:
            in_dq = not in_dq
            continue
        if in_sq or in_dq:
            continue
        if ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            if not stack or stack[-1] != pairs[ch]:
                return False
            stack.pop()
    return not stack and not in_sq and not in_dq


def _valid_sql_block(sql_text: str) -> bool:
    """Validate non-empty balanced SQL DDL/DML migration statements."""
    cleaned = "\n".join(
        ln
        for ln in (sql_text or "").splitlines()
        if not ln.strip().startswith(("--", "#"))
    ).strip()
    if not cleaned:
        return False
    has_kw = bool(
        re.search(
            r"\b(CREATE|ALTER|DROP|SET|INSERT|UPDATE|DELETE|WITH|SELECT|BEGIN|COMMIT|DO|COMMENT|PRAGMA)\b",
            cleaned,
            re.IGNORECASE,
        )
    )
    ends_ok = cleaned.rstrip().endswith(";") or bool(re.search(r"\bGO\s*$", cleaned, re.IGNORECASE))
    return has_kw and _balanced_delimiters(cleaned) and ends_ok


def score_db_migration_row(row: dict[str, Any], pred_text: str) -> dict[str, bool | None]:
    """Evaluate db_migration_eval on SQL/Python syntax, rollback safety, lock/data-loss disclosure, and schema alignment."""
    prompt = row["prompt"]
    prompt_l = prompt.lower()
    is_python_fw = "django migrations" in prompt_l or "alembic" in prompt_l
    sections = _split_markdown_sections(pred_text)

    # Score 1: Forward migration syntax validity
    fwd_source = sections.get("1. forward migration", "") or pred_text
    lang, fwd_code = _pick_code_block(fwd_source, ("python", "sql", "prisma"))
    if is_python_fw or lang == "python":
        try:
            ast.parse(fwd_code)
            s1 = len(fwd_code.strip()) >= 20
        except SyntaxError:
            s1 = False
    else:
        s1 = _valid_sql_block(fwd_code)

    # Score 2: Rollback safety (explicit Down migration / downgrade() / rollback SQL)
    rb_source = ""
    for head, body in sections.items():
        if "rollback" in head or "down migration" in head or head.startswith("2."):
            rb_source = body
            break
    if not rb_source:
        rb_source = fwd_code if ("def downgrade(" in fwd_code or "reverse_sql=" in fwd_code) else ""
    rb_ok = False
    if rb_source:
        rb_lang, rb_code = _pick_code_block(rb_source, ("python", "sql"))
        if rb_lang == "sql":
            rb_ok = _valid_sql_block(rb_code) or ("ROLLBACK" in rb_source.upper() or "not required" in rb_source.lower())
        elif is_python_fw or rb_lang == "python":
            try:
                ast.parse(rb_code)
                rb_ok = (
                    "def downgrade" in rb_code
                    or "reverse" in rb_code.lower()
                    or "DROP " in rb_code.upper()
                    or "ALTER " in rb_code.upper()
                    or "op." in rb_code
                )
            except SyntaxError:
                rb_ok = False
        else:
            rb_ok = _valid_sql_block(rb_code) or ("ROLLBACK" in rb_source.upper() or "not required" in rb_source.lower())
    s2 = rb_ok

    # Score 3: Data-loss / lock risk & dependency impact disclosure
    risk_section = ""
    for head, body in sections.items():
        if "lock" in head or "risk" in head or head.startswith("3."):
            risk_section = body
            break
    risk_l = (risk_section or pred_text).lower()
    s3 = bool(risk_section) and any(
        kw in risk_l
        for kw in ("lock", "concurrently", "data-loss", "data loss", "atomic", "exclusive", "blocking", "expand", "nullable", "dry-run")
    )

    # Score 4: Repository convention & <graph_context> schema alignment
    real_paths, basenames, symbols, _ = _collect_context_identifiers(prompt)
    schema_tokens = set(re.findall(r'(?:TABLE|VIEW|ON|INTO)\s+"?([A-Za-z_][A-Za-z0-9_]+)"?', prompt, re.IGNORECASE))
    db_match = re.search(r"^Database:\s*([A-Za-z0-9_.-]+)", prompt, re.MULTILINE)
    if db_match:
        schema_tokens.add(db_match.group(1))
    s4 = _text_grounds_any_identifier(pred_text, real_paths, basenames, symbols | schema_tokens)

    return {
        "score_1_syntax": s1,
        "score_2_safety": s2,
        "score_3_risk_disclosure": s3,
        "score_4_schema_alignment": s4,
    }


def _detect_iac_stack(prompt: str) -> str:
    """Identify IaC stack strictly from the explicit `IaC Stack:` prompt header when present."""
    m = re.search(r"^IaC Stack:\s*(.+)", prompt or "", re.MULTILINE | re.IGNORECASE)
    header = (m.group(1) if m else (prompt or "")).lower()
    if "kubernetes" in header or "k8s" in header or ".yaml" in header or "yml" in header:
        return "kubernetes"
    if "terraform" in header or "hcl" in header or ".tf" in header:
        return "terraform"
    if "docker" in header:
        return "dockerfile"
    p_l = (prompt or "").lower()
    if "kubernetes" in p_l or "k8s" in p_l or ".yaml" in p_l:
        return "kubernetes"
    if "terraform" in p_l or "hcl" in p_l or ".tf" in p_l:
        return "terraform"
    return "dockerfile"


def _valid_iac_syntax(stack_prompt: str, code: str) -> bool:
    """Validate Kubernetes YAML, Terraform HCL, or Dockerfile syntax."""
    body = (code or "").strip()
    if len(body) < 15:
        return False
    stack = _detect_iac_stack(stack_prompt)
    if stack == "kubernetes":
        try:
            docs = [doc for doc in yaml.safe_load_all(body) if doc is not None]
        except yaml.YAMLError:
            return False
        if not (docs and all(isinstance(d, dict) and bool(d) for d in docs)):
            return False
        if "kubernetes_config" in (stack_prompt or "").lower() or "Kubernetes (.yaml)" in (stack_prompt or ""):
            return any(("kind" in d or "apiVersion" in d or "metadata" in d or "spec" in d) for d in docs)
        return True
    if stack == "terraform":
        has_hcl_block = bool(
            re.search(
                r'^\s*(resource|data|variable|output|locals|module|provider|terraform)\b',
                body,
                re.MULTILINE,
            )
        )
        return has_hcl_block and _balanced_delimiters(body)
    # Dockerfile
    has_from = bool(re.search(r"^\s*FROM\s+\S+", body, re.MULTILINE | re.IGNORECASE))
    has_instr = bool(re.search(r"^\s*(RUN|COPY|ADD|WORKDIR|USER|ENV|ARG|EXPOSE|CMD|ENTRYPOINT|LABEL)\s+", body, re.MULTILINE | re.IGNORECASE))
    return has_from and has_instr


def score_iac_row(row: dict[str, Any], pred_text: str) -> dict[str, bool | None]:
    """Evaluate iac_eval on HCL/YAML/Dockerfile syntax, least-privilege enforcement, impact disclosure, and convention alignment."""
    prompt = row["prompt"]
    stack = _detect_iac_stack(prompt)
    sections = _split_markdown_sections(pred_text)
    cfg_src = sections.get("infrastructure configuration", "") or pred_text
    _, cfg_code = _pick_code_block(cfg_src, ("yaml", "yml", "hcl", "terraform", "tf", "dockerfile", "docker"))

    # Score 1: Syntactic & schema validity
    s1 = _valid_iac_syntax(prompt, cfg_code)

    # Score 2: Least-privilege enforcement
    notes = sections.get("least-privilege iam & network security notes", "") or pred_text
    notes_l = notes.lower()
    cfg_l = cfg_code.lower()
    if stack == "kubernetes":
        unsafe_k8s = "privileged: true" in cfg_l
        lp_notes = any(
            w in notes_l
            for w in ("runasnonroot", "serviceaccount", "service account", "rbac", "clusterrole", "least-privilege", "allowprivilegeescalation", "securitycontext")
        )
        s2 = (not unsafe_k8s) and lp_notes
    elif stack == "terraform":
        wildcard_iam = bool(re.search(r'action\s*=\s*\[\s*"\*"\s*\].*resource\s*=\s*\[\s*"\*"\s*\]', cfg_l, re.DOTALL))
        lp_notes = any(w in notes_l for w in ("least-privilege", "wildcard", "cidr", "arn", "resource", "scope", "iam"))
        s2 = (not wildcard_iam) and lp_notes
    else:
        unsafe_df = bool(re.search(r"chmod\s+777", cfg_l))
        lp_notes = any(w in notes_l for w in ("non-root", "non-privileged", "user ", "unprivileged", "least-privilege"))
        s2 = (not unsafe_df) and lp_notes

    # Score 3: Stateful / supply-chain / network exposure & dependency impact disclosure
    s3 = bool(notes) and any(
        kw in notes_l
        for kw in (
            "supply-chain", "digest", "pin", "secret", "network", "exposure", "port",
            "ingress", "egress", "cidr", "stateful", "replace", "immutable", "blast",
            "encrypt", "kms", "resource", "namespace", "image",
        )
    )

    # Score 4: Repository convention & <graph_context> schema alignment
    real_paths, basenames, symbols, _ = _collect_context_identifiers(prompt)
    scope_tokens = set(re.findall(r"`([A-Za-z0-9_.:/-]{3,80})`", prompt))
    s4 = _text_grounds_any_identifier(pred_text, real_paths, basenames, symbols | scope_tokens)

    return {
        "score_1_syntax": s1,
        "score_2_safety": s2,
        "score_3_risk_disclosure": s3,
        "score_4_schema_alignment": s4,
    }


def _extract_tsx_code(pred_text: str) -> str:
    _, code = _pick_code_block(pred_text, ("tsx", "typescript", "jsx", "ts"))
    return code.strip()


def score_frontend_ui_row(row: dict[str, Any], pred_text: str) -> dict[str, bool | None]:
    """Evaluate frontend_ui_eval on TSX syntax, accessibility/typed props, state/XSS safety, and design-brief alignment."""
    prompt = row["prompt"]
    code = _extract_tsx_code(pred_text)
    code_l = code.lower()

    # Score 1: Valid React + TypeScript JSX/TSX structure
    has_react = "import " in code and "react" in code_l or "React.FC" in code
    has_export = "export const " in code or "export function " in code or "export default " in code
    has_jsx = bool(re.search(r"<[A-Za-z][A-Za-z0-9]*[\s>]", code)) and (
        "</" in code or "/>" in code
    )
    has_ts_types = bool(re.search(r"\b(interface|type)\s+[A-Z][A-Za-z0-9_]*", code))
    # Remove JSX text strings before checking braces roughly, or check JSX `{}`/`()` balance carefully
    paren_ok = code.count("(") == code.count(")") and abs(code.count("{") - code.count("}")) <= 1
    s1 = has_react and has_export and has_jsx and has_ts_types and paren_ok and len(code) >= 120

    # Score 2: Accessibility & typed Props enforcement
    has_props_iface = bool(re.search(r"(?:interface|type)\s+\w*Props\b", code))
    has_a11y = any(
        tok in code_l
        for tok in (
            "aria-", "role=", "alt=", "<nav", "<main", "<header", "<footer",
            "<section", "<button", "<label", "<h1", "<h2", "data-component=",
        )
    )
    s2 = has_props_iface and has_a11y

    # Score 3: Safe state boundaries, XSS hygiene & responsive utility discipline
    unsafe_xss = any(bad in code_l for bad in ("dangerouslysetinnerhtml", "<script", "eval(", "innerhtml"))
    has_modular_state = (
        "className=" in code
        and ("onClick" in code or "onChange" in code or "useState" in code or "onSelectAction" in code or "md:" in code or "sm:" in code or "lg:" in code)
    )
    s3 = (not unsafe_xss) and has_modular_state and has_props_iface

    # Score 4: Repository / Design Brief & Tailwind token alignment
    brief_match = re.search(r"Design Brief:\s*(.+)", prompt)
    brief_text = brief_match.group(1) if brief_match else prompt
    brief_words = [
        w.lower()
        for w in re.findall(r"[A-Za-z]{4,}", brief_text)
        if w.lower() not in _STOPWORDS
    ]
    kw_hits = sum(1 for w in set(brief_words) if w in code_l)
    has_tailwind = 'className=' in code and any(
        tw in code for tw in ("flex", "grid", "mx-auto", "px-", "py-", "bg-", "text-", "rounded")
    )
    s4 = has_tailwind and (kw_hits >= min(2, max(1, len(set(brief_words)))))

    return {
        "score_1_syntax": s1,
        "score_2_safety": s2,
        "score_3_risk_disclosure": s3,
        "score_4_schema_alignment": s4,
    }


SCORER_BY_AGENT = {
    "security_reviewer": score_table1_row,
    "reviewer_dpo": score_table1_row,
    "router": score_router_row,
    "ticket_parser": score_ticket_parser_row,
    "implementer": score_implementer_row,
    "test_writer": score_test_writer_row,
    "commit_pr_writer": score_commit_pr_writer_row,
    "log_classifier": score_log_classifier_row,
    "db_migration": score_db_migration_row,
    "iac": score_iac_row,
    "frontend_ui": score_frontend_ui_row,
}
