"""Wave 3 Data Prep — Agent 12: retriever (ContrastiveTripletRow)."""
from __future__ import annotations

import hashlib
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from datasets import load_dataset
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from training.schemas import ContrastiveTripletRow
from training.scrub import scrub_with_count
from training.utils import (
    MODEL_TRAINING_ROOT,
    extract_diff_paths,
    extract_diff_symbols,
    load_training_env,
    split_train_eval,
    truncate_bounded,
    write_triplet_jsonl,
)

TRAIN_TARGET = 5000
EVAL_TARGET = 300
TOTAL_TARGET = TRAIN_TARGET + EVAL_TARGET

_TEST_FILE_RE = re.compile(r"([A-Za-z0-9_./-]+\.py)::")


def _split_diff_by_file(diff_text: str) -> list[tuple[str, str]]:
    """Split a unified git diff into (file_path, file_diff_chunk) tuples."""
    chunks = re.split(r"(?=^diff --git )", diff_text or "", flags=re.MULTILINE)
    results: list[tuple[str, str]] = []
    for ch in chunks:
        if not ch.strip():
            continue
        paths = extract_diff_paths(ch, limit=1)
        if paths:
            results.append((paths[0], ch))
    return results


def _collect_swe_triplets(quota: int) -> tuple[list[ContrastiveTripletRow], dict[str, int], int]:
    """Build same-repo hard-negative triplets from SWE-bench_Verified + SWE-Gym."""
    repo_pool: dict[str, list[tuple[str, str]]] = defaultdict(list)
    instances: list[dict[str, Any]] = []
    redactions = 0

    for label, ds_name, split_name, max_read in (
        ("SWE-bench_Verified", "princeton-nlp/SWE-bench_Verified", "test", 500),
        ("SWE-Gym", "SWE-Gym/SWE-Gym", "train", 1600),
    ):
        ds = load_dataset(ds_name, split=split_name, streaming=True)
        read_n = 0
        pbar = tqdm(total=max_read, desc=f"retriever index <- {label}", unit="rows")
        for rec in ds:
            if read_n >= max_read:
                break
            repo = str(rec.get("repo") or "").strip()
            problem = str(rec.get("problem_statement") or "").strip()
            patch = str(rec.get("patch") or "").strip()
            test_patch = str(rec.get("test_patch") or "").strip()
            inst_id = str(rec.get("instance_id") or "").strip()
            if not repo or len(problem) < 40 or len(patch) < 30:
                continue

            file_chunks = _split_diff_by_file(patch)
            if not file_chunks:
                continue

            for fpath, chunk in file_chunks:
                syms = extract_diff_symbols(chunk, limit=5)
                doc = (
                    f"Repository: {repo}\n"
                    f"File: {fpath}\n"
                    + (f"Symbols: {', '.join(syms)}\n" if syms else "")
                    + f"Code Snippet:\n{truncate_bounded(chunk, 1200)}"
                )
                repo_pool[repo].append((fpath, doc))

            # Also register test files from test_patch & PASS_TO_PASS into same-repo pool
            for tpath, tchunk in _split_diff_by_file(test_patch):
                tdoc = f"Repository: {repo}\nFile: {tpath}\nCode Snippet:\n{truncate_bounded(tchunk, 900)}"
                repo_pool[repo].append((tpath, tdoc))
            for tfile in _TEST_FILE_RE.findall(str(rec.get("PASS_TO_PASS") or ""))[:4]:
                repo_pool[repo].append((tfile, f"Repository: {repo}\nFile: {tfile}\nModule Role: Existing passing regression test suite"))

            instances.append(
                {
                    "source": label,
                    "repo": repo,
                    "instance_id": inst_id,
                    "problem": problem,
                    "file_chunks": file_chunks,
                }
            )
            read_n += 1
            pbar.update(1)
        pbar.close()

    # Deduplicate per-repo pool by file path (keeping up to 40 distinct file documents per repo)
    dedup_pool: dict[str, list[tuple[str, str]]] = {}
    for repo, items in repo_pool.items():
        seen_paths: set[str] = set()
        uniq: list[tuple[str, str]] = []
        for fpath, doc in items:
            if fpath not in seen_paths:
                seen_paths.add(fpath)
                uniq.append((fpath, doc))
                if len(uniq) >= 40:
                    break
        dedup_pool[repo] = uniq

    triplets: list[ContrastiveTripletRow] = []
    source_counts: dict[str, int] = {}
    seen_queries: set[str] = set()

    for inst in instances:
        if len(triplets) >= quota:
            break
        repo = inst["repo"]
        changed_paths = {fp for fp, _ in inst["file_chunks"]}
        neg_candidates = [doc for fp, doc in dedup_pool.get(repo, []) if fp not in changed_paths]
        if len(neg_candidates) < 2:
            continue

        pos_docs = []
        for fpath, chunk in inst["file_chunks"][:3]:
            syms = extract_diff_symbols(chunk, limit=6)
            doc = (
                f"Repository: {repo}\n"
                f"File: {fpath}\n"
                + (f"Symbols: {', '.join(syms)}\n" if syms else "")
                + f"Code Snippet:\n{truncate_bounded(chunk, 1200)}"
            )
            c_doc, r_doc = scrub_with_count(doc)
            redactions += r_doc
            pos_docs.append(c_doc)

        neg_docs = []
        for ndoc in neg_candidates[:3]:
            c_neg, r_neg = scrub_with_count(ndoc)
            redactions += r_neg
            neg_docs.append(c_neg)

        c_prob, r_p = scrub_with_count(inst["problem"])
        redactions += r_p
        query = f"[{repo}] Find files relevant to issue ({inst['instance_id']}):\n{truncate_bounded(c_prob, 1400)}"
        q_hash = hashlib.sha256(query.encode("utf-8", errors="ignore")).hexdigest()[:24]
        if q_hash in seen_queries:
            continue
        seen_queries.add(q_hash)

        triplets.append(ContrastiveTripletRow(query=query, pos=pos_docs, neg=neg_docs))
        src = inst["source"]
        source_counts[src] = source_counts.get(src, 0) + 1

    return triplets, source_counts, redactions


def _collect_codesearchnet_triplets(quota: int) -> tuple[list[ContrastiveTripletRow], int]:
    """Stream code-search-net/code_search_net and build same-repo hard-negative triplets."""
    ds = load_dataset("code-search-net/code_search_net", "all", split="train", streaming=True)
    # Accumulate functions grouped by repo so negatives come from the SAME repository whenever possible
    by_repo: dict[str, list[dict[str, str]]] = defaultdict(list)
    by_lang: dict[str, list[dict[str, str]]] = defaultdict(list)

    scan_target = quota * 3
    pbar = tqdm(total=scan_target, desc="retriever scan <- code_search_net", unit="funcs")
    scanned = 0
    for rec in ds:
        if scanned >= scan_target and len(by_repo) >= quota:
            break
        if scanned >= quota * 5:
            break
        doc = " ".join(str(rec.get("func_documentation_string") or "").split()).strip()
        code = str(rec.get("func_code_string") or "").strip()
        repo = str(rec.get("repository_name") or "").strip()
        fpath = str(rec.get("func_path_in_repository") or "").strip()
        fname = str(rec.get("func_name") or "").strip()
        lang = str(rec.get("language") or "python").strip()
        if len(doc) < 25 or len(code) < 50 or not repo or not fpath:
            continue
        item = {
            "repo": repo,
            "fpath": fpath,
            "fname": fname,
            "lang": lang,
            "doc": doc,
            "code": code,
        }
        if len(by_repo[repo]) < 12:
            by_repo[repo].append(item)
        if len(by_lang[lang]) < 400:
            by_lang[lang].append(item)
        scanned += 1
        if scanned <= scan_target:
            pbar.update(1)
    pbar.close()

    triplets: list[ContrastiveTripletRow] = []
    seen_queries: set[str] = set()
    redactions = 0

    # First prioritize repositories with >= 3 distinct functions for pure same-repo hard negatives
    multi_repos = [items for items in by_repo.values() if len(items) >= 3]
    single_repos = [items for items in by_repo.values() if len(items) < 3]

    for items in multi_repos + single_repos:
        for idx, item in enumerate(items):
            if len(triplets) >= quota:
                break
            same_repo_negs = [
                other
                for j, other in enumerate(items)
                if j != idx and (other["fpath"] != item["fpath"] or other["fname"] != item["fname"])
            ]
            negs_src = same_repo_negs[:3]
            if len(negs_src) < 2:
                # Pad with same-language functions from a different repo only if same-repo had < 2 other funcs
                for cand in by_lang.get(item["lang"], []):
                    if cand["repo"] != item["repo"]:
                        negs_src.append(cand)
                        if len(negs_src) >= 2:
                            break
            if len(negs_src) < 2:
                continue

            c_doc, r1 = scrub_with_count(item["doc"])
            c_code, r2 = scrub_with_count(item["code"])
            redactions += r1 + r2
            query = f"[{item['repo']} | {item['lang']}] Locate symbol implementing: {truncate_bounded(c_doc, 500)}"
            q_hash = hashlib.sha256(query.encode("utf-8", errors="ignore")).hexdigest()[:24]
            if q_hash in seen_queries:
                continue

            pos_str = (
                f"Repository: {item['repo']}\n"
                f"File: {item['fpath']}\n"
                f"Symbol: {item['fname']}\n"
                f"Code:\n{truncate_bounded(c_code, 1200)}"
            )
            neg_strs: list[str] = []
            for n in negs_src[:3]:
                nc, rn = scrub_with_count(n["code"])
                redactions += rn
                neg_strs.append(
                    f"Repository: {n['repo']}\n"
                    f"File: {n['fpath']}\n"
                    f"Symbol: {n['fname']}\n"
                    f"Code:\n{truncate_bounded(nc, 1000)}"
                )

            seen_queries.add(q_hash)
            triplets.append(ContrastiveTripletRow(query=query, pos=[pos_str], neg=neg_strs))
        if len(triplets) >= quota:
            break

    return triplets, redactions


def _collect_cosqa_triplets(quota: int) -> tuple[list[ContrastiveTripletRow], int]:
    """Stream gonglinyuan/CoSQA positive and hard-negative (query, code) rows."""
    ds = load_dataset("gonglinyuan/CoSQA", split="train", streaming=True)
    pos_items: list[dict[str, str]] = []
    neg_pool: list[dict[str, str]] = []
    neg_by_query: dict[str, list[str]] = defaultdict(list)

    for rec in ds:
        query_txt = " ".join(str(rec.get("doc") or "").split()).strip()
        code_txt = str(rec.get("code") or "").strip()
        if len(query_txt) < 8 or len(code_txt) < 40:
            continue
        label = int(rec.get("label") or 0)
        if label == 1:
            pos_items.append({"query": query_txt, "code": code_txt, "idx": str(rec.get("idx") or "")})
        else:
            neg_by_query[query_txt.lower()].append(code_txt)
            if len(neg_pool) < 4000:
                neg_pool.append({"query": query_txt, "code": code_txt})

    triplets: list[ContrastiveTripletRow] = []
    seen_queries: set[str] = set()
    redactions = 0
    pool_len = len(neg_pool)

    pbar = tqdm(total=quota, desc="retriever <- gonglinyuan/CoSQA", unit="triplets")
    for idx, item in enumerate(pos_items):
        if len(triplets) >= quota:
            break
        q_raw = item["query"]
        c_q, r1 = scrub_with_count(q_raw)
        c_pos, r2 = scrub_with_count(item["code"])
        redactions += r1 + r2

        # Collect same-query hard negatives from CoSQA label=0 first, then topically close negatives
        neg_codes: list[str] = []
        for candidate in neg_by_query.get(q_raw.lower(), []):
            if candidate != item["code"] and candidate not in neg_codes:
                neg_codes.append(candidate)
                if len(neg_codes) >= 3:
                    break
        offset = 0
        while len(neg_codes) < 2 and pool_len > 0 and offset < 50:
            cand = neg_pool[(idx * 7 + offset) % pool_len]["code"]
            offset += 1
            if cand != item["code"] and cand not in neg_codes:
                neg_codes.append(cand)
        if len(neg_codes) < 2:
            continue

        query = f"[Python | CoSQA] Find function matching query: {c_q}"
        q_hash = hashlib.sha256(query.encode("utf-8", errors="ignore")).hexdigest()[:24]
        if q_hash in seen_queries:
            continue

        neg_formatted: list[str] = []
        for ncode in neg_codes[:3]:
            c_n, rn = scrub_with_count(ncode)
            redactions += rn
            neg_formatted.append(f"Language: python\nCode:\n{truncate_bounded(c_n, 1000)}")

        seen_queries.add(q_hash)
        triplets.append(
            ContrastiveTripletRow(
                query=query,
                pos=[f"Language: python\nCode:\n{truncate_bounded(c_pos, 1200)}"],
                neg=neg_formatted,
            )
        )
        pbar.update(1)
    pbar.close()

    return triplets, redactions


def collect_rows() -> tuple[list[ContrastiveTripletRow], dict[str, int], int]:
    """Assemble 5,300 contrastive retrieval triplets across SWE-bench/SWE-Gym, CodeSearchNet, and CoSQA."""
    swe_rows, swe_sources, r1 = _collect_swe_triplets(1700)
    csn_quota = 2100
    csn_rows, r2 = _collect_codesearchnet_triplets(csn_quota)
    cosqa_quota = TOTAL_TARGET - len(swe_rows) - len(csn_rows)
    cosqa_rows, r3 = _collect_cosqa_triplets(cosqa_quota)

    all_rows = swe_rows + csn_rows + cosqa_rows
    extra_csn_added = 0
    if len(all_rows) < TOTAL_TARGET:
        extra_csn, r4 = _collect_codesearchnet_triplets(TOTAL_TARGET - len(all_rows) + 400)
        seen_q = {r.query for r in all_rows}
        for row in extra_csn:
            if row.query not in seen_q:
                seen_q.add(row.query)
                all_rows.append(row)
                extra_csn_added += 1
                if len(all_rows) >= TOTAL_TARGET:
                    break
        r2 += r4

    source_counts = dict(swe_sources)
    source_counts["code_search_net"] = len(csn_rows) + extra_csn_added
    source_counts["CoSQA"] = len(cosqa_rows)
    return all_rows, source_counts, (r1 + r2 + r3)


def main() -> int:
    load_training_env()
    rows, source_counts, redactions = collect_rows()
    train_rows, eval_rows = split_train_eval(rows, TRAIN_TARGET, EVAL_TARGET, seed=42)

    train_path = MODEL_TRAINING_ROOT / "data" / "processed" / "retriever_triplets.jsonl"
    eval_path = MODEL_TRAINING_ROOT / "data" / "eval" / "retriever_triplets_eval.jsonl"
    eval_alias_path = MODEL_TRAINING_ROOT / "data" / "eval" / "retriever_eval.jsonl"
    write_triplet_jsonl(train_rows, train_path)
    write_triplet_jsonl(eval_rows, eval_path)
    write_triplet_jsonl(eval_rows, eval_alias_path)

    print(
        f"[retriever] wrote {len(train_rows)} train -> {train_path} | "
        f"{len(eval_rows)} eval -> {eval_path} (and {eval_alias_path}) | "
        f"redactions={redactions} | sources={source_counts}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
