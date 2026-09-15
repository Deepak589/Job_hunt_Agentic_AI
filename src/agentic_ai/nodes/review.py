"""review — LLM call 4, Sonnet (plan.md §3, §7.4). Loops to rewrite, max 2 attempts total."""

from __future__ import annotations

import functools

from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel, Field

from ..config import settings
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
    llm = ChatAnthropic(model=settings.review_model, max_tokens=2048)
    return llm.with_structured_output(ReviewResult, include_raw=True)


def review(state: JobState, verbose: bool = False) -> dict:
    assert state.draft is not None, "review requires rewrite to have run first"
    human = (
        f"<job_description>\n{state.job.jd_text.strip()}\n</job_description>\n\n"
        f"<draft>\n{state.draft.model_dump_json(indent=2)}\n</draft>"
    )
    messages = [("system", _prompt()), ("human", human)]

    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            result = _model().invoke(messages)
            if verbose:
                print(f"--- review raw response (attempt {attempt}) ---")
                print(result["raw"].content)
            if result["parsing_error"]:
                raise ValueError(result["parsing_error"])
            verdict = result["parsed"]
            scores = state.scores.model_copy(update={"review_score": verdict.score})
            note = f"review: {verdict.score}/10"
            if verdict.weaknesses:
                note += " — " + "; ".join(verdict.weaknesses)
            # Only feed weaknesses back into validation_errors when review_gate will
            # actually retry — otherwise a proceed-path draft with minor noted
            # weaknesses would wrongly trip score_ats's no_fabrication gate, which
            # reads validation_errors as "did fact-checking fail".
            will_retry = verdict.score < settings.min_review_score and state.attempt_count < settings.max_rewrite_attempts
            return {
                "scores": scores,
                "validation_errors": verdict.weaknesses if will_retry else [],
                "notes": [note],
            }
        except Exception as exc:  # noqa: BLE001 — retried once, then surfaced
            last_error = exc

    raise RuntimeError(f"review failed twice: {last_error}")


def review_gate(state: JobState) -> str:
    """'retry' back to rewrite (attempts remain and score is low), else 'proceed'."""
    score = state.scores.review_score
    if score is not None and score < settings.min_review_score and state.attempt_count < settings.max_rewrite_attempts:
        return "retry"
    return "proceed"
