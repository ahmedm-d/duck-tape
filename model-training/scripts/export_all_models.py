#!/usr/bin/env python3
"""Poll Adaption Accounts 1-5, export checkpoints/exemplars, and build teammate handoff bundle."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adaption import APIError, APIStatusError, Adaption

SCRIPT_DIR = Path(__file__).resolve().parent
MODEL_TRAINING_ROOT = SCRIPT_DIR.parent
REPO_ROOT = MODEL_TRAINING_ROOT.parent

sys.path.insert(0, str(MODEL_TRAINING_ROOT / "src"))
from training.utils import load_training_env  # noqa: E402

MANIFEST_PATH = MODEL_TRAINING_ROOT / "data" / "adaption_runs_manifest.json"
PROCESSED_DIR = MODEL_TRAINING_ROOT / "data" / "processed"
CHECKPOINTS_CACHE_DIR = MODEL_TRAINING_ROOT / "models" / "checkpoints"
ACCOUNT3_EVAL_PATH = MODEL_TRAINING_ROOT / "data" / "account3_eval_results.md"

EXPORT_BUNDLE_DIR = MODEL_TRAINING_ROOT / "export_bundle"
EXPORT_MANIFEST_PATH = EXPORT_BUNDLE_DIR / "EXPORT_MANIFEST.json"
MODEL_HANDOFF_PATH = EXPORT_BUNDLE_DIR / "MODEL_HANDOFF.md"

TERMINAL_DOWNLOADABLE_STATUSES = frozenset({"succeeded", "completed"})
GRAPH_BLOCK_RE = re.compile(r"<graph_context>\s*(\{.*?\})\s*</graph_context>", re.DOTALL)

AGENT_ORDER: tuple[str, ...] = (
    "router",
    "ticket_parser",
    "implementer",
    "test_writer",
    "security_reviewer_dpo",
    "reviewer_dpo",
    "log_classifier",
    "commit_pr_writer",
    "db_migration",
    "iac",
    "frontend_ui",
    "hero_security_triage",
)

PROCESSED_FILENAME_OVERRIDES: dict[str, str] = {
    "hero_security_triage": "hero_graph_security_triage.jsonl",
}


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp string."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _to_repo_rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT)).replace("\\", "/")


def _load_account_clients() -> dict[int, Adaption]:
    """Load ADAPTION_API_KEY_1..5 and construct Adaption clients."""
    load_training_env()
    clients: dict[int, Adaption] = {}
    for idx in range(1, 6):
        key = (os.environ.get(f"ADAPTION_API_KEY_{idx}") or "").strip()
        if key:
            clients[idx] = Adaption(api_key=key, timeout=180.0, max_retries=3)
    return clients


def _resolve_live_api_model(base_model: str) -> str:
    """Map an AutoScientist training base model slug to a live Adaption chat completions model slug."""
    lower = base_model.lower()
    if "gemma-4-31b" in lower:
        return "gemma-4-31b-it"
    if "gemma-4-26b" in lower:
        return "gemma-4-26b-a4b-it"
    if "qwen" in lower:
        # Verified live Adaption chat endpoint slug in account3_eval_results.md;
        # qwen3.8-27b is also served on /api/v1/chat/completions.
        return "gemma-4-31b-it"
    return "gemma-4-31b-it"


def _poll_account(account_index: int, client: Adaption) -> dict[str, Any]:
    """List all AutoScientist runs and datasets for one Adaption account."""
    runs: list[Any] = []
    datasets: list[Any] = []
    errors: list[str] = []

    try:
        runs = list(client.autoscientist.list())
    except Exception as exc:
        errors.append(f"autoscientist.list: {exc}")

    try:
        datasets = list(client.datasets.list())
    except Exception as exc:
        errors.append(f"datasets.list: {exc}")

    return {
        "account_index": account_index,
        "runs": runs,
        "datasets": datasets,
        "errors": errors,
    }


def _extract_error_code(exc: APIStatusError) -> str:
    """Extract a concise machine error code from an Adaption APIStatusError."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, dict) and detail.get("code"):
            return str(detail["code"])
        if body.get("code"):
            return str(body["code"])
        if body.get("error"):
            return str(body["error"])
    text = str(exc)
    if "TRAINING_NOT_ELIGIBLE" in text:
        return "TRAINING_NOT_ELIGIBLE"
    return f"HTTP_{exc.status_code}"


def _extract_tgz(tgz_path: Path, weights_dir: Path) -> list[str]:
    """Extract a zstd or gzip .tgz checkpoint archive using system tar (bsdtar)."""
    weights_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["tar", "-xf", str(tgz_path), "-C", str(weights_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"tar extraction failed for {tgz_path}: {proc.stderr.strip() or proc.stdout.strip()}")

    files = sorted(
        str(p.relative_to(weights_dir)).replace("\\", "/")
        for p in weights_dir.rglob("*")
        if p.is_file()
    )
    return files


def _row_diversity_bucket(agent: str, row: dict[str, Any]) -> str:
    """Compute a lightweight diversity key to select varied high-signal few-shot exemplars."""
    text = json.dumps(row, ensure_ascii=False)
    graph_match = GRAPH_BLOCK_RE.search(text)
    graph: dict[str, Any] = {}
    if graph_match:
        try:
            parsed = json.loads(graph_match.group(1))
            if isinstance(parsed, dict):
                graph = parsed
        except json.JSONDecodeError:
            graph = {}

    if agent == "router":
        comp = str(row.get("completion", ""))
        for candidate in ("implementer", "test_writer", "security_reviewer", "reviewer", "db_migration", "iac", "frontend"):
            if candidate in comp:
                return f"target={candidate}"
    elif agent in {"security_reviewer_dpo", "reviewer_dpo"}:
        edges = graph.get("edges") or []
        has_inf = any(isinstance(e, dict) and e.get("confidence") == "INFERRED" for e in edges)
        has_ak = bool(graph.get("approved_knowledge"))
        cms = graph.get("catalogue_matches") or []
        has_null = any(isinstance(c, dict) and c.get("sink_reachable") is None for c in cms)
        return f"inf={has_inf}|ak={has_ak}|null={has_null}"
    elif agent == "log_classifier":
        comp = str(row.get("completion", ""))
        for line in comp.splitlines():
            if "category" in line or "classification" in line:
                return line[:48]

    focal = graph.get("focal_symbols") or []
    if isinstance(focal, list) and focal and isinstance(focal[0], dict):
        path_val = str(focal[0].get("path", ""))
        if path_val:
            return f"path={path_val}"

    has_graph = "<graph_context>" in text
    return f"graph={has_graph}|len={len(text) // 400}"


def _row_signal_score(row: dict[str, Any], raw_bytes_len: int) -> int:
    """Score a training row for canonical few-shot usefulness without bloating prompt size."""
    text = json.dumps(row, ensure_ascii=False)
    score = 0
    if "<graph_context>" in text:
        score += 100
    if "EXTRACTED" in text:
        score += 20
    if "INFERRED" in text:
        score += 20
    if "approved_knowledge" in text:
        score += 20
    if "linked_test_files" in text:
        score += 20
    if "sink_reachable" in text:
        score += 15
    if 600 <= raw_bytes_len <= 45000:
        score += 15
    elif raw_bytes_len > 90000:
        score -= 40
    return score


def export_few_shot_exemplars(
    agent: str,
    dest_path: Path,
    processed_filename: str,
    limit: int = 5,
) -> int:
    """Export 5 diverse, high-signal canonical few-shot exemplars to JSONL."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # Prefer curated DPO exemplars if already generated by Account 3 verification.
    curated_dpo = CHECKPOINTS_CACHE_DIR / agent / "domain_dpo_exemplars.jsonl"
    candidates_sources: list[Path] = []
    if curated_dpo.is_file() and curated_dpo.stat().st_size > 0:
        candidates_sources.append(curated_dpo)

    processed_path = PROCESSED_DIR / processed_filename
    if processed_path.is_file() and processed_path.stat().st_size > 0:
        candidates_sources.append(processed_path)

    augmented_path = CHECKPOINTS_CACHE_DIR / agent / "augmented_dataset.jsonl"
    if augmented_path.is_file() and augmented_path.stat().st_size > 0:
        candidates_sources.append(augmented_path)

    if not candidates_sources:
        dest_path.write_text("", encoding="utf-8", newline="\n")
        return 0

    scored_rows: list[tuple[int, str, dict[str, Any]]] = []
    source_path = candidates_sources[0]
    max_scan = 250 if source_path == processed_path else 50
    with source_path.open("r", encoding="utf-8") as handle:
        for idx, line in enumerate(handle):
            if idx >= max_scan:
                break
            raw = line.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            score = _row_signal_score(row, len(raw))
            bucket = _row_diversity_bucket(agent, row)
            scored_rows.append((score, bucket, row))

    scored_rows.sort(key=lambda item: item[0], reverse=True)
    picked: list[dict[str, Any]] = []
    seen_buckets: set[str] = set()
    used_indices: set[int] = set()

    for idx, (_score, bucket, row) in enumerate(scored_rows):
        if bucket in seen_buckets:
            continue
        seen_buckets.add(bucket)
        used_indices.add(idx)
        picked.append(row)
        if len(picked) >= limit:
            break

    if len(picked) < limit:
        for idx, (_score, _bucket, row) in enumerate(scored_rows):
            if idx in used_indices:
                continue
            picked.append(row)
            if len(picked) >= limit:
                break

    with dest_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in picked[:limit]:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(picked[:limit])


def _extract_account3_eval_section() -> str:
    """Extract the base-vs-adapted evaluation tables from account3_eval_results.md."""
    if not ACCOUNT3_EVAL_PATH.is_file():
        return "_`model-training/data/account3_eval_results.md` not found._"
    text = ACCOUNT3_EVAL_PATH.read_text(encoding="utf-8")
    marker = "## Step 3: Base vs. Adapted Domain Scorecard"
    if marker in text:
        idx = text.index(marker)
        end_marker = "## Smoke Test Sample Excerpts"
        end_idx = text.index(end_marker) if end_marker in text else len(text)
        section = text[idx:end_idx].strip()
        return section
    return text.strip()


def _format_iter(done: Any, max_it: Any) -> str:
    d_str = f"{int(done)}" if isinstance(done, (int, float)) else "-"
    m_str = f"{int(max_it)}" if isinstance(max_it, (int, float)) else "-"
    return f"{d_str}/{m_str}"


def _format_win(value: Any) -> str:
    return f"{value:.4f}" if isinstance(value, (int, float)) else "-"


def build_handoff_markdown(
    manifest_map: dict[str, dict[str, Any]],
    generated_at: str,
) -> str:
    """Render model-training/export_bundle/MODEL_HANDOFF.md for teammates."""
    downloaded_count = sum(1 for v in manifest_map.values() if v.get("status") == "weights_downloaded")
    running_count = len(manifest_map) - downloaded_count

    lines: list[str] = [
        "# Duck Tape Model Export & Teammate Handoff",
        "",
        f"- **Generated (UTC):** `{generated_at}`",
        f"- **Total Agents Exported:** `{len(manifest_map)}` (`{downloaded_count}` weights downloaded, `{running_count}` running with live API + 5 canonical exemplars ready)",
        "- **Manifest JSON:** `model-training/export_bundle/EXPORT_MANIFEST.json`",
        "",
        "## Quick Refresh (Re-check & Download Newly Finished Checkpoints)",
        "",
        "```bash",
        "python model-training/scripts/export_all_models.py --refresh",
        "```",
        "",
        "## Model Inventory & Local Artifact Paths",
        "",
        "| Agent | Account Env | Run ID | Base Model | Live API Model | Status | Iter | Best Win Rate | Local Path |",
        "| --- | --- | --- | --- | --- | --- | --- | ---: | --- |",
    ]

    for agent in AGENT_ORDER:
        info = manifest_map.get(agent)
        if not info:
            continue
        local_path = info.get("weights_path") or info.get("exemplars_path") or "-"
        lines.append(
            f"| `{agent}` | `{info.get('account_env_var', '-')}` | `{info.get('run_id', '-')}` | "
            f"`{info.get('base_model', '-')}` | `{info.get('live_api_model', '-')}` | "
            f"`{info.get('status', '-')}` | `{_format_iter(info.get('iterations_completed'), info.get('max_iterations'))}` | "
            f"`{_format_win(info.get('best_win_rate'))}` | `{local_path}` |"
        )

    lines.extend(
        [
            "",
            "## How Teammates Consume `export_bundle/`",
            "",
            "1. **Checkpoints already extracted (`status == \"weights_downloaded\"`)**: load PEFT/LoRA adapter weights directly from `weights_path` (`adapter_config.json` + `adapter_model.safetensors`).",
            "2. **Active runs (`status == \"running_api_exemplar_ready\"`)**: call Adaption Chat Completions (`https://api.prod.adaptionlabs.ai/api/v1/chat/completions`) using the env var in `account_env_var` (`model-training/.env.training`), `live_api_model`, and the 5 high-signal canonical few-shot rows in `exemplars_path` (`few_shot_exemplars.jsonl`).",
            "3. **Re-harvest in ~10s**: run `python model-training/scripts/export_all_models.py --refresh` whenever an AutoScientist job reaches Iteration 3/3.",
            "",
            "## Base vs. Adapted Evaluation Evidence (Paste Ready for `docs/hackathon/SUBMISSION.md`)",
            "",
            "> Source: `model-training/data/account3_eval_results.md` (`ADAPTION_API_KEY_3`, `gemma-4-31b-it` live inference & 50-row held-out DPO eval benchmarks).",
            "",
            _extract_account3_eval_section(),
            "",
        ]
    )
    return "\n".join(lines)


def harvest_all_models(*, refresh: bool = False) -> dict[str, dict[str, Any]]:
    """Poll Accounts 1-5, download eligible checkpoints, export exemplars, and write handoff files."""
    if not MANIFEST_PATH.is_file():
        raise FileNotFoundError(f"Missing run manifest: {MANIFEST_PATH}")

    raw_manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest_runs: dict[str, dict[str, Any]] = {
        str(entry["agent"]): entry
        for entry in raw_manifest.get("runs", [])
        if isinstance(entry, dict) and entry.get("agent")
    }
    manifest_datasets: dict[str, dict[str, Any]] = {
        str(k): v
        for k, v in (raw_manifest.get("datasets") or {}).items()
        if isinstance(v, dict)
    }

    clients = _load_account_clients()
    if not clients:
        raise RuntimeError("No ADAPTION_API_KEY_1..5 found in model-training/.env.training or environment.")

    # Step 1: Concurrently poll client.autoscientist.list() and client.datasets.list() across Accounts 1-5.
    account_polls: dict[int, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(clients)) as pool:
        futures = {pool.submit(_poll_account, idx, client): idx for idx, client in clients.items()}
        for fut in concurrent.futures.as_completed(futures):
            idx = futures[fut]
            account_polls[idx] = fut.result()

    runs_by_id: dict[str, tuple[int, Any]] = {}
    datasets_by_id: dict[str, tuple[int, Any]] = {}
    datasets_by_name: dict[str, tuple[int, Any]] = {}
    for acct_idx, payload in sorted(account_polls.items()):
        for run in payload["runs"]:
            rid = str(getattr(run, "id", "") or "")
            if rid:
                runs_by_id[rid] = (acct_idx, run)
        for ds in payload["datasets"]:
            did = str(getattr(ds, "dataset_id", "") or "")
            dname = str(getattr(ds, "name", "") or "")
            if did:
                datasets_by_id[did] = (acct_idx, ds)
            if dname:
                datasets_by_name[dname] = (acct_idx, ds)

    EXPORT_BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = utc_now_iso()

    def _process_agent(agent: str) -> tuple[str, dict[str, Any]]:
        manifest_entry = manifest_runs.get(agent, {})
        ds_entry = manifest_datasets.get(agent, {})

        run_id = str(manifest_entry.get("run_id") or "")
        dataset_id = str(manifest_entry.get("dataset_id") or ds_entry.get("dataset_id") or "")
        account_index = int(manifest_entry.get("account_index") or ds_entry.get("account_index") or 0)
        base_model = str(manifest_entry.get("model") or "google/gemma-4-31B-it")

        live_run = None
        if run_id and run_id in runs_by_id:
            account_index, live_run = runs_by_id[run_id]
        elif account_index in clients and run_id:
            try:
                live_run = clients[account_index].autoscientist.get(run_id)
            except Exception:
                live_run = None

        if live_run is not None:
            run_status = str(getattr(live_run, "status", manifest_entry.get("status", "unknown")))
            iters_done = getattr(live_run, "iterations_completed", manifest_entry.get("iterations_completed"))
            max_iters = getattr(live_run, "max_iterations", manifest_entry.get("max_iterations"))
            best_win_rate = getattr(live_run, "best_win_rate", manifest_entry.get("best_win_rate"))
            target_win_rate = getattr(live_run, "target_win_rate", manifest_entry.get("target_win_rate"))
            download_available = bool(getattr(live_run, "download_available", False))
            base_model = str(getattr(live_run, "model", None) or base_model)
            dataset_id = str(getattr(live_run, "dataset_id", None) or dataset_id)
        else:
            run_status = str(manifest_entry.get("status", "unknown"))
            iters_done = manifest_entry.get("iterations_completed")
            max_iters = manifest_entry.get("max_iterations")
            best_win_rate = manifest_entry.get("best_win_rate")
            target_win_rate = manifest_entry.get("target_win_rate")
            download_available = bool(manifest_entry.get("download_available", False))

        aug_ds_name = f"duck_tape_{agent} (augmented)"
        aug_ds_info = datasets_by_name.get(aug_ds_name)
        aug_dataset_id = str(getattr(aug_ds_info[1], "dataset_id", "")) if aug_ds_info else None
        aug_dataset_status = str(getattr(aug_ds_info[1], "status", "")) if aug_ds_info else None

        agent_dir = EXPORT_BUNDLE_DIR / agent
        agent_dir.mkdir(parents=True, exist_ok=True)

        processed_filename = PROCESSED_FILENAME_OVERRIDES.get(agent, f"{agent}.jsonl")
        exemplars_path = agent_dir / "few_shot_exemplars.jsonl"
        if refresh or not exemplars_path.is_file() or exemplars_path.stat().st_size == 0:
            exemplar_count = export_few_shot_exemplars(agent, exemplars_path, processed_filename, limit=5)
        else:
            exemplar_count = sum(1 for ln in exemplars_path.read_text(encoding="utf-8").splitlines() if ln.strip())

        tgz_path = agent_dir / "checkpoint.tgz"
        weights_dir = agent_dir / "weights"
        client = clients.get(account_index)
        download_check = "unknown"
        weights_downloaded = False
        extracted_files: list[str] = []

        is_eligible_hint = download_available or run_status.lower() in TERMINAL_DOWNLOADABLE_STATUSES
        cached_tgz = CHECKPOINTS_CACHE_DIR / agent / f"{run_id}.tgz"

        if is_eligible_hint:
            if not tgz_path.is_file() or tgz_path.stat().st_size == 0:
                if cached_tgz.is_file() and cached_tgz.stat().st_size > 0:
                    shutil.copy2(cached_tgz, tgz_path)
                    download_check = "eligible (copied verified local archive)"
                elif client is not None and run_id:
                    try:
                        resp = client.autoscientist.download(run_id)
                        tgz_path.write_bytes(resp.read())
                        download_check = "eligible (downloaded via SDK)"
                    except APIStatusError as exc:
                        download_check = f"HTTP {exc.status_code} {_extract_error_code(exc)}"
                    except APIError as exc:
                        download_check = f"APIError: {exc}"
            else:
                download_check = "eligible (cached in export_bundle)"

            if tgz_path.is_file() and tgz_path.stat().st_size > 0:
                adapter_cfg = weights_dir / "adapter_config.json"
                if not adapter_cfg.is_file() or refresh:
                    extracted_files = _extract_tgz(tgz_path, weights_dir)
                else:
                    extracted_files = sorted(
                        str(p.relative_to(weights_dir)).replace("\\", "/")
                        for p in weights_dir.rglob("*")
                        if p.is_file()
                    )
                weights_downloaded = True
        else:
            # Explicitly check client.autoscientist.download(run_id) eligibility for running jobs.
            if client is not None and run_id:
                try:
                    resp = client.autoscientist.download(run_id)
                    tgz_path.write_bytes(resp.read())
                    extracted_files = _extract_tgz(tgz_path, weights_dir)
                    weights_downloaded = True
                    download_check = "eligible (downloaded via SDK)"
                except APIStatusError as exc:
                    download_check = f"HTTP {exc.status_code} {_extract_error_code(exc)}"
                except Exception as exc:
                    download_check = f"error: {exc}"
            else:
                download_check = "no_client_or_run_id"

        live_api_model = _resolve_live_api_model(base_model)
        account_env_var = f"ADAPTION_API_KEY_{account_index}"

        if not weights_downloaded:
            active_info = {
                "agent": agent,
                "status": "running_api_exemplar_ready",
                "autoscientist_status": run_status,
                "account_index": account_index,
                "account_env_var": account_env_var,
                "run_id": run_id,
                "dataset_id": dataset_id,
                "augmented_dataset_id": aug_dataset_id,
                "augmented_dataset_status": aug_dataset_status,
                "base_model": base_model,
                "live_api_model": live_api_model,
                "adaption_chat_url": "https://api.prod.adaptionlabs.ai/api/v1/chat/completions",
                "iterations_completed": iters_done,
                "max_iterations": max_iters,
                "best_win_rate": best_win_rate,
                "target_win_rate": target_win_rate,
                "download_check": download_check,
                "exemplars_path": _to_repo_rel(exemplars_path),
                "exemplars_count": exemplar_count,
                "updated_at": generated_at,
            }
            (agent_dir / "active_run_info.json").write_text(
                json.dumps(active_info, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )

        manifest_record: dict[str, Any] = {
            "status": "weights_downloaded" if weights_downloaded else "running_api_exemplar_ready",
            "autoscientist_status": run_status,
            "account_env_var": account_env_var,
            "account_index": account_index,
            "run_id": run_id,
            "dataset_id": dataset_id,
            "augmented_dataset_id": aug_dataset_id,
            "base_model": base_model,
            "live_api_model": live_api_model,
            "iterations_completed": iters_done,
            "max_iterations": max_iters,
            "best_win_rate": best_win_rate,
            "target_win_rate": target_win_rate,
            "gain_vs_50pct": round(best_win_rate - 0.5, 4) if isinstance(best_win_rate, (int, float)) else None,
            "download_available": weights_downloaded or download_available,
            "download_check": download_check,
            "weights_path": _to_repo_rel(weights_dir) if weights_downloaded else None,
            "checkpoint_tgz_path": _to_repo_rel(tgz_path) if weights_downloaded else None,
            "extracted_files": extracted_files if weights_downloaded else [],
            "exemplars_path": _to_repo_rel(exemplars_path),
            "exemplars_count": exemplar_count,
            "updated_at": generated_at,
        }
        return agent, manifest_record

    agents_to_export = [a for a in AGENT_ORDER if a in manifest_runs or a in manifest_datasets]
    export_manifest: dict[str, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(_process_agent, agents_to_export))
    for agent, record in results:
        export_manifest[agent] = record

    ordered_manifest = {agent: export_manifest[agent] for agent in agents_to_export if agent in export_manifest}

    tmp_json = EXPORT_MANIFEST_PATH.with_suffix(".json.tmp")
    tmp_json.write_text(json.dumps(ordered_manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp_json, EXPORT_MANIFEST_PATH)

    handoff_md = build_handoff_markdown(ordered_manifest, generated_at)
    tmp_md = MODEL_HANDOFF_PATH.with_suffix(".md.tmp")
    tmp_md.write_text(handoff_md, encoding="utf-8", newline="\n")
    os.replace(tmp_md, MODEL_HANDOFF_PATH)

    return ordered_manifest


def print_summary_table(export_manifest: dict[str, dict[str, Any]], account_count: int) -> None:
    """Print a concise harvest summary table to stdout."""
    print(f"\n=== Duck Tape Model Harvest & Export Bundle ({utc_now_iso()} | {account_count} accounts) ===")
    print(
        f"{'Agent':<22} | {'Acct':<4} | {'Run Status':<10} | {'Iter':<5} | {'WinRate':<7} | "
        f"{'Bundle Status':<26} | {'Artifact Path'}"
    )
    print("-" * 130)
    for agent, info in export_manifest.items():
        acct = f"#{info.get('account_index', '?')}"
        run_status = str(info.get("autoscientist_status", "-"))
        iteration = _format_iter(info.get("iterations_completed"), info.get("max_iterations"))
        win_rate = _format_win(info.get("best_win_rate"))
        bundle_status = str(info.get("status", "-"))
        artifact = str(info.get("weights_path") or info.get("exemplars_path") or "-")
        print(
            f"{agent:<22} | {acct:<4} | {run_status:<10} | {iteration:<5} | {win_rate:<7} | "
            f"{bundle_status:<26} | {artifact}"
        )

    downloaded = sum(1 for v in export_manifest.values() if v.get("status") == "weights_downloaded")
    running = len(export_manifest) - downloaded
    print(
        f"\nHarvested {len(export_manifest)} models: {downloaded} .tgz checkpoints extracted on disk, "
        f"{running} running with live API + 5 exemplars exported."
    )
    print(f"Manifest: {_to_repo_rel(EXPORT_MANIFEST_PATH)}")
    print(f"Handoff : {_to_repo_rel(MODEL_HANDOFF_PATH)}")


def main() -> int:
    """CLI entry point for harvesting Adaption models across Accounts 1-5."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Poll Adaption Accounts 1-5, harvest checkpoints/exemplars, and generate handoff manifest."
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-check all 5 accounts and download/extract any newly finished .tgz checkpoints.",
    )
    args = parser.parse_args()

    clients = _load_account_clients()
    manifest_map = harvest_all_models(refresh=args.refresh)
    print_summary_table(manifest_map, len(clients))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
