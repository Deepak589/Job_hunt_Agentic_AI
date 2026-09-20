"""rewrite — LLM call 3, Sonnet (plan.md §3, §7.2). Full profile access; XYZ enforced by
the DraftBullet schema + the fact validator downstream, not by trusting the prompt alone.
"""

from __future__ import annotations

import functools
import json

from langchain_core.messages import HumanMessage, SystemMessage

from ..config import settings
from ..costs import record_usage
from ..llm import make_llm
from ..profile import Profile
from ..section_order import classify_role, section_order_for
from ..state import Draft, JobState


@functools.lru_cache(maxsize=1)
def _prompt() -> str:
    return (settings.prompts_dir / "rewrite.md").read_text()


@functools.lru_cache(maxsize=1)
def _model():
    # claude-sonnet-5 rejects an explicit `temperature` — the param is deprecated for
    # this model (confirmed live: "`temperature` is deprecated for this model").
    llm = make_llm(settings.rewrite_model, max_tokens=8192)
    return llm.with_structured_output(Draft, include_raw=True)


def _profile_bullets_json(profile: Profile) -> str:
    return json.dumps(
        [
            {"id": b.id, "parent_id": b.parent_id, "outcome": b.outcome, "metric": b.metric,
             "method": b.method, "status": b.status, "tags": b.tags}
            for b in profile.bullets
        ],
        indent=2,
    )


def rewrite(state: JobState, verbose: bool = False) -> dict:
    assert state.diagnosis is not None, "rewrite requires diagnose to have run first"
    profile = Profile.load()
    role = classify_role(state.job.jd_text)
    order = section_order_for(role)

    # Ordered for prompt-caching, not readability: the profile block is identical
    # across every job in a run, and everything through section_order is identical
    # across every retry of THIS job (rewrite is re-invoked by both the fact-validator
    # and review retry loops) — only validation_errors changes attempt to attempt.
    # Two `cache_control` breakpoints mark the two reusable prefixes; see costs.py for
    # why the resulting cache-read/write tokens are priced differently from a plain call.
    content = [
        {
            "type": "text",
            "text": f"<candidate_profile_bullets>\n{_profile_bullets_json(profile)}\n</candidate_profile_bullets>",
        },
        {
            "type": "text",
            "text": f"<never_claim>\n{profile.never_claim}\n</never_claim>",
            "cache_control": {"type": "ephemeral"},  # end of the cross-job-reusable prefix
        },
        {
            "type": "text",
            "text": f"<job_description>\n{state.job.jd_text.strip()}\n</job_description>",
        },
        {
            "type": "text",
            "text": f"<diagnosis>\n{state.diagnosis.model_dump_json(indent=2)}\n</diagnosis>",
        },
        {
            "type": "text",
            "text": f"<section_order>{order}</section_order>",
            "cache_control": {"type": "ephemeral"},  # end of the per-job-reusable prefix
        },
    ]
    if state.validation_errors:
        content.append({
            "type": "text",
            "text": f"<prior_validation_errors>\n{json.dumps(state.validation_errors, indent=2)}\n</prior_validation_errors>",
        })
    messages = [
        SystemMessage(content=[{"type": "text", "text": _prompt(), "cache_control": {"type": "ephemeral"}}]),
        HumanMessage(content=content),
    ]

    last_error: Exception | None = None
    usage: list[dict] = []
    for attempt in (1, 2):
        try:
            result = _model().invoke(messages)
            usage.append(record_usage(result["raw"], settings.rewrite_model, "rewrite"))
            if verbose:
                print(f"--- rewrite raw response (attempt {attempt}) ---")
                print(result["raw"].content)
            if result["parsing_error"]:
                raise ValueError(result["parsing_error"])
            draft = result["parsed"].model_copy(update={"section_order": order})
            return {
                "draft": draft,
                "attempt_count": state.attempt_count + 1,
                "notes": [f"rewrite attempt {state.attempt_count + 1}: {sum(len(v) for v in draft.bullets.values())} bullets"],
                "llm_calls": usage,
            }
        except Exception as exc:  # noqa: BLE001 — retried once, then surfaced
            last_error = exc

    raise RuntimeError(f"rewrite failed twice: {last_error}")
