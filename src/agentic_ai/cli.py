"""jobpilot CLI.

    jobpilot add --file jd.txt        run a JD through the full 5-role pipeline
    jobpilot add --file jd.txt --review   pause before render for human approval (§9)
    jobpilot add --dir jds/           run every .txt JD in a directory concurrently
    jobpilot review <job_id>          approve/edit/reject a job paused by --review
    jobpilot cost [--days N]          spend summary from the runs ledger
    jobpilot index build [--force]    (re)build the evidence store
    jobpilot index calibrate          measure SEM_THRESHOLD against the labeled probes
    jobpilot profile check            validate master_profile.yaml
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import typer
from rich.console import Console
from rich.table import Table
from ruamel.yaml import YAML

from .config import settings

app = typer.Typer(add_completion=False, help="Job-hunt pipeline — Phase 1.")
index_app = typer.Typer(help="Evidence store.")
profile_app = typer.Typer(help="Master profile.")
source_app = typer.Typer(help="Job-board ingestion.")
app.add_typer(index_app, name="index")
app.add_typer(profile_app, name="profile")
app.add_typer(source_app, name="source")

console = Console()


def _log_event(event: dict) -> None:
    """Append one JSON line to settings.log_path. Decoupled from Rich console output —
    never affects what's printed to the terminal."""
    settings.log_path.parent.mkdir(parents=True, exist_ok=True)
    line = {"timestamp": datetime.now(timezone.utc).isoformat(), **event}
    with settings.log_path.open("a") as f:
        f.write(json.dumps(line) + "\n")


def _log_run(state) -> None:
    per_node: dict[str, float] = {}
    for c in state.llm_calls:
        per_node[c["node"]] = round(per_node.get(c["node"], 0.0) + c["cost_usd"], 6)
    _log_event({
        "event": "run",
        "job_id": state.job.id,
        "cost_by_node": per_node,
        "total_cost_usd": state.total_cost_usd,
        "verdict": state.hiring_manager.verdict if state.hiring_manager else (state.ats.verdict if state.ats else None),
        "skip_reason": state.skip_reason,
    })


def _check_budget() -> None:
    """Raise typer.Exit(1) before any graph run if today's spend already hit the cap.

    The actual cap check lives in budget.py (shared with graph.run_many's
    BudgetGuard, solution.md step 1) — this wraps it in the CLI's console+log+exit
    convention.
    """
    from .budget import BudgetExceeded, check_budget

    try:
        check_budget()
    except BudgetExceeded as e:
        _log_event({
            "event": "budget_guard_rejected",
            "spent_today_usd": e.spent,
            "max_daily_cost_usd": settings.max_daily_cost_usd,
        })
        console.print(f"[bold red]budget cap hit[/bold red] — {e}. Not running.")
        raise typer.Exit(1) from e


# --------------------------------------------------------------------------- add


def _jobposting_from_jsonld(blocks: list[dict]) -> dict | None:
    """Find a schema.org JobPosting among extruct's json-ld blocks, unwrapping @graph
    where present. Returns None if none found."""

    def _flatten(items: list) -> list[dict]:
        out = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if "@graph" in item:
                out.extend(_flatten(item["@graph"]))
            else:
                out.append(item)
        return out

    for item in _flatten(blocks):
        types = item.get("@type", "")
        types = types if isinstance(types, list) else [types]
        if any("JobPosting" in str(t) for t in types):
            return item
    return None


def _fetch_job_posting(url: str) -> dict:
    """Fetch `url` and pull JD fields — JSON-LD JobPosting first (structured, reliable),
    trafilatura's readability extraction as a fallback (title/company left blank; the
    user can pass --title/--company alongside --url same as with --file)."""
    import extruct
    import httpx
    import trafilatura

    from .sourcing.base import strip_html

    resp = httpx.get(url, timeout=15, follow_redirects=True)
    resp.raise_for_status()
    html = resp.text

    try:
        data = extruct.extract(html, base_url=url, syntaxes=["json-ld"])
        posting = _jobposting_from_jsonld(data.get("json-ld", []))
    except Exception:
        posting = None

    if posting:
        org = posting.get("hiringOrganization") or {}
        loc = posting.get("jobLocation") or {}
        if isinstance(loc, list):
            loc = loc[0] if loc else {}
        address = loc.get("address") if isinstance(loc, dict) else None
        location = ""
        if isinstance(address, dict):
            location = address.get("addressLocality", "") or ""
        elif isinstance(loc, dict):
            location = loc.get("name", "") or ""
        return {
            "title": posting.get("title", "") or "",
            "company": org.get("name", "") if isinstance(org, dict) else "",
            "location": location,
            "jd_text": strip_html(posting.get("description", "") or ""),
        }

    jd_text = trafilatura.extract(html) or ""
    return {"title": "", "company": "", "location": "", "jd_text": jd_text}


@app.command()
def add(
    file: Path | None = typer.Option(None, "--file", "-f", help="Path to a saved JD."),
    stdin: bool = typer.Option(False, "--stdin", help="Read the JD from stdin."),
    dir: Path | None = typer.Option(
        None, "--dir", help="Directory of .txt JDs — run all of them concurrently (see Settings.max_concurrent_jobs)."
    ),
    url: str = typer.Option("", "--url", help="Fetch the JD from a job posting URL (JSON-LD JobPosting, falling back to trafilatura)."),
    title: str = typer.Option("", "--title", help="Job title, if known."),
    company: str = typer.Option("", "--company", help="Company, if known."),
    location: str = typer.Option(
        "", "--location", help="Where the job is. Decides every on-site requirement."
    ),
    full_time: bool = typer.Option(
        False, "--full-time", help="Full-time search: lift the werkstudent weekly-hours cap."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print prompts and raw responses."),
    no_render: bool = typer.Option(False, "--no-render", help="Stop after review; skip PDF render + ATS score."),
    review: bool = typer.Option(
        False, "--review", help="Pause before rendering for human approval — run 'jobpilot review <id>' to continue (§9)."
    ),
) -> None:
    """Run one job description through the gap report."""
    if url and (file or stdin or dir):
        raise typer.BadParameter("--url cannot be combined with --file/--stdin/--dir")

    if dir is not None:
        if file or stdin or review:
            raise typer.BadParameter("--dir cannot be combined with --file/--stdin/--review")
        jd_paths = sorted(dir.glob("*.txt"))
        if not jd_paths:
            raise typer.BadParameter(f"no .txt files found in {dir}")
        jd_texts = [p.read_text() for p in jd_paths]

        if verbose:
            from . import nodes  # noqa: F401
            console.print(f"[dim]model: {settings.extract_model}  threshold: {settings.sem_threshold}[/dim]")

        _check_budget()

        from .db.repo import persist_run
        from .graph import run_many

        job_fields = dict(
            title=title, company=company, location=location,
            employment_type="fulltime" if full_time else "werkstudent",
        )
        # A skip (hard-gap gate, disqualifier) is an expected verdict, not a failure —
        # asyncio.gather has no return_exceptions=True, so a job that actually errors
        # propagates as an uncaught exception here and exits non-zero on its own.
        states = asyncio.run(run_many(jd_texts, _skip_render=no_render, **job_fields))
        for state in states:
            persist_run(state)
            _log_run(state)
            _report(state, verbose=verbose)
        raise typer.Exit(0)

    if file:
        jd_text = file.read_text()
    elif stdin:
        jd_text = sys.stdin.read()
    elif url:
        extracted = _fetch_job_posting(url)
        jd_text = extracted["jd_text"]
        title = title or extracted["title"]
        company = company or extracted["company"]
        location = location or extracted["location"]
    else:
        raise typer.BadParameter("give --file, --stdin, or --url")
    if not jd_text.strip():
        raise typer.BadParameter("job description is empty")
    if review and no_render:
        raise typer.BadParameter("--review and --no-render are mutually exclusive")

    if verbose:
        from . import nodes  # noqa: F401
        console.print(f"[dim]model: {settings.extract_model}  threshold: {settings.sem_threshold}[/dim]")

    _check_budget()

    from .db.repo import persist_run

    job_fields = dict(
        title=title, company=company, location=location,
        employment_type="fulltime" if full_time else "werkstudent",
    )

    if review:
        from .graph import run_for_review

        state = run_for_review(jd_text, **job_fields)
        persist_run(state)
        _log_run(state)
        if state.skip_reason or state.draft is None:
            _report(state, verbose=verbose)
        else:
            console.print(f"\n[bold]paused for review[/bold]  job id: [dim]{state.job.id}[/dim]")
            console.print(f"run 'jobpilot review {state.job.id}' to see the draft and approve or reject it")
        raise typer.Exit(1 if state.skip_reason else 0)

    from .graph import run

    state = run(jd_text, _skip_render=no_render, **job_fields)
    persist_run(state)
    _log_run(state)
    _report(state, verbose=verbose)
    raise typer.Exit(1 if state.skip_reason else 0)


def _edit_draft(draft):
    """Open `draft` as YAML in $EDITOR; re-parse into a Draft. Returns None if the user
    gives up on a parse error (draft left unchanged)."""
    from .state import Draft

    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    fd, path_str = tempfile.mkstemp(suffix=".yaml")
    os.close(fd)
    path = Path(path_str)
    try:
        yaml.dump(draft.model_dump(mode="json"), path)
        editor = os.environ.get("EDITOR", "vi")
        while True:
            subprocess.run([editor, str(path)])
            try:
                return Draft.model_validate(yaml.load(path))
            except Exception as exc:
                console.print(f"[red]invalid draft:[/red] {exc}")
                if not typer.confirm("retry edit?"):
                    return None
    finally:
        path.unlink(missing_ok=True)


def _record_judgements(job_id: str, state, human_action: str) -> None:
    """One row per judge node that had a verdict at this pause (solution.md step 8) —
    all sharing the same human_action, the raw material judge_stats' kappa needs."""
    from .db import repo

    if state.recruiter is not None:
        repo.record_judgement(job_id, "recruiter_sim", state.recruiter.result, human_action)
    if state.hiring_manager is not None:
        repo.record_judgement(job_id, "hiring_manager", state.hiring_manager.verdict, human_action)
    if state.scores.review_score is not None:
        repo.record_judgement(job_id, "review", str(state.scores.review_score), human_action)


@app.command()
def review(job_id: str, verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
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


@app.command()
def resume(job_id: str) -> None:
    """Continue a job whose process crashed mid-run (§2 durability) — picks up from the
    last checkpointed node instead of re-paying for extract/diagnose/rewrite. Only for
    the plain 'add' path; a job paused at review continues via 'jobpilot review'."""
    from .db.repo import persist_run
    from .graph import resume as resume_job

    try:
        state = resume_job(job_id)
    except (KeyError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from None

    persist_run(state)
    _log_run(state)
    _report(state, verbose=False)
    raise typer.Exit(1 if state.skip_reason else 0)


@app.command()
def applied(job_id: str) -> None:
    """Record that job_id's tailored CV was sent. Reads the rendered draft's hash from
    out/<id>/draft.sha (render.py's idempotent-render marker, solution.md step 2)."""
    from .db.repo import record_application
    from .render import OUT_DIR

    sha_path = OUT_DIR / job_id / "draft.sha"
    if sha_path.exists():
        cv_pdf_sha = sha_path.read_text().strip()
    else:
        cv_pdf_sha = ""
        console.print(f"[yellow]warning:[/yellow] no rendered PDF found for job {job_id} (no {sha_path})")

    record_application(job_id, cv_pdf_sha)
    console.print(f"recorded application for {job_id}")


@app.command()
def outcome(
    job_id: str,
    outcome: Literal["interview", "reject", "ghost"] = typer.Argument(..., help="interview | reject | ghost"),
) -> None:
    """Record what happened after applying. Run 'jobpilot applied <id>' first."""
    from .db.repo import record_outcome

    try:
        record_outcome(job_id, outcome)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from None
    console.print(f"recorded outcome for {job_id}: {outcome}")


@app.command("judge-stats")
def judge_stats_cmd() -> None:
    """Cohen's kappa per judge node vs what the human actually did at review."""
    from .db.repo import judgement_pairs
    from .judgestats import judge_stats

    stats = judge_stats()
    table = Table(show_lines=False)
    table.add_column("Node")
    table.add_column("kappa", justify="right")
    table.add_column("N", justify="right")
    for node, kappa in stats.items():
        n = len(judgement_pairs(node))
        table.add_row(node, f"{kappa:.3f}" if kappa is not None else "not enough data", str(n))
    console.print(table)


@app.command()
def cost(days: int | None = typer.Option(None, "--days", help="Only include the last N days.")) -> None:
    """Spend summary from the runs ledger — per day and all-time."""
    from .db.repo import cost_summary

    since = datetime.now(timezone.utc) - timedelta(days=days) if days is not None else None
    summary = cost_summary(since=since)

    table = Table(show_lines=False)
    table.add_column("Date")
    table.add_column("Runs", justify="right")
    table.add_column("Tokens in", justify="right")
    table.add_column("Tokens out", justify="right")
    table.add_column("Cost", justify="right")
    for row in summary["by_day"]:
        table.add_row(row["date"], str(row["runs"]), str(row["tokens_in"]), str(row["tokens_out"]), f"${row['cost_usd']:.4f}")
    console.print(table)
    console.print(
        f"\n[bold]total[/bold]  {summary['total_runs']} run(s)  "
        f"{summary['total_tokens_in']} in / {summary['total_tokens_out']} out  "
        f"${summary['total_cost_usd']:.4f}"
    )


def _report(state, verbose: bool = False) -> None:
    job = state.job
    header = f"{job.title or '(untitled)'}"
    if job.company:
        header += f" @ {job.company}"
    console.print(f"\n[bold]{header}[/bold]  [dim]{job.id}[/dim]")

    for label, kind in (("HARD REQUIREMENTS", "hard"), ("DISQUALIFIERS", "disqualifier"), ("NICE TO HAVE", "soft")):
        rows = [r for r in state.requirements if r.type == kind]
        if not rows:
            continue
        table = Table(title=label, title_justify="left", title_style="bold", show_lines=False)
        table.add_column("", width=1)
        table.add_column("Requirement", overflow="fold", max_width=52)
        table.add_column("Signal", width=8)
        table.add_column("Evidence", width=26, overflow="fold")
        table.add_column("Sim", justify="right", width=6)
        for r in rows:
            mark = "[green]✓[/green]" if r.covered else ("[yellow]?[/yellow]" if r.unknown else "[red]✗[/red]")
            top = r.evidence[0] if r.evidence else None
            if r.covered_by == "keyword":
                # the matched profile term IS the evidence; the nearest bullet is not
                evidence, sim = f"[cyan]{r.matched_term}[/cyan]", "—"
            elif top:
                flag = f" [yellow]({top.status})[/yellow]" if top.status else ""
                evidence, sim = top.source_id + flag, f"{top.similarity:.3f}"
            else:
                evidence, sim = "—", "—"
            table.add_row(
                mark,
                r.text,
                r.covered_by or "—",
                evidence,
                sim,
            )
        console.print(table)

    s = state.scores
    console.print(
        f"hard coverage [bold]{s.hard_coverage:.0%}[/bold]  "
        f"soft coverage {s.soft_coverage:.0%}  "
        f"mean similarity {s.semantic_fit:.3f}"
    )

    for q in state.open_questions():
        console.print(f"[yellow]confirm before applying:[/yellow] {q.text} [dim](not in the profile)[/dim]")

    if state.skip_reason:
        console.print(f"\n[bold red]SKIP[/bold red] — {state.skip_reason}")
        if state.draft is None:
            console.print("[dim]No drafting call was made. Build the missing evidence, or move on.[/dim]")
        else:
            console.print("[dim]Drafting was attempted but failed fact-checking; see errors above.[/dim]")
    else:
        gaps = state.uncovered_hard()
        console.print("\n[bold green]PROCEED[/bold green] — "
                      f"{sum(r.covered for r in state.hard())}/{len(state.hard())} hard requirements covered")
        if gaps:
            console.print(f"[yellow]Remaining gap to address in the rewrite:[/yellow] {gaps[0].text}")

    if state.diagnosis and not state.skip_reason:
        console.print("\n[bold]DIAGNOSIS[/bold]")
        if state.diagnosis.hard_gaps:
            console.print("[red]hard gaps:[/red] " + "; ".join(state.diagnosis.hard_gaps))
        if state.diagnosis.positioning_mismatch:
            console.print(f"[yellow]positioning:[/yellow] {state.diagnosis.positioning_mismatch}")
        if state.diagnosis.matches:
            console.print("[green]matches:[/green] " + "; ".join(state.diagnosis.matches[:5]))

    if state.draft:
        console.print(f"\n[bold]DRAFT[/bold]  ({state.attempt_count} attempt(s))")
        console.print(f"profile line: {state.draft.profile_line}")
        console.print(f"sections: {' -> '.join(state.draft.section_order)}")
        if state.scores.review_score is not None:
            console.print(f"review score: {state.scores.review_score}/10")
        if state.draft.highlighted_projects:
            console.print(f"highlighted projects: {', '.join(state.draft.highlighted_projects)}")

    if state.recruiter:
        mark = {"pass": "[green]pass[/green]", "soft_fail": "[yellow]soft_fail[/yellow]", "hard_fail": "[red]hard_fail[/red]"}[state.recruiter.result]
        console.print(f"\n[bold]RECRUITER SCREEN[/bold]  {mark} — {state.recruiter.reason}")

    if state.hiring_manager:
        console.print(f"\n[bold]HIRING MANAGER[/bold]  verdict: {state.hiring_manager.verdict}")
        console.print(state.hiring_manager.why)
        if state.hiring_manager.indefensible_bullets:
            console.print("[red]indefensible under a follow-up question:[/red]")
            for b in state.hiring_manager.indefensible_bullets:
                console.print(f"  - {b}")

    if state.ats:
        console.print(f"\n{state.ats.report}")

    if state.artifacts:
        console.print("[bold]artifacts:[/bold]")
        for k, v in state.artifacts.items():
            console.print(f"  {k}: {v}")

    if state.llm_calls:
        console.print(f"\n[dim]cost: ${state.total_cost_usd:.4f} ({len(state.llm_calls)} LLM call(s))[/dim]")
        if verbose:
            for c in state.llm_calls:
                cache = ""
                if c["cache_read_tokens"] or c["cache_creation_tokens"]:
                    # literal square brackets are Rich markup — escape or they're
                    # silently swallowed by console.print instead of shown (confirmed
                    # live 2026-09-18: this text never rendered, no error either).
                    cache = f" \\[cache: {c['cache_read_tokens']} read, {c['cache_creation_tokens']} written]"
                console.print(
                    f"  [dim]- {c['node']}: {c['input_tokens']}in/{c['output_tokens']}out "
                    f"({c['model']}) -> ${c['cost_usd']:.4f}{cache}[/dim]"
                )

    if verbose:
        console.print("\n[dim]trace:[/dim]")
        for n in state.notes:
            console.print(f"  [dim]- {n}[/dim]")


# ------------------------------------------------------------------------- index


@index_app.command("build")
def index_build(force: bool = typer.Option(False, "--force", help="Drop and rebuild.")) -> None:
    """(Re)build the evidence store from master_profile.yaml."""
    from .evidence import build_index

    count = build_index(force=force)
    console.print(f"evidence store: [bold]{count}[/bold] chunks at {settings.chroma_path}")


@index_app.command("calibrate")
def index_calibrate() -> None:
    """Measure SEM_THRESHOLD against the hand-labeled probes."""
    from .evidence import calibrate

    c = calibrate()
    table = Table(show_lines=False)
    table.add_column("", width=1)
    table.add_column("Probe", overflow="fold", max_width=50)
    table.add_column("Expected", width=9)
    table.add_column("Top bullet", width=26)
    table.add_column("Sim", justify="right", width=6)
    for p in sorted(c["probes"], key=lambda x: -x["similarity"]):
        table.add_row(
            "[green]✓[/green]" if p["correct_at_suggested"] else "[red]✗[/red]",
            p["query"],
            "covered" if p["expected_covered"] else "uncovered",
            p["top_id"],
            f"{p['similarity']:.4f}",
        )
    console.print(table)
    console.print(f"covered range   {c['covered_range']}")
    console.print(f"uncovered range {c['uncovered_range']}")
    console.print(
        f"suggested threshold [bold]{c['suggested_threshold']}[/bold] "
        f"({c['misclassified_probes']}/{c['total_probes']} probes misclassified) — "
        f"config currently uses [bold]{settings.sem_threshold}[/bold]"
    )
    console.print(
        f"\nreranked (cross-encoder) vs cosine, both at current threshold "
        f"{c['current_threshold']}:"
    )
    console.print(
        f"  cosine top-1 correct   {c['cosine_correct_at_current_threshold']}/{c['total_probes']}\n"
        f"  rerank top-1 correct   {c['rerank_correct_at_current_threshold']}/{c['total_probes']}\n"
        f"  rerank changed the top pick on {c['rerank_changed_top_pick']}/{c['total_probes']} probes"
    )


# ----------------------------------------------------------------------- profile


@profile_app.command("check")
def profile_check() -> None:
    """Validate master_profile.yaml integrity and report allowed_metrics drift."""
    from .profile import Profile

    profile = Profile.load()
    errors = profile.validate_profile()
    console.print(
        f"profile: {len(profile.bullets)} bullets, {len(profile.skills)} skills, "
        f"{len(profile.tags())} tags, {len(profile.all_metrics())} metric tokens"
    )

    missing, unbacked = profile.stale_declared_metrics()
    if missing or unbacked:
        console.print("\n[yellow]allowed_metrics drift[/yellow] [dim](cached list, not authoritative)[/dim]")
        if missing:
            console.print(f"  bullets state, list omits : {sorted(missing)}")
        if unbacked:
            console.print(f"  list holds, no bullet backs: {sorted(unbacked)}")

    if errors:
        console.print("\n[bold red]INVALID[/bold red]")
        for e in errors:
            console.print(f"  - {e}")
        raise typer.Exit(1)
    console.print("\n[bold green]OK[/bold green] — profile integrity clean")


# ---------------------------------------------------------------------- source


def _print_jobs_table(jobs: list) -> None:
    table = Table(show_lines=False)
    table.add_column("Title", overflow="fold", max_width=40)
    table.add_column("Company", overflow="fold", max_width=24)
    table.add_column("Location", max_width=16)
    table.add_column("URL", overflow="fold", max_width=40)
    for j in jobs:
        table.add_row(j.title, j.company, j.location, j.url)
    console.print(table)
    console.print(f"[bold]{len(jobs)}[/bold] job(s) found")


def _run_and_report(jobs: list, full_time: bool, verbose: bool) -> None:
    from .db.repo import already_processed
    from .graph import run

    for j in jobs:
        if already_processed(j.id, j.content_hash):
            _log_event({"event": "skip_seen", "job_id": j.id})
            console.print(f"[dim]skip (already processed): {j.title} @ {j.company}[/dim]")
            continue
        state = run(
            j.jd_text,
            title=j.title,
            company=j.company,
            location=j.location,
            source=j.source,
            url=j.url,
            posted_at=j.posted_at,
            lang=j.lang,
            employment_type="fulltime" if full_time else (j.employment_type or "werkstudent"),
        )
        _report(state, verbose=verbose)


@source_app.command("arbeitnow")
def source_arbeitnow(
    query: str = typer.Option("", "--query", "-q", help="Keyword filter (title/JD text)."),
    location: str = typer.Option("", "--location", "-l", help="Location filter."),
    run_pipeline: bool = typer.Option(False, "--run", help="Run each fetched JD through the pipeline."),
    full_time: bool = typer.Option(False, "--full-time", help="Full-time search when running the pipeline."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Fetch postings from Arbeitnow (no auth required)."""
    from .sourcing.arbeitnow import fetch_jobs

    jobs = fetch_jobs(query=query, location=location)
    if run_pipeline:
        _run_and_report(jobs, full_time=full_time, verbose=verbose)
    else:
        _print_jobs_table(jobs)


@source_app.command("adzuna")
def source_adzuna(
    query: str = typer.Option(..., "--query", "-q", help="Keyword search (Adzuna 'what')."),
    location: str = typer.Option("", "--location", "-l", help="Location (Adzuna 'where')."),
    country: str = typer.Option("de", "--country", help="Adzuna country code."),
    run_pipeline: bool = typer.Option(False, "--run", help="Run each fetched JD through the pipeline."),
    full_time: bool = typer.Option(False, "--full-time", help="Full-time search when running the pipeline."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Fetch postings from Adzuna. Requires ADZUNA_APP_ID and ADZUNA_APP_KEY env vars."""
    from .sourcing.adzuna import MissingCredentialsError, fetch_jobs

    try:
        jobs = fetch_jobs(query=query, location=location, country=country)
    except MissingCredentialsError as e:
        raise typer.BadParameter(str(e)) from e

    if run_pipeline:
        _run_and_report(jobs, full_time=full_time, verbose=verbose)
    else:
        _print_jobs_table(jobs)


# ------------------------------------------------------------------------------ run


@app.command()
def run(
    companies: Path | None = typer.Option(
        None, "--companies", help="Path to companies.yaml (default: config/companies.yaml)."
    ),
    digest: str = typer.Option("", "--digest", help="Output format: 'json' for machine-readable stdout."),
    batch: bool = typer.Option(
        False, "--batch", help="Submit extract+diagnose via the Anthropic Batches API (~50% cheaper, slower)."
    ),
) -> None:
    """Poll every configured company (config/companies.yaml), run genuinely new
    postings through the pipeline, print a digest. Meant for a scheduler (see ops/)."""
    from . import runner as runner_mod

    result = runner_mod.run(companies, batch=batch)

    if digest == "json":
        print(json.dumps(result))
        return

    console.print(
        f"[bold]companies polled:[/bold] {result['companies_polled']}  "
        f"[bold]new:[/bold] {result['new_postings']}  "
        f"[bold]prefiltered out:[/bold] {result['prefiltered_out']}  "
        f"[bold]run:[/bold] {result['run']}  [bold]skipped:[/bold] {result['skipped']}"
    )
    v = result["verdicts"]
    console.print(f"verdicts — apply: {v['apply']}  fix_then_apply: {v['fix_then_apply']}  skip: {v['skip']}")
    for reason in result["prefiltered_reasons"]:
        console.print(f"[dim]prefiltered: {reason}[/dim]")
    for line in result["notify"]:
        console.print(f"[yellow]notify:[/yellow] {line}")


if __name__ == "__main__":
    app()
