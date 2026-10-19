"""`jobpilot review` must ask the checkpoint whether `human_review` is pending.

It used to infer reviewability from `state.skip_reason` being unset, which is the wrong
question in both directions:
  * it REFUSED a job whose skip_reason was stale from an earlier run — the railway posting
    (2026-10-17), where approving the draft needed a hand-written Python bypass;
  * it ACCEPTED a thread that had already finished and rendered, calling `resume_review` on
    a graph with nothing left to resume.
The pending-task list is the real answer and `_resume` / `list_pending_review_ids` already
read it, so `get_paused_state(require_pending=True)` asks the same source.
"""

from __future__ import annotations

import pytest
from test_graph_phase3 import _fake_validate_facts, _patch_llm_nodes

import agentic_ai.graph as graph_mod
from agentic_ai.config import settings
from agentic_ai.graph import _not_pending_review_reason
from agentic_ai.state import Job, JobState


def _state(**fields) -> JobState:
    return JobState(
        job=Job(id="abc123", source="manual", jd_text="a jd", title="T", company="C"),
        **fields,
    )


# ----------------------------------------------------------------- the four cases


def test_a_thread_stopped_mid_run_points_at_resume() -> None:
    reason = _not_pending_review_reason("abc123", _state(), ("validate_facts",))
    assert "stopped mid-run at validate_facts" in reason
    assert "jobpilot resume abc123" in reason


def test_a_thread_that_ended_reports_its_skip_reason() -> None:
    reason = _not_pending_review_reason("abc123", _state(skip_reason="2 uncovered hard reqs"), ())
    assert "ended before review: 2 uncovered hard reqs" in reason


def test_a_finished_thread_says_there_is_nothing_to_review() -> None:
    """The direction the old guard got wrong by ACCEPTING: skip_reason is None here."""
    state = _state(artifacts={"cv_pdf": "out/cv.pdf", "cover_letter_pdf": "out/cl.pdf"})
    reason = _not_pending_review_reason("abc123", state, ())
    assert "already finished and rendered" in reason
    assert "cover_letter_pdf" in reason and "cv_pdf" in reason


def test_a_thread_with_neither_is_reported_plainly() -> None:
    reason = _not_pending_review_reason("abc123", _state(), ())
    assert "no pending review step" in reason


# ----------------------------------------------------------------- end to end


def test_require_pending_accepts_a_genuinely_paused_job(monkeypatch, tmp_path) -> None:
    _patch_llm_nodes(monkeypatch)
    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")

    paused = graph_mod.run_for_review("a jd for review", title="T", company="C")

    fetched = graph_mod.get_paused_state(paused.job.id, require_pending=True)
    assert fetched.job.id == paused.job.id


def test_require_pending_rejects_a_finished_job(monkeypatch, tmp_path) -> None:
    """`run()` goes straight through to render, so its thread has no pending review."""
    _patch_llm_nodes(monkeypatch)
    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")
    monkeypatch.setattr(graph_mod, "render_documents", lambda s: {"artifacts": {"cv_pdf": "x.pdf"}})
    monkeypatch.setattr(graph_mod, "score_ats", lambda s: {"notes": ["stub ats"]})

    done = graph_mod.run("a jd that finishes", title="T", company="C")

    with pytest.raises(ValueError, match="nothing to review"):
        graph_mod.get_paused_state(done.job.id, require_pending=True)

    # The plain reader still works, because mcp.server.get_job reads finished jobs with it.
    assert graph_mod.get_paused_state(done.job.id).job.id == done.job.id


def test_a_pending_review_is_accepted_even_carrying_a_skip_reason(monkeypatch, tmp_path) -> None:
    """The direction the old guard got wrong by REFUSING, end to end.

    This is the railway shape: the thread is genuinely sitting at `human_review` with a
    clean validator, while a `skip_reason` from an earlier run is still on the state. The
    old `if paused.skip_reason` guard exited 1 here and approving needed a Python bypass.
    Pendingness decides; a leftover reason cannot veto it.
    """
    _patch_llm_nodes(monkeypatch)
    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")

    def _clean_but_carrying_a_stale_reason(state):
        return {**_fake_validate_facts(state), "skip_reason": "6 uncovered hard requirements"}

    monkeypatch.setattr(graph_mod, "validate_facts_node", _clean_but_carrying_a_stale_reason)

    paused = graph_mod.run_for_review("a jd for review", title="T", company="C")
    assert paused.skip_reason == "6 uncovered hard requirements"

    fetched = graph_mod.get_paused_state(paused.job.id, require_pending=True)
    assert fetched.job.id == paused.job.id
