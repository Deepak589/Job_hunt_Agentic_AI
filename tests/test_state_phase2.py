from __future__ import annotations

from agentic_ai.state import Diagnosis, Draft, DraftBullet, Job, JobState


def _job() -> Job:
    return Job(id="t", source="manual", title="Test Role", jd_text="...")


def test_diagnosis_and_draft_round_trip_through_job_state() -> None:
    diag = Diagnosis(
        hard_gaps=["Rust"],
        soft_gaps=["Kubernetes at scale"],
        disqualifiers=[],
        matches=["Python", "FastAPI"],
        positioning_mismatch=None,
    )
    draft = Draft(
        profile_line="AI/ML Engineer...",
        section_order=["projects", "experience", "skills"],
        bullets={
            "projects": [
                DraftBullet(
                    text="Reduced retrieval latency by 40% by building a hybrid BM25+dense retriever.",
                    metric="40%",
                    source_bullet_id="proj.rag_pipeline.b1",
                )
            ]
        },
        cover_letter="Dear hiring team...",
        highlighted_projects=["proj.rag_pipeline"],
    )
    state = JobState(job=_job(), diagnosis=diag, draft=draft, attempt_count=1)

    dumped = state.model_dump_json()
    restored = JobState.model_validate_json(dumped)

    assert restored.diagnosis is not None and restored.diagnosis.hard_gaps == ["Rust"]
    assert restored.draft is not None
    assert restored.draft.bullets["projects"][0].source_bullet_id == "proj.rag_pipeline.b1"
    assert restored.attempt_count == 1
    assert restored.validation_errors == []


def test_diagnosis_and_draft_default_to_none() -> None:
    """Phase 1 states (a skip, before diagnose ever runs) must still validate."""
    state = JobState(job=_job())
    assert state.diagnosis is None
    assert state.draft is None
    assert state.attempt_count == 0
