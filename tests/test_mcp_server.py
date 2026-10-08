"""jobpilot-mcp server tools (docs/superpowers/specs/2026-10-05-mcp-desktop-chrome-apply-design.md).

State-changing tools shell out to the `jobpilot` CLI via subprocess — mocked here, same
as render.py's own subprocess calls are mocked elsewhere in this suite. Read-only tools
call graph.py/db/repo.py/profile.py directly and are tested against tmp fixture files,
same conventions as test_cli_applications.py.
"""

from __future__ import annotations

import asyncio
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

    result = asyncio.run(server.list_pending_reviews())

    assert result == [
        {"job_id": "job1", "company": "Acme", "title": "DE", "score": 7},
        {"job_id": "job2", "company": "Acme", "title": "DE", "score": 7},
    ]


def test_get_job_returns_structured_summary(monkeypatch, tmp_path) -> None:
    from agentic_ai.state import Diagnosis, Job, JobState

    job = Job(id="job1", source="manual", title="DE", company="Acme", jd_text="JD text", url="https://x")
    state = JobState(job=job, diagnosis=Diagnosis(matches=[], gaps=[], questions=[]))
    monkeypatch.setattr(server.graph, "get_paused_state", lambda jid: state)

    result = asyncio.run(server.get_job("job1"))

    assert result["job_id"] == "job1"
    assert result["jd_text"] == "JD text"
    assert result["url"] == "https://x"
    assert result["draft"] is None
    assert result["cv_pdf"] is None


def test_get_job_missing_returns_error(monkeypatch) -> None:
    def raise_keyerror(jid):
        raise KeyError(f"no paused job found for id {jid!r}")

    monkeypatch.setattr(server.graph, "get_paused_state", raise_keyerror)

    result = asyncio.run(server.get_job("missing"))

    assert "error" in result


def test_list_pending_reviews_missing_paused_state_returns_error(monkeypatch) -> None:
    def fake_ids():
        return ["stale-job"]

    def raise_keyerror(jid):
        raise KeyError(f"no paused job found for id {jid!r}")

    monkeypatch.setattr(server.graph, "list_pending_review_ids", fake_ids)
    monkeypatch.setattr(server.graph, "get_paused_state", raise_keyerror)

    result = asyncio.run(server.list_pending_reviews())

    assert result == [{"error": "\"no paused job found for id 'stale-job'\""}]


def test_get_applicant_profile_missing_file_returns_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "profile_path", tmp_path / "does_not_exist.yaml")

    result = server.get_applicant_profile()

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

    result = asyncio.run(server.add_job(jd_text="some JD text"))

    assert captured["cmd"][:2] == ["jobpilot", "add"]
    assert "--stdin" in captured["cmd"]
    assert result["status"] == "ok"


def test_add_job_cli_failure_returns_error(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text, timeout, input=None):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="budget cap exceeded\n")

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    result = asyncio.run(server.add_job(jd_text="some JD text"))

    assert result == {"error": "budget cap exceeded"}


def test_add_job_cli_failure_falls_back_to_stdout_tail(monkeypatch) -> None:
    """rich.Console (jobpilot's CLI) writes its human-readable reason to stdout, not
    stderr — a reject/hard-gap/budget-cap exit must surface that, not a generic
    "jobpilot exited 1"."""

    def fake_run(cmd, capture_output, text, timeout, input=None):
        return subprocess.CompletedProcess(cmd, 1, stdout="hard gap: no Rust experience\n", stderr="")

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    result = asyncio.run(server.add_job(jd_text="some JD text"))

    assert result == {"error": "hard gap: no Rust experience"}


def test_add_job_stdin_branch_missing_binary_returns_error(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text, timeout, input=None):
        raise FileNotFoundError("jobpilot")

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    result = asyncio.run(server.add_job(jd_text="some JD text"))

    assert "error" in result
    assert "not found on PATH" in result["error"]


def test_review_job_approve_shells_out(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text, timeout, input=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="RESULT\n", stderr="")

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    result = asyncio.run(server.review_job(job_id="job1", action="approve"))

    assert captured["cmd"] == ["jobpilot", "review", "job1", "--action", "approve"]
    assert result["status"] == "ok"


def test_review_job_edit_writes_tmp_file_and_shells_out(monkeypatch, tmp_path) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text, timeout, input=None):
        captured["cmd"] = cmd
        edits_path = cmd[cmd.index("--edits-file") + 1]
        with open(edits_path) as f:
            captured["edits_contents"] = json.load(f)
        return subprocess.CompletedProcess(cmd, 0, stdout="re-paused\n", stderr="")

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    edits = {"profile_line": "x", "section_order": [], "bullets": {}, "cover_letter": "y"}
    result = asyncio.run(server.review_job(job_id="job1", action="edit", edits=edits))

    assert "--action" in captured["cmd"] and "edit" in captured["cmd"]
    assert captured["edits_contents"] == edits
    assert result["status"] == "ok"


def test_mark_applied_shells_out(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text, timeout, input=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="recorded application for job1\n", stderr="")

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    result = asyncio.run(server.mark_applied(job_id="job1"))

    assert captured["cmd"] == ["jobpilot", "applied", "job1"]
    assert result["status"] == "ok"


def test_cli_shellout_timeout_returns_error(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text, timeout, input=None):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    result = asyncio.run(server.mark_applied(job_id="job1"))

    assert "error" in result


# --------------------------------------------------- real MCP hosting path (async)


def test_get_job_works_under_the_real_mcp_host(monkeypatch) -> None:
    """Regression test for the original bug: FastMCP invokes tools directly on its
    own running event loop. A tool that calls `asyncio.run(...)` internally (as
    `graph.get_paused_state` does) raises `RuntimeError: asyncio.run() cannot be
    called from a running event loop` under that host — calling the tool as a plain
    function (no loop running) hides this. Go through `server.mcp.call_tool` so a
    real loop is already running when the tool body executes."""
    from agentic_ai.state import Diagnosis, Job, JobState

    job = Job(id="job1", source="manual", title="DE", company="Acme", jd_text="JD text")
    state = JobState(job=job, diagnosis=Diagnosis(matches=[], gaps=[], questions=[]))
    monkeypatch.setattr(server.graph, "get_paused_state", lambda jid: state)

    _blocks, result = asyncio.run(server.mcp.call_tool("get_job", {"job_id": "job1"}))

    assert result["job_id"] == "job1"
    assert result["jd_text"] == "JD text"


def test_list_pending_reviews_works_under_the_real_mcp_host(monkeypatch) -> None:
    """Same regression as above for the other tool that crashed under the real host."""

    class _FakeState:
        def __init__(self, job_id):
            self.job = type("J", (), {"id": job_id, "company": "Acme", "title": "DE"})()
            self.scores = type("S", (), {"review_score": 7})()

    monkeypatch.setattr(server.graph, "list_pending_review_ids", lambda: ["job1"])
    monkeypatch.setattr(server.graph, "get_paused_state", lambda jid: _FakeState(jid))

    _blocks, structured = asyncio.run(server.mcp.call_tool("list_pending_reviews", {}))

    assert structured["result"] == [{"job_id": "job1", "company": "Acme", "title": "DE", "score": 7}]
