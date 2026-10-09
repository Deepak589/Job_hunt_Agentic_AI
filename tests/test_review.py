"""CLI `review` command routing (--action / --edits-file) against the slim graph."""

from __future__ import annotations


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
