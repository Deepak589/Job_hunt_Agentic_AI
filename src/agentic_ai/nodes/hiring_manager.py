"""hiring_manager — LLM call 6, Sonnet (plan.md §7, CLAUDE.md role 5). "Could you defend
this in an interview" — the highest-value check in the pipeline, kept on Claude.
"""

from __future__ import annotations

import functools
import json

from langchain_core.messages import HumanMessage, SystemMessage

from ..config import settings
from ..costs import record_usage
from ..llm import make_llm
from ..profile import Profile
from ..state import HiringManagerVerdict, JobState


@functools.lru_cache(maxsize=1)
def _prompt() -> str:
    return (settings.prompts_dir / "hiring_manager.md").read_text()


@functools.lru_cache(maxsize=1)
def _model():
    llm = make_llm(settings.hiring_manager_model, max_tokens=2048)
    return llm.with_structured_output(HiringManagerVerdict, include_raw=True)


def _profile_bullets_json(profile: Profile) -> str:
    return json.dumps(
        [
            {"id": b.id, "outcome": b.outcome, "metric": b.metric, "method": b.method, "status": b.status}
            for b in profile.bullets
        ],
        indent=2,
    )


def hiring_manager(state: JobState, verbose: bool = False) -> dict:
    assert state.draft is not None, "hiring_manager requires rewrite to have run first"
    profile = Profile.load()
    # Profile block first + cache_control, same cross-job reuse pattern as diagnose.py —
    # identical across every job in a run.
    content = [
        {
            "type": "text",
            "text": f"<candidate_profile_bullets>\n{_profile_bullets_json(profile)}\n</candidate_profile_bullets>",
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": f"<job_description>\n{state.job.jd_text.strip()}\n</job_description>",
        },
        {
            "type": "text",
            "text": f"<draft>\n{state.draft.model_dump_json(indent=2)}\n</draft>",
        },
    ]
    messages = [
        SystemMessage(content=[{"type": "text", "text": _prompt(), "cache_control": {"type": "ephemeral"}}]),
        HumanMessage(content=content),
    ]

    last_error: Exception | None = None
    usage: list[dict] = []
    for attempt in (1, 2):
        try:
            result = _model().invoke(messages)
            usage.append(record_usage(result["raw"], settings.hiring_manager_model, "hiring_manager"))
            if verbose:
                print(f"--- hiring_manager raw response (attempt {attempt}) ---")
                print(result["raw"].content)
            if result["parsing_error"]:
                raise ValueError(result["parsing_error"])
            verdict = result["parsed"]
            return {
                "hiring_manager": verdict,
                "notes": [f"hiring_manager: {verdict.verdict} — {verdict.why}"],
                "llm_calls": usage,
            }
        except Exception as exc:  # noqa: BLE001 — retried once, then surfaced
            last_error = exc

    raise RuntimeError(f"hiring_manager failed twice: {last_error}")
