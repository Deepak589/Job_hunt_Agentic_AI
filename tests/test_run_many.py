"""graph.run_many — concurrent multi-JD execution (§ jobpilot add --dir). Every
LLM-calling node is monkeypatched, same convention/stubs as test_graph_phase3.py, so
this makes zero real API calls. Uses skip_render=True (the Phase 1/2 path) so the run
stops at `review` — recruiter_sim/hiring_manager aren't needed to prove ordering and
concurrency, and staying off the render path avoids the Typst/profile-data dependency."""

from __future__ import annotations

import asyncio

import agentic_ai.graph as graph_mod
from agentic_ai.state import Diagnosis, Draft, DraftBullet


def _fake_extract_requirements(state):
    # Distinguishable per JD: bake the JD text into a note so we can prove ordering.
    return {"requirements": [], "notes": [f"stub extract for: {state.job.jd_text}"]}


def _fake_diagnose(state):
    diagnosis = Diagnosis(hard_gaps=[], soft_gaps=[], disqualifiers=[], matches=["stub match"])
    return {"diagnosis": diagnosis, "notes": ["stub diagnose"]}


def _fake_rewrite(state):
    draft = Draft(
        profile_line=f"Profile for {state.job.jd_text}",
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


def _patch_llm_nodes(monkeypatch) -> None:
    monkeypatch.setattr(graph_mod, "extract_requirements", _fake_extract_requirements)
    monkeypatch.setattr(graph_mod, "diagnose", _fake_diagnose)
    monkeypatch.setattr(graph_mod, "rewrite", _fake_rewrite)
    monkeypatch.setattr(graph_mod, "review", _fake_review)


def test_run_many_returns_one_result_per_jd(monkeypatch) -> None:
    _patch_llm_nodes(monkeypatch)
    jds = ["first unique jd", "second unique jd", "third unique jd"]

    results = asyncio.run(graph_mod.run_many(jds, _skip_render=True, title="T", company="C"))

    assert len(results) == 3
    for jd, state in zip(jds, results):
        assert state.job.jd_text == jd


def test_run_many_preserves_input_order(monkeypatch) -> None:
    _patch_llm_nodes(monkeypatch)
    jds = [f"jd number {i}" for i in range(5)]

    results = asyncio.run(graph_mod.run_many(jds, _skip_render=True, title="T", company="C"))

    assert [s.job.jd_text for s in results] == jds
    assert [s.draft.profile_line for s in results] == [f"Profile for {jd}" for jd in jds]


def test_run_many_makes_no_real_network_calls(monkeypatch) -> None:
    """All 6 LLM-calling nodes route through ChatAnthropic; monkeypatching the 4 that
    run on the skip_render=True path (extract_requirements, diagnose, rewrite, review)
    and never reaching recruiter_sim/hiring_manager on this path is the proof that no
    live model call happens."""
    _patch_llm_nodes(monkeypatch)
    results = asyncio.run(graph_mod.run_many(["only jd"], _skip_render=True))
    assert len(results) == 1
    assert results[0].recruiter is None
    assert results[0].hiring_manager is None
