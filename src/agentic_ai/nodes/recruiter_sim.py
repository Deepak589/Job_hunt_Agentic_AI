"""recruiter_sim — deterministic keyword-literal ATS scan (plan.md §7, CLAUDE.md role 4,
solution.md step 9). Was an LLM call (Haiku); a shallow keyword scanner needs no model —
it's pure substring matching, same domain `scoring/ats.py`'s literal-keywords component
already checks.
"""

from __future__ import annotations

from typing import Literal

from ..state import JobState, RecruiterResult


def _rendered_cv_text(state: JobState) -> str:
    assert state.draft is not None
    lines = [state.draft.profile_line]
    for bullets in state.draft.bullets.values():
        lines.extend(b.text for b in bullets)
    return "\n".join(lines)


def recruiter_sim(state: JobState, verbose: bool = False) -> dict:
    """Deterministic literal-keyword screen. Only checks requirements the pipeline
    already believes are covered — an uncovered hard requirement already failed
    upstream (hard-gap gate) or is an accepted known gap; this node isn't re-litigating
    coverage, only whether what's believed covered actually shows up verbatim in the
    draft (the thing a real keyword-scanning ATS would check).

    Note: this node's output space is now binary (pass/hard_fail) — there's no
    natural deterministic "soft_fail" left once the check is per-requirement
    literal-substring-or-not. `RecruiterResult.result` keeps "soft_fail" as a valid
    literal for other code/tests, but this node never returns it (solution.md step 9
    ruling: no threshold invented to keep a third bucket alive with no data behind it).
    """
    assert state.draft is not None, "recruiter_sim requires rewrite to have run first"
    cv_text = _rendered_cv_text(state).lower()

    covered_hard = [r for r in state.hard() if r.covered]
    missing = [r for r in covered_hard if not any(kw.lower() in cv_text for kw in r.keywords)]

    if missing:
        result: Literal["pass", "hard_fail"] = "hard_fail"
        reason = "hard requirement(s) not found verbatim in draft: " + ", ".join(r.text for r in missing)
    else:
        result = "pass"
        reason = "all covered hard requirements' keywords present"

    verdict = RecruiterResult(result=result, reason=reason)
    return {
        "recruiter": verdict,
        "notes": [f"recruiter_sim: {verdict.result} — {verdict.reason}"],
        "llm_calls": [],
    }


def recruiter_gate(state: JobState) -> Literal["hard_fail", "proceed"]:
    """'hard_fail' skips before render — CLAUDE.md role 4: "Hard-fail = don't recommend
    applying without fixing the gap first." pass/soft_fail both proceed to hiring_manager."""
    if state.recruiter is not None and state.recruiter.result == "hard_fail":
        return "hard_fail"
    return "proceed"


def log_recruiter_fail(state: JobState) -> dict:
    """A draft the recruiter screen would hard-fail must not reach render_documents."""
    reason = f"recruiter_sim hard_fail: {state.recruiter.reason if state.recruiter else 'unknown'}"
    return {"skip_reason": reason, "notes": [f"SKIP — {reason}"]}
