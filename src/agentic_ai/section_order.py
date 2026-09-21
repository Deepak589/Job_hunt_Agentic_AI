"""Role classification + deterministic section order (plan.md §7, CLAUDE.md Section Order).

The LLM only picks WHICH role type a JD is; the section order for that role type is a
fixed dict lookup. Letting the model freestyle the order would make it inconsistent
across applications for the same role type — the thing plan.md §7 explicitly warns against.
"""

from __future__ import annotations

import functools
from typing import Literal

from pydantic import BaseModel, Field

from .config import settings
from .llm import invoke_structured, make_structured

RoleType = Literal["data_scientist", "data_analyst", "ai_engineer", "fullstack_ship"]

# CLAUDE.md "Section Order (tailor per role)"
SECTION_ORDER: dict[RoleType, list[str]] = {
    "data_scientist": ["skills", "projects", "experience"],
    "data_analyst": ["experience", "skills", "projects"],
    "ai_engineer": ["projects", "experience", "skills"],
    "fullstack_ship": ["profile", "projects", "experience", "skills"],
}


def section_order_for(role: RoleType) -> list[str]:
    return SECTION_ORDER[role]


class RoleClassification(BaseModel):
    """classify_role — which of the four CLAUDE.md role archetypes this JD is."""

    role: RoleType = Field(
        description=(
            "data_scientist: modeling/analysis depth is the ask. "
            "data_analyst: reporting/BI/SQL-first. "
            "ai_engineer: building AI/agentic products or ML systems in production. "
            "fullstack_ship: general shipping-focused role where AI-tool fluency beats exact stack match."
        )
    )


@functools.lru_cache(maxsize=1)
def _prompt() -> str:
    return (
        "Classify this job description into exactly one role archetype for CV section "
        "ordering. Pick the one whose emphasis matches what the JD rewards, not just "
        "keyword overlap — a title alone is not enough."
    )


@functools.lru_cache(maxsize=1)
def _model():
    return make_structured(settings.role_classifier_model, RoleClassification, temperature=0, max_tokens=256)


# Manual cache (not functools.lru_cache): a cache HIT must report zero usage (no API
# call happened), but a MISS must report the real usage record — lru_cache can't
# distinguish those for a second return value, so it would either double-count a
# retry's cost or drop the first call's cost entirely.
_role_cache: dict[str, RoleType] = {}


def classify_role(jd_text: str) -> tuple[RoleType, list[dict]]:
    """Cached by jd_text — a review/fact-validation retry re-invokes rewrite for the
    same JD, and the role classification cannot have changed between attempts. It's a
    paid call (solution.md step 5), so the caller gets the usage record back too — empty
    on a cache hit, since no call was made.
    """
    if jd_text in _role_cache:
        return _role_cache[jd_text], []

    messages = [
        ("system", _prompt()),
        ("human", f"<job_description>\n{jd_text.strip()}\n</job_description>"),
    ]
    try:
        role, usage = invoke_structured(
            _model(), messages, model=settings.role_classifier_model, node="classify_role"
        )
        role = role.role
    except RuntimeError:
        # A classifier failure must not block the pipeline — fall back to the general
        # case rather than raise, unlike extract_requirements (whose output the gate
        # depends on). Not cached: a transient failure shouldn't pin every future call
        # for this JD to the fallback.
        return "fullstack_ship", []
    _role_cache[jd_text] = role
    return role, usage
