"""jobs/runs persistence — the cost ledger (plan.md §11-12, Phase 3).

One row per `persist_run()` call in `runs`; `jobs` is upserted so re-running the same
job (same content-hash id) doesn't duplicate it.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..config import settings
from ..state import JobState

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


_NEW_COLUMNS = [
    ("jobs", "content_hash", "TEXT"),
    ("jobs", "raw_json", "TEXT"),
    ("runs", "content_hash", "TEXT"),
]


def init_db(db_path: Path | None = None) -> None:
    path = db_path or settings.jobs_db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA_PATH.read_text())
        # CREATE TABLE IF NOT EXISTS is a no-op on a pre-existing db — an on-disk db from
        # before solution.md step 1 needs these columns added explicitly.
        for table, column, coltype in _NEW_COLUMNS:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.commit()


def persist_run(state: JobState, db_path: Path | None = None) -> str:
    """Upsert the job row, insert one run row. Returns the new run_id."""
    path = db_path or settings.jobs_db_path
    init_db(path)
    now = datetime.now(timezone.utc).isoformat()
    job = state.job
    run_id = uuid.uuid4().hex

    tokens_in = sum(c["input_tokens"] for c in state.llm_calls)
    tokens_out = sum(c["output_tokens"] for c in state.llm_calls)

    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO jobs (id, source, url, title, company, location, jd_text,
                               employment_type, content_hash, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET last_seen = excluded.last_seen,
                                           content_hash = excluded.content_hash
            """,
            (job.id, job.source, job.url, job.title, job.company, job.location,
             job.jd_text, job.employment_type, job.content_hash, now, now),
        )
        conn.execute(
            """
            INSERT INTO runs (run_id, job_id, content_hash, started_at, finished_at,
                               hard_coverage, soft_coverage, review_score, recruiter,
                               verdict, attempt_count, skip_reason,
                               tokens_in, tokens_out, cost_usd, ats_total)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, job.id, job.content_hash, now, now,
                state.scores.hard_coverage, state.scores.soft_coverage,
                state.scores.review_score,
                state.recruiter.result if state.recruiter else None,
                state.hiring_manager.verdict if state.hiring_manager else (state.ats.verdict if state.ats else None),
                state.attempt_count, state.skip_reason,
                tokens_in, tokens_out, state.total_cost_usd,
                state.ats.total if state.ats else None,
            ),
        )
        conn.commit()
    return run_id


def already_processed(job_id: str, content_hash: str, db_path: Path | None = None) -> bool:
    """True if a run for this exact (job_id, content_hash) pair already reached a verdict.

    job_id alone would wrongly skip a posting whose JD text changed since the last run —
    the id now identifies the POSTING (source+url), not the text (solution.md step 1), so
    an edited listing under the same id needs to be recognized as new content and re-run.
    A crashed run leaves no row (or a row with verdict NULL) and is NOT considered
    processed — durability (checkpointer resume) is solution.md step 2's job, not this."""
    path = db_path or settings.jobs_db_path
    init_db(path)
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT 1 FROM runs WHERE job_id = ? AND content_hash = ? AND verdict IS NOT NULL LIMIT 1",
            (job_id, content_hash),
        ).fetchone()
    return row is not None


def cost_summary(db_path: Path | None = None, since: datetime | None = None) -> dict:
    """Per-day + all-time run count/tokens/$ from the `runs` table.

    `since`, if given, restricts to `started_at >= since.isoformat()`. Day buckets
    use the first 10 chars of `started_at` (an ISO timestamp, so that's the date).
    """
    path = db_path or settings.jobs_db_path
    init_db(path)
    where, params = "", ()
    if since is not None:
        where, params = "WHERE started_at >= ?", (since.isoformat(),)

    with sqlite3.connect(path) as conn:
        by_day = conn.execute(
            f"""
            SELECT substr(started_at, 1, 10) AS day, COUNT(*), SUM(tokens_in),
                   SUM(tokens_out), SUM(cost_usd)
            FROM runs {where} GROUP BY day ORDER BY day
            """,
            params,
        ).fetchall()
        total_runs, total_in, total_out, total_cost = conn.execute(
            f"SELECT COUNT(*), SUM(tokens_in), SUM(tokens_out), SUM(cost_usd) FROM runs {where}",
            params,
        ).fetchone()

    return {
        "by_day": [
            {"date": day, "runs": runs, "tokens_in": tin or 0, "tokens_out": tout or 0, "cost_usd": cost or 0.0}
            for day, runs, tin, tout, cost in by_day
        ],
        "total_runs": total_runs or 0,
        "total_tokens_in": total_in or 0,
        "total_tokens_out": total_out or 0,
        "total_cost_usd": total_cost or 0.0,
    }
