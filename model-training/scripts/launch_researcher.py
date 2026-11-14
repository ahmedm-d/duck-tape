#!/usr/bin/env python3
"""Standalone single-account Adaption launcher for the Duck Tape researcher model.

The researcher is an instruction-tuned specialist: input = one code function plus its
<graph_context>, output = JSON vulnerability findings. This script is deliberately separate from
launch_adaption_parallel.py so it never touches the multi-account jobs that script manages.

Modes (default --dry-run, which sends nothing and needs no network):
    --dry-run   validate local data and print the exact launch plan
    --execute   upload (raw mode), wait for processing, create the AutoScientist run, record it
    --poll      refresh the recorded run from Adaption and update the manifest
    --download  with --poll, save the trained artifact once download_available is true

API key: ADAPTION_API_KEY_RESEARCHER, else ADAPTION_API_KEY_3, from the process environment or
model-training/.env.training (gitignored). The key value is never printed or written anywhere.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

try:  # Use the OS trust store when available (keeps TLS verification on; needed where antivirus re-signs HTTPS).
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

SCRIPT_DIR = Path(__file__).resolve().parent
MODEL_TRAINING_ROOT = SCRIPT_DIR.parent
REPO_ROOT = MODEL_TRAINING_ROOT.parent

sys.path.insert(0, str(MODEL_TRAINING_ROOT / "src"))
from training.researcher_intake import FindingsError, parse_findings  # noqa: E402
from training.schemas import InstructionRow  # noqa: E402

AGENT = "researcher"
DATASET_NAME = "duck_tape_researcher"
DEFAULT_MODEL = "Qwen/Qwen3.6-35B-A3B"
DEFAULT_TARGET_ROWS = 10000
DEFAULT_IDEMPOTENCY_KEY = "duck-tape-researcher-v1"
# Same values A's launcher uses for prompt/completion SFT agents; both confirmed against SDK 0.19.0
# (autoscientist.create: training_method in {instruction, alignment}, data_format in {chat, instruction}).
TRAINING_METHOD = "instruction"
DATA_FORMAT = "instruction"
PROCESSING_MODE = "raw"
COLUMN_MAPPING: dict[str, Any] = {"prompt": "prompt", "completion": "completion"}
KEY_ENV_VARS = ("ADAPTION_API_KEY_RESEARCHER", "ADAPTION_API_KEY_3")
# Account 3 is shared with A's DPO jobs; a dedicated RESEARCHER key gets a distinct integer id so
# A's poll_adaption_runs.py (which int()s account_index) neither crashes nor polls it with the wrong key.
ACCOUNT_INDEX_BY_VAR = {"ADAPTION_API_KEY_3": 3, "ADAPTION_API_KEY_RESEARCHER": 5}
ACCOUNT_ROLE = "Researcher (vulnerability findings)"
MAX_CONCURRENT_JOBS_PER_ACCOUNT = 5
ACTIVE_STATUSES = frozenset({"pending", "running"})
FAILURE_STATUSES = frozenset({"failed", "cancelled"})
CUSTOM_EVAL_NAME = "researcher_findings_eval"

PROCESSED_TRAIN = MODEL_TRAINING_ROOT / "data" / "processed" / "researcher.jsonl"
EVAL_FILE = MODEL_TRAINING_ROOT / "data" / "eval" / "researcher_eval.jsonl"
MANIFEST_PATH = MODEL_TRAINING_ROOT / "data" / "adaption_runs_manifest.json"
DEFAULT_DOWNLOAD_DIR = MODEL_TRAINING_ROOT / "models" / "checkpoints"  # gitignored

CUSTOM_EVAL_RUBRIC = (
    "Score a Duck Tape researcher response on a 0 to 10 whole-number scale. The input is one code "
    "function with a <graph_context> block; the response must be a JSON object "
    '{"findings": [...]}. Score 10 only when ALL hold: (1) the response is valid JSON with exactly '
    'one top-level key "findings" and no prose or markdown; (2) when the function is already fixed '
    "or safe, findings is an empty list; (3) every finding names the correct CWE and a reference "
    "path/start_line/end_line that really contains the vulnerable code; (4) no detail is invented: "
    "no file, function, call edge or reachability that is absent from the input, and sink_reachable "
    "null is treated as unknown, never as false. Deduct points proportionally per violated rule; "
    "an invalid JSON response scores 0."
)


class LaunchError(RuntimeError):
    """A launch problem with a secret-free, user-actionable message."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------- credentials

def resolve_api_key(
    environ: Mapping[str, str] | None = None,
    *,
    load_env: Callable[[], Any] | None = None,
) -> tuple[str, str]:
    """Return (variable_name, key). Only the variable name is ever safe to print."""
    if load_env is None:
        from training.utils import load_training_env as load_env  # lazy: pulls python-dotenv
    if environ is None:
        load_env()
        environ = os.environ
    for var in KEY_ENV_VARS:
        value = (environ.get(var) or "").strip()
        if value:
            return var, value
    raise LaunchError(
        "No Adaption API key found. Set ADAPTION_API_KEY_RESEARCHER (or ADAPTION_API_KEY_3) in the "
        "environment or in model-training/.env.training."
    )


def scrub(text: str, key: str | None) -> str:
    """Remove the API key from any text before it can reach a terminal or a manifest."""
    return text.replace(key, "***") if key else text


# --------------------------------------------------------------------------- data validation

def count_jsonl_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def validate_jsonl(path: Path, *, max_errors: int = 5) -> tuple[int, list[str]]:
    """Validate every row as InstructionRow with a findings-JSON completion; return (rows, errors)."""
    if not path.is_file():
        return 0, [f"missing file: {path}"]
    rows, errors = 0, []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            rows += 1
            if len(errors) >= max_errors:
                continue
            try:
                row = InstructionRow.model_validate_json(line)
                parse_findings(row.completion)
            except (ValueError, FindingsError) as exc:
                first = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
                errors.append(f"{path.name}:{line_no}: {first}")
    if rows == 0:
        errors.append(f"{path.name} has no rows")
    return rows, errors


def compute_augmentation_rows(train_rows: int, target_rows: int) -> int:
    return max(0, target_rows - train_rows)


def build_plan(
    *,
    train_file: Path,
    eval_file: Path,
    model: str,
    target_rows: int,
    idempotency_key: str,
) -> dict[str, Any]:
    train_rows, train_errors = validate_jsonl(train_file)
    eval_rows, eval_errors = validate_jsonl(eval_file)
    aug = compute_augmentation_rows(train_rows, target_rows)
    return {
        "dataset_name": DATASET_NAME,
        "train_file": train_file,
        "eval_file": eval_file,
        "train_rows": train_rows,
        "eval_rows": eval_rows,
        "errors": train_errors + eval_errors,
        "model": model,
        "training_method": TRAINING_METHOD,
        "data_format": DATA_FORMAT,
        "processing_mode": PROCESSING_MODE,
        "column_mapping": dict(COLUMN_MAPPING),
        "target_rows": target_rows,
        "augmentation_domain_rows": aug,
        "effective_rows": train_rows + aug,
        "idempotency_key": idempotency_key,
    }


def print_plan(plan: dict[str, Any], *, key_var: str | None, mode: str) -> None:
    print(f"=== Researcher launch plan ({mode}) ===")
    print(f"dataset name          : {plan['dataset_name']}")
    print(f"train rows            : {plan['train_rows']}  ({plan['train_file']})")
    print(f"eval rows             : {plan['eval_rows']}  ({plan['eval_file']})")
    print(f"model                 : {plan['model']}")
    print(f"training_method       : {plan['training_method']}")
    print(f"data_format           : {plan['data_format']}")
    print(f"processing_mode       : {plan['processing_mode']}  column_mapping={plan['column_mapping']}")
    print(f"target rows           : {plan['target_rows']}")
    print(f"augmentation_domain_rows: {plan['augmentation_domain_rows']}  (effective rows {plan['effective_rows']})")
    print(f"idempotency key       : {plan['idempotency_key']}")
    print(f"api key variable      : {key_var or 'NOT SET (required for --execute/--poll)'}")


# --------------------------------------------------------------------------- manifest

def load_manifest(path: Path) -> dict[str, Any]:
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for name in ("runs",):
                data.setdefault(name, [])
            for name in ("datasets", "custom_evals", "trajectory_extensions", "accounts"):
                data.setdefault(name, {})
            return data
    return {"schema_version": "2.0", "runs": [], "datasets": {}, "custom_evals": {}, "accounts": {}}


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = utc_now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def update_manifest(path: Path, mutator: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Read-modify-write immediately so other agents' entries written meanwhile are preserved."""
    manifest = load_manifest(path)
    mutator(manifest)
    save_manifest(path, manifest)
    return manifest


def find_run_entry(manifest: dict[str, Any], idempotency_key: str | None = None) -> dict[str, Any] | None:
    for entry in manifest.get("runs", []):
        if entry.get("agent") == AGENT or (idempotency_key and entry.get("idempotency_key") == idempotency_key):
            return entry
    return None


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def run_fields(run: Any, fallback: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Live fields of an AutoscientistRun, shaped like A's runs[] entries."""
    fallback = fallback or {}
    best_hp = getattr(run, "best_hyperparams", None)
    if best_hp is not None and hasattr(best_hp, "model_dump"):
        best_hp = best_hp.model_dump()
    return {
        "run_id": getattr(run, "id", None) or fallback.get("run_id"),
        "dataset_id": getattr(run, "dataset_id", None) or fallback.get("dataset_id"),
        "model": getattr(run, "model", None) or fallback.get("model"),
        "training_method": getattr(run, "training_method", None) or fallback.get("training_method"),
        "status": getattr(run, "status", None) or fallback.get("status", "unknown"),
        "iterations_completed": getattr(run, "iterations_completed", fallback.get("iterations_completed", 0)),
        "max_iterations": getattr(run, "max_iterations", fallback.get("max_iterations")),
        "target_win_rate": getattr(run, "target_win_rate", fallback.get("target_win_rate")),
        "best_win_rate": getattr(run, "best_win_rate", fallback.get("best_win_rate")),
        "download_available": bool(getattr(run, "download_available", False)),
        "error": getattr(run, "error", None),
        "best_hyperparams": best_hp,
        "created_at": _iso(getattr(run, "created_at", fallback.get("created_at"))),
        "completed_at": _iso(getattr(run, "completed_at", None)),
        "last_checked_at": utc_now_iso(),
    }


def record_run(
    path: Path,
    run: Any,
    plan: dict[str, Any],
    *,
    key_var: str,
    base_rows: int,
    aug_rows: int,
    notes: str | None,
) -> dict[str, Any]:
    entry = {
        "agent": AGENT,
        "account_index": ACCOUNT_INDEX_BY_VAR.get(key_var, 5),
        "account_label": "researcher" if key_var == "ADAPTION_API_KEY_RESEARCHER" else f"account_{ACCOUNT_INDEX_BY_VAR.get(key_var)}",
        "account_role": ACCOUNT_ROLE,
        "wave": "all",
        "idempotency_key": plan["idempotency_key"],
        **run_fields(run, {"model": plan["model"], "training_method": plan["training_method"]}),
        "base_rows": base_rows,
        "augmentation_domain_rows": aug_rows,
        "effective_rows": base_rows + aug_rows,
        "processing_mode": plan["processing_mode"],
        "adopted_from_web_ui": False,
        "notes": notes,
    }

    def mutate(manifest: dict[str, Any]) -> None:
        runs = manifest.setdefault("runs", [])
        for idx, existing in enumerate(runs):
            if existing.get("agent") == AGENT or existing.get("idempotency_key") == entry["idempotency_key"]:
                runs[idx] = {**existing, **entry}
                break
        else:
            runs.append(entry)
        manifest["training_started"] = True

    update_manifest(path, mutate)
    return entry


# --------------------------------------------------------------------------- client + errors

def make_client(api_key: str) -> Any:
    from adaption import Adaption  # lazy: dry-run never needs the SDK

    return Adaption(api_key=api_key, timeout=180.0, max_retries=3)


def describe_api_error(exc: Exception, key_var: str, key: str | None = None) -> str:
    """Translate SDK exceptions into secret-free, actionable messages."""
    try:
        import adaption
    except ImportError:  # pragma: no cover
        return scrub(str(exc), key)
    status = getattr(exc, "status_code", None)
    text = scrub(str(exc), key)
    low = text.lower()
    if isinstance(exc, adaption.AuthenticationError) or status == 401:
        return f"Authentication failed (HTTP 401): the key in {key_var} was rejected. Check it is a current Adaption key."
    if isinstance(exc, adaption.PermissionDeniedError) or status == 403:
        return f"Permission denied (HTTP 403) for the key in {key_var}: {text}"
    if status == 402 or "insufficient credit" in low or "not enough credit" in low:
        return f"Insufficient Adaption credits for the account behind {key_var}: {text}"
    if isinstance(exc, adaption.RateLimitError) or status == 429 or "concurrent" in low or "max active" in low or "job limit" in low:
        return f"Adaption job cap or rate limit reached for {key_var} (max {MAX_CONCURRENT_JOBS_PER_ACCOUNT} concurrent runs); wait for a run to finish and retry: {text}"
    if isinstance(exc, adaption.APIConnectionError):
        return f"Could not reach Adaption ({type(exc).__name__}). Check the network or TLS interception, then retry: {text}"
    if isinstance(exc, adaption.APIStatusError):
        return f"Adaption API error (HTTP {status}): {text}"
    return text


# --------------------------------------------------------------------------- execute

def _find_remote_dataset(client: Any, name: str) -> Any | None:
    for ds in client.datasets.list(limit=50):
        if (getattr(ds, "name", None) or "") == name and getattr(ds, "status", None) != "failed":
            return ds
    return None


def _wait_dataset(client: Any, dataset_id: str) -> Any:
    status = client.datasets.wait_for_completion(
        dataset_id, initial_interval=2.0, max_interval=15.0, backoff_factor=1.5, timeout=600.0
    )
    detail = client.datasets.get(dataset_id)
    if getattr(status, "status", None) != "succeeded":
        raise LaunchError(
            f"Dataset {dataset_id} ended in status={getattr(status, 'status', None)!r}: "
            f"{getattr(detail, 'error_data', None)}"
        )
    return detail


def ensure_dataset(client: Any, plan: dict[str, Any], manifest_path: Path) -> tuple[str, int]:
    """Reuse a recorded or same-named remote dataset, else upload raw. Returns (dataset_id, rows)."""
    cached = load_manifest(manifest_path).get("datasets", {}).get(AGENT, {})
    if cached.get("dataset_id") and cached.get("status") == "succeeded":
        ds = client.datasets.get(cached["dataset_id"])
        if getattr(ds, "status", None) == "succeeded":
            print(f"Reusing recorded dataset {ds.dataset_id}")
            return ds.dataset_id, int(getattr(ds, "row_count", None) or plan["train_rows"])

    existing = _find_remote_dataset(client, plan["dataset_name"])
    if existing is not None:
        detail = existing
        if getattr(existing, "status", None) != "succeeded":
            detail = _wait_dataset(client, existing.dataset_id)
        print(f"Reusing remote dataset {existing.dataset_id} named {plan['dataset_name']}")
        dataset_id = existing.dataset_id
    else:
        resp = client.datasets.upload_file(
            plan["train_file"],
            name=plan["dataset_name"],
            processing_mode=plan["processing_mode"],
            column_mapping=plan["column_mapping"],
        )
        dataset_id = resp.dataset_id
        detail = _wait_dataset(client, dataset_id)
        print(f"Uploaded raw dataset {dataset_id}")

    rows = int(getattr(detail, "row_count", None) or plan["train_rows"])

    def mutate(manifest: dict[str, Any]) -> None:
        manifest.setdefault("datasets", {})[AGENT] = {
            "dataset_id": dataset_id,
            "name": getattr(detail, "name", None) or plan["dataset_name"],
            "status": getattr(detail, "status", "succeeded"),
            "row_count": rows,
            "processing_mode": plan["processing_mode"],
            "column_mapping": plan["column_mapping"],
            "source_file": _rel(plan["train_file"]),
            "uploaded_at": utc_now_iso(),
        }

    update_manifest(manifest_path, mutate)
    return dataset_id, rows


def _rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def check_model(client: Any, model: str) -> None:
    """Fail if the model is definitely unavailable; warn (not fail) if listing itself errors."""
    try:
        models = {m.id: m for m in client.autoscientist.list_models().models}
    except Exception as exc:  # listing is advisory; create() is the real gate
        print(f"WARNING: could not list AutoScientist models ({type(exc).__name__}); skipping model check.")
        return
    if model not in models:
        raise LaunchError(
            f"Model {model!r} is not offered by AutoScientist for this account. Available: {sorted(models)}. "
            "Re-run with --model <id>."
        )
    methods = getattr(models[model], "methods", None) or []
    if methods and "sft" not in methods:
        raise LaunchError(f"Model {model!r} does not list SFT support (methods={methods}).")


def maybe_register_custom_eval(client: Any, dataset_id: str, manifest_path: Path) -> None:
    """Opt-in, non-fatal: the SDK scores custom rubrics against adapted datasets, so on a raw upload it may be a no-op."""
    existing = load_manifest(manifest_path).get("custom_evals", {}).get(AGENT, {})
    if existing.get("custom_eval_id"):
        print(f"Reusing custom_eval_id={existing['custom_eval_id']}")
        return
    try:
        evals = client.datasets.custom_evals
        listing = evals.list(dataset_id)
        ce = next((i for i in (getattr(listing, "items", None) or []) if getattr(i, "name", None) == CUSTOM_EVAL_NAME), None)
        if ce is None:
            ce = evals.create(dataset_id, name=CUSTOM_EVAL_NAME, judge_prompt=CUSTOM_EVAL_RUBRIC)
        record = {
            "custom_eval_id": getattr(ce, "custom_eval_id", None),
            "dataset_id": dataset_id,
            "name": CUSTOM_EVAL_NAME,
            "status": getattr(ce, "status", "created"),
            "eval_file": _rel(EVAL_FILE),
            "updated_at": utc_now_iso(),
        }
    except Exception as exc:
        record = {
            "dataset_id": dataset_id,
            "name": CUSTOM_EVAL_NAME,
            "status": "skipped_or_unsupported",
            "error": type(exc).__name__ + ": " + str(exc)[:300],
            "updated_at": utc_now_iso(),
        }
        print(f"Non-fatal custom eval note: {record['error']}")
    update_manifest(manifest_path, lambda m: m.setdefault("custom_evals", {}).__setitem__(AGENT, record))


def execute(
    client: Any,
    plan: dict[str, Any],
    *,
    key_var: str,
    manifest_path: Path = MANIFEST_PATH,
    custom_eval: bool = False,
) -> dict[str, Any]:
    """Upload and launch idempotently. Returns the researcher manifest entry."""
    if plan["errors"]:
        raise LaunchError("Local data failed validation; nothing was sent:\n  " + "\n  ".join(plan["errors"]))

    # 1. Idempotency: a recorded non-failed run is refreshed, never duplicated.
    entry = find_run_entry(load_manifest(manifest_path), plan["idempotency_key"])
    if entry and entry.get("run_id"):
        live = client.autoscientist.get(entry["run_id"])
        if getattr(live, "status", None) in FAILURE_STATUSES:
            raise LaunchError(
                f"Recorded researcher run {entry['run_id']} is {live.status!r} (error={getattr(live, 'error', None)}). "
                "To relaunch, remove the researcher entry from the manifest and pass a new --idempotency-key."
            )
        entry = record_run(
            manifest_path, live, plan, key_var=key_var,
            base_rows=entry.get("base_rows", plan["train_rows"]),
            aug_rows=entry.get("augmentation_domain_rows", plan["augmentation_domain_rows"]),
            notes="Reused idempotent run from manifest",
        )
        print(f"Researcher run already recorded: run_id={entry['run_id']} status={entry['status']} (no new run created)")
        return entry

    # 2. Concurrency cap and model check before any upload.
    active = [r for r in client.autoscientist.list(limit=50) if getattr(r, "status", None) in ACTIVE_STATUSES]
    if len(active) >= MAX_CONCURRENT_JOBS_PER_ACCOUNT:
        raise LaunchError(
            f"The account behind {key_var} already has {len(active)} active runs "
            f"(cap {MAX_CONCURRENT_JOBS_PER_ACCOUNT}). Use ADAPTION_API_KEY_RESEARCHER with a free account or wait."
        )
    check_model(client, plan["model"])

    # 3. Dataset (raw upload preserves <graph_context> and JSON completions verbatim).
    dataset_id, base_rows = ensure_dataset(client, plan, manifest_path)
    aug_rows = compute_augmentation_rows(base_rows, plan["target_rows"])

    if custom_eval:
        maybe_register_custom_eval(client, dataset_id, manifest_path)

    # 4. Adopt an existing live run on this dataset rather than starting a second one.
    adopted = next(
        (r for r in client.autoscientist.list(dataset_id=dataset_id, limit=20) if getattr(r, "status", None) not in FAILURE_STATUSES),
        None,
    )
    if adopted is not None:
        entry = record_run(manifest_path, adopted, plan, key_var=key_var, base_rows=base_rows, aug_rows=aug_rows,
                           notes="Adopted existing remote run for dataset_id")
        print(f"Adopted remote run_id={entry['run_id']} status={entry['status']}")
        return entry

    # 5. Create the AutoScientist run.
    run = client.autoscientist.create(
        dataset_id=dataset_id,
        model=plan["model"],
        training_method=plan["training_method"],
        data_format=plan["data_format"],
        augmentation_domain_rows=aug_rows,
        idempotency_key=plan["idempotency_key"],
    )
    entry = record_run(manifest_path, run, plan, key_var=key_var, base_rows=base_rows, aug_rows=aug_rows, notes=None)
    print(
        f"Launched researcher run_id={entry['run_id']} model={entry['model']} "
        f"method={entry['training_method']} aug_domain={aug_rows} status={entry['status']}"
    )
    return entry


# --------------------------------------------------------------------------- poll

def poll(
    client: Any,
    *,
    manifest_path: Path = MANIFEST_PATH,
    download_dir: Path | None = None,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    entry = find_run_entry(manifest)
    if not entry or not entry.get("run_id"):
        raise LaunchError("No researcher run is recorded in the manifest. Run --execute first.")
    live = client.autoscientist.get(entry["run_id"])
    fields = run_fields(live, entry)
    update_manifest(
        manifest_path,
        lambda m: [r.update(fields) for r in m.get("runs", []) if r.get("agent") == AGENT],
    )
    updated = {**entry, **fields}
    best = updated.get("best_win_rate")
    print(
        f"researcher run_id={updated['run_id']} status={updated['status']} "
        f"iterations={updated.get('iterations_completed')}/{updated.get('max_iterations')} "
        f"best_win_rate={'-' if best is None else format(best, '.3f')} "
        f"download_available={updated['download_available']}"
    )
    if updated.get("error"):
        print(f"run error: {updated['error']}")
    if download_dir is not None:
        if not updated["download_available"]:
            print("Download not available yet; nothing saved.")
        else:
            download_dir.mkdir(parents=True, exist_ok=True)
            target = download_dir / f"researcher_{updated['run_id']}.gz"
            client.autoscientist.download(updated["run_id"]).write_to_file(target)
            print(f"Saved trained artifact to {target} (do not commit model weights).")
            update_manifest(
                manifest_path,
                lambda m: [r.update(artifact_downloaded_at=utc_now_iso()) for r in m.get("runs", []) if r.get("agent") == AGENT],
            )
    return updated


# --------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None, *, client_factory: Callable[[str], Any] = make_client) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Launch the Duck Tape researcher model on Adaption AutoScientist.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Validate data and print the plan; sends nothing (default).")
    mode.add_argument("--execute", action="store_true", help="Upload, create the AutoScientist run, record it.")
    mode.add_argument("--poll", action="store_true", help="Refresh the recorded run from Adaption.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--target-rows", type=int, default=DEFAULT_TARGET_ROWS)
    parser.add_argument("--idempotency-key", default=DEFAULT_IDEMPOTENCY_KEY)
    parser.add_argument("--train-file", type=Path, default=PROCESSED_TRAIN)
    parser.add_argument("--eval-file", type=Path, default=EVAL_FILE)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--custom-eval", action="store_true", help="With --execute: also register the findings rubric (non-fatal).")
    parser.add_argument("--download", nargs="?", const=str(DEFAULT_DOWNLOAD_DIR), default=None, metavar="DIR",
                        help="With --poll: save the trained artifact when available (default dir: models/checkpoints).")
    args = parser.parse_args(argv)

    plan = build_plan(
        train_file=args.train_file, eval_file=args.eval_file, model=args.model,
        target_rows=args.target_rows, idempotency_key=args.idempotency_key,
    )

    key_var: str | None = None
    key: str | None = None
    try:
        key_var, key = resolve_api_key()
    except LaunchError as exc:
        if args.execute or args.poll:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    if not (args.execute or args.poll):
        print_plan(plan, key_var=key_var, mode="dry-run")
        if plan["errors"]:
            print("\nValidation errors:", file=sys.stderr)
            for err in plan["errors"]:
                print(f"  {err}", file=sys.stderr)
            print("\nDry run FAILED; nothing was sent.", file=sys.stderr)
            return 1
        print("\nDry run PASSED: nothing was sent and no credits were spent.")
        return 0

    assert key_var and key
    try:
        client = client_factory(key)
        print(f"Using API key from {key_var}")
        if args.poll:
            poll(client, manifest_path=args.manifest, download_dir=Path(args.download) if args.download else None)
        else:
            print_plan(plan, key_var=key_var, mode="execute")
            execute(client, plan, key_var=key_var, manifest_path=args.manifest, custom_eval=args.custom_eval)
        return 0
    except LaunchError as exc:
        print(f"ERROR: {scrub(str(exc), key)}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: {describe_api_error(exc, key_var, key)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
