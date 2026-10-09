"""Checkpointer/interrupt/resume mechanics of the slim graph, exercised with every
LLM-calling node monkeypatched so this costs zero API calls (gates get real unit tests,
LLM nodes never get mocked-call tests — but the interrupt/resume plumbing around them is
risky enough to verify end-to-end with stand-ins).
"""

from __future__ import annotations

import agentic_ai.graph as graph_mod
from agentic_ai.config import settings
from agentic_ai.state import Diagnosis, Draft, DraftBullet


def _fake_analyze(state):
    diagnosis = Diagnosis(hard_gaps=[], soft_gaps=[], disqualifiers=[], matches=["stub match"])
    return {"requirements": [], "diagnosis": diagnosis, "notes": ["stub analyze"]}


def _fake_validate_facts(state):
    return {"validation_errors": [], "notes": ["stub validate_facts"]}


def _fake_rewrite(state):
    draft = Draft(
        profile_line="Test profile line.",
        section_order=["projects"],
        bullets={
            "projects": [
                DraftBullet(text="Improved recall by 0.833.", metric="0.833", source_bullet_id="proj.rag_pipeline.b1")
            ]
        },
        cover_letter="x",
    )
    return {"draft": draft, "attempt_count": state.attempt_count + 1, "notes": ["stub rewrite"]}


def _patch_llm_nodes(monkeypatch) -> None:
    monkeypatch.setattr(graph_mod, "analyze", _fake_analyze)
    monkeypatch.setattr(graph_mod, "rewrite", _fake_rewrite)
    monkeypatch.setattr(graph_mod, "validate_facts_node", _fake_validate_facts)


def test_run_for_review_pauses_before_render(monkeypatch, tmp_path) -> None:
    _patch_llm_nodes(monkeypatch)
    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")

    paused = graph_mod.run_for_review("a fake jd, quite unique", title="T", company="C")

    assert paused.draft is not None
    assert paused.diagnosis is not None and paused.diagnosis.matches == ["stub match"]
    assert paused.artifacts == {}  # render_documents has not run yet
    assert paused.ats is None


def test_get_paused_state_reads_back_the_same_job(monkeypatch, tmp_path) -> None:
    _patch_llm_nodes(monkeypatch)
    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")

    paused = graph_mod.run_for_review("another fake jd, also unique", title="T", company="C")
    fetched = graph_mod.get_paused_state(paused.job.id)

    assert fetched.draft is not None
    assert fetched.draft.profile_line == paused.draft.profile_line
    assert fetched.diagnosis is not None


def test_resume_review_approve_renders_and_scores(monkeypatch, tmp_path) -> None:
    _patch_llm_nodes(monkeypatch)
    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")

    paused = graph_mod.run_for_review("third fake jd for approval", title="T", company="C")
    resumed = graph_mod.resume_review(paused.job.id, action="approve")

    assert resumed.skip_reason is None
    assert resumed.artifacts.get("cv_pdf")
    assert resumed.ats is not None


def test_resume_review_reject_never_renders(monkeypatch, tmp_path) -> None:
    _patch_llm_nodes(monkeypatch)
    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")

    paused = graph_mod.run_for_review("fourth fake jd for rejection", title="T", company="C")
    rejected = graph_mod.resume_review(paused.job.id, action="reject")

    assert rejected.skip_reason == "rejected by user at review"
    assert rejected.artifacts == {}
    assert rejected.ats is None


def test_resume_review_edit_pauses_again_with_the_edited_draft(monkeypatch, tmp_path) -> None:
    """Editing must not render: it fact-checks the edited draft and pauses again, so the
    next approve renders what the human actually edited."""
    _patch_llm_nodes(monkeypatch)
    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")

    paused = graph_mod.run_for_review("fifth fake jd for editing", title="T", company="C")

    edited = paused.draft.model_copy(update={"profile_line": "Edited profile line."})
    reviewed_again = graph_mod.resume_review(paused.job.id, action="edit", draft=edited)

    assert reviewed_again.draft.profile_line == "Edited profile line."
    assert reviewed_again.draft.bullets == paused.draft.bullets  # untouched fields survive
    assert reviewed_again.artifacts == {}  # still paused, not rendered
    assert reviewed_again.ats is None
    assert graph_mod.job_id("manual", "", "fifth fake jd for editing") in graph_mod.list_pending_review_ids()


# ------------------------------------------------------------------- crash recovery


def test_resume_continues_without_repaying_completed_nodes(monkeypatch, tmp_path) -> None:
    """A crash mid-run must not force analyze/rewrite to be re-paid for — `resume()` picks
    up from the last checkpointed node."""
    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")
    calls = {"analyze": 0, "rewrite": 0, "validate": 0}

    def _an(state):
        calls["analyze"] += 1
        return _fake_analyze(state)

    def _rw(state):
        calls["rewrite"] += 1
        return _fake_rewrite(state)

    crashed_once = {"done": False}

    def _validate_crashes_once(state):
        calls["validate"] += 1
        if not crashed_once["done"]:
            crashed_once["done"] = True
            raise RuntimeError("simulated crash")
        return _fake_validate_facts(state)

    monkeypatch.setattr(graph_mod, "analyze", _an)
    monkeypatch.setattr(graph_mod, "rewrite", _rw)
    monkeypatch.setattr(graph_mod, "validate_facts_node", _validate_crashes_once)

    jd_text = "crash test jd, quite unique"
    job_id = graph_mod.job_id("manual", "", jd_text)

    try:
        graph_mod.run(jd_text, title="T", company="C")
    except RuntimeError:
        pass

    assert calls == {"analyze": 1, "rewrite": 1, "validate": 1}

    resumed = graph_mod.resume(job_id)

    assert calls["analyze"] == 1  # not re-run
    assert calls["rewrite"] == 1  # not re-run
    assert calls["validate"] == 2  # re-run — it never completed
    assert resumed.artifacts.get("cv_pdf")


def test_resume_rejects_a_job_paused_for_review(monkeypatch, tmp_path) -> None:
    """resume() is for crashed plain runs — a job paused at human_review must be
    continued via resume_review, not resume()."""
    _patch_llm_nodes(monkeypatch)
    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")

    paused = graph_mod.run_for_review("paused for review, not a crash", title="T", company="C")

    import pytest

    with pytest.raises(ValueError, match="use 'jobpilot review'"):
        graph_mod.resume(paused.job.id)


def test_run_for_review_crash_persists_cost_like_run_does(monkeypatch, tmp_path) -> None:
    """`_run` already wraps `ainvoke` in `_invoke_tracked` so a crash mid-pipeline still
    persists the cost spent on nodes that completed before it (see its docstring: "a
    crash on e.g. rewrite must not lose the llm_calls spent on extract_requirements/
    diagnose/judge_requirements before it"). `run_for_review` called raw `ainvoke`
    instead, with no such persistence — a crashed review-path run silently dropped
    its cost."""
    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")
    monkeypatch.setattr(settings, "jobs_db_path", tmp_path / "jobpilot.db")

    monkeypatch.setattr(graph_mod, "analyze", _fake_analyze)

    def _boom(state):
        raise RuntimeError("simulated crash in rewrite")

    monkeypatch.setattr(graph_mod, "rewrite", _boom)

    import pytest

    with pytest.raises(RuntimeError, match="simulated crash in rewrite"):
        graph_mod.run_for_review("a crashing jd, quite unique", title="T", company="C")

    import sqlite3

    job_id = graph_mod.job_id("manual", "", "a crashing jd, quite unique")
    with sqlite3.connect(settings.jobs_db_path) as conn:
        row = conn.execute(
            "SELECT verdict FROM runs WHERE job_id = ?", (job_id,)
        ).fetchone()
    assert row is not None, "crashed review-path run left no cost record"
    assert row[0] is None


def test_checkpoint_round_trip_does_not_warn_about_unregistered_types(monkeypatch, tmp_path, caplog) -> None:
    """LangGraph's default (permissive) msgpack serializer warns on every unregistered
    type it deserializes from a checkpoint ("This will be blocked in a future version").
    Every type it's warning about here is one of this project's own trusted pydantic
    models (state.py) — reading back our own job/draft/score state a second time
    (e.g. a retried add_job on the same content-hash thread) must not warn."""
    import logging

    _patch_llm_nodes(monkeypatch)
    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")

    with caplog.at_level(logging.WARNING, logger="langgraph.checkpoint.serde.jsonplus"):
        paused = graph_mod.run_for_review(
            "a job description read back twice, quite unique", title="T", company="C"
        )
        # reading the state back is what triggers deserialization of the checkpointed types
        graph_mod.get_paused_state(paused.job.id)

    unregistered_warnings = [r for r in caplog.records if "unregistered type" in r.message]
    assert unregistered_warnings == []


def test_list_pending_review_ids_returns_only_paused_jobs(tmp_path, monkeypatch) -> None:
    _patch_llm_nodes(monkeypatch)
    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")

    paused = graph_mod.run_for_review(
        "Werkstudent Data Engineer. Must know Python and SQL.", title="DE", company="Acme"
    )
    finished = graph_mod.run_for_review(
        "Werkstudent Backend. Must know Python and SQL.", title="BE", company="Acme2"
    )
    # approve() actually resumes the graph past human_review, clearing it from the
    # pending set for real; reject() now does too (graph.py's _resume_review no
    # longer short-circuits reject), but approve is what this test needs to prove
    # the "finished" job drops out of list_pending_review_ids().
    graph_mod.resume_review(finished.job.id, action="approve")

    pending = graph_mod.list_pending_review_ids()

    assert paused.job.id in pending
    assert finished.job.id not in pending
