"""Wave 1 Data Prep — Agent 1: router (held-out eval + local backup train set)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from datasets import load_dataset
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from training.schemas import InstructionRow, RouterCompletion
from training.scrub import scrub_with_count
from training.utils import (
    MODEL_TRAINING_ROOT,
    extract_diff_paths,
    extract_symbols_and_paths,
    load_training_env,
    split_train_eval,
    truncate_bounded,
    write_instruction_jsonl,
)

TRAIN_TARGET = 1200
EVAL_TARGET = 200
TOTAL_TARGET = TRAIN_TARGET + EVAL_TARGET

SECURITY_KEYWORDS = (
    "cve",
    "xss",
    "csrf",
    "sql injection",
    "injection",
    "auth",
    "oauth",
    "jwt",
    "token",
    "secret",
    "password",
    "permission",
    "privilege",
    "sanitize",
    "escape",
    "crypto",
    "tls",
    "ssl",
    "vulnerability",
)
DB_KEYWORDS = ("migration", "alembic", "schema", "foreign key", "sqlite", "postgres", "alter table", "dynamodb")
IAC_KEYWORDS = (
    "dockerfile",
    "terraform",
    "kubernetes",
    "helm",
    "github action",
    "ci/cd",
    "pyproject.toml",
    "setup.cfg",
    "requirements.txt",
    "tox.ini",
    "makefile",
    "workflow",
    "deploy",
)
UI_KEYWORDS = ("react", "component", "css", "layout", "modal", "button", "frontend", "render", "browser", "dom")
DECISION_KEYWORDS = (
    "tradeoff",
    "trade-off",
    "breaking change",
    "backwards compatibility",
    "backward compatibility",
    "design decision",
    "alternatively",
    "which approach",
    "whether to",
    "option a",
)


def _classify_domain_agent(text_lower: str, paths: list[str]) -> str | None:
    joined_paths = " ".join(paths).lower()
    if any(ext in joined_paths for ext in (".sql", "migration", "alembic")) or any(
        kw in text_lower for kw in DB_KEYWORDS
    ):
        return "DBMigration"
    if any(
        tok in joined_paths for tok in ("dockerfile", ".github/", ".tf", "terraform", "helm", "k8s", ".yml", ".yaml")
    ) or any(kw in text_lower for kw in IAC_KEYWORDS):
        return "IaC"
    if any(ext in joined_paths for ext in (".tsx", ".jsx", ".css", ".scss", ".html", "frontend/")) or any(
        kw in text_lower for kw in UI_KEYWORDS
    ):
        return "FrontendUI"
    if any(kw in text_lower for kw in SECURITY_KEYWORDS):
        return "SecurityReviewer"
    if any(kw in text_lower for kw in DECISION_KEYWORDS) or text_lower.count("?") >= 3:
        return "HumanDecision"
    return None


def _build_router_row(
    idx: int,
    source_name: str,
    repo: str,
    problem: str,
    patch: str,
    fail_to_pass: str,
    agent_counts: dict[str, int],
) -> tuple[InstructionRow, str, int]:
    cleaned_problem, redactions_1 = scrub_with_count(problem)
    cleaned_patch, redactions_2 = scrub_with_count(patch)
    total_redactions = redactions_1 + redactions_2

    paths = extract_diff_paths(cleaned_patch, limit=6)
    symbols = extract_symbols_and_paths(cleaned_problem, cleaned_patch, limit=6)
    text_lower = cleaned_problem.lower()
    domain_agent = _classify_domain_agent(text_lower, paths)
    if domain_agent == "SecurityReviewer" and agent_counts.get("SecurityReviewer", 0) >= 160:
        domain_agent = None

    # Use specialized domain routing when task matches a specialist domain;
    # otherwise rotate cleanly across the core maintenance workflow stages.
    core_stages = ("intake", "parsed", "evidence_gathered", "patch_generated", "tests_ready")
    if domain_agent is not None and (domain_agent != "SecurityReviewer" or idx % 2 == 0):
        stage = "evidence_gathered"
        next_agent = domain_agent
    else:
        stage = core_stages[idx % len(core_stages)]
        next_agent = {
            "intake": "TicketParser",
            "parsed": "Retriever",
            "evidence_gathered": "Implementer",
            "patch_generated": "TestWriter",
            "tests_ready": "Reviewer",
        }[stage]

    if next_agent in ("SecurityReviewer", "DBMigration", "IaC"):
        risk_level = "high"
    elif next_agent in ("Implementer", "HumanDecision", "FrontendUI") or len(paths) >= 3:
        risk_level = "medium"
    else:
        risk_level = "low"

    base_budget = {
        "TicketParser": 2048,
        "Retriever": 4096,
        "Implementer": 8192,
        "TestWriter": 6144,
        "Reviewer": 6144,
        "SecurityReviewer": 8192,
        "DBMigration": 8192,
        "IaC": 6144,
        "FrontendUI": 6144,
        "HumanDecision": 4096,
    }[next_agent]
    token_budget = min(16384, base_budget + (2048 if len(cleaned_problem) > 1500 else 0))

    reason_map = {
        "TicketParser": f"Raw maintenance intake from {repo} is not yet structured into priority, change type, and candidate symbols.",
        "Retriever": f"Ticket is parsed for {repo}, but snapshot-bound call sites and file evidence have not been retrieved yet.",
        "Implementer": f"Candidate evidence ({', '.join(symbols[:3]) or repo}) is ready for an isolated patch plan and edit.",
        "TestWriter": f"Code changes touch {', '.join(paths[:2]) or repo} without completed failing-before/passing-after regression test coverage.",
        "Reviewer": f"Patch and regression test targets ({len(paths)} file(s)) are prepared and require an independent review pass.",
        "SecurityReviewer": f"Task involves security-sensitive boundaries (auth/tokens/sanitization) in {repo} requiring dedicated security review.",
        "DBMigration": f"Task touches database schema or migration state ({', '.join(paths[:2]) or repo}) requiring migration safety verification.",
        "IaC": f"Task modifies build, container, or deployment configuration ({', '.join(paths[:2]) or repo}) requiring IaC specialist handling.",
        "FrontendUI": f"Task affects client UI rendering or frontend components ({', '.join(paths[:2]) or repo}) requiring FrontendUI routing.",
        "HumanDecision": f"Task in {repo} contains unresolved product or compatibility trade-offs requiring a human decision card.",
    }

    stage_context = {
        "intake": "Ticket parsed: false | Evidence retrieved: false | Patch prepared: false | Tests verified: false",
        "parsed": f"Ticket parsed: true | Candidate hints: {', '.join(symbols[:4]) or 'none'} | Evidence retrieved: false",
        "evidence_gathered": (
            f"Ticket parsed: true | Candidate files: {', '.join(paths[:4]) or ', '.join(symbols[:4]) or 'unknown'} "
            "| Patch prepared: false"
        ),
        "patch_generated": (
            f"Ticket parsed: true | Modified files: {', '.join(paths[:4]) or '1 file'} "
            "| Regression tests written: false"
        ),
        "tests_ready": (
            f"Ticket parsed: true | Modified files: {', '.join(paths[:4]) or '1 file'} "
            f"| Target tests: {truncate_bounded(fail_to_pass or 'pending verification', 180)} | Review status: pending"
        ),
    }[stage]

    prompt = (
        "Route this Duck Tape maintenance job to the next specialist agent.\n"
        f"Source Dataset: {source_name}\n"
        f"Repository: {repo}\n"
        f"Workflow Stage: {stage}\n"
        f"State: {stage_context}\n\n"
        f"Problem Statement:\n{truncate_bounded(cleaned_problem, 2200)}\n\n"
        "Respond with strict JSON matching: "
        '{"next_agent": str, "routing_reason": str, "risk_level": "low|medium|high", "token_budget": int}.'
    )

    completion_obj = RouterCompletion(
        next_agent=next_agent,
        routing_reason=reason_map[next_agent],
        risk_level=risk_level,
        token_budget=token_budget,
    )
    completion_str = json.dumps(completion_obj.model_dump(), ensure_ascii=False)
    RouterCompletion.model_validate(json.loads(completion_str))
    row = InstructionRow(prompt=prompt, completion=completion_str)
    return row, next_agent, total_redactions


def collect_rows() -> tuple[list[InstructionRow], dict[str, int], int]:
    """Stream rows from SWE-smith and SWE-Gym up to TOTAL_TARGET."""
    rows: list[InstructionRow] = []
    seen_ids: set[str] = set()
    agent_counts: dict[str, int] = {}
    total_redactions = 0

    sources = (
        ("SWE-bench/SWE-smith", "train", 750),
        ("SWE-Gym/SWE-Gym", "train", 650),
    )
    global_idx = 0
    for ds_name, split_name, quota in sources:
        stream = load_dataset(ds_name, split=split_name, streaming=True)
        collected = 0
        pbar = tqdm(total=quota, desc=f"router <- {ds_name}", unit="rows")
        for record in stream:
            if collected >= quota:
                break
            instance_id = str(record.get("instance_id") or "")
            problem = str(record.get("problem_statement") or "").strip()
            if not problem or len(problem) < 40 or instance_id in seen_ids:
                continue
            seen_ids.add(instance_id)
            repo = str(record.get("repo") or "unknown/repo")
            patch = str(record.get("patch") or "")
            fail_to_pass = str(record.get("FAIL_TO_PASS") or "")

            row, next_agent, redactions = _build_router_row(
                global_idx, ds_name, repo, problem, patch, fail_to_pass, agent_counts
            )
            rows.append(row)
            agent_counts[next_agent] = agent_counts.get(next_agent, 0) + 1
            total_redactions += redactions
            collected += 1
            global_idx += 1
            pbar.update(1)
        pbar.close()

    return rows, agent_counts, total_redactions


def main() -> int:
    load_training_env()
    rows, agent_counts, redactions = collect_rows()
    train_rows, eval_rows = split_train_eval(rows, TRAIN_TARGET, EVAL_TARGET, seed=42)

    train_path = MODEL_TRAINING_ROOT / "data" / "processed" / "router.jsonl"
    eval_path = MODEL_TRAINING_ROOT / "data" / "eval" / "router_eval.jsonl"
    write_instruction_jsonl(train_rows, train_path)
    write_instruction_jsonl(eval_rows, eval_path)

    print(
        f"[router] wrote {len(train_rows)} train -> {train_path} | "
        f"{len(eval_rows)} eval -> {eval_path} | redactions={redactions} | dist={agent_counts}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
