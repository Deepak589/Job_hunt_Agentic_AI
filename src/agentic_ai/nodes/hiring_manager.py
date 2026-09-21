"""hiring_manager — LLM call 6, Sonnet (plan.md §7, CLAUDE.md role 5). "Could you defend
this in an interview" — the highest-value check in the pipeline, kept on Claude.
"""

from __future__ import annotations

import functools
import json

from langchain_core.messages import HumanMessage, SystemMessage

from ..config import settings
from ..llm import invoke_structured, make_structured
from ..profile import Profile
from ..state import HiringManagerVerdict, JobState


@functools.lru_cache(maxsize=1)
def _prompt() -> str:
    return (settings.prompts_dir / "hiring_manager.md").read_text()


@functools.lru_cache(maxsize=1)
def _model():
    return make_structured(settings.hiring_manager_model, HiringManagerVerdict, max_tokens=2048)


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

    verdict, usage = invoke_structured(_model(), messages, model=settings.hiring_manager_model, node="hiring_manager", verbose=verbose)
    return {
        "hiring_manager": verdict,
        "notes": [f"hiring_manager: {verdict.verdict} — {verdict.why}"],
        "llm_calls": usage,
    }
