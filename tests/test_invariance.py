"""solution.md step 8 — same draft, two section orders -> deterministic outputs must
not depend on print order. Scoped to what's deterministic: ats_score() (pure), and
recruiter_sim() (pure since solution.md step 9 — no LLM call left to stub)."""

from __future__ import annotations

import agentic_ai.nodes.recruiter_sim as recruiter_sim_mod
from agentic_ai.profile import Profile
from agentic_ai.scoring.ats import ParsedPdf, ats_score
from agentic_ai.state import Draft, DraftBullet, Job, JobState, Requirement, Scores


def _draft(section_order: list[str]) -> Draft:
    return Draft(
        profile_line="x",
        section_order=section_order,
        bullets={
            "projects": [
                DraftBullet(text="Improved recall by 0.833 using rag.", metric="0.833", source_bullet_id="proj.rag_pipeline.b1")
            ],
            "experience": [
                DraftBullet(text="Reduced latency by 20% using python.", metric="20%", source_bullet_id="exp.a.b1")
            ],
        },
        cover_letter="x",
    )


def _state(section_order: list[str]) -> JobState:
    job = Job(id="t", source="manual", title="AI Engineer", company="Acme", jd_text="...")
    reqs = [
        Requirement(text="Python", type="hard", keywords=["python"], covered=True, covered_by="keyword"),
        Requirement(text="RAG", type="hard", keywords=["rag"], covered=True, covered_by="semantic"),
    ]
    return JobState(
        job=job, draft=_draft(section_order), requirements=reqs, validation_errors=[],
        scores=Scores(hard_coverage=1.0, review_score=9),
    )


def test_ats_total_is_invariant_to_section_order() -> None:
    profile = Profile.load()
    pdf = ParsedPdf(recovered=14, expected=14, text="python rag experience")

    a = ats_score(_state(["skills", "projects", "experience"]), pdf, profile)
    b = ats_score(_state(["experience", "skills", "projects"]), pdf, profile)

    assert a.total == b.total


def test_recruiter_sim_result_is_invariant_to_section_order() -> None:
    a = recruiter_sim_mod.recruiter_sim(_state(["skills", "projects", "experience"]))
    b = recruiter_sim_mod.recruiter_sim(_state(["experience", "skills", "projects"]))

    assert a["recruiter"].result == b["recruiter"].result == "pass"
