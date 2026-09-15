"""Coverage scoring and the hard-gap gate.

This is the step that saves money, so it is tested explicitly rather than incidentally.
Every test here is offline: no embedding model, no network, no LLM. Similarities are set
by hand so the gate's behaviour is separable from retrieval quality.
"""

from __future__ import annotations

import pytest

from agentic_ai.config import settings
from agentic_ai.coverage import gate_reason, hard_gap_gate, keyword_set, score_coverage
from agentic_ai.state import Evidence, Job, JobState, Requirement

BELOW = settings.sem_threshold - 0.05
ABOVE = settings.sem_threshold + 0.05


def req(
    text: str,
    type: str = "hard",
    keywords: list[str] | None = None,
    similarity: float = 0.0,
) -> Requirement:
    ev = [Evidence(source="cv_bullet", source_id="exp.fixture.b1", text="x", similarity=similarity)]
    return Requirement(text=text, type=type, keywords=keywords or [], evidence=ev)


def state(*reqs: Requirement, employment_type: str = "werkstudent", location: str = "") -> JobState:
    job = Job(
        id="t", source="manual", title="Test Role", jd_text="...",
        employment_type=employment_type, location=location,
    )
    return JobState(job=job, requirements=list(reqs))


def scored(*reqs: Requirement, employment_type: str = "werkstudent", location: str = "") -> JobState:
    s = state(*reqs, employment_type=employment_type, location=location)
    return s.model_copy(update=score_coverage(s))


# ------------------------------------------------------------------- the two signals


def test_keyword_signal_covers_without_semantic_match() -> None:
    s = scored(req("Experience with Docker", keywords=["docker"], similarity=BELOW))
    assert s.requirements[0].covered
    assert s.requirements[0].covered_by == "keyword"


def test_semantic_signal_covers_without_keyword_match() -> None:
    s = scored(req("Build search over internal documents", keywords=["nothing"], similarity=ABOVE))
    assert s.requirements[0].covered
    assert s.requirements[0].covered_by == "semantic"


def test_neither_signal_means_uncovered() -> None:
    s = scored(req("5 years of Rust", keywords=["rust"], similarity=BELOW))
    assert not s.requirements[0].covered
    assert s.requirements[0].covered_by is None


def test_keyword_match_is_whole_token_not_substring() -> None:
    """"java" must not be satisfied by "javascript" and vice versa.

    Substring matching here would be the same defect class as the old metric check.
    """
    terms = keyword_set()
    assert "javascript" not in terms
    s = scored(req("Frontend in JavaScript", keywords=["javascript"], similarity=BELOW))
    assert not s.requirements[0].covered


def test_multi_word_keyword_needs_every_word_known() -> None:
    s = scored(req("CI/CD with GitHub Actions", keywords=["github actions"], similarity=BELOW))
    assert s.requirements[0].covered, "both words are known profile terms"
    s2 = scored(req("Apache Spark pipelines", keywords=["apache spark"], similarity=BELOW))
    assert not s2.requirements[0].covered


# ----------------------------------------------------------------------- the scores


def test_coverage_fractions_are_computed_not_asserted() -> None:
    s = scored(
        req("Python", keywords=["python"], similarity=BELOW),
        req("Rust", keywords=["rust"], similarity=BELOW),
        req("Nice: fintech", type="soft", keywords=["fintech"], similarity=BELOW),
    )
    assert s.scores.hard_coverage == 0.5
    assert s.scores.soft_coverage == 0.0


def test_no_requirements_of_a_type_scores_one() -> None:
    """Absence of soft requirements is not a soft-coverage failure."""
    s = scored(req("Python", keywords=["python"], similarity=BELOW))
    assert s.scores.soft_coverage == 1.0


# ------------------------------------------------------------------------- the gate


def test_single_hard_gap_proceeds() -> None:
    """MAX_BLOCKING_HARD_GAPS starts at 1 — one gap is a rewrite problem, not a skip."""
    s = scored(
        req("Python", keywords=["python"], similarity=BELOW),
        req("Rust", keywords=["rust"], similarity=BELOW),
    )
    assert hard_gap_gate(s) == "proceed"
    assert gate_reason(s) is None


def test_two_hard_gaps_skip_and_name_them() -> None:
    s = scored(
        req("4+ years of Rust", keywords=["rust"], similarity=BELOW),
        req("Scala data platform", keywords=["scala"], similarity=BELOW),
        req("Python", keywords=["python"], similarity=BELOW),
    )
    assert hard_gap_gate(s) == "skip"
    reason = gate_reason(s)
    assert "Rust" in reason and "Scala" in reason
    assert "Python" not in reason, "covered requirements must not appear in the skip reason"


def test_german_fluency_disqualifier_skips() -> None:
    """The profile lists German at A1/A2 and never_claim forbids claiming fluency.

    Regression guard: an earlier keyword set added every listed language regardless of
    level, so "german" matched and a German-required job passed the gate.
    """
    s = scored(req("Fluent German (C1)", type="disqualifier", keywords=["german", "c1", "fluent german"], similarity=BELOW))
    assert hard_gap_gate(s) == "skip"
    assert "German" in gate_reason(s)


def test_met_disqualifier_does_not_skip() -> None:
    """"Authorized to work in Germany" is answered by the profile's own constraints."""
    s = scored(
        req(
            "You must be legally authorized to work in Germany",
            type="disqualifier",
            keywords=["germany", "work authorization"],
            similarity=BELOW,
        )
    )
    assert s.requirements[0].covered
    assert hard_gap_gate(s) == "proceed"


def test_disqualifier_skips_even_when_everything_else_is_covered() -> None:
    s = scored(
        req("Python", keywords=["python"], similarity=ABOVE),
        req("SQL", keywords=["sql"], similarity=ABOVE),
        req("Fluent German (C1)", type="disqualifier", keywords=["german"], similarity=BELOW),
    )
    assert s.scores.hard_coverage == 1.0
    assert hard_gap_gate(s) == "skip", "a disqualifier is categorical, not outweighed by coverage"


@pytest.mark.parametrize("n_gaps,expected", [(0, "proceed"), (1, "proceed"), (2, "skip"), (5, "skip")])
def test_gate_respects_max_blocking_hard_gaps(n_gaps: int, expected: str) -> None:
    reqs = [req(f"Unknown tech {i}", keywords=[f"unobtainium{i}"], similarity=BELOW) for i in range(n_gaps)]
    reqs.append(req("Python", keywords=["python"], similarity=BELOW))
    assert hard_gap_gate(scored(*reqs)) == expected


# ------------------------------------------------- eligibility regressions (real JDs)


def test_enrollment_disqualifier_is_met() -> None:
    """Regression: a Werkstudent JD skipped at 100% coverage.

    "Currently enrolled in a Master's programme" is a real gating condition the profile
    satisfies (edu.msc, status in_progress), but the enrollment phrasings were absent from
    the keyword set, so the gate read it as unmet and skipped an applicable job. The
    false-skip direction is the expensive one: it silently drops jobs he can get.
    """
    s = scored(
        req(
            "Currently enrolled in a Master's programme in Computer Science, Data Science or similar",
            type="disqualifier",
            keywords=["enrolled", "master's programme", "data science", "computer science"],
            similarity=BELOW,
        )
    )
    assert s.requirements[0].covered
    assert hard_gap_gate(s) == "proceed"


def test_hours_disqualifier_is_met_from_profile_constraints() -> None:
    """A stated hours limit at or below `constraints.max_hours_per_week_in_term` is met."""
    s = scored(
        req(
            "You are available 20 hours per week during the semester",
            type="disqualifier",
            keywords=["20 hours per week", "20 hours"],
            similarity=BELOW,
        )
    )
    assert s.requirements[0].covered


def test_degree_terms_do_not_leak_as_loose_words() -> None:
    """Eligibility terms are phrase-level on purpose.

    Splitting "MSc Data Science" and "Computer Science & Engineering" into words would put
    "data", "science" and "engineering" in the set individually, and the multi-word rule
    would then read "data engineering" — a role he has no evidence for — as covered.
    """
    terms = keyword_set()
    assert "data science" in terms and "computer science" in terms
    assert "data" not in terms and "science" not in terms and "engineering" not in terms
    s = scored(req("Data engineering pipelines", keywords=["data engineering"], similarity=BELOW))
    assert not s.requirements[0].covered


def test_keyword_coverage_records_the_matched_term() -> None:
    """The report shows the matched term as the evidence, so it has to be recorded.

    Without it the row displayed the nearest bullet by similarity, implying a bullet
    evidenced the requirement when the match was purely lexical.
    """
    s = scored(req("Familiarity with Docker", keywords=["docker"], similarity=BELOW))
    r = s.requirements[0]
    assert r.covered_by == "keyword" and r.matched_term == "docker"
    semantic = scored(req("Something paraphrased", keywords=[], similarity=ABOVE)).requirements[0]
    assert semantic.covered_by == "semantic" and semantic.matched_term == ""


def test_hours_above_the_cap_is_not_covered() -> None:
    """A 40h/week JD is not answerable by a 24h cap — and the bare phrase must not rescue it.

    "hours per week" used to sit in the keyword set unconditionally, so any hours
    requirement matched regardless of the number. False-proceed on a full-time JD.
    """
    s = scored(
        req(
            "You are available 40 hours per week",
            type="disqualifier",
            keywords=["40 hours per week", "40 hours", "hours per week", "full-time"],
            similarity=BELOW,
        )
    )
    assert not s.requirements[0].covered
    assert hard_gap_gate(s) == "skip"


def test_full_time_search_lifts_the_hours_cap() -> None:
    """`--full-time` sets employment_type=fulltime; the 24h werkstudent cap stops binding."""
    r = req(
        "You are available 40 hours per week",
        type="disqualifier",
        keywords=["40 hours per week", "hours per week", "full-time"],
        similarity=BELOW,
    )
    assert not scored(r).requirements[0].covered
    assert scored(r, employment_type="fulltime").requirements[0].covered


def test_compound_language_requirement_is_not_covered_by_the_claimable_half() -> None:
    """Bain: "Fluency in English and German is required" passed on the keyword `english`.

    One requirement, two languages, any-match keyword logic — a German-mandatory job
    cleared the gate. A named language below fluency blocks the whole requirement.
    """
    s = scored(
        req(
            "Fluency in English and German is required",
            type="disqualifier",
            keywords=["english", "german", "fluency"],
            similarity=BELOW,
        )
    )
    assert not s.requirements[0].covered
    assert hard_gap_gate(s) == "skip"


def test_english_only_requirement_still_covered() -> None:
    """The block must not swallow the language he does have."""
    s = scored(req("Fluent in English", type="disqualifier", keywords=["english"], similarity=BELOW))
    assert s.requirements[0].covered
    assert hard_gap_gate(s) == "proceed"


def test_unclaimable_language_blocks_a_high_semantic_score_too() -> None:
    """Paraphrase cannot establish fluency either — the block precedes both signals."""
    s = scored(req("Sehr gute Deutschkenntnisse / good German skills", keywords=[], similarity=ABOVE))
    assert not s.requirements[0].covered


def test_filler_words_do_not_sink_an_exact_keyword() -> None:
    """Retorio emitted `fluent in english`, never a bare `english`.

    The all-words rule then failed on `fluent` and `in`, and a disqualifier he MEETS
    routed the job to skip. Grading and glue words name nothing; they are dropped.
    """
    s = scored(
        req(
            "Fluent in English",
            type="disqualifier",
            keywords=["fluent in english", "english fluency"],
            similarity=BELOW,
        )
    )
    assert s.requirements[0].covered
    assert s.requirements[0].matched_term == "english"


def test_filler_stripping_does_not_rescue_an_unclaimable_language() -> None:
    """`fluent in german` reduces to `german`, which the language block still stops."""
    s = scored(
        req("Fluent in German", type="disqualifier", keywords=["fluent in german"], similarity=BELOW)
    )
    assert not s.requirements[0].covered


def test_unrecorded_personal_fact_asks_instead_of_skipping() -> None:
    """Bundesbank skipped at 100% hard coverage on a semester count and a grade average.

    Neither is in master_profile.yaml and no CV bullet would carry them, so the evidence
    store returned nothing and the gate read absence as failure. They are questions.
    """
    s = scored(
        req("At least in the 4th semester of studies", type="disqualifier", keywords=["4th semester"], similarity=BELOW),
        req("Current grade point average of 2.5 or better", type="disqualifier", keywords=["gpa 2.5"], similarity=BELOW),
    )
    assert all(r.unknown for r in s.requirements)
    assert s.unmet_disqualifiers() == []
    assert len(s.open_questions()) == 2
    assert hard_gap_gate(s) == "proceed"


def test_unknown_never_rescues_a_real_disqualifier() -> None:
    """German is recorded — A1/A2. That is a fact, not a blank, and it still skips."""
    s = scored(
        req("Fluent German (C1)", type="disqualifier", keywords=["german"], similarity=BELOW),
        req("At least in the 4th semester", type="disqualifier", keywords=["semester"], similarity=BELOW),
    )
    assert hard_gap_gate(s) == "skip"
    assert "German" in gate_reason(s)


def test_unknown_applies_only_to_disqualifiers() -> None:
    """A hard requirement mentioning a transcript is still a gap, not a question."""
    s = scored(req("Submit a transcript of records", type="hard", keywords=["transcript"], similarity=BELOW))
    assert not s.requirements[0].unknown


ONSITE = "Minimum of three days per week working in person at the office"


def test_onsite_in_a_reachable_city_is_met() -> None:
    """Bain's Berlin office is commutable from Potsdam. It was skipping anyway."""
    s = scored(req(ONSITE, type="disqualifier", keywords=["on-site"], similarity=BELOW),
               location="Berlin, Berlin, Germany")
    assert s.requirements[0].covered
    assert hard_gap_gate(s) == "proceed"


def test_onsite_in_an_unreachable_city_skips() -> None:
    s = scored(req("Collaboration predominantly on-site in Mannheim", type="disqualifier",
                   keywords=["mannheim", "on-site"], similarity=BELOW),
               location="Mannheim, Baden-Württemberg, Germany")
    assert not s.requirements[0].covered
    assert hard_gap_gate(s) == "skip"


def test_germany_alone_does_not_make_a_location_reachable() -> None:
    """Every LinkedIn location ends in ", Germany" — it must not match on its own."""
    s = scored(req(ONSITE, type="disqualifier", keywords=["on-site"], similarity=BELOW),
               location="Munich, Bavaria, Germany")
    assert not s.requirements[0].covered


def test_onsite_without_a_job_location_is_a_question_not_a_skip() -> None:
    """Bain's clause names no city at all. Unknown office != unreachable office."""
    s = scored(req(ONSITE, type="disqualifier", keywords=["on-site"], similarity=BELOW))
    assert s.requirements[0].unknown
    assert hard_gap_gate(s) == "proceed"
    assert len(s.open_questions()) == 1


def test_ability_phrasing_counts_as_an_onsite_requirement() -> None:
    """Mubea wrote "Ability to work regularly in Attendorn" — a presence demand in
    ability clothing. It skipped correctly only because nothing matched it."""
    r_ = req("Ability to work regularly in Attendorn", type="disqualifier",
             keywords=["attendorn"], similarity=BELOW)
    assert not scored(r_, location="Attendorn, Germany").requirements[0].covered
    assert scored(r_, location="Berlin, Germany").requirements[0].covered


# ------------------------------------------------- query-side clause split (retrieval)


def test_subqueries_leaves_a_single_idea_alone() -> None:
    from agentic_ai.coverage import subqueries

    assert subqueries("Proficiency in Python") == ["Proficiency in Python"]


def test_subqueries_splits_a_compound_requirement_into_its_parts() -> None:
    from agentic_ai.coverage import subqueries

    subs = subqueries("Solid backend fundamentals: HTTP APIs, async, databases, testing")
    assert subs[0].startswith("Solid backend"), "the whole text is always queried too"
    assert "HTTP APIs" in subs and "databases" in subs


def test_subqueries_drops_filler_only_fragments() -> None:
    """"e.g." lists leave connective scraps that would retrieve noise."""
    from agentic_ai.coverage import subqueries

    subs = subqueries("First programming experience (e.g. university projects, or internships)")
    assert "experience" not in [s.lower() for s in subs]
    assert any("internship" in s.lower() for s in subs)


def test_subqueries_are_capped() -> None:
    from agentic_ai.coverage import MAX_SUBQUERIES, subqueries

    long = ", ".join(f"skill{n}" for n in range(20))
    assert len(subqueries(long)) <= MAX_SUBQUERIES
