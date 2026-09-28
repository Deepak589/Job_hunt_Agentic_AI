"""_capped_bullets — structural overflow guard (max bullets/section, max chars/bullet)."""

from __future__ import annotations

from agentic_ai.config import settings
from agentic_ai.nodes.rewrite import _capped_bullets
from agentic_ai.state import DraftBullet


def test_extra_bullets_beyond_the_cap_are_dropped() -> None:
    items = [DraftBullet(text=f"Did thing {i}.", source_bullet_id="x") for i in range(settings.max_bullets_per_section + 3)]
    capped = _capped_bullets({"projects": items})
    assert len(capped["projects"]) == settings.max_bullets_per_section


def test_overlong_bullet_without_a_metric_is_truncated() -> None:
    long_text = "Built a thing. " * 30
    items = [DraftBullet(text=long_text, source_bullet_id="x")]
    capped = _capped_bullets({"projects": items})
    assert len(capped["projects"][0].text) <= settings.max_bullet_chars + 1


def test_overlong_bullet_is_not_truncated_if_it_would_cut_the_metric() -> None:
    metric = "0.833"
    long_text = "Built a thing. " * 25 + f"Improved recall by {metric}."
    items = [DraftBullet(text=long_text, metric=metric, source_bullet_id="x")]
    capped = _capped_bullets({"projects": items})
    assert capped["projects"][0].text == long_text
    assert metric in capped["projects"][0].text


def test_short_bullets_pass_through_unchanged() -> None:
    items = [DraftBullet(text="Short bullet.", metric=None, source_bullet_id="x")]
    capped = _capped_bullets({"projects": items})
    assert capped["projects"][0].text == "Short bullet."
