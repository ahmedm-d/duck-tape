"""Shared boundaries for source evidence, proposed changes and isolated execution."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field


class Record(BaseModel):
    model_config = ConfigDict(extra='allow')


class SourceEntry(Record):
    path: str
    status: str = 'included'
    hash: str | None = None
    size: int = 0
    mode: int = 0o100644
    reason: str = ''


class SourceSnapshot(Record):
    id: str
    repo_id: str
    manifest_hash: str
    files: list[SourceEntry] = Field(default_factory=list)
    origin: Any = None
    branch: str | None = None
    commit: str | None = None
    gaps: list[str] = Field(default_factory=list)


class EvidenceRef(Record):
    repo_id: str = ''
    snapshot_id: str = ''
    path: str
    blob_hash: str = ''
    chunk_id: str = ''
    start_line: int = Field(default=1, ge=1)
    end_line: int = Field(default=1, ge=1)
    reason: str = ''


class ContextBundle(Record):
    items: list[dict] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    knowledge: list[dict] = Field(default_factory=list)
    snapshot: str = ''
    repo_id: str = ''
    overview: str = ''
    context_hash: str = ''
    token_estimate: int = 0


class EditOperation(Record):
    op: Literal['create', 'update', 'delete', 'rename'] = 'update'
    path: str
    expected_hash: str | None = None
    destination: str | None = None
    content: str | None = None
    replacements: list[dict[str, str]] = Field(default_factory=list)
    complete_file: bool = False


class CandidatePatch(Record):
    base_snapshot: str
    diff: str
    patch_hash: str
    files: list[dict] = Field(default_factory=list)
    worktree: str = ''
    attempt: int = 1


class ExecutionLimits(Record):
    cpus: float = Field(default=2, gt=0, le=8)
    memory_mb: int = Field(default=2048, ge=128, le=16384)
    pids: int = Field(default=256, ge=16, le=1024)
    timeout_seconds: int = Field(default=300, ge=1, le=1800)
    log_bytes: int = Field(default=1048576, ge=1024, le=4194304)


class CheckCommand(Record):
    id: str
    argv: list[str]
    cwd: str = '.'
    purpose: str = 'test'
    timeout_seconds: int = Field(default=300, ge=1, le=1800)
    required: bool = True


class CheckPlan(Record):
    profile: str
    commands: list[CheckCommand] = Field(default_factory=list)
    image: str | None = None
    image_digest: str = ''
    dependency_fingerprint: str = ''
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)
    approval: dict | None = None
    fingerprint: str = ''


class CheckResult(Record):
    id: str = ''
    phase: str
    status: str
    command: Any = ''
    exit_code: int | None = None
    exit_reason: str = ''
    duration: float = 0
    stdout: str = ''
    base_snapshot: str = ''
    patch_hash: str = ''
    environment: Any = None


class ProviderRequest(Record):
    task: str
    payload: dict
    risk: str = 'low'
    repo_id: str = ''
    snapshot_id: str = ''
    scope: str = ''
    failure_count: int = 0
    max_output_tokens: int = Field(default=4096, ge=1, le=32768)
    cache: bool = True


class ProviderResult(Record):
    output: dict
    telemetry: dict


class RunInputs(Record):
    snapshot: str
    plan: Any = None
    context: Any = None
    knowledge: list[dict] = Field(default_factory=list)
    decisions: list[dict] = Field(default_factory=list)
    previous_revision: Any = None
    execution_policy: Any = None


class RunCheckpoint(Record):
    stage: str
    owner: str
    input_fingerprint: str
    outputs: dict = Field(default_factory=dict)
    attempts: dict = Field(default_factory=dict)


class CancelledError(Exception):
    pass


class CancellationToken:
    def __init__(self, predicate: Callable[[], bool] | None = None, deadline: float | None = None):
        self._event = threading.Event()
        self.predicate = predicate
        self.deadline = deadline

    def cancel(self):
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set() or bool(self.predicate and self.predicate()) or bool(self.deadline and time.monotonic() >= self.deadline)

    def is_set(self) -> bool:
        return self.is_cancelled()

    def wait(self, seconds: float) -> bool:
        end = time.monotonic() + seconds
        while not self.is_cancelled() and time.monotonic() < end:
            self._event.wait(min(.1, max(0, end - time.monotonic())))
        return self.is_cancelled()

    def raise_if_cancelled(self):
        if self.is_cancelled():
            raise CancelledError('Work cancelled, superseded or deadline exceeded')
