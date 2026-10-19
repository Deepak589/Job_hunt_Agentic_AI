"""Per-run cost attribution — `llm_calls` accumulates, `current_run_calls` does not.

`thread_id` is the job id, so re-running a job reuses its checkpoint, and `llm_calls` has an
`operator.add` reducer that CANNOT be cleared by returning a value. Before the watermark, the
second run's `persist_run` wrote a row carrying both runs' calls: two rows for the railway
posting each recorded $0.826 for a job that cost about $0.34. `budget.py` reads today's total
out of those rows to enforce JOBPILOT_MAX_DAILY_COST_USD, so the inflation could refuse runs
against spend that never happened.
"""

from __future__ import annotations

import sqlite3

from agentic_ai.config import settings
from agentic_ai.db import repo
from agentic_ai.state import Job, JobState


def _call(node: str, cost: float, tin: int = 100, tout: int = 50) -> dict:
    return {
        "node": node,
        "model": "claude-sonnet-5",
        "input_tokens": tin,
        "output_tokens": tout,
        "cost_usd": cost,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }


def _state(calls: list[dict], offset: int = 0) -> JobState:
    return JobState(
        job=Job(id="abc123", source="manual", jd_text="a jd", title="T", company="C"),
        llm_calls=calls,
        llm_calls_offset=offset,
    )


def test_a_fresh_run_counts_everything() -> None:
    state = _state([_call("agent_cv", 0.20), _call("rewrite", 0.14)])
    assert state.total_cost_usd == 0.34
    assert len(state.current_run_calls()) == 2


def test_a_rerun_counts_only_its_own_calls() -> None:
    """The actual bug: offset 2 means the first run's $0.34 is not charged again."""
    first_run = [_call("agent_cv", 0.20), _call("rewrite", 0.14)]
    second_run = [_call("agent_cv", 0.21), _call("rewrite", 0.13)]
    state = _state(first_run + second_run, offset=len(first_run))

    assert state.total_cost_usd == 0.34
    assert [c["node"] for c in state.current_run_calls()] == ["agent_cv", "rewrite"]
    # Nothing is destroyed — the full history stays inspectable.
    assert len(state.llm_calls) == 4


def test_load_job_stamps_the_offset_at_the_current_length() -> None:
    """`load_job` runs exactly once per new run, which is what makes it the right place."""
    from agentic_ai.graph import load_job

    prior = [_call("agent_cv", 0.20), _call("rewrite", 0.14)]
    out = load_job(_state(prior))
    assert out["llm_calls_offset"] == 2

    assert load_job(_state([]))["llm_calls_offset"] == 0


def test_persisted_tokens_and_cost_are_this_runs_only() -> None:
    """repo.persist_run summed the whole list, so every re-run row inherited its
    predecessors' tokens as well as their cost."""
    first = [_call("agent_cv", 0.20, tin=1000, tout=500)]
    second = [_call("agent_cv", 0.21, tin=1100, tout=400)]
    state = _state(first + second, offset=len(first))

    repo.persist_run(state)

    with sqlite3.connect(settings.jobs_db_path) as conn:
        rows = conn.execute("select tokens_in, tokens_out, cost_usd from runs").fetchall()
    assert rows == [(1100, 400, 0.21)]


def test_a_resumed_run_still_counts_the_crashed_attempts_spend() -> None:
    """The case the watermark must NOT break. A crash-resume replays from the last
    unfinished node with `load_job` already complete, so the offset it set survives and the
    money already spent on this run stays counted — it was spent."""
    crashed_attempt = [_call("agent_cv", 0.20)]
    after_resume = [_call("rewrite", 0.14)]
    # offset is still 0: load_job set it on the original run and does not run again.
    state = _state(crashed_attempt + after_resume, offset=0)
    assert state.total_cost_usd == 0.34
