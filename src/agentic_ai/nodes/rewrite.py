"""rewrite — LLM call 3, Sonnet (plan.md §3, §7.2). Full profile access; XYZ enforced by
the DraftBullet schema + the fact validator downstream, not by trusting the prompt alone.
"""

from __future__ import annotations

import functools
import json

from langchain_core.messages import HumanMessage, SystemMessage

from ..config import settings
from ..llm import invoke_structured, make_structured
from ..preferences import load_preferences
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
    return make_structured(settings.rewrite_model, Draft, max_tokens=8192)


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
    role, role_usage = classify_role(state.job.jd_text)
    order = section_order_for(role)

    # Ordered for prompt-caching, not readability: the profile block is identical
    # across every job in a run, and everything through section_order is identical
    # across every retry of THIS job (rewrite is re-invoked by both the fact-validator
    # and review retry loops) — only validation_errors changes attempt to attempt.
    # Two `cache_control` breakpoints mark the two reusable prefixes; see costs.py for
    # why the resulting cache-read/write tokens are priced differently from a plain call.
    # 1h ttl on the cross-job-reusable prefix (same reasoning as diagnose.py); the
    # per-job-reusable one stays 5m — it's only ever reused within retries of one job,
    # seconds apart, not across a whole batch run.
    content = [
        {
            "type": "text",
            "text": f"<candidate_profile_bullets>\n{_profile_bullets_json(profile)}\n</candidate_profile_bullets>",
        },
        {
            "type": "text",
            "text": f"<never_claim>\n{profile.never_claim}\n</never_claim>",
            "cache_control": {"type": "ephemeral", "ttl": "1h"},  # end of the cross-job-reusable prefix
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
    preferences = load_preferences()
    if preferences:
        # Per-job, not cache-breakpointed — it grows as more edits accumulate between
        # any two jobs, so caching past this point would go stale. Placed after both
        # existing breakpoints so it doesn't disturb them.
        pref_lines = "\n".join(f"- {p}" for p in preferences)
        content.append({
            "type": "text",
            "text": f"<user_preferences>\n{pref_lines}\n</user_preferences>",
        })
    if state.validation_errors:
        content.append({
            "type": "text",
            "text": f"<prior_validation_errors>\n{json.dumps(state.validation_errors, indent=2)}\n</prior_validation_errors>",
        })
    messages = [
        SystemMessage(content=[{"type": "text", "text": _prompt(), "cache_control": {"type": "ephemeral"}}]),
        HumanMessage(content=content),
    ]

    parsed, usage = invoke_structured(_model(), messages, model=settings.rewrite_model, node="rewrite", verbose=verbose)
    draft = parsed.model_copy(update={"section_order": order})
    return {
        "draft": draft,
        "attempt_count": state.attempt_count + 1,
        "notes": [f"rewrite attempt {state.attempt_count + 1}: {sum(len(v) for v in draft.bullets.values())} bullets"],
        "llm_calls": role_usage + usage,
    }
