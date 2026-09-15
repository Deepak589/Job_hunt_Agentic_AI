"""plan.md §6 — gates are a hard zero, points are counted not asserted."""

from __future__ import annotations

from agentic_ai.profile import Profile
from agentic_ai.scoring.ats import ParsedPdf, ats_score, gates_failed
from agentic_ai.state import Draft, DraftBullet, Job, JobState, Requirement, Scores


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
    pdf = ParsedPdf(recovered=14, expected=14)
    result = ats_score(s, pdf, profile)
    assert result.total > 0
    assert "hard req coverage" in result.report
    assert result.gates == {"no_fabrication": True, "no_disqualifiers": True}


def test_verdict_thresholds() -> None:
    from agentic_ai.scoring.ats import verdict_for

    assert verdict_for(96) == "apply"
    assert verdict_for(95) == "apply"
    assert verdict_for(94) == "fix_then_apply"
    assert verdict_for(90) == "fix_then_apply"
    assert verdict_for(89.9) == "skip"


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
