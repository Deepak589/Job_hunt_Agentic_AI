"""runner.py — poll -> snapshot diff -> prefilter -> run_many -> digest (solution.md
step 6). Every connector's `fetch_jobs` and every LLM-calling graph node is
monkeypatched — zero real network or API calls, same convention as
tests/test_run_many.py and tests/test_graph_phase3.py."""

from __future__ import annotations

import agentic_ai.batch as batch_mod
import agentic_ai.coverage as coverage_mod
import agentic_ai.graph as graph_mod
import agentic_ai.runner as runner_mod
from agentic_ai.nodes.requirements import ExtractedRequirement, RequirementList
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


def _passthrough_coverage(monkeypatch) -> None:
    """retrieve_evidence/score_coverage are deterministic/non-LLM but still hit a real
    embedding model — stub them so the batch test stays a pure unit test of the
    prefill wiring, not an integration test of coverage.py (already covered by
    tests/test_coverage.py)."""
    monkeypatch.setattr(coverage_mod, "retrieve_evidence", lambda state: {"requirements": state.requirements})
    monkeypatch.setattr(coverage_mod, "score_coverage", lambda state: {})


def _usage(node: str) -> dict:
    return {
        "node": node, "model": "claude-x", "input_tokens": 10, "output_tokens": 5,
        "cache_read_tokens": 0, "cache_creation_tokens": 0, "cost_usd": 0.001,
    }


def _batch_job(url: str, jd_text: str) -> Job:
    return Job(
        id="placeholder", source="greenhouse", url=url, title="T", company="FakeCo",
        location="Berlin, Germany", jd_text=jd_text, lang="en", employment_type="fulltime",
    )


def test_run_batch_prefills_requirements_and_diagnosis_into_run_many(monkeypatch, tmp_path) -> None:
    """Every job's extract+diagnose batch call succeeds — proves the skip fired for
    both nodes by raising if either is invoked with an empty field (nodes/requirements.py
    and nodes/diagnose.py's own skip is what's under test here, not a stub standing in
    for them)."""
    monkeypatch.setattr(graph_mod, "rewrite", lambda state: {
        "draft": Draft(profile_line="p", section_order=["projects"], bullets={}, cover_letter="c"),
        "attempt_count": state.attempt_count + 1, "notes": [],
    })
    monkeypatch.setattr(graph_mod, "review", lambda state: {
        "scores": state.scores.model_copy(update={"review_score": 9}), "validation_errors": [], "notes": [],
    })
    monkeypatch.setattr(graph_mod, "recruiter_sim", lambda state: {
        "recruiter": RecruiterResult(result="pass", reason="stub"), "notes": [],
    })
    monkeypatch.setattr(graph_mod, "hiring_manager", lambda state: {
        "hiring_manager": HiringManagerVerdict(verdict="apply", why="stub"), "notes": [],
    })
    monkeypatch.setattr(graph_mod, "render_documents", lambda state: {
        "artifacts": {"cv_pdf": "x.pdf", "cover_pdf": "y.pdf"}, "notes": [],
    })
    monkeypatch.setattr(graph_mod, "score_ats", lambda state: {
        "ats": AtsScore(total=97.0, verdict="apply", gates={}, components={}, report="stub"), "notes": [],
    })

    def _guarded_extract(state):
        if not state.requirements:
            raise AssertionError("extract_requirements invoked live — prefill skip did not fire")
        return {}

    def _guarded_diagnose(state):
        if state.diagnosis is None:
            raise AssertionError("diagnose invoked live — prefill skip did not fire")
        return {}

    monkeypatch.setattr(graph_mod, "extract_requirements", _guarded_extract)
    monkeypatch.setattr(graph_mod, "diagnose", _guarded_diagnose)
    _passthrough_coverage(monkeypatch)
    _isolate_db(monkeypatch, tmp_path)

    job = _batch_job("https://x/a", "Need Python.")
    jid = graph_mod.job_id(job.source, job.url, job.jd_text)
    diagnosis = Diagnosis(hard_gaps=[], soft_gaps=[], disqualifiers=[], matches=["batch match"])
    fake = _FakeBatchClient({
        f"{jid}:extract": {
            "parsed": RequirementList(requirements=[
                ExtractedRequirement(text="Python", type="hard", keywords=["python"])
            ]),
            "usage": _usage("extract_requirements"), "error": None,
        },
        f"{jid}:diagnose": {"parsed": diagnosis, "usage": _usage("diagnose"), "error": None},
    })
    monkeypatch.setattr(batch_mod, "BatchClient", lambda: fake)

    digest = {"run": 0, "skipped": 0, "verdicts": {"apply": 0, "fix_then_apply": 0, "skip": 0}, "notify": []}
    runner_mod._run_batch([(job, "FakeCo")], digest)

    assert digest["run"] == 1
    assert digest["verdicts"]["apply"] == 1
    assert digest["notify"] == []  # no fallback


def test_run_batch_falls_back_to_live_when_extract_errored(monkeypatch, tmp_path) -> None:
    """One job's extract batch call errors — it must still get a live run, not be
    dropped, and the digest must note the fallback."""
    monkeypatch.setattr(graph_mod, "rewrite", lambda state: {
        "draft": Draft(profile_line="p", section_order=["projects"], bullets={}, cover_letter="c"),
        "attempt_count": state.attempt_count + 1, "notes": [],
    })
    monkeypatch.setattr(graph_mod, "review", lambda state: {
        "scores": state.scores.model_copy(update={"review_score": 9}), "validation_errors": [], "notes": [],
    })
    monkeypatch.setattr(graph_mod, "recruiter_sim", lambda state: {
        "recruiter": RecruiterResult(result="pass", reason="stub"), "notes": [],
    })
    monkeypatch.setattr(graph_mod, "hiring_manager", lambda state: {
        "hiring_manager": HiringManagerVerdict(verdict="apply", why="stub"), "notes": [],
    })
    monkeypatch.setattr(graph_mod, "render_documents", lambda state: {
        "artifacts": {"cv_pdf": "x.pdf", "cover_pdf": "y.pdf"}, "notes": [],
    })
    monkeypatch.setattr(graph_mod, "score_ats", lambda state: {
        "ats": AtsScore(total=97.0, verdict="apply", gates={}, components={}, report="stub"), "notes": [],
    })

    def _guarded_extract(state):
        # Real skip check first — a prefilled job (the batch-succeeded one) must never
        # reach the "live" branch below at all.
        if state.requirements:
            return {}
        return {"requirements": [], "notes": ["live fallback extract"]}

    def _guarded_diagnose(state):
        if state.diagnosis is not None:
            return {}
        return {"diagnosis": Diagnosis(matches=["live fallback"]), "notes": []}

    monkeypatch.setattr(graph_mod, "extract_requirements", _guarded_extract)
    monkeypatch.setattr(graph_mod, "diagnose", _guarded_diagnose)
    _passthrough_coverage(monkeypatch)
    _isolate_db(monkeypatch, tmp_path)

    job_ok = _batch_job("https://x/ok", "Need Python.")
    job_bad = _batch_job("https://x/bad", "Need Rust.")
    jid_ok = graph_mod.job_id(job_ok.source, job_ok.url, job_ok.jd_text)
    jid_bad = graph_mod.job_id(job_bad.source, job_bad.url, job_bad.jd_text)
    diagnosis = Diagnosis(hard_gaps=[], soft_gaps=[], disqualifiers=[], matches=["batch match"])
    fake = _FakeBatchClient({
        f"{jid_ok}:extract": {
            "parsed": RequirementList(requirements=[
                ExtractedRequirement(text="Python", type="hard", keywords=["python"])
            ]),
            "usage": _usage("extract_requirements"), "error": None,
        },
        f"{jid_bad}:extract": {"parsed": None, "usage": None, "error": "batch result errored"},
        f"{jid_ok}:diagnose": {"parsed": diagnosis, "usage": _usage("diagnose"), "error": None},
    })
    monkeypatch.setattr(batch_mod, "BatchClient", lambda: fake)

    digest = {"run": 0, "skipped": 0, "verdicts": {"apply": 0, "fix_then_apply": 0, "skip": 0}, "notify": []}
    runner_mod._run_batch([(job_ok, "FakeCo"), (job_bad, "FakeCo")], digest)

    assert digest["run"] == 2
    assert digest["verdicts"]["apply"] == 2
    assert any("batch fallback: 1" in line for line in digest["notify"])
