# MCP Desktop + Chrome-Assisted Apply Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local stdio MCP server so Claude Desktop can drive the jobpilot pipeline
conversationally (list/review/approve jobs) and hand tailored CV/cover-letter data to
Claude-in-Chrome for form-filling — never submitting.

**Architecture:** `src/agentic_ai/mcp/server.py` exposes 6 `FastMCP` tools. Two
(`add_job`, `review_job`, `mark_applied` — 3 state-changing tools) shell out to the
existing `jobpilot` CLI via `subprocess.run` so there is exactly one orchestration path
(same reasoning `CLAUDE.md` gives for Cowork). Three read-only tools
(`list_pending_reviews`, `get_job`, `get_applicant_profile`) call `graph.py` /
`db/repo.py` / `profile.py` functions in-process — no subprocess needed for reads.

**Tech Stack:** `mcp` Python SDK (`mcp.server.fastmcp.FastMCP`), stdio transport,
stdlib `subprocess`, existing `typer` CLI, existing `pytest`/`CliRunner` conventions.

**Spec:** `docs/superpowers/specs/2026-10-05-mcp-desktop-chrome-apply-design.md`

## Global Constraints

- Never auto-submits. No tool clicks a submit button or posts anything — that stays in
  Claude-in-Chrome's hands, driven by the user's own "ok, submitting" instruction in chat.
- No site-specific scraping/filling code — Claude-in-Chrome reads whatever page is open
  itself; the MCP server only ever hands it structured data (text + file paths).
- Single local process, stdio transport, no network listener, no auth layer.
- State-changing tools shell out to the `jobpilot` CLI — never re-import LangGraph nodes
  directly from the MCP server (that would be a second, untested orchestration path).
- Errors from CLI shell-outs or missing files/DB rows become `{"error": "..."}` tool
  results, never a raw exception/traceback surfaced to Claude.

---

## File structure

- **New** `src/agentic_ai/mcp/__init__.py` — empty, makes `mcp` a package.
- **New** `src/agentic_ai/mcp/server.py` — the `FastMCP` app and its 6 tools, plus a
  `main()` entry point.
- **New** `tests/test_mcp_server.py` — one test per tool, `monkeypatch`-based, no real
  subprocess/DB/LLM calls.
- **Modify** `src/agentic_ai/graph.py` — add `list_pending_review_ids()`. No `jobs` row
  exists for a job until `resume_review` completes it (confirmed: `persist_run` is only
  called from `review()`/`resume()`, never on the pause path), so listing pending jobs
  must scan `checkpoints.db` directly, not `jobpilot.db`. This corrects an assumption in
  the design spec's tools table.
- **Modify** `src/agentic_ai/cli.py` — add `--action` / `--edits-file` options to
  `review()` so it has a non-interactive path (the spec's flagged open question).
  Interactive behavior (no `--action` given) is unchanged.
- **Modify** `pyproject.toml` — add `mcp>=1.2` to `dependencies`, add
  `jobpilot-mcp = "agentic_ai.mcp.server:main"` to `[project.scripts]`.
- **Modify** `README.md` (or `CLAUDE.md`, whichever already documents CLI usage) — one
  section: how to point Claude Desktop's MCP config at
  `uv run --directory <repo> jobpilot-mcp`.

---

## Task 1: `list_pending_review_ids()` in `graph.py`

**Files:**
- Modify: `src/agentic_ai/graph.py` (append near `get_paused_state`, after line 391)
- Test: `tests/test_graph_phase3.py` (existing file already covers `run_for_review`/
  `resume_review`/`get_paused_state` — add to it, don't create a new test file)

**Interfaces:**
- Consumes: `settings.checkpoint_db_path` (`config.py`), `_open_checkpointer()`,
  `build_graph(skip_render, checkpointer, review_pause)` — all already defined above in
  `graph.py`.
- Produces: `list_pending_review_ids() -> list[str]` — job ids currently paused at
  `human_review`. Used by Task 3's `list_pending_reviews` MCP tool.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_graph_phase3.py`:

```python
def test_list_pending_review_ids_returns_only_paused_jobs(tmp_path, monkeypatch) -> None:
    from agentic_ai import graph
    from agentic_ai.config import settings

    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")

    paused = graph.run_for_review(
        "Werkstudent Data Engineer. Must know Python and SQL.", title="DE", company="Acme"
    )
    finished = graph.run_for_review(
        "Werkstudent Backend. Must know Python and SQL.", title="BE", company="Acme2"
    )
    graph.resume_review(finished.job.id, action="reject")

    pending = graph.list_pending_review_ids()

    assert paused.job.id in pending
    assert finished.job.id not in pending
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_graph_phase3.py::test_list_pending_review_ids_returns_only_paused_jobs -v`
Expected: FAIL with `AttributeError: module 'agentic_ai.graph' has no attribute 'list_pending_review_ids'`

- [ ] **Step 3: Write the implementation**

Add after `get_paused_state` (line 391) in `src/agentic_ai/graph.py`:

```python
async def _list_pending_review_ids() -> list[str]:
    import sqlite3

    if not settings.checkpoint_db_path.exists():
        return []
    with sqlite3.connect(settings.checkpoint_db_path) as conn:
        thread_ids = [row[0] for row in conn.execute("SELECT DISTINCT thread_id FROM checkpoints")]

    pending: list[str] = []
    async with _open_checkpointer() as saver:
        probe = build_graph(skip_render=False, checkpointer=saver, review_pause=True)
        for tid in thread_ids:
            config = {"configurable": {"thread_id": tid}}
            snapshot = await probe.aget_state(config)
            if snapshot.values and "human_review" in snapshot.next:
                pending.append(tid)
    return pending


def list_pending_review_ids() -> list[str]:
    """Job ids currently paused at `human_review`. No `jobs` row exists for a paused
    job (`persist_run` only runs once `review()`/`resume()` finishes one) so this scans
    `checkpoints.db`'s thread ids directly instead of `jobpilot.db`."""
    return asyncio.run(_list_pending_review_ids())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_graph_phase3.py::test_list_pending_review_ids_returns_only_paused_jobs -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentic_ai/graph.py tests/test_graph_phase3.py
git commit -m "feat: add list_pending_review_ids for MCP's list_pending_reviews tool

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: Non-interactive `--action`/`--edits-file` on `jobpilot review`

**Files:**
- Modify: `src/agentic_ai/cli.py:302-355` (the `review()` command)
- Test: `tests/test_review.py` (existing file — add new test functions)

**Interfaces:**
- Consumes: `graph.get_paused_state`, `graph.resume_review`, `db.repo.persist_run`,
  `preferences.diff_edited_bullets`, `preferences.record_preferences`,
  `state.Draft` — all already used by the existing interactive path in this same
  function.
- Produces: `jobpilot review <job_id> --action approve|edit|reject [--edits-file PATH]`
  — a single non-blocking action with no `typer.prompt`. Used by Task 3's `review_job`
  MCP tool via `subprocess.run(["jobpilot", "review", job_id, "--action", ..., ...])`.
  Exit code: `0` on success/still-pending, `1` if the resulting state has a
  `skip_reason`. stdout on error from a bad `--action` value or malformed
  `--edits-file`: a `typer.BadParameter` message (typer already writes these to
  stderr and exits 2 — no new error handling needed here).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_review.py` (check the existing file's imports/fixtures first and
match them — it already builds a paused job via `graph.run_for_review` for the
interactive tests, reuse that same setup helper rather than duplicating it):

```python
def test_review_action_approve_is_noninteractive(tmp_path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from agentic_ai import graph
    from agentic_ai.cli import app
    from agentic_ai.config import settings

    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")
    monkeypatch.setattr(settings, "jobs_db_path", tmp_path / "jobpilot.db")
    monkeypatch.setattr("agentic_ai.render.OUT_DIR", tmp_path / "out")

    paused = graph.run_for_review(
        "Werkstudent Data Engineer. Must know Python and SQL.", title="DE", company="Acme"
    )

    runner = CliRunner()
    result = runner.invoke(app, ["review", paused.job.id, "--action", "approve"])

    assert result.exit_code == 0, result.output
    assert "typer.prompt" not in result.output  # sanity: no interactive prompt fired


def test_review_action_edit_requires_edits_file(tmp_path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from agentic_ai import graph
    from agentic_ai.cli import app
    from agentic_ai.config import settings

    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")
    monkeypatch.setattr(settings, "jobs_db_path", tmp_path / "jobpilot.db")

    paused = graph.run_for_review(
        "Werkstudent Data Engineer. Must know Python and SQL.", title="DE", company="Acme"
    )

    runner = CliRunner()
    result = runner.invoke(app, ["review", paused.job.id, "--action", "edit"])

    assert result.exit_code != 0
    assert "--edits-file" in result.output


def test_review_action_edit_applies_edited_draft(tmp_path, monkeypatch) -> None:
    import json

    from typer.testing import CliRunner

    from agentic_ai import graph
    from agentic_ai.cli import app
    from agentic_ai.config import settings

    monkeypatch.setattr(settings, "checkpoint_db_path", tmp_path / "checkpoints.db")
    monkeypatch.setattr(settings, "jobs_db_path", tmp_path / "jobpilot.db")
    monkeypatch.setattr("agentic_ai.render.OUT_DIR", tmp_path / "out")

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_review.py -k "action" -v`
Expected: FAIL — `--action` is not a recognized option (typer `UsageError` /
`No such option`).

- [ ] **Step 3: Write the implementation**

Replace the `review()` function body in `src/agentic_ai/cli.py` (lines 302-355):

```python
@app.command()
def review(
    job_id: str,
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    action: str | None = typer.Option(
        None, "--action", help="approve|edit|reject — skip the interactive prompt (for MCP/non-interactive use)."
    ),
    edits_file: Path | None = typer.Option(
        None, "--edits-file", help="JSON file holding an edited Draft. Required with --action edit."
    ),
) -> None:
    """Approve, edit, or reject a job paused by 'jobpilot add --review' (§9). An edit
    re-runs recruiter_sim/hiring_manager against the new draft and pauses again with a
    fresh verdict — approve/reject never re-run those, so their verdict cannot go stale."""
    from .db.repo import persist_run
    from .graph import get_paused_state, resume_review
    from .preferences import diff_edited_bullets, record_preferences

    try:
        paused = get_paused_state(job_id)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from None

    if paused.skip_reason:
        console.print(f"job {job_id} already ended: {paused.skip_reason}")
        raise typer.Exit(1)

    _report(paused, verbose=verbose)

    if action is not None:
        if action not in ("approve", "edit", "reject"):
            raise typer.BadParameter("--action must be approve, edit, or reject")
        if action == "edit":
            if edits_file is None:
                raise typer.BadParameter("--edits-file is required with --action edit")
            import json

            from .state import Draft

            edited = Draft.model_validate(json.loads(edits_file.read_text()))
            _record_judgements(job_id, paused, "edit")
            if paused.draft is not None:
                changed = diff_edited_bullets(paused.draft, edited)
                if changed:
                    record_preferences(changed)
            state = resume_review(job_id, action="edit", draft=edited)
        else:
            _record_judgements(job_id, paused, action)
            state = resume_review(job_id, action=action)

        if state.skip_reason is None and not state.artifacts:
            console.print(f"\njob {job_id} re-paused for review (new verdict after edit)")
            _report(state, verbose=verbose)
            raise typer.Exit(0)

        persist_run(state)
        _log_run(state)
        if state.artifacts.get("cv_pdf"):
            console.print("\n[bold]RESULT[/bold]")
            _report(state, verbose=verbose)
        else:
            console.print(f"\n[yellow]{state.skip_reason}[/yellow]")
        raise typer.Exit(1 if state.skip_reason else 0)

    while True:
        choice = typer.prompt("\napprove (a) / edit (e) / reject (r)?").strip().lower()
        if choice in ("a", "approve"):
            _record_judgements(job_id, paused, "approve")
            state = resume_review(job_id, action="approve")
            break
        if choice in ("r", "reject"):
            _record_judgements(job_id, paused, "reject")
            state = resume_review(job_id, action="reject")
            break
        if choice in ("e", "edit"):
            edited = _edit_draft(paused.draft)
            if edited is None:
                continue
            _record_judgements(job_id, paused, "edit")
            if paused.draft is not None:
                changed = diff_edited_bullets(paused.draft, edited)
                if changed:
                    record_preferences(changed)
            paused = resume_review(job_id, action="edit", draft=edited)
            if paused.skip_reason:  # e.g. recruiter hard-failed the edited draft
                state = paused
                break
            _report(paused, verbose=verbose)
            continue
        console.print("[yellow]enter a, e, or r[/yellow]")

    persist_run(state)
    _log_run(state)
    if state.artifacts.get("cv_pdf"):
        console.print("\n[bold]RESULT[/bold]")
        _report(state, verbose=verbose)
    else:
        console.print(f"\n[yellow]{state.skip_reason}[/yellow]")
    raise typer.Exit(1 if state.skip_reason else 0)
```

Update the module docstring line at `src/agentic_ai/cli.py:6` to mention the new flag:

```
    jobpilot review <job_id>          approve/edit/reject a job paused by --review
    jobpilot review <job_id> --action approve|edit|reject [--edits-file f.json]
                                       same, non-interactive (used by the MCP server)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_review.py -v`
Expected: PASS (both new tests, and all pre-existing interactive-path tests still pass
unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/agentic_ai/cli.py tests/test_review.py
git commit -m "feat: non-interactive --action/--edits-file for jobpilot review

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: MCP server with the 6 tools

**Files:**
- Create: `src/agentic_ai/mcp/__init__.py`
- Create: `src/agentic_ai/mcp/server.py`
- Test: `tests/test_mcp_server.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `graph.list_pending_review_ids` (Task 1), `graph.get_paused_state`,
  `db.repo.get_job`, `profile.Profile.load`, `render.OUT_DIR`, `render.job_dir_name`
  (all pre-existing), `subprocess.run` (stdlib), the `review --action` flags (Task 2).
- Produces: a `FastMCP` instance named `mcp` in `server.py` with 6 registered tools,
  and `main()` — the console-script entry point that calls `mcp.run()`. Nothing else
  depends on this module; it is the top of the dependency graph for this feature.

- [ ] **Step 1: Add the `mcp` dependency and console script**

Edit `pyproject.toml`: add `"mcp>=1.2",` to the `dependencies` list (after
`"python-dotenv>=1.2.3",`), and add to `[project.scripts]`:

```toml
[project.scripts]
jobpilot = "agentic_ai.cli:app"
jobpilot-mcp = "agentic_ai.mcp.server:main"
```

Run: `uv sync --extra dev`
Expected: resolves and installs `mcp` with no conflicts.

- [ ] **Step 2: Create the package file**

`src/agentic_ai/mcp/__init__.py`:

```python
```

(empty — just makes `agentic_ai.mcp` a package)

- [ ] **Step 3: Write the failing tests**

`tests/test_mcp_server.py`:

```python
"""jobpilot-mcp server tools (docs/superpowers/specs/2026-10-05-mcp-desktop-chrome-apply-design.md).

State-changing tools shell out to the `jobpilot` CLI via subprocess — mocked here, same
as render.py's own subprocess calls are mocked elsewhere in this suite. Read-only tools
call graph.py/db/repo.py/profile.py directly and are tested against tmp fixture files,
same conventions as test_cli_applications.py.
"""

from __future__ import annotations

import json
import subprocess

import pytest

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

    def fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="paused for review  job id: abc123\n", stderr="")

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    result = server.add_job(jd_text="some JD text")

    assert captured["cmd"][:2] == ["jobpilot", "add"]
    assert "--stdin" in captured["cmd"]
    assert result["status"] == "ok"


def test_add_job_cli_failure_returns_error(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text, timeout):
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
        captured["edits_contents"] = json.loads(open(edits_path).read())
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
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/test_mcp_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentic_ai.mcp'`

- [ ] **Step 5: Write the implementation**

`src/agentic_ai/mcp/server.py`:

```python
"""Local stdio MCP server — lets Claude Desktop drive the jobpilot pipeline and hand
tailored output to Claude-in-Chrome for form-filling (docs/superpowers/specs/
2026-10-05-mcp-desktop-chrome-apply-design.md).

State-changing tools shell out to `jobpilot` (the one orchestration path — CLAUDE.md's
reasoning for Cowork applies here too). Read-only tools call graph.py/db/repo.py/
profile.py directly: they report state, they don't run pipeline logic, so there's
nothing to keep in sync with a second code path.

No tool submits a form or posts anything. That boundary is structural (CLAUDE.md:
"It never applies") — there is no tool here that could be wired to a submit button.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .. import graph
from ..config import settings
from ..profile import Profile

mcp = FastMCP("jobpilot")

_CLI_TIMEOUT_S = 300  # a full pipeline run (extract/diagnose/rewrite/render) can take minutes


def _run_cli(args: list[str]) -> dict:
    """Shell out to the `jobpilot` console script. Never raises — CLI failures,
    timeouts, and missing-binary errors all come back as {"error": ...}."""
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=_CLI_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"error": f"'{' '.join(args)}' timed out after {_CLI_TIMEOUT_S}s"}
    except FileNotFoundError:
        return {"error": "'jobpilot' not found on PATH — is the package installed in this environment?"}
    if result.returncode != 0:
        stderr_tail = "\n".join(result.stderr.strip().splitlines()[-5:])
        return {"error": stderr_tail or f"jobpilot exited {result.returncode}"}
    return {"status": "ok", "output": result.stdout.strip()}


@mcp.tool()
def list_pending_reviews() -> list[dict]:
    """Jobs currently paused at the human_review checkpoint, newest first isn't
    guaranteed — just every job awaiting a decision."""
    summaries = []
    for job_id in graph.list_pending_review_ids():
        state = graph.get_paused_state(job_id)
        summaries.append(
            {
                "job_id": state.job.id,
                "company": state.job.company,
                "title": state.job.title,
                "score": state.scores.review_score,
            }
        )
    return summaries


@mcp.tool()
def get_job(job_id: str) -> dict:
    """JD text, diagnosis, draft (profile line/bullets/cover letter), score, and
    rendered CV/cover-letter PDF paths (if the job has been approved and rendered)."""
    try:
        state = graph.get_paused_state(job_id)
    except KeyError as exc:
        return {"error": str(exc)}

    return {
        "job_id": state.job.id,
        "company": state.job.company,
        "title": state.job.title,
        "url": state.job.url,
        "jd_text": state.job.jd_text,
        "diagnosis": state.diagnosis.model_dump(mode="json") if state.diagnosis else None,
        "draft": state.draft.model_dump(mode="json") if state.draft else None,
        "review_score": state.scores.review_score,
        "skip_reason": state.skip_reason,
        "cv_pdf": state.artifacts.get("cv_pdf"),
        "cover_letter_pdf": state.artifacts.get("cover_letter_pdf"),
    }


@mcp.tool()
def get_applicant_profile() -> dict:
    """Contact fields Claude-in-Chrome needs to fill a form: name, email, phone,
    location, github, linkedin."""
    identity = Profile.load().raw["identity"]
    return {
        "name": identity["name"],
        "email": identity["email"],
        "phone": identity["phone"],
        "location": identity["location"],
        "github": identity["github"],
        "linkedin": identity["linkedin"],
    }


@mcp.tool()
def add_job(jd_text: str | None = None, url: str | None = None) -> dict:
    """Run a JD through the pipeline to the human_review checkpoint. Give either
    jd_text (pasted/read JD) or url (a posting URL) — not both."""
    if bool(jd_text) == bool(url):
        return {"error": "give exactly one of jd_text or url"}
    args = ["jobpilot", "add", "--review"]
    if url:
        args += ["--url", url]
        return _run_cli(args)
    args += ["--stdin"]
    try:
        result = subprocess.run(
            args, input=jd_text, capture_output=True, text=True, timeout=_CLI_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        return {"error": f"'{' '.join(args)}' timed out after {_CLI_TIMEOUT_S}s"}
    if result.returncode != 0:
        stderr_tail = "\n".join(result.stderr.strip().splitlines()[-5:])
        return {"error": stderr_tail or f"jobpilot exited {result.returncode}"}
    return {"status": "ok", "output": result.stdout.strip()}


@mcp.tool()
def review_job(job_id: str, action: str, edits: dict | None = None) -> dict:
    """approve / edit / reject a job paused at human_review. `edits` (a Draft, same
    shape as get_job's "draft" field) is required when action == "edit"."""
    if action not in ("approve", "edit", "reject"):
        return {"error": "action must be approve, edit, or reject"}
    if action == "edit":
        if edits is None:
            return {"error": "edits is required when action == 'edit'"}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(edits, f)
            edits_path = f.name
        try:
            return _run_cli(
                ["jobpilot", "review", job_id, "--action", "edit", "--edits-file", edits_path]
            )
        finally:
            Path(edits_path).unlink(missing_ok=True)
    return _run_cli(["jobpilot", "review", job_id, "--action", action])


@mcp.tool()
def mark_applied(job_id: str) -> dict:
    """Record that job_id's tailored CV was sent, after the user clicks Submit
    themselves in Chrome."""
    return _run_cli(["jobpilot", "applied", job_id])


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
```

Note on `add_job`: `subprocess.run` is called twice in the function body above only in
the `jd_text` branch (needs `input=`) vs the `url` branch (needs no stdin) — this
duplicates `_run_cli`'s error handling. Keep it inline as written; collapsing it into
`_run_cli(args, stdin=...)` is a reasonable Task-4 cleanup but isn't required for
correctness and the plan's tests pass against the version above.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_mcp_server.py -v`
Expected: PASS

- [ ] **Step 7: Run the full test suite to confirm no regressions**

Run: `pytest -q && ruff check . && mypy src`
Expected: all green. (`mypy --strict` will likely flag the untyped `mcp.server.fastmcp`
import if the `mcp` package ships no `py.typed`/stubs — if so, add
`[[tool.mypy.overrides]] module = "mcp.*" ignore_missing_imports = true` to
`pyproject.toml` rather than loosening `--strict` project-wide.)

- [ ] **Step 8: Commit**

```bash
git add src/agentic_ai/mcp/ tests/test_mcp_server.py pyproject.toml uv.lock
git commit -m "feat: add jobpilot-mcp stdio server for Claude Desktop + Chrome-assisted apply

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: Manual end-to-end smoke test + docs

**Files:**
- Modify: whichever file documents CLI usage for end users (check `README.md` first;
  `CLAUDE.md`'s `## Commands` section is session context only per its own header, so
  user-facing setup docs belong in `README.md`).

**Interfaces:**
- Consumes: `jobpilot-mcp` console script (Task 3).
- Produces: a documented Claude Desktop config stanza; no new code.

- [ ] **Step 1: Verify the server starts stand-alone**

Run: `uv run --directory . jobpilot-mcp` (then Ctrl-C) — confirms the console script
resolves and `FastMCP.run()` starts the stdio loop without raising on import.
Expected: process starts and blocks on stdin (no traceback). Ctrl-C to stop.

- [ ] **Step 2: Add the Claude Desktop config snippet**

Add to `README.md`:

```markdown
## Claude Desktop (MCP)

Add to Claude Desktop's MCP config (`claude_desktop_config.json`):

\`\`\`json
{
  "mcpServers": {
    "jobpilot": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/Agentic_ai", "jobpilot-mcp"]
    }
  }
}
\`\`\`

Restart Claude Desktop. Ask it to list pending reviews, get a job's draft, or approve
one — it calls the MCP tools above. To fill an application form, open the job posting
in Chrome (Claude-in-Chrome extension) and ask Claude to fill it using the job's data —
it never submits; that click stays yours.
```

- [ ] **Step 3: Manual smoke test**

With a real (or test) `master_profile.yaml` and `.env` configured:
1. `uv run jobpilot add --file <some_jd.txt> --review` — get a `job_id`.
2. `uv run jobpilot-mcp` in one terminal; point Claude Desktop at it per the config
   above; restart Claude Desktop.
3. In a Claude Desktop chat: ask it to list pending reviews — confirm the job from
   step 1 appears with company/title/score.
4. Ask it to show that job's draft — confirm JD text, diagnosis, draft bullets come
   back.
5. Ask it to approve the job — confirm it reports a rendered CV/cover-letter path, and
   `jobpilot review <job_id>` on the CLI now shows "already ended" (state moved past
   the pause).

Expected: all 4 steps succeed with no raw tracebacks surfaced in the Claude Desktop
chat at any point (every failure mode — missing job, timeout, CLI non-zero exit —
should read as a plain `{"error": "..."}` message instead).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: add Claude Desktop MCP config instructions

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Review section (fill in after execution)

- [ ] `pytest -q && ruff check . && mypy src` clean
- [ ] `jobpilot profile check` exits 0 (unaffected by this change, but part of definition
      of done per `CLAUDE.md`)
- [ ] `graphify update .` run so the graph picks up `src/agentic_ai/mcp/`
- [ ] Manual smoke test (Task 4, Step 3) completed and confirmed
