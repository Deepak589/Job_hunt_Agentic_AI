"""Role classification + deterministic section order (plan.md §7, CLAUDE.md Section Order).

The LLM only picks WHICH role type a JD is; the section order for that role type is a
fixed dict lookup. Letting the model freestyle the order would make it inconsistent
across applications for the same role type — the thing plan.md §7 explicitly warns against.
"""

from __future__ import annotations

import functools
from typing import Literal

from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel, Field

from .config import settings

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
    llm = ChatAnthropic(model=settings.role_classifier_model, temperature=0, max_tokens=256)
    return llm.with_structured_output(RoleClassification, include_raw=True)


@functools.lru_cache(maxsize=128)
def classify_role(jd_text: str) -> RoleType:
    """Cached by jd_text — a review/fact-validation retry re-invokes rewrite for the
    same JD, and the role classification cannot have changed between attempts.

    Not wired into costs.py: the caller only wants `RoleType` back, and the cache above
    already kills repeat calls. A 256-max_tokens Haiku call is cheap enough that the
    small miss on `JobState.total_cost_usd` isn't worth a second return value here.
    """
    messages = [
        ("system", _prompt()),
        ("human", f"<job_description>\n{jd_text.strip()}\n</job_description>"),
    ]
    last_error: Exception | None = None
    for _ in (1, 2):
        try:
            result = _model().invoke(messages)
            if result["parsing_error"]:
                raise ValueError(result["parsing_error"])
            return result["parsed"].role
        except Exception as exc:  # noqa: BLE001 — retried once, then falls back
            last_error = exc
    # A classifier failure must not block the pipeline — fall back to the general case
    # rather than raise, unlike extract_requirements (whose output the gate depends on).
    del last_error
    return "fullstack_ship"
