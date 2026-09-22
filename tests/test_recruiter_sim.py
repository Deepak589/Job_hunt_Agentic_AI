"""recruiter_sim — deterministic literal-keyword screen (solution.md step 9)."""

from __future__ import annotations

from agentic_ai.nodes.recruiter_sim import recruiter_sim
from agentic_ai.state import Draft, DraftBullet, Job, JobState, Requirement


def _state(reqs: list[Requirement], profile_line: str = "AI Engineer.") -> JobState:
    job = Job(id="t", source="manual", title="AI Engineer", company="Acme", jd_text="...")
    draft = Draft(
        profile_line=profile_line,
        section_order=["projects"],
        bullets={"projects": [DraftBullet(text="Built a python RAG pipeline.", source_bullet_id="proj.rag_pipeline.b1")]},
        cover_letter="x",
    )
    return JobState(job=job, draft=draft, requirements=reqs)


def test_all_covered_hard_keywords_present_passes_with_no_llm_calls() -> None:
    reqs = [
        Requirement(text="Python", type="hard", keywords=["python"], covered=True, covered_by="keyword"),
        Requirement(text="RAG", type="hard", keywords=["rag"], covered=True, covered_by="semantic"),
    ]
    out = recruiter_sim(_state(reqs))
    assert out["recruiter"].result == "pass"
    assert out["llm_calls"] == []


def test_missing_covered_hard_keyword_hard_fails() -> None:
    reqs = [
        Requirement(text="Kubernetes", type="hard", keywords=["kubernetes", "k8s"], covered=True, covered_by="semantic"),
    ]
    out = recruiter_sim(_state(reqs))
    assert out["recruiter"].result == "hard_fail"
    assert "Kubernetes" in out["recruiter"].reason


def test_no_hard_requirements_passes() -> None:
    reqs = [Requirement(text="German B2", type="soft", keywords=["german"], covered=False)]
    out = recruiter_sim(_state(reqs))
    assert out["recruiter"].result == "pass"


def test_uncovered_hard_requirement_missing_keyword_does_not_trigger_fail() -> None:
    reqs = [
        Requirement(text="Python", type="hard", keywords=["python"], covered=True, covered_by="keyword"),
        Requirement(text="Go", type="hard", keywords=["golang"], covered=False),
    ]
    out = recruiter_sim(_state(reqs))
    assert out["recruiter"].result == "pass"
