"""CEFR level comparison — the rail that replaced "is this language below fluency".

The old binary test failed in both directions at once: it skipped a posting asking for
Grundkenntnisse from a candidate who had more than that, and it flagged the honest
sentence "German at A2, working toward B1" as a fabricated fluency claim because it only
looked for the word "german".
"""

from __future__ import annotations

import pytest

from agentic_ai.languages import (
    claim_error,
    demanded_level,
    language_verdict,
    level_index,
    mentions_language,
)

RAW = {
    "constraints": {
        "languages": [
            {"name": "English", "level": "Fluent"},
            {"name": "German", "level": "A2", "in_progress": "B1"},
        ]
    }
}


@pytest.mark.parametrize(
    "text,expected",
    [
        # Ordering matters: "sehr gute" must not resolve to the b1 its substring "gute" hits.
        ("Sehr gute Deutschkenntnisse", "b2"),
        ("Du bringst gute Deutsch- und Englischkenntnisse mit", "b1"),
        ("Grundkenntnisse Deutsch von Vorteil", "a2"),
        ("Verhandlungssichere Deutschkenntnisse", "c1"),
        ("Muttersprachliches Deutsch", "c2"),
        # One demand, three inflections — plain substring "fluent" missed "Fluency".
        ("Fluency in English and German is required", "c1"),
        ("speaks German fluently", "c1"),
        ("fluent German", "c1"),
        # No level named at all.
        ("German required", None),
        ("Strong Python skills", None),
    ],
)
def test_demanded_level(text: str, expected: str | None) -> None:
    assert demanded_level(text) == expected


@pytest.mark.parametrize(
    "text,lang,expected",
    [
        # The country is not the language. "authorized to work in Germany" was being read
        # as a German-language demand, which made a work-authorization clause uncoverable.
        ("You must be legally authorized to work in Germany", "German", False),
        ("Berlin, Germany", "German", False),
        ("Sitz in Deutschland", "German", False),
        ("Gute Deutschkenntnisse", "German", True),
        ("German-speaking team", "German", True),
        ("Englischkenntnisse erforderlich", "English", True),
    ],
)
def test_mentions_language_excludes_the_country(text: str, lang: str, expected: bool) -> None:
    assert mentions_language(text, lang) is expected


def test_level_index_accepts_codes_and_labels() -> None:
    assert level_index("A2") == 1
    assert level_index("Fluent") == level_index("c1")
    assert level_index("Native") == level_index("c2")
    assert level_index(None) == -1
    assert level_index("nonsense") == -1


@pytest.mark.parametrize(
    "text,expected",
    [
        # At or below the held level: no gap.
        ("Grundkenntnisse Deutsch", "met"),
        ("Fluent English required", "met"),
        # One step above A2 with B1 in progress: a review question, never a skip. This is
        # the case the old rail turned into a hard disqualifier.
        ("Du bringst gute Deutsch- und Englischkenntnisse mit", "unknown"),
        # Two steps and beyond: a real gap.
        ("Sehr gute Deutschkenntnisse", "unmet"),
        ("Verhandlungssicheres Deutsch", "unmet"),
        # The weakest language in a compound requirement decides, so an any-match on
        # "english" can no longer clear a German-mandatory posting.
        ("Fluency in English and German is required", "unmet"),
        # Unqualified mention: cannot be assumed satisfied below C1.
        ("German required", "unknown"),
        # Not a language requirement.
        ("Strong Python skills", None),
    ],
)
def test_language_verdict(text: str, expected: str | None) -> None:
    assert language_verdict(text, RAW, max_gap_steps=1) == expected


def test_shipped_default_never_skips_on_language() -> None:
    """Policy: a German demand must never be a skip, at any level the posting asks for.

    Both `uncovered_hard` and `unmet_disqualifiers` exclude `unknown`, so an `unknown`
    language requirement reaches human review and the cover letter states the honest
    level. `unmet` is the only verdict that would skip, and the shipped ceiling makes it
    unreachable for a language.
    """
    from agentic_ai.config import settings

    for text in (
        "Grundkenntnisse Deutsch",
        "Gute Deutschkenntnisse",
        "Sehr gute Deutschkenntnisse",
        "Verhandlungssicheres Deutsch",
        "Muttersprachliches Deutsch erforderlich",
        "Fluency in English and German is required",
        "German required",
    ):
        verdict = language_verdict(text, RAW, settings.max_language_gap_steps)
        assert verdict in {"met", "unknown"}, f"{text!r} would skip on language ({verdict})"


def test_gap_ceiling_is_configurable() -> None:
    """With no tolerance, the same B1 demand becomes a skip."""
    text = "Gute Deutschkenntnisse"
    assert language_verdict(text, RAW, max_gap_steps=1) == "unknown"
    assert language_verdict(text, RAW, max_gap_steps=0) == "unmet"


@pytest.mark.parametrize(
    "sentence",
    [
        "My German is at A2 and I am currently working toward B1.",
        "I have completed A2 German and am learning B1.",
        "I work in English; my German is A2, improving toward B1.",
        "I am improving my German.",  # names no level — nothing to fabricate
        "I speak English fluently.",  # held at Fluent
        "I am authorized to work in Germany.",  # the country, not a claim
    ],
)
def test_honest_language_statements_pass(sentence: str) -> None:
    assert claim_error(sentence, RAW) is None


@pytest.mark.parametrize(
    "sentence",
    [
        "I am fluent in German.",
        "Verhandlungssicheres Deutsch bringe ich mit.",
        "I am learning C1 German.",  # in progress, but not the recorded in_progress level
        "My German is B2.",
    ],
)
def test_overclaimed_language_is_an_error(sentence: str) -> None:
    err = claim_error(sentence, RAW)
    assert err is not None and "German" in err


def test_in_progress_claim_needs_the_in_progress_framing() -> None:
    """B1 is claimable only as in progress — a flat "my German is B1" is still an overclaim."""
    assert claim_error("I have completed A2, working toward B1.", RAW) is None
    assert claim_error("My German is B1.", RAW) is not None
