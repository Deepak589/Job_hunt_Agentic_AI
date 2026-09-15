"""rewrite — LLM call 3, Sonnet (plan.md §3, §7.2). Full profile access; XYZ enforced by
the DraftBullet schema + the fact validator downstream, not by trusting the prompt alone.
"""

from __future__ import annotations

import functools
import json

from langchain_anthropic import ChatAnthropic

from ..config import settings
from ..profile import Profile
from ..section_order import classify_role, section_order_for
from ..state import Draft, JobState


@functools.lru_cache(maxsize=1)
def _prompt() -> str:
    return (settings.prompts_dir / "rewrite.md").read_text()


@functools.lru_cache(maxsize=1)
def _model():
    llm = ChatAnthropic(model=settings.rewrite_model, temperature=0.3, max_tokens=8192)
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

    human_parts = [
        f"<job_description>\n{state.job.jd_text.strip()}\n</job_description>",
        f"<diagnosis>\n{state.diagnosis.model_dump_json(indent=2)}\n</diagnosis>",
        f"<section_order>{order}</section_order>",
        f"<candidate_profile_bullets>\n{_profile_bullets_json(profile)}\n</candidate_profile_bullets>",
        f"<never_claim>\n{profile.never_claim}\n</never_claim>",
    ]
    if state.validation_errors:
        human_parts.append(
            f"<prior_validation_errors>\n{json.dumps(state.validation_errors, indent=2)}\n</prior_validation_errors>"
        )
    messages = [("system", _prompt()), ("human", "\n\n".join(human_parts))]

    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            result = _model().invoke(messages)
            if verbose:
                print(f"--- rewrite raw response (attempt {attempt}) ---")
                print(result["raw"].content)
            if result["parsing_error"]:
                raise ValueError(result["parsing_error"])
            draft = result["parsed"]
            return {
                "draft": draft,
                "attempt_count": state.attempt_count + 1,
                "notes": [f"rewrite attempt {state.attempt_count + 1}: {sum(len(v) for v in draft.bullets.values())} bullets"],
            }
        except Exception as exc:  # noqa: BLE001 — retried once, then surfaced
            last_error = exc

    raise RuntimeError(f"rewrite failed twice: {last_error}")
