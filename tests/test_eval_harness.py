"""evals/run_harness.py — proves the harness's own collect/diff logic works, with
every LLM-calling node monkeypatched (same convention as test_graph_phase3.py) so
this costs zero API calls.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import agentic_ai.graph as graph_mod
from agentic_ai.state import Diagnosis, Draft, DraftBullet, Scores

# evals/ isn't a package — load run_harness.py by path.
_spec = importlib.util.spec_from_file_location(
    "run_harness", Path(__file__).parent.parent / "evals" / "run_harness.py"
)
run_harness = importlib.util.module_from_spec(_spec)
sys.modules["run_harness"] = run_harness
_spec.loader.exec_module(run_harness)


# --------------------------------------------------------------------- stub LLM nodes


def _fake_extract_requirements(state):
    return {"requirements": [], "notes": ["stub: 0 requirements"]}


def _fake_diagnose(state):
    return {"diagnosis": Diagnosis(matches=["stub match"]), "notes": ["stub diagnose"]}


def _fake_rewrite(state):
    draft = Draft(
        profile_line="Test profile line.",
        section_order=["projects"],
        bullets={"projects": [DraftBullet(text="Improved recall by 0.8.", metric="0.8", source_bullet_id="proj.rag_pipeline.b1")]},
        cover_letter="x",
    )
    return {"draft": draft, "attempt_count": state.attempt_count + 1, "notes": ["stub rewrite"]}


def _fake_review(state):
    scores = state.scores.model_copy(update={"review_score": 9})
    return {"scores": scores, "validation_errors": [], "notes": ["stub review"]}


def _patch_llm_nodes(monkeypatch) -> None:
    monkeypatch.setattr(graph_mod, "extract_requirements", _fake_extract_requirements)
    monkeypatch.setattr(graph_mod, "diagnose", _fake_diagnose)
    monkeypatch.setattr(graph_mod, "rewrite", _fake_rewrite)
    monkeypatch.setattr(graph_mod, "review", _fake_review)


# --------------------------------------------------------------------- collect_metrics


def test_collect_metrics_runs_fixture_through_stubbed_graph(monkeypatch, tmp_path) -> None:
    _patch_llm_nodes(monkeypatch)
    fixture = tmp_path / "fake_job.txt"
    fixture.write_text("a fake jd, quite unique for the harness test")

    metrics = run_harness.collect_metrics(fixture, full=False)

    # score_coverage/validate_facts are real (only the 4 LLM nodes are stubbed): zero
    # extracted requirements means vacuous 1.0 coverage, and the stub bullet's "0.8"
    # metric has no match in master_profile.yaml so fact validation legitimately fails.
    assert set(metrics) == {"hard_coverage", "soft_coverage", "semantic_fit", "skip_reason"}
    assert metrics["hard_coverage"] == 1.0
    assert metrics["soft_coverage"] == 1.0
    assert "fact validation failed" in metrics["skip_reason"]
    assert "ats_total" not in metrics  # --full not requested


# --------------------------------------------------------------------- diff_metrics


def test_diff_metrics_clean_when_equal() -> None:
    m = {"hard_coverage": 1.0, "soft_coverage": 0.5, "semantic_fit": 0.7, "skip_reason": None}
    assert run_harness.diff_metrics(m, dict(m)) == []


def test_diff_metrics_flags_coverage_drift() -> None:
    baseline = {"hard_coverage": 1.0, "soft_coverage": 0.5, "semantic_fit": 0.7, "skip_reason": None}
    current = {"hard_coverage": 0.8, "soft_coverage": 0.5, "semantic_fit": 0.7, "skip_reason": None}
    problems = run_harness.diff_metrics(baseline, current)
    assert len(problems) == 1
    assert "hard_coverage" in problems[0]


def test_diff_metrics_flags_new_skip_reason() -> None:
    baseline = {"hard_coverage": 1.0, "skip_reason": None}
    current = {"hard_coverage": 1.0, "skip_reason": "no evidence for Rust"}
    problems = run_harness.diff_metrics(baseline, current)
    assert any("skip_reason" in p for p in problems)


def test_diff_metrics_ats_total_within_tolerance_is_clean() -> None:
    baseline = {"ats_total": 92.0}
    current = {"ats_total": 95.0}  # +3, under the 5.0 band
    assert run_harness.diff_metrics(baseline, current) == []


def test_diff_metrics_ats_total_beyond_tolerance_flags() -> None:
    baseline = {"ats_total": 92.0}
    current = {"ats_total": 80.0}  # -12, over the band
    problems = run_harness.diff_metrics(baseline, current)
    assert len(problems) == 1 and "ats_total" in problems[0]


# --------------------------------------------------------------------- diff_all


def test_diff_all_flags_new_and_missing_fixtures() -> None:
    baseline = {"job_a": {"hard_coverage": 1.0}}
    current = {"job_b": {"hard_coverage": 1.0}}
    result = run_harness.diff_all(baseline, current)
    assert "job_a" in result and "missing" in result["job_a"][0]
    assert "job_b" in result and "not in baseline" in result["job_b"][0]


def test_diff_all_empty_when_nothing_moved() -> None:
    baseline = {"job_a": {"hard_coverage": 1.0, "skip_reason": None}}
    current = {"job_a": {"hard_coverage": 1.0, "skip_reason": None}}
    assert run_harness.diff_all(baseline, current) == {}
