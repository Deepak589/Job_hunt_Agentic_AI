"""agent_cv — the single-call baseline path (NOT part of the graph).

One Sonnet call does all five roles of `Examples/CLAUDE.md` — diagnose, rewrite, review,
recruiter screen, hiring-manager verdict — reading the JD and the profile together and
returning a finished package. It exists to answer one question with numbers instead of
impressions: does the manual flow's single reasoning pass beat the graph on the same JDs?

What stays deterministic, because these are the parts a model has got wrong before and the
comparison must not be allowed to cheat on:
  * the language and on-site rails (`analyze._apply_rails`) still override the model's
    verdict on every disqualifier;
  * `validate_facts` still runs, in REPORT mode — errors are counted and printed, never
    used to block. Blocking is what the graph does; the point here is to measure how many
    errors a judgment-first path actually produces.

Nothing in this module is imported by `graph.py`. Deleting it leaves the pipeline unchanged.
"""

from __future__ import annotations

import functools
import json
from typing import Literal

from pydantic import BaseModel, Field

from ..config import settings
from ..llm import invoke_structured, make_structured
from ..profile import Profile
from ..state import Diagnosis, Draft, DraftBullet, DraftOut, Requirement, Scores
from .analyze import AnalysisOut, AnalyzedRequirement, _profile_brief, to_state_update
from .rewrite import _to_capped_draft


class AgentCvOut(BaseModel):
    """Everything the five roles produce, in one response."""

    # role 1 — diagnosis
    requirements: list[AnalyzedRequirement]
    matches: list[str] = Field(default_factory=list, description="What the profile covers well, most JD-relevant first.")
    positioning_mismatch: str | None = None
    # role 2 — rewriter
    profile_line: str
    section_order: list[str]
    bullets: list[DraftBullet] = Field(description="Every profile bullet, reordered by JD relevance.")
    cover_letter: str
    highlighted_projects: list[str] = Field(default_factory=list)
    # roles 3-5 — reviewer, recruiter, hiring manager
    self_score: int = Field(ge=1, le=10, description="Reviewer score out of 10.")
    recruiter_verdict: Literal["pass", "soft_fail", "hard_fail"]
    final_verdict: Literal["apply", "fix_then_apply", "skip"]
    verdict_reason: str


class AgentCvResult(BaseModel):
    """`agent_cv` output, shaped so the comparison can read the same fields off both paths."""

    draft: Draft
    requirements: list[Requirement]
    diagnosis: Diagnosis
    scores: Scores
    self_score: int
    recruiter_verdict: str
    final_verdict: str
    verdict_reason: str
    llm_calls: list[dict]

    @property
    def total_cost_usd(self) -> float:
        return round(sum(c["cost_usd"] for c in self.llm_calls), 6)


@functools.lru_cache(maxsize=1)
def _prompt() -> str:
    return (settings.prompts_dir / "agent_cv.md").read_text()


@functools.lru_cache(maxsize=1)
def _model():
    # One call carries diagnosis + full draft + cover letter + three verdicts, so it needs
    # the same headroom as rewrite, not analyze's 8k.
    return make_structured(settings.rewrite_model, AgentCvOut, max_tokens=16384)


def _profile_bullets_json(profile: Profile) -> str:
    """Full bullet records. `_profile_brief` alone is not enough here: this call also
    rewrites, so it needs every bullet's tags and parent_id, not just the summary view."""
    return json.dumps(
        [
            {"id": b.id, "parent_id": b.parent_id, "outcome": b.outcome, "metric": b.metric,
             "method": b.method, "status": b.status, "tags": b.tags}
            for b in profile.bullets
        ],
        indent=2,
    )


def agent_cv(jd_text: str, job_location: str = "", verbose: bool = False) -> AgentCvResult:
    profile = Profile.load()
    # JD first, then profile — same ordering as `analyze`, for the same reason: the
    # requirement list must come from the posting, not from what the candidate happens to
    # have (repo CLAUDE.md, "Deliberate reversal of the old extractor rule").
    messages = [
        ("system", _prompt()),
        (
            "human",
            f"<job_description>\n{jd_text.strip()}\n</job_description>\n\n"
            f"<candidate_profile>\n{_profile_brief(profile)}\n</candidate_profile>\n\n"
            f"<candidate_profile_bullets>\n{_profile_bullets_json(profile)}\n</candidate_profile_bullets>\n\n"
            f"<never_claim>\n{profile.never_claim}\n</never_claim>",
        ),
    ]
    parsed, usage = invoke_structured(
        _model(), messages, model=settings.rewrite_model, node="agent_cv", verbose=verbose
    )
    if not parsed.requirements:
        raise RuntimeError("agent_cv failed: model returned zero requirements")

    # Same deterministic post-processing the graph applies, so the two paths' numbers mean
    # the same thing: rails override disqualifier verdicts, sections come from the profile.
    reqs, diagnosis, scores = to_state_update(
        AnalysisOut(
            requirements=parsed.requirements,
            matches=parsed.matches,
            positioning_mismatch=parsed.positioning_mismatch,
        ),
        job_location,
    )
    # DraftOut, not `parsed` directly: `_to_capped_draft` is typed for it, and building one
    # here keeps rewrite.py untouched and `mypy --strict` happy.
    draft = _to_capped_draft(
        DraftOut(
            profile_line=parsed.profile_line,
            section_order=parsed.section_order,
            bullets=parsed.bullets,
            cover_letter=parsed.cover_letter,
            highlighted_projects=parsed.highlighted_projects,
        ),
        profile,
        parsed.section_order,
    )
    return AgentCvResult(
        draft=draft,
        requirements=reqs,
        diagnosis=diagnosis,
        scores=scores,
        self_score=parsed.self_score,
        recruiter_verdict=parsed.recruiter_verdict,
        final_verdict=parsed.final_verdict,
        verdict_reason=parsed.verdict_reason,
        llm_calls=usage,
    )
