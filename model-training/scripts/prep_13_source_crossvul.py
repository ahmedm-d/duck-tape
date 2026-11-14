"""Extract function-level vulnerable/fixed pairs (python/javascript/typescript) from hitoshura25/crossvul."""
from __future__ import annotations

import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))
import prep_13_researcher as p  # noqa: E402

DATASET = "hitoshura25/crossvul"
LICENCE = "apache-2.0 (dataset card; derived from CrossVul, Zenodo 4734050)"
OUT = HERE.parent / "data" / "raw" / "researcher_pairs_crossvul.jsonl"
EXT = {"python": ".py", "javascript": ".js", "typescript": ".ts"}
MAX_FUNC_CHARS = 12000


def main() -> int:
    from datasets import load_dataset

    start = time.time()
    ds = load_dataset(DATASET, split="train", streaming=True)
    seen: set[str] = set()
    records: list[dict] = []
    rows = kept_rows = invalid = dup = 0
    for row in ds:
        rows += 1
        lang = str(row.get("language") or "").lower()
        if lang not in EXT:
            continue
        kept_rows += 1
        rec = dict(row)
        rec["language"] = lang
        for pair in p.crossvul_function_pairs(rec):
            v, f = pair.vulnerable, pair.fixed
            if len(v) < 20 or len(f) < 20 or len(v) > MAX_FUNC_CHARS or len(f) > MAX_FUNC_CHARS:
                continue
            if p.normalize_code(v) == p.normalize_code(f):
                continue
            key = p.normalize_code(v)
            if key in seen:
                dup += 1
                continue
            fid = str(row.get("file_pair_id") or "")
            fname = fid if Path(fid).suffix else fid + EXT[lang]
            out = {
                "source_id": DATASET,
                "licence": LICENCE,
                "project": "crossvul",  # validate_pair requires a non-empty project
                "cve": None,
                "cwe": pair.cwe,
                "file_name": fname,
                "language": lang,
                "vulnerable_code": v,
                "fixed_code": f,
                "commit": None,
                "pair_id": pair.pair_id,
            }
            if row.get("cwe_description"):
                out["cwe_name"] = str(row["cwe_description"])
            if p.validate_pair(out):
                invalid += 1
                continue
            seen.add(key)
            records.append(out)
    n = p.write_pair_records(records, OUT)
    langs = Counter(r["language"] for r in records)
    print(f"rows={rows} lang_rows={kept_rows} written={n} invalid={invalid} dup={dup}")
    print("per-language:", dict(langs))
    print("distinct CWEs:", len({r["cwe"] for r in records}))
    print(f"seconds: {time.time() - start:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
