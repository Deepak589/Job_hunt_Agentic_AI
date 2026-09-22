"""Cohen's kappa per judge node vs human action (solution.md step 8). Pure Python — no
new dependency for one statistic on a handful of rows.
"""

from __future__ import annotations

from .config import settings
from .db.repo import judgement_pairs

# Ruling: each judge node's output space isn't literally the human_action space, so
# "agree" needs a mapping. recruiter_sim/hiring_manager map straight onto human_action;
# 'review' stores the raw review_score and is bucketed via review_bucket() below before
# this mapping applies (its judge-output space is pass/retry, not a numeric score).
_SIMPLE_MAPS: dict[str, dict[str, str]] = {
    "recruiter_sim": {"pass": "approve", "soft_fail": "edit", "hard_fail": "reject"},
    "hiring_manager": {"apply": "approve", "fix_then_apply": "edit", "skip": "reject"},
}
_REVIEW_MAP = {"pass": "approve", "retry": "edit"}


def review_bucket(review_score: int) -> str:
    """review_score >= settings.min_review_score -> 'pass' else 'retry' (the judge-output
    category for the 'review' node, before mapping onto human_action)."""
    return "pass" if review_score >= settings.min_review_score else "retry"


def cohens_kappa(pairs: list[tuple[str, str]], map_judge_to_human: dict[str, str]) -> float | None:
    """po/pe/kappa over `pairs` = (judge_output, human_action). None if `pairs` is empty —
    nothing to compute yet, not an error (realistic on a fresh install)."""
    if not pairs:
        return None
    n = len(pairs)
    agree = sum(1 for judge, human in pairs if map_judge_to_human.get(judge) == human)
    po = agree / n

    categories = set(human for _, human in pairs) | set(map_judge_to_human.get(j, j) for j, _ in pairs)
    pe = 0.0
    for c in categories:
        n_judge_c = sum(1 for j, _ in pairs if map_judge_to_human.get(j) == c)
        n_human_c = sum(1 for _, h in pairs if h == c)
        pe += (n_judge_c / n) * (n_human_c / n)

    if pe == 1.0:
        return 0.0
    return (po - pe) / (1 - pe)


def review_cap_still_trusted() -> bool:
    """Gate for `scoring/ats.py`'s review cap (solution.md step 9).

    The cap holds a fact-clean-but-review-flagged draft below "apply". It only makes
    sense while the review judge is trustworthy. Ruling: trust it (keep the cap) unless
    there's enough real history to say otherwise — >= 30 recorded review judgements AND
    kappa(review) < 0.4 (judge disagrees with human too often to lean on). With fewer
    than 30 pairs (the realistic state on a fresh install — none of this has fired
    against real data yet), keep today's behavior: cap applies.
    """
    pairs = judgement_pairs("review")
    if len(pairs) < 30:
        return True
    review_pairs = [(review_bucket(int(score)), human) for score, human in pairs]
    kappa = cohens_kappa(review_pairs, _REVIEW_MAP)
    if kappa is not None and kappa < 0.4:
        return False
    return True


def judge_stats() -> dict[str, float | None]:
    """One kappa per judge node, pulling pairs from the judgements table."""
    stats = {node: cohens_kappa(judgement_pairs(node), mapping) for node, mapping in _SIMPLE_MAPS.items()}
    review_pairs = [(review_bucket(int(score)), human) for score, human in judgement_pairs("review")]
    stats["review"] = cohens_kappa(review_pairs, _REVIEW_MAP)
    return stats
