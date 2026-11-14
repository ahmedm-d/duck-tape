"""Wave 3 Data Prep — Agent 9: db_migration (InstructionRow)."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from datasets import load_dataset
from huggingface_hub import hf_hub_download
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from training.schemas import InstructionRow
from training.scrub import scrub_with_count
from training.utils import (
    MODEL_TRAINING_ROOT,
    load_training_env,
    split_train_eval,
    truncate_bounded,
    write_instruction_jsonl,
)

TRAIN_TARGET = 5000
EVAL_TARGET = 200
TOTAL_TARGET = TRAIN_TARGET + EVAL_TARGET

REQUIRED_HEADINGS = (
    "### 1. Forward Migration",
    "### 2. Rollback / Down Migration",
    "### 3. Lock & Data-Loss Risk Assessment",
)

_TABLE_RE = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE|ALTER\s+TABLE|CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?|CREATE\s+(?:UNIQUE\s+)?INDEX(?:\s+CONCURRENTLY)?(?:\s+IF\s+NOT\s+EXISTS)?\s+\S+\s+ON)\s+[`\"']?([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
_DDL_TABLE_RE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"']?([A-Za-z_][A-Za-z0-9_]*)[`\"']?", re.IGNORECASE)
_COL_RE = re.compile(r"\bWHERE\s+[`\"']?(?:[A-Za-z_][A-Za-z0-9_]*\.)?([A-Za-z_][A-Za-z0-9_]*)\b", re.IGNORECASE)
_SLUG_RE = re.compile(r"[^a-z0-9_]+")


def _slug(text: str, max_len: int = 40) -> str:
    cleaned = _SLUG_RE.sub("_", (text or "").lower()).strip("_")
    return (cleaned[:max_len].strip("_")) or "migration_step"


def _extract_tables(sql_text: str) -> list[str]:
    tables: list[str] = []
    seen: set[str] = set()
    for pat in (_DDL_TABLE_RE, _TABLE_RE):
        for match in pat.findall(sql_text or ""):
            t = match.strip()
            if t.lower() in {"select", "where", "set", "values", "null"}:
                continue
            if t not in seen:
                seen.add(t)
                tables.append(t)
    return tables or ["target_table"]


def _extract_filter_cols(sql_text: str) -> list[str]:
    cols: list[str] = []
    seen: set[str] = set()
    for match in _COL_RE.findall(sql_text or ""):
        c = match.strip()
        if c.lower() in {"not", "exists", "null", "true", "false"}:
            continue
        if c not in seen:
            seen.add(c)
            cols.append(c)
    return cols or ["id"]


def _assess_sql_risk(forward_code: str, dialect: str = "PostgreSQL") -> str:
    low = forward_code.lower()
    has_drop = any(tok in low for tok in ("drop table", "drop column", "truncate ", "delete from"))
    has_alter_type = "alter column" in low or "modify " in low or "change " in low
    has_not_null = "not null" in low and "alter table" in low
    has_concurrent = "concurrently" in low

    if has_drop:
        lock_note = (
            f"Acquires `ACCESS EXCLUSIVE` table lock in {dialect} during DDL execution; "
            "set `lock_timeout = '5s'` and execute in an off-peak maintenance window."
        )
        data_loss_note = (
            "HIGH DATA-LOSS RISK — destructive `DROP`/`TRUNCATE` statement detected. "
            "Take a point-in-time snapshot first and use a two-phase expand-and-contract deprecation window."
        )
    elif has_alter_type or has_not_null:
        lock_note = (
            f"Acquires `ACCESS EXCLUSIVE` lock while rewriting or validating column metadata in {dialect}; "
            "for large tables, add nullable column/NOT VALID constraint first, backfill in batches, then validate."
        )
        data_loss_note = (
            "MEDIUM RISK — type coercion or `NOT NULL` enforcement can fail or truncate on legacy rows; "
            "run a pre-flight `SELECT COUNT(*) WHERE col IS NULL` verification query."
        )
    elif "create index" in low or "create unique index" in low:
        lock_note = (
            f"{'Uses `CONCURRENTLY` (`SHARE UPDATE EXCLUSIVE`, non-blocking for reads/writes)' if has_concurrent else 'Standard `CREATE INDEX` blocks concurrent writes (`SHARE` lock); prefer `CREATE INDEX CONCURRENTLY` in PostgreSQL'}."
        )
        data_loss_note = (
            "LOW DATA-LOSS RISK — additive index/view change only; monitor disk headroom and build duration."
        )
    else:
        lock_note = (
            f"Metadata-only or additive DDL/view creation in {dialect} (`SHARE UPDATE EXCLUSIVE` / brief metadata lock); "
            "pair with `SET lock_timeout = '3s'` to avoid queuing behind long analytical queries."
        )
        data_loss_note = (
            "LOW DATA-LOSS RISK — additive migration without dropping existing columns or overwriting historical rows."
        )

    return (
        f"- **Lock Impact:** {lock_note}\n"
        f"- **Data-Loss Risk:** {data_loss_note}\n"
        "- **Verification Plan:** Dry-run forward and rollback migrations against an isolated staging snapshot, "
        "verify row counts/constraints, and confirm zero invalid indexes or orphaned foreign keys."
    )


def _derive_rollback_from_commit(old_contents: str, new_contents: str, file_path: str) -> tuple[str, str]:
    """Build concrete rollback script and language tag from a real migration commit."""
    low_path = file_path.lower()
    if low_path.endswith(".py"):
        lang = "python"
        if old_contents.strip():
            rollback = (
                "# Rollback to prior migration state / downgrade handler\n"
                "def downgrade() -> None:\n"
                "    # Restore prior schema state from previous revision\n"
                + "\n".join(f"    # {ln}" for ln in truncate_bounded(old_contents, 900).splitlines()[:18])
            )
        else:
            rollback = (
                "def downgrade() -> None:\n"
                "    # Reverse objects introduced in upgrade()\n"
                "    # Example: op.drop_index(...) / op.drop_column(...) / op.drop_table(...)\n"
                "    pass"
            )
        return lang, rollback

    if low_path.endswith(".prisma"):
        lang = "prisma"
        rollback = (
            "// Restore previous Prisma schema snapshot before `prisma migrate deploy`\n"
            + (truncate_bounded(old_contents, 1200) if old_contents.strip() else "// Revert added model/field entries")
        )
        return lang, rollback

    lang = "sql"
    if old_contents.strip():
        return lang, f"-- Revert `{file_path}` to previous revision\n{truncate_bounded(old_contents, 1400)}"

    # Derive inverse DDL statements from newly created objects
    inv_lines: list[str] = [f"-- Down migration reversing `{file_path}`", "BEGIN;"]
    for m in re.finditer(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([`\"']?[A-Za-z0-9_.]+[`\"']?)", new_contents, re.I):
        inv_lines.append(f"DROP TABLE IF EXISTS {m.group(1)} CASCADE;")
    for m in re.finditer(r"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+([`\"']?[A-Za-z0-9_.]+[`\"']?)", new_contents, re.I):
        inv_lines.append(f"DROP VIEW IF EXISTS {m.group(1)};")
    for m in re.finditer(r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?([`\"']?[A-Za-z0-9_.]+[`\"']?)", new_contents, re.I):
        inv_lines.append(f"DROP INDEX IF EXISTS {m.group(1)};")
    for m in re.finditer(r"ALTER\s+TABLE\s+([`\"']?[A-Za-z0-9_.]+[`\"']?)\s+ADD\s+(?:COLUMN\s+)?([`\"']?[A-Za-z0-9_]+[`\"']?)", new_contents, re.I):
        inv_lines.append(f"ALTER TABLE {m.group(1)} DROP COLUMN IF EXISTS {m.group(2)};")
    if len(inv_lines) == 2:
        inv_lines.append("-- Restore table/constraint snapshot from pre-migration backup")
    inv_lines.append("COMMIT;")
    return lang, "\n".join(inv_lines)


def _build_schema_migration_pair(
    idx: int,
    source_name: str,
    db_id: str,
    dialect: str,
    schema_ddl: str,
    question: str,
    sql_query: str,
    evidence: str = "",
) -> tuple[InstructionRow, int] | None:
    c_schema, r1 = scrub_with_count(schema_ddl)
    c_q, r2 = scrub_with_count(question)
    c_sql, r3 = scrub_with_count(sql_query)
    c_ev, r4 = scrub_with_count(evidence)
    redactions = r1 + r2 + r3 + r4

    if len(c_q.strip()) < 10 or len(c_sql.strip()) < 12:
        return None

    tables = _extract_tables(c_sql)
    primary_table = tables[0]
    cols = _extract_filter_cols(c_sql)
    primary_col = cols[0]
    view_name = f"v_{_slug(db_id, 18)}_{_slug(primary_table, 16)}_{idx % 97}"
    idx_name = f"idx_{_slug(primary_table, 20)}_{_slug(primary_col, 16)}"

    # Rotate across 4 real migration frameworks so the specialist masters all Steward targets
    framework_idx = idx % 4
    if framework_idx == 0:
        framework = f"Flyway SQL ({dialect})"
        code_lang = "sql"
        if dialect == "PostgreSQL":
            forward = (
                "SET lock_timeout = '5s';\n"
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {idx_name}\n"
                f"    ON \"{primary_table}\" (\"{primary_col}\");\n\n"
                f"CREATE OR REPLACE VIEW \"{view_name}\" AS\n"
                f"{c_sql.rstrip(';')};"
            )
            rollback = (
                f"DROP VIEW IF EXISTS \"{view_name}\";\n"
                f"DROP INDEX CONCURRENTLY IF EXISTS {idx_name};"
            )
        else:
            forward = (
                f"CREATE INDEX IF NOT EXISTS {idx_name} ON `{primary_table}` (`{primary_col}`);\n\n"
                f"CREATE VIEW `{view_name}` AS\n"
                f"{c_sql.rstrip(';')};"
            )
            rollback = (
                f"DROP VIEW IF EXISTS `{view_name}`;\n"
                f"DROP INDEX {idx_name} ON `{primary_table}`;"
            )
    elif framework_idx == 1:
        framework = "Alembic (SQLAlchemy)"
        code_lang = "python"
        escaped_sql = c_sql.replace('"""', '\\"\\"\\"')
        forward = (
            "from alembic import op\n\n"
            "def upgrade() -> None:\n"
            f"    op.create_index(\"{idx_name}\", \"{primary_table}\", [\"{primary_col}\"], unique=False, if_not_exists=True)\n"
            f"    op.execute(\"\"\"CREATE OR REPLACE VIEW {view_name} AS\n{escaped_sql.rstrip(';')}\"\"\")"
        )
        rollback = (
            "from alembic import op\n\n"
            "def downgrade() -> None:\n"
            f"    op.execute(\"DROP VIEW IF EXISTS {view_name}\")\n"
            f"    op.drop_index(\"{idx_name}\", table_name=\"{primary_table}\", if_exists=True)"
        )
    elif framework_idx == 2:
        framework = "Django Migrations"
        code_lang = "python"
        escaped_sql = c_sql.replace('"""', '\\"\\"\\"')
        forward = (
            "from django.db import migrations\n\n"
            "class Migration(migrations.Migration):\n"
            "    atomic = False\n"
            "    operations = [\n"
            "        migrations.RunSQL(\n"
            f"            sql=\"\"\"CREATE INDEX IF NOT EXISTS {idx_name} ON {primary_table} ({primary_col});\n"
            f"CREATE VIEW {view_name} AS {escaped_sql.rstrip(';')};\"\"\",\n"
            f"            reverse_sql=\"\"\"DROP VIEW IF EXISTS {view_name};\n"
            f"DROP INDEX IF EXISTS {idx_name};\"\"\",\n"
            "        )\n"
            "    ]"
        )
        rollback = (
            "# Django reverses RunSQL using `reverse_sql` (`python manage.py migrate <app> <previous_rev>`):\n"
            f"DROP VIEW IF EXISTS {view_name};\n"
            f"DROP INDEX IF EXISTS {idx_name};"
        )
    else:
        framework = f"Prisma Migrate ({dialect})"
        code_lang = "sql"
        forward = (
            "-- prisma/migrations/migration.sql\n"
            f"CREATE INDEX IF NOT EXISTS \"{idx_name}\" ON \"{primary_table}\"(\"{primary_col}\");\n"
            f"CREATE OR REPLACE VIEW \"{view_name}\" AS\n"
            f"{c_sql.rstrip(';')};"
        )
        rollback = (
            "-- prisma/migrations/down.sql\n"
            f"DROP VIEW IF EXISTS \"{view_name}\";\n"
            f"DROP INDEX IF EXISTS \"{idx_name}\";"
        )

    risk_notes = _assess_sql_risk(forward, dialect=dialect)
    completion = (
        f"### 1. Forward Migration\n```{code_lang}\n{forward}\n```\n\n"
        f"### 2. Rollback / Down Migration\n```{'sql' if framework_idx >= 2 else code_lang}\n{rollback}\n```\n\n"
        f"### 3. Lock & Data-Loss Risk Assessment\n{risk_notes}"
    )

    prompt = (
        "Design a safe database schema/query migration for this repository.\n"
        f"Source: {source_name}\n"
        f"Database: {db_id} ({dialect})\n"
        f"Migration Framework: {framework}\n\n"
        f"Current Schema:\n```sql\n{truncate_bounded(c_schema, 1600)}\n```\n\n"
        f"Migration Goal:\nCreate an indexed reporting view (`{view_name}`) and supporting lookup index "
        f"for `{primary_table}.{primary_col}` to answer: {c_q}"
        + (f"\nDomain Evidence: {c_ev}" if c_ev else "")
        + "\n\nRespond with Markdown containing:\n"
        "1. `### 1. Forward Migration`\n"
        "2. `### 2. Rollback / Down Migration`\n"
        "3. `### 3. Lock & Data-Loss Risk Assessment`"
    )
    return InstructionRow(prompt=prompt, completion=completion), redactions


def _build_commit_migration_row(
    source_name: str,
    repo: str,
    file_path: str,
    old_contents: str,
    new_contents: str,
    message: str,
) -> tuple[InstructionRow, int] | None:
    c_old, r1 = scrub_with_count(old_contents)
    c_new, r2 = scrub_with_count(new_contents)
    c_msg, r3 = scrub_with_count(message)
    redactions = r1 + r2 + r3

    if len(c_new.strip()) < 30 or len(c_msg.strip()) < 8:
        return None

    low_path = file_path.lower()
    if "prisma" in low_path:
        framework = "Prisma Migrate"
    elif "alembic" in low_path or "op." in c_new:
        framework = "Alembic"
    elif "migrations" in low_path and low_path.endswith(".py"):
        framework = "Django Migrations"
    else:
        framework = "Flyway / SQL Migration"

    code_lang, rollback = _derive_rollback_from_commit(c_old, c_new, file_path)
    risk_notes = _assess_sql_risk(c_new, dialect="PostgreSQL/SQL")

    completion = (
        f"### 1. Forward Migration\n```{code_lang}\n{truncate_bounded(c_new, 2200)}\n```\n\n"
        f"### 2. Rollback / Down Migration\n```{code_lang}\n{truncate_bounded(rollback, 1400)}\n```\n\n"
        f"### 3. Lock & Data-Loss Risk Assessment\n{risk_notes}"
    )

    prior_block = (
        f"Previous File Content (`{file_path}`):\n```\n{truncate_bounded(c_old, 1100)}\n```\n\n"
        if c_old.strip()
        else f"Target New Migration Path: `{file_path}` (new migration revision)\n\n"
    )
    prompt = (
        "Author a forward migration, rollback plan, and lock/data-loss risk assessment for this database change.\n"
        f"Source: {source_name}\n"
        f"Repository: {repo}\n"
        f"Migration Framework: {framework}\n"
        f"Target File: {file_path}\n\n"
        f"{prior_block}"
        f"Migration Goal:\n{truncate_bounded(c_msg, 600)}\n\n"
        "Respond with Markdown containing:\n"
        "1. `### 1. Forward Migration`\n"
        "2. `### 2. Rollback / Down Migration`\n"
        "3. `### 3. Lock & Data-Loss Risk Assessment`"
    )
    return InstructionRow(prompt=prompt, completion=completion), redactions


def _format_spider_schema(db_id: str, query_toks: list[str], sql_query: str) -> str:
    tables = _extract_tables(sql_query)
    cols = _extract_filter_cols(sql_query)
    lines = [f"-- Database: {db_id}"]
    for tbl in tables[:4]:
        col_defs = ", ".join(f'"{c}" TEXT' for c in cols[:4])
        lines.append(f'CREATE TABLE "{tbl}" ("id" BIGINT PRIMARY KEY, {col_defs});')
    return "\n".join(lines)


def collect_rows() -> tuple[list[InstructionRow], dict[str, int], int]:
    """Stream BIRD Mini-Dev, commitpackft SQL/Alembic/Django/Prisma, and Spider rows."""
    rows: list[InstructionRow] = []
    seen_sigs: set[str] = set()
    source_counts: dict[str, int] = {}
    total_redactions = 0

    def _add(source_key: str, item: tuple[InstructionRow, int] | None, unique_key: str = "") -> bool:
        nonlocal total_redactions
        if item is None:
            return False
        row, red = item
        p_hash = hashlib.sha256(row.prompt.encode("utf-8", errors="ignore")).hexdigest()[:24]
        k_hash = hashlib.sha256(unique_key.encode("utf-8", errors="ignore")).hexdigest()[:24] if unique_key else p_hash
        if p_hash in seen_sigs or k_hash in seen_sigs:
            return False
        seen_sigs.add(p_hash)
        seen_sigs.add(k_hash)
        rows.append(row)
        source_counts[source_key] = source_counts.get(source_key, 0) + 1
        total_redactions += red
        return True

    # 1. birdsql/bird_mini_dev (500 unique questions paired with real PostgreSQL/MySQL/SQLite schemas)
    dialect_schemas: dict[str, dict[str, str]] = {}
    for dialect_key in ("postgresql", "mysql", "sqlite"):
        s_path = hf_hub_download(
            "birdsql/bird_mini_dev",
            f"baseline_inference/schemas/{dialect_key}.json",
            repo_type="dataset",
        )
        with open(s_path, "r", encoding="utf-8") as f:
            dialect_schemas[dialect_key] = json.load(f)

    bird_files = (
        ("PostgreSQL", "postgresql", "mini_dev_postgresql.jsonl"),
        ("MySQL", "mysql", "mini_dev_mysql.jsonl"),
        ("SQLite", "sqlite", "mini_dev_sqlite.jsonl"),
    )
    bird_streams = [
        (
            d_label,
            d_key,
            list(
                load_dataset(
                    "json",
                    data_files=f"hf://datasets/birdsql/bird_mini_dev/data/{fname}",
                    split="train",
                    streaming=True,
                )
            ),
        )
        for d_label, d_key, fname in bird_files
    ]
    pbar = tqdm(total=500, desc="db_migration <- birdsql/bird_mini_dev", unit="rows")
    for q_idx in range(500):
        d_label, d_key, records = bird_streams[q_idx % 3]
        if q_idx >= len(records):
            continue
        rec = records[q_idx]
        db_id = str(rec.get("db_id") or "database")
        schema_ddl = str(dialect_schemas[d_key].get(db_id) or f"-- Schema for {db_id}")
        question = str(rec.get("question") or "").strip()
        sql_query = str(rec.get("SQL") or "").strip()
        evidence = str(rec.get("evidence") or "").strip()
        built = _build_schema_migration_pair(
            len(rows),
            f"birdsql/bird_mini_dev ({d_label})",
            db_id,
            d_label,
            schema_ddl,
            question,
            sql_query,
            evidence=evidence,
        )
        if _add("bird_mini_dev", built, unique_key=f"bird:{db_id}:{question.lower()}"):
            pbar.update(1)
    pbar.close()

    # 2. bigcode/commitpackft SQL migrations
    sql_quota = 1750
    ds_sql = load_dataset(
        "json",
        data_files="hf://datasets/bigcode/commitpackft/data/sql/data.jsonl",
        split="train",
        streaming=True,
    )
    pbar = tqdm(total=sql_quota, desc="db_migration <- commitpackft/sql", unit="rows")
    for rec in ds_sql:
        if source_counts.get("commitpackft_sql", 0) >= sql_quota:
            break
        new_file = str(rec.get("new_file") or rec.get("old_file") or "migration.sql")
        old_c = str(rec.get("old_contents") or "")
        new_c = str(rec.get("new_contents") or "")
        msg = str(rec.get("message") or rec.get("subject") or "").strip()
        repo = str(rec.get("repos") or "open-source/db").split(",")[0].strip()
        if old_c == new_c or len(new_c) < 30:
            continue
        built = _build_commit_migration_row(
            "bigcode/commitpackft (sql)", repo, new_file, old_c, new_c, msg
        )
        if _add("commitpackft_sql", built, unique_key=f"sql:{repo}:{new_file}:{new_c[:160]}"):
            pbar.update(1)
    pbar.close()

    # 3. bigcode/commitpackft Python (Alembic / Django migrations) & TypeScript (Prisma / SQL migrations)
    py_quota = 300
    ds_py = load_dataset(
        "json",
        data_files="hf://datasets/bigcode/commitpackft/data/python/data.jsonl",
        split="train",
        streaming=True,
    )
    pbar = tqdm(total=py_quota, desc="db_migration <- commitpackft/python (Alembic/Django)", unit="rows")
    for checked, rec in enumerate(ds_py):
        if source_counts.get("commitpackft_python_migrations", 0) >= py_quota or checked >= 25000:
            break
        new_file = str(rec.get("new_file") or rec.get("old_file") or "")
        new_c = str(rec.get("new_contents") or "")
        old_c = str(rec.get("old_contents") or "")
        nf_low = new_file.lower()
        nc_low = new_c.lower()
        if not (
            "migrations/" in nf_low
            or "alembic" in nf_low
            or "op.add_column" in nc_low
            or "op.create_table" in nc_low
            or "migrations.migration" in nc_low
        ):
            continue
        msg = str(rec.get("message") or rec.get("subject") or "").strip()
        repo = str(rec.get("repos") or "open-source/py-app").split(",")[0].strip()
        built = _build_commit_migration_row(
            "bigcode/commitpackft (python migrations)", repo, new_file, old_c, new_c, msg
        )
        if _add("commitpackft_python_migrations", built, unique_key=f"pymig:{repo}:{new_file}:{new_c[:160]}"):
            pbar.update(1)
    pbar.close()

    ts_quota = 150
    ds_ts = load_dataset(
        "json",
        data_files="hf://datasets/bigcode/commitpackft/data/typescript/data.jsonl",
        split="train",
        streaming=True,
    )
    pbar = tqdm(total=ts_quota, desc="db_migration <- commitpackft/typescript (Prisma/ORM)", unit="rows")
    for checked, rec in enumerate(ds_ts):
        if source_counts.get("commitpackft_ts_migrations", 0) >= ts_quota or checked >= 25000:
            break
        new_file = str(rec.get("new_file") or rec.get("old_file") or "")
        new_c = str(rec.get("new_contents") or "")
        old_c = str(rec.get("old_contents") or "")
        nf_low = new_file.lower()
        nc_low = new_c.lower()
        if not (
            "prisma" in nf_low
            or "migration" in nf_low
            or "queryrunner" in nc_low
            or "alter table" in nc_low
            or "createtable(" in nc_low
        ):
            continue
        msg = str(rec.get("message") or rec.get("subject") or "").strip()
        repo = str(rec.get("repos") or "open-source/ts-app").split(",")[0].strip()
        built = _build_commit_migration_row(
            "bigcode/commitpackft (ts migrations)", repo, new_file, old_c, new_c, msg
        )
        if _add("commitpackft_ts_migrations", built, unique_key=f"tsmig:{repo}:{new_file}:{new_c[:160]}"):
            pbar.update(1)
    pbar.close()

    # 4. xlangai/spider (train + validation splits to reach TOTAL_TARGET = 5,200)
    rem = TOTAL_TARGET - len(rows)
    pbar = tqdm(total=rem, desc="db_migration <- xlangai/spider", unit="rows")
    for split_name in ("train", "validation"):
        if len(rows) >= TOTAL_TARGET:
            break
        ds_sp = load_dataset("xlangai/spider", split=split_name, streaming=True)
        for rec in ds_sp:
            if len(rows) >= TOTAL_TARGET:
                break
            db_id = str(rec.get("db_id") or "spider_db")
            question = str(rec.get("question") or "").strip()
            query = str(rec.get("query") or "").strip()
            query_toks = rec.get("query_toks") or []
            schema_ddl = _format_spider_schema(db_id, list(query_toks), query)
            built = _build_schema_migration_pair(
                len(rows),
                f"xlangai/spider ({split_name})",
                db_id,
                "PostgreSQL",
                schema_ddl,
                question,
                query,
            )
            if _add("xlangai_spider", built, unique_key=f"spider:{db_id}:{question.lower()}"):
                pbar.update(1)
    pbar.close()

    return rows, source_counts, total_redactions


def main() -> int:
    load_training_env()
    rows, source_counts, redactions = collect_rows()
    train_rows, eval_rows = split_train_eval(rows, TRAIN_TARGET, EVAL_TARGET, seed=42)

    train_path = MODEL_TRAINING_ROOT / "data" / "processed" / "db_migration.jsonl"
    eval_path = MODEL_TRAINING_ROOT / "data" / "eval" / "db_migration_eval.jsonl"
    write_instruction_jsonl(train_rows, train_path)
    write_instruction_jsonl(eval_rows, eval_path)

    print(
        f"[db_migration] wrote {len(train_rows)} train -> {train_path} | "
        f"{len(eval_rows)} eval -> {eval_path} | redactions={redactions} | sources={source_counts}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
