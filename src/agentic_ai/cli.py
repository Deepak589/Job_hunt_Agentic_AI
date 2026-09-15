"""jobpilot — Phase 1 CLI.

    jobpilot add --file jd.txt        run a JD through the gap report
    jobpilot index build [--force]    (re)build the evidence store
    jobpilot index calibrate          measure SEM_THRESHOLD against the labeled probes
    jobpilot profile check            validate master_profile.yaml
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import settings

app = typer.Typer(add_completion=False, help="Job-hunt pipeline — Phase 1.")
index_app = typer.Typer(help="Evidence store.")
profile_app = typer.Typer(help="Master profile.")
app.add_typer(index_app, name="index")
app.add_typer(profile_app, name="profile")

console = Console()


# --------------------------------------------------------------------------- add


@app.command()
def add(
    file: Path | None = typer.Option(None, "--file", "-f", help="Path to a saved JD."),
    stdin: bool = typer.Option(False, "--stdin", help="Read the JD from stdin."),
    title: str = typer.Option("", "--title", help="Job title, if known."),
    company: str = typer.Option("", "--company", help="Company, if known."),
    location: str = typer.Option(
        "", "--location", help="Where the job is. Decides every on-site requirement."
    ),
    full_time: bool = typer.Option(
        False, "--full-time", help="Full-time search: lift the werkstudent weekly-hours cap."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print prompts and raw responses."),
) -> None:
    """Run one job description through the gap report."""
    if file:
        jd_text = file.read_text()
    elif stdin:
        jd_text = sys.stdin.read()
    else:
        raise typer.BadParameter("give --file or --stdin")
    if not jd_text.strip():
        raise typer.BadParameter("job description is empty")

    from .graph import run

    if verbose:
        from . import nodes  # noqa: F401
        console.print(f"[dim]model: {settings.extract_model}  threshold: {settings.sem_threshold}[/dim]")

    state = run(
        jd_text,
        title=title,
        company=company,
        location=location,
        employment_type="fulltime" if full_time else "werkstudent",
    )
    _report(state, verbose=verbose)
    raise typer.Exit(1 if state.skip_reason else 0)


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
        console.print("[dim]No drafting call was made. Build the missing evidence, or move on.[/dim]")
    else:
        gaps = state.uncovered_hard()
        console.print("\n[bold green]PROCEED[/bold green] — "
                      f"{sum(r.covered for r in state.hard())}/{len(state.hard())} hard requirements covered")
        if gaps:
            console.print(f"[yellow]Remaining gap to address in the rewrite:[/yellow] {gaps[0].text}")

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


if __name__ == "__main__":
    app()
