"""Adversarial cases for validate_facts — the highest-value guardrail (plan.md §8).

Each test is a lie the rewriter is capable of telling. A false negative here is how a
fabricated number or an invented employer reaches an actual application.
"""

from __future__ import annotations

import pytest

from agentic_ai.profile import Profile
from agentic_ai.state import Draft, DraftBullet
from agentic_ai.validators.facts import validate_facts


@pytest.fixture(scope="module")
def profile() -> Profile:
    return Profile.load()


def _draft(text: str, metric: str | None, source_bullet_id: str, section: str = "projects") -> Draft:
    return Draft(
        profile_line="x",
        section_order=[section],
        bullets={section: [DraftBullet(text=text, metric=metric, source_bullet_id=source_bullet_id)]},
        cover_letter="",
    )


def test_real_bullet_with_real_metric_passes(profile: Profile) -> None:
    b = profile.by_id("proj.rag_pipeline.b1")
    assert b is not None and b.metric
    d = _draft(f"Improved retrieval by {b.metric}.", b.metric, b.id)
    assert validate_facts(d, profile) == []


def test_unknown_source_bullet_id_is_an_error(profile: Profile) -> None:
    d = _draft("Did a thing.", None, "proj.does_not_exist.b99")
    errors = validate_facts(d, profile)
    assert any("unknown source" in e for e in errors)


def test_fabricated_number_not_in_profile_is_an_error(profile: Profile) -> None:
    real = profile.by_id("proj.rag_pipeline.b1")
    assert real is not None
    d = _draft("Reduced latency by 87% using a new cache.", "87%", real.id)
    errors = validate_facts(d, profile)
    assert any("87" in e for e in errors)


def test_inflated_percentage_is_caught_even_with_a_real_prefix(profile: Profile) -> None:
    """The P1 regression class from Phase 1's profile_sync bug, replayed against the
    fact validator: a fabricated number that happens to contain a real substring."""
    real = profile.by_id("proj.rag_pipeline.b1")
    assert real is not None and "0.833" in (real.metric or "")
    d = _draft("Reached 0.8339 recall by tuning the retriever.", "0.8339", real.id)
    errors = validate_facts(d, profile)
    assert any("0.8339" in e for e in errors)


def test_metric_field_none_with_no_number_in_text_passes(profile: Profile) -> None:
    """Skipping Y honestly (CLAUDE.md) must never itself be flagged."""
    real = profile.by_id("proj.cloudnotes.b1")
    assert real is not None
    d = _draft("Built the CI/CD pipeline for CloudNotes.", None, real.id)
    assert validate_facts(d, profile) == []


def test_unevidenced_jd_keyword_planted_in_a_bullet_is_an_error(profile: Profile) -> None:
    """The most likely hallucination (plan.md §8): the model sees a JD term and wants
    to please, so it writes tech that is nowhere in the profile."""
    real = profile.by_id("exp.valuemomentum.b1")
    assert real is not None
    d = _draft("Deployed the fix using Kubernetes.", None, real.id)
    errors = validate_facts(d, profile, jd_keywords=["kubernetes"])
    assert any("kubernetes" in e.lower() for e in errors)


def test_jd_keyword_that_is_genuinely_in_the_profile_is_not_flagged(profile: Profile) -> None:
    real = profile.by_id("proj.rag_pipeline.b1")
    assert real is not None
    assert "python" in profile.all_tech()
    d = _draft("Built the retriever in Python.", None, real.id)
    errors = validate_facts(d, profile, jd_keywords=["python"])
    assert errors == []


def test_unclaimable_german_fluency_in_the_cover_letter_is_an_error(profile: Profile) -> None:
    """never_claim: German professional fluency. Checked wherever draft text appears,
    not just in bullets — a cover letter can lie too."""
    d = Draft(
        profile_line="x",
        section_order=["projects"],
        bullets={},
        cover_letter="I am fluent in German and excited to join your team.",
    )
    errors = validate_facts(d, profile)
    assert any("german" in e.lower() for e in errors)


def test_clean_multi_bullet_draft_passes(profile: Profile) -> None:
    b1 = profile.by_id("proj.rag_pipeline.b1")
    b2 = profile.by_id("proj.cloudnotes.b1")
    assert b1 and b2
    d = Draft(
        profile_line="x",
        section_order=["projects"],
        bullets={
            "projects": [
                DraftBullet(text=f"Improved recall by {b1.metric}.", metric=b1.metric, source_bullet_id=b1.id),
                DraftBullet(text="Built the CloudNotes CI/CD pipeline.", metric=None, source_bullet_id=b2.id),
            ]
        },
        cover_letter="Excited to apply my Python and FastAPI experience.",
    )
    assert validate_facts(d, profile, jd_keywords=["python", "fastapi"]) == []
