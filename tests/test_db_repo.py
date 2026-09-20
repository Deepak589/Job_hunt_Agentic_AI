"""jobs/runs cost ledger (plan.md §11-12, Phase 3)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from agentic_ai.db.repo import cost_summary, persist_run
from agentic_ai.state import Job, JobState, Scores


def _state(**overrides) -> JobState:
    job = Job(id="job1", source="manual", title="AI Engineer", company="Acme", jd_text="...")
    base = dict(
        job=job,
        scores=Scores(hard_coverage=0.8, soft_coverage=0.5, review_score=8),
        attempt_count=1,
        llm_calls=[
            {"node": "extract_requirements", "model": "claude-haiku-4-5", "input_tokens": 100, "output_tokens": 50,
             "cache_read_tokens": 0, "cache_creation_tokens": 0, "cost_usd": 0.001},
            {"node": "diagnose", "model": "claude-sonnet-5", "input_tokens": 200, "output_tokens": 100,
             "cache_read_tokens": 0, "cache_creation_tokens": 0, "cost_usd": 0.002},
        ],
    )
    base.update(overrides)
    return JobState(**base)


def test_persist_run_writes_a_jobs_row_and_a_runs_row(tmp_path) -> None:
    db_path = tmp_path / "jobpilot.db"
    run_id = persist_run(_state(), db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        job_row = conn.execute("SELECT id, title, company FROM jobs WHERE id = ?", ("job1",)).fetchone()
        run_row = conn.execute(
            "SELECT job_id, hard_coverage, review_score, tokens_in, tokens_out, cost_usd FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()

    assert job_row == ("job1", "AI Engineer", "Acme")
    assert run_row == ("job1", 0.8, 8, 300, 150, 0.003)


def test_persist_run_upserts_the_job_not_duplicates_it(tmp_path) -> None:
    db_path = tmp_path / "jobpilot.db"
    persist_run(_state(), db_path=db_path)
    persist_run(_state(), db_path=db_path)  # same job.id, second run of the same JD

    with sqlite3.connect(db_path) as conn:
        job_count = conn.execute("SELECT COUNT(*) FROM jobs WHERE id = ?", ("job1",)).fetchone()[0]
        run_count = conn.execute("SELECT COUNT(*) FROM runs WHERE job_id = ?", ("job1",)).fetchone()[0]

    assert job_count == 1
    assert run_count == 2  # one row per run, even for the same job


def test_persist_run_records_skip_reason_and_recruiter_result(tmp_path) -> None:
    db_path = tmp_path / "jobpilot.db"
    from agentic_ai.state import RecruiterResult

    state = _state(skip_reason="disqualifier: no C1 German", recruiter=RecruiterResult(result="hard_fail", reason="x"))
    run_id = persist_run(state, db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT skip_reason, recruiter FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    assert row == ("disqualifier: no C1 German", "hard_fail")


def test_cost_summary_totals_and_buckets_by_day(tmp_path) -> None:
    db_path = tmp_path / "jobpilot.db"
    persist_run(_state(), db_path=db_path)  # tokens_in=300, tokens_out=150, cost=0.003
    persist_run(_state(job=Job(id="job2", source="manual", title="X", jd_text="...")), db_path=db_path)

    summary = cost_summary(db_path=db_path)

    assert summary["total_runs"] == 2
    assert summary["total_tokens_in"] == 600
    assert summary["total_tokens_out"] == 300
    assert round(summary["total_cost_usd"], 6) == 0.006
    assert len(summary["by_day"]) == 1  # both runs happen "today"
    today = summary["by_day"][0]
    assert today["runs"] == 2
    assert round(today["cost_usd"], 6) == 0.006


def test_cost_summary_since_filters_out_older_runs(tmp_path) -> None:
    db_path = tmp_path / "jobpilot.db"
    persist_run(_state(), db_path=db_path)

    future = datetime.now(timezone.utc) + timedelta(days=1)
    summary = cost_summary(db_path=db_path, since=future)

    assert summary["total_runs"] == 0
    assert summary["total_cost_usd"] == 0.0
    assert summary["by_day"] == []
