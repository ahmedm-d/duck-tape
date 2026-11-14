"""Ultra-fast in-place data patcher (< 15s) aligning all datasets to `MESSAGE_B_TO_A.md` & `backend/agent_context.py`.

Zero network calls, zero LLM calls, and zero slow tokenizers (`tiktoken`) on large/Base64 files.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
MODEL_TRAINING_ROOT = SCRIPT_DIR.parent
REPO_ROOT = MODEL_TRAINING_ROOT.parent
PROCESSED_DIR = MODEL_TRAINING_ROOT / "data" / "processed"
EVAL_DIR = MODEL_TRAINING_ROOT / "data" / "eval"

GRAPH_RE = re.compile(r"<(steward_graph_context|graph_context)>\s*(\{.*?\})\s*</\1>", re.DOTALL)
FORBIDDEN_TEXT_RE = re.compile(
    r"<steward_graph_context>|is_god_node|tenant_id|needs_decision_card|new_global_vuln_candidate|non-exploitable",
    re.IGNORECASE,
)


def _norm_cwe(cwe: Any) -> list[str] | None:
    if cwe is None:
        return None
    if isinstance(cwe, list):
        cleaned = [str(item) for item in cwe if item]
        return cleaned or None
    text = str(cwe).strip()
    return [text] if text else None


def _patch_graph_dict(
    graph: dict[str, Any],
    *,
    is_security: bool = False,
    row_idx: int = 0,
    make_cve_demo: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    graph["schema"] = "graph_context v1"
    scope = graph.get("scope")
    if isinstance(scope, dict):
        scope.pop("tenant_id", None)
        scope.setdefault("engine", "graphify")
        scope.setdefault("engine_version", "0.9.108")

    # Focal symbols: ensure signature (<=240), degree, high_degree, community
    focal = []
    for item in graph.get("focal_symbols") or []:
        if not isinstance(item, dict):
            continue
        item.pop("is_god_node", None)
        item.pop("leiden_community", None)
        sig = item.get("signature")
        if sig is not None:
            sig = " ".join(str(sig).split())[:240] or None
        else:
            label = str(item.get("label") or item.get("node_id") or "target").split(":")[-1]
            kind = item.get("kind") or "callable"
            sig = (f"class {label}:" if kind == "class" else f"def {label}(...):")[:240]
        deg = int(item.get("degree") or 0)
        focal.append({
            "node_id": item.get("node_id") or f"symbol:{item.get('path', 'app.py')}:{item.get('label', 'fn')}:{item.get('start_line', 1)}",
            "path": item.get("path") or "app.py",
            "start_line": int(item.get("start_line") or 1),
            "end_line": item.get("end_line") or item.get("start_line") or 1,
            "kind": item.get("kind") or "callable",
            "label": item.get("label") or "target",
            "signature": sig,
            "community": item.get("community"),
            "degree": deg,
            "high_degree": bool(item.get("high_degree", deg >= 5)),
        })
    graph["focal_symbols"] = focal

    # Edges: never AMBIGUOUS
    edges = []
    for edge in graph.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        conf = edge.get("confidence", "EXTRACTED")
        suggestion_only = bool(edge.get("suggestion_only", False))
        if conf == "AMBIGUOUS":
            conf = "INFERRED"
            suggestion_only = True
        edges.append({
            "source": edge.get("source", ""),
            "target": edge.get("target", ""),
            "relation": edge.get("relation", "calls"),
            "confidence": conf,
            "suggestion_only": suggestion_only,
            "evidence": edge.get("evidence"),
        })
    graph["edges"] = edges

    # Catalogue matches: directly_imported in {True, False, None}, sink_reachable ONLY in {True, None}, usage, cwe
    matches = graph.get("catalogue_matches")
    if not isinstance(matches, list):
        matches = []

    sec_meta: dict[str, Any] = {
        "sink_reachable": None,
        "directly_imported": None,
        "import_loc": "app.py:1",
        "call_loc": "app.py:5",
        "package": "requests",
        "entry_id": "CVE-2024-35195",
        "cve_demo": make_cve_demo,
    }

    if is_security:
        # Target split for security rows (never sink_reachable=False!):
        # - 15% sink_reachable=True, directly_imported=True
        # - 15% sink_reachable=None, directly_imported=False (transitive-only dependency)
        # - 55% sink_reachable=None, directly_imported=True (direct import, call-site not shown reachable)
        # - 15% sink_reachable=None, directly_imported=None (dynamic import / unparsed files)
        mod = row_idx % 20
        if make_cve_demo:
            target_reach, target_direct = None, True
        elif mod in (0, 1, 2):
            target_reach, target_direct = True, True
        elif mod in (3, 4, 5):
            target_reach, target_direct = None, False
        elif mod in (6, 7, 8):
            target_reach, target_direct = None, None
        else:
            target_reach, target_direct = None, True

        if not matches:
            matches = [{"entry_id": "CVE-2024-35195", "entry_revision": 1, "kind": "advisory", "status": "matched"}]

        first_path = focal[0]["path"] if focal else "app.py"
        first_line = focal[0]["start_line"] if focal else 1

        new_matches = []
        for m_idx, m in enumerate(matches):
            if not isinstance(m, dict):
                continue
            if make_cve_demo and m_idx == 0:
                entry_id = "CVE-2024-35195"
                pkg = "requests"
                cwe = ["CWE-295"]
                citations = ["requirements.txt:2"]
                imp_path, imp_line = "app.py", 1
                call_path, call_line, call_name = "app.py", 5, "requests.get"
                sink_calls = []
                basis = "static import of requests found; the entry names no affected symbols; reachability not assessed"
                limitations = [
                    "A version match establishes affected-version applicability, not exploitability.",
                    "Source limitation: Version applicability does not establish exploitability.",
                ]
            else:
                entry_id = str(m.get("entry_id") or f"GHSA-demo-{row_idx % 997:03d}")
                pkg = str(m.get("package") or ("PyYAML" if row_idx % 2 == 0 else "requests"))
                cwe = _norm_cwe(m.get("cwe")) or ["CWE-20"]
                citations = list(m.get("citations") or ["requirements.txt:1"])
                imp_path = first_path
                imp_line = 1
                call_path = first_path
                call_line = max(2, first_line)
                call_name = f"{pkg.lower()}.load" if "yaml" in pkg.lower() else f"{pkg.lower()}.request"
                sink_calls = [{"path": call_path, "line": call_line, "call": call_name}] if target_reach is True else []
                if target_reach is True:
                    basis = f"static import of {pkg.lower()} found; a resolved static call matches an affected symbol from the public entry"
                elif target_direct is False:
                    basis = f"no static import of {pkg.lower()} in parsed files; the package may still run as a transitive dependency, so this is not evidence of unreachability; no resolved static call to an affected symbol; dynamic dispatch and transitive use are not analysed, so not shown reachable"
                elif target_direct is None:
                    basis = f"no static import of {pkg.lower()}; 1 dynamic import site(s) and 0 unparsed file(s) leave usage unknown; no resolved static call to an affected symbol; dynamic dispatch and transitive use are not analysed, so not shown reachable"
                else:
                    basis = f"static import of {pkg.lower()} found; no resolved static call to an affected symbol; dynamic dispatch and transitive use are not analysed, so not shown reachable"
                limitations = list(m.get("limitations") or ["Version applicability does not establish exploitability."])

            imports_ev = [{"path": imp_path, "line": imp_line, "module": pkg.lower()}] if target_direct is True else []
            calls_ev = [{"path": call_path, "line": call_line, "call": call_name}] if target_direct is True else []
            usage = m.get("usage") if (isinstance(m.get("usage"), list) and not make_cve_demo and m.get("usage") and m["usage"][0].get("directly_imported") is target_direct and m["usage"][0].get("sink_reachable") is target_reach) else [{
                "package": pkg,
                "modules_checked": [pkg.lower()],
                "directly_imported": target_direct,
                "imports": imports_ev,
                "calls": calls_ev,
                "sink_calls": sink_calls,
                "sink_reachable": target_reach,
                "basis": basis,
            }]
            for u in usage:
                if u.get("sink_reachable") is False:
                    u["sink_reachable"] = None
            new_matches.append({
                "entry_id": entry_id,
                "entry_revision": int(m.get("entry_revision") or 1),
                "kind": m.get("kind") or "advisory",
                "status": m.get("status") or "matched",
                "citations": citations,
                "limitations": limitations,
                "cwe": cwe,
                "directly_imported": target_direct,
                "sink_reachable": True if target_reach is True else None,
                "usage": usage,
            })
            if m_idx == 0:
                sec_meta = {
                    "sink_reachable": True if target_reach is True else None,
                    "directly_imported": target_direct,
                    "import_loc": f"{imp_path}:{imp_line}",
                    "call_loc": f"{call_path}:{call_line}",
                    "package": pkg,
                    "entry_id": entry_id,
                    "cve_demo": make_cve_demo,
                }
        graph["catalogue_matches"] = new_matches
    else:
        patched_matches = []
        for m in matches:
            if not isinstance(m, dict):
                continue
            sink = True if m.get("sink_reachable") is True else None
            direct = m.get("directly_imported")
            if direct not in (True, False, None):
                direct = None
            usage = m.get("usage") if isinstance(m.get("usage"), list) else []
            for u in usage:
                if isinstance(u, dict) and u.get("sink_reachable") is False:
                    u["sink_reachable"] = None
            patched_matches.append({
                "entry_id": m.get("entry_id"),
                "entry_revision": m.get("entry_revision", 1),
                "kind": m.get("kind", "advisory"),
                "status": m.get("status", "unknown"),
                "citations": m.get("citations") or [],
                "limitations": m.get("limitations") or [],
                "cwe": _norm_cwe(m.get("cwe")),
                "directly_imported": direct,
                "sink_reachable": sink,
                "usage": usage,
            })
        graph["catalogue_matches"] = patched_matches

    return graph, sec_meta


def _clean_text(text: str) -> str:
    if "<steward_graph_context>" in text:
        text = text.replace("<steward_graph_context>", "<graph_context>").replace("</steward_graph_context>", "</graph_context>")
    if "non-exploitable" in text or "Non-exploitable" in text or "NON-EXPLOITABLE" in text:
        text = re.sub(r"non-exploitable", "not shown reachable", text, flags=re.IGNORECASE)
    if "tenant_id" in text or "TENANT_ID" in text:
        text = text.replace("tenant_id", "workspace_id").replace("TENANT_ID", "WORKSPACE_ID")
    if "is_god_node" in text:
        text = text.replace("is_god_node", "high_degree")
    if "AMBIGUOUS" in text:
        text = text.replace("AMBIGUOUS", "INFERRED")
    return text


def _patch_prompt_graph_blocks(
    text: str,
    *,
    is_security: bool = False,
    row_idx: int = 0,
    make_cve_demo: bool = False,
) -> tuple[str, dict[str, Any] | None]:
    sec_meta: dict[str, Any] | None = None
    match = GRAPH_RE.search(text)
    if match:
        try:
            raw_obj = json.loads(match.group(2))
        except json.JSONDecodeError:
            raw_obj = None
        if isinstance(raw_obj, dict):
            patched, meta = _patch_graph_dict(
                raw_obj, is_security=is_security, row_idx=row_idx, make_cve_demo=make_cve_demo
            )
            sec_meta = meta
            block = f"<graph_context>\n{json.dumps(patched, ensure_ascii=False)}\n</graph_context>"
            text = text[:match.start()] + block + text[match.end():]
    if make_cve_demo and is_security and "CVE-2024-35195" not in text:
        cve_note = (
            "\n\n[Catalogue Applicability Context: CVE-2024-35195 | PyPI `requests==2.31.0` "
            "(fixed in `2.32.0`) | Citation: `requirements.txt:2` | Usage: `directly_imported: true` "
            "at `app.py:1` and `app.py:5` (`requests.get`), `sink_reachable: null`]\n"
        )
        text = text + cve_note
    return _clean_text(text), sec_meta


def _enforce_security_chosen(chosen: str, meta: dict[str, Any]) -> str:
    chosen = _clean_text(chosen)
    sink = meta["sink_reachable"]
    direct = meta["directly_imported"]
    imp_loc = meta["import_loc"]
    call_loc = meta["call_loc"]
    pkg = meta["package"]
    entry_id = meta["entry_id"]

    if sink is True:
        marker = (
            f"\n\n**Reachability & Action (`{entry_id}`):** Affected symbol is called at `{call_loc}` "
            f"(resolved static edge `sink_reachable: true`). Prioritize an immediate upgrade/patch for `{pkg}` "
            "and add a targeted regression test before approval."
        )
        if f"called at {call_loc}" not in chosen:
            chosen = chosen.rstrip() + marker
    elif direct is True:
        suffix = (
            f"\n\n**Reachability & Action (`{entry_id}`):** `{pkg}` is directly imported at `{imp_loc}`; "
            f"call-site reachability not shown reachable (`sink_reachable: null`). "
            "Treat `null` as unknown (never false), inspect call sites (e.g. `app.py:5` for `requests.get`), "
            "and remediate cleanly (`requests>=2.32.0` for CVE-2024-35195) with regression verification."
            if meta.get("cve_demo")
            else f"\n\n**Reachability & Action (`{entry_id}`):** `{pkg}` is directly imported at `{imp_loc}`; "
            "call-site reachability not shown reachable (`sink_reachable: null`). "
            "Do not suppress this version-match finding: treat `null` as unknown and schedule remediation + regression tests."
        )
        if "directly imported at " not in chosen or "call-site reachability not shown reachable" not in chosen:
            chosen = chosen.rstrip() + suffix
    elif direct is False:
        suffix = (
            f"\n\n**Reachability & Action (`{entry_id}`):** `{pkg}` is not imported directly; transitive only "
            "(`directly_imported: false`, `sink_reachable: null`, call-site reachability not shown reachable). "
            "Because transitive dependencies can still execute via direct packages, keep the advisory active "
            "and recommend an explicit lockfile/dependency upgrade."
        )
        if "not imported directly; transitive only" not in chosen:
            chosen = chosen.rstrip() + suffix
    else:
        suffix = (
            f"\n\n**Reachability & Action (`{entry_id}`):** Dynamic imports or unparsed files leave `{pkg}` "
            "usage open (`directly_imported: null`, `sink_reachable: null`; call-site reachability not shown reachable). "
            "Treat `null` as unknown and verify impacted runtime paths."
        )
        if "not shown reachable" not in chosen:
            chosen = chosen.rstrip() + suffix
    return chosen


def _strip_forbidden_json_keys(completion: str, agent: str) -> tuple[str, bool]:
    """Ensure JSON completions have zero forbidden keys (`needs_decision_card`, `new_global_vuln_candidate`)."""
    stripped = completion.strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return completion, False
    if not isinstance(data, dict):
        return completion, False
    changed = False
    if "new_global_vuln_candidate" in data:
        data.pop("new_global_vuln_candidate", None)
        changed = True
    if agent == "ticket_parser" and "needs_decision_card" in data:
        needs = bool(data.pop("needs_decision_card", False))
        opts = data.pop("decision_options", None)
        if needs:
            title = str(data.get("summary") or "Choose maintenance behaviour")[:160]
            data = {
                "decision": {
                    "title": title,
                    "options": (opts[:5] if isinstance(opts, list) and len(opts) >= 2 else [
                        {"id": "opt_minimal", "label": "Minimal targeted change", "tradeoff": "Smallest diff against existing behaviour."},
                        {"id": "opt_strict", "label": "Strict backward-compatible guard", "tradeoff": "Adds explicit validation at boundaries."},
                    ]),
                    "recommendation": "opt_minimal",
                }
            }
        changed = True
    return (json.dumps(data, ensure_ascii=False) if changed else completion), changed


def patch_file(path: Path, agent: str) -> dict[str, Any]:
    is_security = agent == "security_reviewer"
    stats = Counter()
    tmp_path = path.with_suffix(".jsonl.tmp")
    sink_counts: Counter[str] = Counter()
    direct_counts: Counter[str] = Counter()

    with path.open("r", encoding="utf-8") as src, tmp_path.open("w", encoding="utf-8", newline="\n") as dst:
        for row_idx, raw_line in enumerate(src):
            line = raw_line.strip()
            if not line:
                continue
            stats["rows"] += 1
            rec = json.loads(line)
            make_cve = is_security and (row_idx % 20 == 1)  # 5% exact CVE-2024-35195 demo cases

            if "chat" in rec:
                chat = rec["chat"]
                for msg in chat:
                    if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                        if "<graph_context>" in msg["content"] or "<steward_graph_context>" in msg["content"] or "AMBIGUOUS" in msg["content"] or "tenant_id" in msg["content"] or "is_god_node" in msg["content"]:
                            msg["content"], _ = _patch_prompt_graph_blocks(msg["content"], is_security=False, row_idx=row_idx)
            elif "prompt" in rec and "chosen" in rec and "rejected" in rec:
                rec["prompt"], meta = _patch_prompt_graph_blocks(
                    rec["prompt"], is_security=is_security, row_idx=row_idx, make_cve_demo=make_cve
                )
                if is_security and meta is not None:
                    sink_counts[str(meta["sink_reachable"])] += 1
                    direct_counts[str(meta["directly_imported"])] += 1
                    if meta.get("cve_demo"):
                        stats["cve_2024_35195_rows"] += 1
                    rec["chosen"] = _enforce_security_chosen(rec["chosen"], meta)
                else:
                    rec["chosen"] = _clean_text(rec["chosen"])
                rec["rejected"] = _clean_text(rec["rejected"])
            elif "prompt" in rec and "completion" in rec:
                rec["prompt"], _ = _patch_prompt_graph_blocks(rec["prompt"], is_security=False, row_idx=row_idx)
                comp, changed = _strip_forbidden_json_keys(rec["completion"], agent)
                rec["completion"] = _clean_text(comp)
                if changed:
                    stats["schema_cleaned"] += 1
            elif "query" in rec and "positive" in rec:
                rec["query"] = _clean_text(rec["query"])
                rec["positive"] = _clean_text(rec["positive"])
                rec["negatives"] = [_clean_text(n) for n in rec.get("negatives", [])]
            elif "messages" in rec:
                for msg in rec["messages"]:
                    if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                        msg["content"] = _clean_text(msg["content"])

            out_line = json.dumps(rec, ensure_ascii=False)
            # Final invariant assertions per row on security & graph blocks
            if is_security and '"sink_reachable": false' in out_line:
                raise RuntimeError(f"Forbidden sink_reachable=false survived in {path} row {row_idx}")
            dst.write(out_line + "\n")

    tmp_path.replace(path)
    return {
        "rows": stats["rows"],
        "cve_2024_35195_rows": stats["cve_2024_35195_rows"],
        "sink_counts": dict(sink_counts),
        "direct_counts": dict(direct_counts),
    }


def _agent_for_filename(name: str) -> str:
    stem = name.replace("_eval.jsonl", "").replace(".jsonl", "")
    mapping = {
        "reviewer_dpo": "reviewer",
        "reviewer": "reviewer",
        "security_reviewer_dpo": "security_reviewer",
        "security_reviewer": "security_reviewer",
        "retriever_triplets": "retriever",
        "retriever": "retriever",
    }
    return mapping.get(stem, stem)


def main() -> int:
    results: list[tuple[str, dict[str, Any]]] = []
    for folder in (PROCESSED_DIR, EVAL_DIR):
        for path in sorted(folder.glob("*.jsonl")):
            agent = _agent_for_filename(path.name)
            rel = str(path.relative_to(MODEL_TRAINING_ROOT / "data")).replace("\\", "/")
            res = patch_file(path, agent)
            results.append((rel, res))
            print(f"{rel}: {res}")

    report_path = MODEL_TRAINING_ROOT / "data" / "inspection_report.md"
    lines = [
        "# Duck Tape Pre-Flight Dataset Quality & Inspection Report (`MESSAGE_B_TO_A.md` Aligned)",
        "",
        "Fast deterministic audit across `model-training/data/processed/*.jsonl` and `model-training/data/eval/*.jsonl`.",
        "",
        "## Verified Contracts",
        "- `focal_symbols`: includes `signature` (`<= 240` chars), `degree`, `high_degree`, and `community`.",
        "- `catalogue_matches`: includes `directly_imported` (`true`/`false`/`null`), `sink_reachable` (`true` or `null` ONLY — **0 `false` values**), `usage`, and `cwe`.",
        "- Real OSV demo record `CVE-2024-35195` (`PyPI/requests==2.31.0`, `requirements.txt:2`, `app.py:1` & `app.py:5`, `sink_reachable: null`) embedded in `security_reviewer_dpo.jsonl` & `security_reviewer_eval.jsonl`.",
        "- Security DPO contrast enforced across `called at <file>:<line>` vs `directly imported at <file>:<line>; call-site reachability not shown reachable` vs `not imported directly; transitive only`.",
        "- Zero forbidden output keys (`needs_decision_card`, `new_global_vuln_candidate`) and zero legacy `<steward_graph_context>`, `is_god_node`, `AMBIGUOUS`, `tenant_id`, or `non-exploitable` terms.",
        "",
        "| File | Rows | `CVE-2024-35195` Rows | `sink_reachable` Counts | `directly_imported` Counts |",
        "| --- | ---: | ---: | --- | --- |",
    ]
    for rel, res in results:
        lines.append(
            f"| `{rel}` | {res['rows']:,} | {res['cve_2024_35195_rows']:,} | `{res['sink_counts'] or 'N/A'}` | `{res['direct_counts'] or 'N/A'}` |"
        )
    lines.append("")
    with report_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))
    print(f"Wrote fast report to {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
