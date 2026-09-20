"""Budget guard + structured JSON logging (CLI-level, no live API calls)."""

from __future__ import annotations

import json

import pytest
import typer

from agentic_ai import cli
from agentic_ai.db.repo import persist_run
from agentic_ai.state import Job, JobState, Scores


def _state(**overrides) -> JobState:
    job = Job(id="job1", source="manual", title="AI Engineer", jd_text="...")
    base = dict(
        job=job,
        scores=Scores(hard_coverage=0.8),
        llm_calls=[
            {"node": "extract_requirements", "model": "claude-haiku-4-5", "input_tokens": 100, "output_tokens": 50,
             "cache_read_tokens": 0, "cache_creation_tokens": 0, "cost_usd": 0.01},
            {"node": "diagnose", "model": "claude-sonnet-5", "input_tokens": 200, "output_tokens": 100,
             "cache_read_tokens": 0, "cache_creation_tokens": 0, "cost_usd": 0.02},
        ],
    )
    base.update(overrides)
    return JobState(**base)


def test_check_budget_noop_when_cap_unset(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli.settings, "jobs_db_path", tmp_path / "jobpilot.db")
    monkeypatch.setattr(cli.settings, "max_daily_cost_usd", None)
    cli._check_budget()  # must not raise


def test_check_budget_passes_under_cap(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "jobpilot.db"
    persist_run(_state(), db_path=db_path)  # spent 0.03 today
    monkeypatch.setattr(cli.settings, "jobs_db_path", db_path)
    monkeypatch.setattr(cli.settings, "max_daily_cost_usd", 1.0)
    cli._check_budget()  # under cap, must not raise


def test_check_budget_blocks_at_cap(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "jobpilot.db"
    log_path = tmp_path / "jobpilot.log.jsonl"
    persist_run(_state(), db_path=db_path)  # spent 0.03 today
    monkeypatch.setattr(cli.settings, "jobs_db_path", db_path)
    monkeypatch.setattr(cli.settings, "log_path", log_path)
    monkeypatch.setattr(cli.settings, "max_daily_cost_usd", 0.03)

    with pytest.raises(typer.Exit):
        cli._check_budget()

    lines = [json.loads(l) for l in log_path.read_text().splitlines()]
    assert lines[0]["event"] == "budget_guard_rejected"
    assert lines[0]["spent_today_usd"] == 0.03


def test_log_run_writes_per_node_cost_breakdown(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "jobpilot.log.jsonl"
    monkeypatch.setattr(cli.settings, "log_path", log_path)

    cli._log_run(_state(skip_reason="disqualifier: no C1 German"))

    lines = [json.loads(l) for l in log_path.read_text().splitlines()]
    assert lines[0]["event"] == "run"
    assert lines[0]["job_id"] == "job1"
    assert lines[0]["cost_by_node"] == {"extract_requirements": 0.01, "diagnose": 0.02}
    assert lines[0]["total_cost_usd"] == 0.03
    assert lines[0]["skip_reason"] == "disqualifier: no C1 German"
