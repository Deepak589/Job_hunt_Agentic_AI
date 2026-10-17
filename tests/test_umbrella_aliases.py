"""The umbrella-term false positive, and the scoping that must survive fixing it.

`good_jd` (Linde, "Working Student - AI Engineering (Generative AI & Agents)") reported
fact errors on terms that are honest: `llm`, `llms`, `generative ai`, `agentic ai`,
`business applications` — five on the agent path, one on the graph path, all five tracing
to one JD line, and all five describing work the profile really contains. The profile has
no skill called "LLM"; it has LangChain, LangGraph, Ollama, transformers and RAG.

The fix (validators/aliases.py) resolves an umbrella through its backing terms IN THE SAME
SCOPE the term was already being checked in. These tests pin both halves of that, because
only the second half makes the first one safe to ship: the umbrella passes where the
backing work is, and still fails where it is not.
"""

from __future__ import annotations

import pytest

from agentic_ai.coverage import _normalize
from agentic_ai.profile import Profile
from agentic_ai.state import Draft, DraftBullet
from agentic_ai.validators.aliases import UMBRELLA_EVIDENCE, is_evidenced
from agentic_ai.validators.facts import _bullet_text_tokens, validate_facts

# The two hard requirements `analyze` actually produced for good_jd, verbatim from the
# checkpoint — not a hand-written approximation of them.
GOOD_JD_HARD_KEYWORDS = [
    "python", "software development practices", "git", "ci/cd",
    "llm", "llms", "large language models", "generative ai", "agentic ai",
    "ai application development",
]

AI_PROJECT_BULLET = "proj.jobpilot_multi_agent_job_search_cv_tailoring_pipeline.b1"
INSURANCE_BULLET = "exp.valuemomentum.b1"


@pytest.fixture(scope="module")
def profile() -> Profile:
    return Profile.load()


def _draft(bullet_text: str, source_bullet_id: str, cover_letter: str = "") -> Draft:
    return Draft(
        profile_line="x",
        section_order=["projects"],
        bullets={"projects": [DraftBullet(text=bullet_text, metric=None, source_bullet_id=source_bullet_id)]},
        cover_letter=cover_letter,
    )


def test_cover_letter_may_use_the_jd_umbrella_when_the_profile_holds_the_work(profile: Profile) -> None:
    """The exact regression: this sentence is true, and it was reported as fabrication."""
    letter = (
        "I build with LLMs and agentic AI: a multi-agent LangGraph pipeline and a "
        "hand-rolled RAG retriever. My generative AI work is hands-on, not theoretical."
    )
    draft = _draft("Built a multi-agent pipeline.", AI_PROJECT_BULLET, cover_letter=letter)

    errors = [e for e in validate_facts(draft, profile, jd_keywords=GOOD_JD_HARD_KEYWORDS) if "unevidenced" in e]
    assert errors == []


def test_umbrella_on_a_bullet_whose_own_work_backs_it_passes(profile: Profile) -> None:
    draft = _draft("Built an agentic AI pipeline that tailors CVs.", AI_PROJECT_BULLET)

    errors = [e for e in validate_facts(draft, profile, jd_keywords=["agentic ai"]) if "unevidenced" in e]
    assert errors == []


def test_umbrella_on_an_unrelated_bullet_is_still_an_error(profile: Profile) -> None:
    """The guarantee that makes the alias table safe. The insurance data migration is real
    work, and the profile really does contain LLM work — but not in THIS bullet. An alias
    that leaked profile-wide would pass this, and then "LLM" could be printed on any
    bullet on the CV."""
    draft = _draft("Migrated policy data using LLMs.", INSURANCE_BULLET)

    errors = validate_facts(draft, profile, jd_keywords=["llm"])
    assert any("llm" in e.lower() and "unevidenced" in e for e in errors)


def test_a_tool_the_profile_never_used_is_still_an_error(profile: Profile) -> None:
    """The alias table must not have widened the matcher in general."""
    draft = _draft("Shipped it on Kubernetes.", INSURANCE_BULLET)

    errors = validate_facts(draft, profile, jd_keywords=["kubernetes"])
    assert any("kubernetes" in e.lower() for e in errors)


def test_an_umbrella_with_no_backing_in_scope_does_not_pass() -> None:
    """`is_evidenced` is pure: an umbrella whose backing terms are all absent fails."""
    assert not is_evidenced("generative ai", frozenset({"plsql", "oneshield"}))
    assert is_evidenced("generative ai", frozenset({"plsql", "langchain"}))


def test_every_alias_target_is_a_real_profile_term(profile: Profile) -> None:
    """The table's own invariant, and the reason it cannot quietly become a whitelist.

    Every term on the right-hand side must already exist in the profile's evidence
    universe. A row whose backing is absent either never fires (dead weight that reads
    like coverage) or, worse, was added to make a run pass. Either way this fails first.
    """
    universe = {_normalize(t) for t in profile.all_tech() | profile.all_stack()}
    for b in profile.bullets:
        universe |= _bullet_text_tokens(b)
    summary = profile.raw.get("profile_summary", {})
    universe |= set(
        _normalize(str(summary.get("core", "")) + " " + " ".join(summary.get("proof_points", []))).split()
    )

    missing = {
        umbrella: [t for t in backing if t not in universe]
        for umbrella, backing in UMBRELLA_EVIDENCE.items()
    }
    assert {u: m for u, m in missing.items() if m} == {}


def test_plural_spelling_folds_onto_one_row() -> None:
    """"llms" and "llm" are one row: `coverage._singular` folds the plural, so the table
    does not need a line per spelling and cannot drift between them."""
    assert _normalize("llms") == _normalize("llm")
    assert _normalize("llms") in UMBRELLA_EVIDENCE
