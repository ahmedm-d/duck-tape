"""Wave 2 Data Prep — Agent 5: implementer (multi-turn ChatTrajectoryRow)."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import tiktoken
from datasets import load_dataset
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from training.schemas import ChatTrajectoryRow, count_chat_tokens
from training.scrub import scrub_with_count
from training.utils import (
    MODEL_TRAINING_ROOT,
    load_training_env,
    split_train_eval,
    write_chat_jsonl,
)

TRAIN_TARGET = 5000
EVAL_TARGET = 200
TOTAL_TARGET = TRAIN_TARGET + EVAL_TARGET
MAX_TOKENS = 45000


def _scrub_value(val: Any) -> tuple[Any, int]:
    """Recursively scrub string leaves in dicts/lists."""
    if isinstance(val, str):
        return scrub_with_count(val)
    if isinstance(val, dict):
        cleaned_dict: dict[str, Any] = {}
        total_red = 0
        for k, v in val.items():
            ck, r1 = scrub_with_count(str(k))
            cv, r2 = _scrub_value(v)
            cleaned_dict[ck] = cv
            total_red += r1 + r2
        return cleaned_dict, total_red
    if isinstance(val, list):
        cleaned_list: list[Any] = []
        total_red = 0
        for item in val:
            ci, r = _scrub_value(item)
            cleaned_list.append(ci)
            total_red += r
        return cleaned_list, total_red
    return val, 0


def _normalize_tool_calls(raw_tool_calls: Any, msg_idx: int) -> tuple[list[dict[str, Any]], int]:
    """Parse and normalize OpenAI tool_calls so function.arguments is always a scrubbed dict."""
    if isinstance(raw_tool_calls, str):
        try:
            raw_tool_calls = json.loads(raw_tool_calls)
        except json.JSONDecodeError:
            return [], 0
    if not isinstance(raw_tool_calls, list):
        return [], 0

    normalized: list[dict[str, Any]] = []
    redactions = 0

    for tc_idx, tc in enumerate(raw_tool_calls):
        if not isinstance(tc, dict):
            continue
        fn_raw = tc.get("function")
        if not isinstance(fn_raw, dict):
            fn_raw = {}
        fn_name_raw = fn_raw.get("name") or tc.get("name") or "tool"
        fn_name, r_name = scrub_with_count(str(fn_name_raw).strip() or "tool")
        redactions += r_name

        args_raw = fn_raw.get("arguments")
        if args_raw is None:
            args_raw = tc.get("arguments")

        if isinstance(args_raw, dict):
            parsed_args = args_raw
        elif isinstance(args_raw, str):
            stripped = args_raw.strip()
            if not stripped:
                parsed_args = {}
            else:
                try:
                    loaded = json.loads(stripped)
                    parsed_args = loaded if isinstance(loaded, dict) else {"value": loaded}
                except json.JSONDecodeError:
                    parsed_args = {"raw_arguments": stripped}
        elif args_raw is None:
            parsed_args = {}
        else:
            parsed_args = {"value": args_raw}

        cleaned_args, r_args = _scrub_value(parsed_args)
        redactions += r_args
        if not isinstance(cleaned_args, dict):
            cleaned_args = {"value": cleaned_args}

        tc_id_raw = str(tc.get("id") or f"call_{msg_idx}_{tc_idx}").strip()
        tc_id, r_id = scrub_with_count(tc_id_raw)
        redactions += r_id

        normalized.append(
            {
                "id": tc_id or f"call_{msg_idx}_{tc_idx}",
                "type": "function",
                "function": {
                    "name": fn_name,
                    "arguments": cleaned_args,
                },
            }
        )

    return normalized, redactions


def _normalize_trajectory(
    raw_messages: Any,
    enc: tiktoken.Encoding,
) -> tuple[ChatTrajectoryRow, int, int] | None:
    """Normalize raw message list into a validated <=45k token ChatTrajectoryRow."""
    if isinstance(raw_messages, str):
        try:
            raw_messages = json.loads(raw_messages)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw_messages, list) or len(raw_messages) < 2:
        return None

    chat: list[dict[str, Any]] = []
    total_redactions = 0

    for idx, msg in enumerate(raw_messages):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "user").strip().lower()
        if role not in ("system", "user", "assistant", "tool", "developer"):
            role = "user"

        raw_tcs = msg.get("tool_calls")
        normalized_tcs, r_tc = _normalize_tool_calls(raw_tcs, idx) if raw_tcs else ([], 0)
        total_redactions += r_tc

        raw_content = msg.get("content")
        if isinstance(raw_content, list):
            parts = []
            for part in raw_content:
                if isinstance(part, dict):
                    parts.append(str(part.get("text") or part.get("content") or ""))
                else:
                    parts.append(str(part))
            content_str = "\n".join(p for p in parts if p).strip()
        else:
            content_str = str(raw_content or "").strip()

        if not content_str:
            thought = str(msg.get("thought") or "").strip()
            action = str(msg.get("action") or "").strip()
            if thought and action:
                content_str = f"{thought}\n{action}"
            elif thought:
                content_str = thought
            elif action:
                content_str = action
            elif normalized_tcs:
                names = ", ".join(tc["function"]["name"] for tc in normalized_tcs)
                content_str = f"[tool_call: {names}]"
            elif role == "tool":
                content_str = "[tool output]"
            else:
                continue

        cleaned_content, r_content = scrub_with_count(content_str)
        total_redactions += r_content
        if not cleaned_content.strip():
            cleaned_content = "[redacted]"

        norm_msg: dict[str, Any] = {
            "role": role,
            "content": cleaned_content,
        }
        if normalized_tcs:
            norm_msg["tool_calls"] = normalized_tcs
        tc_id = msg.get("tool_call_id")
        if isinstance(tc_id, str) and tc_id.strip():
            cleaned_tc_id, r_tid = scrub_with_count(tc_id.strip())
            total_redactions += r_tid
            if cleaned_tc_id:
                norm_msg["tool_call_id"] = cleaned_tc_id
        name = msg.get("name")
        if isinstance(name, str) and name.strip():
            cleaned_name, r_nm = scrub_with_count(name.strip())
            total_redactions += r_nm
            if cleaned_name:
                norm_msg["name"] = cleaned_name

        chat.append(norm_msg)

    if len(chat) < 2:
        return None
    if not any(m["role"] == "assistant" for m in chat):
        return None

    # Fast token count using the shared cl100k_base encoding
    total_tokens = 0
    for m in chat:
        total_tokens += len(enc.encode(m["content"], disallowed_special=()))
        if m.get("tool_calls"):
            total_tokens += len(enc.encode(json.dumps(m["tool_calls"], ensure_ascii=False), disallowed_special=()))
        if total_tokens > MAX_TOKENS:
            return None

    row = ChatTrajectoryRow(chat=chat)
    p_toks, c_toks = count_chat_tokens(row.chat)
    if (p_toks + c_toks) > MAX_TOKENS:
        return None
    return row, total_redactions, (p_toks + c_toks)


def _issue_signature(chat: list[dict[str, Any]], instance_id: str) -> str:
    if instance_id:
        return f"id:{instance_id.strip().lower()}"
    for msg in chat:
        if msg.get("role") == "user":
            norm = " ".join(str(msg.get("content") or "").split())[:300]
            return "msg:" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:24]
    norm_first = " ".join(str(chat[0].get("content") or "").split())[:300]
    return "first:" + hashlib.sha256(norm_first.encode("utf-8")).hexdigest()[:24]


def collect_rows() -> tuple[list[ChatTrajectoryRow], dict[str, int], dict[str, int]]:
    """Stream resolved trajectories from Nebius, SWE-smith-trajectories, and R2E-Gym."""
    enc = tiktoken.get_encoding("cl100k_base")
    rows: list[ChatTrajectoryRow] = []
    seen_sigs: set[str] = set()
    source_counts: dict[str, int] = {}
    stats = {
        "redactions": 0,
        "dropped_unresolved": 0,
        "dropped_too_long": 0,
        "dropped_duplicate": 0,
        "dropped_invalid": 0,
    }

    def _stream_source(
        label: str,
        ds_name: str,
        split_name: str,
        msg_field: str,
        quota: int,
        require_resolved: bool,
    ) -> None:
        if quota <= 0:
            return
        ds = load_dataset(ds_name, split=split_name, streaming=True)
        collected = 0
        pbar = tqdm(total=quota, desc=f"implementer <- {label}", unit="rows")
        for rec in ds:
            if collected >= quota or len(rows) >= TOTAL_TARGET:
                break
            if require_resolved:
                resolved_val = rec.get("resolved")
                if not (resolved_val is True or resolved_val == 1):
                    stats["dropped_unresolved"] += 1
                    continue
            instance_id = str(rec.get("instance_id") or "").strip()
            id_sig = f"id:{instance_id.lower()}" if instance_id else ""
            if id_sig and id_sig in seen_sigs:
                stats["dropped_duplicate"] += 1
                continue

            norm = _normalize_trajectory(rec.get(msg_field), enc)
            if norm is None:
                stats["dropped_too_long"] += 1
                continue
            row, red, _toks = norm
            sig = _issue_signature(row.chat, instance_id)
            if sig in seen_sigs:
                stats["dropped_duplicate"] += 1
                continue

            # Also check first user message signature to prevent cross-dataset duplicates
            user_sig = _issue_signature(row.chat, "")
            if user_sig in seen_sigs:
                stats["dropped_duplicate"] += 1
                continue

            seen_sigs.add(sig)
            seen_sigs.add(user_sig)
            rows.append(row)
            source_counts[label] = source_counts.get(label, 0) + 1
            stats["redactions"] += red
            collected += 1
            pbar.update(1)
        pbar.close()

    # 1. nebius/SWE-rebench-openhands-trajectories
    _stream_source(
        "nebius/SWE-rebench-openhands-trajectories",
        "nebius/SWE-rebench-openhands-trajectories",
        "train",
        "trajectory",
        1700,
        require_resolved=True,
    )

    # 2. SWE-bench/SWE-smith-trajectories (tool split)
    _stream_source(
        "SWE-bench/SWE-smith-trajectories (tool)",
        "SWE-bench/SWE-smith-trajectories",
        "tool",
        "messages",
        2100,
        require_resolved=True,
    )

    # 3. R2E-Gym/R2EGym-SFT-Trajectories (pre-filtered resolved SFT trajectories)
    rem = TOTAL_TARGET - len(rows)
    _stream_source(
        "R2E-Gym/R2EGym-SFT-Trajectories",
        "R2E-Gym/R2EGym-SFT-Trajectories",
        "train",
        "messages",
        min(1400, rem),
        require_resolved=False,
    )

    # 4. Top up any shortfall from SWE-bench/SWE-smith-trajectories (ticks split)
    if len(rows) < TOTAL_TARGET:
        _stream_source(
            "SWE-bench/SWE-smith-trajectories (ticks)",
            "SWE-bench/SWE-smith-trajectories",
            "ticks",
            "messages",
            TOTAL_TARGET - len(rows),
            require_resolved=True,
        )

    return rows, source_counts, stats


def main() -> int:
    load_training_env()
    rows, source_counts, stats = collect_rows()
    train_rows, eval_rows = split_train_eval(rows, TRAIN_TARGET, EVAL_TARGET, seed=42)

    train_path = MODEL_TRAINING_ROOT / "data" / "processed" / "implementer.jsonl"
    eval_path = MODEL_TRAINING_ROOT / "data" / "eval" / "implementer_eval.jsonl"
    write_chat_jsonl(train_rows, train_path)
    write_chat_jsonl(eval_rows, eval_path)

    print(
        f"[implementer] wrote {len(train_rows)} train -> {train_path} | "
        f"{len(eval_rows)} eval -> {eval_path} | stats={stats} | sources={source_counts}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
