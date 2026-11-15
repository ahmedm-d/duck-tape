#!/usr/bin/env python3
"""Fast parallel pre-flight data audit for Stage 4 model training datasets."""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = ROOT / "data" / "processed"
EVAL_DIR = ROOT / "data" / "eval"
REPORT_PATH = ROOT / "data" / "inspection_report.md"

GRAPH_JSON_RE = re.compile(r"<graph_context>\s*(\{.*?\})\s*</graph_context>", re.DOTALL)
FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL | re.IGNORECASE)
FORBIDDEN_JSON_KEYS = {"needs_decision_card", "new_global_vuln_candidate"}

DATASETS: list[dict[str, Any]] = [
    {"name": "router", "group": 1, "train": "router.jsonl", "evals": ["router_eval.jsonl"]},
    {"name": "ticket_parser", "group": 1, "train": "ticket_parser.jsonl", "evals": ["ticket_parser_eval.jsonl"]},
    {"name": "implementer", "group": 2, "train": "implementer.jsonl", "evals": ["implementer_eval.jsonl"]},
    {"name": "test_writer", "group": 2, "train": "test_writer.jsonl", "evals": ["test_writer_eval.jsonl"]},
    {
        "name": "security_reviewer_dpo",
        "group": 3,
        "train": "security_reviewer_dpo.jsonl",
        "evals": ["security_reviewer_eval.jsonl"],
    },
    {
        "name": "reviewer_dpo",
        "group": 3,
        "train": "reviewer_dpo.jsonl",
        "evals": ["reviewer_dpo_eval.jsonl", "reviewer_eval.jsonl"],
    },
    {"name": "log_classifier", "group": 4, "train": "log_classifier.jsonl", "evals": ["log_classifier_eval.jsonl"]},
    {"name": "commit_pr_writer", "group": 4, "train": "commit_pr_writer.jsonl", "evals": ["commit_pr_writer_eval.jsonl"]},
    {"name": "db_migration", "group": 4, "train": "db_migration.jsonl", "evals": ["db_migration_eval.jsonl"]},
    {"name": "iac", "group": 4, "train": "iac.jsonl", "evals": ["iac_eval.jsonl"]},
    {"name": "frontend_ui", "group": 4, "train": "frontend_ui.jsonl", "evals": ["frontend_ui_eval.jsonl"]},
    {
        "name": "retriever_triplets",
        "group": 4,
        "train": "retriever_triplets.jsonl",
        "evals": ["retriever_triplets_eval.jsonl", "retriever_eval.jsonl"],
    },
]


def _replace_steward_tags(obj: Any) -> tuple[Any, bool]:
    changed = False
    if isinstance(obj, str):
        if "<steward_graph_context>" in obj or "</steward_graph_context>" in obj:
            new_s = obj.replace("<steward_graph_context>", "<graph_context>").replace(
                "</steward_graph_context>", "</graph_context>"
            )
            return new_s, new_s != obj
        return obj, False
    if isinstance(obj, list):
        out = []
        for item in obj:
            fixed, item_changed = _replace_steward_tags(item)
            changed = changed or item_changed
            out.append(fixed)
        return out, changed
    if isinstance(obj, dict):
        out_d = {}
        for k, v in obj.items():
            fixed, v_changed = _replace_steward_tags(v)
            changed = changed or v_changed
            out_d[k] = fixed
        return out_d, changed
    return obj, False


def _strip_forbidden_keys(obj: Any) -> tuple[Any, bool]:
    changed = False
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in FORBIDDEN_JSON_KEYS:
                changed = True
                continue
            fixed_v, v_changed = _strip_forbidden_keys(v)
            changed = changed or v_changed
            out[k] = fixed_v
        return out, changed
    if isinstance(obj, list):
        out_l = []
        for item in obj:
            fixed_i, i_changed = _strip_forbidden_keys(item)
            changed = changed or i_changed
            out_l.append(fixed_i)
        return out_l, changed
    return obj, False


def _has_forbidden_keys(obj: Any) -> bool:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in FORBIDDEN_JSON_KEYS or _has_forbidden_keys(v):
                return True
    elif isinstance(obj, list):
        return any(_has_forbidden_keys(item) for item in obj)
    return False


def _extract_graph_blocks(text: str) -> list[tuple[int, int, str]]:
    """Return (start, end, json_str) spans for each <graph_context>{...}</graph_context> block."""
    spans: list[tuple[int, int, str]] = []
    for match in GRAPH_JSON_RE.finditer(text):
        spans.append((match.start(1), match.end(1), match.group(1)))
    return spans


def _audit_and_fix_graph_text(
    text: str,
    check_group2: bool = False,
    check_sec_catalogue: bool = False,
    fix: bool = True,
) -> tuple[str, bool, dict[str, int]]:
    stats = {
        "blocks": 0,
        "parse_err": 0,
        "missing_focal_sig": 0,
        "bad_edge_conf": 0,
        "missing_blast_tests": 0,
        "bad_directly_imported": 0,
        "bad_sink_reachable": 0,
    }
    spans = _extract_graph_blocks(text)
    if not spans:
        return text, False, stats

    out_parts: list[str] = []
    last_idx = 0
    text_changed = False

    for start, end, raw_json in spans:
        stats["blocks"] += 1
        out_parts.append(text[last_idx:start])
        last_idx = end

        try:
            g = json.loads(raw_json)
        except json.JSONDecodeError:
            stats["parse_err"] += 1
            out_parts.append(raw_json)
            continue

        block_changed = False
        if not isinstance(g, dict):
            stats["parse_err"] += 1
            out_parts.append(raw_json)
            continue

        if check_group2:
            focal = g.get("focal_symbols")
            if not isinstance(focal, list) or any(
                not isinstance(sym, dict) or not sym.get("signature") for sym in focal
            ):
                stats["missing_focal_sig"] += 1

            edges = g.get("edges")
            if not isinstance(edges, list):
                stats["bad_edge_conf"] += 1
            else:
                for edge in edges:
                    conf = edge.get("confidence") if isinstance(edge, dict) else None
                    if conf not in ("EXTRACTED", "INFERRED"):
                        if fix and conf == "AMBIGUOUS":
                            edge["confidence"] = "INFERRED"
                            block_changed = True
                        else:
                            stats["bad_edge_conf"] += 1

            blast = g.get("blast_radius")
            if not isinstance(blast, dict) or not isinstance(blast.get("linked_test_files"), list):
                stats["missing_blast_tests"] += 1

        if check_sec_catalogue:
            matches = g.get("catalogue_matches")
            if isinstance(matches, list):
                for item in matches:
                    if not isinstance(item, dict):
                        continue
                    if item.get("directly_imported") not in (True, False, None):
                        stats["bad_directly_imported"] += 1
                    if item.get("sink_reachable") not in (True, None):
                        if fix and item.get("sink_reachable") is False:
                            item["sink_reachable"] = None
                            block_changed = True
                        else:
                            stats["bad_sink_reachable"] += 1
                    usage_list = item.get("usage")
                    if isinstance(usage_list, list):
                        for u in usage_list:
                            if not isinstance(u, dict):
                                continue
                            if u.get("directly_imported") not in (True, False, None):
                                stats["bad_directly_imported"] += 1
                            if u.get("sink_reachable") not in (True, None):
                                if fix and u.get("sink_reachable") is False:
                                    u["sink_reachable"] = None
                                    block_changed = True
                                else:
                                    stats["bad_sink_reachable"] += 1

        if block_changed:
            text_changed = True
            out_parts.append(json.dumps(g, ensure_ascii=False))
        else:
            out_parts.append(raw_json)

    out_parts.append(text[last_idx:])
    return ("".join(out_parts) if text_changed else text), text_changed, stats


def _trim_text_keep_edges(text: str, target_len: int) -> str:
    marker = "\n...[truncated]...\n"
    if len(text) <= target_len or target_len < 200:
        return text
    if "<graph_context>" in text and "</graph_context>" in text:
        g_start = text.find("<graph_context>")
        g_end = text.rfind("</graph_context>") + len("</graph_context>")
        graph_part = text[g_start:g_end]
        outer_budget = target_len - len(graph_part)
        if outer_budget >= 200:
            prefix = text[:g_start]
            suffix = text[g_end:]
            pref_budget = min(len(prefix), outer_budget // 2)
            suff_budget = min(len(suffix), outer_budget - pref_budget)
            if len(prefix) > pref_budget > 80:
                h = max(30, (pref_budget - len(marker)) // 2)
                prefix = prefix[:h] + marker + prefix[-h:]
            if len(suffix) > suff_budget > 80:
                h = max(30, (suff_budget - len(marker)) // 2)
                suffix = suffix[:h] + marker + suffix[-h:]
            return prefix + graph_part + suffix
        return text
    keep = max(80, (target_len - len(marker)) // 2)
    return text[:keep] + marker + text[-keep:]


def _trim_oversized_implementer_row(row: dict[str, Any], target_chars: int = 175000) -> bool:
    """Trim longest tool/non-graph message contents so len(json.dumps(row)) // 4 < 45000."""
    chat = row.get("chat")
    if not isinstance(chat, list) or not chat:
        return False
    cur_len = len(json.dumps(row, ensure_ascii=False))
    if cur_len // 4 < 45000:
        return False

    modified = False
    for _ in range(12):
        cur_len = len(json.dumps(row, ensure_ascii=False))
        if cur_len // 4 < 45000:
            break
        excess = max(1000, cur_len - target_chars)

        # Priority: tool messages without graph_context, then other messages without graph_context,
        # then non-graph parts of messages containing graph_context.
        candidates: list[tuple[int, int, int, int]] = []
        for idx, msg in enumerate(chat):
            if not isinstance(msg, dict) or not isinstance(msg.get("content"), str):
                continue
            content = msg["content"]
            has_graph = "<graph_context>" in content
            if has_graph:
                g_start = content.find("<graph_context>")
                g_end = content.rfind("</graph_context>") + len("</graph_context>")
                trimmable_len = len(content) - (g_end - g_start)
                tier = 2
            else:
                trimmable_len = len(content)
                tier = 0 if msg.get("role") == "tool" else 1
            if trimmable_len > 500:
                candidates.append((tier, -trimmable_len, idx, trimmable_len))

        candidates.sort()
        trimmed_any = False
        for _, _, idx, trimmable_len in candidates:
            if excess <= 0:
                break
            msg = chat[idx]
            content = msg["content"]
            cut = min(trimmable_len - 300, excess + 1000)
            if cut <= 200:
                continue
            new_content = _trim_text_keep_edges(content, len(content) - cut)
            if len(new_content) < len(content):
                msg["content"] = new_content
                modified = True
                trimmed_any = True
                excess -= len(content) - len(new_content)

        if not trimmed_any:
            # Fallback: trim oversized string values inside tool_calls[].function.arguments
            for msg in chat:
                if excess <= 0:
                    break
                for tc in msg.get("tool_calls") or []:
                    fn = tc.get("function") if isinstance(tc, dict) else None
                    args = fn.get("arguments") if isinstance(fn, dict) else None
                    if isinstance(args, dict):
                        for ak, av in list(args.items()):
                            if isinstance(av, str) and len(av) > 1000 and excess > 0:
                                cut = min(len(av) - 400, excess + 500)
                                new_av = _trim_text_keep_edges(av, len(av) - cut)
                                if len(new_av) < len(av):
                                    args[ak] = new_av
                                    modified = True
                                    trimmed_any = True
                                    excess -= len(av) - len(new_av)
            if not trimmed_any:
                break

    return modified


def _audit_single_file(path: Path, dataset_name: str, group: int, fix: bool = True) -> dict[str, Any]:
    rows_raw: list[str] = []
    valid_rows = 0
    total_rows = 0
    file_changed = False
    max_est_tokens = 0
    sample_row: str | None = None

    graph_stats = {
        "blocks": 0,
        "parse_err": 0,
        "missing_focal_sig": 0,
        "bad_edge_conf": 0,
        "missing_blast_tests": 0,
        "bad_directly_imported": 0,
        "bad_sink_reachable": 0,
        "sec_sampled_blocks": 0,
    }
    issues: list[str] = []
    fixes_applied: list[str] = []

    def _accumulate(target: dict[str, int], src: dict[str, int]) -> None:
        for k, v in src.items():
            target[k] = target.get(k, 0) + v

    with path.open("r", encoding="utf-8") as fp:
        for idx, line in enumerate(fp):
            line_stripped = line.rstrip("\r\n")
            if not line_stripped:
                continue
            total_rows += 1
            try:
                row = json.loads(line_stripped)
            except json.JSONDecodeError:
                issues.append(f"row {idx}: invalid JSON line")
                rows_raw.append(line_stripped)
                continue

            if not isinstance(row, dict):
                issues.append(f"row {idx}: non-dict row")
                rows_raw.append(line_stripped)
                continue

            row_changed = False
            row, tag_changed = _replace_steward_tags(row)
            if tag_changed:
                row_changed = True
                fixes_applied.append("replaced <steward_graph_context> tag")

            row_ok = True

            # Group 1: router, ticket_parser
            if group == 1:
                if not isinstance(row.get("prompt"), str) or not row["prompt"].strip():
                    row_ok = False
                    issues.append(f"row {idx}: missing/empty prompt")
                comp = row.get("completion")
                if not isinstance(comp, str) or not comp.strip():
                    row_ok = False
                    issues.append(f"row {idx}: missing/empty completion")
                else:
                    comp_work = comp
                    if "```" in comp_work:
                        m = FENCE_RE.match(comp_work)
                        if m and fix:
                            comp_work = m.group(1).strip()
                            row["completion"] = comp_work
                            row_changed = True
                            fixes_applied.append("stripped markdown fence")
                        elif m:
                            row_ok = False
                            issues.append(f"row {idx}: markdown fence in completion")
                    if "```json" in comp_work.lower() or "```" in comp_work:
                        row_ok = False
                        issues.append(f"row {idx}: residual code fence in completion")
                    try:
                        parsed_comp = json.loads(comp_work)
                        if _has_forbidden_keys(parsed_comp):
                            if fix:
                                parsed_comp, _ = _strip_forbidden_keys(parsed_comp)
                                row["completion"] = json.dumps(parsed_comp, ensure_ascii=False)
                                row_changed = True
                                fixes_applied.append("removed forbidden keys")
                            else:
                                row_ok = False
                                issues.append(f"row {idx}: forbidden key in completion")
                    except json.JSONDecodeError:
                        row_ok = False
                        issues.append(f"row {idx}: invalid JSON completion")

                if isinstance(row.get("prompt"), str):
                    new_p, g_changed, g_stats = _audit_and_fix_graph_text(row["prompt"], fix=fix)
                    _accumulate(graph_stats, g_stats)
                    if g_changed:
                        row["prompt"] = new_p
                        row_changed = True
                        fixes_applied.append("fixed graph_context")

            # Group 2: implementer, test_writer
            elif group == 2:
                if dataset_name == "implementer":
                    chat = row.get("chat")
                    if not isinstance(chat, list) or not chat:
                        row_ok = False
                        issues.append(f"row {idx}: chat not non-empty list")
                    else:
                        for m_idx, msg in enumerate(chat):
                            if not isinstance(msg, dict) or "role" not in msg or "content" not in msg:
                                row_ok = False
                                issues.append(f"row {idx}: chat[{m_idx}] invalid dict")
                                continue
                            tool_calls = msg.get("tool_calls")
                            if tool_calls is not None:
                                if not isinstance(tool_calls, list):
                                    row_ok = False
                                    issues.append(f"row {idx}: tool_calls not list")
                                else:
                                    for tc in tool_calls:
                                        fn = tc.get("function") if isinstance(tc, dict) else None
                                        args = fn.get("arguments") if isinstance(fn, dict) else None
                                        if isinstance(args, str):
                                            try:
                                                json.loads(args)
                                            except json.JSONDecodeError:
                                                row_ok = False
                                                issues.append(f"row {idx}: invalid tool_call arguments JSON")
                            content = msg.get("content")
                            if isinstance(content, str) and "<graph_context>" in content:
                                new_c, g_changed, g_stats = _audit_and_fix_graph_text(
                                    content, check_group2=True, fix=fix
                                )
                                _accumulate(graph_stats, g_stats)
                                if g_changed:
                                    msg["content"] = new_c
                                    row_changed = True
                                    fixes_applied.append("fixed graph_context edge confidence")
                    est_tok = len(json.dumps(row, ensure_ascii=False)) // 4
                    if est_tok >= 45000 and fix:
                        if _trim_oversized_implementer_row(row):
                            row_changed = True
                            fixes_applied.append("truncated oversized chat tool outputs (<45k est tokens)")
                            est_tok = len(json.dumps(row, ensure_ascii=False)) // 4
                    if est_tok > max_est_tokens:
                        max_est_tokens = est_tok
                    if est_tok >= 45000:
                        row_ok = False
                        issues.append(f"row {idx}: est_tokens {est_tok} >= 45000")
                else:  # test_writer
                    if not isinstance(row.get("prompt"), str) or not row["prompt"].strip():
                        row_ok = False
                        issues.append(f"row {idx}: missing/empty prompt")
                    if not isinstance(row.get("completion"), str) or not row["completion"].strip():
                        row_ok = False
                        issues.append(f"row {idx}: missing/empty completion")
                    if isinstance(row.get("prompt"), str) and "<graph_context>" in row["prompt"]:
                        new_p, g_changed, g_stats = _audit_and_fix_graph_text(
                            row["prompt"], check_group2=True, fix=fix
                        )
                        _accumulate(graph_stats, g_stats)
                        if g_changed:
                            row["prompt"] = new_p
                            row_changed = True
                            fixes_applied.append("fixed graph_context edge confidence")

            # Group 3: security_reviewer_dpo, reviewer_dpo
            elif group == 3:
                p = row.get("prompt")
                ch = row.get("chosen")
                rj = row.get("rejected")
                if not isinstance(p, str) or not p.strip():
                    row_ok = False
                    issues.append(f"row {idx}: missing/empty prompt")
                if not isinstance(ch, str) or len(ch) <= 40:
                    row_ok = False
                    issues.append(f"row {idx}: chosen len <= 40")
                if not isinstance(rj, str) or len(rj) <= 40:
                    row_ok = False
                    issues.append(f"row {idx}: rejected len <= 40")
                if isinstance(ch, str) and isinstance(rj, str) and ch == rj:
                    row_ok = False
                    issues.append(f"row {idx}: chosen == rejected")

                if dataset_name == "security_reviewer_dpo":
                    if isinstance(ch, str) and "non-exploitable" in ch.lower():
                        if fix:
                            new_ch = re.sub(
                                r"non-exploitable",
                                lambda m: "Unconfirmed-reachability" if m.group(0)[0].isupper() else "unconfirmed-reachability",
                                ch,
                                flags=re.IGNORECASE,
                            )
                            row["chosen"] = new_ch
                            row_changed = True
                            fixes_applied.append("replaced 'non-exploitable' in chosen")
                        else:
                            row_ok = False
                            issues.append(f"row {idx}: forbidden string 'non-exploitable' in chosen")

                    if isinstance(p, str) and "<graph_context>" in p:
                        new_p, g_changed, g_stats = _audit_and_fix_graph_text(
                            p, check_sec_catalogue=True, fix=fix
                        )
                        _accumulate(graph_stats, g_stats)
                        if g_stats["blocks"] > 0 and graph_stats["sec_sampled_blocks"] < 100:
                            graph_stats["sec_sampled_blocks"] += g_stats["blocks"]
                        if g_changed:
                            row["prompt"] = new_p
                            row_changed = True
                            fixes_applied.append("replaced sink_reachable=false with null")
                else:
                    if isinstance(p, str) and "<graph_context>" in p:
                        new_p, g_changed, g_stats = _audit_and_fix_graph_text(p, fix=fix)
                        _accumulate(graph_stats, g_stats)
                        if g_changed:
                            row["prompt"] = new_p
                            row_changed = True
                            fixes_applied.append("fixed graph_context")

            # Group 4: specialist agents
            elif group == 4:
                if dataset_name == "frontend_ui":
                    if not isinstance(row.get("prompt"), str) or not row["prompt"].strip():
                        row_ok = False
                        issues.append(f"row {idx}: missing/empty prompt")
                    if "image_context" not in row or not row["image_context"]:
                        row_ok = False
                        issues.append(f"row {idx}: missing/empty image_context")
                    if not isinstance(row.get("completion"), str) or not row["completion"].strip():
                        row_ok = False
                        issues.append(f"row {idx}: missing/empty completion")
                elif dataset_name == "retriever_triplets":
                    for k in ("query", "pos", "neg"):
                        val = row.get(k)
                        if not val or (isinstance(val, str) and not val.strip()):
                            row_ok = False
                            issues.append(f"row {idx}: missing/empty {k}")
                else:
                    if not isinstance(row.get("prompt"), str) or not row["prompt"].strip():
                        row_ok = False
                        issues.append(f"row {idx}: missing/empty prompt")
                    comp = row.get("completion")
                    if not isinstance(comp, str) or not comp.strip():
                        row_ok = False
                        issues.append(f"row {idx}: missing/empty completion")
                    elif dataset_name == "log_classifier":
                        comp_work = comp
                        if "```" in comp_work:
                            m = FENCE_RE.match(comp_work)
                            if m and fix:
                                comp_work = m.group(1).strip()
                                row["completion"] = comp_work
                                row_changed = True
                                fixes_applied.append("stripped markdown fence")
                        try:
                            json.loads(comp_work)
                        except json.JSONDecodeError:
                            row_ok = False
                            issues.append(f"row {idx}: invalid JSON completion")

                    if isinstance(row.get("prompt"), str) and "<graph_context>" in row["prompt"]:
                        new_p, g_changed, g_stats = _audit_and_fix_graph_text(row["prompt"], fix=fix)
                        _accumulate(graph_stats, g_stats)
                        if g_changed:
                            row["prompt"] = new_p
                            row_changed = True
                            fixes_applied.append("fixed graph_context")

            if (
                graph_stats["parse_err"] > 0
                or graph_stats["missing_focal_sig"] > 0
                or graph_stats["bad_edge_conf"] > 0
                or graph_stats["missing_blast_tests"] > 0
                or graph_stats["bad_directly_imported"] > 0
                or graph_stats["bad_sink_reachable"] > 0
            ):
                # Track cumulative graph issues after fix attempt
                pass

            if row_ok:
                valid_rows += 1

            if row_changed:
                file_changed = True
                serialized = json.dumps(row, ensure_ascii=False)
                rows_raw.append(serialized)
            else:
                rows_raw.append(line_stripped)

            if idx == 0:
                sample_row = rows_raw[-1]

    if file_changed and fix:
        tmp_path = path.with_suffix(".jsonl.tmp")
        with tmp_path.open("w", encoding="utf-8", newline="\n") as out_fp:
            for r_line in rows_raw:
                out_fp.write(r_line + "\n")
        os.replace(tmp_path, path)
        # Re-verify cleanly after write
        recheck = _audit_single_file(path, dataset_name, group, fix=False)
        recheck["fixed"] = True
        recheck["fixes_applied"] = sorted(set(fixes_applied))
        return recheck

    return {
        "path": str(path),
        "total": total_rows,
        "valid": valid_rows,
        "max_est_tokens": max_est_tokens,
        "graph_stats": graph_stats,
        "issues": issues[:10],
        "issue_count": len(issues),
        "fixed": False,
        "fixes_applied": [],
        "sample_row": sample_row or "",
    }


def audit_dataset(spec: dict[str, Any]) -> dict[str, Any]:
    name = spec["name"]
    group = spec["group"]
    train_path = PROCESSED_DIR / spec["train"]
    train_res = _audit_single_file(train_path, name, group, fix=True)

    eval_results: list[dict[str, Any]] = []
    for eval_name in spec["evals"]:
        ep = EVAL_DIR / eval_name
        if ep.exists():
            eval_results.append(_audit_single_file(ep, name, group, fix=True))

    primary_eval = eval_results[0] if eval_results else {"total": 0, "valid": 0, "graph_stats": {}, "issues": [], "fixed": False}
    all_res = [train_res] + eval_results

    total_all = sum(r["total"] for r in all_res)
    valid_all = sum(r["valid"] for r in all_res)
    schema_pct = (100.0 * valid_all / total_all) if total_all else 0.0

    # Aggregate graph stats across train + eval
    g_agg = {
        "blocks": sum(r["graph_stats"].get("blocks", 0) for r in all_res),
        "parse_err": sum(r["graph_stats"].get("parse_err", 0) for r in all_res),
        "missing_focal_sig": sum(r["graph_stats"].get("missing_focal_sig", 0) for r in all_res),
        "bad_edge_conf": sum(r["graph_stats"].get("bad_edge_conf", 0) for r in all_res),
        "missing_blast_tests": sum(r["graph_stats"].get("missing_blast_tests", 0) for r in all_res),
        "bad_directly_imported": sum(r["graph_stats"].get("bad_directly_imported", 0) for r in all_res),
        "bad_sink_reachable": sum(r["graph_stats"].get("bad_sink_reachable", 0) for r in all_res),
    }

    graph_errors = (
        g_agg["parse_err"]
        + g_agg["missing_focal_sig"]
        + g_agg["bad_edge_conf"]
        + g_agg["missing_blast_tests"]
        + g_agg["bad_directly_imported"]
        + g_agg["bad_sink_reachable"]
    )

    if g_agg["blocks"] == 0:
        graph_check = "N/A (no graph blocks)"
    elif graph_errors == 0:
        if group == 2:
            graph_check = f"PASS ({g_agg['blocks']} blocks: focal_sig/edges/blast OK)"
        elif name == "security_reviewer_dpo":
            graph_check = f"PASS ({g_agg['blocks']} blocks: sink_reachable!=false OK)"
        else:
            graph_check = f"PASS ({g_agg['blocks']} blocks valid JSON)"
    else:
        graph_check = (
            f"FAIL (err={g_agg['parse_err']}, sig={g_agg['missing_focal_sig']}, "
            f"edge={g_agg['bad_edge_conf']}, blast={g_agg['missing_blast_tests']}, "
            f"imp={g_agg['bad_directly_imported']}, sink={g_agg['bad_sink_reachable']})"
        )

    count_ok = train_res["total"] >= 1000 and primary_eval["total"] >= 100
    all_issues = [iss for r in all_res for iss in r["issues"]]
    any_fixed = any(r["fixed"] for r in all_res)
    fixes_all = sorted({fx for r in all_res for fx in r.get("fixes_applied", [])})

    if not count_ok or schema_pct < 100.0 or graph_errors > 0 or all_issues:
        status = f"FAIL ({len(all_issues)} issues)"
    elif any_fixed:
        status = f"FIXED ({', '.join(fixes_all)})"
    else:
        status = "PASS"

    aug_notes: list[str] = []
    if train_res["total"] < 1000:
        aug_notes.append(f"Train < 1000 ({train_res['total']})")
    if primary_eval["total"] < 100:
        aug_notes.append(f"Eval < 100 ({primary_eval['total']})")
    if schema_pct < 100.0:
        aug_notes.append(f"Fix {total_all - valid_all} invalid rows")
    if graph_errors > 0:
        aug_notes.append("Fix graph contract violations")
    aug_str = "; ".join(aug_notes) if aug_notes else "None"

    eval_str = str(primary_eval["total"])
    if len(eval_results) > 1:
        extra = ", ".join(f"{Path(r['path']).name}:{r['total']}" for r in eval_results[1:])
        eval_str = f"{primary_eval['total']} ({extra})"

    return {
        "name": name,
        "train_rows": train_res["total"],
        "eval_rows": eval_str,
        "schema_pct": f"{schema_pct:.2f}%",
        "graph_check": graph_check,
        "aug_needed": aug_str,
        "status": status,
        "max_est_tokens": train_res["max_est_tokens"],
        "sample_row": train_res["sample_row"],
        "issues": all_issues[:5],
    }


def main() -> None:
    t0 = time.perf_counter()
    with concurrent.futures.ProcessPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(audit_dataset, DATASETS))
    elapsed = time.perf_counter() - t0

    lines: list[str] = [
        "# Stage 4 Pre-Flight Data Audit Report",
        "",
        f"Audited 12 datasets (`processed` + `eval`) in **{elapsed:.2f}s** using `ProcessPoolExecutor(max_workers=8)`.",
        "",
        "| Dataset | Train Rows | Eval Rows | Schema Valid % | Graph Contract Check | Augmentation Needed for Stage 4 | Status (PASS/FIXED) |",
        "| --- | ---: | --- | ---: | --- | --- | --- |",
    ]
    for res in results:
        lines.append(
            f"| `{res['name']}` | {res['train_rows']} | {res['eval_rows']} | {res['schema_pct']} | "
            f"{res['graph_check']} | {res['aug_needed']} | {res['status']} |"
        )

    lines.extend(["", "## Sample Rows (< 300 chars)", ""])
    sample_targets = {"ticket_parser", "implementer", "security_reviewer_dpo"}
    for res in results:
        if res["name"] in sample_targets:
            snippet = res["sample_row"][:280].replace("\n", " ").replace("`", "'")
            lines.append(f"- **`{res['name']}`**: `{snippet}...`")

    any_issues = [r for r in results if r["issues"]]
    if any_issues:
        lines.extend(["", "## Sample Issues", ""])
        for r in any_issues:
            lines.append(f"- **`{r['name']}`**: {r['issues']}")

    report_text = "\n".join(lines) + "\n"
    REPORT_PATH.write_text(report_text, encoding="utf-8", newline="\n")
    print(report_text)


if __name__ == "__main__":
    main()
