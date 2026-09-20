"""Phase 3 — recruiter_gate routing (pure) + the checkpointer/interrupt/resume
mechanics, exercised with every LLM-calling node monkeypatched so this costs zero
API calls (same convention as test_graph_phase2.py: gates get real unit tests, LLM
nodes never get mocked-call tests — but the interrupt/resume plumbing around them is
new and risky enough to verify end-to-end with stand-ins).
"""

from __future__ import annotations

import agentic_ai.graph as graph_mod
from agentic_ai.config import settings
from agentic_ai.nodes.recruiter_sim import recruiter_gate
from agentic_ai.state import (
    Diagnosis,
    Draft,
    DraftBullet,
    HiringManagerVerdict,
    Job,
    JobState,
    RecruiterResult,
    Scores,
)


def _state(**overrides) -> JobState:
    job = Job(id="t", source="manual", title="Test", jd_text="...")
    base = dict(job=job)
    base.update(overrides)
    return JobState(**base)


# --------------------------------------------------------------------- recruiter_gate


def test_recruiter_gate_hard_fail_skips() -> None:
    s = _state(recruiter=RecruiterResult(result="hard_fail", reason="no Rust anywhere"))
    assert recruiter_gate(s) == "hard_fail"


def test_recruiter_gate_pass_proceeds() -> None:
    s = _state(recruiter=RecruiterResult(result="pass", reason="all terms present"))
    assert recruiter_gate(s) == "proceed"


def test_recruiter_gate_soft_fail_proceeds() -> None:
    s = _state(recruiter=RecruiterResult(result="soft_fail", reason="one synonym missing"))
    assert recruiter_gate(s) == "proceed"


def test_recruiter_gate_no_result_proceeds() -> None:
    assert recruiter_gate(_state()) == "proceed"


# --------------------------------------------------------- interrupt/resume mechanics


def _fake_extract_requirements(state):
    return {"requirements": [], "notes": ["stub: 0 requirements"]}


def _fake_diagnose(state):
    diagnosis = Diagnosis(hard_gaps=[], soft_gaps=[], disqualifiers=[], matches=["stub match"])
    return {"diagnosis": diagnosis, "notes": ["stub diagnose"]}


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


def _fake_review(state):
    scores = state.scores.model_copy(update={"review_score": 9})
    return {"scores": scores, "validation_errors": [], "notes": ["stub review"]}


def _fake_recruiter_sim(state):
    return {"recruiter": RecruiterResult(result="pass", reason="stub"), "notes": ["stub recruiter"]}


def _fake_hiring_manager(state):
    verdict = HiringManagerVerdict(verdict="apply", why="stub", indefensible_bullets=[])
    return {"hiring_manager": verdict, "notes": ["stub hiring_manager"]}


def _patch_llm_nodes(monkeypatch) -> None:
    monkeypatch.setattr(graph_mod, "extract_requirements", _fake_extract_requirements)
    monkeypatch.setattr(graph_mod, "diagnose", _fake_diagnose)
    monkeypatch.setattr(graph_mod, "rewrite", _fake_rewrite)
    monkeypatch.setattr(graph_mod, "review", _fake_review)
    monkeypatch.setattr(graph_mod, "recruiter_sim", _fake_recruiter_sim)
    monkeypatch.setattr(graph_mod, "hiring_manager", _fake_hiring_manager)


def test_run_for_review_pauses_before_render(monkeypatch, tmp_path) -> None:
    _patch_llm_nodes(monkeypatch)
    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")

    paused = graph_mod.run_for_review("a fake jd, quite unique", title="T", company="C")

    assert paused.draft is not None
    assert paused.recruiter is not None and paused.recruiter.result == "pass"
    assert paused.hiring_manager is not None and paused.hiring_manager.verdict == "apply"
    assert paused.artifacts == {}  # render_documents has not run yet
    assert paused.ats is None


def test_get_paused_state_reads_back_the_same_job(monkeypatch, tmp_path) -> None:
    _patch_llm_nodes(monkeypatch)
    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")

    paused = graph_mod.run_for_review("another fake jd, also unique", title="T", company="C")
    fetched = graph_mod.get_paused_state(paused.job.id)

    assert fetched.draft is not None
    assert fetched.draft.profile_line == paused.draft.profile_line
    assert fetched.hiring_manager.verdict == "apply"


def test_resume_review_approve_renders_and_scores(monkeypatch, tmp_path) -> None:
    _patch_llm_nodes(monkeypatch)
    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")

    paused = graph_mod.run_for_review("third fake jd for approval", title="T", company="C")
    resumed = graph_mod.resume_review(paused.job.id, approve=True)

    assert resumed.skip_reason is None
    assert resumed.artifacts.get("cv_pdf")
    assert resumed.ats is not None


def test_resume_review_reject_never_renders(monkeypatch, tmp_path) -> None:
    _patch_llm_nodes(monkeypatch)
    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")

    paused = graph_mod.run_for_review("fourth fake jd for rejection", title="T", company="C")
    rejected = graph_mod.resume_review(paused.job.id, approve=False)

    assert rejected.skip_reason == "rejected by user at review"
    assert rejected.artifacts == {}
    assert rejected.ats is None


def test_update_draft_reflected_in_paused_state(monkeypatch, tmp_path) -> None:
    _patch_llm_nodes(monkeypatch)
    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")

    paused = graph_mod.run_for_review("fifth fake jd for editing", title="T", company="C")
    edited = paused.draft.model_copy(update={"profile_line": "Edited profile line."})

    graph_mod.update_draft(paused.job.id, edited)
    fetched = graph_mod.get_paused_state(paused.job.id)

    assert fetched.draft.profile_line == "Edited profile line."
    # untouched fields survive the update
    assert fetched.draft.bullets == paused.draft.bullets
    # recruiter/hiring_manager reflect the ORIGINAL draft — editing does not re-run them
    assert fetched.hiring_manager.verdict == "apply"


def test_update_draft_then_resume_renders_the_edited_draft(monkeypatch, tmp_path) -> None:
    _patch_llm_nodes(monkeypatch)
    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")

    paused = graph_mod.run_for_review("sixth fake jd for edit then approve", title="T", company="C")
    edited = paused.draft.model_copy(update={"profile_line": "Edited before approving."})
    graph_mod.update_draft(paused.job.id, edited)

    resumed = graph_mod.resume_review(paused.job.id, approve=True)

    assert resumed.skip_reason is None
    assert resumed.draft.profile_line == "Edited before approving."
    assert resumed.artifacts.get("cv_pdf")
