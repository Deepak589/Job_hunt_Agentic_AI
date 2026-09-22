"""review — 3-sample ensemble, plain majority vote (solution.md step 9)."""

from __future__ import annotations

from agentic_ai.nodes import review as review_mod
from agentic_ai.nodes.review import ReviewResult, review
from agentic_ai.state import Draft, DraftBullet, Job, JobState


def _state() -> JobState:
    job = Job(id="t", source="manual", title="AI Engineer", company="Acme", jd_text="...")
    draft = Draft(
        profile_line="x",
        section_order=["projects"],
        bullets={"projects": [DraftBullet(text="Improved recall by 0.833.", metric="0.833", source_bullet_id="proj.rag_pipeline.b1")]},
        cover_letter="x",
    )
    return JobState(job=job, draft=draft)


def _mock_samples(monkeypatch, results: list[ReviewResult]) -> None:
    calls = iter(results)

    def fake(*args, **kwargs):
        return next(calls), [{"node": "review"}]

    monkeypatch.setattr(review_mod, "invoke_structured", fake)


def test_two_pass_one_fail_majority_pass_uses_median(monkeypatch) -> None:
    results = [
        ReviewResult(score=8, weaknesses=[]),
        ReviewResult(score=9, weaknesses=[]),
        ReviewResult(score=5, weaknesses=["weak bullet 1"]),
    ]
    _mock_samples(monkeypatch, results)
    out = review(_state())
    assert out["scores"].review_score == 8  # median of [5, 8, 9]
    assert out["validation_errors"] == []


def test_one_pass_two_fail_majority_retry_unions_weaknesses(monkeypatch) -> None:
    results = [
        ReviewResult(score=9, weaknesses=[]),
        ReviewResult(score=4, weaknesses=["weak bullet A", "weak bullet B"]),
        ReviewResult(score=3, weaknesses=["weak bullet B", "weak bullet C"]),
    ]
    _mock_samples(monkeypatch, results)
    state = _state().model_copy(update={"attempt_count": 0})
    out = review(state)
    assert out["scores"].review_score == 4  # median of [3, 4, 9]
    assert out["validation_errors"] == ["weak bullet A", "weak bullet B", "weak bullet C"]


def test_makes_exactly_three_llm_calls(monkeypatch) -> None:
    results = [ReviewResult(score=8, weaknesses=[]) for _ in range(3)]
    _mock_samples(monkeypatch, results)
    out = review(_state())
    assert len(out["llm_calls"]) == 3


def test_notes_contains_variance_and_raw_scores(monkeypatch) -> None:
    results = [
        ReviewResult(score=8, weaknesses=[]),
        ReviewResult(score=9, weaknesses=[]),
        ReviewResult(score=5, weaknesses=[]),
    ]
    _mock_samples(monkeypatch, results)
    out = review(_state())
    note = out["notes"][0]
    assert "[5, 8, 9]" in note or "8" in note and "9" in note and "5" in note
    assert "variance" in note
    assert "median" in note
