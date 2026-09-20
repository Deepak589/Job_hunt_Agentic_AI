"""Regression harness for the JobPilot pipeline.

Runs JD fixtures (evals/golden/*.txt, evals/real/*.txt) through the graph, captures
the metrics that matter for regression detection (state.scores + skip_reason, and
optionally state.ats), and diffs the result against a checked-in baseline snapshot
(evals/baseline.json) so a change to evidence.py's threshold or ats.py's rubric shows
up as a fixture diff instead of a manual eyeball.

Costs real Anthropic API money: every fixture makes 4 LLM calls (extract_requirements,
diagnose, rewrite, review) via `run(..., _skip_render=True)`, or just 1 if the job
skips at the hard-gap gate before those run. `--full` additionally runs recruiter_sim,
hiring_manager and the PDF render to get an ATS score — more LLM calls again. The
default fixture set is evals/golden/ (4 files) precisely so a bare `--check` stays
cheap; pass `--fixtures all` to also run the 10 files in evals/real/.

Usage:
    python evals/run_harness.py                        # run golden, print metrics
    python evals/run_harness.py --check                 # run golden, diff vs baseline
    python evals/run_harness.py --update-baseline        # run golden, overwrite baseline
    python evals/run_harness.py --fixtures all --check   # also cover evals/real/
    python evals/run_harness.py --full --update-baseline # include ats.total/verdict
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EVALS_DIR = Path(__file__).parent
BASELINE_PATH = EVALS_DIR / "baseline.json"

# hard/soft coverage, semantic_fit and skip_reason are deterministic given the same
# profile + JD text + embedding model — no LLM feeds them, so zero tolerance is correct.
# ats.total (only captured with --full) is downstream of LLM-written bullets and can
# drift a little run to run without a real regression; give it a band.
ATS_TOTAL_TOL = 5.0


def fixture_paths(which: str) -> list[Path]:
    sets = {
        "golden": sorted((EVALS_DIR / "golden").glob("*.txt")),
        "real": sorted((EVALS_DIR / "real").glob("*.txt")),
    }
    if which == "all":
        return sets["golden"] + sets["real"]
    return sets[which]


def collect_metrics(fixture_path: Path, full: bool) -> dict:
    """Run one fixture through the graph and pull out the regression-relevant fields."""
    from agentic_ai.graph import run  # lazy: keep --help free of API-key/model deps

    jd_text = fixture_path.read_text()
    state = run(jd_text, _skip_render=not full)
    metrics: dict = {
        "hard_coverage": state.scores.hard_coverage,
        "soft_coverage": state.scores.soft_coverage,
        "semantic_fit": state.scores.semantic_fit,
        "skip_reason": state.skip_reason,
    }
    if full and state.ats is not None:
        metrics["ats_total"] = state.ats.total
        metrics["ats_verdict"] = state.ats.verdict
    return metrics


def run_fixtures(paths: list[Path], full: bool) -> dict[str, dict]:
    return {p.stem: collect_metrics(p, full) for p in paths}


def diff_metrics(baseline: dict, current: dict) -> list[str]:
    """Compare one fixture's baseline vs current metrics. Empty list == clean."""
    problems = []
    for key in sorted(set(baseline) | set(current)):
        if key not in baseline:
            problems.append(f"{key}: new metric {current[key]!r} (not in baseline)")
            continue
        if key not in current:
            problems.append(f"{key}: missing (baseline had {baseline[key]!r})")
            continue
        b, c = baseline[key], current[key]
        tol = ATS_TOTAL_TOL if key == "ats_total" else 0.0
        if isinstance(b, (int, float)) and isinstance(c, (int, float)) and tol:
            if abs(b - c) > tol:
                problems.append(f"{key}: {b} -> {c} (moved > {tol})")
        elif b != c:
            problems.append(f"{key}: {b!r} -> {c!r}")
    return problems


def diff_all(baseline: dict[str, dict], current: dict[str, dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for name in sorted(set(baseline) | set(current)):
        if name not in baseline:
            out[name] = ["new fixture, not in baseline (run --update-baseline)"]
        elif name not in current:
            out[name] = ["fixture missing from this run"]
        else:
            problems = diff_metrics(baseline[name], current[name])
            if problems:
                out[name] = problems
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fixtures", choices=["golden", "real", "all"], default="golden",
                         help="fixture set to run (default: golden, 4 files)")
    parser.add_argument("--full", action="store_true",
                         help="full pipeline (render + recruiter_sim + hiring_manager + ats score) "
                              "instead of --no-render; more LLM calls")
    parser.add_argument("--check", action="store_true", help="diff this run against evals/baseline.json")
    parser.add_argument("--update-baseline", action="store_true",
                         help="overwrite evals/baseline.json with this run's metrics")
    args = parser.parse_args(argv)

    paths = fixture_paths(args.fixtures)
    if not paths:
        print(f"no fixtures found for --fixtures {args.fixtures}", file=sys.stderr)
        return 1

    print(f"running {len(paths)} fixture(s) ({args.fixtures}, "
          f"{'full pipeline' if args.full else 'no-render'})...")
    current = run_fixtures(paths, args.full)

    if args.update_baseline:
        BASELINE_PATH.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        print(f"wrote baseline for {len(current)} fixture(s) to {BASELINE_PATH}")
        return 0

    if args.check:
        if not BASELINE_PATH.exists():
            print(f"no baseline at {BASELINE_PATH} — run --update-baseline first", file=sys.stderr)
            return 1
        baseline = json.loads(BASELINE_PATH.read_text())
        problems = diff_all(baseline, current)
        if not problems:
            print(f"OK — {len(current)} fixture(s) match baseline")
            return 0
        for name, lines in problems.items():
            print(f"REGRESSION {name}:")
            for line in lines:
                print(f"  {line}")
        return 1

    for name, metrics in current.items():
        print(name, metrics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
