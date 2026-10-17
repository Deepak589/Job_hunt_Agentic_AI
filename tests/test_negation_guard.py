"""Naming a gap is not claiming it — and claiming it is still caught.

`prompts/rewrite.md` §5 and `prompts/agent_cv.md` both require the cover letter to name a
missing hard requirement directly rather than dodge it. `validate_facts` check 3 used a bare
whole-word regex over the whole letter, so it read the model obeying that instruction as the
fabrication it exists to catch. On the railway-AI posting (2026-10-17) the sentence

    "I haven't worked with LlamaIndex, FAISS, MongoDB, PyMuPDF, Docling or BeautifulSoup
     specifically — my retrieval and document work has gone through Chroma/pgvector and
     LangChain-native loaders instead"

scored one unevidenced-tech error per name: five blocking errors on a true sentence, enough
to burn both rewrite retries and end at `log_fact_failure`. It only went unnoticed because
`hard_gap_gate` skipped that job before the validator ran.

These tests pin the narrow exemption and, more importantly, its limits — a guard that
exempts too much turns the project's highest-value gate into decoration.
"""

from __future__ import annotations

import pytest

from agentic_ai.profile import Profile
from agentic_ai.state import Draft, DraftBullet
from agentic_ai.validators.facts import validate_facts

GAP_TERMS = ["faiss", "mongodb", "docling", "pymupdf", "beautifulsoup"]
REAL_BULLET = "exp.valuemomentum.b1"


@pytest.fixture(scope="module")
def profile() -> Profile:
    return Profile.load()


def _letter(text: str) -> Draft:
    return Draft(
        profile_line="x",
        section_order=["projects"],
        bullets={"projects": [DraftBullet(text="Migrated policy data.", metric=None, source_bullet_id=REAL_BULLET)]},
        cover_letter=text,
    )


def _bullet(text: str) -> Draft:
    return Draft(
        profile_line="x",
        section_order=["projects"],
        bullets={"projects": [DraftBullet(text=text, metric=None, source_bullet_id=REAL_BULLET)]},
        cover_letter="",
    )


def test_the_exact_sentence_from_the_live_run_passes(profile: Profile) -> None:
    draft = _letter(
        "I haven't worked with LlamaIndex, FAISS, MongoDB, PyMuPDF, Docling or BeautifulSoup "
        "specifically — my retrieval and document work has gone through Chroma/pgvector and "
        "LangChain-native loaders instead, and I pick up new tooling quickly."
    )
    assert [e for e in validate_facts(draft, profile, jd_keywords=GAP_TERMS) if "unevidenced" in e] == []


@pytest.mark.parametrize(
    "sentence",
    [
        "I have not used FAISS in production.",
        "I haven't used FAISS yet.",
        "I have no hands-on experience with FAISS.",
        "I am yet to use FAISS.",
        "I am unfamiliar with FAISS.",
        "I am new to FAISS.",
        "I shipped the retriever without FAISS.",
        "My profile lacks FAISS.",
    ],
)
def test_explicit_absence_phrasings_are_not_claims(profile: Profile, sentence: str) -> None:
    assert [e for e in validate_facts(_letter(sentence), profile, jd_keywords=["faiss"]) if "unevidenced" in e] == []


def test_a_claim_in_another_sentence_is_still_caught(profile: Profile) -> None:
    """The limit that makes the exemption safe: one honest disclaimer does not launder a
    claim elsewhere in the same letter."""
    draft = _letter("I haven't worked with Docling. I built the retrieval layer on FAISS and shipped it.")
    errors = validate_facts(draft, profile, jd_keywords=GAP_TERMS)

    assert any("faiss" in e.lower() for e in errors)
    assert not any("docling" in e.lower() for e in errors)


def test_a_cue_after_the_term_does_not_exempt_it(profile: Profile) -> None:
    """The cue must precede the term. Otherwise "Built it on FAISS, without which..." would
    exempt itself."""
    draft = _letter("Built the index on FAISS, without which the recall work would not have shipped.")
    assert any("faiss" in e.lower() for e in validate_facts(draft, profile, jd_keywords=["faiss"]))


def test_bare_not_does_not_exempt_a_claim(profile: Profile) -> None:
    """"no"/"not" alone are deliberately NOT cues — ordinary positive prose contains them,
    and treating them as disclaimers would gut the check."""
    draft = _letter("Built the pipeline, not the prototype, on FAISS.")
    assert any("faiss" in e.lower() for e in validate_facts(draft, profile, jd_keywords=["faiss"]))


def test_bullets_get_no_exemption(profile: Profile) -> None:
    """A bullet is a positive claim about a cited accomplishment, so it has nothing to
    disclaim — the bullet-scoped check stays exactly as strict as before."""
    errors = validate_facts(_bullet("Built it without FAISS and without MongoDB."), profile, jd_keywords=GAP_TERMS)

    assert any("faiss" in e.lower() for e in errors)
    assert any("mongodb" in e.lower() for e in errors)


def test_an_unqualified_claim_is_unaffected(profile: Profile) -> None:
    draft = _letter("I have deep production experience with FAISS and MongoDB.")
    errors = validate_facts(draft, profile, jd_keywords=GAP_TERMS)

    assert any("faiss" in e.lower() for e in errors)
    assert any("mongodb" in e.lower() for e in errors)
