"""agent_cv — the ONE reasoning call that both diagnoses and drafts (graph node).

One Sonnet call does all five roles of `Examples/CLAUDE.md` — diagnose, rewrite, review,
recruiter screen, hiring-manager verdict — reading the JD and the profile together and
returning a finished package.

It started as a side path (`jobpilot agent-draft`) whose job was to answer one question
with numbers: does the manual flow's single reasoning pass beat `analyze` + `rewrite`? It
drafted better and cost less. But measured as a PIPELINE it was not one — it reached none
of the machinery the graph already had, and it ran `validate_facts` in REPORT mode, which
looks like a gate and is not: on `good_jd` it reported 5 fact errors and still returned
`apply`. A report-only validator cannot tell a benign umbrella term from a real
fabrication, and these documents go out with the candidate's name on them.

So the call moved INSIDE the graph (`graph.build_graph`, `settings.use_agent_cv`) rather
than the graph's guarantees being rebuilt around the call. `agent_cv_node` below replaces
`analyze` + the first `rewrite`; everything downstream is untouched and therefore still
true of this path: the categorical hard-gap gate, BLOCKING `validate_facts`, the
`interrupt()` review checkpoint, `record_run`, dedupe, the cost ledger, `preferences.yaml`
and MCP review visibility.

What stays deterministic, because these are the parts a model has got wrong before:
  * the language and on-site rails (`analyze._apply_rails`) override the model's verdict on
    every gate-affecting requirement — by what the requirement does, NOT by the bucket this
    call filed it under, which is the bug that scoping them to disqualifiers hid;
  * `validate_facts` blocks, with the rewrite-retry loop behind it.

`jobpilot agent-draft` still exists and still reports instead of blocking — it is the
comparison harness, and the thing being compared must not change underneath it.
"""

from __future__ import annotations

import functools
import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..config import settings
from ..llm import invoke_structured, make_structured
from ..profile import Profile
from ..state import (
    AtsVerdict,
    Diagnosis,
    Draft,
    DraftBullet,
    DraftOut,
    HiringManagerVerdict,
    JobState,
    RecruiterResult,
    Requirement,
    Scores,
)
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
    # Narrower than `str` on purpose: these are handed straight to `RecruiterResult` and
    # `HiringManagerVerdict`, whose fields are Literals. Widening them here would push a
    # `type: ignore` into the node.
    recruiter_verdict: Literal["pass", "soft_fail", "hard_fail"]
    final_verdict: AtsVerdict
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


def agent_cv_node(state: JobState, verbose: bool = False) -> dict[str, Any]:
    """`agent_cv` as a graph node — replaces `analyze` and the FIRST `rewrite` in one call.

    Returns the union of what those two nodes returned, so nothing downstream had to
    change: `hard_gap_gate` reads `requirements`, `validate_facts` reads `draft`,
    `human_review` reads `draft` + `diagnosis`, `render_documents` and `score_ats` read
    what they always did.

    The three judgments only this path produces already have homes in `JobState`, so they
    reach the review artifact instead of being printed to a terminal and lost:
    `self_score` -> `scores.review_score`, `recruiter_verdict` -> `recruiter`,
    `final_verdict` -> `hiring_manager`. The schema returns one `verdict_reason`, so both
    of the latter two carry it; that is the model's own single explanation, not a copy
    pretending to be two opinions.

    `attempt_count` is incremented here because this call PRODUCED a draft — the retry
    budget (`max_rewrite_attempts`) must count it, or a fact error would get one more
    paid attempt on this path than on the `analyze` path.

    Retries deliberately do NOT come back here. `fact_gate` routes a fact error to
    `rewrite`, which receives the diagnosis and the exact errors and fixes only what they
    name. Re-running this node instead would re-pay for requirement extraction and could
    return a DIFFERENT requirement list after `hard_gap_gate` had already passed the first
    one — a gate decision silently invalidated by its own retry.
    """
    if state.requirements and state.diagnosis is not None and state.draft is not None:
        return {}  # prefilled (`_prefill`), already paid for

    result = agent_cv(state.job.jd_text, job_location=state.job.location, verbose=verbose)
    counts = {t: sum(1 for r in result.requirements if r.type == t) for t in ("hard", "soft", "disqualifier")}
    bullet_count = sum(len(v) for v in result.draft.bullets.values())
    return {
        "requirements": result.requirements,
        "diagnosis": result.diagnosis,
        "scores": result.scores.model_copy(update={"review_score": result.self_score}),
        "draft": result.draft,
        "attempt_count": state.attempt_count + 1,
        "recruiter": RecruiterResult(result=result.recruiter_verdict, reason=result.verdict_reason),
        "hiring_manager": HiringManagerVerdict(verdict=result.final_verdict, why=result.verdict_reason),
        # Recorded so `graph._resume` can rebuild the topology that WROTE a checkpoint:
        # `aget_state` silently omits a pending task whose node the probe graph does not
        # register, so probing an agent_cv checkpoint with an analyze graph reports it
        # finished. None means the analyze path (every checkpoint written before this).
        "graph_path": "agent_cv",
        "notes": [
            f"agent_cv: {len(result.requirements)} requirements {counts}, "
            f"{len(result.diagnosis.hard_gaps)} hard gaps, {bullet_count} bullets, "
            f"self_score {result.self_score}/10, recruiter {result.recruiter_verdict}, "
            f"verdict {result.final_verdict}"
        ],
        "llm_calls": result.llm_calls,
    }
