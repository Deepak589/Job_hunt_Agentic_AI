"""jobpilot applied / jobpilot outcome (solution.md step 8)."""

from __future__ import annotations

from typer.testing import CliRunner

from agentic_ai.cli import app
from agentic_ai.config import settings
from agentic_ai.render import job_dir_name

runner = CliRunner()


def _isolate_db(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "jobs_db_path", tmp_path / "jobpilot.db")


def _insert_job(job_id: str, company: str = "Acme", title: str = "Engineer") -> None:
    import sqlite3

    from agentic_ai.db.repo import init_db

    init_db(settings.jobs_db_path)
    with sqlite3.connect(settings.jobs_db_path) as conn:
        conn.execute(
            "INSERT INTO jobs (id, company, title) VALUES (?, ?, ?)",
            (job_id, company, title),
        )


def test_applied_records_application_with_sha_when_render_exists(monkeypatch, tmp_path) -> None:
    _isolate_db(monkeypatch, tmp_path)
    _insert_job("job1")
    out_dir = tmp_path / "out"
    monkeypatch.setattr("agentic_ai.render.OUT_DIR", out_dir)
    job_dir = out_dir / job_dir_name("Acme", "Engineer", "job1")
    job_dir.mkdir(parents=True)
    (job_dir / "draft.sha").write_text("abc123")

    result = runner.invoke(app, ["applied", "job1"])

    assert result.exit_code == 0, result.output
    import sqlite3
    with sqlite3.connect(settings.jobs_db_path) as conn:
        row = conn.execute("SELECT job_id, cv_pdf_sha FROM applications WHERE job_id = ?", ("job1",)).fetchone()
    assert row == ("job1", "abc123")


def test_applied_warns_when_no_render_found(monkeypatch, tmp_path) -> None:
    _isolate_db(monkeypatch, tmp_path)
    _insert_job("job2")
    monkeypatch.setattr("agentic_ai.render.OUT_DIR", tmp_path / "out")

    result = runner.invoke(app, ["applied", "job2"])

    assert result.exit_code == 0, result.output
    assert "warning" in result.output.lower()
    import sqlite3
    with sqlite3.connect(settings.jobs_db_path) as conn:
        row = conn.execute("SELECT cv_pdf_sha FROM applications WHERE job_id = ?", ("job2",)).fetchone()
    assert row == ("",)


def test_outcome_updates_row_after_applied(monkeypatch, tmp_path) -> None:
    _isolate_db(monkeypatch, tmp_path)
    _insert_job("job3")
    monkeypatch.setattr("agentic_ai.render.OUT_DIR", tmp_path / "out")
    runner.invoke(app, ["applied", "job3"])

    result = runner.invoke(app, ["outcome", "job3", "interview"])

    assert result.exit_code == 0, result.output
    import sqlite3
    with sqlite3.connect(settings.jobs_db_path) as conn:
        row = conn.execute("SELECT outcome FROM applications WHERE job_id = ?", ("job3",)).fetchone()
    assert row == ("interview",)


def test_outcome_without_prior_applied_is_a_clean_error(monkeypatch, tmp_path) -> None:
    _isolate_db(monkeypatch, tmp_path)

    result = runner.invoke(app, ["outcome", "never-applied", "reject"])

    assert result.exit_code != 0
    assert "jobpilot applied" in result.output


def test_outcome_rejects_invalid_value(monkeypatch, tmp_path) -> None:
    _isolate_db(monkeypatch, tmp_path)

    result = runner.invoke(app, ["outcome", "job4", "bogus"])

    assert result.exit_code != 0
