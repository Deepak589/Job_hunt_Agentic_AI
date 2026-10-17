"""graph.run_many — concurrent multi-JD execution (§ jobpilot add --dir). Every
LLM-calling node is monkeypatched, same convention/stubs as test_graph_phase3.py, so
this makes zero real API calls. Uses skip_render=True (the Phase 1/2 path) so the run
stops after validate_facts — rendering/review aren't needed to prove ordering and
concurrency, and staying off the render path avoids the Chrome/profile-data dependency."""

from __future__ import annotations

import asyncio

import agentic_ai.graph as graph_mod
from agentic_ai.state import Diagnosis, Draft, DraftBullet


def _fake_analyze(state):
    # Distinguishable per JD: bake the JD text into a note so we can prove ordering.
    diagnosis = Diagnosis(hard_gaps=[], soft_gaps=[], disqualifiers=[], matches=["stub match"])
    return {"requirements": [], "diagnosis": diagnosis, "notes": [f"stub analyze for: {state.job.jd_text}"]}


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


def _fake_validate_facts(state):
    return {"validation_errors": [], "notes": ["stub validate_facts"]}


def _fake_agent_cv(state):
    """The default topology's single node does `analyze` AND `rewrite`, so its stub is the
    union of both — the same contract `agent_cv_node` has with everything downstream."""
    return {**_fake_analyze(state), **_fake_rewrite(state), "graph_path": "agent_cv"}


def _patch_llm_nodes(monkeypatch) -> None:
    """Patch BOTH topologies' drafting nodes so these tests cost nothing whichever one
    `settings.use_agent_cv` selects. Patching only `analyze`/`rewrite` left the default
    (agent) path reaching the real API."""
    monkeypatch.setattr(graph_mod, "agent_cv_node", _fake_agent_cv)
    monkeypatch.setattr(graph_mod, "analyze", _fake_analyze)
    monkeypatch.setattr(graph_mod, "rewrite", _fake_rewrite)
    monkeypatch.setattr(graph_mod, "validate_facts_node", _fake_validate_facts)


def _isolate_db(monkeypatch, tmp_path) -> None:
    """run_many now checks already_processed()/BudgetGuard against settings.jobs_db_path
    (solution.md step 1), and opens an AsyncSqliteSaver against settings.checkpoint_db_path
    (solution.md step 2) — point both at throwaway files so tests never touch real project
    state."""
    monkeypatch.setattr(graph_mod.settings, "jobs_db_path", tmp_path / "jobpilot.db")
    monkeypatch.setattr(graph_mod.settings, "checkpoint_db_path", tmp_path / "checkpoints.db")


def test_run_many_returns_one_result_per_jd(monkeypatch, tmp_path) -> None:
    _patch_llm_nodes(monkeypatch)
    _isolate_db(monkeypatch, tmp_path)
    jds = ["first unique jd", "second unique jd", "third unique jd"]

    results = asyncio.run(graph_mod.run_many(jds, _skip_render=True, title="T", company="C"))

    assert len(results) == 3
    for jd, state in zip(jds, results):
        assert state.job.jd_text == jd


def test_run_many_preserves_input_order(monkeypatch, tmp_path) -> None:
    _patch_llm_nodes(monkeypatch)
    _isolate_db(monkeypatch, tmp_path)
    jds = [f"jd number {i}" for i in range(5)]

    results = asyncio.run(graph_mod.run_many(jds, _skip_render=True, title="T", company="C"))

    assert [s.job.jd_text for s in results] == jds
    assert [s.draft.profile_line for s in results] == [f"Profile for {jd}" for jd in jds]


def test_run_many_makes_no_real_network_calls(monkeypatch, tmp_path) -> None:
    """All 6 LLM-calling nodes route through ChatAnthropic; monkeypatching the 4 that
    run on the skip_render=True path (extract_requirements, diagnose, rewrite, review)
    and never reaching recruiter_sim/hiring_manager on this path is the proof that no
    live model call happens."""
    _patch_llm_nodes(monkeypatch)
    _isolate_db(monkeypatch, tmp_path)
    results = asyncio.run(graph_mod.run_many(["only jd"], _skip_render=True))
    assert len(results) == 1
    assert results[0].recruiter is None
    assert results[0].hiring_manager is None


def test_run_many_skips_a_job_already_processed(monkeypatch, tmp_path) -> None:
    """solution.md step 1: re-running the same JD text (same content-hash job id)
    must not re-pay for extract/diagnose/rewrite — it should skip before the graph
    ever runs."""
    from agentic_ai.db.repo import persist_run
    from agentic_ai.state import Job, JobState, Scores

    _isolate_db(monkeypatch, tmp_path)
    jd_text = "seen this one already"
    job_id = graph_mod.job_id("manual", "", jd_text)
    from agentic_ai.state import AtsScore

    seen = JobState(
        job=Job(id=job_id, source="manual", title="T", company="C", jd_text=jd_text),
        scores=Scores(hard_coverage=1.0, review_score=9),
        # persist_run reads verdict off hiring_manager/ats — set ats so `verdict` is
        # non-null, which is what already_processed() checks for.
        ats=AtsScore(total=97.0, verdict="apply", gates={}, components={}, report=""),
    )
    persist_run(seen, db_path=graph_mod.settings.jobs_db_path)

    def _fail(*a, **k):
        raise AssertionError("already-processed job must not reach the drafting node")

    monkeypatch.setattr(graph_mod, "agent_cv_node", _fail)
    monkeypatch.setattr(graph_mod, "analyze", _fail)

    results = asyncio.run(graph_mod.run_many([jd_text], _skip_render=True))

    assert len(results) == 1
    assert results[0].skip_reason is not None
    assert "already_processed" in results[0].skip_reason


def test_run_many_rejects_jobs_over_the_daily_budget_cap(monkeypatch, tmp_path) -> None:
    """solution.md step 1: the cap check reads persisted + in-flight-RESERVED spend, so
    concurrent jobs in the same batch can't all pass a stale persisted-only check."""
    from agentic_ai import budget as budget_mod

    _patch_llm_nodes(monkeypatch)
    _isolate_db(monkeypatch, tmp_path)
    monkeypatch.setattr(graph_mod.settings, "max_daily_cost_usd", 0.01)
    monkeypatch.setattr(budget_mod, "estimated_job_cost", lambda: 0.02)

    jds = ["job over cap 1", "job over cap 2"]
    results = asyncio.run(graph_mod.run_many(jds, _skip_render=True))

    assert len(results) == 2
    assert all(s.skip_reason == "budget_cap_hit" for s in results)


def test_run_many_persists_cost_from_a_crashed_job(monkeypatch, tmp_path) -> None:
    """A node raising mid-graph (e.g. the real max_tokens parse failures seen in
    judge_requirements/rewrite) must not lose the spend already made on earlier nodes —
    db/repo.py's already_processed() docstring promises "a crashed run leaves no row
    (or a row with verdict NULL)"; this proves the NULL-row half actually happens."""
    import sqlite3

    def _fake_analyze_with_cost(state):
        return {
            "requirements": [],
            "diagnosis": Diagnosis(),
            "llm_calls": [
                {"node": "analyze", "model": "claude-sonnet-5",
                 "input_tokens": 100, "output_tokens": 50, "cache_read_tokens": 0,
                 "cache_creation_tokens": 0, "cost_usd": 0.0012}
            ],
        }

    def _fake_rewrite_crashes(state):
        raise RuntimeError("rewrite failed: Invalid json output (stop_reason=max_tokens)")

    # Pinned to the analyze topology on purpose: this test is about spend BANKED by a
    # completed node surviving a later node's crash, and only a two-call path has an
    # earlier node to bank it. The agent path's equivalent is the test below.
    monkeypatch.setattr(graph_mod.settings, "use_agent_cv", False)
    monkeypatch.setattr(graph_mod, "analyze", _fake_analyze_with_cost)
    monkeypatch.setattr(graph_mod, "rewrite", _fake_rewrite_crashes)
    _isolate_db(monkeypatch, tmp_path)

    try:
        asyncio.run(graph_mod.run_many(["jd that crashes"], _skip_render=True))
    except RuntimeError:
        pass

    with sqlite3.connect(graph_mod.settings.jobs_db_path) as conn:
        row = conn.execute("SELECT verdict, tokens_in, tokens_out, cost_usd FROM runs").fetchone()

    assert row is not None, "crashed run left no row at all"
    verdict, tokens_in, tokens_out, cost_usd = row
    assert verdict is None
    assert tokens_in == 100
    assert tokens_out == 50
    assert cost_usd == 0.0012


def test_agent_path_crash_still_leaves_a_null_verdict_row(monkeypatch, tmp_path) -> None:
    """The agent path's half of the crash guarantee, and the one place it is weaker.

    `_invoke_tracked` persists whatever the checkpointer captured before a crash. On the
    two-call path that includes `analyze`'s recorded spend. On the agent path the first
    node that spends is also the first node that can crash, so there is nothing banked yet
    and the cost of the in-flight call is NOT recorded — Anthropic still billed it. What
    must survive either way is the row itself with a NULL verdict, which is what
    `already_processed` reads to decide a job is re-runnable rather than done.
    """
    import sqlite3

    def _crash(state):
        raise RuntimeError("agent_cv failed: Invalid json output (stop_reason=max_tokens)")

    monkeypatch.setattr(graph_mod, "agent_cv_node", _crash)
    _isolate_db(monkeypatch, tmp_path)

    try:
        asyncio.run(graph_mod.run_many(["a jd that crashes on the agent path"], _skip_render=True))
    except RuntimeError:
        pass

    with sqlite3.connect(graph_mod.settings.jobs_db_path) as conn:
        row = conn.execute("SELECT verdict, cost_usd FROM runs").fetchone()

    assert row is not None, "crashed agent-path run left no row at all"
    verdict, cost_usd = row
    assert verdict is None, "a crashed run is re-runnable, not done"
    assert cost_usd == 0.0, "documents the gap: the in-flight call's spend is not banked"
