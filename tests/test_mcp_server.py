"""jobpilot-mcp server tools (docs/superpowers/specs/2026-10-05-mcp-desktop-chrome-apply-design.md).

State-changing tools shell out to the `jobpilot` CLI via subprocess — mocked here, same
as render.py's own subprocess calls are mocked elsewhere in this suite. Read-only tools
call graph.py/db/repo.py/profile.py directly and are tested against tmp fixture files,
same conventions as test_cli_applications.py.
"""

from __future__ import annotations

import json
import subprocess

from agentic_ai.config import settings
from agentic_ai.mcp import server


def test_list_pending_reviews_returns_job_summaries(monkeypatch) -> None:
    def fake_ids():
        return ["job1", "job2"]

    class _FakeState:
        def __init__(self, job_id):
            self.job = type("J", (), {"id": job_id, "company": "Acme", "title": "DE"})()
            self.scores = type("S", (), {"review_score": 7})()

    monkeypatch.setattr(server.graph, "list_pending_review_ids", fake_ids)
    monkeypatch.setattr(server.graph, "get_paused_state", lambda jid: _FakeState(jid))

    result = server.list_pending_reviews()

    assert result == [
        {"job_id": "job1", "company": "Acme", "title": "DE", "score": 7},
        {"job_id": "job2", "company": "Acme", "title": "DE", "score": 7},
    ]


def test_get_job_returns_structured_summary(monkeypatch, tmp_path) -> None:
    from agentic_ai.state import Diagnosis, Job, JobState

    job = Job(id="job1", source="manual", title="DE", company="Acme", jd_text="JD text", url="https://x")
    state = JobState(job=job, diagnosis=Diagnosis(matches=[], gaps=[], questions=[]))
    monkeypatch.setattr(server.graph, "get_paused_state", lambda jid: state)

    result = server.get_job("job1")

    assert result["job_id"] == "job1"
    assert result["jd_text"] == "JD text"
    assert result["url"] == "https://x"
    assert result["draft"] is None
    assert result["cv_pdf"] is None


def test_get_job_missing_returns_error(monkeypatch) -> None:
    def raise_keyerror(jid):
        raise KeyError(f"no paused job found for id {jid!r}")

    monkeypatch.setattr(server.graph, "get_paused_state", raise_keyerror)

    result = server.get_job("missing")

    assert "error" in result


def test_get_applicant_profile_reads_identity(monkeypatch, tmp_path) -> None:
    profile_yaml = tmp_path / "master_profile.yaml"
    profile_yaml.write_text(
        "identity:\n"
        "  name: Jane Example\n"
        "  email: jane@example.com\n"
        "  phone: \"+49 123\"\n"
        "  location: Berlin, Germany\n"
        "  github: github.com/jane\n"
        "  linkedin: linkedin.com/in/jane\n"
        "skills: {}\n"
        "experience: []\n"
        "projects: []\n"
    )
    monkeypatch.setattr(settings, "profile_path", profile_yaml)

    result = server.get_applicant_profile()

    assert result == {
        "name": "Jane Example",
        "email": "jane@example.com",
        "phone": "+49 123",
        "location": "Berlin, Germany",
        "github": "github.com/jane",
        "linkedin": "linkedin.com/in/jane",
    }


def test_add_job_shells_out_to_cli(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text, timeout, input=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="paused for review  job id: abc123\n", stderr="")

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    result = server.add_job(jd_text="some JD text")

    assert captured["cmd"][:2] == ["jobpilot", "add"]
    assert "--stdin" in captured["cmd"]
    assert result["status"] == "ok"


def test_add_job_cli_failure_returns_error(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text, timeout, input=None):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="budget cap exceeded\n")

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    result = server.add_job(jd_text="some JD text")

    assert result == {"error": "budget cap exceeded"}


def test_review_job_approve_shells_out(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="RESULT\n", stderr="")

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    result = server.review_job(job_id="job1", action="approve")

    assert captured["cmd"] == ["jobpilot", "review", "job1", "--action", "approve"]
    assert result["status"] == "ok"


def test_review_job_edit_writes_tmp_file_and_shells_out(monkeypatch, tmp_path) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        edits_path = cmd[cmd.index("--edits-file") + 1]
        with open(edits_path) as f:
            captured["edits_contents"] = json.load(f)
        return subprocess.CompletedProcess(cmd, 0, stdout="re-paused\n", stderr="")

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    edits = {"profile_line": "x", "section_order": [], "bullets": {}, "cover_letter": "y"}
    result = server.review_job(job_id="job1", action="edit", edits=edits)

    assert "--action" in captured["cmd"] and "edit" in captured["cmd"]
    assert captured["edits_contents"] == edits
    assert result["status"] == "ok"


def test_mark_applied_shells_out(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="recorded application for job1\n", stderr="")

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    result = server.mark_applied(job_id="job1")

    assert captured["cmd"] == ["jobpilot", "applied", "job1"]
    assert result["status"] == "ok"


def test_cli_shellout_timeout_returns_error(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    result = server.mark_applied(job_id="job1")

    assert "error" in result
