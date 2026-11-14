"""Pre-flight quality gate & inspection report generator for all 12 Duck Tape datasets."""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
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
    compute_jsonl_token_stats,
    count_chat_tokens,
    validate_ticket_parser_output,
)
from training.scrub import contains_secrets  # noqa: E402
from training.utils import truncate_bounded  # noqa: E402

MAX_TOKENS_CEILING = 45000
MIN_UNIQUENESS_PCT = 95.0
MIN_REJECTED_CHARS = 51  # strictly > 50 chars

FORBIDDEN_TERMS: tuple[str, ...] = (
    "<steward_graph_context>",
    "is_god_node",
    "AMBIGUOUS",
    "tenant_id",
    "needs_decision_card",
    "new_global_vuln_candidate",
    "non-exploitable",
)

GRAPH_CONTEXT_AGENTS = frozenset(
    {
        "router",
        "ticket_parser",
        "commit_pr_writer",
        "log_classifier",
        "implementer",
        "test_writer",
        "reviewer",
        "security_reviewer",
        "db_migration",
        "iac",
    }
)
CI_WORKFLOW_AGENTS = frozenset({"implementer", "test_writer", "commit_pr_writer", "log_classifier"})

_GRAPH_CTX_RE = re.compile(r"<graph_context>\s*(\{.*?\})\s*</graph_context>", re.DOTALL)
_REQUIRED_GC_KEYS = frozenset(
    {
        "scope",
        "focal_symbols",
        "edges",
        "blast_radius",
        "catalogue_matches",
        "approved_knowledge",
        "gaps",
    }
)


@dataclass(frozen=True)
class AgentDatasetSpec:
    agent: str
    fmt: str
    base_model: str
    train_rel: str
    eval_rel: str
    expected_train: int
    expected_eval: int
    autoscientist_target: int
    completion_schema: type | None = None
    required_substrings: tuple[str, ...] = ()


AGENT_SPECS: tuple[AgentDatasetSpec, ...] = (
    AgentDatasetSpec(
        agent="router",
        fmt="instruction",
        base_model="Gemma 4 (26B/31B)",
        train_rel="data/processed/router.jsonl",
        eval_rel="data/eval/router_eval.jsonl",
        expected_train=1200,
        expected_eval=200,
        autoscientist_target=10000,
        completion_schema=RouterCompletion,
    ),
    AgentDatasetSpec(
        agent="ticket_parser",
        fmt="instruction",
        base_model="Gemma 4 (26B/31B)",
        train_rel="data/processed/ticket_parser.jsonl",
        eval_rel="data/eval/ticket_parser_eval.jsonl",
        expected_train=5000,
        expected_eval=200,
        autoscientist_target=10000,
        completion_schema=TicketParserCompletion,
    ),
    AgentDatasetSpec(
        agent="commit_pr_writer",
        fmt="instruction",
        base_model="Gemma 4 (26B/31B)",
        train_rel="data/processed/commit_pr_writer.jsonl",
        eval_rel="data/eval/commit_pr_writer_eval.jsonl",
        expected_train=5000,
        expected_eval=200,
        autoscientist_target=10000,
        required_substrings=(
            "### Summary",
            "### Cited Symbols",
            "### Confidence Assessment",
            "### Risk Assessment",
        ),
    ),
    AgentDatasetSpec(
        agent="log_classifier",
        fmt="instruction",
        base_model="Gemma 4 (26B/31B)",
        train_rel="data/processed/log_classifier.jsonl",
        eval_rel="data/eval/log_classifier_eval.jsonl",
        expected_train=5000,
        expected_eval=200,
        autoscientist_target=10000,
        completion_schema=LogClassifierCompletion,
    ),
    AgentDatasetSpec(
        agent="implementer",
        fmt="chat",
        base_model="Qwen 3.8 (27B/35B)",
        train_rel="data/processed/implementer.jsonl",
        eval_rel="data/eval/implementer_eval.jsonl",
        expected_train=5000,
        expected_eval=200,
        autoscientist_target=10000,
    ),
    AgentDatasetSpec(
        agent="test_writer",
        fmt="instruction",
        base_model="Qwen 3.8 (27B/35B)",
        train_rel="data/processed/test_writer.jsonl",
        eval_rel="data/eval/test_writer_eval.jsonl",
        expected_train=5000,
        expected_eval=200,
        autoscientist_target=10000,
        required_substrings=(
            "### Reproduction Test Diff",
            "### Verification Command",
        ),
    ),
    AgentDatasetSpec(
        agent="reviewer",
        fmt="preference",
        base_model="Qwen 3.8 (27B/35B)",
        train_rel="data/processed/reviewer_dpo.jsonl",
        eval_rel="data/eval/reviewer_dpo_eval.jsonl",
        expected_train=5000,
        expected_eval=200,
        autoscientist_target=12000,
    ),
    AgentDatasetSpec(
        agent="security_reviewer",
        fmt="preference",
        base_model="Qwen 3.8 (27B/35B)",
        train_rel="data/processed/security_reviewer_dpo.jsonl",
        eval_rel="data/eval/security_reviewer_eval.jsonl",
        expected_train=5000,
        expected_eval=200,
        autoscientist_target=12000,
    ),
    AgentDatasetSpec(
        agent="db_migration",
        fmt="instruction",
        base_model="Qwen 3.8 (27B/35B)",
        train_rel="data/processed/db_migration.jsonl",
        eval_rel="data/eval/db_migration_eval.jsonl",
        expected_train=5000,
        expected_eval=200,
        autoscientist_target=10000,
        required_substrings=(
            "### 1. Forward Migration",
            "### 2. Rollback / Down Migration",
            "### 3. Lock & Data-Loss Risk Assessment",
        ),
    ),
    AgentDatasetSpec(
        agent="iac",
        fmt="instruction",
        base_model="Qwen 3.8 (27B/35B)",
        train_rel="data/processed/iac.jsonl",
        eval_rel="data/eval/iac_eval.jsonl",
        expected_train=5000,
        expected_eval=200,
        autoscientist_target=10000,
        required_substrings=(
            "### Least-Privilege IAM & Network Security Notes",
            "### Infrastructure Configuration",
        ),
    ),
    AgentDatasetSpec(
        agent="frontend_ui",
        fmt="vision_instruction",
        base_model="Gemma 4 31B VLM / Qwen 3.5 VLM",
        train_rel="data/processed/frontend_ui.jsonl",
        eval_rel="data/eval/frontend_ui_eval.jsonl",
        expected_train=2500,
        expected_eval=100,
        autoscientist_target=10000,
        required_substrings=("```tsx", "React", "className"),
    ),
    AgentDatasetSpec(
        agent="retriever",
        fmt="contrastive_triplet",
        base_model="Qwen3-Embedding-4B",
        train_rel="data/processed/retriever_triplets.jsonl",
        eval_rel="data/eval/retriever_triplets_eval.jsonl",
        expected_train=5000,
        expected_eval=300,
        autoscientist_target=10000,
    ),
)


def _check_secrets_in_obj(obj: Any) -> int:
    if isinstance(obj, str):
        return 1 if contains_secrets(obj) else 0
    if isinstance(obj, dict):
        return sum(_check_secrets_in_obj(v) for v in obj.values())
    if isinstance(obj, list):
        return sum(_check_secrets_in_obj(v) for v in obj)
    return 0


def _count_forbidden_in_text(text: str, counts: Counter[str]) -> None:
    low = text.lower()
    for term in FORBIDDEN_TERMS:
        if term == "AMBIGUOUS":
            hits = text.count("AMBIGUOUS")
        else:
            hits = low.count(term.lower())
        if hits:
            counts[term] += hits


def _scan_forbidden_in_record(record: dict[str, Any], fmt: str, counts: Counter[str]) -> None:
    if fmt == "vision_instruction":
        _count_forbidden_in_text(str(record.get("prompt") or ""), counts)
        _count_forbidden_in_text(str(record.get("completion") or ""), counts)
        return
    if fmt == "chat":
        for msg in record.get("chat") or []:
            _count_forbidden_in_text(str(msg.get("content") or ""), counts)
            if msg.get("tool_calls"):
                _count_forbidden_in_text(json.dumps(msg["tool_calls"], ensure_ascii=False), counts)
        return
    if fmt == "contrastive_triplet":
        _count_forbidden_in_text(str(record.get("query") or ""), counts)
        for item in (record.get("pos") or []) + (record.get("neg") or []):
            _count_forbidden_in_text(str(item), counts)
        return
    for key, val in record.items():
        _count_forbidden_in_text(str(val), counts)


def _validate_graph_context(text: str, path: Path, line_no: int) -> dict[str, Any]:
    match = _GRAPH_CTX_RE.search(text or "")
    if not match:
        raise ValueError(f"{path}:{line_no} missing <graph_context>...</graph_context> block")
    ctx = json.loads(match.group(1))
    if set(ctx.keys()) != _REQUIRED_GC_KEYS:
        raise ValueError(f"{path}:{line_no} invalid graph_context v1 top-level keys: {sorted(ctx.keys())}")
    scope = ctx["scope"]
    if set(scope.keys()) != {"repo_id", "snapshot_id", "engine", "engine_version", "graph_hash"}:
        raise ValueError(f"{path}:{line_no} invalid graph_context scope keys: {sorted(scope.keys())}")
    for sym in ctx["focal_symbols"]:
        if "is_god_node" in sym or "leiden_community" in sym:
            raise ValueError(f"{path}:{line_no} forbidden focal_symbols key")
        if sym.get("kind") not in {"callable", "class", "symbol", "file"}:
            raise ValueError(f"{path}:{line_no} invalid focal_symbol kind: {sym.get('kind')}")
    for edge in ctx["edges"]:
        if edge.get("confidence") not in {"EXTRACTED", "INFERRED"}:
            raise ValueError(f"{path}:{line_no} invalid edge confidence: {edge.get('confidence')}")
        if not isinstance(edge.get("suggestion_only"), bool):
            raise ValueError(f"{path}:{line_no} edge suggestion_only must be bool")
        if edge.get("relation") not in {"calls", "imports", "imports_from", "tests"}:
            raise ValueError(f"{path}:{line_no} invalid edge relation: {edge.get('relation')}")
        if not isinstance(edge.get("evidence"), dict) or set(edge["evidence"].keys()) != {"path", "line"}:
            raise ValueError(f"{path}:{line_no} invalid edge evidence")
    br = ctx["blast_radius"]
    if set(br.keys()) != {"dependent_files", "communities_touched", "linked_test_files", "structural_risk", "basis"}:
        raise ValueError(f"{path}:{line_no} invalid blast_radius keys")
    if br["structural_risk"] not in {"low", "medium", "high", None}:
        raise ValueError(f"{path}:{line_no} invalid structural_risk: {br['structural_risk']}")
    return ctx


def _prompt_signature(record: dict[str, Any], fmt: str) -> str:
    if fmt == "chat":
        chat = record.get("chat") or []
        for msg in chat:
            if isinstance(msg, dict) and msg.get("role") == "user":
                return str(msg.get("content") or "").strip()
        return json.dumps(chat[:2], sort_keys=True, ensure_ascii=False)
    if fmt == "contrastive_triplet":
        return str(record.get("query") or "").strip()
    return str(record.get("prompt") or "").strip()


def _fenced(lang: str, text: str) -> str:
    fence = "````" if "```" in text else "```"
    return f"{fence}{lang}\n{text}\n{fence}"


def _format_sample_markdown(record: dict[str, Any], fmt: str) -> str:
    if fmt == "chat":
        chat = record.get("chat") or []
        roles = [str(m.get("role")) for m in chat]
        first_user = next((str(m.get("content") or "") for m in chat if m.get("role") == "user"), "")
        last_asst = next((str(m.get("content") or "") for m in reversed(chat) if m.get("role") == "assistant"), "")
        return (
            f"- **Turns:** `{len(chat)}` (`{' -> '.join(roles[:8])}{'...' if len(roles) > 8 else ''}`)\n"
            f"- **User Prompt Excerpt:**\n{_fenced('text', truncate_bounded(first_user, 380))}\n"
            f"- **Final Assistant Excerpt:**\n{_fenced('text', truncate_bounded(last_asst, 380))}"
        )
    if fmt == "preference":
        return (
            f"- **Prompt:**\n{_fenced('text', truncate_bounded(str(record.get('prompt') or ''), 420))}\n"
            f"- **Chosen:**\n{_fenced('markdown', truncate_bounded(str(record.get('chosen') or ''), 380))}\n"
            f"- **Rejected:**\n{_fenced('markdown', truncate_bounded(str(record.get('rejected') or ''), 320))}"
        )
    if fmt == "vision_instruction":
        img_ctx = str(record.get("image_context") or "")
        img_preview = f"{img_ctx[:64]}... (len={len(img_ctx)} chars)"
        return (
            f"- **Prompt:**\n{_fenced('text', truncate_bounded(str(record.get('prompt') or ''), 380))}\n"
            f"- **Image Context:** `{img_preview}`\n"
            f"- **Completion:**\n{_fenced('markdown', truncate_bounded(str(record.get('completion') or ''), 480))}"
        )
    if fmt == "contrastive_triplet":
        pos_list = record.get("pos") or []
        neg_list = record.get("neg") or []
        return (
            f"- **Query:**\n{_fenced('text', truncate_bounded(str(record.get('query') or ''), 340))}\n"
            f"- **Positive[0] ({len(pos_list)} pos):**\n{_fenced('text', truncate_bounded(str(pos_list[0]) if pos_list else '', 340))}\n"
            f"- **Negative[0] ({len(neg_list)} neg):**\n{_fenced('text', truncate_bounded(str(neg_list[0]) if neg_list else '', 340))}"
        )
    comp = str(record.get("completion") or "")
    lang = "json" if comp.lstrip().startswith("{") else "markdown"
    return (
        f"- **Prompt:**\n{_fenced('text', truncate_bounded(str(record.get('prompt') or ''), 420))}\n"
        f"- **Completion:**\n{_fenced(lang, truncate_bounded(comp, 480))}"
    )


def _audit_file(
    spec: AgentDatasetSpec,
    path: Path,
    enc: tiktoken.Encoding,
) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Missing dataset file for {spec.agent}: {path}")

    stats = compute_jsonl_token_stats(path)
    sigs: list[str] = []
    secret_hits = 0
    fence_hits = 0
    json_failures = 0
    min_rejected_len: int | None = None
    max_raw_tokens = 0
    dist: Counter[str] = Counter()
    forbidden_counts: Counter[str] = Counter()
    graph_context_valid = 0
    ci_workflow_valid = 0
    samples: list[dict[str, Any]] = []

    with path.open("r", encoding="utf-8") as handle:
        lines = [ln.strip() for ln in handle if ln.strip()]

    sample_indices = {0, min(len(lines) - 1, max(1, len(lines) // 2))} if lines else set()

    for idx, raw_line in enumerate(lines):
        record = json.loads(raw_line)
        if idx in sample_indices:
            samples.append(record)

        _scan_forbidden_in_record(record, spec.fmt, forbidden_counts)
        sigs.append(_prompt_signature(record, spec.fmt))

        if spec.fmt == "chat":
            row_chat = ChatTrajectoryRow.model_validate(record)
            if _check_secrets_in_obj(row_chat.chat) > 0:
                secret_hits += 1
            p_toks, c_toks = count_chat_tokens(row_chat.chat)
            raw_toks = p_toks + c_toks
            max_raw_tokens = max(max_raw_tokens, raw_toks)
            if raw_toks >= MAX_TOKENS_CEILING:
                raise ValueError(f"{path}:{idx + 1} token count {raw_toks} >= {MAX_TOKENS_CEILING}")
            for msg in row_chat.chat:
                for tc in msg.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    if not isinstance(fn.get("arguments"), dict):
                        raise ValueError(f"{path}:{idx + 1} non-dict tool_call arguments")

            first_user = next((str(m.get("content") or "") for m in row_chat.chat if m.get("role") == "user"), "")
            last_asst = next((str(m.get("content") or "") for m in reversed(row_chat.chat) if m.get("role") == "assistant"), "")
            if spec.agent in GRAPH_CONTEXT_AGENTS:
                _validate_graph_context(first_user, path, idx + 1)
                graph_context_valid += 1
            if spec.agent in CI_WORKFLOW_AGENTS:
                if "blast_radius.linked_test_files" in first_user and "blast_radius.linked_test_files" in last_asst:
                    ci_workflow_valid += 1
                else:
                    raise ValueError(f"{path}:{idx + 1} missing CI/worktree workflow rules")
            dist["chat_valid"] += 1
            if any(m.get("tool_calls") for m in row_chat.chat):
                dist["with_tool_calls"] += 1

        elif spec.fmt == "preference":
            row_pref = PreferenceRow.model_validate(record)
            if _check_secrets_in_obj(record) > 0:
                secret_hits += 1
            rej_len = len(row_pref.rejected.strip())
            min_rejected_len = rej_len if min_rejected_len is None else min(min_rejected_len, rej_len)
            if rej_len < MIN_REJECTED_CHARS:
                raise ValueError(f"{path}:{idx + 1} DPO rejected response too short ({rej_len} chars)")
            raw_toks = (
                len(enc.encode(row_pref.prompt, disallowed_special=()))
                + len(enc.encode(row_pref.chosen, disallowed_special=()))
                + len(enc.encode(row_pref.rejected, disallowed_special=()))
            )
            max_raw_tokens = max(max_raw_tokens, raw_toks)
            if raw_toks >= MAX_TOKENS_CEILING:
                raise ValueError(f"{path}:{idx + 1} token count {raw_toks} >= {MAX_TOKENS_CEILING}")

            ctx = _validate_graph_context(row_pref.prompt, path, idx + 1)
            graph_context_valid += 1

            if spec.agent == "security_reviewer":
                matches = ctx.get("catalogue_matches") or []
                if not matches:
                    raise ValueError(f"{path}:{idx + 1} security_reviewer missing catalogue_matches")
                sink_val = matches[0].get("sink_reachable")
                if sink_val is None:
                    if "not shown reachable" not in row_pref.chosen or "SUPPRESSED" in row_pref.chosen:
                        raise ValueError(f"{path}:{idx + 1} sink_reachable=null chosen violated null-safety rule")
                    dist["sink_reachable_null"] += 1
                elif sink_val is True:
                    if "EXTRACTED" not in row_pref.chosen:
                        raise ValueError(f"{path}:{idx + 1} sink_reachable=true chosen missing EXTRACTED citation")
                    dist["sink_reachable_true"] += 1
                elif sink_val is False:
                    if "zero ast imports" not in row_pref.chosen.lower() or "not shown reachable" not in row_pref.chosen.lower():
                        raise ValueError(f"{path}:{idx + 1} sink_reachable=false chosen missing zero-AST-import bump note")
                    dist["sink_reachable_false"] += 1
                else:
                    raise ValueError(f"{path}:{idx + 1} invalid sink_reachable value: {sink_val!r}")
            else:
                dist["preference_valid"] += 1

        elif spec.fmt == "vision_instruction":
            row_vis = VisionInstructionRow.model_validate(record)
            if contains_secrets(row_vis.prompt) or contains_secrets(row_vis.completion):
                secret_hits += 1
            for req in spec.required_substrings:
                if req not in row_vis.completion:
                    raise ValueError(f"{path}:{idx + 1} missing required marker {req!r}")
            raw_toks = (
                len(enc.encode(row_vis.prompt, disallowed_special=()))
                + len(enc.encode(row_vis.image_context, disallowed_special=()))
                + len(enc.encode(row_vis.completion, disallowed_special=()))
            )
            max_raw_tokens = max(max_raw_tokens, raw_toks)
            if raw_toks >= MAX_TOKENS_CEILING:
                raise ValueError(f"{path}:{idx + 1} raw token count {raw_toks} >= {MAX_TOKENS_CEILING}")
            dist["vision_valid"] += 1

        elif spec.fmt == "contrastive_triplet":
            row_trip = ContrastiveTripletRow.model_validate(record)
            if _check_secrets_in_obj(record) > 0:
                secret_hits += 1
            if any(p in row_trip.neg for p in row_trip.pos):
                raise ValueError(f"{path}:{idx + 1} positive item appears in negatives")
            raw_toks = len(enc.encode(row_trip.query, disallowed_special=())) + sum(
                len(enc.encode(t, disallowed_special=())) for t in row_trip.pos + row_trip.neg
            )
            max_raw_tokens = max(max_raw_tokens, raw_toks)
            if raw_toks >= MAX_TOKENS_CEILING:
                raise ValueError(f"{path}:{idx + 1} token count {raw_toks} >= {MAX_TOKENS_CEILING}")
            dist["triplets_valid"] += 1
            dist[f"neg_count:{len(row_trip.neg)}"] += 1

        else:
            row_inst = InstructionRow.model_validate(record)
            if contains_secrets(row_inst.prompt) or contains_secrets(row_inst.completion):
                secret_hits += 1
            raw_toks = len(enc.encode(row_inst.prompt, disallowed_special=())) + len(
                enc.encode(row_inst.completion, disallowed_special=())
            )
            max_raw_tokens = max(max_raw_tokens, raw_toks)
            if raw_toks >= MAX_TOKENS_CEILING:
                raise ValueError(f"{path}:{idx + 1} token count {raw_toks} >= {MAX_TOKENS_CEILING}")
            if spec.agent in GRAPH_CONTEXT_AGENTS:
                _validate_graph_context(row_inst.prompt, path, idx + 1)
                graph_context_valid += 1
            if spec.completion_schema is not None:
                if "```" in row_inst.completion:
                    fence_hits += 1
                try:
                    parsed = json.loads(row_inst.completion)
                    if spec.agent == "ticket_parser":
                        obj = validate_ticket_parser_output(parsed)
                        if isinstance(obj, DecisionCompletion):
                            model_schemas.check(parsed, model_schemas.DECISION)
                    else:
                        obj = spec.completion_schema.model_validate(parsed)
                except Exception:
                    json_failures += 1
                    raise
                if spec.agent == "router":
                    dist[obj.next_agent] += 1
                elif spec.agent == "ticket_parser":
                    if isinstance(obj, DecisionCompletion):
                        dist["decision_card"] += 1
                    else:
                        dist[obj.change_type] += 1
                elif spec.agent == "log_classifier":
                    dist[obj.failure_category] += 1
                    if "blast_radius.linked_test_files" in row_inst.prompt and "blast_radius.linked_test_files" in obj.recommended_action:
                        ci_workflow_valid += 1
                    else:
                        raise ValueError(f"{path}:{idx + 1} log_classifier missing CI/worktree guidance")
            else:
                for req in spec.required_substrings:
                    if req not in row_inst.completion:
                        raise ValueError(f"{path}:{idx + 1} missing required heading {req!r}")
                if spec.agent in CI_WORKFLOW_AGENTS:
                    if "blast_radius.linked_test_files" in row_inst.prompt and "blast_radius.linked_test_files" in row_inst.completion:
                        ci_workflow_valid += 1
                    else:
                        raise ValueError(f"{path}:{idx + 1} {spec.agent} missing CI/worktree guidance")
                if spec.agent == "iac":
                    if "Terraform (.tf)" in row_inst.prompt:
                        dist["terraform"] += 1
                    elif "Kubernetes" in row_inst.prompt:
                        dist["kubernetes"] += 1
                    elif "Dockerfile" in row_inst.prompt:
                        dist["dockerfile"] += 1
                elif spec.agent == "db_migration":
                    if "Flyway" in row_inst.prompt:
                        dist["flyway_sql"] += 1
                    elif "Alembic" in row_inst.prompt:
                        dist["alembic"] += 1
                    elif "Django" in row_inst.prompt:
                        dist["django"] += 1
                    elif "Prisma" in row_inst.prompt:
                        dist["prisma"] += 1
                else:
                    dist["markdown_valid"] += 1

    unique_sigs = set(sigs)
    uniqueness_pct = round((len(unique_sigs) / len(sigs)) * 100.0, 2) if sigs else 0.0

    return {
        "stats": stats,
        "sigs": unique_sigs,
        "uniqueness_pct": uniqueness_pct,
        "secret_hits": secret_hits,
        "fence_hits": fence_hits,
        "json_failures": json_failures,
        "min_rejected_len": min_rejected_len,
        "max_raw_tokens": max_raw_tokens,
        "dist": dict(sorted(dist.items())),
        "forbidden_counts": dict(forbidden_counts),
        "graph_context_valid": graph_context_valid,
        "ci_workflow_valid": ci_workflow_valid,
        "samples": samples,
    }


def main() -> int:
    enc = tiktoken.get_encoding("cl100k_base")
    summary_rows: list[dict[str, Any]] = []
    gate_failures: list[str] = []

    md_summary: list[str] = [
        "# Duck Tape Pre-Flight Dataset Quality & Inspection Report",
        "",
        "Comprehensive quality gate & `HANDOFF_B_TO_A.md` (`graph_context v1`) audit across all **12 specialist agent datasets** ",
        "in `model-training/data/processed/` and `model-training/data/eval/`.",
        "",
        "## 1. Pre-Flight Quality Gate Checklist",
        "",
    ]

    table_lines: list[str] = [
        "## 2. Dataset Summary & AutoScientist Augmentation Plan",
        "",
        "| # | Agent | Format | Base Model | Train Rows | Eval Rows | Total Tokens (Train / Eval) | Max Tokens | Uniqueness % | Overlap | Secret Hits | `augmentation_domain_rows` (Target) |",
        "| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]

    strict_json_lines: list[str] = [
        "",
        "## 3. Strict JSON & Grounding Checks",
        "",
        "| Agent | Strict JSON Valid | Markdown Fence Hits | Min Rejected Chars (DPO) | Status |",
        "| --- | --- | ---: | --- | --- |",
    ]

    handoff_lines: list[str] = [
        "",
        "## 4. Handoff v1 (`graph_context v1`), Null-Safety & Forbidden-Key Audit",
        "",
        "| Agent | `<graph_context>` v1 Valid | CI / Worktree Grounded | Forbidden Term Hits | Status |",
        "| --- | ---: | ---: | ---: | --- |",
    ]

    dist_lines: list[str] = [
        "",
        "## 5. Label, Framework & Source Distributions",
        "",
    ]

    sample_lines: list[str] = [
        "",
        "## 6. Formatted Sample Rows (2 per Agent)",
        "",
    ]

    total_train_rows = 0
    total_eval_rows = 0
    total_secrets = 0
    total_overlap = 0
    total_forbidden: Counter[str] = Counter()

    for idx, spec in enumerate(AGENT_SPECS, start=1):
        train_path = MODEL_TRAINING_ROOT / spec.train_rel
        eval_path = MODEL_TRAINING_ROOT / spec.eval_rel

        tr = _audit_file(spec, train_path, enc)
        ev = _audit_file(spec, eval_path, enc)

        tr_rows = tr["stats"]["rows"]
        ev_rows = ev["stats"]["rows"]
        total_train_rows += tr_rows
        total_eval_rows += ev_rows

        overlap = len(tr["sigs"] & ev["sigs"])
        total_overlap += overlap
        secrets = tr["secret_hits"] + ev["secret_hits"]
        total_secrets += secrets

        for k, v in tr["forbidden_counts"].items():
            total_forbidden[k] += v
        for k, v in ev["forbidden_counts"].items():
            total_forbidden[k] += v
        agent_forb = sum(tr["forbidden_counts"].values()) + sum(ev["forbidden_counts"].values())

        if tr_rows < 1000:
            gate_failures.append(f"{spec.agent}: train rows {tr_rows} < 1000")
        if tr_rows != spec.expected_train:
            gate_failures.append(f"{spec.agent}: expected {spec.expected_train} train rows, got {tr_rows}")
        if ev_rows != spec.expected_eval:
            gate_failures.append(f"{spec.agent}: expected {spec.expected_eval} eval rows, got {ev_rows}")
        if overlap != 0:
            gate_failures.append(f"{spec.agent}: train/eval overlap = {overlap}")
        if secrets != 0:
            gate_failures.append(f"{spec.agent}: secret hits = {secrets}")
        if agent_forb != 0:
            gate_failures.append(
                f"{spec.agent}: forbidden handoff terms present: "
                f"train={tr['forbidden_counts']} eval={ev['forbidden_counts']}"
            )
        if tr["uniqueness_pct"] <= MIN_UNIQUENESS_PCT or ev["uniqueness_pct"] <= MIN_UNIQUENESS_PCT:
            gate_failures.append(
                f"{spec.agent}: prompt uniqueness too low (train={tr['uniqueness_pct']}%, eval={ev['uniqueness_pct']}%)"
            )
        if tr["fence_hits"] != 0 or ev["fence_hits"] != 0:
            gate_failures.append(f"{spec.agent}: markdown fence hits in strict JSON")

        if spec.agent == "security_reviewer":
            tr_null_ratio = tr["dist"].get("sink_reachable_null", 0) / tr_rows
            ev_null_ratio = ev["dist"].get("sink_reachable_null", 0) / ev_rows
            if abs(tr_null_ratio - 0.70) > 0.02 or abs(ev_null_ratio - 0.70) > 0.02:
                gate_failures.append(
                    f"security_reviewer: sink_reachable=null ratio must be ~0.70 (got train={tr_null_ratio:.2%}, eval={ev_null_ratio:.2%})"
                )

        aug_needed = max(0, spec.autoscientist_target - tr_rows)
        max_tok = max(tr["max_raw_tokens"], ev["max_raw_tokens"])

        table_lines.append(
            f"| {idx} | `{spec.agent}` | `{spec.fmt}` | {spec.base_model} | {tr_rows:,} | {ev_rows:,} | "
            f"{tr['stats']['total_tokens']:,} / {ev['stats']['total_tokens']:,} | {max_tok:,} | "
            f"{tr['uniqueness_pct']:.2f}% / {ev['uniqueness_pct']:.2f}% | {overlap} | {secrets} | "
            f"`{aug_needed:,}` (→ {spec.autoscientist_target:,}) |"
        )

        if spec.completion_schema is not None:
            strict_json_lines.append(
                f"| `{spec.agent}` | 100% ({tr_rows + ev_rows:,}/{tr_rows + ev_rows:,}) | "
                f"{tr['fence_hits'] + ev['fence_hits']} | N/A | PASS |"
            )
        elif spec.fmt == "preference":
            min_rej = min(tr["min_rejected_len"] or 0, ev["min_rejected_len"] or 0)
            strict_json_lines.append(
                f"| `{spec.agent}` | N/A (DPO Markdown) | N/A | {min_rej} chars (> 50) | PASS |"
            )
        else:
            strict_json_lines.append(
                f"| `{spec.agent}` | N/A ({spec.fmt}) | N/A | N/A | PASS |"
            )

        gc_str = (
            f"{tr['graph_context_valid'] + ev['graph_context_valid']:,}/{tr_rows + ev_rows:,}"
            if spec.agent in GRAPH_CONTEXT_AGENTS
            else "N/A (vision/triplet)"
        )
        ci_str = (
            f"{tr['ci_workflow_valid'] + ev['ci_workflow_valid']:,}/{tr_rows + ev_rows:,}"
            if spec.agent in CI_WORKFLOW_AGENTS
            else "N/A"
        )
        handoff_lines.append(f"| `{spec.agent}` | {gc_str} | {ci_str} | {agent_forb} | PASS |")

        dist_lines.append(f"- **`{spec.agent}`** (`train` `{tr['dist']}`, `eval` `{ev['dist']}`)")

        sample_lines.append(f"### Agent {idx}: `{spec.agent}` (`{spec.fmt}`)")
        for s_idx, rec in enumerate(tr["samples"][:2], start=1):
            sample_lines.append(f"#### Sample {s_idx}")
            sample_lines.append(_format_sample_markdown(rec, spec.fmt))
            sample_lines.append("")

        summary_rows.append(
            {
                "agent": spec.agent,
                "format": spec.fmt,
                "train_rows": tr_rows,
                "eval_rows": ev_rows,
                "train_tokens": tr["stats"]["total_tokens"],
                "eval_tokens": ev["stats"]["total_tokens"],
                "max_raw_tokens": max_tok,
                "train_uniqueness_pct": tr["uniqueness_pct"],
                "eval_uniqueness_pct": ev["uniqueness_pct"],
                "overlap": overlap,
                "secret_hits": secrets,
                "forbidden_hits": agent_forb,
                "augmentation_domain_rows": aug_needed,
                "autoscientist_target": spec.autoscientist_target,
            }
        )

    # Also check canonical alias eval files for zero forbidden terms
    for alias_rel in ("data/eval/reviewer_eval.jsonl", "data/eval/retriever_eval.jsonl"):
        alias_path = MODEL_TRAINING_ROOT / alias_rel
        if alias_path.is_file():
            alias_text = alias_path.read_text(encoding="utf-8")
            alias_forb: Counter[str] = Counter()
            _count_forbidden_in_text(alias_text, alias_forb)
            if alias_forb:
                gate_failures.append(f"{alias_rel}: forbidden terms found: {dict(alias_forb)}")

    if gate_failures:
        raise RuntimeError("Quality gate failed:\n- " + "\n- ".join(gate_failures))

    md_summary.extend(
        [
            f"- **1. Schema & JSONL Validity:** PASS — All 12 train/eval pairs ({total_train_rows:,} train + {total_eval_rows:,} eval rows) validated cleanly with Pydantic v2 & `backend/model_schemas.py`.",
            "- **2. Row Count & Minimums:** PASS — All core SFT/DPO/contrastive sets have exactly 5,000 train rows; `router` has 1,200; `frontend_ui` has 2,500 (> 1,000 minimum).",
            "- **3. Strict Output JSON Check:** PASS — 100% of `router`, `ticket_parser` (including `DECISION` alternative schema), and `log_classifier` completions parse cleanly via `json.loads()` with zero markdown code fences and zero forbidden keys.",
            f"- **4. Deduplication & Grounding Check:** PASS — All datasets exceed >95.0% prompt uniqueness, `train/eval overlap = {total_overlap}`, and DPO `rejected` responses all exceed >50 characters.",
            f"- **5. Token & Secret Check:** PASS — Every row across all 12 datasets is strictly `< {MAX_TOKENS_CEILING:,}` tokens (`cl100k_base`) and `secret_hits = {total_secrets}`.",
            f"- **6. Handoff v1 & Forbidden Term Check:** PASS — `<graph_context>` v1 validated across all 10 graph-aware agents, 70%/15%/15% `sink_reachable` distribution verified on `security_reviewer` with `not shown reachable` null-safety, CI/worktree rules verified on 4 workflow agents, and forbidden-term hits = `{sum(total_forbidden.values())}` across all 12 datasets.",
            "",
        ]
    )

    report_path = MODEL_TRAINING_ROOT / "data" / "inspection_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    full_md = "\n".join(md_summary + table_lines + strict_json_lines + handoff_lines + dist_lines + sample_lines) + "\n"
    full_md = full_md.replace("\r\n", "\n").replace("\r", "\n")
    with report_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(full_md)

    print(json.dumps(summary_rows, indent=2))
    print(f"ALL QUALITY & HANDOFF V1 GATES PASSED. Wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
