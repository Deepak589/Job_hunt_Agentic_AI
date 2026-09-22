"""Cohen's kappa per judge node (solution.md step 8)."""

from __future__ import annotations

from agentic_ai import judgestats
from agentic_ai.config import settings
from agentic_ai.db.repo import record_judgement


def test_cohens_kappa_perfect_agreement() -> None:
    m = {"pass": "approve", "hard_fail": "reject"}
    pairs = [("pass", "approve"), ("pass", "approve"), ("hard_fail", "reject")]
    assert judgestats.cohens_kappa(pairs, m) == 1.0


def test_cohens_kappa_partial_agreement_hand_computed() -> None:
    # 4 pairs, map is identity (judge label == human label already).
    # judge:  pass, pass, fail, fail
    # human:  approve, edit, edit, edit   (mapped: pass->approve, fail->edit)
    # agree on pairs: (pass,approve) yes, (pass,edit) no, (fail,edit) yes, (fail,edit) yes
    # po = 3/4 = 0.75
    # categories (human space) = {approve, edit}
    # n=4; n_judge(->approve)=2 (the two "pass"), n_human(approve)=1 -> pe term = (2/4)*(1/4) = 0.125
    #      n_judge(->edit)=2 (the two "fail"),   n_human(edit)=3    -> pe term = (2/4)*(3/4) = 0.375
    # pe = 0.125 + 0.375 = 0.5
    # kappa = (po - pe) / (1 - pe) = (0.75 - 0.5) / 0.5 = 0.5
    m = {"pass": "approve", "fail": "edit"}
    pairs = [("pass", "approve"), ("pass", "edit"), ("fail", "edit"), ("fail", "edit")]
    kappa = judgestats.cohens_kappa(pairs, m)
    assert kappa == 0.5


def test_cohens_kappa_empty_pairs_is_none() -> None:
    assert judgestats.cohens_kappa([], {}) is None


def test_judge_stats_from_recorded_rows(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "jobs_db_path", tmp_path / "jobpilot.db")
    record_judgement("j1", "recruiter_sim", "pass", "approve")
    record_judgement("j2", "recruiter_sim", "hard_fail", "reject")
    record_judgement("j1", "review", str(settings.min_review_score), "approve")
    record_judgement("j2", "review", str(settings.min_review_score - 1), "edit")

    stats = judgestats.judge_stats()

    assert stats["recruiter_sim"] == 1.0
    assert stats["review"] == 1.0
    assert stats["hiring_manager"] is None
