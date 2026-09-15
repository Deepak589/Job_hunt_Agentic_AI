"""validate_facts — the highest-value guardrail (plan.md §8). Deterministic, no LLM.

Three checks, each catching a distinct way a rewrite can lie:

1. Every bullet cites a real profile bullet (`source_bullet_id`) — this is what makes the
   other two checks possible, and on its own it also settles the "employer/title/dates not
   in real history" gate from plan.md §6: a bullet cannot claim work at a company that
   isn't the real parent of a real cited bullet.
2. Every numeric token printed in a bullet is in `Profile.all_metrics()` — exact match,
   not substring (Phase 1's P1 bug: a fabricated "87" must not pass because "8" or "0.87"
   is real).
3. A JD keyword planted verbatim in a bullet, that the CITED SOURCE BULLET has no evidence
   for, is the single most likely hallucination (the model sees "Kubernetes" in the JD and
   wants to please, and Kubernetes being a real skill elsewhere on the CV does not make it
   true of *this* accomplishment). Scoped to the cited bullet's own evidence — a skill's
   `evidence` list, or its parent experience/project's `stack` — not a profile-wide
   gazetteer, or a real skill evidenced by a different job would pass as evidence for this
   one. Prose with no citation (the cover letter) has no single bullet to scope to, so it
   is checked against profile-wide evidence instead.

A fourth check runs over the whole draft (bullets + cover letter): an unclaimable-fluency
language claim, reusing the exact language-fluency logic `coverage.py` already built for
the JD-side gate. Broader `never_claim` enforcement (arbitrary prose claims) is not
attempted here — see the "Still open" note this task leaves in tasks/todo.md.
"""

from __future__ import annotations

import re

from ..coverage import FLUENT_LEVELS, _normalize
from ..profile import NUMERIC_RE, Profile
from ..state import Draft


def _draft_text(draft: Draft) -> list[tuple[str, str]]:
    """(location, text) for every piece of prose in the draft — bullets and cover letter."""
    out = [(f"{section}[{i}]", b.text) for section, bullets in draft.bullets.items() for i, b in enumerate(bullets)]
    if draft.cover_letter.strip():
        out.append(("cover_letter", draft.cover_letter))
    return out


def _contains_term(text: str, term: str) -> bool:
    """Whole-word match of an already-`_normalize`d term against RAW prose.

    `term` comes in singular-folded ("kubernetes" -> "kubernete", `coverage._singular`)
    and must not be compared against `_normalize(text)`: `_normalize` keeps "." (for
    decimals), so a sentence-final "Kubernetes." normalizes to the single, unfolded
    token "kubernetes." (endswith(".") skips the fold) — a folded term then matches
    nothing, ever, for any bullet ending in punctuation. Matching the folded term
    against the raw text instead, with an optional trailing "s" standing in for the one
    `_singular` may have stripped, sidesteps that without re-deriving fold rules here.
    """
    pattern = re.compile(rf"(?<![a-z0-9]){re.escape(term)}s?(?![a-z0-9])", re.IGNORECASE)
    return bool(pattern.search(text))


def _local_tech(profile: Profile, source_bullet_id: str) -> set[str]:
    """Tech evidenced by this specific real bullet — not the whole profile.

    Union of skill names whose `evidence` names this bullet and its parent
    experience/project's `stack` list, normalized so it compares like-for-like with a
    normalized JD term. A bogus `source_bullet_id` (caught separately) evidences
    nothing, which is the right answer here too.
    """
    tech = {_normalize(s.name) for s in profile.skills if source_bullet_id in s.evidence}
    b = profile.by_id(source_bullet_id)
    if b:
        for section in ("experience", "projects"):
            for entry in profile.raw.get(section, []):
                if entry["id"] == b.parent_id:
                    tech |= {_normalize(item) for item in entry.get("stack", [])}
    return tech


def _unclaimable_language_error(draft: Draft, profile: Profile) -> list[str]:
    langs = profile.raw.get("constraints", {}).get("languages", [])
    unclaimable = {
        _normalize(lang["name"])
        for lang in langs
        if not any(lvl in str(lang.get("level", "")).lower() for lvl in FLUENT_LEVELS)
    }
    if not unclaimable:
        return []
    errors = []
    for loc, text in _draft_text(draft):
        words = set(_normalize(text).split())
        for lang in unclaimable & words:
            errors.append(f"{loc}: claims fluency in {lang} — never_claim forbids this")
    return errors


def validate_facts(draft: Draft, profile: Profile, jd_keywords: list[str] = ()) -> list[str]:
    errors: list[str] = []
    bullet_ids = profile.bullet_ids
    allowed_metrics = profile.all_metrics()
    allowed_tech = {_normalize(t) for t in profile.all_tech() | profile.all_stack()}

    for section, bullets in draft.bullets.items():
        for b in bullets:
            if b.source_bullet_id not in bullet_ids:
                errors.append(f"{section}: bullet cites unknown source {b.source_bullet_id!r}")

            for num in NUMERIC_RE.findall(b.text):
                if num not in allowed_metrics and num.rstrip("%") not in allowed_metrics:
                    errors.append(f"{section}: unverified number {num!r} in: {b.text[:60]}")

            local_tech = _local_tech(profile, b.source_bullet_id)
            for kw in jd_keywords:
                k = _normalize(kw)
                if not k or k in local_tech:
                    continue
                if _contains_term(b.text, k):
                    errors.append(f"{section}: claims unevidenced tech {kw!r} (JD term, not in profile)")

    if draft.cover_letter.strip():
        for kw in jd_keywords:
            k = _normalize(kw)
            if not k or k in allowed_tech:
                continue
            if _contains_term(draft.cover_letter, k):
                errors.append(f"cover_letter: claims unevidenced tech {kw!r} (JD term, not in profile)")

    errors.extend(_unclaimable_language_error(draft, profile))
    return errors
