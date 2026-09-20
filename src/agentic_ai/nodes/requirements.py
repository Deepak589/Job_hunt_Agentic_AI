"""extract_requirements — LLM call 1, Haiku (plan.md §3).

SEES ONLY THE JD. Never the profile (§21.2).

This is load-bearing, not a stylistic preference: a requirement extractor that knows the
candidate's CV finds the requirements it expects them to have, and the gap analysis
downstream is then measuring its own prior instead of the posting. Every coverage number
and the skip gate itself depend on this node being blind to the profile.
"""

from __future__ import annotations

import functools

from pydantic import BaseModel, Field

from ..config import settings
from ..costs import record_usage
from ..llm import make_llm
from ..state import JobState, ReqType, Requirement


class ExtractedRequirement(BaseModel):
    """One requirement as stated in the JD.

    Deliberately narrower than `state.Requirement`: the model never sees the `evidence` or
    `covered` fields, which are filled downstream. Exposing them would put the coverage
    question in front of a node whose entire value is not knowing the answer.
    """

    text: str = Field(description="The requirement, quoted or closely paraphrased from the JD.")
    type: ReqType = Field(description="hard = required, soft = nice-to-have, disqualifier = stated gating condition.")
    keywords: list[str] = Field(
        default_factory=list,
        description="Literal ATS-matchable terms for this requirement, lowercased.",
    )


class RequirementList(BaseModel):
    """emit_requirements — every requirement stated in the job description."""

    requirements: list[ExtractedRequirement] = Field(
        description="One entry per distinct requirement the JD states."
    )


@functools.lru_cache(maxsize=1)
def _prompt() -> str:
    return (settings.prompts_dir / "extract_requirements.md").read_text()


@functools.lru_cache(maxsize=1)
def _model():
    llm = make_llm(settings.extract_model, temperature=0, max_tokens=4096)
    return llm.with_structured_output(RequirementList, include_raw=True)


def extract(jd_text: str, verbose: bool = False) -> tuple[list[Requirement], list[dict]]:
    """JD text -> requirements. One retry, then raise. Second return value is one usage
    record per API call made (including a call whose parse failed and got retried —
    it was still billed)."""
    messages = [
        ("system", _prompt()),
        ("human", f"<job_description>\n{jd_text.strip()}\n</job_description>"),
    ]
    if verbose:
        print("--- extract_requirements prompt ---")
        print(_prompt())
        print(f"--- jd ({len(jd_text)} chars) ---")

    last_error: Exception | None = None
    usage: list[dict] = []
    for attempt in (1, 2):
        try:
            result = _model().invoke(messages)
            usage.append(record_usage(result["raw"], settings.extract_model, "extract_requirements"))
            if verbose:
                print(f"--- raw response (attempt {attempt}) ---")
                print(result["raw"].content)
            if result["parsing_error"]:
                raise ValueError(result["parsing_error"])
            reqs = [Requirement(**r.model_dump()) for r in result["parsed"].requirements]
            if not reqs:
                raise ValueError("model returned zero requirements")
            return reqs, usage
        except Exception as exc:  # noqa: BLE001 — retried once, then surfaced
            last_error = exc
            if verbose:
                print(f"attempt {attempt} failed: {exc}")

    raise RuntimeError(f"extract_requirements failed twice: {last_error}")


def extract_requirements(state: JobState) -> dict:
    """Graph node."""
    reqs, usage = extract(state.job.jd_text)
    counts = {t: sum(1 for r in reqs if r.type == t) for t in ("hard", "soft", "disqualifier")}
    return {
        "requirements": reqs,
        "notes": [f"extracted {len(reqs)} requirements {counts}"],
        "llm_calls": usage,
    }
