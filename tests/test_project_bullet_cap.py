"""_capped_per_project — at most N bullets per project, experience untouched.

The per-SECTION cap could not express this: every project lands in the single "projects"
section, so `max_bullets_per_section` (20) never fired on 17 project bullets spread across
5 projects, and a five-bullet project crowded out a two-bullet one.
"""

from __future__ import annotations

import pytest

from agentic_ai.config import settings
from agentic_ai.nodes.rewrite import _capped_per_project, _to_capped_draft
from agentic_ai.profile import Profile
from agentic_ai.state import DraftBullet, DraftOut


@pytest.fixture
def profile() -> Profile:
    return Profile.load()


def _bullets_of(profile: Profile, parent_id: str) -> list[str]:
    return [b.id for b in profile.bullets if b.parent_id == parent_id]


def _project_parents(profile: Profile) -> list[str]:
    experience_ids = {e["id"] for e in profile.raw.get("experience", [])}
    seen: list[str] = []
    for b in profile.bullets:
        if b.parent_id not in experience_ids and b.parent_id not in seen:
            seen.append(b.parent_id)
    return seen


def _draft_bullet(source_id: str, text: str = "Shipped a thing using a tool.") -> DraftBullet:
    return DraftBullet(text=text, source_bullet_id=source_id)


def test_a_project_is_trimmed_to_the_configured_cap(profile: Profile) -> None:
    parent = next(p for p in _project_parents(profile) if len(_bullets_of(profile, p)) > 2)
    ids = _bullets_of(profile, parent)

    kept = _capped_per_project([_draft_bullet(i) for i in ids], profile)

    assert len(kept) == settings.max_bullets_per_project
    assert [b.source_bullet_id for b in kept] == ids[: settings.max_bullets_per_project], (
        "the cap must keep the FIRST n in the model's emitted order — both prompts define "
        "that order as strongest and most JD-relevant first, so order is the selection"
    )


def test_the_cap_is_per_project_not_across_all_projects(profile: Profile) -> None:
    """The bug this exists to prevent: one long project eating the whole allowance."""
    parents = [p for p in _project_parents(profile) if len(_bullets_of(profile, p)) >= 2][:2]
    assert len(parents) == 2, "fixture needs two projects with 2+ bullets"

    items = [_draft_bullet(i) for p in parents for i in _bullets_of(profile, p)]
    kept = _capped_per_project(items, profile)

    for parent in parents:
        kept_here = [
            b
            for b in kept
            if (src := profile.by_id(b.source_bullet_id)) and src.parent_id == parent
        ]
        assert len(kept_here) == settings.max_bullets_per_project


def test_experience_bullets_are_never_trimmed(profile: Profile) -> None:
    """Explicit, because capping experience was the thing NOT asked for."""
    experience_ids = {e["id"] for e in profile.raw.get("experience", [])}
    exp_bullets = [b for b in profile.bullets if b.parent_id in experience_ids]
    biggest = max(experience_ids, key=lambda e: len(_bullets_of(profile, e)))
    assert len(_bullets_of(profile, biggest)) > settings.max_bullets_per_project, (
        "fixture needs a role with more bullets than the project cap, or this proves nothing"
    )

    out = DraftOut(
        profile_line="A line.",
        section_order=["experience", "projects"],
        bullets=[_draft_bullet(b.id) for b in exp_bullets],
        cover_letter="A letter.",
        highlighted_projects=[],
    )
    draft = _to_capped_draft(out, profile, ["experience", "projects"])

    assert len(draft.bullets.get("experience", [])) == len(exp_bullets)


def test_an_unknown_source_id_is_kept_so_validate_facts_can_report_it(profile: Profile) -> None:
    """Swallowing it here would hide the error the fact validator exists to raise."""
    items = [_draft_bullet("proj.does_not_exist.b9") for _ in range(5)]
    kept = _capped_per_project(items, profile)
    assert len(kept) == 5


def test_the_cap_is_a_setting_not_a_literal(profile: Profile, monkeypatch) -> None:
    """CLAUDE.md: thresholds are Settings fields. Proves the restore path documented in
    config.py (JOBPILOT_MAX_BULLETS_PER_PROJECT=20) actually restores every bullet."""
    parent = max(_project_parents(profile), key=lambda p: len(_bullets_of(profile, p)))
    ids = _bullets_of(profile, parent)

    monkeypatch.setattr(settings, "max_bullets_per_project", 20)
    assert len(_capped_per_project([_draft_bullet(i) for i in ids], profile)) == len(ids)

    monkeypatch.setattr(settings, "max_bullets_per_project", 1)
    assert len(_capped_per_project([_draft_bullet(i) for i in ids], profile)) == 1
