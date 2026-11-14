"""Extract Python/JS/TS vulnerable/fixed function pairs from starsofchance/CVEfixes_v1.0.8.

Streams training_set_part1/2.jsonl via HfFileSystem (no full download) and writes
data/raw/researcher_pairs_cvefixes.jsonl in the normalized pair schema of prep_13_researcher.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
import prep_13_researcher as p  # noqa: E402

DATASET = "starsofchance/CVEfixes_v1.0.8"
FILES = ["training_set_part1.jsonl", "training_set_part2.jsonl"]
LICENCE = "mit (dataset card; upstream CVEfixes attribution required)"
TARGET = 2500
MAX_SECONDS = 12 * 60
MAX_CHARS = 12_000
MAX_PER_COMMIT = 12
MAX_PER_CVE = 25
LANGS = {"python": "python", "javascript": "javascript", "typescript": "typescript"}
EXT = {".py": "python", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
       ".cjs": "javascript", ".ts": "typescript", ".tsx": "typescript"}
OUT = ROOT / "data" / "raw" / "researcher_pairs_cvefixes.jsonl"


def first_cwe(info) -> tuple[str | None, str | None]:
    for item in info or []:
        cid = str(item.get("cwe_id") or "")
        if re.fullmatch(r"CWE-\d+", cid):
            return cid, item.get("cwe_name")
    return None, None


def language_of(fc) -> str | None:
    lang = str(fc.get("programming_language") or "").strip().lower()
    if lang in LANGS:
        return LANGS[lang]
    path = str(fc.get("new_path") or fc.get("old_path") or "")
    return EXT.get(Path(path).suffix.lower()) if lang in ("", "nan", "none") else None


def pairs_from_cve(d: dict):
    cve = d.get("cve_id")
    if not isinstance(cve, str) or not re.fullmatch(r"CVE-\d{4}-\d{4,}", cve):
        cve = None
    cwe, cwe_name = first_cwe(d.get("cwe_info"))
    if not cwe:
        return
    for fix in d.get("fixes_info") or []:
        cd = fix.get("commit_details") or {}
        commit = cd.get("hash") or fix.get("hash")
        project = (cd.get("repository") or {}).get("repo_name")
        if not project or not commit:
            continue
        for fc in cd.get("file_changes") or []:
            lang = language_of(fc)
            if not lang:
                continue
            fname = fc.get("new_path") or fc.get("old_path")
            if re.search(r"\.min\.|[-.]bundle\.|/(dist|vendor|node_modules)/", str(fname)):
                continue
            fname = fname if isinstance(fname, str) and fname not in ("None", "nan") else fc.get("filename")
            old: dict[str, str] = {}
            new: dict[str, str] = {}
            for m in fc.get("method_changes") or []:
                code = m.get("code")
                if not isinstance(code, str) or len(code) > MAX_CHARS:
                    continue
                key = m.get("name") or m.get("signature")
                if not key:
                    continue
                target = old if str(m.get("before_change")) == "True" else new
                target.setdefault(key, code)
            for key, vcode in old.items():
                fcode = new.get(key)
                if fcode is None:
                    continue
                yield {
                    "source_id": DATASET, "licence": LICENCE, "project": project, "cve": cve,
                    "cwe": cwe, "cwe_name": cwe_name, "file_name": fname, "language": lang,
                    "vulnerable_code": vcode, "fixed_code": fcode, "commit": commit,
                }


def main() -> None:
    from huggingface_hub import HfFileSystem
    fs = HfFileSystem()
    t0 = time.time()
    seen: set[str] = set()
    records: list[dict] = []
    n = 0
    done = False
    per_commit: dict[str, int] = {}
    per_cve: dict[str, int] = {}
    for name in FILES:
        with fs.open(f"datasets/{DATASET}/{name}", "rb") as fh:
            for line in fh:
                n += 1
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                for rec in pairs_from_cve(d):
                    if p.validate_pair(rec):
                        continue
                    if per_commit.get(rec["commit"], 0) >= MAX_PER_COMMIT:
                        continue
                    if per_cve.get(str(rec["cve"]), 0) >= MAX_PER_CVE:
                        continue
                    key = p.normalize_code(rec["vulnerable_code"])
                    if key in seen:
                        continue
                    seen.add(key)
                    per_commit[rec["commit"]] = per_commit.get(rec["commit"], 0) + 1
                    per_cve[str(rec["cve"])] = per_cve.get(str(rec["cve"]), 0) + 1
                    records.append(rec)
                if n % 200 == 0:
                    print(f"{name} cves={n} pairs={len(records)} t={time.time()-t0:.0f}s", flush=True)
                if len(records) >= TARGET or time.time() - t0 > MAX_SECONDS:
                    done = True
                    break
        if done:
            break
    records = records[:TARGET]
    count = p.write_pair_records(records, OUT)
    langs: dict[str, int] = {}
    for r in records:
        langs[r["language"]] = langs.get(r["language"], 0) + 1
    print(f"WROTE {count} pairs, langs={langs}, cves={len({r['cve'] for r in records})}, "
          f"projects={len({r['project'] for r in records})}, cves_read={n}, t={time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
