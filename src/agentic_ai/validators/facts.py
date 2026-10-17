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
   `evidence` list, its parent experience/project's `stack`, OR a word that genuinely
   appears in that bullet's own `outcome`/`method` text — not a profile-wide gazetteer, or
   a real skill evidenced by a different job would pass as evidence for this one. Prose
   with no citation (the cover letter) has no single bullet to scope to, so it is checked
   against profile-wide evidence instead (skills, stack, AND every bullet's own text).
   An UMBRELLA term ("LLMs", "Generative AI", "Agentic AI") is the one case where the
   term itself is absent from the profile while the work it names is present — the
   profile has LangChain, LangGraph and RAG, never a skill called "LLM". `aliases.py`
   maps each umbrella to the profile terms that back it, resolved against THIS SAME
   scope: the cited bullet's own evidence for a bullet, profile-wide for the cover
   letter. So a bullet built from the insurance migration still cannot say "LLM", and
   an umbrella whose backing work leaves the profile stops passing on its own.
   Only HARD requirement keywords are checked here — a SOFT nice-to-have is exactly the
   kind of generic, non-committal term ("data", "learning", "analytical") an extractor
   tags loosely; flagging it at the same severity as a hard-requirement fabrication
   produced false positives in live testing (2026-09-15) with no real fabrication behind
   them. DISQUALIFIER keywords are excluded entirely (not filtered by word here) — by the
   time a draft exists, `hard_gap_gate` already proved every disqualifier true or sent the
   job to skip, so re-checking them for fabrication is redundant by construction. The
   caller (`nodes/validate_facts.py`) filters by type before calling this function.

   In the COVER LETTER only, a sentence that states the absence of a term ("I haven't
   worked with FAISS") is not a claim of it — see `_disclaimed`. The project's own CV rules
   require naming a gap directly, and without this the validator punished the model for
   obeying them. Bullets get no such exemption: a bullet is a positive claim about a cited
   accomplishment, so it has nothing to disclaim.

A fourth check runs over the whole draft (bullets + cover letter): an unclaimable-fluency
language claim, reusing the exact language-fluency logic `coverage.py` already built for
the JD-side gate. Broader `never_claim` enforcement (arbitrary prose claims) is not
attempted here — see the "Still open" note this task leaves in tasks/todo.md.
"""

from __future__ import annotations

import re

from ..coverage import _eligibility_terms, _normalize, location_terms
from ..profile import NUMERIC_RE, Profile
from ..state import Draft
from .aliases import is_evidenced


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


def _bullet_text_tokens(b) -> set[str]:
    """Every word actually appearing in this bullet's own outcome/method text.

    Widens "evidence" past skill/stack names to the bullet's real description — a JD
    keyword the model echoes ("data", "analysis") is not a fabrication if that word is
    literally what this accomplishment already says. Word-level, not phrase-level: this
    only helps single-word terms, which is exactly the class of false positive it fixes.
    """
    return set(_normalize(f"{b.outcome} {b.method}").split())


def _local_tech(profile: Profile, source_bullet_id: str) -> set[str]:
    """Tech evidenced by this specific real bullet — not the whole profile.

    Union of skill names whose `evidence` names this bullet, its parent
    experience/project's `stack` list, and every word in the bullet's own outcome/method
    text — normalized so it compares like-for-like with a normalized JD term. A bogus
    `source_bullet_id` (caught separately) evidences nothing, which is the right answer
    here too.
    """
    tech = {_normalize(s.name) for s in profile.skills if source_bullet_id in s.evidence}
    b = profile.by_id(source_bullet_id)
    if b:
        tech |= _bullet_text_tokens(b)
        for section in ("experience", "projects"):
            for entry in profile.raw.get(section, []):
                if entry["id"] == b.parent_id:
                    tech |= {_normalize(item) for item in entry.get("stack", [])}
    return tech


def _unclaimable_language_error(draft: Draft, profile: Profile) -> list[str]:
    """Language claims a draft may not make, checked per sentence and by LEVEL.

    The old version errored whenever the word "german" appeared anywhere in the draft. That
    made the sentence the CV rules explicitly ask for — name the gap directly, "A2
    completed, working toward B1" — impossible to write, while a genuinely false "fluent in
    German" was caught by the same blunt rule for the wrong reason. It also reported every
    hit as "never_claim forbids this" when the source was the language level, not
    `never_claim` (which is currently empty).
    """
    from ..languages import claim_error

    raw = profile.raw
    errors: list[str] = []
    for loc, text in _draft_text(draft):
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
            if not sentence.strip():
                continue
            err = claim_error(sentence, raw)
            if err:
                errors.append(f"{loc}: {err}")
    return errors


# Explicit statements of ABSENCE. Deliberately narrow: every cue here names not-having, so
# none of them can be produced by ordinary positive prose. Bare "no" and "not" are absent on
# purpose — "Built the pipeline, not the prototype, on FAISS" would exempt a real fabrication.
_ABSENCE_CUES = re.compile(
    r"\b(?:"
    r"hav(?:e|ing)\s+not|haven'?t|has\s+not|hasn'?t|had\s+not|hadn'?t|"
    r"not\s+(?:yet\s+)?(?:worked|used|built|shipped|touched|run|deployed)|"
    r"never\s+(?:worked|used|built|shipped|touched|run|deployed)|"
    r"no\s+(?:direct\s+|hands-on\s+|production\s+|prior\s+|professional\s+)?experience|"
    r"yet\s+to|unfamiliar\s+with|new\s+to|without|lacks?|lacking|outside\s+my"
    r")\b",
    re.IGNORECASE,
)


def _disclaimed(sentence: str, term: str) -> bool:
    """True if this sentence states the ABSENCE of `term` rather than claiming it.

    The CV rules require the cover letter to name a gap directly — `prompts/rewrite.md` §5
    and `prompts/agent_cv.md` both say to state it plainly and not dodge. `_contains_term`
    is a bare whole-word regex, so it read the model obeying that instruction as the
    fabrication it is meant to catch: "I haven't worked with LlamaIndex, FAISS, MongoDB,
    PyMuPDF, Docling or BeautifulSoup specifically" scored one unevidenced-tech error per
    name. Two project rules in direct conflict, and the honest letter lost.

    Scoped to one sentence, and the cue must sit BEFORE the term — a disclaimer in the
    previous sentence cannot launder a claim in this one. Cover letter only: a bullet is a
    positive claim about a cited accomplishment by construction, so a bullet has no business
    disclaiming anything, and the bullet-scoped check stays as strict as it was.
    """
    m = re.search(rf"(?<![a-z0-9]){re.escape(term)}s?(?![a-z0-9])", sentence, re.IGNORECASE)
    if not m:
        return False
    return _ABSENCE_CUES.search(sentence[: m.start()]) is not None


def _claimed_in_prose(text: str, term: str) -> bool:
    """`_contains_term`, but a sentence that states the term's absence does not count.

    A term claimed in ANY sentence still errors — one honest disclaimer elsewhere does not
    license a claim.
    """
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        if not sentence.strip():
            continue
        if _contains_term(sentence, term) and not _disclaimed(sentence, term):
            return True
    return False


def validate_facts(draft: Draft, profile: Profile, jd_keywords: list[str] = ()) -> list[str]:
    errors: list[str] = []
    bullet_ids = profile.bullet_ids
    allowed_metrics = profile.all_metrics()
    allowed_tech = {_normalize(t) for t in profile.all_tech() | profile.all_stack()}
    for b in profile.bullets:
        allowed_tech |= _bullet_text_tokens(b)
    # profile_summary (core + proof_points) is free-text evidence too, not tied to any
    # one bullet id — without it, a word the profile genuinely states there (e.g. "cloud"
    # in "owned the Kubernetes, CI/CD and AWS layer of a cloud project") reads as an
    # unevidenced JD term the cover letter must not have said.
    summary = profile.raw.get("profile_summary", {})
    summary_text = str(summary.get("core", "")) + " " + " ".join(summary.get("proof_points", []))
    allowed_tech |= set(_normalize(summary_text).split())
    # Eligibility/location keywords ("germany", "enrolled", "data science") are true facts
    # the profile itself states, not skills that could be fabricated — kept as a defensive
    # exemption for the rare hard requirement phrased as an eligibility fact (e.g. "must
    # be EU-eligible" tagged hard). Disqualifier keywords never reach this function at all
    # (see module docstring), so this set is no longer the primary defense it once was.
    not_fabricatable = location_terms() | {_normalize(t) for t in _eligibility_terms(profile.raw)}
    # Languages the profile records are stated facts, not fabricable tech. "english" was
    # being reported as an unevidenced JD term while the profile lists English as Fluent.
    # The LEVEL claim is policed separately by `_unclaimable_language_error`, so naming a
    # language here is safe; overclaiming it is not.
    from ..languages import LANG_ALIASES, profile_languages

    for rec in profile_languages(profile.raw):
        key = str(rec.get("name", "")).strip().lower()
        not_fabricatable |= {_normalize(a) for a in LANG_ALIASES.get(key, (key,))} | {_normalize(key)}

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
                # 2-char keywords are too weak a signal here: eligibility disqualifiers
                # like "Computer Science, AI, Data Science, IT" emit "ai"/"it" as literal
                # keywords, and "it" collides with the ordinary pronoun in any sentence.
                if not k or len(k) <= 2 or k in not_fabricatable or is_evidenced(k, local_tech):
                    continue
                if _contains_term(b.text, k):
                    errors.append(f"{section}: claims unevidenced tech {kw!r} (JD term, not in profile)")

    if draft.cover_letter.strip():
        for kw in jd_keywords:
            k = _normalize(kw)
            if not k or len(k) <= 2 or k in not_fabricatable or is_evidenced(k, allowed_tech):
                continue
            if _claimed_in_prose(draft.cover_letter, k):
                errors.append(f"cover_letter: claims unevidenced tech {kw!r} (JD term, not in profile)")

    errors.extend(_unclaimable_language_error(draft, profile))
    return errors
