#!/usr/bin/env python3
"""Live multi-account Adaption AutoScientist poller and state updater for Duck Tape."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adaption import Adaption

SCRIPT_DIR = Path(__file__).resolve().parent
MODEL_TRAINING_ROOT = SCRIPT_DIR.parent

sys.path.insert(0, str(MODEL_TRAINING_ROOT / "src"))
from training.utils import load_training_env  # noqa: E402

MANIFEST_PATH = MODEL_TRAINING_ROOT / "data" / "adaption_runs_manifest.json"
TRAINING_STATE_PATH = MODEL_TRAINING_ROOT / "TRAINING_STATE.md"

TERMINAL_STATUSES = frozenset({"succeeded", "completed", "failed", "cancelled", "eval_failed"})
RUNS_START_MARKER = "<!-- ADAPTION_RUNS_START -->"
RUNS_END_MARKER = "<!-- ADAPTION_RUNS_END -->"


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp string."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_account_clients() -> dict[int, Adaption]:
    """Load ADAPTION_API_KEY_1..4 and return Adaption clients per account index."""
    load_training_env()
    clients: dict[int, Adaption] = {}
    for idx in range(1, 5):
        key = (os.environ.get(f"ADAPTION_API_KEY_{idx}") or "").strip()
        if key:
            clients[idx] = Adaption(api_key=key, timeout=60.0, max_retries=3)
    return clients


def load_manifest() -> dict[str, Any]:
    """Load model-training/data/adaption_runs_manifest.json."""
    if not MANIFEST_PATH.is_file():
        raise FileNotFoundError(f"Manifest not found: {MANIFEST_PATH}")
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def save_manifest(manifest: dict[str, Any]) -> None:
    """Atomically write updated manifest JSON using UTF-8 LF."""
    manifest["updated_at"] = utc_now_iso()
    tmp_path = MANIFEST_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp_path, MANIFEST_PATH)


def _format_iteration(run_entry: dict[str, Any]) -> str:
    done = run_entry.get("iterations_completed")
    max_it = run_entry.get("max_iterations")
    done_str = str(int(done)) if isinstance(done, (int, float)) else (str(done) if done is not None else "-")
    max_str = str(int(max_it)) if isinstance(max_it, (int, float)) else (str(max_it) if max_it is not None else "-")
    return f"{done_str}/{max_str}"


def _format_win_rate(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.3f}"
    return "-"


def poll_once() -> tuple[dict[str, Any], bool]:
    """Poll all runs in manifest once, save manifest + TRAINING_STATE.md, return (manifest, all_terminal)."""
    clients = load_account_clients()
    manifest = load_manifest()
    runs: list[dict[str, Any]] = manifest.get("runs", [])

    def _poll_run(entry: dict[str, Any]) -> dict[str, Any]:
        acct_idx = int(entry.get("account_index", 0))
        run_id = entry.get("run_id")
        client = clients.get(acct_idx)
        if not client or not run_id:
            return entry
        try:
            live = client.autoscientist.get(run_id)
            best_hp = getattr(live, "best_hyperparams", None)
            if best_hp is not None and hasattr(best_hp, "model_dump"):
                best_hp = best_hp.model_dump()
            created_at = getattr(live, "created_at", None)
            completed_at = getattr(live, "completed_at", None)
            updated = dict(entry)
            updated.update(
                {
                    "dataset_id": getattr(live, "dataset_id", entry.get("dataset_id")),
                    "model": getattr(live, "model", None) or entry.get("model"),
                    "training_method": getattr(live, "training_method", None) or entry.get("training_method"),
                    "status": getattr(live, "status", entry.get("status")),
                    "iterations_completed": getattr(live, "iterations_completed", entry.get("iterations_completed")),
                    "max_iterations": getattr(live, "max_iterations", entry.get("max_iterations")),
                    "target_win_rate": getattr(live, "target_win_rate", entry.get("target_win_rate")),
                    "best_win_rate": getattr(live, "best_win_rate", entry.get("best_win_rate")),
                    "download_available": getattr(live, "download_available", False),
                    "error": getattr(live, "error", None),
                    "best_hyperparams": best_hp,
                    "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
                    "completed_at": completed_at.isoformat() if hasattr(completed_at, "isoformat") else completed_at,
                    "last_checked_at": utc_now_iso(),
                }
            )
            return updated
        except Exception as exc:
            updated = dict(entry)
            updated["last_poll_error"] = str(exc)
            updated["last_checked_at"] = utc_now_iso()
            return updated

    if runs:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            polled_runs = list(pool.map(_poll_run, runs))
        manifest["runs"] = polled_runs
        runs = polled_runs

    # Refresh optional Web UI router dataset status and trajectory extension dataset status
    web_ui_ds = manifest.get("datasets", {}).get("router_web_ui")
    if isinstance(web_ui_ds, dict) and web_ui_ds.get("dataset_id") and 1 in clients:
        try:
            ds_detail = clients[1].datasets.get(web_ui_ds["dataset_id"])
            web_ui_ds["status"] = getattr(ds_detail, "status", web_ui_ds.get("status"))
            web_ui_ds["row_count"] = getattr(ds_detail, "row_count", web_ui_ds.get("row_count"))
            web_ui_ds["last_checked_at"] = utc_now_iso()
        except Exception:
            pass

    for agent_name, ext_info in manifest.get("trajectory_extensions", {}).items():
        if not isinstance(ext_info, dict):
            continue
        ext_ds_id = ext_info.get("extended_dataset_id")
        if ext_ds_id and ext_info.get("status") not in TERMINAL_STATUSES and 2 in clients:
            try:
                ds_status = clients[2].datasets.get_status(ext_ds_id)
                ext_info["status"] = getattr(ds_status, "status", ext_info.get("status"))
                ext_info["row_count"] = getattr(ds_status, "row_count", ext_info.get("row_count"))
                ext_info["last_checked_at"] = utc_now_iso()
            except Exception:
                pass

    save_manifest(manifest)
    update_training_state_md(manifest)

    sorted_runs = sorted(runs, key=lambda r: (int(r.get("account_index", 0)), str(r.get("agent", ""))))
    print(f"\n=== Adaption AutoScientist Live Status ({manifest.get('updated_at')}) ===")
    print(
        f"{'Account':<7} | {'Agent':<21} | {'Run ID':<36} | {'Status':<11} | {'Iteration':<9} | {'Best Win Rate':<13}"
    )
    print("-" * 111)
    for r in sorted_runs:
        acct = f"Acct {r.get('account_index', '?')}"
        agent = str(r.get("agent", "?"))
        run_id = str(r.get("run_id") or "-")
        status = str(r.get("status") or "unknown")
        iteration = _format_iteration(r)
        win_rate = _format_win_rate(r.get("best_win_rate"))
        print(f"{acct:<7} | {agent:<21} | {run_id:<36} | {status:<11} | {iteration:<9} | {win_rate:<13}")

    all_terminal = bool(runs) and all(str(r.get("status", "")).lower() in TERMINAL_STATUSES for r in runs)
    active_count = sum(1 for r in runs if str(r.get("status", "")).lower() not in TERMINAL_STATUSES)
    done_count = len(runs) - active_count
    print(f"\nSummary: {len(runs)} total runs | {active_count} active | {done_count} terminal")
    return manifest, all_terminal


def update_training_state_md(manifest: dict[str, Any]) -> None:
    """Update model-training/TRAINING_STATE.md with live AutoScientist run status."""
    if not TRAINING_STATE_PATH.is_file():
        return

    text = TRAINING_STATE_PATH.read_text(encoding="utf-8")
    runs: list[dict[str, Any]] = sorted(
        manifest.get("runs", []),
        key=lambda r: (int(r.get("account_index", 0)), str(r.get("agent", ""))),
    )

    all_terminal = bool(runs) and all(str(r.get("status", "")).lower() in TERMINAL_STATUSES for r in runs)
    phase_status = "Completed" if all_terminal else "In Progress"
    started_flag = "true" if runs else "false"

    text = re.sub(
        r"^- \*\*Current Phase:\*\* .*$",
        f"- **Current Phase:** Part 4 of 5 — Multi-Account Adaption AutoScientist Orchestration (**{phase_status}**)",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^- \*\*Model Training Started:\*\* .*$",
        f"- **Model Training Started:** `{started_flag}` (multi-account Adaption AutoScientist orchestration active in Part 4)",
        text,
        flags=re.MULTILINE,
    )

    active_count = sum(1 for r in runs if str(r.get("status", "")).lower() not in TERMINAL_STATUSES)
    succeeded_count = sum(1 for r in runs if str(r.get("status", "")).lower() in {"succeeded", "completed"})
    failed_count = sum(1 for r in runs if str(r.get("status", "")).lower() in {"failed", "cancelled", "eval_failed"})

    lines = [
        RUNS_START_MARKER,
        "## Multi-Account Adaption AutoScientist Runs (Part 4 of 5)",
        "",
        f"- **Last Polled (UTC):** `{manifest.get('updated_at', utc_now_iso())}`",
        f"- **Run Summary:** `{len(runs)}` total | `{active_count}` active | `{succeeded_count}` succeeded | `{failed_count}` failed/cancelled",
        "- **Orchestrator Scripts:** `model-training/scripts/launch_adaption_parallel.py`, `model-training/scripts/poll_adaption_runs.py`",
        "- **Run Manifest:** `model-training/data/adaption_runs_manifest.json`",
        "",
        "| Account | Agent | Model | Method | Eff. Rows | Run ID | Status | Iteration | Best Win Rate |",
        "| --- | --- | --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for r in runs:
        acct = f"Acct {r.get('account_index', '?')}"
        agent = f"`{r.get('agent', '?')}`"
        model = f"`{r.get('model', '?')}`"
        method = f"`{r.get('training_method', '?')}`"
        eff_rows = r.get("effective_rows")
        eff_str = f"{int(eff_rows):,}" if isinstance(eff_rows, (int, float)) else "-"
        run_id = f"`{r.get('run_id', '-')}`"
        status = f"`{r.get('status', 'unknown')}`"
        iteration = f"`{_format_iteration(r)}`"
        win_rate = f"`{_format_win_rate(r.get('best_win_rate'))}`"
        lines.append(
            f"| {acct} | {agent} | {model} | {method} | {eff_str} | {run_id} | {status} | {iteration} | {win_rate} |"
        )

    web_ui_ds = manifest.get("datasets", {}).get("router_web_ui")
    if isinstance(web_ui_ds, dict) and web_ui_ds.get("dataset_id"):
        lines.append(
            f"- **Account 1 Web UI Router Discovery:** `{web_ui_ds.get('name')}` (`{web_ui_ds.get('dataset_id')}`, "
            f"status=`{web_ui_ds.get('status')}`)"
        )
    for agent_name, ext in manifest.get("trajectory_extensions", {}).items():
        if isinstance(ext, dict):
            lines.append(
                f"- **Trajectory Extension (`{agent_name}`):** status=`{ext.get('status')}`"
                + (f", run_id=`{ext.get('run_id')}`" if ext.get("run_id") else "")
                + (f", dataset_id=`{ext.get('extended_dataset_id')}`" if ext.get("extended_dataset_id") else "")
            )
    for agent_name, ce in manifest.get("custom_evals", {}).items():
        if isinstance(ce, dict):
            lines.append(
                f"- **Custom Eval (`{agent_name}`):** status=`{ce.get('status')}`"
                + (f", custom_eval_id=`{ce.get('custom_eval_id')}`" if ce.get("custom_eval_id") else "")
            )

    lines.extend(
        [
            "",
            "## Next Step (Part 5 of 5)",
            "- Poll `python model-training/scripts/poll_adaption_runs.py` until runs reach terminal states (`succeeded`/`completed`), then download best checkpoints (`client.autoscientist.download()`), benchmark base vs. adapted held-out eval sets, and wire the Databricks/vLLM endpoints + `backend/worker.py` patch.",
            RUNS_END_MARKER,
        ]
    )
    block = "\n".join(lines)

    if RUNS_START_MARKER in text and RUNS_END_MARKER in text:
        pattern = re.compile(
            re.escape(RUNS_START_MARKER) + r".*?" + re.escape(RUNS_END_MARKER),
            re.DOTALL,
        )
        new_text = pattern.sub(block, text)
    elif "## Next Step (Part 4 of 5)" in text:
        idx = text.index("## Next Step (Part 4 of 5)")
        new_text = text[:idx].rstrip() + "\n\n" + block + "\n"
    else:
        new_text = text.rstrip() + "\n\n" + block + "\n"

    tmp_path = TRAINING_STATE_PATH.with_suffix(".md.tmp")
    tmp_path.write_text(new_text, encoding="utf-8", newline="\n")
    os.replace(tmp_path, TRAINING_STATE_PATH)


def main() -> int:
    """CLI entry point for live polling across Accounts 1-4."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Poll Adaption AutoScientist runs across Accounts 1-4.")
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Continuously poll until all runs reach a terminal state.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=60.0,
        help="Polling interval in seconds when --watch is enabled (default: 60).",
    )
    args = parser.parse_args()

    if not args.watch:
        poll_once()
        return 0

    while True:
        _, all_terminal = poll_once()
        if all_terminal:
            print("All runs reached terminal state.")
            return 0
        time.sleep(max(5.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
