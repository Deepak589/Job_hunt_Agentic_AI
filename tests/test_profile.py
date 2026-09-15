"""Profile loader, metric expansion, and yaml integrity.

The metric tests are the load-bearing ones: Phase 2's fact validator asserts every number
in a generated bullet is in `Profile.all_metrics()`, so a false negative here is how a
fabricated figure reaches an employer.
"""

from __future__ import annotations

import pytest
from ruamel.yaml import YAML

from agentic_ai.profile import NUMERIC_RE, Profile, expand_metric_tokens


@pytest.fixture(scope="module")
def profile() -> Profile:
    return Profile.load()


# --------------------------------------------------------------------------- counts


def test_profile_counts(profile: Profile) -> None:
    """Guards against a silent yaml truncation or a mis-parsed section."""
    assert len(profile.skills) == 61
    assert len(profile.bullet_ids) == 20
    assert len(profile.tags()) == 102
    assert len(profile.bullets) == len(profile.bullet_ids), "duplicate bullet ids"


def test_bullets_carry_parent_and_evidence_text(profile: Profile) -> None:
    b = profile.by_id("proj.rag_pipeline.b1")
    assert b is not None
    assert b.parent_id == "proj.rag_pipeline"
    text = b.as_evidence_text()
    assert b.outcome in text and b.method in text
    assert "0.833" in text, "metric must be embedded alongside outcome and method"


def test_not_shipped_bullet_is_flagged(profile: Profile) -> None:
    """never_claim depends on this status surviving the load."""
    flagged = [b.id for b in profile.bullets if b.status == "not_shipped"]
    assert flagged == ["proj.ra_nutrition.b3"]


# ------------------------------------------------------------------- metric tokens


# Each of these is a substring of a real profile metric, which is exactly how the
# previous substring-based check let fabricated numbers through.
@pytest.mark.parametrize(
    "fake,hides_in",
    [
        ("9", "0.9"),
        ("40", "740"),
        ("13", "13,582"),
        ("0.85", "0.854"),
        ("54", "54.13"),
        ("0.83", "0.833"),
        ("100", "100+"),
        ("3.5", "3.5GB"),
        ("31", "31.97"),
    ],
)
def test_fabricated_number_rejected(profile: Profile, fake: str, hides_in: str) -> None:
    metrics = profile.all_metrics()
    assert hides_in in metrics, f"{hides_in!r} should be a real declared metric"
    assert fake not in metrics, f"{fake!r} must not be legitimised by {hides_in!r}"


@pytest.mark.parametrize(
    "real",
    ["0.833", "0.672", "0.854", "740", "13,582", "54.13", "31.97", "100+", "10x", "3.5GB"],
)
def test_real_number_accepted(profile: Profile, real: str) -> None:
    assert real in profile.all_metrics()


def test_percent_spelling_accepted_either_way(profile: Profile) -> None:
    """A bullet printing "60%" must match whether the figure is declared bare or suffixed."""
    metrics = profile.all_metrics()
    assert "60%" in metrics and "60" in metrics


def test_slash_joined_values_split(profile: Profile) -> None:
    """"alpha 0.3/0.5/0.7/0.9" must yield four tokens, not one mangled "0.3/0"."""
    tokens = expand_metric_tokens(["Recall@10 across alpha 0.3/0.5/0.7/0.9"])
    assert {"0.3", "0.5", "0.7", "0.9"} <= tokens
    assert "0.3/0" not in tokens
    assert {"0.3", "0.5", "0.7"} <= profile.all_metrics()


def test_thousands_separator_not_split() -> None:
    assert NUMERIC_RE.findall("13,582 images") == ["13,582"]
    assert NUMERIC_RE.findall("5, 6, 7") == ["5", "6", "7"], "trailing commas excluded"


def test_metrics_derive_from_bullets_not_the_cached_list(profile: Profile) -> None:
    """allowed_metrics is a render of the bullets; the bullets are the authority.

    The cached list is currently missing the alpha-sweep values, so a loader reading only
    that list would reject numbers the human-reviewed bullets actually state.
    """
    missing, _ = profile.stale_declared_metrics()
    assert missing, "fixture assumption: the cached list is known to be stale today"
    assert missing <= profile.all_metrics()


# -------------------------------------------------------------------- yaml integrity


def test_real_profile_is_clean(profile: Profile) -> None:
    assert profile.validate_profile() == []


def test_dangling_skill_evidence_is_caught(tmp_path, profile: Profile) -> None:
    """A typo in a skill's evidence list silently breaks retrieval — it must error."""
    yaml = YAML(typ="safe")
    raw = dict(profile.raw)
    raw["skills"] = {
        "languages": [{"name": "Python", "level": "expert", "evidence": ["exp.typo.b9"]}]
    }
    path = tmp_path / "broken.yaml"
    with path.open("w") as fh:
        yaml.dump(raw, fh)

    errors = Profile.load(path).validate_profile()
    assert len(errors) == 1
    assert "exp.typo.b9" in errors[0]


def test_unreviewed_profile_is_caught(tmp_path, profile: Profile) -> None:
    yaml = YAML(typ="safe")
    raw = dict(profile.raw)
    raw["meta"] = {**raw["meta"], "reviewed_by_human": False}
    path = tmp_path / "unreviewed.yaml"
    with path.open("w") as fh:
        yaml.dump(raw, fh)

    assert any("reviewed_by_human" in e for e in Profile.load(path).validate_profile())
