"""Wave 2 Data Prep — Agent 8: security_reviewer (PreferenceRow for DPO)."""
from __future__ import annotations

import hashlib
import json
import random
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from datasets import load_dataset
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from training.schemas import PreferenceRow
from training.scrub import scrub_with_count
from training.utils import (
    MODEL_TRAINING_ROOT,
    load_training_env,
    split_train_eval,
    truncate_bounded,
    write_preference_jsonl,
)

TRAIN_CLASS_TARGET = 2500
EVAL_CLASS_TARGET = 100
TOTAL_CLASS_TARGET = TRAIN_CLASS_TARGET + EVAL_CLASS_TARGET  # 2600 per class -> 5200 total

OSV_PACKAGES: tuple[tuple[str, str], ...] = (
    ("PyPI", "requests"),
    ("PyPI", "urllib3"),
    ("PyPI", "cryptography"),
    ("PyPI", "jinja2"),
    ("PyPI", "pyyaml"),
    ("PyPI", "django"),
    ("PyPI", "flask"),
    ("PyPI", "pillow"),
    ("PyPI", "sqlalchemy"),
    ("PyPI", "aiohttp"),
    ("PyPI", "werkzeug"),
    ("PyPI", "paramiko"),
    ("PyPI", "lxml"),
    ("PyPI", "fastapi"),
    ("PyPI", "starlette"),
    ("npm", "lodash"),
    ("npm", "axios"),
    ("npm", "express"),
    ("npm", "jsonwebtoken"),
    ("npm", "minimist"),
    ("npm", "ws"),
    ("npm", "tar"),
    ("Go", "github.com/gin-gonic/gin"),
    ("Go", "golang.org/x/crypto"),
    ("Maven", "com.fasterxml.jackson.core:jackson-databind"),
)

FALLBACK_OSV_RECORDS: list[dict[str, str]] = [
    {
        "id": "GHSA-j8r2-6x86-q33q",
        "ecosystem": "PyPI",
        "package": "requests",
        "affected_version": "2.28.1",
        "fixed_version": "2.31.0",
        "summary": "Unintended leak of Proxy-Authorization header on cross-origin redirect",
        "symbol": "Session.resolve_redirects",
    },
    {
        "id": "GHSA-34jh-p97f-mpxf",
        "ecosystem": "PyPI",
        "package": "urllib3",
        "affected_version": "2.0.6",
        "fixed_version": "2.0.7",
        "summary": "Cookie request header isn't stripped during cross-origin redirects",
        "symbol": "PoolManager.urlopen",
    },
    {
        "id": "GHSA-h5c8-rqwp-cp95",
        "ecosystem": "PyPI",
        "package": "jinja2",
        "affected_version": "3.1.2",
        "fixed_version": "3.1.3",
        "summary": "HTML attribute injection via xmlattr filter when untrusted keys are passed",
        "symbol": "do_xmlattr",
    },
    {
        "id": "GHSA-8qvm-5x2c-j2w7",
        "ecosystem": "npm",
        "package": "axios",
        "affected_version": "1.5.0",
        "fixed_version": "1.6.0",
        "summary": "Cross-Site Request Forgery token leakage to third-party hosts",
        "symbol": "dispatchXhrRequest",
    },
]

_FUNC_NAME_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{2,50})\s*\(")


def _fetch_osv_advisories() -> list[dict[str, str]]:
    """Query live OSV.dev API for real advisory records with deterministic fallback."""
    advisories: list[dict[str, str]] = []
    for ecosystem, package in OSV_PACKAGES:
        try:
            req = urllib.request.Request(
                "https://api.osv.dev/v1/query",
                data=json.dumps({"package": {"name": package, "ecosystem": ecosystem}}).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "Steward-DataPrep/1.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            for vuln in (payload.get("vulns") or [])[:4]:
                vuln_id = str(vuln.get("id") or "").strip()
                summary = str(vuln.get("summary") or vuln.get("details") or "").strip()
                if not vuln_id or not summary:
                    continue
                affected_ver = "1.0.0"
                fixed_ver = "1.0.1"
                for aff in vuln.get("affected") or []:
                    versions = aff.get("versions") or []
                    if versions:
                        affected_ver = str(versions[-1])
                    for rng in aff.get("ranges") or []:
                        for ev in rng.get("events") or []:
                            if ev.get("fixed"):
                                fixed_ver = str(ev["fixed"])
                                break
                advisories.append(
                    {
                        "id": vuln_id,
                        "ecosystem": ecosystem,
                        "package": package,
                        "affected_version": affected_ver,
                        "fixed_version": fixed_ver,
                        "summary": " ".join(summary.split())[:280],
                        "symbol": f"{package.split('/')[-1].split(':')[-1]}.handler",
                    }
                )
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            continue

    return advisories if advisories else list(FALLBACK_OSV_RECORDS)


def _extract_func_name(code: str, fallback: str = "process_input") -> str:
    for line in (code or "").splitlines()[:8]:
        m = _FUNC_NAME_RE.search(line)
        if m and m.group(1) not in {"if", "for", "while", "switch", "sizeof", "return", "catch"}:
            return m.group(1)
    return fallback


def _format_lockfile_snippet(adv: dict[str, str]) -> str:
    eco = adv["ecosystem"]
    pkg = adv["package"]
    ver = adv["affected_version"]
    if eco == "PyPI":
        return f"# requirements.lock\n{pkg}=={ver}"
    if eco == "npm":
        return f"// package-lock.json\n\"{pkg}\": {{\"version\": \"{ver}\"}}"
    if eco == "Go":
        return f"// go.mod\nrequire {pkg} v{ver.lstrip('v')}"
    return f"<!-- pom.xml -->\n<dependency><artifactId>{pkg}</artifactId><version>{ver}</version></dependency>"


def _build_security_pair(
    idx: int,
    source_name: str,
    project: str,
    cwe: str,
    cve: str,
    func_code: str,
    is_exploitable: bool,
    adv: dict[str, str],
) -> tuple[PreferenceRow, int] | None:
    c_func, r1 = scrub_with_count(func_code)
    c_proj, r2 = scrub_with_count(project or "service/core")
    c_sum, r3 = scrub_with_count(adv["summary"])
    redactions = r1 + r2 + r3

    if len(c_func.strip()) < 40:
        return None

    fn_name = _extract_func_name(c_func, fallback=f"handle_{idx % 100}")
    lockfile = _format_lockfile_snippet(adv)
    pkg = adv["package"]
    adv_id = cve or adv["id"]
    cwe_label = cwe or "CWE-20"

    if is_exploitable:
        caller_graph = (
            f"api_router.dispatch_request(req)\n"
            f"  -> {c_proj}.controller.handle_untrusted_payload(req.body)\n"
            f"    -> {fn_name}(untrusted_input)  [UNSANITIZED DATAFLOW REACHES SINK]"
        )
        reachability_note = "reachable_with_untrusted_input=true"
    else:
        if idx % 2 == 0:
            caller_graph = (
                f"api_router.dispatch_request(req)\n"
                f"  -> {c_proj}.controller.safe_handler(validated_id)\n"
                f"    -> [NO CALL PATH TO {adv['symbol']}; {fn_name} only invoked by offline build script on static constants]"
            )
            reachability_note = "reachable_with_untrusted_input=false (unreachable from external entrypoints)"
        else:
            caller_graph = (
                f"api_router.dispatch_request(req)\n"
                f"  -> {c_proj}.controller.handle_validated_input(sanitized_payload)\n"
                f"    -> {fn_name}(sanitized_payload)  [PATCHED / BOUNDS-CHECKED IMPLEMENTATION]"
            )
            reachability_note = "reachable_with_untrusted_input=false (patched & input validated before call)"

    prompt = (
        "Assess whether this dependency advisory and function diff represent an exploitable vulnerability in the repository.\n"
        "Rule: A dependency version match alone is not proof of exploitability — verify reachability from untrusted inputs "
        "in the caller graph and inspect the exact function symbols before raising a blocking alarm.\n"
        f"Source: {source_name}\n"
        f"Project: {c_proj}\n"
        f"CWE / Advisory: {cwe_label} | {adv['id']}" + (f" ({adv_id})" if adv_id != adv["id"] else "") + "\n\n"
        f"Dependency Lockfile Snippet:\n```\n{lockfile}\n```\n\n"
        f"OSV Advisory:\n"
        f"- ID: {adv['id']} ({adv['ecosystem']}: {pkg} == {adv['affected_version']}, fixed >= {adv['fixed_version']})\n"
        f"- Summary: {c_sum}\n\n"
        f"Repository Caller Graph ({reachability_note}):\n```\n{caller_graph}\n```\n\n"
        f"Target Function Snippet (`{fn_name}`):\n```\n{truncate_bounded(c_func, 2000)}\n```"
    )

    if is_exploitable:
        chosen = (
            f"### Verdict: EXPLOITABLE (Reachable with Untrusted Input)\n"
            f"A dependency version match alone is not proof of exploitability, but tracing the caller graph confirms "
            f"that untrusted input flows from `api_router.dispatch_request` through `{c_proj}.controller.handle_untrusted_payload` "
            f"directly into `{fn_name}` without validation or escaping.\n\n"
            f"### Cited Symbols & Evidence\n"
            f"- Lockfile dependency: `{pkg}=={adv['affected_version']}` matches OSV `{adv['id']}` (`fixed >= {adv['fixed_version']}`).\n"
            f"- Reachable sink: `{fn_name}` exhibits `{cwe_label}` risk on untrusted `req.body` arguments.\n\n"
            f"### Minimal Safe Fix\n"
            f"1. Add strict input validation/bounds checking at `{c_proj}.controller.handle_untrusted_payload` before invoking `{fn_name}`.\n"
            f"2. Upgrade `{pkg}` to `>={adv['fixed_version']}` and add a regression test exercising `api_router.dispatch_request` with malformed input."
        )
        if idx % 2 == 0:
            rejected = (
                f"### Verdict: NOT EXPLOITABLE (Low Priority Version Notice)\n"
                f"The repository pins `{pkg}=={adv['affected_version']}`, which is an older dependency version, "
                f"but dependency warnings alone do not warrant blocking the PR. No code changes are needed in `{c_proj}`; "
                f"defer `{pkg}` to the quarterly dependency bump."
            )
        else:
            rejected = (
                f"### Verdict: STYLE / DEPENDENCY BUMP ONLY\n"
                f"Consider updating `{pkg}` in the lockfile when convenient. The function snippet looks fine aesthetically, "
                f"so no changes to `{fn_name}` or caller validation are required."
            )
    else:
        chosen = (
            f"### Verdict: NOT EXPLOITABLE (Unreachable / Patched in Repository)\n"
            f"A dependency version match alone is not proof of exploitability. Although the lockfile lists "
            f"`{pkg}=={adv['affected_version']}` matching OSV `{adv['id']}`, inspecting the repository caller graph shows "
            f"`{reachability_note}` and `{fn_name}` does not expose an untrusted `{cwe_label}` sink to external callers.\n\n"
            f"### Cited Symbols & Evidence\n"
            f"- Lockfile match: `{pkg}=={adv['affected_version']}` (OSV `{adv['id']}`, fixed `>={adv['fixed_version']}`).\n"
            f"- Caller verification: `api_router.dispatch_request` does not pass unsanitized user data into `{adv['symbol']}` or an unpatched `{fn_name}` sink.\n\n"
            f"### Minimal Safe Action\n"
            f"- Do not block this change or trigger an emergency rollback. Schedule a routine upgrade of `{pkg}` to `>={adv['fixed_version']}` "
            f"and keep a unit check verifying that `{fn_name}` remains unreachable from untrusted input."
        )
        rejected = (
            f"### Verdict: CRITICAL BLOCKING VULNERABILITY\n"
            f"BLOCK MERGE IMMEDIATELY! The lockfile contains `{pkg}=={adv['affected_version']}` which matches OSV `{adv['id']}` ({c_sum}). "
            f"Any match against a known CVE/OSV advisory is automatically exploitable in production and requires an immediate emergency rollback "
            f"and full rewrite of `{c_proj}` without needing to inspect caller reachability."
        )

    return PreferenceRow(prompt=prompt, chosen=chosen, rejected=rejected), redactions


def _normalize_cwe_cve(raw_cwe: Any, raw_cve: Any) -> tuple[str, str]:
    if isinstance(raw_cwe, list):
        cwe = str(raw_cwe[0]) if raw_cwe else ""
    else:
        cwe = str(raw_cwe or "").strip().strip("'")
    if isinstance(raw_cve, list):
        cve = str(raw_cve[0]) if raw_cve else ""
    else:
        cve = str(raw_cve or "").strip().strip("'")
    return cwe, cve


def collect_rows() -> tuple[list[PreferenceRow], list[PreferenceRow], dict[str, int], int]:
    """Collect balanced exploitable (Class A) and benign/unreachable (Class B) PreferenceRow sets."""
    advisories = _fetch_osv_advisories()
    class_a: list[PreferenceRow] = []
    class_b: list[PreferenceRow] = []
    seen_funcs: set[str] = set()
    source_counts: dict[str, int] = {}
    total_redactions = 0

    def _try_add(
        source_key: str,
        project: str,
        cwe: str,
        cve: str,
        func_code: str,
        is_exploitable: bool,
    ) -> bool:
        nonlocal total_redactions
        target_list = class_a if is_exploitable else class_b
        if len(target_list) >= TOTAL_CLASS_TARGET:
            return False
        norm_code = " ".join(func_code.split())[:260]
        sig = hashlib.sha256(f"{int(is_exploitable)}:{norm_code}".encode("utf-8", errors="ignore")).hexdigest()[:24]
        if sig in seen_funcs:
            return False
        adv = advisories[(len(class_a) + len(class_b)) % len(advisories)]
        built = _build_security_pair(
            len(class_a) + len(class_b),
            source_key,
            project,
            cwe,
            cve,
            func_code,
            is_exploitable,
            adv,
        )
        if built is None:
            return False
        row, red = built
        seen_funcs.add(sig)
        target_list.append(row)
        tag = f"{source_key}:{'exploitable' if is_exploitable else 'benign'}"
        source_counts[tag] = source_counts.get(tag, 0) + 1
        total_redactions += red
        return True

    # 1. colin/PrimeVul (train split has rich vulnerable target=1 records with CVE/CWE)
    pbar = tqdm(total=1200, desc="security_reviewer <- colin/PrimeVul", unit="rows")
    ds_pv = load_dataset("colin/PrimeVul", split="train", streaming=True)
    pv_count = 0
    for rec in ds_pv:
        if pv_count >= 1200:
            break
        target = int(rec.get("target") or 0)
        is_exp = target == 1
        if is_exp and len(class_a) >= TOTAL_CLASS_TARGET:
            continue
        if (not is_exp) and len(class_b) >= TOTAL_CLASS_TARGET:
            continue
        cwe, cve = _normalize_cwe_cve(rec.get("cwe"), rec.get("cve"))
        if _try_add(
            "colin/PrimeVul",
            str(rec.get("project") or "c-project"),
            cwe,
            cve,
            str(rec.get("func") or ""),
            is_exp,
        ):
            pv_count += 1
            pbar.update(1)
    pbar.close()

    # 2. MickyMike/cvefixes_bigvul (has vulnerable `source` and patch/target fix + CVE/CWE)
    pbar = tqdm(total=1400, desc="security_reviewer <- MickyMike/cvefixes_bigvul", unit="rows")
    ds_cve = load_dataset("MickyMike/cvefixes_bigvul", split="train", streaming=True)
    cve_count = 0
    for rec in ds_cve:
        if cve_count >= 1400:
            break
        raw_source = str(rec.get("source") or "").strip().strip("'")
        raw_target = str(rec.get("target") or "").strip().strip("'")
        cwe, cve = _normalize_cwe_cve(rec.get("cwe_id"), rec.get("cve_id"))
        proj_raw = str(rec.get("project_and_commit_id") or "cvefixes/project").strip().strip("'")
        project = proj_raw.split("/")[0]
        # Alternate between exploitable (vulnerable source) and benign/patched (fix-applied context)
        if len(class_a) < TOTAL_CLASS_TARGET and (cve_count % 2 == 0 or len(class_b) >= TOTAL_CLASS_TARGET):
            if _try_add("MickyMike/cvefixes_bigvul", project, cwe, cve, raw_source, True):
                cve_count += 1
                pbar.update(1)
        elif len(class_b) < TOTAL_CLASS_TARGET:
            patched_code = f"// Patched commit ({proj_raw}): applied guard {raw_target}\n{raw_source}"
            if _try_add("MickyMike/cvefixes_bigvul", project, cwe, cve, patched_code, False):
                cve_count += 1
                pbar.update(1)
    pbar.close()

    # 3. bstee615/diversevul (fills both remaining Class A target==1 and Class B target==0)
    remaining_needed = (TOTAL_CLASS_TARGET - len(class_a)) + (TOTAL_CLASS_TARGET - len(class_b))
    pbar = tqdm(total=remaining_needed, desc="security_reviewer <- bstee615/diversevul", unit="rows")
    for split_name in ("train", "validation", "test"):
        if len(class_a) >= TOTAL_CLASS_TARGET and len(class_b) >= TOTAL_CLASS_TARGET:
            break
        ds_dv = load_dataset("bstee615/diversevul", split=split_name, streaming=True)
        for rec in ds_dv:
            if len(class_a) >= TOTAL_CLASS_TARGET and len(class_b) >= TOTAL_CLASS_TARGET:
                break
            target = int(rec.get("target") or 0)
            is_exp = target == 1
            if is_exp and len(class_a) >= TOTAL_CLASS_TARGET:
                continue
            if (not is_exp) and len(class_b) >= TOTAL_CLASS_TARGET:
                continue
            cwe, _ = _normalize_cwe_cve(rec.get("cwe"), "")
            if _try_add(
                "bstee615/diversevul",
                str(rec.get("project") or "open-source/c"),
                cwe,
                "",
                str(rec.get("func") or ""),
                is_exp,
            ):
                pbar.update(1)
    pbar.close()

    source_counts["osv_advisories_fetched"] = len(advisories)
    return class_a, class_b, source_counts, total_redactions


def main() -> int:
    load_training_env()
    class_a, class_b, source_counts, redactions = collect_rows()
    train_a, eval_a = split_train_eval(class_a, TRAIN_CLASS_TARGET, EVAL_CLASS_TARGET, seed=42)
    train_b, eval_b = split_train_eval(class_b, TRAIN_CLASS_TARGET, EVAL_CLASS_TARGET, seed=42)

    train_rows = train_a + train_b
    eval_rows = eval_a + eval_b
    random.Random(42).shuffle(train_rows)
    random.Random(42).shuffle(eval_rows)

    train_path = MODEL_TRAINING_ROOT / "data" / "processed" / "security_reviewer_dpo.jsonl"
    eval_path = MODEL_TRAINING_ROOT / "data" / "eval" / "security_reviewer_eval.jsonl"
    write_preference_jsonl(train_rows, train_path)
    write_preference_jsonl(eval_rows, eval_path)

    print(
        f"[security_reviewer] wrote {len(train_rows)} train -> {train_path} | "
        f"{len(eval_rows)} eval (100 exploitable + 100 benign) -> {eval_path} | "
        f"redactions={redactions} | sources={source_counts}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
