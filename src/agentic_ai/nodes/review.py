"""review — LLM call 4, Sonnet (plan.md §3, §7.4). Loops to rewrite, max 2 attempts total."""

from __future__ import annotations

import functools
import statistics
from typing import Literal

from pydantic import BaseModel, Field

from ..config import settings
from ..llm import invoke_structured, make_structured
from ..state import JobState, Scores


class ReviewResult(BaseModel):
    """emit_review — the judge's verdict on one draft."""

    score: int = Field(ge=1, le=10, description="1-10. Below 7 sends the draft back to rewrite.")
    weaknesses: list[str] = Field(default_factory=list, description="Specific, actionable — name the bullet.")


@functools.lru_cache(maxsize=1)
def _prompt() -> str:
    return (settings.prompts_dir / "review.md").read_text()


@functools.lru_cache(maxsize=1)
def _model():
    # claude-sonnet-5 rejects an explicit `temperature` — the param is deprecated for
    # this model (confirmed live: "`temperature` is deprecated for this model").
    return make_structured(settings.review_model, ReviewResult, max_tokens=2048)


def review(state: JobState, verbose: bool = False) -> dict:
    """3-sample ensemble, plain majority vote (solution.md step 9).

    Ruling: 3 independent samples of the same `settings.review_model` rather than a
    Haiku+Sonnet ensemble — this is self-consistency sampling, not model diversity, and
    doesn't need a second model routing path. (A Haiku+Sonnet ensemble is a documented
    future option, not built now.)

    Ruling: plain (unweighted) majority vote. solution.md floats weighting votes by
    judge/human disagreement once κ(review) data exists — it doesn't yet (step 8 just
    landed the machinery; no real judgements recorded). Wiring in fabricated weights
    with no data behind them is exactly the "asserted from judgement" scoring this
    project's CLAUDE.md forbids. Future implementer: plug real weights in here, at the
    vote-counting step, once `judgestats.judge_stats()["review"]` has real history.
    """
    assert state.draft is not None, "review requires rewrite to have run first"
    human = (
        f"<job_description>\n{state.job.jd_text.strip()}\n</job_description>\n\n"
        f"<draft>\n{state.draft.model_dump_json(indent=2)}\n</draft>"
    )
    messages = [("system", _prompt()), ("human", human)]

    results = []
    usage = []
    for _ in range(3):
        verdict, sample_usage = invoke_structured(_model(), messages, model=settings.review_model, node="review", verbose=verbose)
        results.append(verdict)
        usage.extend(sample_usage)

    scores_list = [r.score for r in results]
    pass_votes = sum(1 for s in scores_list if s >= settings.min_review_score)
    majority_pass = pass_votes >= 2
    median_score = statistics.median(scores_list)
    variance = statistics.pvariance(scores_list)

    scores = state.scores.model_copy(update={"review_score": median_score})
    note = f"review: samples={scores_list} median={median_score} variance={variance:.2f} votes={pass_votes}/3"

    # Union (deduped, first-seen order) of weaknesses from every sample that voted
    # retry — mirrors the single-sample logic this replaces.
    weaknesses: list[str] = []
    for r in results:
        if r.score < settings.min_review_score:
            for w in r.weaknesses:
                if w not in weaknesses:
                    weaknesses.append(w)

    will_retry = not majority_pass and state.attempt_count < settings.max_rewrite_attempts
    return {
        "scores": scores,
        "validation_errors": weaknesses if will_retry else [],
        "notes": [note],
        "llm_calls": usage,
    }


def review_gate(state: JobState) -> Literal["retry", "proceed"]:
    """'retry' back to rewrite (attempts remain and score is low), else 'proceed'."""
    score = state.scores.review_score
    if score is not None and score < settings.min_review_score and state.attempt_count < settings.max_rewrite_attempts:
        return "retry"
    return "proceed"
