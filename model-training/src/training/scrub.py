"""Regex-based secret and credential scrubber for scraped code, logs, and issues."""
from __future__ import annotations

import re
from typing import Iterable

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "pem_key",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"
        ),
        "[REDACTED_PEM_KEY]",
    ),
    (
        "openai_key",
        re.compile(r"\bsk-(?:proj-|live-|test-)?[A-Za-z0-9_\-]{20,}\b"),
        "[REDACTED_OPENAI_KEY]",
    ),
    (
        "huggingface_token",
        re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
        "[REDACTED_HF_TOKEN]",
    ),
    (
        "github_token",
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
        "[REDACTED_GITHUB_TOKEN]",
    ),
    (
        "aws_access_key",
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        "[REDACTED_AWS_KEY]",
    ),
    (
        "slack_token",
        re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),
        "[REDACTED_SLACK_TOKEN]",
    ),
    (
        "google_api_key",
        re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"),
        "[REDACTED_GOOGLE_KEY]",
    ),
    (
        "databricks_token",
        re.compile(r"\bdapi[0-9a-f]{20,}\b"),
        "[REDACTED_DATABRICKS_TOKEN]",
    ),
    (
        "bearer_header",
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]{12,}=*"),
        "Bearer [REDACTED_TOKEN]",
    ),
    (
        "generic_secret_assignment",
        re.compile(
            r"""(?i)(api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|client[_-]?secret)(\s*[:=]\s*)(['"])[A-Za-z0-9\-._~+/=]{12,}\3"""
        ),
        r"\1\2\3[REDACTED_SECRET]\3",
    ),
)


def scrub_with_count(text: str) -> tuple[str, int]:
    """Redact credentials, API tokens, and PEM keys from text, returning (cleaned, redaction_count)."""
    if not text:
        return text, 0
    cleaned = text
    total_redactions = 0
    for _name, pattern, replacement in _SECRET_PATTERNS:
        cleaned, count = pattern.subn(replacement, cleaned)
        total_redactions += count
    return cleaned, total_redactions


def scrub_text(text: str) -> str:
    """Redact credentials, API tokens, and PEM keys from scraped text."""
    cleaned, _ = scrub_with_count(text)
    return cleaned


def contains_secrets(text: str) -> bool:
    """Return True if any known credential or private-key pattern matches text."""
    if not text:
        return False
    return any(pattern.search(text) is not None for _name, pattern, _replacement in _SECRET_PATTERNS)


def scrub_rows(texts: Iterable[str]) -> tuple[list[str], int]:
    """Scrub an iterable of strings and return cleaned strings plus total redactions."""
    cleaned_list: list[str] = []
    total = 0
    for item in texts:
        cleaned, count = scrub_with_count(item)
        cleaned_list.append(cleaned)
        total += count
    return cleaned_list, total
