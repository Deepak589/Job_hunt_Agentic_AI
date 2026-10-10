#!/usr/bin/env python3
"""Run the graph path and the single-call agent path over the same JDs, print one table.

    .venv/bin/python scripts/compare_paths.py evals/golden/*.txt
    .venv/bin/python scripts/compare_paths.py JD/good_jd.txt --render

Reads nothing from the runs DB and writes nothing to it: both paths are invoked in-process
so a dedupe row can never silence a comparison. Costs real API calls — one graph run plus
one agent call per JD.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from agentic_ai.nodes.agent_cv import agent_cv
from agentic_ai.profile import Profile
from agentic_ai.validators.facts import validate_facts


def _counts(reqs) -> dict[str, int]:
    return {t: sum(1 for r in reqs if r.type == t) for t in ("hard", "soft", "disqualifier")}


async def _graph_row(jd_text: str, location: str) -> dict:
    """One graph run, no render (Chrome-free), no DB dedupe."""
    from agentic_ai import graph as g
    from agentic_ai.state import Job, JobState

    # skip_render=True: no Chrome, and no checkpointer needed (no human_review node,
    # so nothing calls interrupt()). A fresh thread_id per comparison keeps the old
    # pipeline's checkpoints from leaking in.
    compiled = g.build_graph(review_pause=False, skip_render=True)
    job = Job(id=g.job_id("manual", "", jd_text), source="manual", url="", jd_text=jd_text, location=location)
    state = await compiled.ainvoke(JobState(job=job), {"configurable": {"thread_id": f"cmp:{job.id}"}})
    state = JobState(**state) if isinstance(state, dict) else state
    bullets = sum(len(v) for v in state.draft.bullets.values()) if state.draft else 0
    return {
        "path": "graph",
        "requirement_counts": _counts(state.requirements),
        "hard_coverage": round(state.scores.hard_coverage, 4),
        "bullets": bullets,
        "fact_error_count": len(state.validation_errors),
        "fact_errors": state.validation_errors,
        "skip_reason": state.skip_reason,
        "cost_usd": round(state.total_cost_usd, 6),
    }


def _agent_row(jd_text: str, location: str) -> dict:
    result = agent_cv(jd_text, job_location=location)
    hard_keywords = [kw for r in result.requirements if r.type == "hard" for kw in r.keywords]
    errors = validate_facts(result.draft, Profile.load(), jd_keywords=hard_keywords)
    return {
        "path": "agent",
        "requirement_counts": _counts(result.requirements),
        "hard_coverage": round(result.scores.hard_coverage, 4),
        "bullets": sum(len(v) for v in result.draft.bullets.values()),
        "fact_error_count": len(errors),
        "fact_errors": errors,
        "skip_reason": None,
        "final_verdict": result.final_verdict,
        "self_score": result.self_score,
        "cost_usd": result.total_cost_usd,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("jds", nargs="+", type=Path)
    ap.add_argument("--location", default="")
    ap.add_argument("--graph-only", action="store_true")
    ap.add_argument("--agent-only", action="store_true")
    args = ap.parse_args()

    rows: list[dict] = []
    for jd in args.jds:
        text = jd.read_text()
        print(f"\n=== {jd.name} ===")
        if not args.agent_only:
            try:
                r = asyncio.run(_graph_row(text, args.location))
            except Exception as exc:  # a crash is a result worth recording, not a stop
                r = {"path": "graph", "error": f"{type(exc).__name__}: {exc}"}
            r["jd"] = jd.name
            rows.append(r)
            print(json.dumps(r, indent=2))
        if not args.graph_only:
            try:
                r = _agent_row(text, args.location)
            except Exception as exc:
                r = {"path": "agent", "error": f"{type(exc).__name__}: {exc}"}
            r["jd"] = jd.name
            rows.append(r)
            print(json.dumps(r, indent=2))

    out = Path("out/compare_paths.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2))
    print(f"\nwrote {out}")

    print(f"\n{'JD':<38} {'path':<6} {'reqs':<16} {'hardcov':<8} {'bul':<4} {'facterr':<8} {'cost':<9} verdict/skip")
    for r in rows:
        if "error" in r:
            print(f"{r['jd']:<38} {r['path']:<6} ERROR {r['error'][:60]}")
            continue
        c = r["requirement_counts"]
        reqs = f"{c['hard']}/{c['soft']}/{c['disqualifier']}"
        tail = r.get("final_verdict") or r.get("skip_reason") or "-"
        print(
            f"{r['jd']:<38} {r['path']:<6} {reqs:<16} {r['hard_coverage']:<8} "
            f"{r['bullets']:<4} {r['fact_error_count']:<8} ${r['cost_usd']:<8} {tail}"
        )


if __name__ == "__main__":
    main()
