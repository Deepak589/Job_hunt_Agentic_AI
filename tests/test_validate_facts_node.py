"""validate_facts_node — the keyword-type filter (2026-09-15 live-testing finding).

A soft nice-to-have keyword is exactly the generic, non-committal term an extractor
tags loosely ("data", "learning") — checking it at the same severity as a hard
requirement produced false positives against real JDs. A disqualifier's keywords are
excluded too: `hard_gap_gate` already sent every unmet disqualifier to skip/END before
a draft exists, so re-checking them for fabrication here is redundant by construction.
This node must only pass hard keywords into the fact validator.
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


def test_disqualifier_keyword_is_never_checked_here() -> None:
    """Disqualifiers are proven true upstream by `hard_gap_gate` before a draft ever
    exists — an unmet one never reaches this node. Re-checking one here would only
    reproduce the brittle eligibility-vs-tech word-list this node used to need."""
    profile = Profile.load()
    real = profile.by_id("exp.valuemomentum.b1")
    assert real is not None
    req = Requirement(text="requires: Kubernetes", type="disqualifier", keywords=["kubernetes"])
    state = _state_with_requirement(req, "Deployed the fix using Kubernetes.", real.id)
    out = validate_facts_node(state)
    assert out["validation_errors"] == []


def test_rail_owned_keywords_never_reach_the_fact_checker() -> None:
    """A location or language keyword is not fabricable tech, and the fact-checker must
    never be handed one.

    The old code excluded them by BUCKET — "a DISQUALIFIER is already proven true by
    `hard_gap_gate`". `agent_cv` types and drafts in one call and files such clauses as
    `hard`, so that exclusion stopped firing: on good_jd the cover letter was flagged for
    `claims unevidenced tech 'pullach'` — for naming the town the office is in.
    """
    from agentic_ai.nodes.validate_facts import hard_keywords_for_facts
    from agentic_ai.state import Requirement

    reqs = [
        Requirement(text="Hands-on experience with Python", type="hard", keywords=["python", "git"]),
        Requirement(text="Location: Pullach, nearby Munich, Germany", type="hard",
                    keywords=["pullach", "munich", "on-site", "location"]),
        Requirement(text="Strong communication skills in English", type="hard",
                    keywords=["english", "communication skills"]),
        Requirement(text="Kubernetes", type="soft", keywords=["kubernetes"]),
    ]

    kws = hard_keywords_for_facts(reqs)

    assert "python" in kws and "git" in kws
    assert "pullach" not in kws and "munich" not in kws, "the on-site rail owns these"
    assert "english" not in kws, "the language rail owns this"
    assert "kubernetes" not in kws, "soft keywords were never policed here"
