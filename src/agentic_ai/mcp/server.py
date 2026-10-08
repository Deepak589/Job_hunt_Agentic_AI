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

import asyncio
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .. import graph
from ..profile import Profile

mcp = FastMCP("jobpilot")

_CLI_TIMEOUT_S = 300  # a full pipeline run (extract/diagnose/rewrite/render) can take minutes


async def _run_cli(args: list[str], input: str | None = None) -> dict[str, Any]:
    """Shell out to the `jobpilot` console script. Never raises — CLI failures,
    timeouts, and missing-binary errors all come back as {"error": ...}.

    Runs the blocking `subprocess.run` off the event loop thread (`asyncio.to_thread`)
    — this can take up to `_CLI_TIMEOUT_S`, and the stdio MCP server has to keep
    answering pings/cancellations on the loop while it waits."""
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            args,
            input=input,
            capture_output=True,
            text=True,
            timeout=_CLI_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"'{' '.join(args)}' timed out after {_CLI_TIMEOUT_S}s"}
    except FileNotFoundError:
        return {"error": "'jobpilot' not found on PATH — is the package installed in this environment?"}
    if result.returncode != 0:
        stderr_tail = "\n".join(result.stderr.strip().splitlines()[-5:])
        # rich.Console (jobpilot's CLI) writes the human-readable reason (reject,
        # hard-gap skip, budget cap) to stdout, not stderr — never discard it in
        # favor of a generic "exited N".
        stdout_tail = "\n".join(result.stdout.strip().splitlines()[-5:])
        # stdout wins: rich.Console prints jobpilot's own reason there on every
        # exit path (reject/skip/budget-cap); stderr tends to carry incidental
        # library noise (model loading, HF Hub warnings), not the real reason.
        return {"error": stdout_tail or stderr_tail or f"jobpilot exited {result.returncode}"}
    return {"status": "ok", "output": result.stdout.strip()}


@mcp.tool()
async def list_pending_reviews() -> list[dict[str, Any]]:
    """Jobs currently paused at the human_review checkpoint, newest first isn't
    guaranteed — just every job awaiting a decision."""
    try:
        summaries = []
        ids = await asyncio.to_thread(graph.list_pending_review_ids)
        for job_id in ids:
            state = await asyncio.to_thread(graph.get_paused_state, job_id)
            summaries.append(
                {
                    "job_id": state.job.id,
                    "company": state.job.company,
                    "title": state.job.title,
                    "score": state.scores.review_score,
                }
            )
        return summaries
    except KeyError as exc:
        return [{"error": str(exc)}]


@mcp.tool()
async def get_job(job_id: str) -> dict[str, Any]:
    """JD text, diagnosis, draft (profile line/bullets/cover letter), score, and
    rendered CV/cover-letter PDF paths (if the job has been approved and rendered)."""
    try:
        state = await asyncio.to_thread(graph.get_paused_state, job_id)
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
def get_applicant_profile() -> dict[str, Any]:
    """Contact fields Claude-in-Chrome needs to fill a form: name, email, phone,
    location, github, linkedin."""
    try:
        identity = Profile.load().raw["identity"]
        return {
            "name": identity["name"],
            "email": identity["email"],
            "phone": identity["phone"],
            "location": identity["location"],
            "github": identity["github"],
            "linkedin": identity["linkedin"],
        }
    except (KeyError, OSError) as exc:
        return {"error": str(exc)}


@mcp.tool()
async def add_job(jd_text: str | None = None, url: str | None = None) -> dict[str, Any]:
    """Run a JD through the pipeline to the human_review checkpoint. Give either
    jd_text (pasted/read JD) or url (a posting URL) — not both."""
    if bool(jd_text) == bool(url):
        return {"error": "give exactly one of jd_text or url"}
    args = ["jobpilot", "add", "--review"]
    if url:
        args += ["--url", url]
        return await _run_cli(args)
    args += ["--stdin"]
    return await _run_cli(args, input=jd_text)


@mcp.tool()
async def review_job(job_id: str, action: str, edits: dict[str, Any] | None = None) -> dict[str, Any]:
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
            return await _run_cli(
                ["jobpilot", "review", job_id, "--action", "edit", "--edits-file", edits_path]
            )
        finally:
            Path(edits_path).unlink(missing_ok=True)
    return await _run_cli(["jobpilot", "review", job_id, "--action", action])


@mcp.tool()
async def mark_applied(job_id: str) -> dict[str, Any]:
    """Record that job_id's tailored CV was sent, after the user clicks Submit
    themselves in Chrome."""
    return await _run_cli(["jobpilot", "applied", job_id])


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
