"""validate_facts graph node — thin wrapper over validators.facts (plan.md §3, §8).

The routing decision (retry vs. give up) lives here, not in the pure validator, because
it needs `state.attempt_count` — a graph concern, not a fact-checking concern.
"""

from __future__ import annotations

from typing import Literal

from ..config import settings
from ..profile import Profile
from ..state import JobState
from ..validators.facts import validate_facts


def validate_facts_node(state: JobState) -> dict:
    assert state.draft is not None, "validate_facts requires rewrite to have run first"
    profile = Profile.load()
    jd_keywords = [kw for r in state.requirements for kw in r.keywords]
    errors = validate_facts(state.draft, profile, jd_keywords=jd_keywords)
    return {
        "validation_errors": errors,
        "notes": [f"validate_facts: {len(errors)} error(s)" if errors else "validate_facts: clean"],
    }


def fact_gate(state: JobState) -> Literal["retry", "clean", "give_up"]:
    if not state.validation_errors:
        return "clean"
    if state.attempt_count < settings.max_rewrite_attempts:
        return "retry"
    return "give_up"


def log_fact_failure(state: JobState) -> dict:
    """A draft that never passed fact-checking must not reach render_documents."""
    reason = (
        f"fact validation failed after {state.attempt_count} rewrite attempt(s): "
        + "; ".join(state.validation_errors)
    )
    return {"skip_reason": reason, "notes": [f"SKIP — {reason}"]}
