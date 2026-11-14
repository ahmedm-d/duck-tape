#!/usr/bin/env python3
"""Multi-account Adaption Labs parallel training orchestrator for Duck Tape (Part 4 of 5).

Coordinates dataset validation, Web UI router auto-discovery, raw-mode uploads
(preserving <graph_context> blocks and JSON schemas while saving Adaptive Data
credits), custom security eval registration, multi-turn trajectory extension,
and concurrent AutoScientist SFT/DPO launches across 4 Adaption accounts.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import copy
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from adaption import APIError, APIStatusError, Adaption

SCRIPT_DIR = Path(__file__).resolve().parent
MODEL_TRAINING_ROOT = SCRIPT_DIR.parent
REPO_ROOT = MODEL_TRAINING_ROOT.parent

sys.path.insert(0, str(MODEL_TRAINING_ROOT / "src"))
from training.utils import load_training_env  # noqa: E402

PROCESSED_DIR = MODEL_TRAINING_ROOT / "data" / "processed"
EVAL_DIR = MODEL_TRAINING_ROOT / "data" / "eval"
RAW_DIR = MODEL_TRAINING_ROOT / "data" / "raw"
MANIFEST_PATH = MODEL_TRAINING_ROOT / "data" / "adaption_runs_manifest.json"

CREDITS_PER_ACCOUNT = 1250
MAX_CONCURRENT_JOBS_PER_ACCOUNT = 5
TERMINAL_STATUSES = frozenset({"succeeded", "completed", "failed", "cancelled", "eval_failed"})
FAILURE_STATUSES = frozenset({"failed", "cancelled", "eval_failed"})

_MANIFEST_LOCK = threading.Lock()


@dataclass(frozen=True)
class AgentLaunchSpec:
    """Configuration for a single specialist agent training run."""

    account_index: int
    account_role: str
    agent: str
    wave: Literal["priority", "all"]
    priority_order: int
    dataset_filename: str
    eval_filename: str | None
    model: str
    fallback_model: str | None
    training_method: Literal["instruction", "alignment"]
    data_format: Literal["chat", "instruction"]
    processing_mode: Literal["raw", "adapt"]
    target_effective_rows: int
    column_mapping: dict[str, Any]
    is_chat_source: bool = False
    extend_slice_rows: int = 0
    register_custom_eval: bool = False
    discover_web_ui: bool = False

    @property
    def idempotency_key(self) -> str:
        return f"duck-tape-{self.agent}-v1"

    @property
    def dataset_name(self) -> str:
        return f"duck_tape_{self.agent}"

    @property
    def dataset_path(self) -> Path:
        return PROCESSED_DIR / self.dataset_filename

    @property
    def eval_path(self) -> Path | None:
        return (EVAL_DIR / self.eval_filename) if self.eval_filename else None


AGENT_SPECS: tuple[AgentLaunchSpec, ...] = (
    # Account 1: Intake & Routing
    AgentLaunchSpec(
        account_index=1,
        account_role="Intake & Routing",
        agent="router",
        wave="priority",
        priority_order=1,
        dataset_filename="router.jsonl",
        eval_filename="router_eval.jsonl",
        model="google/gemma-4-31B-it",
        fallback_model=None,
        training_method="instruction",
        data_format="instruction",
        processing_mode="raw",
        target_effective_rows=10000,
        column_mapping={"prompt": "prompt", "completion": "completion"},
        discover_web_ui=True,
    ),
    AgentLaunchSpec(
        account_index=1,
        account_role="Intake & Routing",
        agent="ticket_parser",
        wave="priority",
        priority_order=2,
        dataset_filename="ticket_parser.jsonl",
        eval_filename="ticket_parser_eval.jsonl",
        model="google/gemma-4-31B-it",
        fallback_model=None,
        training_method="instruction",
        data_format="instruction",
        processing_mode="raw",
        target_effective_rows=10000,
        column_mapping={"prompt": "prompt", "completion": "completion"},
    ),
    # Account 2: CI Implementation & Regression Testing
    AgentLaunchSpec(
        account_index=2,
        account_role="CI Implementation & Regression Testing",
        agent="implementer",
        wave="priority",
        priority_order=1,
        dataset_filename="implementer.jsonl",
        eval_filename="implementer_eval.jsonl",
        model="Qwen/Qwen3.6-35B-A3B",
        fallback_model=None,
        training_method="instruction",
        data_format="chat",
        processing_mode="raw",
        target_effective_rows=10000,
        column_mapping={"prompt": "prompt", "completion": "completion"},
        is_chat_source=True,
        extend_slice_rows=500,
    ),
    AgentLaunchSpec(
        account_index=2,
        account_role="CI Implementation & Regression Testing",
        agent="test_writer",
        wave="priority",
        priority_order=2,
        dataset_filename="test_writer.jsonl",
        eval_filename="test_writer_eval.jsonl",
        model="Qwen/Qwen3.6-35B-A3B",
        fallback_model=None,
        training_method="instruction",
        data_format="instruction",
        processing_mode="raw",
        target_effective_rows=10000,
        column_mapping={"prompt": "prompt", "completion": "completion"},
    ),
    # Account 3: Security & Code Review DPO
    AgentLaunchSpec(
        account_index=3,
        account_role="Security & Code Review DPO",
        agent="security_reviewer_dpo",
        wave="priority",
        priority_order=1,
        dataset_filename="security_reviewer_dpo.jsonl",
        eval_filename="security_reviewer_eval.jsonl",
        model="Qwen/Qwen3.6-35B-A3B",
        fallback_model=None,
        training_method="alignment",
        data_format="instruction",
        processing_mode="raw",
        target_effective_rows=12000,
        column_mapping={"prompt": "prompt", "chosen": "chosen", "rejected": "rejected"},
        register_custom_eval=True,
    ),
    AgentLaunchSpec(
        account_index=3,
        account_role="Security & Code Review DPO",
        agent="reviewer_dpo",
        wave="priority",
        priority_order=2,
        dataset_filename="reviewer_dpo.jsonl",
        eval_filename="reviewer_dpo_eval.jsonl",
        model="Qwen/Qwen3.6-35B-A3B",
        fallback_model=None,
        training_method="alignment",
        data_format="instruction",
        processing_mode="raw",
        target_effective_rows=12000,
        column_mapping={"prompt": "prompt", "chosen": "chosen", "rejected": "rejected"},
    ),
    # Account 4: CI Logs, Git PRs & Specialists (up to 5 concurrent jobs)
    AgentLaunchSpec(
        account_index=4,
        account_role="CI Logs, Git PRs & Specialists",
        agent="log_classifier",
        wave="priority",
        priority_order=1,
        dataset_filename="log_classifier.jsonl",
        eval_filename="log_classifier_eval.jsonl",
        model="google/gemma-4-31B-it",
        fallback_model=None,
        training_method="instruction",
        data_format="instruction",
        processing_mode="raw",
        target_effective_rows=10000,
        column_mapping={"prompt": "prompt", "completion": "completion"},
    ),
    AgentLaunchSpec(
        account_index=4,
        account_role="CI Logs, Git PRs & Specialists",
        agent="commit_pr_writer",
        wave="priority",
        priority_order=2,
        dataset_filename="commit_pr_writer.jsonl",
        eval_filename="commit_pr_writer_eval.jsonl",
        model="google/gemma-4-31B-it",
        fallback_model=None,
        training_method="instruction",
        data_format="instruction",
        processing_mode="raw",
        target_effective_rows=10000,
        column_mapping={"prompt": "prompt", "completion": "completion"},
    ),
    AgentLaunchSpec(
        account_index=4,
        account_role="CI Logs, Git PRs & Specialists",
        agent="db_migration",
        wave="all",
        priority_order=3,
        dataset_filename="db_migration.jsonl",
        eval_filename="db_migration_eval.jsonl",
        model="Qwen/Qwen3.6-35B-A3B",
        fallback_model=None,
        training_method="instruction",
        data_format="instruction",
        processing_mode="raw",
        target_effective_rows=10000,
        column_mapping={"prompt": "prompt", "completion": "completion"},
    ),
    AgentLaunchSpec(
        account_index=4,
        account_role="CI Logs, Git PRs & Specialists",
        agent="iac",
        wave="all",
        priority_order=4,
        dataset_filename="iac.jsonl",
        eval_filename="iac_eval.jsonl",
        model="Qwen/Qwen3.6-35B-A3B",
        fallback_model=None,
        training_method="instruction",
        data_format="instruction",
        processing_mode="raw",
        target_effective_rows=10000,
        column_mapping={"prompt": "prompt", "completion": "completion"},
    ),
    AgentLaunchSpec(
        account_index=4,
        account_role="CI Logs, Git PRs & Specialists",
        agent="frontend_ui",
        wave="all",
        priority_order=5,
        dataset_filename="frontend_ui.jsonl",
        eval_filename="frontend_ui_eval.jsonl",
        model="google/gemma-4-31B-it-VLM",
        fallback_model="Qwen/Qwen3.5-9B",
        training_method="instruction",
        data_format="instruction",
        processing_mode="raw",
        target_effective_rows=1000,
        column_mapping={"prompt": "prompt", "completion": "completion", "context": ["image_context"]},
    ),
)


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp string."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def make_adaption_client(api_key: str) -> Adaption:
    """Create an Adaption client and attach custom_evals alias for SDK parity."""
    client = Adaption(api_key=api_key, timeout=180.0, max_retries=3)
    if not hasattr(client, "custom_evals") or getattr(client, "custom_evals", None) is None:
        setattr(client, "custom_evals", client.datasets.custom_evals)
    return client


def load_account_keys() -> dict[int, str]:
    """Load ADAPTION_API_KEY_1..4 from model-training/.env.training or process env."""
    load_training_env()
    keys: dict[int, str] = {}
    missing: list[str] = []
    for idx in range(1, 5):
        env_var = f"ADAPTION_API_KEY_{idx}"
        val = (os.environ.get(env_var) or "").strip()
        if not val:
            missing.append(env_var)
        else:
            keys[idx] = val
    if missing:
        raise RuntimeError(f"Missing required Adaption API keys: {', '.join(missing)}")
    return keys


def count_jsonl_rows(path: Path) -> int:
    """Count non-empty JSONL lines in path."""
    if not path.is_file():
        return 0
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count


def compute_augmentation_rows(base_rows: int, target_effective_rows: int) -> int:
    """Compute required augmentation_domain_rows without overshooting target."""
    return max(0, target_effective_rows - base_rows)


def estimate_raw_credits(aug_domain_rows: int, extend_rows: int = 0) -> float:
    """Estimate Adaption credits when using processing_mode='raw' (~1 credit / 100 aug rows)."""
    # Raw uploads avoid Adaptive Data per-row adaptation fees; domain augmentation & trajectory extension bill per row.
    aug_credits = (aug_domain_rows + 99) // 100 if aug_domain_rows > 0 else 0
    ext_credits = (extend_rows + 99) // 100 if extend_rows > 0 else 0
    return float(aug_credits + ext_credits)


def load_manifest() -> dict[str, Any]:
    """Load model-training/data/adaption_runs_manifest.json or return base skeleton."""
    if MANIFEST_PATH.is_file():
        try:
            data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("runs", [])
                data.setdefault("datasets", {})
                data.setdefault("custom_evals", {})
                data.setdefault("trajectory_extensions", {})
                data.setdefault("accounts", {})
                return data
        except json.JSONDecodeError:
            pass
    return {
        "schema_version": "2.0",
        "pipeline_part": "4_of_5_adaption_parallel_training",
        "branch": "feat/agent/model-training",
        "training_started": False,
        "accounts": {},
        "datasets": {},
        "custom_evals": {},
        "trajectory_extensions": {},
        "runs": [],
    }


def save_manifest_unlocked(manifest: dict[str, Any]) -> None:
    """Atomically write manifest JSON with UTF-8 LF formatting."""
    manifest["schema_version"] = "2.0"
    manifest["pipeline_part"] = "4_of_5_adaption_parallel_training"
    manifest["branch"] = "feat/agent/model-training"
    manifest["updated_at"] = utc_now_iso()
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = MANIFEST_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp_path, MANIFEST_PATH)


def update_manifest(mutator: Any) -> dict[str, Any]:
    """Thread-safely read, mutate, and persist the manifest immediately."""
    with _MANIFEST_LOCK:
        manifest = load_manifest()
        mutator(manifest)
        save_manifest_unlocked(manifest)
        return copy.deepcopy(manifest)


def _upsert_run_record(manifest: dict[str, Any], record: dict[str, Any]) -> None:
    """Insert or update a run entry in manifest['runs'] by idempotency_key or agent."""
    runs = manifest.setdefault("runs", [])
    key = record.get("idempotency_key")
    agent = record.get("agent")
    for idx, existing in enumerate(runs):
        if (key and existing.get("idempotency_key") == key) or (agent and existing.get("agent") == agent):
            merged = {**existing, **record}
            runs[idx] = merged
            return
    runs.append(record)


def _run_to_dict(
    run: Any,
    *,
    spec: AgentLaunchSpec,
    base_rows: int,
    aug_rows: int,
    adopted_from_web_ui: bool = False,
    notes: str | None = None,
) -> dict[str, Any]:
    """Convert an AutoscientistRun response into a manifest record."""
    status = getattr(run, "status", "unknown")
    best_hp = getattr(run, "best_hyperparams", None)
    if best_hp is not None and hasattr(best_hp, "model_dump"):
        best_hp = best_hp.model_dump()
    created_at = getattr(run, "created_at", None)
    completed_at = getattr(run, "completed_at", None)
    return {
        "agent": spec.agent,
        "account_index": spec.account_index,
        "account_role": spec.account_role,
        "wave": spec.wave,
        "idempotency_key": spec.idempotency_key,
        "run_id": run.id,
        "dataset_id": getattr(run, "dataset_id", None),
        "model": getattr(run, "model", None) or spec.model,
        "training_method": getattr(run, "training_method", None) or spec.training_method,
        "status": status,
        "iterations_completed": getattr(run, "iterations_completed", 0),
        "max_iterations": getattr(run, "max_iterations", None),
        "target_win_rate": getattr(run, "target_win_rate", None),
        "best_win_rate": getattr(run, "best_win_rate", None),
        "download_available": getattr(run, "download_available", False),
        "error": getattr(run, "error", None),
        "best_hyperparams": best_hp,
        "base_rows": base_rows,
        "augmentation_domain_rows": aug_rows,
        "effective_rows": base_rows + aug_rows,
        "processing_mode": spec.processing_mode,
        "adopted_from_web_ui": adopted_from_web_ui,
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
        "completed_at": completed_at.isoformat() if hasattr(completed_at, "isoformat") else completed_at,
        "last_checked_at": utc_now_iso(),
        "notes": notes,
    }


def _discover_account1_router(client: Adaption) -> dict[str, Any]:
    """Inspect Account 1 datasets and runs for an existing Web UI router dataset/run."""
    datasets = list(client.datasets.list(limit=50))
    runs = list(client.autoscientist.list(limit=50))

    web_ui_datasets = [
        d
        for d in datasets
        if "router" in (getattr(d, "name", None) or "").lower()
        and not (getattr(d, "name", None) or "").startswith("duck_tape_")
    ]
    raw_uploaded_datasets = [
        d for d in datasets if (getattr(d, "name", None) or "") == "duck_tape_router"
    ]

    web_ui_ds = web_ui_datasets[0] if web_ui_datasets else None
    web_ui_ds_detail = client.datasets.get(web_ui_ds.dataset_id) if web_ui_ds else None

    matching_run = None
    router_ds_ids = {d.dataset_id for d in (web_ui_datasets + raw_uploaded_datasets)}
    for r in runs:
        if getattr(r, "dataset_id", None) in router_ds_ids:
            if getattr(r, "status", None) not in FAILURE_STATUSES:
                matching_run = r
                break

    return {
        "datasets_total": len(datasets),
        "runs_total": len(runs),
        "web_ui_dataset": web_ui_ds_detail.model_dump(mode="json") if web_ui_ds_detail else None,
        "raw_uploaded_dataset": (
            raw_uploaded_datasets[0].model_dump(mode="json") if raw_uploaded_datasets else None
        ),
        "matching_run": matching_run.model_dump(mode="json") if matching_run else None,
        "matching_run_obj": matching_run,
    }


def _format_chat_row_for_raw_sft(chat: list[dict[str, Any]]) -> dict[str, str]:
    """Convert a multi-turn chat trajectory into prompt/completion preserving <graph_context> verbatim."""
    if len(chat) < 2:
        raise ValueError(f"Expected >= 2 chat turns, got {len(chat)}")

    prompt_blocks: list[str] = []
    for msg in chat[:-1]:
        role = str(msg.get("role") or "user")
        content = str(msg.get("content") or "")
        block = f"[{role}]\n{content}"
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            block += "\n[tool_calls]\n" + json.dumps(tool_calls, ensure_ascii=False)
        prompt_blocks.append(block)

    last_msg = chat[-1]
    completion_text = str(last_msg.get("content") or "").strip()
    last_tool_calls = last_msg.get("tool_calls")
    if last_tool_calls:
        tc_json = json.dumps(last_tool_calls, ensure_ascii=False)
        if completion_text:
            completion_text = f"{completion_text}\n\n<tool_calls>\n{tc_json}"
        else:
            completion_text = f"<tool_calls>\n{tc_json}"

    return {
        "prompt": "\n\n".join(prompt_blocks),
        "completion": completion_text,
    }


def _prepare_implementer_artifacts(source_path: Path, slice_rows: int = 500) -> tuple[Path, Path]:
    """Materialize gitignored slice (chat) and raw-SFT projection files under model-training/data/raw/."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    slice_path = RAW_DIR / f"implementer_slice_{slice_rows}.jsonl"
    raw_sft_path = RAW_DIR / "implementer_raw_sft.jsonl"

    source_count = count_jsonl_rows(source_path)
    need_slice = count_jsonl_rows(slice_path) != min(slice_rows, source_count)
    need_sft = count_jsonl_rows(raw_sft_path) != source_count

    if not need_slice and not need_sft:
        return slice_path, raw_sft_path

    slice_tmp = slice_path.with_suffix(".jsonl.tmp") if need_slice else None
    sft_tmp = raw_sft_path.with_suffix(".jsonl.tmp") if need_sft else None

    slice_fp = slice_tmp.open("w", encoding="utf-8", newline="\n") if slice_tmp else None
    sft_fp = sft_tmp.open("w", encoding="utf-8", newline="\n") if sft_tmp else None
    try:
        with source_path.open("r", encoding="utf-8") as src_fp:
            written = 0
            for line in src_fp:
                stripped = line.strip()
                if not stripped:
                    continue
                row = json.loads(stripped)
                if slice_fp is not None and written < slice_rows:
                    slice_fp.write(stripped + "\n")
                if sft_fp is not None:
                    sft_row = _format_chat_row_for_raw_sft(row["chat"])
                    sft_fp.write(json.dumps(sft_row, ensure_ascii=False) + "\n")
                written += 1
    finally:
        if slice_fp is not None:
            slice_fp.close()
        if sft_fp is not None:
            sft_fp.close()

    if slice_tmp is not None:
        os.replace(slice_tmp, slice_path)
    if sft_tmp is not None:
        os.replace(sft_tmp, raw_sft_path)

    return slice_path, raw_sft_path


def _prepare_dpo_with_completion_fallback(source_path: Path, agent: str) -> Path:
    """Create a gitignored raw DPO JSONL with a completion alias column if server requires it."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RAW_DIR / f"{agent}_raw_dpo.jsonl"
    src_count = count_jsonl_rows(source_path)
    if count_jsonl_rows(out_path) == src_count:
        return out_path

    tmp_path = out_path.with_suffix(".jsonl.tmp")
    with source_path.open("r", encoding="utf-8") as in_fp, tmp_path.open("w", encoding="utf-8", newline="\n") as out_fp:
        for line in in_fp:
            stripped = line.strip()
            if not stripped:
                continue
            row = json.loads(stripped)
            row.setdefault("completion", row["chosen"])
            out_fp.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp_path, out_path)
    return out_path


def _build_security_custom_eval_rubric(eval_path: Path | None) -> str:
    """Construct the custom judge rubric informed by security_reviewer_eval.jsonl."""
    eval_rows = count_jsonl_rows(eval_path) if eval_path else 0
    cve_count = 0
    if eval_path and eval_path.is_file():
        with eval_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if "CVE-2024-35195" in line:
                    cve_count += 1
    return (
        f"Evaluate Duck Tape security advisory triage responses against the {eval_rows}-row "
        f"security_reviewer_eval.jsonl benchmark (including {cve_count} CVE-2024-35195 OSV lockfile cases). "
        "Score 10 (on a 0 to 10 whole-number scale) only when the response strictly obeys all 4 null-safety and graph-evidence rules: "
        "(1) A dependency version match establishes affected-version applicability, never automatic exploitability; "
        "(2) Treat sink_reachable: null in <graph_context> as unknown (never as false), NEVER suppress the finding, "
        "and describe reachability using the exact phrase 'not shown reachable' (never 'non-exploitable'); "
        "(3) When sink_reachable is true, cite the EXTRACTED call/import edge and prioritize immediate remediation; "
        "(4) Distinguish directly_imported true/false/null accurately (e.g., transitive dependency vs direct import). "
        "Deduct points proportionally for each rule violated, giving a final integer score between 0 and 10."
    )


def _find_reusable_remote_dataset(client: Adaption, dataset_name: str) -> Any | None:
    """Find an existing non-failed dataset with exact dataset_name."""
    for ds in client.datasets.list(limit=50):
        if (getattr(ds, "name", None) or "") == dataset_name and getattr(ds, "status", None) != "failed":
            return ds
    return None


def _upload_and_wait_raw(
    client: Adaption,
    *,
    file_path: Path,
    dataset_name: str,
    column_mapping: dict[str, Any],
    processing_mode: Literal["raw", "adapt"] | None = "raw",
) -> tuple[str, Any]:
    """Upload a local JSONL file and wait until it reaches a terminal dataset status."""
    existing = _find_reusable_remote_dataset(client, dataset_name)
    if existing is not None:
        dataset_id = existing.dataset_id
        status = getattr(existing, "status", None)
        if status == "succeeded":
            return dataset_id, client.datasets.get(dataset_id)
        if status in {"pending", "running", "processing"}:
            status_resp = client.datasets.wait_for_completion(
                dataset_id, initial_interval=2.0, max_interval=15.0, backoff_factor=1.5, timeout=600.0
            )
            if status_resp.status == "succeeded":
                return dataset_id, client.datasets.get(dataset_id)

    upload_kwargs: dict[str, Any] = {"name": dataset_name}
    if processing_mode is not None:
        upload_kwargs["processing_mode"] = processing_mode
        upload_kwargs["column_mapping"] = column_mapping

    resp = client.datasets.upload_file(file_path, **upload_kwargs)
    dataset_id = resp.dataset_id
    if processing_mode is None:
        return dataset_id, client.datasets.get(dataset_id)

    status_resp = client.datasets.wait_for_completion(
        dataset_id, initial_interval=2.0, max_interval=15.0, backoff_factor=1.5, timeout=600.0
    )
    detail = client.datasets.get(dataset_id)
    if status_resp.status != "succeeded":
        raise RuntimeError(
            f"Dataset {dataset_name} ({dataset_id}) ended in status={status_resp.status!r}: "
            f"{getattr(detail, 'error_data', None)}"
        )
    return dataset_id, detail


def _maybe_run_implementer_trajectory_extension(
    client: Adaption,
    spec: AgentLaunchSpec,
    slice_path: Path,
) -> None:
    """Optionally demonstrate extend_trajectories() on a 500-row implementer slice without blocking SFT."""
    manifest = load_manifest()
    existing_ext = manifest.get("trajectory_extensions", {}).get(spec.agent, {})
    if existing_ext.get("run_id") and existing_ext.get("status") not in FAILURE_STATUSES:
        print(f"[Account {spec.account_index} | {spec.agent}] Reusing existing extend_trajectories run_id={existing_ext['run_id']}")
        return

    slice_ds_name = f"duck_tape_{spec.agent}_slice_{spec.extend_slice_rows}"
    try:
        existing_ds = _find_reusable_remote_dataset(client, slice_ds_name)
        if existing_ds is not None:
            slice_dataset_id = existing_ds.dataset_id
        else:
            up_resp = client.datasets.upload_file(slice_path, name=slice_ds_name)
            slice_dataset_id = up_resp.dataset_id

        def _record_source(m: dict[str, Any]) -> None:
            m.setdefault("trajectory_extensions", {})[spec.agent] = {
                "source_dataset_id": slice_dataset_id,
                "source_dataset_name": slice_ds_name,
                "slice_rows": spec.extend_slice_rows,
                "status": "uploaded_awaiting_estimate",
                "updated_at": utc_now_iso(),
            }

        update_manifest(_record_source)

        job_spec = {
            "max_rows": spec.extend_slice_rows,
            "idempotency_key": f"duck-tape-{spec.agent}-extend-{spec.extend_slice_rows}-v1",
        }
        col_map = {"chat": "chat"}
        est = client.datasets.extend_trajectories(
            slice_dataset_id, column_mapping=col_map, job_specification=job_spec, estimate=True
        )
        est_credits = float(getattr(est, "estimated_credits_consumed", 0) or 0)
        print(
            f"[Account {spec.account_index} | {spec.agent}] extend_trajectories({spec.extend_slice_rows} rows) "
            f"estimate={est_credits} credits, ~{getattr(est, 'estimated_minutes', '?')} min"
        )

        # Keep enough credit headroom on Account 2 for both implementer and test_writer AutoScientist runs.
        if est_credits > 400:
            def _record_skipped(m: dict[str, Any]) -> None:
                m.setdefault("trajectory_extensions", {})[spec.agent].update(
                    {
                        "status": "skipped_credit_preservation",
                        "estimated_credits": est_credits,
                        "updated_at": utc_now_iso(),
                    }
                )

            update_manifest(_record_skipped)
            print(
                f"[Account {spec.account_index} | {spec.agent}] Skipping live extend_trajectories execution "
                f"({est_credits} credits > 400 threshold) to preserve credits for SFT runs."
            )
            return

        ext_resp = client.datasets.extend_trajectories(
            slice_dataset_id, column_mapping=col_map, job_specification=job_spec, estimate=False
        )

        def _record_started(m: dict[str, Any]) -> None:
            m.setdefault("trajectory_extensions", {})[spec.agent].update(
                {
                    "extended_dataset_id": getattr(ext_resp, "dataset_id", None),
                    "run_id": getattr(ext_resp, "run_id", None),
                    "estimated_credits": est_credits,
                    "status": "running",
                    "updated_at": utc_now_iso(),
                }
            )

        update_manifest(_record_started)
        print(
            f"[Account {spec.account_index} | {spec.agent}] Started extend_trajectories "
            f"run_id={getattr(ext_resp, 'run_id', None)} -> dataset_id={getattr(ext_resp, 'dataset_id', None)}"
        )
    except Exception as exc:
        def _record_err(m: dict[str, Any]) -> None:
            m.setdefault("trajectory_extensions", {})[spec.agent] = {
                "status": "skipped_error",
                "error": str(exc),
                "updated_at": utc_now_iso(),
            }

        update_manifest(_record_err)
        print(
            f"[Account {spec.account_index} | {spec.agent}] Non-fatal extend_trajectories error "
            f"(continuing to main SFT launch): {exc}"
        )


def _maybe_register_security_custom_eval(client: Adaption, spec: AgentLaunchSpec, dataset_id: str) -> None:
    """Register security_reviewer_eval.jsonl rubric on Account 3 via client.custom_evals.create()."""
    manifest = load_manifest()
    existing_ce = manifest.get("custom_evals", {}).get(spec.agent, {})
    if existing_ce.get("custom_eval_id"):
        print(f"[Account {spec.account_index} | {spec.agent}] Reusing custom_eval_id={existing_ce['custom_eval_id']}")
        return

    rubric = _build_security_custom_eval_rubric(spec.eval_path)
    try:
        # Check if already registered remotely on this dataset
        existing_list = client.custom_evals.list(dataset_id)
        items = getattr(existing_list, "custom_evals", None) or getattr(existing_list, "data", None) or []
        for item in items:
            if getattr(item, "name", None) == "security_reviewer_eval":
                ce_obj = item
                break
        else:
            ce_obj = client.custom_evals.create(
                dataset_id,
                name="security_reviewer_eval",
                judge_prompt=rubric,
            )

        def _record_ce(m: dict[str, Any]) -> None:
            m.setdefault("custom_evals", {})[spec.agent] = {
                "custom_eval_id": getattr(ce_obj, "custom_eval_id", None),
                "dataset_id": dataset_id,
                "name": getattr(ce_obj, "name", "security_reviewer_eval"),
                "status": getattr(ce_obj, "status", "created"),
                "eval_file": str(spec.eval_path.relative_to(REPO_ROOT)).replace("\\", "/") if spec.eval_path else None,
                "updated_at": utc_now_iso(),
            }

        update_manifest(_record_ce)
        print(
            f"[Account {spec.account_index} | {spec.agent}] Registered custom_eval_id="
            f"{getattr(ce_obj, 'custom_eval_id', None)} status={getattr(ce_obj, 'status', None)}"
        )
    except Exception as exc:
        def _record_ce_err(m: dict[str, Any]) -> None:
            m.setdefault("custom_evals", {})[spec.agent] = {
                "dataset_id": dataset_id,
                "name": "security_reviewer_eval",
                "status": "skipped_or_unsupported",
                "error": str(exc),
                "eval_file": str(spec.eval_path.relative_to(REPO_ROOT)).replace("\\", "/") if spec.eval_path else None,
                "updated_at": utc_now_iso(),
            }

        update_manifest(_record_ce_err)
        print(
            f"[Account {spec.account_index} | {spec.agent}] Non-fatal custom_evals.create note "
            f"(continuing to DPO launch): {exc}"
        )


def _execute_single_agent(
    client: Adaption,
    spec: AgentLaunchSpec,
    available_models: set[str],
) -> dict[str, Any]:
    """Upload dataset (or adopt Web UI dataset/run) and launch AutoScientist idempotently."""
    base_rows = count_jsonl_rows(spec.dataset_path)
    if base_rows == 0:
        raise FileNotFoundError(f"Missing or empty dataset file: {spec.dataset_path}")

    # 1. Check local manifest for an already-created non-failed run
    manifest = load_manifest()
    for existing in manifest.get("runs", []):
        if existing.get("idempotency_key") == spec.idempotency_key and existing.get("run_id"):
            if existing.get("status") not in FAILURE_STATUSES:
                live = client.autoscientist.get(existing["run_id"])
                if getattr(live, "status", None) not in FAILURE_STATUSES:
                    existing_ds_id = getattr(live, "dataset_id", None) or existing.get("dataset_id")
                    if spec.register_custom_eval and existing_ds_id:
                        _maybe_register_security_custom_eval(client, spec, existing_ds_id)
                    record = _run_to_dict(
                        live,
                        spec=spec,
                        base_rows=existing.get("base_rows", base_rows),
                        aug_rows=existing.get(
                            "augmentation_domain_rows",
                            compute_augmentation_rows(base_rows, spec.target_effective_rows),
                        ),
                        adopted_from_web_ui=bool(existing.get("adopted_from_web_ui")),
                        notes="Reused idempotent run from manifest",
                    )
                    update_manifest(lambda m: _upsert_run_record(m, record))
                    print(
                        f"[Account {spec.account_index} | {spec.agent}] Reusing existing run_id={live.id} "
                        f"status={live.status}"
                    )
                    return record

    # 2. Enforce account concurrency cap (5 active jobs)
    remote_runs = list(client.autoscientist.list(limit=50))
    active_runs = [r for r in remote_runs if getattr(r, "status", None) not in TERMINAL_STATUSES]

    # 3. Account 1 Router Web UI auto-discovery first
    dataset_id: str | None = None
    adopted_web_ui = False
    notes: str | None = None
    effective_base_rows = base_rows

    if spec.discover_web_ui:
        discovery = _discover_account1_router(client)
        web_ui_ds = discovery.get("web_ui_dataset")
        if web_ui_ds:
            def _save_web_ui_discovery(m: dict[str, Any]) -> None:
                m.setdefault("datasets", {})[f"{spec.agent}_web_ui"] = {
                    "dataset_id": web_ui_ds.get("dataset_id"),
                    "name": web_ui_ds.get("name"),
                    "kind": web_ui_ds.get("kind"),
                    "status": web_ui_ds.get("status"),
                    "row_count": web_ui_ds.get("row_count"),
                    "discovered_at": utc_now_iso(),
                }

            update_manifest(_save_web_ui_discovery)

        matching_run = discovery.get("matching_run_obj")
        if matching_run is not None:
            adopted_web_ui = bool(
                web_ui_ds and getattr(matching_run, "dataset_id", None) == web_ui_ds.get("dataset_id")
            )
            ds_rows = (
                int(web_ui_ds["row_count"])
                if adopted_web_ui and web_ui_ds and web_ui_ds.get("row_count")
                else base_rows
            )
            aug_rows = compute_augmentation_rows(ds_rows, spec.target_effective_rows)
            record = _run_to_dict(
                matching_run,
                spec=spec,
                base_rows=ds_rows,
                aug_rows=aug_rows,
                adopted_from_web_ui=adopted_web_ui,
                notes="Adopted existing Account 1 router run",
            )
            update_manifest(lambda m: (_upsert_run_record(m, record), m.__setitem__("training_started", True)))
            print(
                f"[Account 1 | router] Adopted existing run_id={matching_run.id} "
                f"dataset_id={matching_run.dataset_id} status={matching_run.status}"
            )
            return record

        if web_ui_ds and web_ui_ds.get("status") == "succeeded":
            dataset_id = web_ui_ds["dataset_id"]
            if web_ui_ds.get("row_count"):
                effective_base_rows = int(web_ui_ds["row_count"])
            adopted_web_ui = True
            notes = f"Launched on Web UI dataset {web_ui_ds.get('name')} ({dataset_id})"
            print(
                f"[Account 1 | router] Adopting ready Web UI dataset {dataset_id} "
                f"({web_ui_ds.get('name')}, rows={effective_base_rows})"
            )
        elif web_ui_ds:
            notes = (
                f"Web UI dataset {web_ui_ds.get('dataset_id')} ({web_ui_ds.get('name')}) is in "
                f"status={web_ui_ds.get('status')!r}; falling back to local router.jsonl raw upload"
            )
            print(f"[Account 1 | router] {notes}")

    if len(active_runs) >= MAX_CONCURRENT_JOBS_PER_ACCOUNT:
        raise RuntimeError(
            f"Account {spec.account_index} already has {len(active_runs)} active jobs "
            f"(cap={MAX_CONCURRENT_JOBS_PER_ACCOUNT}); deferring {spec.agent}."
        )

    # 4. Upload dataset in raw mode if not adopted from Web UI
    if dataset_id is None:
        manifest = load_manifest()
        cached_ds = manifest.get("datasets", {}).get(spec.agent, {})
        if cached_ds.get("dataset_id") and cached_ds.get("status") == "succeeded":
            try:
                ds_obj = client.datasets.get(cached_ds["dataset_id"])
                if getattr(ds_obj, "status", None) == "succeeded":
                    dataset_id = ds_obj.dataset_id
                    if getattr(ds_obj, "row_count", None):
                        effective_base_rows = int(ds_obj.row_count)
                    print(f"[Account {spec.account_index} | {spec.agent}] Reusing dataset_id={dataset_id}")
            except APIError:
                dataset_id = None

    if dataset_id is None:
        upload_path = spec.dataset_path
        upload_col_map = dict(spec.column_mapping)

        if spec.is_chat_source:
            slice_path, raw_sft_path = _prepare_implementer_artifacts(
                spec.dataset_path, slice_rows=spec.extend_slice_rows or 500
            )
            if spec.extend_slice_rows > 0:
                _maybe_run_implementer_trajectory_extension(client, spec, slice_path)
            upload_path = raw_sft_path

        try:
            dataset_id, ds_detail = _upload_and_wait_raw(
                client,
                file_path=upload_path,
                dataset_name=spec.dataset_name,
                column_mapping=upload_col_map,
                processing_mode=spec.processing_mode,
            )
        except (APIStatusError, RuntimeError) as exc:
            # Handle DPO completion alias requirement or multimodal context column fallback
            if spec.training_method == "alignment":
                print(
                    f"[Account {spec.account_index} | {spec.agent}] Retrying raw DPO upload with "
                    f"completion=chosen fallback after: {exc}"
                )
                dpo_fallback_path = _prepare_dpo_with_completion_fallback(spec.dataset_path, spec.agent)
                upload_col_map = {
                    "prompt": "prompt",
                    "completion": "completion",
                    "chosen": "chosen",
                    "rejected": "rejected",
                }
                dataset_id, ds_detail = _upload_and_wait_raw(
                    client,
                    file_path=dpo_fallback_path,
                    dataset_name=f"{spec.dataset_name}_v2",
                    column_mapping=upload_col_map,
                    processing_mode=spec.processing_mode,
                )
            elif "context" in upload_col_map:
                print(
                    f"[Account {spec.account_index} | {spec.agent}] Retrying raw upload without context "
                    f"column mapping after: {exc}"
                )
                upload_col_map = {"prompt": "prompt", "completion": "completion"}
                dataset_id, ds_detail = _upload_and_wait_raw(
                    client,
                    file_path=upload_path,
                    dataset_name=f"{spec.dataset_name}_v2",
                    column_mapping=upload_col_map,
                    processing_mode=spec.processing_mode,
                )
            else:
                raise

        if getattr(ds_detail, "row_count", None):
            effective_base_rows = int(ds_detail.row_count)

        def _save_dataset(m: dict[str, Any]) -> None:
            m.setdefault("datasets", {})[spec.agent] = {
                "dataset_id": dataset_id,
                "name": getattr(ds_detail, "name", spec.dataset_name),
                "status": getattr(ds_detail, "status", "succeeded"),
                "row_count": effective_base_rows,
                "processing_mode": spec.processing_mode,
                "column_mapping": upload_col_map,
                "source_file": str(spec.dataset_path.relative_to(REPO_ROOT)).replace("\\", "/"),
                "uploaded_at": utc_now_iso(),
            }

        update_manifest(_save_dataset)
        print(
            f"[Account {spec.account_index} | {spec.agent}] Uploaded raw dataset_id={dataset_id} "
            f"(rows={effective_base_rows})"
        )

    # 5. Check remote runs on this dataset_id before launching
    for r in client.autoscientist.list(dataset_id=dataset_id, limit=20):
        if getattr(r, "status", None) not in FAILURE_STATUSES:
            aug_rows = compute_augmentation_rows(effective_base_rows, spec.target_effective_rows)
            record = _run_to_dict(
                r,
                spec=spec,
                base_rows=effective_base_rows,
                aug_rows=aug_rows,
                adopted_from_web_ui=adopted_web_ui,
                notes="Adopted existing remote run for dataset_id",
            )
            update_manifest(lambda m: (_upsert_run_record(m, record), m.__setitem__("training_started", True)))
            print(f"[Account {spec.account_index} | {spec.agent}] Adopted remote run_id={r.id} status={r.status}")
            return record

    # 6. Register custom eval on Security Reviewer DPO dataset
    if spec.register_custom_eval:
        _maybe_register_security_custom_eval(client, spec, dataset_id)

    # 7. Launch AutoScientist run with model & effective-row fallbacks if required by API
    chosen_model = spec.model if spec.model in available_models else (spec.fallback_model or spec.model)
    aug_rows = compute_augmentation_rows(effective_base_rows, spec.target_effective_rows)

    def _create_run(model_slug: str, domain_aug: int) -> Any:
        kwargs: dict[str, Any] = {
            "dataset_id": dataset_id,
            "model": model_slug,
            "training_method": spec.training_method,
            "data_format": spec.data_format,
            "augmentation_domain_rows": domain_aug,
            "idempotency_key": spec.idempotency_key,
        }
        return client.autoscientist.create(**kwargs)

    try:
        run = _create_run(chosen_model, aug_rows)
    except APIStatusError as exc:
        # Handle VLM effective row minimum or fallback model
        if spec.agent == "frontend_ui" and aug_rows == 0:
            aug_rows = compute_augmentation_rows(effective_base_rows, 10000)
            try:
                run = _create_run(chosen_model, aug_rows)
            except APIStatusError:
                if spec.fallback_model and spec.fallback_model in available_models:
                    chosen_model = spec.fallback_model
                    aug_rows = 0
                    run = _create_run(chosen_model, aug_rows)
                else:
                    raise
        elif spec.fallback_model and spec.fallback_model in available_models and chosen_model != spec.fallback_model:
            print(
                f"[Account {spec.account_index} | {spec.agent}] Falling back from {chosen_model} "
                f"to {spec.fallback_model} after: {exc}"
            )
            chosen_model = spec.fallback_model
            run = _create_run(chosen_model, aug_rows)
        else:
            raise

    record = _run_to_dict(
        run,
        spec=spec,
        base_rows=effective_base_rows,
        aug_rows=aug_rows,
        adopted_from_web_ui=adopted_web_ui,
        notes=notes,
    )
    update_manifest(lambda m: (_upsert_run_record(m, record), m.__setitem__("training_started", True)))
    print(
        f"[Account {spec.account_index} | {spec.agent}] Launched run_id={run.id} "
        f"model={run.model} method={run.training_method} aug_domain={aug_rows} status={run.status}"
    )
    return record


def _select_specs(wave: Literal["priority", "all"]) -> list[AgentLaunchSpec]:
    """Filter AGENT_SPECS by requested wave."""
    if wave == "priority":
        return [s for s in AGENT_SPECS if s.wave == "priority"]
    return list(AGENT_SPECS)


def run_dry_run(wave: Literal["priority", "all"]) -> int:
    """Validate keys, models, local row counts, Account 1 Web UI state, and print plan without spending credits."""
    keys = load_account_keys()
    specs = _select_specs(wave)

    print("=== Adaption Multi-Account Dry Run ===")
    clients: dict[int, Adaption] = {}
    account_summaries: dict[int, dict[str, Any]] = {}
    for idx in sorted(keys):
        client = make_adaption_client(keys[idx])
        clients[idx] = client
        ds_list = list(client.datasets.list(limit=50))
        runs_list = list(client.autoscientist.list(limit=50))
        active = [r for r in runs_list if getattr(r, "status", None) not in TERMINAL_STATUSES]
        account_summaries[idx] = {
            "datasets": len(ds_list),
            "runs": len(runs_list),
            "active_runs": len(active),
        }
        print(
            f"  Account {idx}: AUTH OK | datasets={len(ds_list)} | runs={len(runs_list)} "
            f"| active={len(active)}/{MAX_CONCURRENT_JOBS_PER_ACCOUNT}"
        )

    models_resp = clients[1].autoscientist.list_models()
    available_models = {m.id: m for m in models_resp.models}
    print(f"\nAvailable AutoScientist models ({len(available_models)}):")
    for model_id in sorted(available_models):
        m = available_models[model_id]
        print(f"  - {model_id} (methods={m.methods}, training_types={m.training_types})")

    acc1_router = _discover_account1_router(clients[1])
    web_ui_ds = acc1_router.get("web_ui_dataset")
    print("\nAccount 1 Router Web UI Discovery:")
    if web_ui_ds:
        print(
            f"  Found Web UI dataset: id={web_ui_ds.get('dataset_id')} name={web_ui_ds.get('name')!r} "
            f"status={web_ui_ds.get('status')!r} row_count={web_ui_ds.get('row_count')}"
        )
    else:
        print("  No Web UI router dataset found; will upload router.jsonl.")
    if acc1_router.get("matching_run"):
        mr = acc1_router["matching_run"]
        print(f"  Found existing router run: id={mr.get('id')} status={mr.get('status')!r}")
    else:
        print("  No existing AutoScientist run for router yet.")

    missing_files: list[str] = []
    missing_models: list[str] = []
    per_account_credits: dict[int, float] = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}
    per_account_jobs: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0}

    print(
        "\n"
        f"{'Acct':<4} | {'Wave':<8} | {'Agent':<21} | {'Base':>6} | {'AugDom':>6} | {'Eff':>6} | "
        f"{'Mode':<4} | {'Method':<11} | {'Model':<26} | {'EstCr':>5}"
    )
    print("-" * 126)

    for spec in specs:
        base_rows = count_jsonl_rows(spec.dataset_path)
        if base_rows == 0:
            missing_files.append(str(spec.dataset_path))
        if spec.eval_path and not spec.eval_path.is_file():
            missing_files.append(str(spec.eval_path))

        model_ok = spec.model in available_models or (spec.fallback_model in available_models)
        if not model_ok:
            missing_models.append(spec.model)

        aug_rows = compute_augmentation_rows(base_rows, spec.target_effective_rows)
        eff_rows = base_rows + aug_rows
        est_cr = estimate_raw_credits(aug_rows, extend_rows=spec.extend_slice_rows)
        per_account_credits[spec.account_index] += est_cr
        per_account_jobs[spec.account_index] += 1

        print(
            f"{spec.account_index:<4} | {spec.wave:<8} | {spec.agent:<21} | {base_rows:>6} | {aug_rows:>6} | "
            f"{eff_rows:>6} | {spec.processing_mode:<4} | {spec.training_method:<11} | {spec.model:<26} | "
            f"{est_cr:>5.0f}"
        )

    print("\nAccount Allocation & Credit Summary (Budget: 1,250 credits & max 5 concurrent jobs/account):")
    for idx in range(1, 5):
        planned = per_account_jobs[idx]
        active = account_summaries[idx]["active_runs"]
        est_aug_cr = per_account_credits[idx]
        print(
            f"  Account {idx}: planned_jobs={planned}, active_remote={active}, "
            f"total_concurrency={planned + active}/{MAX_CONCURRENT_JOBS_PER_ACCOUNT}, "
            f"est_aug_extend_credits={est_aug_cr:.0f} (raw_upload=0; remaining headroom for AutoScientist loops="
            f"~{max(0.0, CREDITS_PER_ACCOUNT - est_aug_cr):.0f}/{CREDITS_PER_ACCOUNT})"
        )

    if missing_files:
        print(f"\nERROR: Missing dataset/eval files: {missing_files}", file=sys.stderr)
        return 1
    if missing_models:
        print(f"\nERROR: Unavailable model slugs: {missing_models}", file=sys.stderr)
        return 1

    print("\nDry run PASSED cleanly (0 credits spent).")
    return 0


def run_execute(wave: Literal["priority", "all"]) -> int:
    """Execute uploads and AutoScientist launches concurrently across Accounts 1-4."""
    keys = load_account_keys()
    specs = _select_specs(wave)

    clients = {idx: make_adaption_client(keys[idx]) for idx in sorted(keys)}
    available_models = {m.id for m in clients[1].autoscientist.list_models().models}

    update_manifest(
        lambda m: m.__setitem__(
            "accounts",
            {
                str(idx): {
                    "credit_budget": CREDITS_PER_ACCOUNT,
                    "concurrency_cap": MAX_CONCURRENT_JOBS_PER_ACCOUNT,
                    "authenticated": True,
                }
                for idx in sorted(keys)
            },
        )
    )

    by_account: dict[int, list[AgentLaunchSpec]] = {1: [], 2: [], 3: [], 4: []}
    for spec in specs:
        by_account[spec.account_index].append(spec)
    for idx in by_account:
        by_account[idx].sort(key=lambda s: s.priority_order)

    def _run_account_queue(account_index: int) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        client = clients[account_index]
        for spec in by_account[account_index]:
            res = _execute_single_agent(client, spec, available_models)
            results.append(res)
        return results

    failures: list[tuple[int, str]] = []
    launched: list[dict[str, Any]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        future_map = {
            pool.submit(_run_account_queue, idx): idx for idx in sorted(by_account) if by_account[idx]
        }
        for future in concurrent.futures.as_completed(future_map):
            acct_idx = future_map[future]
            try:
                launched.extend(future.result())
            except Exception as exc:
                failures.append((acct_idx, str(exc)))
                print(f"[Account {acct_idx}] ERROR: {exc}", file=sys.stderr)

    print(f"\nExecution summary: {len(launched)} runs registered/adopted, {len(failures)} account failures.")
    for item in sorted(launched, key=lambda r: (r["account_index"], r["agent"])):
        print(
            f"  Acct {item['account_index']} | {item['agent']:<21} | run_id={item['run_id']} | "
            f"status={item['status']} | model={item['model']} | eff_rows={item['effective_rows']}"
        )

    return 1 if failures else 0


def main() -> int:
    """CLI entry point for the multi-account Adaption parallel launcher."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Multi-account Adaption Labs AutoScientist training launcher for Duck Tape."
    )
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Verify API keys, models, Account 1 Web UI runs, row counts, and credit estimates without spending credits.",
    )
    mode_group.add_argument(
        "--execute",
        action="store_true",
        help="Upload datasets in raw mode and launch AutoScientist runs in parallel across Accounts 1-4.",
    )
    parser.add_argument(
        "--wave",
        choices=("priority", "all"),
        default="all",
        help="Launch priority Security + CI/Git demo agents or all 11 AutoScientist agents (default: all).",
    )
    args = parser.parse_args()

    if args.dry_run:
        return run_dry_run(args.wave)
    return run_execute(args.wave)


if __name__ == "__main__":
    raise SystemExit(main())
