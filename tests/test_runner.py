"""runner.py — poll -> snapshot diff -> prefilter -> run_many -> digest (solution.md
step 6). Every connector's `fetch_jobs` and every LLM-calling graph node is
monkeypatched — zero real network or API calls, same convention as
tests/test_run_many.py and tests/test_graph_phase3.py."""

from __future__ import annotations

import agentic_ai.graph as graph_mod
import agentic_ai.runner as runner_mod
from agentic_ai.sourcing import greenhouse
from agentic_ai.state import (
    AtsScore,
    Diagnosis,
    Draft,
    DraftBullet,
    HiringManagerVerdict,
    Job,
    RecruiterResult,
)


def _fake_extract_requirements(state):
    return {"requirements": [], "notes": ["stub extract"]}


def _fake_diagnose(state):
    diagnosis = Diagnosis(hard_gaps=[], soft_gaps=[], disqualifiers=[], matches=["stub match"])
    return {"diagnosis": diagnosis, "notes": ["stub diagnose"]}


def _fake_rewrite(state):
    draft = Draft(
        profile_line="Test profile line.",
        section_order=["projects"],
        bullets={
            "projects": [
                DraftBullet(text="Improved recall by 0.833.", metric="0.833", source_bullet_id="proj.rag_pipeline.b1")
            ]
        },
        cover_letter="x",
    )
    return {"draft": draft, "attempt_count": state.attempt_count + 1, "notes": ["stub rewrite"]}


def _fake_review(state):
    scores = state.scores.model_copy(update={"review_score": 9})
    return {"scores": scores, "validation_errors": [], "notes": ["stub review"]}


def _fake_recruiter_sim(state):
    return {"recruiter": RecruiterResult(result="pass", reason="stub"), "notes": ["stub recruiter"]}


def _fake_hiring_manager(state):
    verdict = HiringManagerVerdict(verdict="apply", why="stub", indefensible_bullets=[])
    return {"hiring_manager": verdict, "notes": ["stub hiring_manager"]}


def _fake_render_documents(state):
    return {"artifacts": {"cv_pdf": "fake_cv.pdf", "cover_pdf": "fake_cover.pdf"}, "notes": ["stub render"]}


def _fake_score_ats(state):
    ats = AtsScore(total=97.0, verdict="apply", gates={}, components={}, report="stub report")
    return {"ats": ats, "notes": ["stub ats"]}


def _patch_llm_nodes(monkeypatch) -> None:
    monkeypatch.setattr(graph_mod, "extract_requirements", _fake_extract_requirements)
    monkeypatch.setattr(graph_mod, "diagnose", _fake_diagnose)
    monkeypatch.setattr(graph_mod, "rewrite", _fake_rewrite)
    monkeypatch.setattr(graph_mod, "review", _fake_review)
    monkeypatch.setattr(graph_mod, "recruiter_sim", _fake_recruiter_sim)
    monkeypatch.setattr(graph_mod, "hiring_manager", _fake_hiring_manager)
    monkeypatch.setattr(graph_mod, "render_documents", _fake_render_documents)
    monkeypatch.setattr(graph_mod, "score_ats", _fake_score_ats)


def _isolate_db(monkeypatch, tmp_path) -> None:
    """Same pattern as tests/test_run_many.py's `_isolate_db` — point the shared
    `settings` singleton's db paths at throwaway files so tests never touch real
    project state."""
    monkeypatch.setattr(graph_mod.settings, "jobs_db_path", tmp_path / "jobpilot.db")
    monkeypatch.setattr(graph_mod.settings, "checkpoint_db_path", tmp_path / "checkpoints.db")


def _companies_yaml(tmp_path, source="greenhouse", identifier="fakeco") -> str:
    path = tmp_path / "companies.yaml"
    path.write_text(
        f"companies:\n  - name: FakeCo\n    source: {source}\n    identifier: {identifier}\n"
    )
    return path


_ONE_JOB = [
    Job(
        id="greenhouse:fakeco:1",
        source="greenhouse",
        url="https://boards.greenhouse.io/fakeco/jobs/1",
        title="Data Scientist",
        company="FakeCo",
        location="Berlin, Germany",
        jd_text="We need Python and SQL.",
        lang="en",
        employment_type="fulltime",
    )
]


def test_run_twice_produces_zero_new_postings_second_time(monkeypatch, tmp_path) -> None:
    _patch_llm_nodes(monkeypatch)
    _isolate_db(monkeypatch, tmp_path)
    companies_path = _companies_yaml(tmp_path)
    monkeypatch.setattr(greenhouse, "fetch_jobs", lambda identifier, **kw: list(_ONE_JOB))

    first = runner_mod.run(companies_path)
    assert first["new_postings"] == 1
    assert first["run"] == 1
    assert first["verdicts"]["apply"] == 1

    second = runner_mod.run(companies_path)
    assert second["new_postings"] == 0
    assert second["run"] == 0


def test_three_consecutive_failures_recorded_and_notified(monkeypatch, tmp_path) -> None:
    from agentic_ai.db.repo import get_company_snapshot

    _isolate_db(monkeypatch, tmp_path)
    companies_path = _companies_yaml(tmp_path)

    def _raise(identifier, **kw):
        raise RuntimeError("board is down")

    monkeypatch.setattr(greenhouse, "fetch_jobs", _raise)

    digest = None
    for _ in range(3):
        digest = runner_mod.run(companies_path)

    snapshot = get_company_snapshot("FakeCo", "greenhouse", db_path=graph_mod.settings.jobs_db_path)
    assert snapshot["consecutive_failures"] == 3
    assert any("FakeCo" in line and "3" in line for line in digest["notify"])
