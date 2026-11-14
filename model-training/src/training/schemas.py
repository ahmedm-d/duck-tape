"""Pydantic v2 validators for Adaption datasets and Wave 1 agent outputs."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Literal

import tiktoken
from pydantic import BaseModel, ConfigDict, Field, field_validator


class InstructionRow(BaseModel):
    """Adaption instruction-tuning JSONL row format."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1)
    completion: str = Field(min_length=1)


class ChatTrajectoryRow(BaseModel):
    """Adaption multi-turn chat trajectory JSONL row format."""

    model_config = ConfigDict(extra="forbid")

    chat: list[dict[str, Any]] = Field(min_length=1)

    @field_validator("chat")
    @classmethod
    def validate_messages(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for index, message in enumerate(value):
            role = message.get("role")
            content = message.get("content")
            if not isinstance(role, str) or not role.strip():
                raise ValueError(f"chat[{index}].role must be a non-empty string")
            if not isinstance(content, str) or not content.strip():
                raise ValueError(f"chat[{index}].content must be a non-empty string")
        return value


class PreferenceRow(BaseModel):
    """Adaption preference (DPO/RLHF) JSONL row format."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1)
    chosen: str = Field(min_length=1)
    rejected: str = Field(min_length=1)


class VisionInstructionRow(BaseModel):
    """Adaption vision instruction-tuning JSONL row format."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1)
    image_context: str = Field(min_length=1)
    completion: str = Field(min_length=1)


ROUTER_AGENTS = (
    "TicketParser",
    "Retriever",
    "Implementer",
    "TestWriter",
    "Reviewer",
    "SecurityReviewer",
    "DBMigration",
    "IaC",
    "FrontendUI",
    "HumanDecision",
)


class RouterCompletion(BaseModel):
    """Strict JSON completion schema for Agent 1 (router)."""

    model_config = ConfigDict(extra="forbid")

    next_agent: Literal[
        "TicketParser",
        "Retriever",
        "Implementer",
        "TestWriter",
        "Reviewer",
        "SecurityReviewer",
        "DBMigration",
        "IaC",
        "FrontendUI",
        "HumanDecision",
    ]
    routing_reason: str = Field(min_length=1)
    risk_level: Literal["low", "medium", "high"]
    token_budget: int = Field(ge=256, le=65536)


class TicketParserCompletion(BaseModel):
    """Strict JSON completion schema for Agent 2 (ticket_parser)."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    source_tag: Literal["ticket", "request", "security", "analysis", "research"]
    change_type: Literal["feature", "fix", "refactor", "security", "performance"]
    priority: Literal["P0", "P1", "P2", "P3"]
    candidate_files_or_symbols: list[str]
    needs_decision_card: bool
    decision_options: list[str]


class LogClassifierCompletion(BaseModel):
    """Strict JSON completion schema for Agent 4 (log_classifier)."""

    model_config = ConfigDict(extra="forbid")

    failure_category: Literal[
        "regression",
        "baseline_failure",
        "flaky_test",
        "missing_dependency",
        "syntax_error",
        "sandbox_blocked",
    ]
    failing_tests: list[str]
    root_cause_summary: str = Field(min_length=1)
    recommended_action: str = Field(min_length=1)


def _summarize_lengths(counts: list[int]) -> dict[str, float | int]:
    if not counts:
        return {"total": 0, "mean": 0.0, "min": 0, "max": 0, "p95": 0}
    ordered = sorted(counts)
    p95_idx = min(len(ordered) - 1, max(0, math.ceil(0.95 * len(ordered)) - 1))
    return {
        "total": sum(ordered),
        "mean": round(sum(ordered) / len(ordered), 2),
        "min": ordered[0],
        "max": ordered[-1],
        "p95": ordered[p95_idx],
    }


def compute_jsonl_token_stats(path: Path, encoding_name: str = "cl100k_base") -> dict[str, Any]:
    """Compute row count and tiktoken token distribution statistics for a JSONL file."""
    enc = tiktoken.get_encoding(encoding_name)
    prompt_tokens: list[int] = []
    completion_tokens: list[int] = []
    total_tokens: list[int] = []
    rows = 0

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            record = json.loads(raw)
            validated = InstructionRow.model_validate(record)
            p_len = len(enc.encode(validated.prompt, disallowed_special=()))
            c_len = len(enc.encode(validated.completion, disallowed_special=()))
            prompt_tokens.append(p_len)
            completion_tokens.append(c_len)
            total_tokens.append(p_len + c_len)
            rows += 1

    return {
        "path": str(path),
        "rows": rows,
        "encoding": encoding_name,
        "total_tokens": sum(total_tokens),
        "prompt": _summarize_lengths(prompt_tokens),
        "completion": _summarize_lengths(completion_tokens),
        "combined": _summarize_lengths(total_tokens),
    }
