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
    Job,
)


def _fake_analyze(state):
    diagnosis = Diagnosis(hard_gaps=[], soft_gaps=[], disqualifiers=[], matches=["stub match"])
    return {"requirements": [], "diagnosis": diagnosis, "notes": ["stub analyze"]}


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


def _fake_validate_facts(state):
    return {"validation_errors": [], "notes": ["stub validate_facts"]}


def _fake_render_documents(state):
    return {"artifacts": {"cv_pdf": "fake_cv.pdf", "cover_pdf": "fake_cover.pdf"}, "notes": ["stub render"]}


def _fake_score_ats(state):
    ats = AtsScore(total=97.0, verdict="apply", gates={}, components={}, report="stub report")
    return {"ats": ats, "notes": ["stub ats"]}


def _patch_llm_nodes(monkeypatch) -> None:
    monkeypatch.setattr(graph_mod, "analyze", _fake_analyze)
    monkeypatch.setattr(graph_mod, "rewrite", _fake_rewrite)
    monkeypatch.setattr(graph_mod, "validate_facts_node", _fake_validate_facts)
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


# ------------------------------------------------------------------- run(batch=True)


class _FakeBatchClient:
    """Stands in for `agentic_ai.batch.BatchClient` — canned `fetch_results` per
    custom_id, keyed off which `submit()` call requested it (extract batch, then
    diagnose batch), no real network/API calls."""

    def __init__(self, results_by_custom_id: dict) -> None:
        self.results_by_custom_id = results_by_custom_id
        self.submitted: list[list[str]] = []

    def submit(self, requests) -> str:
        self.submitted.append([r.custom_id for r in requests])
        return f"batch{len(self.submitted)}"

    def poll(self, batch_id: str, **kw) -> None:
        pass

    def fetch_results(self, batch_id: str) -> dict:
        ids = self.submitted[int(batch_id.removeprefix("batch")) - 1]
        return {cid: self.results_by_custom_id[cid] for cid in ids if cid in self.results_by_custom_id}


def test_batch_flag_is_retired_and_runs_live(monkeypatch, tmp_path) -> None:
    _patch_llm_nodes(monkeypatch)
    _isolate_db(monkeypatch, tmp_path)
    companies_path = _companies_yaml(tmp_path)
    monkeypatch.setattr(greenhouse, "fetch_jobs", lambda identifier, **kw: list(_ONE_JOB))

    digest = runner_mod.run(companies_path, batch=True)

    assert digest["run"] == 1
    assert any("--batch is retired" in line for line in digest["notify"])
