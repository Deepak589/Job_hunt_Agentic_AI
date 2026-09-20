"""recruiter_sim — LLM call 5, Haiku (plan.md §7, CLAUDE.md role 4). Fast, shallow,
keyword-literal on purpose — simulating a shallow filter with a deep model defeats
the point.
"""

from __future__ import annotations

import functools
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage

from ..config import settings
from ..costs import record_usage
from ..llm import make_llm
from ..state import JobState, RecruiterResult


@functools.lru_cache(maxsize=1)
def _prompt() -> str:
    return (settings.prompts_dir / "recruiter_sim.md").read_text()


@functools.lru_cache(maxsize=1)
def _model():
    llm = make_llm(settings.recruiter_model, max_tokens=1024)
    return llm.with_structured_output(RecruiterResult, include_raw=True)


def _rendered_cv_text(state: JobState) -> str:
    assert state.draft is not None
    lines = [state.draft.profile_line]
    for bullets in state.draft.bullets.values():
        lines.extend(b.text for b in bullets)
    return "\n".join(lines)


def recruiter_sim(state: JobState, verbose: bool = False) -> dict:
    assert state.draft is not None, "recruiter_sim requires rewrite to have run first"
    hard_reqs = "\n".join(f"- {r.text}" for r in state.hard())
    human = (
        f"<job_description>\n{state.job.jd_text.strip()}\n</job_description>\n\n"
        f"<hard_requirements>\n{hard_reqs}\n</hard_requirements>\n\n"
        f"<cv_text>\n{_rendered_cv_text(state)}\n</cv_text>"
    )
    messages = [
        SystemMessage(content=[{"type": "text", "text": _prompt(), "cache_control": {"type": "ephemeral"}}]),
        HumanMessage(content=human),
    ]

    last_error: Exception | None = None
    usage: list[dict] = []
    for attempt in (1, 2):
        try:
            result = _model().invoke(messages)
            usage.append(record_usage(result["raw"], settings.recruiter_model, "recruiter_sim"))
            if verbose:
                print(f"--- recruiter_sim raw response (attempt {attempt}) ---")
                print(result["raw"].content)
            if result["parsing_error"]:
                raise ValueError(result["parsing_error"])
            verdict = result["parsed"]
            return {
                "recruiter": verdict,
                "notes": [f"recruiter_sim: {verdict.result} — {verdict.reason}"],
                "llm_calls": usage,
            }
        except Exception as exc:  # noqa: BLE001 — retried once, then surfaced
            last_error = exc

    raise RuntimeError(f"recruiter_sim failed twice: {last_error}")


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
