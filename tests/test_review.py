"""review — 3-sample ensemble, plain majority vote (solution.md step 9)."""

from __future__ import annotations

from agentic_ai.nodes import review as review_mod
from agentic_ai.nodes.review import ReviewResult, review
from agentic_ai.state import Draft, DraftBullet, Job, JobState


def _state() -> JobState:
    job = Job(id="t", source="manual", title="AI Engineer", company="Acme", jd_text="...")
    draft = Draft(
        profile_line="x",
        section_order=["projects"],
        bullets={"projects": [DraftBullet(text="Improved recall by 0.833.", metric="0.833", source_bullet_id="proj.rag_pipeline.b1")]},
        cover_letter="x",
    )
    return JobState(job=job, draft=draft)


def _mock_samples(monkeypatch, results: list[ReviewResult]) -> None:
    calls = iter(results)

    def fake(*args, **kwargs):
        return next(calls), [{"node": "review"}]

    monkeypatch.setattr(review_mod, "invoke_structured", fake)


def test_two_pass_one_fail_majority_pass_uses_median(monkeypatch) -> None:
    results = [
        ReviewResult(score=8, weaknesses=[]),
        ReviewResult(score=9, weaknesses=[]),
        ReviewResult(score=5, weaknesses=["weak bullet 1"]),
    ]
    _mock_samples(monkeypatch, results)
    out = review(_state())
    assert out["scores"].review_score == 8  # median of [5, 8, 9]
    assert out["validation_errors"] == []


def test_one_pass_two_fail_majority_retry_unions_weaknesses(monkeypatch) -> None:
    results = [
        ReviewResult(score=9, weaknesses=[]),
        ReviewResult(score=4, weaknesses=["weak bullet A", "weak bullet B"]),
        ReviewResult(score=3, weaknesses=["weak bullet B", "weak bullet C"]),
    ]
    _mock_samples(monkeypatch, results)
    state = _state().model_copy(update={"attempt_count": 0})
    out = review(state)
    assert out["scores"].review_score == 4  # median of [3, 4, 9]
    assert out["validation_errors"] == ["weak bullet A", "weak bullet B", "weak bullet C"]


def test_makes_exactly_three_llm_calls(monkeypatch) -> None:
    results = [ReviewResult(score=8, weaknesses=[]) for _ in range(3)]
    _mock_samples(monkeypatch, results)
    out = review(_state())
    assert len(out["llm_calls"]) == 3


def test_notes_contains_variance_and_raw_scores(monkeypatch) -> None:
    results = [
        ReviewResult(score=8, weaknesses=[]),
        ReviewResult(score=9, weaknesses=[]),
        ReviewResult(score=5, weaknesses=[]),
    ]
    _mock_samples(monkeypatch, results)
    out = review(_state())
    note = out["notes"][0]
    assert "[5, 8, 9]" in note or "8" in note and "9" in note and "5" in note
    assert "variance" in note
    assert "median" in note


def _patch_validate_facts_clean(monkeypatch) -> None:
    """`_patch_llm_nodes`'s stub draft cites a source bullet id that this machine's
    local (gitignored, personal) master_profile.yaml does not contain, so the real
    validate_facts_node would fail it for reasons unrelated to this CLI test. Stub it
    clean — these tests are about --action/--edits-file routing, not fact-checking."""
    import agentic_ai.graph as graph_mod

    monkeypatch.setattr(
        graph_mod,
        "validate_facts_node",
        lambda state: {"validation_errors": [], "notes": ["stub validate_facts"]},
    )


def test_review_action_approve_is_noninteractive(tmp_path, monkeypatch) -> None:
    from test_graph_phase3 import _patch_llm_nodes
    from typer.testing import CliRunner

    from agentic_ai import graph
    from agentic_ai.cli import app
    from agentic_ai.config import settings

    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")
    monkeypatch.setattr(settings, "jobs_db_path", tmp_path / "jobpilot.db")
    monkeypatch.setattr("agentic_ai.render.OUT_DIR", tmp_path / "out")
    _patch_llm_nodes(monkeypatch)
    _patch_validate_facts_clean(monkeypatch)

    paused = graph.run_for_review(
        "Werkstudent Data Engineer. Must know Python and SQL.", title="DE", company="Acme"
    )

    runner = CliRunner()
    result = runner.invoke(app, ["review", paused.job.id, "--action", "approve"])

    assert result.exit_code == 0, result.output
    assert "typer.prompt" not in result.output  # sanity: no interactive prompt fired


def test_review_action_edit_requires_edits_file(tmp_path, monkeypatch) -> None:
    from test_graph_phase3 import _patch_llm_nodes
    from typer.testing import CliRunner

    from agentic_ai import graph
    from agentic_ai.cli import app
    from agentic_ai.config import settings

    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")
    monkeypatch.setattr(settings, "jobs_db_path", tmp_path / "jobpilot.db")
    _patch_llm_nodes(monkeypatch)
    _patch_validate_facts_clean(monkeypatch)

    paused = graph.run_for_review(
        "Werkstudent Data Engineer. Must know Python and SQL.", title="DE", company="Acme"
    )

    runner = CliRunner()
    result = runner.invoke(app, ["review", paused.job.id, "--action", "edit"])

    assert result.exit_code != 0
    assert "--edits-file" in result.output


def test_review_action_edit_applies_edited_draft(tmp_path, monkeypatch) -> None:
    import json

    from test_graph_phase3 import _patch_llm_nodes
    from typer.testing import CliRunner

    from agentic_ai import graph
    from agentic_ai.cli import app
    from agentic_ai.config import settings

    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")
    monkeypatch.setattr(settings, "jobs_db_path", tmp_path / "jobpilot.db")
    monkeypatch.setattr("agentic_ai.render.OUT_DIR", tmp_path / "out")
    _patch_llm_nodes(monkeypatch)
    _patch_validate_facts_clean(monkeypatch)

    paused = graph.run_for_review(
        "Werkstudent Data Engineer. Must know Python and SQL.", title="DE", company="Acme"
    )
    assert paused.draft is not None
    edited = paused.draft.model_dump(mode="json")
    edited["profile_line"] = "Edited profile line for test"
    edits_path = tmp_path / "edits.json"
    edits_path.write_text(json.dumps(edited))

    runner = CliRunner()
    result = runner.invoke(
        app, ["review", paused.job.id, "--action", "edit", "--edits-file", str(edits_path)]
    )

    assert result.exit_code == 0, result.output


def test_add_review_crash_exits_clean_without_raw_traceback(tmp_path, monkeypatch) -> None:
    """A crash inside run_for_review (e.g. a bad checkpoint resume) must not dump a raw
    Python traceback to stdout — the MCP server's add_job tool reads stdout as the
    user-facing reason, and a multi-page traceback there is worse than useless."""
    from typer.testing import CliRunner

    import agentic_ai.graph as graph_mod
    from agentic_ai.cli import app
    from agentic_ai.config import settings

    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")
    monkeypatch.setattr(settings, "jobs_db_path", tmp_path / "jobpilot.db")
    monkeypatch.setattr(settings, "log_path", tmp_path / "jobpilot.log.jsonl")

    def _boom(jd_text, **job_fields):
        raise RuntimeError("stale checkpoint: Scores deserialization failed")

    monkeypatch.setattr(graph_mod, "run_for_review", _boom)

    runner = CliRunner()
    result = runner.invoke(app, ["add", "--stdin", "--review"], input="some JD text")

    assert result.exit_code == 1
    assert "Traceback (most recent call last)" not in result.output
    assert "stale checkpoint: Scores deserialization failed" in result.output

    logged = [line for line in settings.log_path.read_text().splitlines() if '"crash"' in line]
    assert len(logged) == 1
    assert "stale checkpoint: Scores deserialization failed" in logged[0]
