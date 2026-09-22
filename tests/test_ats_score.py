"""plan.md §6 — gates are a hard zero, points are counted not asserted."""

from __future__ import annotations

import pytest

from agentic_ai.config import settings
from agentic_ai.db.repo import record_judgement
from agentic_ai.profile import Profile
from agentic_ai.scoring.ats import ParsedPdf, ats_score, gates_failed
from agentic_ai.state import Draft, DraftBullet, Job, JobState, Requirement, Scores


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    """ats_score() -> review_cap_still_trusted() -> judgement_pairs() touches the jobs
    db (solution.md step 9) — every test in this file must use an isolated db, not the
    real on-disk data/jobpilot.db, even ones that don't otherwise care about judgements."""
    monkeypatch.setattr(settings, "jobs_db_path", tmp_path / "jobpilot.db")


def _profile() -> Profile:
    return Profile.load()


def _clean_state(review_score: int = 9) -> JobState:
    job = Job(id="t", source="manual", title="AI Engineer", company="Acme", jd_text="...")
    draft = Draft(
        profile_line="x",
        section_order=["projects"],
        bullets={
            "projects": [
                DraftBullet(text="Improved recall by 0.833.", metric="0.833", source_bullet_id="proj.rag_pipeline.b1")
            ]
        },
        cover_letter="x",
    )
    reqs = [
        Requirement(text="Python", type="hard", keywords=["python"], covered=True, covered_by="keyword"),
        Requirement(text="RAG", type="hard", keywords=["rag"], covered=True, covered_by="semantic"),
    ]
    return JobState(
        job=job, draft=draft, requirements=reqs, validation_errors=[],
        scores=Scores(hard_coverage=1.0, review_score=review_score),
    )


def test_gates_pass_with_no_validation_errors_and_no_disqualifier() -> None:
    assert gates_failed(_clean_state()) == {}


def test_gates_fail_when_validation_errors_present() -> None:
    s = _clean_state().model_copy(update={"validation_errors": ["projects[0]: unverified number '99%'"]})
    failed = gates_failed(s)
    assert failed.get("no_fabrication") is False


def test_gates_fail_on_unmet_disqualifier() -> None:
    s = _clean_state()
    s = s.model_copy(update={"requirements": s.requirements + [
        Requirement(text="C1 German", type="disqualifier", keywords=["german"], covered=False)
    ]})
    failed = gates_failed(s)
    assert failed.get("no_disqualifiers") is False


def test_a_failed_gate_zeroes_the_total_regardless_of_points() -> None:
    profile = _profile()
    s = _clean_state().model_copy(update={"validation_errors": ["bad number"]})
    pdf = ParsedPdf(recovered=10, expected=10)
    result = ats_score(s, pdf, profile)
    assert result.total == 0.0
    assert result.verdict == "skip"


def test_clean_high_coverage_state_scores_high_and_reports_counts() -> None:
    profile = _profile()
    s = _clean_state(review_score=9)
    pdf = ParsedPdf(recovered=14, expected=14, text="python rag experience")
    result = ats_score(s, pdf, profile)
    assert result.total > 0
    assert "hard req coverage" in result.report
    assert result.gates == {"no_fabrication": True, "no_disqualifiers": True}


def test_literal_keywords_component_separates_drafts_with_equal_coverage() -> None:
    """plan.md §6 / solution.md step 4: two drafts with identical requirement coverage
    must NOT score identically — one has the evidenced JD keywords literally in the
    rendered PDF text, the other doesn't."""
    profile = _profile()
    s = _clean_state(review_score=9)
    pdf_with_keywords = ParsedPdf(recovered=14, expected=14, text="Built a python RAG pipeline.")
    pdf_without_keywords = ParsedPdf(recovered=14, expected=14, text="Built a retrieval pipeline.")
    with_kw = ats_score(s, pdf_with_keywords, profile)
    without_kw = ats_score(s, pdf_without_keywords, profile)
    assert with_kw.total - without_kw.total >= 10
    assert with_kw.components["literal keywords"] == 20.0
    assert without_kw.components["literal keywords"] == 0.0


def test_low_review_score_caps_total_below_apply() -> None:
    profile = _profile()
    s = _clean_state(review_score=4)
    pdf = ParsedPdf(recovered=14, expected=14)
    result = ats_score(s, pdf, profile)
    assert result.total <= 94.0
    assert result.verdict != "apply"
    assert "CAPPED" in result.report


def test_high_review_score_is_not_capped() -> None:
    profile = _profile()
    s = _clean_state(review_score=9)
    pdf = ParsedPdf(recovered=14, expected=14)
    result = ats_score(s, pdf, profile)
    assert "CAPPED" not in result.report


def test_missing_review_score_is_not_capped() -> None:
    profile = _profile()
    s = _clean_state(review_score=9).model_copy(update={"scores": Scores(hard_coverage=1.0, review_score=None)})
    pdf = ParsedPdf(recovered=14, expected=14)
    result = ats_score(s, pdf, profile)
    assert "CAPPED" not in result.report


def test_review_cap_does_not_override_a_failed_gate() -> None:
    profile = _profile()
    s = _clean_state(review_score=4).model_copy(update={"validation_errors": ["bad number"]})
    pdf = ParsedPdf(recovered=10, expected=10)
    result = ats_score(s, pdf, profile)
    assert result.total == 0.0
    assert result.verdict == "skip"


def test_verdict_thresholds() -> None:
    from agentic_ai.scoring.ats import verdict_for

    assert verdict_for(96) == "apply"
    assert verdict_for(95) == "apply"
    assert verdict_for(94) == "fix_then_apply"
    assert verdict_for(90) == "fix_then_apply"
    assert verdict_for(89.9) == "skip"


def test_review_cap_still_applies_with_no_judgement_history() -> None:
    """solution.md step 9: fresh install, no judgements recorded yet — cap behaves
    exactly as before (< 30 pairs keeps the cap trusted)."""
    profile = _profile()
    s = _clean_state(review_score=4)
    pdf = ParsedPdf(recovered=14, expected=14)
    result = ats_score(s, pdf, profile)
    assert result.total <= 94.0
    assert "CAPPED" in result.report


def test_review_cap_skipped_when_kappa_below_threshold_with_enough_history() -> None:
    """>= 30 review judgements, engineered to disagree with human (kappa < 0.4) — the
    judge is shown unreliable, so a fact-clean-but-low-review draft is not held below
    'apply' by a cap that can't be trusted."""
    for i in range(15):
        record_judgement(f"j{i}a", "review", "9", "edit")  # judge says pass, human disagrees
        record_judgement(f"j{i}b", "review", "3", "approve")  # judge says retry, human disagrees

    profile = _profile()
    s = _clean_state(review_score=4)
    pdf = ParsedPdf(recovered=14, expected=14, text="Built a python RAG pipeline.")
    result = ats_score(s, pdf, profile)
    assert "CAPPED" not in result.report
    assert result.total > 94.0


def test_review_cap_still_applies_when_kappa_above_threshold_with_enough_history() -> None:
    """>= 30 review judgements, engineered to mostly agree with human (kappa >= 0.4) —
    the judge is trusted, so the cap still applies exactly as before."""
    for i in range(25):
        record_judgement(f"j{i}a", "review", "9", "approve")  # agree
    for i in range(5):
        record_judgement(f"j{i}b", "review", "3", "edit")  # agree

    profile = _profile()
    s = _clean_state(review_score=4)
    pdf = ParsedPdf(recovered=14, expected=14, text="Built a python RAG pipeline.")
    result = ats_score(s, pdf, profile)
    assert "CAPPED" in result.report
    assert result.total <= 94.0


def test_pdf_parseability_recovers_expected_fields_from_a_real_render(tmp_path) -> None:
    """Component 3 depends on re-extracting text from the ACTUAL rendered PDF, not a
    guess — this is the failure mode that silently loses interviews (plan.md §6)."""
    import json

    from agentic_ai.render import build_cv_render_data, render_documents
    from agentic_ai.state import Job

    profile = _profile()
    draft = Draft(
        profile_line="AI/ML Engineer.",
        section_order=["projects"],
        bullets={"projects": [DraftBullet(text="Improved recall by 0.833.", metric="0.833", source_bullet_id="proj.rag_pipeline.b1")]},
        cover_letter="x",
    )
    job = Job(id="pdftest", source="manual", title="AI Engineer", company="Acme", jd_text="...")
    state = JobState(job=job, draft=draft)
    out = render_documents(state)
    from pathlib import Path

    from agentic_ai.scoring.ats import parse_pdf

    parsed = parse_pdf(Path(out["artifacts"]["cv_pdf"]), profile)
    assert parsed.recovered > 0
    assert parsed.expected > 0
