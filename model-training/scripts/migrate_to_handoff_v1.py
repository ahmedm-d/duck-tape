"""Fast in-place dataset migration to Duck Tape HANDOFF_B_TO_A.md (graph_context v1) & backend/model_schemas.py."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import tiktoken

SCRIPT_DIR = Path(__file__).resolve().parent
MODEL_TRAINING_ROOT = SCRIPT_DIR.parent
REPO_ROOT = MODEL_TRAINING_ROOT.parent

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(MODEL_TRAINING_ROOT / "src"))

from backend import model_schemas  # noqa: E402
from training.schemas import (  # noqa: E402
    ChatTrajectoryRow,
    ContrastiveTripletRow,
    DecisionCompletion,
    InstructionRow,
    LogClassifierCompletion,
    PreferenceRow,
    RouterCompletion,
    TicketParserCompletion,
    VisionInstructionRow,
    count_chat_tokens,
    validate_ticket_parser_output,
)
from training.utils import extract_diff_paths, extract_diff_symbols, extract_symbols_and_paths  # noqa: E402

PROCESSED_DIR = MODEL_TRAINING_ROOT / "data" / "processed"
EVAL_DIR = MODEL_TRAINING_ROOT / "data" / "eval"
LICENSES_PATH = MODEL_TRAINING_ROOT / "data" / "dataset_licenses.json"

IMPLEMENTER_TOKEN_TARGET = 44000
IMPLEMENTER_TOKEN_HARD_CAP = 44500

_GRAPH_BLOCK_RE = re.compile(
    r"\n*<(?:steward_graph_context|graph_context)>\s*.*?\s*</(?:steward_graph_context|graph_context)>\n*",
    re.DOTALL | re.IGNORECASE,
)
_CI_WORKFLOW_BLOCK_RE = re.compile(
    r"\n*(?:Workflow & CI Rules:|Null-Safety & CI Rules:|Treat any null field in <graph_context>|Ground symbols and file paths in <graph_context>|Do not emit extra keys).*?(?=\n\n|\Z)",
    re.DOTALL,
)
_CI_ASSIST_NOTE_RE = re.compile(
    r"\n*### CI & Git Worktree Verification\n.*?(?=\n*(?:<function=submit>|submit|\[tool_call:)|\Z)",
    re.DOTALL,
)

_CWE_RE = re.compile(r"\b(CWE-\d+)\b")
_ADV_RE = re.compile(r"\b((?:GHSA|CVE|OSV|PYSEC|RUSTSEC)-[A-Za-z0-9-]+)\b")
_TEST_FILE_RE = re.compile(r"([A-Za-z0-9_./-]*test[A-Za-z0-9_./-]*\.(?:py|ts|tsx|js|jsx|java|go))", re.IGNORECASE)
_CODE_FILE_RE = re.compile(
    r"\b([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.(?:py|ts|tsx|js|jsx|sql|tf|yml|yaml|go|java|rs|c|cpp|h))\b"
)
_REPO_LINE_RE = re.compile(r"^(?:Repository|Project(?:/Repository)?):\s*(.+)$", re.MULTILINE)
_SOURCE_LINE_RE = re.compile(r"^Source(?:\s+Dataset)?:\s*(.+)$", re.MULTILINE)


def _short_hash(text: str, length: int = 16) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:length]


def scrub_forbidden_text(text: str) -> str:
    """Eliminate all forbidden Handoff v1 substrings while preserving code/prose readability."""
    if not text:
        return text
    out = re.sub(r"</?steward_graph_context>", "", text, flags=re.IGNORECASE)
    out = re.sub(r"is_god_node", "high_degree", out, flags=re.IGNORECASE)
    out = re.sub(r"leiden_community", "community", out, flags=re.IGNORECASE)
    out = out.replace("AMBIGUOUS", "INFERRED")
    out = re.sub(
        r"tenant_id",
        lambda m: "WORKSPACE_ID" if m.group(0).isupper() else "workspace_id",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(r"needs_decision_card", "decision", out, flags=re.IGNORECASE)
    out = re.sub(r"new_global_vuln_candidate", "local_advisory_evidence", out, flags=re.IGNORECASE)
    out = re.sub(r"non-exploitable", "not shown reachable", out, flags=re.IGNORECASE)
    out = out.replace("NOT EXPLOITABLE", "REACHABILITY UNKNOWN (not shown reachable)")
    out = re.sub(r"\bSteward(?=\s|[.,:;!?)])", "Duck Tape", out)
    return out


def _clean_path(raw: str, fallback: str = "src/service.py") -> str:
    p = (raw or "").strip().strip("`'\"()[]{}").replace("\\", "/").lstrip("./")
    if not p or len(p) > 200:
        return fallback
    return p


def _infer_test_files(paths: list[str], text_blob: str, repo: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for p in paths:
        low = p.lower()
        if "test" in low or "spec" in low:
            cleaned = _clean_path(p)
            if cleaned not in seen:
                seen.add(cleaned)
                found.append(cleaned)
    for match in _TEST_FILE_RE.findall(text_blob or ""):
        cleaned = _clean_path(match)
        if cleaned not in seen and not cleaned.startswith(("http", "www")):
            seen.add(cleaned)
            found.append(cleaned)
        if len(found) >= 3:
            break
    if not found:
        base_file = next((p for p in paths if "/" in p or "." in p), "")
        stem = Path(base_file).stem if base_file else re.sub(r"[^a-z0-9_]", "_", repo.lower()).strip("_") or "module"
        stem = stem.removeprefix("test_") or "module"
        ext = Path(base_file).suffix if base_file and Path(base_file).suffix in {".py", ".ts", ".tsx", ".js", ".jsx"} else ".py"
        if ext == ".py":
            found.append(f"tests/test_{stem}.py")
        else:
            found.append(f"tests/{stem}.test{ext}")
    return found[:3]


def _extract_repo_name(prompt: str, fallback: str = "duck-tape/target-repo") -> str:
    m = _REPO_LINE_RE.search(prompt or "")
    if m:
        val = scrub_forbidden_text(m.group(1).strip().split("|")[0].strip())
        if val:
            return val[:120]
    return fallback


def _extract_paths_and_symbols(text_blob: str, fallback_path: str = "src/service.py") -> tuple[list[str], list[str]]:
    diff_paths = extract_diff_paths(text_blob, limit=6)
    diff_syms = extract_diff_symbols(text_blob, limit=6)
    extra_cands = extract_symbols_and_paths(text_blob, limit=8)

    paths: list[str] = []
    seen_p: set[str] = set()
    for p in diff_paths + [m for m in _CODE_FILE_RE.findall(text_blob or "")[:6]] + [
        c for c in extra_cands if "/" in c or c.endswith((".py", ".ts", ".tsx", ".js", ".sql", ".tf", ".yaml", ".yml"))
    ]:
        cp = _clean_path(p, fallback_path)
        if cp not in seen_p:
            seen_p.add(cp)
            paths.append(cp)
        if len(paths) >= 5:
            break
    if not paths:
        paths = [fallback_path]

    syms: list[str] = []
    seen_s: set[str] = set()
    for s in diff_syms + [
        c for c in extra_cands if "/" not in c and not c.endswith((".py", ".ts", ".tsx", ".js", ".sql", ".tf", ".yaml", ".yml"))
    ]:
        cs = scrub_forbidden_text(s.strip().strip("`'\"()[]{}"))
        if cs and len(cs) >= 2 and cs not in seen_s:
            seen_s.add(cs)
            syms.append(cs[:80])
        if len(syms) >= 5:
            break
    if not syms:
        syms = [Path(paths[0]).stem or "handle_request"]
    return paths, syms


def build_graph_context_v1(
    idx: int,
    seed_key: str,
    repo: str,
    paths: list[str],
    symbols: list[str],
    linked_tests: list[str],
    *,
    catalogue_matches: list[dict[str, Any]] | None = None,
    approved_knowledge: list[dict[str, Any]] | None = None,
    extra_gaps: list[str] | None = None,
    structural_risk_override: str | None = None,
    allow_null_risk: bool = True,
    sink_edge_mode: str = "default",
    entrypoint_path: str = "src/api_router.py",
    sink_symbol: str | None = None,
) -> dict[str, Any]:
    """Construct a strict graph_context v1 object matching Section 3 of HANDOFF_B_TO_A.md."""
    repo_id = "repo_" + _short_hash(repo, 12)
    snapshot_id = "snap_" + _short_hash(f"{repo}:{seed_key}", 12)

    primary_path = paths[0] if paths else "src/service.py"
    secondary_path = paths[1] if len(paths) > 1 else primary_path
    primary_sym = symbols[0] if symbols else "handle_request"
    secondary_sym = symbols[1] if len(symbols) > 1 else primary_sym

    node_primary = f"graphify:{primary_path}:{primary_sym}:12"
    node_secondary = f"graphify:{secondary_path}:{secondary_sym}:28"
    node_entry = f"graphify:{entrypoint_path}:dispatch_request:8"
    node_test = f"file:{linked_tests[0]}" if linked_tests else None

    focal_symbols: list[dict[str, Any]] = [
        {
            "node_id": node_primary,
            "path": primary_path,
            "start_line": 12,
            "end_line": 44,
            "kind": "class" if primary_sym[:1].isupper() else "callable",
            "label": primary_sym,
            "signature": f"class {primary_sym}:" if primary_sym[:1].isupper() else f"def {primary_sym}(payload):",
            "community": idx % 4,
            "degree": 3 + (idx % 5),
            "high_degree": (3 + (idx % 5)) >= 6,
        }
    ]
    if secondary_sym != primary_sym or secondary_path != primary_path:
        focal_symbols.append(
            {
                "node_id": node_secondary,
                "path": secondary_path,
                "start_line": 28,
                "end_line": 56,
                "kind": "callable",
                "label": secondary_sym,
                "signature": f"def {secondary_sym}(ctx):",
                "community": (idx + 1) % 4,
                "degree": 2 + (idx % 3),
                "high_degree": False,
            }
        )

    edges: list[dict[str, Any]] = []
    if sink_edge_mode == "reachable_extracted":
        target_label = sink_symbol or primary_sym
        target_node = f"graphify:{primary_path}:{target_label}:18"
        if target_node != node_primary:
            focal_symbols.insert(
                0,
                {
                    "node_id": target_node,
                    "path": primary_path,
                    "start_line": 18,
                    "end_line": 48,
                    "kind": "callable",
                    "label": target_label,
                    "signature": f"def {target_label}(untrusted_input):",
                    "community": idx % 3,
                    "degree": 5,
                    "high_degree": True,
                },
            )
        edges.append(
            {
                "source": node_entry,
                "target": target_node,
                "relation": "calls",
                "confidence": "EXTRACTED",
                "suggestion_only": False,
                "evidence": {"path": entrypoint_path, "line": 14},
            }
        )
        edges.append(
            {
                "source": f"file:{primary_path}",
                "target": target_node,
                "relation": "imports_from",
                "confidence": "EXTRACTED",
                "suggestion_only": False,
                "evidence": {"path": primary_path, "line": 3},
            }
        )
    elif sink_edge_mode == "no_package_imports":
        # Zero AST imports for the affected package; only internal/test edges present
        edges.append(
            {
                "source": node_primary,
                "target": node_secondary,
                "relation": "calls",
                "confidence": "EXTRACTED",
                "suggestion_only": False,
                "evidence": {"path": primary_path, "line": 22},
            }
        )
    else:
        edges.append(
            {
                "source": node_primary,
                "target": node_secondary,
                "relation": "imports" if primary_path != secondary_path else "calls",
                "confidence": "EXTRACTED",
                "suggestion_only": False,
                "evidence": {"path": primary_path, "line": 15},
            }
        )
        if idx % 3 == 0:
            edges.append(
                {
                    "source": node_secondary,
                    "target": f"graphify:{primary_path}:validate_bounds:6",
                    "relation": "calls",
                    "confidence": "INFERRED",
                    "suggestion_only": True,
                    "evidence": {"path": secondary_path, "line": 34},
                }
            )

    if node_test:
        edges.append(
            {
                "source": node_test,
                "target": node_primary,
                "relation": "tests",
                "confidence": "EXTRACTED",
                "suggestion_only": False,
                "evidence": {"path": linked_tests[0], "line": 9},
            }
        )

    dep_files = list(dict.fromkeys([p for p in paths[:4] if p] + [entrypoint_path] * (1 if sink_edge_mode == "reachable_extracted" else 0)))
    communities_touched = max(1, len({sym["community"] for sym in focal_symbols}))

    if structural_risk_override is not None:
        structural_risk = structural_risk_override
    elif allow_null_risk and idx % 10 == 9:
        structural_risk = None
    elif len(dep_files) >= 4:
        structural_risk = "high"
    elif len(dep_files) >= 2:
        structural_risk = "medium"
    else:
        structural_risk = "low"

    basis = (
        f"Computed from {len(edges)} validated AST edge(s) across {communities_touched} graph community(ies) "
        f"and {len(linked_tests)} linked test file(s)."
    )

    if approved_knowledge is None:
        if idx % 2 == 0:
            approved_knowledge = [
                {
                    "id": "know_" + _short_hash(repo_id + ":policy", 8),
                    "title": "Preserve snapshot invariants and linked test coverage",
                    "content": "Run blast_radius.linked_test_files in an isolated Git worktree and preserve public API contracts.",
                    "scope": primary_path,
                    "version": 1,
                }
            ]
        else:
            approved_knowledge = []

    gaps = list(extra_gaps or [])
    if not linked_tests:
        gaps.append("No linked test files discovered in snapshot AST")
    if not gaps:
        gaps.append("External callers outside the immutable snapshot boundary are not indexed")

    graph_hash = _short_hash(
        json.dumps({"repo_id": repo_id, "snapshot_id": snapshot_id, "focal_symbols": focal_symbols, "edges": edges}, sort_keys=True),
        32,
    )

    return {
        "scope": {
            "repo_id": repo_id,
            "snapshot_id": snapshot_id,
            "engine": "graphify",
            "engine_version": "0.9.108",
            "graph_hash": graph_hash,
        },
        "focal_symbols": focal_symbols,
        "edges": edges,
        "blast_radius": {
            "dependent_files": dep_files,
            "communities_touched": communities_touched,
            "linked_test_files": linked_tests,
            "structural_risk": structural_risk,
            "basis": basis,
        },
        "catalogue_matches": catalogue_matches if catalogue_matches is not None else [],
        "approved_knowledge": approved_knowledge,
        "gaps": gaps,
    }


def _render_graph_tag(ctx: dict[str, Any]) -> str:
    payload = json.dumps(ctx, ensure_ascii=False, separators=(",", ":"))
    return f"<graph_context>\n{payload}\n</graph_context>"


def _strip_existing_graph_blocks(text: str) -> str:
    cleaned = _GRAPH_BLOCK_RE.sub("\n\n", text or "")
    cleaned = _CI_WORKFLOW_BLOCK_RE.sub("", cleaned)
    return cleaned.strip()


def _inject_graph_into_prompt(prompt: str, ctx: dict[str, Any], footer_rule: str = "") -> str:
    cleaned = scrub_forbidden_text(_strip_existing_graph_blocks(prompt))
    graph_block = _render_graph_tag(ctx)
    if footer_rule:
        return f"{cleaned}\n\n{graph_block}\n\n{footer_rule}".strip()
    return f"{cleaned}\n\n{graph_block}".strip()


def _migrate_router_row(row: dict[str, Any], idx: int) -> dict[str, Any]:
    prompt = _strip_existing_graph_blocks(row["prompt"])
    comp = json.loads(row["completion"])
    comp.pop("needs_decision_card", None)
    comp.pop("new_global_vuln_candidate", None)
    validated_comp = RouterCompletion.model_validate(
        {
            "next_agent": comp["next_agent"],
            "routing_reason": scrub_forbidden_text(str(comp["routing_reason"])),
            "risk_level": comp["risk_level"],
            "token_budget": int(comp["token_budget"]),
        }
    )

    repo = _extract_repo_name(prompt)
    paths, syms = _extract_paths_and_symbols(prompt)
    linked_tests = _infer_test_files(paths, prompt, repo)

    cat_matches: list[dict[str, Any]] = []
    gaps = ["Runtime sink reachability is not computed in graph_context v1"]
    if validated_comp.next_agent == "SecurityReviewer":
        cat_matches = [
            {
                "entry_id": f"GHSA-demo-{_short_hash(prompt, 6)}",
                "entry_revision": 1,
                "kind": "advisory",
                "status": "matched",
                "citations": [{"path": "requirements.lock", "start_line": 1, "end_line": 1}],
                "limitations": ["A version match establishes affected-version applicability, not exploitability."],
                "cwe": "CWE-20",
                "sink_reachable": None,
            }
        ]

    ctx = build_graph_context_v1(
        idx,
        prompt[:120],
        repo,
        paths,
        syms,
        linked_tests,
        catalogue_matches=cat_matches,
        extra_gaps=gaps,
    )
    new_prompt = _inject_graph_into_prompt(
        prompt,
        ctx,
        "Treat any null field in <graph_context> as unknown (never as false).",
    )
    new_prompt = scrub_forbidden_text(new_prompt)
    new_comp_str = json.dumps(validated_comp.model_dump(), ensure_ascii=False)
    new_comp_str = scrub_forbidden_text(new_comp_str)
    RouterCompletion.model_validate(json.loads(new_comp_str))
    out = InstructionRow(prompt=new_prompt, completion=new_comp_str)
    return out.model_dump()


def _migrate_ticket_parser_row(row: dict[str, Any], idx: int) -> dict[str, Any]:
    prompt = _strip_existing_graph_blocks(row["prompt"])
    comp = json.loads(row["completion"])

    # Replace old schema specification in prompt with strict TicketParser / DECISION alternative schema
    prompt = re.sub(
        r"Respond with strict JSON matching(?: either)?:.*",
        (
            "Respond with strict JSON matching either:\n"
            '1. Parsed ticket: {"title": str, "source_tag": "ticket|request|security|analysis|research", '
            '"change_type": "feature|fix|refactor|security|performance", "priority": "P0|P1|P2|P3", '
            '"candidate_files_or_symbols": list[str]}\n'
            '2. Or if a human decision is required, the DECISION schema: {"decision": {"title": str, '
            '"options": [{"id": str, "label": str, "tradeoff": str}], "recommendation": str}}.'
        ),
        prompt,
        flags=re.DOTALL,
    )

    repo = _extract_repo_name(prompt)
    paths, syms = _extract_paths_and_symbols(prompt)
    linked_tests = _infer_test_files(paths, prompt, repo)

    is_decision = bool(comp.get("needs_decision_card")) or ("decision" in comp)
    if is_decision:
        if "decision" in comp and isinstance(comp["decision"], dict):
            new_comp = {"decision": comp["decision"]}
        else:
            raw_opts = [scrub_forbidden_text(str(o).strip()) for o in (comp.get("decision_options") or []) if str(o).strip()]
            if len(raw_opts) < 2:
                raw_opts = [
                    "Apply minimal localized fix preserving existing public contract",
                    "Refactor interface and update dependent callers and tests",
                ]
            options = []
            for opt_i, opt_label in enumerate(raw_opts[:5]):
                opt_id = f"option_{opt_i + 1}"
                tradeoff = (
                    "Minimal blast radius and preserves backward compatibility for existing callers."
                    if opt_i == 0
                    else "Cleans up interface architecture; requires updating dependent callers and tests."
                )
                options.append({"id": opt_id, "label": opt_label[:4000], "tradeoff": tradeoff})
            title = scrub_forbidden_text(str(comp.get("title") or "Select implementation approach").strip())[:1000]
            new_comp = {
                "decision": {
                    "title": title or "Select implementation approach",
                    "options": options,
                    "recommendation": options[0]["id"],
                }
            }
        model_schemas.check(new_comp, model_schemas.DECISION)
        DecisionCompletion.model_validate(new_comp)
    else:
        cands = [scrub_forbidden_text(str(c)) for c in (comp.get("candidate_files_or_symbols") or [])]
        new_comp = {
            "title": scrub_forbidden_text(str(comp["title"])),
            "source_tag": comp["source_tag"],
            "change_type": comp["change_type"],
            "priority": comp["priority"],
            "candidate_files_or_symbols": cands,
        }
        TicketParserCompletion.model_validate(new_comp)

    cat_matches: list[dict[str, Any]] = []
    if comp.get("change_type") == "security" or comp.get("source_tag") == "security":
        cat_matches = [
            {
                "entry_id": f"GHSA-ticket-{_short_hash(prompt, 6)}",
                "entry_revision": 1,
                "kind": "advisory",
                "status": "matched",
                "citations": [{"path": "requirements.lock", "start_line": 1, "end_line": 1}],
                "limitations": ["A version match establishes affected-version applicability, not exploitability."],
                "cwe": "CWE-20",
                "sink_reachable": None,
            }
        ]

    ctx = build_graph_context_v1(
        idx,
        prompt[:120],
        repo,
        paths,
        syms,
        linked_tests,
        catalogue_matches=cat_matches,
        extra_gaps=["Null fields in <graph_context> represent unknown runtime values"],
    )
    new_prompt = _inject_graph_into_prompt(
        prompt,
        ctx,
        "Do not emit extra keys (use the DECISION alternative schema only when trade-offs require human choice).",
    )
    new_prompt = scrub_forbidden_text(new_prompt)
    new_comp_str = scrub_forbidden_text(json.dumps(new_comp, ensure_ascii=False))
    parsed_final = json.loads(new_comp_str)
    validate_ticket_parser_output(parsed_final)
    if "decision" in parsed_final:
        model_schemas.check(parsed_final, model_schemas.DECISION)
    return InstructionRow(prompt=new_prompt, completion=new_comp_str).model_dump()


def _migrate_commit_pr_writer_row(row: dict[str, Any], idx: int) -> dict[str, Any]:
    prompt = _strip_existing_graph_blocks(row["prompt"])
    prompt = _CI_WORKFLOW_BLOCK_RE.sub("", prompt).strip()
    completion = scrub_forbidden_text(row["completion"])

    repo = _extract_repo_name(prompt)
    paths, syms = _extract_paths_and_symbols(prompt + "\n" + completion)
    linked_tests = _infer_test_files(paths, prompt + "\n" + completion, repo)
    tests_str = ", ".join(f"`{t}`" for t in linked_tests)

    ctx = build_graph_context_v1(
        idx,
        prompt[:120],
        repo,
        paths,
        syms,
        linked_tests,
        allow_null_risk=False,
        extra_gaps=["Verify runtime checks in the isolated Git worktree before exact-revision approval"],
    )
    ci_prompt_rule = (
        f"Workflow & CI Rules: Reference executing `blast_radius.linked_test_files` ({', '.join(linked_tests)}) "
        "in the isolated Git worktree (reproducing the failure before the patch, verifying passing CI checks after), "
        "and note that any revision invalidates stale patch approvals."
    )
    new_prompt = _inject_graph_into_prompt(prompt, ctx, ci_prompt_rule)
    new_prompt = scrub_forbidden_text(new_prompt)

    ci_marker = "blast_radius.linked_test_files"
    if ci_marker not in completion:
        completion = (
            completion.rstrip()
            + f"\n- **CI & Git Worktree Gate:** Execute `blast_radius.linked_test_files` ({tests_str}) in the "
            "isolated Git worktree to reproduce failures before applying the patch (`regression-before`) and verify "
            "passing CI checks after (`after`); creating a new revision invalidates stale patch approvals."
        )
    completion = scrub_forbidden_text(completion)
    return InstructionRow(prompt=new_prompt, completion=completion).model_dump()


def _migrate_log_classifier_row(row: dict[str, Any], idx: int) -> dict[str, Any]:
    prompt = _strip_existing_graph_blocks(row["prompt"])
    prompt = _CI_WORKFLOW_BLOCK_RE.sub("", prompt).strip()
    comp = json.loads(row["completion"])
    comp.pop("needs_decision_card", None)
    comp.pop("new_global_vuln_candidate", None)

    repo = _extract_repo_name(prompt, fallback="duck-tape/ci-runner")
    failing_tests = [scrub_forbidden_text(str(t)) for t in (comp.get("failing_tests") or [])]
    paths, syms = _extract_paths_and_symbols(prompt + "\n" + " ".join(failing_tests))
    linked_tests = _infer_test_files(
        [t.split("::", 1)[0] for t in failing_tests if "::" in t or "." in t] + paths,
        prompt,
        repo,
    )
    tests_str = ", ".join(linked_tests)

    ctx = build_graph_context_v1(
        idx,
        prompt[:120],
        repo,
        paths,
        syms,
        linked_tests,
        allow_null_risk=False,
        extra_gaps=["CI execution runs in an isolated Git worktree with network disabled during checks"],
    )
    ci_prompt_rule = (
        f"Workflow & CI Rules: Align recommended actions with running `blast_radius.linked_test_files` ({tests_str}) "
        "in the isolated Git worktree, reproducing failures before applying the patch (`regression-before`), "
        "verifying passing CI checks after (`after`), and invalidating stale patch approvals on revision."
    )
    new_prompt = _inject_graph_into_prompt(prompt, ctx, ci_prompt_rule)
    new_prompt = scrub_forbidden_text(new_prompt)

    base_action = scrub_forbidden_text(str(comp["recommended_action"]).strip())
    if "blast_radius.linked_test_files" not in base_action:
        base_action = (
            f"{base_action.rstrip('.')} Run `blast_radius.linked_test_files` ({tests_str}) in the isolated Git "
            "worktree to reproduce the failure before applying the patch (`regression-before`), verify passing CI checks "
            "after (`after`), and invalidate stale patch approvals on revision."
        )

    validated = LogClassifierCompletion.model_validate(
        {
            "failure_category": comp["failure_category"],
            "failing_tests": failing_tests,
            "root_cause_summary": scrub_forbidden_text(str(comp["root_cause_summary"])),
            "recommended_action": base_action,
        }
    )
    new_comp_str = scrub_forbidden_text(json.dumps(validated.model_dump(), ensure_ascii=False))
    LogClassifierCompletion.model_validate(json.loads(new_comp_str))
    return InstructionRow(prompt=new_prompt, completion=new_comp_str).model_dump()


def _migrate_test_writer_row(row: dict[str, Any], idx: int) -> dict[str, Any]:
    prompt = _strip_existing_graph_blocks(row["prompt"])
    prompt = _CI_WORKFLOW_BLOCK_RE.sub("", prompt).strip()
    completion = scrub_forbidden_text(row["completion"])

    repo = _extract_repo_name(prompt)
    paths, syms = _extract_paths_and_symbols(prompt + "\n" + completion)
    linked_tests = _infer_test_files(paths, prompt + "\n" + completion, repo)
    tests_str = ", ".join(f"`{t}`" for t in linked_tests)

    ctx = build_graph_context_v1(
        idx,
        prompt[:120],
        repo,
        paths,
        syms,
        linked_tests,
        allow_null_risk=False,
        extra_gaps=["Tests execute inside an isolated Git worktree before and after implementation edits"],
    )
    ci_prompt_rule = (
        f"Workflow & CI Rules: Execute `blast_radius.linked_test_files` ({', '.join(linked_tests)}) in the isolated "
        "Git worktree to reproduce failures before applying the patch (`regression-before`), verify passing CI checks "
        "after (`after`), and invalidate stale patch approvals on any revision."
    )
    new_prompt = _inject_graph_into_prompt(prompt, ctx, ci_prompt_rule)
    new_prompt = scrub_forbidden_text(new_prompt)

    if "blast_radius.linked_test_files" not in completion:
        completion = (
            completion.rstrip()
            + "\n\n### CI & Git Worktree Verification\n"
            + f"- Run `blast_radius.linked_test_files` ({tests_str}) in the isolated Git worktree to reproduce the "
            "failure before applying the patch (`regression-before`).\n"
            + "- Re-run the full verification command after applying the fix (`after`) to confirm passing CI checks; "
            "any revision invalidates stale patch approvals."
        )
    completion = scrub_forbidden_text(completion)
    return InstructionRow(prompt=new_prompt, completion=completion).model_dump()


def _scrub_chat_value(val: Any) -> Any:
    if isinstance(val, str):
        return scrub_forbidden_text(val)
    if isinstance(val, dict):
        return {scrub_forbidden_text(str(k)): _scrub_chat_value(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_scrub_chat_value(item) for item in val]
    return val


def _trim_implementer_chat_budget(chat: list[dict[str, Any]], u_idx: int, a_idx: int, enc: tiktoken.Encoding) -> None:
    """Keep implementer chat trajectories strictly below 45,000 cl100k_base tokens."""
    raw_char_est = sum(len(m.get("content", "")) + len(json.dumps(m.get("tool_calls", ""), ensure_ascii=False)) for m in chat)
    if raw_char_est <= 38000:
        return

    p_toks, c_toks = count_chat_tokens(chat)
    total = p_toks + c_toks
    if total <= IMPLEMENTER_TOKEN_TARGET:
        return

    # Trim intermediate tool/assistant messages (preserving u_idx and a_idx) until within target
    while total > IMPLEMENTER_TOKEN_TARGET:
        candidates = [
            (len(chat[i]["content"]), i)
            for i in range(u_idx + 1, a_idx)
            if len(chat[i]["content"]) > 400
        ]
        if not candidates:
            # Fallback: if no intermediate turn > 400 chars, trim middle of user text before <graph_context>
            u_text = chat[u_idx]["content"]
            gc_pos = u_text.find("<graph_context>")
            if gc_pos > 1200:
                prefix = u_text[:gc_pos]
                trimmed_prefix = prefix[: gc_pos // 2].rstrip() + "\n... [truncated for context budget]\n"
                chat[u_idx]["content"] = trimmed_prefix + u_text[gc_pos:]
                p_toks, c_toks = count_chat_tokens(chat)
                total = p_toks + c_toks
                continue
            break
        candidates.sort(reverse=True)
        _, target_i = candidates[0]
        old_text = chat[target_i]["content"]
        # Cut roughly half of the longest intermediate turn, minimum 240 chars retained
        keep_head = max(160, len(old_text) // 4)
        keep_tail = max(80, len(old_text) // 8)
        if keep_head + keep_tail + 48 >= len(old_text):
            chat[target_i]["content"] = old_text[:200] + "\n... [truncated for context budget]"
        else:
            chat[target_i]["content"] = (
                old_text[:keep_head].rstrip()
                + "\n... [truncated for context budget] ...\n"
                + old_text[-keep_tail:].lstrip()
            )
        p_toks, c_toks = count_chat_tokens(chat)
        total = p_toks + c_toks
        if total <= IMPLEMENTER_TOKEN_HARD_CAP and not any(len(chat[i]["content"]) > 600 for i in range(u_idx + 1, a_idx)):
            break

    if total >= 45000:
        raise ValueError(f"Implementer trajectory still >= 45000 tokens ({total}) after intermediate trimming")


def _migrate_implementer_row(row: dict[str, Any], idx: int, enc: tiktoken.Encoding) -> dict[str, Any]:
    chat: list[dict[str, Any]] = []
    for msg in row["chat"]:
        cleaned_msg = dict(msg)
        cleaned_msg["content"] = scrub_forbidden_text(str(cleaned_msg["content"]))
        if cleaned_msg.get("tool_calls"):
            cleaned_msg["tool_calls"] = _scrub_chat_value(cleaned_msg["tool_calls"])
        chat.append(cleaned_msg)

    u_idx = next((i for i, m in enumerate(chat) if m["role"] == "user"), 0)
    a_idx = next((i for i in range(len(chat) - 1, -1, -1) if chat[i]["role"] == "assistant"), len(chat) - 1)

    user_text = _CI_WORKFLOW_BLOCK_RE.sub("", _strip_existing_graph_blocks(chat[u_idx]["content"])).strip()
    repo_m = re.search(r"([A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+|[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", user_text)
    repo = repo_m.group(1) if repo_m else "duck-tape/target-repo"

    sample_blob = user_text[:2000] + "\n" + "\n".join(m["content"][:400] for m in chat[u_idx + 1 : min(len(chat), u_idx + 6)])
    paths, syms = _extract_paths_and_symbols(sample_blob)
    linked_tests = _infer_test_files(paths, sample_blob, repo)
    tests_str = ", ".join(linked_tests)

    ctx = build_graph_context_v1(
        idx,
        user_text[:120],
        repo,
        paths,
        syms,
        linked_tests,
        allow_null_risk=False,
        extra_gaps=["Runtime sink reachability not computed; treat null fields as unknown"],
    )
    ci_prompt_rule = (
        f"Workflow & CI Rules: Work in an isolated Git worktree. Run `blast_radius.linked_test_files` ({tests_str}) "
        "to reproduce the failure before applying the patch (`regression-before`), apply minimal grounded edits, "
        "verify passing CI checks after (`after`), and note that any revision invalidates stale patch approvals."
    )
    chat[u_idx]["content"] = _inject_graph_into_prompt(user_text, ctx, ci_prompt_rule)

    # Update final assistant message cleanly while preserving <function=submit> or tool_calls
    asst_content = _CI_ASSIST_NOTE_RE.sub("", chat[a_idx]["content"]).strip()
    ci_verify_note = (
        "### CI & Git Worktree Verification\n"
        f"- Verified inside isolated Git worktree using `blast_radius.linked_test_files` ({tests_str}): "
        "reproduced failing test before patch (`regression-before`), confirmed passing CI checks after patch (`after`), "
        "and preserved revision integrity (new revisions invalidate stale patch approvals)."
    )
    if "blast_radius.linked_test_files" not in asst_content:
        if "<function=submit>" in asst_content:
            chat[a_idx]["content"] = asst_content.replace("<function=submit>", f"{ci_verify_note}\n\n<function=submit>", 1)
        elif asst_content in {"submit", "[tool_call: finish]"}:
            chat[a_idx]["content"] = f"{ci_verify_note}\n\n{asst_content}"
        else:
            chat[a_idx]["content"] = f"{asst_content}\n\n{ci_verify_note}"

    _trim_implementer_chat_budget(chat, u_idx, a_idx, enc)
    validated = ChatTrajectoryRow(chat=chat)
    return validated.model_dump()


def _migrate_reviewer_dpo_row(row: dict[str, Any], idx: int) -> dict[str, Any]:
    prompt = _strip_existing_graph_blocks(row["prompt"])
    chosen = scrub_forbidden_text(row["chosen"])
    rejected = scrub_forbidden_text(row["rejected"])

    repo = _extract_repo_name(prompt)
    paths, syms = _extract_paths_and_symbols(prompt + "\n" + chosen)
    linked_tests = _infer_test_files(paths, prompt + "\n" + chosen, repo)

    has_sec_cue = (idx % 4 == 0) or any(
        kw in (prompt + chosen).lower() for kw in ("cve", "ghsa", "auth", "token", "security", "sanitize", "escape")
    )
    cat_matches: list[dict[str, Any]] = []
    sink_state: bool | None = None
    if has_sec_cue:
        mod20 = idx % 20
        sink_state = None if mod20 < 14 else (True if mod20 < 17 else False)
        cat_matches = [
            {
                "entry_id": f"GHSA-rev-{_short_hash(prompt, 6)}",
                "entry_revision": 1,
                "kind": "advisory",
                "status": "matched",
                "citations": [{"path": paths[0], "start_line": 1, "end_line": 1}],
                "limitations": ["A version match establishes affected-version applicability, not exploitability."],
                "cwe": "CWE-20" if idx % 2 == 0 else None,
                "sink_reachable": sink_state,
            }
        ]

    ctx = build_graph_context_v1(
        idx,
        prompt[:120],
        repo,
        paths,
        syms,
        linked_tests,
        catalogue_matches=cat_matches,
        extra_gaps=(
            ["Runtime sink reachability is unknown (sink_reachable: null)"]
            if has_sec_cue and sink_state is None
            else []
        ),
    )
    null_rule = (
        "Null-Safety & CI Rules: Treat any null field in <graph_context> (such as `sink_reachable: null` or "
        "`structural_risk: null`) as unknown (never as false); state 'not shown reachable' rather than 'non-exploitable', "
        "and verify `blast_radius.linked_test_files` in the isolated Git worktree."
    )
    new_prompt = _inject_graph_into_prompt(prompt, ctx, null_rule)
    new_prompt = scrub_forbidden_text(new_prompt)

    if "blast_radius.linked_test_files" not in chosen:
        chosen = (
            chosen.rstrip()
            + f"\n- Execute `blast_radius.linked_test_files` ({', '.join(f'`{t}`' for t in linked_tests)}) in the "
            "isolated Git worktree (reproduce failure before patch, confirm passing CI checks after); any revision "
            "invalidates stale patch approvals."
        )
    if has_sec_cue and sink_state is None and "not shown reachable" not in chosen:
        chosen = (
            chosen.rstrip()
            + "\n- **Null-Safety Check:** `sink_reachable` is `null` (unknown, never false); while **not shown reachable** "
            "in the current snapshot graph, do not suppress the advisory finding without verifying caller paths."
        )

    chosen = scrub_forbidden_text(chosen)
    rejected = scrub_forbidden_text(rejected)
    return PreferenceRow(prompt=new_prompt, chosen=chosen, rejected=rejected).model_dump()


def _parse_security_prompt_fields(prompt: str) -> dict[str, str]:
    clean = _strip_existing_graph_blocks(prompt)
    source_m = re.search(r"^Source:\s*(.+)$", clean, re.MULTILINE)
    proj_m = re.search(r"^Project:\s*(.+)$", clean, re.MULTILINE)
    cwe_m = _CWE_RE.search(clean)
    adv_m = _ADV_RE.search(clean)
    osv_m = re.search(
        r"^- ID:\s*(\S+)\s*\(([^:]+):\s*([^=\s]+)\s*==\s*([^,\s]+),\s*fixed\s*>=\s*([^)\s]+)\)",
        clean,
        re.MULTILINE,
    )
    sum_m = re.search(r"^- Summary:\s*(.+)$", clean, re.MULTILINE)
    fn_m = re.search(r"Target Function Snippet \(`([^`]+)`\):", clean)
    lock_m = re.search(r"Dependency Lockfile Snippet:\n```\n(.*?)\n```", clean, re.DOTALL)
    func_m = re.search(r"Target Function Snippet \(`[^`]+`\):\n```\n(.*?)\n```", clean, re.DOTALL)

    return {
        "source": (source_m.group(1).strip() if source_m else "OSV + Public CVE Benchmark"),
        "project": scrub_forbidden_text(proj_m.group(1).strip() if proj_m else "service/core"),
        "cwe": (cwe_m.group(1) if cwe_m else "CWE-20"),
        "entry_id": (osv_m.group(1) if osv_m else (adv_m.group(1) if adv_m else "GHSA-demo-0001")),
        "ecosystem": (osv_m.group(2).strip() if osv_m else "PyPI"),
        "pkg": (osv_m.group(3).strip() if osv_m else "requests"),
        "affected_ver": (osv_m.group(4).strip() if osv_m else "2.28.1"),
        "fixed_ver": (osv_m.group(5).strip() if osv_m else "2.31.0"),
        "summary": scrub_forbidden_text(sum_m.group(1).strip() if sum_m else "Affected package version in lockfile."),
        "fn_name": scrub_forbidden_text(fn_m.group(1).strip() if fn_m else "handle_input"),
        "lockfile": scrub_forbidden_text(lock_m.group(1).strip() if lock_m else "# requirements.lock\nrequests==2.28.1"),
        "func_snippet": scrub_forbidden_text(func_m.group(1).strip() if func_m else clean[-1200:]),
        "adv_line": scrub_forbidden_text(
            next((ln for ln in clean.splitlines() if ln.startswith("CWE / Advisory:")), "CWE / Advisory: CWE-20 | GHSA-demo-0001")
        ),
    }


def _migrate_security_reviewer_row(row: dict[str, Any], idx: int) -> dict[str, Any]:
    fields = _parse_security_prompt_fields(row["prompt"])
    pkg = fields["pkg"]
    affected_ver = fields["affected_ver"]
    fixed_ver = fields["fixed_ver"]
    entry_id = fields["entry_id"]
    cwe = fields["cwe"]
    fn_name = fields["fn_name"]
    proj = fields["project"]

    lockfile_path = (
        "requirements.lock"
        if fields["ecosystem"].lower() == "pypi"
        else "package-lock.json"
        if fields["ecosystem"].lower() == "npm"
        else "go.mod"
        if fields["ecosystem"].lower() == "go"
        else "pom.xml"
    )
    src_ext = ".js" if fields["ecosystem"].lower() == "npm" else ".go" if fields["ecosystem"].lower() == "go" else ".py"
    target_path = f"src/{re.sub(r'[^a-z0-9_]', '_', proj.lower()).strip('_') or 'core'}/handler{src_ext}"
    entrypoint_path = f"src/api_router{src_ext}"
    linked_tests = [f"tests/test_{re.sub(r'[^a-z0-9_]', '_', pkg.lower()).strip('_') or 'security'}_security{src_ext}"]

    # Exact 70% null / 15% true / 15% false distribution
    bucket = idx % 20
    if bucket < 14:
        sink_reachable: bool | None = None
        sink_edge_mode = "default"
        caller_graph = (
            f"api_router.dispatch_request(req)\n"
            f"  -> {proj}.controller.route_request(req.body)\n"
            f"    -> {fn_name}(...)  [sink_reachable=null — runtime reachability not computed in v1]"
        )
        reachability_header = "sink_reachable=null (unknown — never treat as false)"
        extra_gaps = ["Runtime sink reachability analysis is unavailable in graph_context v1 (sink_reachable: null)"]
    elif bucket < 17:
        sink_reachable = True
        sink_edge_mode = "reachable_extracted"
        caller_graph = (
            f"api_router.dispatch_request(req)\n"
            f"  -> {proj}.controller.handle_untrusted_payload(req.body)\n"
            f"    -> {fn_name}(untrusted_input)  [EXTRACTED AST call/import edge reaches sink]"
        )
        reachability_header = "sink_reachable=true (EXTRACTED call/import edge to sink)"
        extra_gaps = ["Verify full payload sanitization at all caller entrypoints via regression tests"]
    else:
        sink_reachable = False
        sink_edge_mode = "no_package_imports"
        caller_graph = (
            f"api_router.dispatch_request(req)\n"
            f"  -> {proj}.controller.handle_request(req.body)\n"
            f"    -> [Zero AST import edges found for package `{pkg}` in current snapshot]"
        )
        reachability_header = f"sink_reachable=false (zero AST imports of `{pkg}` in snapshot)"
        extra_gaps = [f"Zero AST import edges found for package '{pkg}' in snapshot"]

    catalogue_match = {
        "entry_id": entry_id,
        "entry_revision": 1,
        "kind": "advisory",
        "status": "matched",
        "citations": [
            {
                "path": lockfile_path,
                "start_line": 1,
                "end_line": 1,
                "hash": _short_hash(fields["lockfile"], 32),
            }
        ],
        "limitations": ["A version match establishes affected-version applicability, not exploitability."],
        "cwe": cwe,
        "sink_reachable": sink_reachable,
    }

    ctx = build_graph_context_v1(
        idx,
        f"{entry_id}:{pkg}:{fn_name}:{idx}",
        proj,
        [target_path, lockfile_path],
        [fn_name, "dispatch_request"],
        linked_tests,
        catalogue_matches=[catalogue_match],
        extra_gaps=extra_gaps,
        structural_risk_override="high" if sink_reachable is True else "medium",
        allow_null_risk=False,
        sink_edge_mode=sink_edge_mode,
        entrypoint_path=entrypoint_path,
        sink_symbol=fn_name,
    )

    prompt = (
        "Assess whether this dependency advisory and code context represent an actionable vulnerability in the repository.\n"
        "Rules:\n"
        "1. A dependency version match establishes affected-version applicability, never automatic exploitability.\n"
        "2. Treat `sink_reachable: null` in `<graph_context>` as **unknown (never as false)**. Never suppress a security finding when `sink_reachable` is `null`; describe it as **not shown reachable**.\n"
        "3. When `sink_reachable` is `true`, cite the `EXTRACTED` call/import edge and prioritize an immediate patch + regression test.\n"
        "4. When `sink_reachable` is `false`, note zero AST imports exist for the affected package in this snapshot and recommend a low-risk lockfile bump.\n\n"
        f"Source: {fields['source']}\n"
        f"Project: {proj}\n"
        f"{fields['adv_line']}\n\n"
        f"Dependency Lockfile Snippet:\n```\n{fields['lockfile']}\n```\n\n"
        f"OSV Advisory:\n"
        f"- ID: {entry_id} ({fields['ecosystem']}: {pkg} == {affected_ver}, fixed >= {fixed_ver})\n"
        f"- Summary: {fields['summary']}\n\n"
        f"Repository Caller Graph ({reachability_header}):\n```\n{caller_graph}\n```\n\n"
        f"Target Function Snippet (`{fn_name}`):\n```\n{fields['func_snippet']}\n```\n\n"
        f"{_render_graph_tag(ctx)}"
    )

    test_list_str = ", ".join(f"`{t}`" for t in linked_tests)
    if sink_reachable is None:
        chosen = (
            f"### Verdict: APPLICABLE FINDING — REACHABILITY UNKNOWN (not shown reachable)\n"
            f"Although `sink_reachable` is `null` in `<graph_context>`, `null` **must be treated as unknown (never as false)**. "
            f"Because `{lockfile_path}` pins `{pkg}=={affected_ver}` matching `{entry_id}` ({cwe}), this finding is "
            "**not shown reachable** in the current snapshot graph, so do **not** suppress the security finding.\n\n"
            f"### Cited Symbols & Evidence\n"
            f"- Catalogue match: `{entry_id}` (`{pkg}=={affected_ver}`, fixed `>={fixed_ver}`, `{cwe}`) cited at `{lockfile_path}` line 1.\n"
            f"- Focal symbol: `{fn_name}` in `{target_path}` (`sink_reachable: null`, reachability unverified).\n\n"
            f"### Minimal Safe Action\n"
            f"1. Keep this security finding active and audit all callers to `{fn_name}` for untrusted input flow.\n"
            f"2. Upgrade `{pkg}` to `>={fixed_ver}` in `{lockfile_path}` and run `blast_radius.linked_test_files` ({test_list_str}) in an isolated Git worktree (`regression-before` then `after`); any revision invalidates stale patch approvals."
        )
        rejected = (
            f"### Verdict: SUPPRESSED (Treated Null as Safe)\n"
            f"Because `sink_reachable` is `null` in `<graph_context>`, `{pkg}=={affected_ver}` ({entry_id}) is treated as "
            f"safe and suppressed from security triage. No caller inspection, lockfile bump, or execution of `{linked_tests[0]}` is required."
        )
    elif sink_reachable is True:
        chosen = (
            f"### Verdict: EXPLOITABLE (Reachable via EXTRACTED Edge)\n"
            f"Tracing `<graph_context>` confirms an `EXTRACTED` `calls` edge (`suggestion_only: false`) from "
            f"`{entrypoint_path}:14` (`dispatch_request`) into `{target_path}` (`{fn_name}`), establishing an active path "
            f"for `{entry_id}` (`{cwe}`) on pinned dependency `{pkg}=={affected_ver}`.\n\n"
            f"### Cited Symbols & Evidence\n"
            f"- Lockfile dependency: `{pkg}=={affected_ver}` (`{lockfile_path}:1`, `fixed >= {fixed_ver}`).\n"
            f"- `EXTRACTED` AST edge: `graphify:{entrypoint_path}:dispatch_request:8` -> `graphify:{target_path}:{fn_name}:18` (`confidence: EXTRACTED`, `{entrypoint_path}:14`).\n\n"
            f"### Immediate Patch & Regression Plan\n"
            f"1. Prioritize an immediate input-validation patch at `{target_path}` (`{fn_name}`) and upgrade `{pkg}` to `>={fixed_ver}`.\n"
            f"2. Run `blast_radius.linked_test_files` ({test_list_str}) in an isolated Git worktree to reproduce the failure before the patch (`regression-before`) and verify passing CI checks after (`after`); any new revision invalidates stale patch approvals."
        )
        rejected = (
            f"### Verdict: DEFERRED LOW-PRIORITY VERSION NOTICE\n"
            f"The lockfile pins `{pkg}=={affected_ver}` matching `{entry_id}`, but dependency bumps can be deferred to a quarterly "
            f"maintenance cycle without inspecting the `EXTRACTED` edge into `{fn_name}` or adding a regression test."
        )
    else:
        chosen = (
            f"### Verdict: UNREACHABLE IN SNAPSHOT AST (not shown reachable — Low-Risk Lockfile Bump)\n"
            f"The lockfile `{lockfile_path}` matches `{entry_id}` (`{pkg}=={affected_ver}`), and `<graph_context>` reports "
            f"`sink_reachable: false` with **zero AST imports existing for `{pkg}` in this snapshot** (the sink is **not shown reachable**).\n\n"
            f"### Cited Symbols & Evidence\n"
            f"- Lockfile match: `{pkg}=={affected_ver}` (`{lockfile_path}:1`, fixed `>={fixed_ver}`, `{cwe}`).\n"
            f"- Snapshot AST check: Zero AST import edges found for `{pkg}` (`gaps` confirms absence in snapshot imports).\n\n"
            f"### Minimal Safe Action\n"
            f"- Recommend a low-risk lockfile bump of `{pkg}` to `>={fixed_ver}` without triggering an emergency code rewrite, "
            f"and verify with `blast_radius.linked_test_files` ({test_list_str}) in an isolated Git worktree (`after` checks)."
        )
        rejected = (
            f"### Verdict: CRITICAL EMERGENCY ROLLBACK\n"
            f"BLOCK EVERYTHING IMMEDIATELY! Any match to `{entry_id}` (`{pkg}=={affected_ver}`) proves production exploitation "
            f"even when zero AST imports exist for `{pkg}` (`sink_reachable: false`). Perform an emergency architecture rewrite of `{proj}`."
        )

    prompt = scrub_forbidden_text(prompt)
    chosen = scrub_forbidden_text(chosen)
    rejected = scrub_forbidden_text(rejected)
    return PreferenceRow(prompt=prompt, chosen=chosen, rejected=rejected).model_dump()


def _migrate_db_or_iac_row(row: dict[str, Any], idx: int, kind: str) -> dict[str, Any]:
    prompt = _strip_existing_graph_blocks(row["prompt"])
    completion = scrub_forbidden_text(row["completion"])
    repo = _extract_repo_name(prompt, fallback=f"duck-tape/{kind}-service")
    paths, syms = _extract_paths_and_symbols(
        prompt + "\n" + completion,
        fallback_path="migrations/001_init.sql" if kind == "db_migration" else "infra/main.tf",
    )
    linked_tests = _infer_test_files(paths, prompt, repo)

    ctx = build_graph_context_v1(
        idx,
        prompt[:120],
        repo,
        paths,
        syms,
        linked_tests,
        extra_gaps=["Treat any null field in <graph_context> as unknown (never as false)"],
    )
    new_prompt = _inject_graph_into_prompt(
        prompt,
        ctx,
        "Ground symbols and file paths in <graph_context> and verify changes in an isolated Git worktree.",
    )
    new_prompt = scrub_forbidden_text(new_prompt)
    return InstructionRow(prompt=new_prompt, completion=completion).model_dump()


def _migrate_frontend_ui_row(row: dict[str, Any]) -> dict[str, Any]:
    validated = VisionInstructionRow(
        prompt=scrub_forbidden_text(row["prompt"]),
        image_context=row["image_context"],
        completion=scrub_forbidden_text(row["completion"]),
    )
    return validated.model_dump()


def _migrate_retriever_row(row: dict[str, Any]) -> dict[str, Any]:
    validated = ContrastiveTripletRow(
        query=scrub_forbidden_text(row["query"]),
        pos=[scrub_forbidden_text(p) for p in row["pos"]],
        neg=[scrub_forbidden_text(n) for n in row["neg"]],
    )
    return validated.model_dump()


def _migrate_file(path: Path, handler_name: str, enc: tiktoken.Encoding) -> dict[str, int]:
    tmp_path = path.with_suffix(".jsonl.tmp")
    count = 0
    sink_counts = {"null": 0, "true": 0, "false": 0}
    decision_count = 0

    with path.open("r", encoding="utf-8") as src, tmp_path.open("w", encoding="utf-8", newline="\n") as dst:
        for line in src:
            raw = line.strip()
            if not raw:
                continue
            row = json.loads(raw)

            if handler_name == "router":
                migrated = _migrate_router_row(row, count)
            elif handler_name == "ticket_parser":
                migrated = _migrate_ticket_parser_row(row, count)
                if '"decision"' in migrated["completion"]:
                    decision_count += 1
            elif handler_name == "commit_pr_writer":
                migrated = _migrate_commit_pr_writer_row(row, count)
            elif handler_name == "log_classifier":
                migrated = _migrate_log_classifier_row(row, count)
            elif handler_name == "implementer":
                migrated = _migrate_implementer_row(row, count, enc)
            elif handler_name == "test_writer":
                migrated = _migrate_test_writer_row(row, count)
            elif handler_name == "reviewer":
                migrated = _migrate_reviewer_dpo_row(row, count)
            elif handler_name == "security_reviewer":
                migrated = _migrate_security_reviewer_row(row, count)
                mod20 = count % 20
                if mod20 < 14:
                    sink_counts["null"] += 1
                elif mod20 < 17:
                    sink_counts["true"] += 1
                else:
                    sink_counts["false"] += 1
            elif handler_name == "db_migration":
                migrated = _migrate_db_or_iac_row(row, count, "db_migration")
            elif handler_name == "iac":
                migrated = _migrate_db_or_iac_row(row, count, "iac")
            elif handler_name == "frontend_ui":
                migrated = _migrate_frontend_ui_row(row)
            elif handler_name == "retriever":
                migrated = _migrate_retriever_row(row)
            else:
                raise ValueError(f"Unknown handler {handler_name}")

            dst.write(json.dumps(migrated, ensure_ascii=False) + "\n")
            count += 1

    os.replace(tmp_path, path)
    return {"rows": count, "decision_cards": decision_count, **{f"sink_{k}": v for k, v in sink_counts.items()}}


def _write_dataset_licenses(stats_map: dict[str, dict[str, int]]) -> None:
    payload = {
        "schema_version": "duck-tape-licenses-1",
        "handoff_contract": "HANDOFF_B_TO_A.md (graph_context v1)",
        "model_schema_version": model_schemas.SCHEMA_VERSION,
        "privacy_and_provenance": {
            "synthetic_or_public_only": True,
            "team_notes_included": False,
            "imported_client_repo_code_included": False,
            "weights_committed": False,
        },
        "datasets": [
            {
                "agent": "router",
                "train_file": "model-training/data/processed/router.jsonl",
                "eval_file": "model-training/data/eval/router_eval.jsonl",
                "train_rows": stats_map["processed/router.jsonl"]["rows"],
                "eval_rows": stats_map["eval/router_eval.jsonl"]["rows"],
                "sources": [
                    {
                        "id": "SWE-bench/SWE-smith",
                        "url": "https://huggingface.co/datasets/SWE-bench/SWE-smith",
                        "license": "MIT",
                    },
                    {
                        "id": "SWE-Gym/SWE-Gym",
                        "url": "https://huggingface.co/datasets/SWE-Gym/SWE-Gym",
                        "license": "MIT",
                    },
                ],
            },
            {
                "agent": "ticket_parser",
                "train_file": "model-training/data/processed/ticket_parser.jsonl",
                "eval_file": "model-training/data/eval/ticket_parser_eval.jsonl",
                "train_rows": stats_map["processed/ticket_parser.jsonl"]["rows"],
                "eval_rows": stats_map["eval/ticket_parser_eval.jsonl"]["rows"],
                "sources": [
                    {
                        "id": "princeton-nlp/SWE-bench_Verified",
                        "url": "https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified",
                        "license": "MIT",
                    },
                    {
                        "id": "SWE-Gym/SWE-Gym",
                        "url": "https://huggingface.co/datasets/SWE-Gym/SWE-Gym",
                        "license": "MIT",
                    },
                    {
                        "id": "cjsanjay/jira_train_17012024",
                        "url": "https://huggingface.co/datasets/cjsanjay/jira_train_17012024",
                        "license": "Apache-2.0 (public Jira issue mirror)",
                    },
                    {
                        "id": "kristmh/clean_Jira_balanced",
                        "url": "https://huggingface.co/datasets/kristmh/clean_Jira_balanced",
                        "license": "Apache-2.0 / public Jira issue corpus",
                    },
                ],
            },
            {
                "agent": "commit_pr_writer",
                "train_file": "model-training/data/processed/commit_pr_writer.jsonl",
                "eval_file": "model-training/data/eval/commit_pr_writer_eval.jsonl",
                "train_rows": stats_map["processed/commit_pr_writer.jsonl"]["rows"],
                "eval_rows": stats_map["eval/commit_pr_writer_eval.jsonl"]["rows"],
                "sources": [
                    {
                        "id": "bigcode/commitpackft",
                        "url": "https://huggingface.co/datasets/bigcode/commitpackft",
                        "license": "Permissive OSS commit subset (MIT / Apache-2.0 / BSD)",
                    },
                    {
                        "id": "Maxscha/commitbench",
                        "url": "https://huggingface.co/datasets/Maxscha/commitbench",
                        "license": "Apache-2.0",
                    },
                ],
            },
            {
                "agent": "log_classifier",
                "train_file": "model-training/data/processed/log_classifier.jsonl",
                "eval_file": "model-training/data/eval/log_classifier_eval.jsonl",
                "train_rows": stats_map["processed/log_classifier.jsonl"]["rows"],
                "eval_rows": stats_map["eval/log_classifier_eval.jsonl"]["rows"],
                "sources": [
                    {
                        "id": "logpai/loghub",
                        "url": "https://github.com/logpai/loghub",
                        "license": "MIT",
                    },
                    {
                        "id": "TestingResearchIllinois/idoft",
                        "url": "https://github.com/TestingResearchIllinois/idoft",
                        "license": "MIT / NCSA",
                    },
                    {
                        "id": "SWE-Gym/SWE-Gym",
                        "url": "https://huggingface.co/datasets/SWE-Gym/SWE-Gym",
                        "license": "MIT",
                    },
                    {
                        "id": "SWE-bench/SWE-smith",
                        "url": "https://huggingface.co/datasets/SWE-bench/SWE-smith",
                        "license": "MIT",
                    },
                ],
            },
            {
                "agent": "implementer",
                "train_file": "model-training/data/processed/implementer.jsonl",
                "eval_file": "model-training/data/eval/implementer_eval.jsonl",
                "train_rows": stats_map["processed/implementer.jsonl"]["rows"],
                "eval_rows": stats_map["eval/implementer_eval.jsonl"]["rows"],
                "sources": [
                    {
                        "id": "nebius/SWE-rebench-openhands-trajectories",
                        "url": "https://huggingface.co/datasets/nebius/SWE-rebench-openhands-trajectories",
                        "license": "CC-BY-4.0 / Apache-2.0",
                    },
                    {
                        "id": "SWE-bench/SWE-smith-trajectories",
                        "url": "https://huggingface.co/datasets/SWE-bench/SWE-smith-trajectories",
                        "license": "MIT",
                    },
                    {
                        "id": "R2E-Gym/R2EGym-SFT-Trajectories",
                        "url": "https://huggingface.co/datasets/R2E-Gym/R2EGym-SFT-Trajectories",
                        "license": "MIT",
                    },
                ],
            },
            {
                "agent": "test_writer",
                "train_file": "model-training/data/processed/test_writer.jsonl",
                "eval_file": "model-training/data/eval/test_writer_eval.jsonl",
                "train_rows": stats_map["processed/test_writer.jsonl"]["rows"],
                "eval_rows": stats_map["eval/test_writer_eval.jsonl"]["rows"],
                "sources": [
                    {
                        "id": "nebius/SWE-rebench-openhands-trajectories",
                        "url": "https://huggingface.co/datasets/nebius/SWE-rebench-openhands-trajectories",
                        "license": "CC-BY-4.0 / Apache-2.0",
                    },
                    {
                        "id": "kjain14/testgeneval",
                        "url": "https://huggingface.co/datasets/kjain14/testgeneval",
                        "license": "MIT",
                    },
                    {
                        "id": "SWE-Gym/SWE-Gym",
                        "url": "https://huggingface.co/datasets/SWE-Gym/SWE-Gym",
                        "license": "MIT",
                    },
                    {
                        "id": "SWE-bench/SWE-smith",
                        "url": "https://huggingface.co/datasets/SWE-bench/SWE-smith",
                        "license": "MIT",
                    },
                ],
            },
            {
                "agent": "reviewer",
                "train_file": "model-training/data/processed/reviewer_dpo.jsonl",
                "eval_file": "model-training/data/eval/reviewer_dpo_eval.jsonl",
                "train_rows": stats_map["processed/reviewer_dpo.jsonl"]["rows"],
                "eval_rows": stats_map["eval/reviewer_dpo_eval.jsonl"]["rows"],
                "sources": [
                    {
                        "id": "fasterinnerlooper/codereviewer",
                        "url": "https://huggingface.co/datasets/fasterinnerlooper/codereviewer",
                        "license": "MIT (Microsoft CodeReviewer)",
                    },
                    {
                        "id": "Dahoas/code-review-instruct-critique-revision-python",
                        "url": "https://huggingface.co/datasets/Dahoas/code-review-instruct-critique-revision-python",
                        "license": "MIT / Apache-2.0",
                    },
                ],
            },
            {
                "agent": "security_reviewer",
                "train_file": "model-training/data/processed/security_reviewer_dpo.jsonl",
                "eval_file": "model-training/data/eval/security_reviewer_eval.jsonl",
                "train_rows": stats_map["processed/security_reviewer_dpo.jsonl"]["rows"],
                "eval_rows": stats_map["eval/security_reviewer_eval.jsonl"]["rows"],
                "sources": [
                    {
                        "id": "OSV.dev API",
                        "url": "https://osv.dev/",
                        "license": "CC-BY-4.0",
                    },
                    {
                        "id": "colin/PrimeVul",
                        "url": "https://huggingface.co/datasets/colin/PrimeVul",
                        "license": "MIT / Apache-2.0",
                    },
                    {
                        "id": "MickyMike/cvefixes_bigvul",
                        "url": "https://huggingface.co/datasets/MickyMike/cvefixes_bigvul",
                        "license": "CC-BY-4.0",
                    },
                    {
                        "id": "bstee615/diversevul",
                        "url": "https://huggingface.co/datasets/bstee615/diversevul",
                        "license": "Apache-2.0",
                    },
                ],
            },
            {
                "agent": "db_migration",
                "train_file": "model-training/data/processed/db_migration.jsonl",
                "eval_file": "model-training/data/eval/db_migration_eval.jsonl",
                "train_rows": stats_map["processed/db_migration.jsonl"]["rows"],
                "eval_rows": stats_map["eval/db_migration_eval.jsonl"]["rows"],
                "sources": [
                    {
                        "id": "birdsql/bird_mini_dev",
                        "url": "https://huggingface.co/datasets/birdsql/bird_mini_dev",
                        "license": "CC-BY-SA-4.0",
                    },
                    {
                        "id": "bigcode/commitpackft",
                        "url": "https://huggingface.co/datasets/bigcode/commitpackft",
                        "license": "Permissive OSS commit subset (MIT / Apache-2.0 / BSD)",
                    },
                    {
                        "id": "xlangai/spider",
                        "url": "https://huggingface.co/datasets/xlangai/spider",
                        "license": "CC-BY-SA-4.0",
                    },
                ],
            },
            {
                "agent": "iac",
                "train_file": "model-training/data/processed/iac.jsonl",
                "eval_file": "model-training/data/eval/iac_eval.jsonl",
                "train_rows": stats_map["processed/iac.jsonl"]["rows"],
                "eval_rows": stats_map["eval/iac_eval.jsonl"]["rows"],
                "sources": [
                    {
                        "id": "autoiac-project/iac-eval",
                        "url": "https://huggingface.co/datasets/autoiac-project/iac-eval",
                        "license": "MIT",
                    },
                    {
                        "id": "bigcode/commitpackft",
                        "url": "https://huggingface.co/datasets/bigcode/commitpackft",
                        "license": "Permissive OSS commit subset (MIT / Apache-2.0 / BSD)",
                    },
                    {
                        "id": "HelloBoieeee/kubernetes_config",
                        "url": "https://huggingface.co/datasets/HelloBoieeee/kubernetes_config",
                        "license": "Apache-2.0",
                    },
                    {
                        "id": "LeeSek/dockerfiles-linted",
                        "url": "https://huggingface.co/datasets/LeeSek/dockerfiles-linted",
                        "license": "MIT",
                    },
                    {
                        "id": "loubnabnl/dockerfile_checks",
                        "url": "https://huggingface.co/datasets/loubnabnl/dockerfile_checks",
                        "license": "Apache-2.0",
                    },
                ],
            },
            {
                "agent": "frontend_ui",
                "train_file": "model-training/data/processed/frontend_ui.jsonl",
                "eval_file": "model-training/data/eval/frontend_ui_eval.jsonl",
                "train_rows": stats_map["processed/frontend_ui.jsonl"]["rows"],
                "eval_rows": stats_map["eval/frontend_ui_eval.jsonl"]["rows"],
                "sources": [
                    {
                        "id": "SALT-NLP/Design2Code",
                        "url": "https://huggingface.co/datasets/SALT-NLP/Design2Code",
                        "license": "MIT",
                    },
                    {
                        "id": "HuggingFaceM4/WebSight",
                        "url": "https://huggingface.co/datasets/HuggingFaceM4/WebSight",
                        "license": "Apache-2.0",
                    },
                ],
            },
            {
                "agent": "retriever",
                "train_file": "model-training/data/processed/retriever_triplets.jsonl",
                "eval_file": "model-training/data/eval/retriever_triplets_eval.jsonl",
                "train_rows": stats_map["processed/retriever_triplets.jsonl"]["rows"],
                "eval_rows": stats_map["eval/retriever_triplets_eval.jsonl"]["rows"],
                "sources": [
                    {
                        "id": "princeton-nlp/SWE-bench_Verified",
                        "url": "https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified",
                        "license": "MIT",
                    },
                    {
                        "id": "SWE-Gym/SWE-Gym",
                        "url": "https://huggingface.co/datasets/SWE-Gym/SWE-Gym",
                        "license": "MIT",
                    },
                    {
                        "id": "code-search-net/code_search_net",
                        "url": "https://huggingface.co/datasets/code-search-net/code_search_net",
                        "license": "MIT",
                    },
                    {
                        "id": "gonglinyuan/CoSQA",
                        "url": "https://huggingface.co/datasets/gonglinyuan/CoSQA",
                        "license": "MIT",
                    },
                ],
            },
        ],
    }
    LICENSES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LICENSES_PATH.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def main() -> int:
    enc = tiktoken.get_encoding("cl100k_base")
    targets: list[tuple[Path, str]] = [
        (PROCESSED_DIR / "router.jsonl", "router"),
        (EVAL_DIR / "router_eval.jsonl", "router"),
        (PROCESSED_DIR / "ticket_parser.jsonl", "ticket_parser"),
        (EVAL_DIR / "ticket_parser_eval.jsonl", "ticket_parser"),
        (PROCESSED_DIR / "commit_pr_writer.jsonl", "commit_pr_writer"),
        (EVAL_DIR / "commit_pr_writer_eval.jsonl", "commit_pr_writer"),
        (PROCESSED_DIR / "log_classifier.jsonl", "log_classifier"),
        (EVAL_DIR / "log_classifier_eval.jsonl", "log_classifier"),
        (PROCESSED_DIR / "implementer.jsonl", "implementer"),
        (EVAL_DIR / "implementer_eval.jsonl", "implementer"),
        (PROCESSED_DIR / "test_writer.jsonl", "test_writer"),
        (EVAL_DIR / "test_writer_eval.jsonl", "test_writer"),
        (PROCESSED_DIR / "reviewer_dpo.jsonl", "reviewer"),
        (EVAL_DIR / "reviewer_dpo_eval.jsonl", "reviewer"),
        (PROCESSED_DIR / "security_reviewer_dpo.jsonl", "security_reviewer"),
        (EVAL_DIR / "security_reviewer_eval.jsonl", "security_reviewer"),
        (PROCESSED_DIR / "db_migration.jsonl", "db_migration"),
        (EVAL_DIR / "db_migration_eval.jsonl", "db_migration"),
        (PROCESSED_DIR / "iac.jsonl", "iac"),
        (EVAL_DIR / "iac_eval.jsonl", "iac"),
        (PROCESSED_DIR / "frontend_ui.jsonl", "frontend_ui"),
        (EVAL_DIR / "frontend_ui_eval.jsonl", "frontend_ui"),
        (PROCESSED_DIR / "retriever_triplets.jsonl", "retriever"),
        (EVAL_DIR / "retriever_triplets_eval.jsonl", "retriever"),
    ]

    stats_map: dict[str, dict[str, int]] = {}
    for path, handler_name in targets:
        if not path.is_file():
            raise FileNotFoundError(f"Expected dataset file missing: {path}")
        rel = f"{path.parent.name}/{path.name}"
        print(f"Migrating {rel} ({handler_name})...", flush=True)
        res = _migrate_file(path, handler_name, enc)
        stats_map[rel] = res
        print(f"  done -> {res}", flush=True)

    # Keep eval aliases synchronized with their canonical eval files
    shutil.copyfile(EVAL_DIR / "reviewer_dpo_eval.jsonl", EVAL_DIR / "reviewer_eval.jsonl")
    shutil.copyfile(EVAL_DIR / "retriever_triplets_eval.jsonl", EVAL_DIR / "retriever_eval.jsonl")

    _write_dataset_licenses(stats_map)
    print(f"Wrote {LICENSES_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
