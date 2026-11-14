"""Shared environment, JSONL I/O, and code/diff extraction helpers."""
from __future__ import annotations

import os
import random
import re
from pathlib import Path
from typing import Sequence, TypeVar

from dotenv import load_dotenv

from .schemas import ChatTrajectoryRow, ContrastiveTripletRow, InstructionRow, PreferenceRow, VisionInstructionRow

T = TypeVar("T")

MODEL_TRAINING_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = MODEL_TRAINING_ROOT.parent

_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_./-])([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.(?:py|ts|tsx|js|jsx|sql|yml|yaml|json|toml|md|tf|sh|java|go|rs|cpp|c|h))\b"
)
_FILE_RE = re.compile(
    r"\b([A-Za-z0-9_.-]+\.(?:py|ts|tsx|js|jsx|sql|yml|yaml|json|toml|tf|Dockerfile))\b"
)
_DIFF_FILE_RE = re.compile(r"^(?:\+\+\+ b/|--- a/|diff --git a/\S+ b/)([^\s]+)", re.MULTILINE)
_PY_SYMBOL_RE = re.compile(r"^[+-]\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)
_TS_SYMBOL_RE = re.compile(
    r"^[+-]\s*(?:export\s+)?(?:async\s+)?(?:function|class|interface|type|const|enum)\s+([A-Za-z_$][A-Za-z0-9_$]*)",
    re.MULTILINE,
)
_CALL_OR_ATTR_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{2,}(?:\.[A-Za-z_][A-Za-z0-9_]{1,}){1,2})\b")
_BACKTICK_ID_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_./:-]{2,64})`")


def load_training_env() -> Path | None:
    """Load credentials from model-training/.env.training (or repo-root .env.training)."""
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    for candidate in (MODEL_TRAINING_ROOT / ".env.training", REPO_ROOT / ".env.training"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return candidate
    return None


def write_instruction_jsonl(rows: Sequence[InstructionRow], output_path: Path) -> Path:
    """Write validated InstructionRow objects as UTF-8 LF JSONL."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(row.model_dump_json() + "\n")
    return output_path


def write_chat_jsonl(rows: Sequence[ChatTrajectoryRow], output_path: Path) -> Path:
    """Write validated ChatTrajectoryRow objects as UTF-8 LF JSONL."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(row.model_dump_json() + "\n")
    return output_path


def write_preference_jsonl(rows: Sequence[PreferenceRow], output_path: Path) -> Path:
    """Write validated PreferenceRow objects as UTF-8 LF JSONL."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(row.model_dump_json() + "\n")
    return output_path


def write_vision_jsonl(rows: Sequence[VisionInstructionRow], output_path: Path) -> Path:
    """Write validated VisionInstructionRow objects as UTF-8 LF JSONL."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(row.model_dump_json() + "\n")
    return output_path


def write_triplet_jsonl(rows: Sequence[ContrastiveTripletRow], output_path: Path) -> Path:
    """Write validated ContrastiveTripletRow objects as UTF-8 LF JSONL."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(row.model_dump_json() + "\n")
    return output_path


def split_train_eval(
    rows: Sequence[T],
    train_count: int,
    eval_count: int,
    seed: int = 42,
) -> tuple[list[T], list[T]]:
    """Deterministically shuffle and split rows into disjoint train and eval subsets."""
    required = train_count + eval_count
    if len(rows) < required:
        raise ValueError(
            f"Collected {len(rows)} valid rows, fewer than required {required} ({train_count} train + {eval_count} eval)"
        )
    shuffled = list(rows)
    random.Random(seed).shuffle(shuffled)
    eval_rows = shuffled[:eval_count]
    train_rows = shuffled[eval_count : eval_count + train_count]
    return train_rows, eval_rows


def truncate_bounded(text: str, max_chars: int) -> str:
    """Bound text to max_chars cleanly with a truncation marker."""
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max(0, max_chars - 24)].rstrip() + "\n... [truncated]"


def extract_diff_paths(diff_text: str, limit: int = 12) -> list[str]:
    """Extract unique changed file paths from a unified git diff."""
    paths: list[str] = []
    seen: set[str] = set()
    for match in _DIFF_FILE_RE.findall(diff_text or ""):
        path = match.strip()
        if path == "/dev/null" or path in seen:
            continue
        seen.add(path)
        paths.append(path)
        if len(paths) >= limit:
            break
    return paths


def extract_diff_symbols(diff_text: str, limit: int = 12) -> list[str]:
    """Extract modified or added function/class/type symbol names from a unified git diff."""
    symbols: list[str] = []
    seen: set[str] = set()
    for pattern in (_PY_SYMBOL_RE, _TS_SYMBOL_RE):
        for sym in pattern.findall(diff_text or ""):
            if sym not in seen:
                seen.add(sym)
                symbols.append(sym)
                if len(symbols) >= limit:
                    return symbols
    return symbols


def extract_symbols_and_paths(text: str, diff_text: str = "", limit: int = 8) -> list[str]:
    """Extract candidate file paths and symbol identifiers from ticket text and optional diff."""
    candidates: list[str] = []
    seen: set[str] = set()
    clean_text = re.sub(r"https?://\S+", " ", text or "")

    def _add(item: str) -> None:
        cleaned = item.strip().strip(".,:;()[]{}\"'`")
        if not cleaned or len(cleaned) < 3 or len(cleaned) > 100:
            return
        if cleaned.lower() in {"http", "https", "true", "false", "none", "null", "self.assert"}:
            return
        if cleaned not in seen:
            seen.add(cleaned)
            candidates.append(cleaned)

    for path in extract_diff_paths(diff_text, limit=limit):
        _add(path)
    for sym in extract_diff_symbols(diff_text, limit=limit):
        _add(sym)
    for match in _PATH_RE.findall(clean_text):
        _add(match)
    for match in _FILE_RE.findall(clean_text):
        _add(match)
    for match in _BACKTICK_ID_RE.findall(clean_text):
        if "/" in match or "." in match or "_" in match or match[0].isupper():
            _add(match)
    for match in _CALL_OR_ATTR_RE.findall(clean_text):
        if not match.startswith(("e.g", "i.e", "v1.", "v2.", "127.")):
            _add(match)

    return candidates[:limit]
