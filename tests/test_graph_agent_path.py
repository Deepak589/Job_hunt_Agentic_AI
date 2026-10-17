"""The agent topology: `agent_cv` as a graph node (settings.use_agent_cv).

`agent_cv` used to be a second entry point (`jobpilot agent-draft`) that drafted well and
reached none of the pipeline — no blocking validator, no review checkpoint, no run row, no
dedupe, no cost ledger, no preferences, invisible to the MCP review tools. These tests pin
what moving it inside the graph was for: the same one reasoning call, now behind every
guarantee the graph already had.

LLM-calling nodes are stubbed; what is under test is the wiring, which is the part that
can silently differ between the two topologies.
"""

from __future__ import annotations

import pytest

import agentic_ai.graph as graph_mod
from agentic_ai.config import settings
from agentic_ai.nodes import agent_cv as agent_cv_mod
from agentic_ai.nodes.agent_cv import AgentCvResult, agent_cv_node
from agentic_ai.state import Diagnosis, Draft, DraftBullet, Job, JobState, Requirement, Scores


def _draft() -> Draft:
    return Draft(
        profile_line="Test profile line.",
        section_order=["projects"],
        bullets={
            "projects": [
                DraftBullet(text="Improved recall by 0.833.", metric="0.833", source_bullet_id="proj.rag_pipeline.b1")
            ]
        },
        cover_letter="x",
    )


def _result(requirements: list[Requirement] | None = None) -> AgentCvResult:
    return AgentCvResult(
        draft=_draft(),
        requirements=requirements if requirements is not None else [Requirement(text="Python", type="hard", covered=True)],
        diagnosis=Diagnosis(matches=["stub match"]),
        scores=Scores(hard_coverage=1.0, soft_coverage=1.0),
        self_score=8,
        recruiter_verdict="pass",
        final_verdict="apply",
        verdict_reason="strong fit",
        llm_calls=[{"cost_usd": 0.18}],
    )


def _state(**kw) -> JobState:
    return JobState(job=Job(id="j", source="manual", title="T", jd_text="a jd", location="Berlin"), **kw)


# --------------------------------------------------------------------------- node contract


def test_node_returns_the_union_of_what_analyze_and_rewrite_returned(monkeypatch) -> None:
    monkeypatch.setattr(agent_cv_mod, "agent_cv", lambda *a, **k: _result())

    out = agent_cv_node(_state())

    # analyze's half
    assert [r.text for r in out["requirements"]] == ["Python"]
    assert out["diagnosis"].matches == ["stub match"]
    # rewrite's half
    assert out["draft"].profile_line == "Test profile line."
    assert out["attempt_count"] == 1, "the call produced a draft, so it spends a retry"
    assert out["llm_calls"] == [{"cost_usd": 0.18}]


def test_the_three_agent_only_judgments_land_in_state_instead_of_a_terminal(monkeypatch) -> None:
    """On the side path these were printed and lost. In state they reach the review
    artifact, which is the whole point of the move."""
    monkeypatch.setattr(agent_cv_mod, "agent_cv", lambda *a, **k: _result())

    out = agent_cv_node(_state())

    assert out["scores"].review_score == 8
    assert out["recruiter"].result == "pass"
    assert out["hiring_manager"].verdict == "apply"


def test_node_records_the_topology_for_resume(monkeypatch) -> None:
    monkeypatch.setattr(agent_cv_mod, "agent_cv", lambda *a, **k: _result())
    assert agent_cv_node(_state())["graph_path"] == "agent_cv"


def test_node_skips_when_prefilled(monkeypatch) -> None:
    def boom(*a, **k):
        raise AssertionError("must not call the model when requirements+diagnosis+draft are prefilled")

    monkeypatch.setattr(agent_cv_mod, "agent_cv", boom)
    prefilled = _state(
        requirements=[Requirement(text="Python", type="hard")], diagnosis=Diagnosis(), draft=_draft()
    )

    assert agent_cv_node(prefilled) == {}


# --------------------------------------------------------------------------- topology


def _stub_nodes(monkeypatch, requirements: list[Requirement] | None = None, fact_errors=None):
    """Returns a call counter so a test can assert WHICH node ran, not just the outcome."""
    calls = {"agent_cv": 0, "rewrite": 0, "validate_facts": 0}
    errors = list(fact_errors or [])

    def fake_agent_cv(state):
        calls["agent_cv"] += 1
        r = _result(requirements)
        return {
            "requirements": r.requirements, "diagnosis": r.diagnosis, "scores": r.scores,
            "draft": r.draft, "attempt_count": state.attempt_count + 1,
            "graph_path": "agent_cv", "notes": ["stub agent_cv"],
        }

    def fake_rewrite(state):
        calls["rewrite"] += 1
        return {"draft": _draft(), "attempt_count": state.attempt_count + 1, "notes": ["stub rewrite"]}

    def fake_validate(state):
        calls["validate_facts"] += 1
        out = [errors.pop(0)] if errors else []
        return {"validation_errors": out, "notes": ["stub validate_facts"]}

    monkeypatch.setattr(graph_mod, "agent_cv_node", fake_agent_cv)
    monkeypatch.setattr(graph_mod, "rewrite", fake_rewrite)
    monkeypatch.setattr(graph_mod, "validate_facts_node", fake_validate)
    return calls


def test_unmet_disqualifier_still_skips_on_the_agent_path(monkeypatch, tmp_path) -> None:
    """The categorical gate is unchanged — it just now runs after a draft exists, because
    one call returns both (config.use_agent_cv has the cost note)."""
    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")
    _stub_nodes(monkeypatch, requirements=[Requirement(text="C1 German", type="disqualifier", covered=False)])

    state = graph_mod.run("a jd that disqualifies, quite unique", _skip_render=True)

    assert state.skip_reason is not None and "C1 German" in state.skip_reason
    assert state.artifacts == {}


def test_a_fact_error_retries_through_rewrite_not_back_through_agent_cv(monkeypatch, tmp_path) -> None:
    """Re-running `agent_cv` would re-pay for extraction AND could hand back a different
    requirement list after `hard_gap_gate` had already ruled on the first one."""
    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")
    calls = _stub_nodes(monkeypatch, fact_errors=["cover_letter: claims unevidenced tech 'llm'"])

    state = graph_mod.run("a jd needing one retry, quite unique", _skip_render=True)

    assert calls["agent_cv"] == 1, "the reasoning call must not be repeated by the retry loop"
    assert calls["rewrite"] == 1, "the retry goes to rewrite, which gets the exact errors"
    assert calls["validate_facts"] == 2
    assert state.validation_errors == []


def test_fact_errors_still_block_when_the_retry_budget_runs_out(monkeypatch, tmp_path) -> None:
    """The failure the report-only validator on the side path could not produce: a draft
    with a fact error must never reach render."""
    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")
    err = "cover_letter: claims unevidenced tech 'kubernetes'"
    _stub_nodes(monkeypatch, fact_errors=[err] * (settings.max_rewrite_attempts + 2))

    state = graph_mod.run("a jd that cannot be fixed, quite unique", _skip_render=True)

    assert state.skip_reason is not None and "fact validation failed" in state.skip_reason
    assert state.artifacts == {}


def test_the_analyze_topology_still_builds_and_records_itself(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")
    monkeypatch.setattr(settings, "use_agent_cv", False)

    def fake_analyze(state):
        return {
            "requirements": [Requirement(text="Python", type="hard", covered=True)],
            "diagnosis": Diagnosis(matches=["stub match"]),
            "graph_path": "analyze",
            "notes": ["stub analyze"],
        }

    def boom(state):
        raise AssertionError("agent_cv must not run when use_agent_cv is False")

    monkeypatch.setattr(graph_mod, "analyze", fake_analyze)
    monkeypatch.setattr(graph_mod, "agent_cv_node", boom)
    monkeypatch.setattr(graph_mod, "rewrite", lambda s: {"draft": _draft(), "attempt_count": 1, "notes": []})
    monkeypatch.setattr(graph_mod, "validate_facts_node", lambda s: {"validation_errors": [], "notes": []})

    state = graph_mod.run("a jd on the analyze path, quite unique", _skip_render=True)

    assert state.graph_path == "analyze"
    assert state.draft is not None


# --------------------------------------------------------------------------- resume


def test_resume_finishes_a_job_on_the_topology_that_started_it(monkeypatch, tmp_path) -> None:
    """`aget_state` can only name a pending task whose node the probe graph registers, so
    probing an `agent_cv` checkpoint with an `analyze` graph reads back an EMPTY `next` —
    which `_resume` would take for "already finished" and return half-built state as the
    result. `JobState.graph_path` is what stops that."""
    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")
    monkeypatch.setattr(settings, "jobs_db_path", tmp_path / "jobpilot.db")

    jd = "a jd that crashes mid-draft, quite unique"

    def crash(state):
        raise RuntimeError("simulated crash while drafting")

    monkeypatch.setattr(graph_mod, "agent_cv_node", crash)
    with pytest.raises(RuntimeError, match="simulated crash while drafting"):
        graph_mod.run(jd, _skip_render=True)

    # The default flips underneath the crashed job — a config change, a different machine,
    # an env var. The checkpoint still has to be finishable.
    calls = _stub_nodes(monkeypatch)
    # `resume` always rebuilds with skip_render=False (pre-existing: the private
    # `_skip_render` kwarg lives on the run, not the checkpoint), so the tail needs stubs.
    monkeypatch.setattr(graph_mod, "render_documents", lambda s: {"artifacts": {"cv": "cv.pdf"}})
    monkeypatch.setattr(graph_mod, "score_ats", lambda s: {"notes": ["stub score_ats"]})
    monkeypatch.setattr(settings, "use_agent_cv", False)
    monkeypatch.setattr(graph_mod, "analyze", lambda s: (_ for _ in ()).throw(
        AssertionError("resumed on the wrong topology — this checkpoint was written by agent_cv")
    ))

    resumed = graph_mod.resume(graph_mod.job_id("manual", "", jd))

    assert calls["agent_cv"] == 1, "resume must run the node the checkpoint stopped at"
    assert resumed.draft is not None, "a resumed crash must not read back as 'already finished'"


def test_a_rerun_does_not_inherit_the_previous_runs_skip_reason(monkeypatch, tmp_path) -> None:
    """`thread_id` is the job id, so re-running a job resumes the same checkpoint and a
    channel with no reducer survives. The railway posting was gate-skipped, then re-run
    after the typing fix and passed — and arrived at review still carrying "6 uncovered
    hard requirements" from the run before. `persist_run` recorded that, so a successful
    run went into the ledger as a skip with no verdict and no ats.
    """
    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")
    jd = "a jd skipped once then passed, quite unique"

    # run 1: the gate refuses it
    _stub_nodes(monkeypatch, requirements=[Requirement(text="C1 German", type="disqualifier", covered=False)])
    first = graph_mod.run(jd, _skip_render=True)
    assert first.skip_reason is not None

    # run 2: same job, same thread — now it qualifies
    _stub_nodes(monkeypatch, requirements=[Requirement(text="Python", type="hard", covered=True)])
    second = graph_mod.run(jd, _skip_render=True)

    assert second.skip_reason is None, "a passing re-run must not inherit the old skip reason"
    assert second.draft is not None
    assert second.attempt_count == 1, "the retry budget resets for a new run"
