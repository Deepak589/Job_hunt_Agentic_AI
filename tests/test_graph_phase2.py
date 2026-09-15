"""Routing logic for the diagnose->rewrite->validate_facts->review loop, tested without
any LLM call — every node exercised here (`fact_gate`, `review_gate`,
`log_fact_failure`) is pure Python over a hand-built JobState.
"""

from __future__ import annotations

from agentic_ai.config import settings
from agentic_ai.nodes.review import review_gate
from agentic_ai.nodes.validate_facts import fact_gate, log_fact_failure
from agentic_ai.state import Draft, Job, JobState, Scores


def _state(**overrides) -> JobState:
    job = Job(id="t", source="manual", title="Test", jd_text="...")
    draft = Draft(profile_line="x", section_order=["projects"], bullets={}, cover_letter="")
    base = dict(job=job, draft=draft)
    base.update(overrides)
    return JobState(**base)


def test_fact_gate_clean_goes_to_review() -> None:
    assert fact_gate(_state(validation_errors=[])) == "clean"


def test_fact_gate_retries_when_attempts_remain() -> None:
    s = _state(validation_errors=["bad number"], attempt_count=1)
    assert settings.max_rewrite_attempts == 2
    assert fact_gate(s) == "retry"


def test_fact_gate_gives_up_when_attempts_exhausted() -> None:
    s = _state(validation_errors=["bad number"], attempt_count=2)
    assert fact_gate(s) == "give_up"


def test_log_fact_failure_names_the_error_and_sets_skip_reason() -> None:
    s = _state(validation_errors=["projects[0]: unverified number '87%'"], attempt_count=2)
    out = log_fact_failure(s)
    assert "87%" in out["skip_reason"]
    assert out["skip_reason"] is not None  # never reaches render_documents with this set


def test_review_gate_retries_on_low_score_with_attempts_left() -> None:
    s = _state(scores=Scores(review_score=5), attempt_count=1)
    assert review_gate(s) == "retry"


def test_review_gate_proceeds_on_low_score_when_attempts_exhausted() -> None:
    """Ship the best version and flag the weakness — never loop forever (CLAUDE.md)."""
    s = _state(scores=Scores(review_score=5), attempt_count=2)
    assert review_gate(s) == "proceed"


def test_review_gate_proceeds_on_passing_score() -> None:
    s = _state(scores=Scores(review_score=8), attempt_count=1)
    assert review_gate(s) == "proceed"
