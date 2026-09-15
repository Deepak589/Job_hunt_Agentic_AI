"""validate_facts_node — the keyword-type filter (2026-09-15 live-testing finding).

A soft nice-to-have keyword is exactly the generic, non-committal term an extractor
tags loosely ("data", "learning") — checking it at the same severity as a hard
requirement produced false positives against real JDs. This node must only pass
hard/disqualifier keywords into the fact validator, never soft ones.
"""

from __future__ import annotations

from agentic_ai.nodes.validate_facts import validate_facts_node
from agentic_ai.profile import Profile
from agentic_ai.state import Draft, DraftBullet, Job, JobState, Requirement


def _state_with_requirement(req: Requirement, bullet_text: str, source_bullet_id: str) -> JobState:
    job = Job(id="t", source="manual", title="Test Role", jd_text="...")
    draft = Draft(
        profile_line="x",
        section_order=["experience"],
        bullets={
            "experience": [
                DraftBullet(text=bullet_text, metric=None, source_bullet_id=source_bullet_id)
            ]
        },
        cover_letter="",
    )
    return JobState(job=job, draft=draft, requirements=[req])


def test_soft_keyword_not_evidenced_anywhere_is_not_flagged() -> None:
    """A soft requirement's keyword must never reach validate_facts — even one with no
    evidence anywhere in the profile, unlike a hard/disqualifier keyword (below)."""
    profile = Profile.load()
    real = profile.by_id("exp.valuemomentum.b1")
    assert real is not None
    req = Requirement(text="nice to have: Kubernetes", type="soft", keywords=["kubernetes"])
    state = _state_with_requirement(req, "Deployed the fix using Kubernetes.", real.id)
    out = validate_facts_node(state)
    assert out["validation_errors"] == []


def test_hard_keyword_not_evidenced_anywhere_is_still_flagged() -> None:
    profile = Profile.load()
    real = profile.by_id("exp.valuemomentum.b1")
    assert real is not None
    req = Requirement(text="must have: Kubernetes", type="hard", keywords=["kubernetes"])
    state = _state_with_requirement(req, "Deployed the fix using Kubernetes.", real.id)
    out = validate_facts_node(state)
    assert any("kubernetes" in e.lower() for e in out["validation_errors"])


def test_disqualifier_keyword_not_evidenced_anywhere_is_still_flagged() -> None:
    profile = Profile.load()
    real = profile.by_id("exp.valuemomentum.b1")
    assert real is not None
    req = Requirement(text="requires: Kubernetes", type="disqualifier", keywords=["kubernetes"])
    state = _state_with_requirement(req, "Deployed the fix using Kubernetes.", real.id)
    out = validate_facts_node(state)
    assert any("kubernetes" in e.lower() for e in out["validation_errors"])
