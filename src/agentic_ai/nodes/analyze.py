"""analyze — the ONE reasoning call before drafting (replaces extract_requirements,
retrieve_evidence, score_coverage, judge_requirements and diagnose).

Reads the JD and the profile together, like the applicant does in Examples/CLAUDE.md, and
returns requirements with a verdict each, plus matches. Deterministic code then re-checks the
two things a model has got wrong before and the gate cannot afford to: a language below the
profile's level, and an on-site location (coverage.py keeps those rules).
"""

from __future__ import annotations

import functools
import json
from typing import Literal

from pydantic import BaseModel, Field

from ..config import settings
from ..coverage import _is_onsite, _location_reachable, language_requirement_verdict, location_terms
from ..llm import invoke_structured, make_structured
from ..profile import Profile
from ..state import Diagnosis, JobState, ReqType, Requirement, Scores


class AnalyzedRequirement(BaseModel):
    text: str = Field(description="The requirement, quoted or closely paraphrased from the JD.")
    type: ReqType
    keywords: list[str] = Field(default_factory=list, description="Lowercase ATS terms.")
    verdict: Literal["met", "unmet", "unknown"]
    evidence: str = Field(description="One line: the profile fact/bullet id, or the stated absence.")


class AnalysisOut(BaseModel):
    """analyze output."""

    requirements: list[AnalyzedRequirement]
    matches: list[str] = Field(description="What the profile covers well, most JD-relevant first.")
    positioning_mismatch: str | None = None


@functools.lru_cache(maxsize=1)
def _prompt() -> str:
    return (settings.prompts_dir / "analyze.md").read_text()


@functools.lru_cache(maxsize=1)
def _model():
    return make_structured(settings.diagnose_model, AnalysisOut, max_tokens=8192)


def _profile_brief(profile: Profile) -> str:
    raw = profile.raw
    return json.dumps(
        {
            "constraints": raw.get("constraints", {}),
            "identity_location": raw.get("identity", {}).get("location"),
            "education": raw.get("education", []),
            "profile_summary": raw.get("profile_summary", {}),
            "skills": sorted(profile.all_tech()),
            "stack": sorted(profile.all_stack()),
            "bullets": [
                {"id": b.id, "outcome": b.outcome, "metric": b.metric, "method": b.method, "status": b.status}
                for b in profile.bullets
            ],
        },
        indent=2,
        default=str,
    )


# The buckets whose verdict can skip a job: an unmet DISQUALIFIER skips immediately, an
# uncovered HARD joins the `max_blocking_hard_gaps` budget. SOFT only moves a coverage
# number, so re-deciding it would add review questions without preventing anything.
_GATED_TYPES = frozenset({"disqualifier", "hard"})


def _apply_rails(reqs: list[Requirement], job_location: str) -> list[Requirement]:
    """Deterministic overrides on every gate-affecting requirement; the model's verdict
    never wins over these.

    Applied by what a requirement DOES, not by the bucket the model filed it under. This
    used to run only on `type == "disqualifier"`, which made both rails depend on the
    model labelling correctly — and it does not always. `agent_cv`, which types and
    drafts in one call, filed good_jd's "Pullach, nearby Munich" on-site clause and its
    English clause as `hard`. Under the old scoping that silently skipped BOTH rails:
    `_location_reachable` never ran, so the commute question was never asked, and a
    German demand labelled `hard` would have bypassed the language rail entirely and
    landed in the hard-gap budget — the one outcome "a language demand never skips a
    job" (CLAUDE.md, config.max_language_gap_steps) exists to rule out. A rail that only
    fires when the model picks the right noun is not a rail.
    """
    places = location_terms()
    out = []
    for r in reqs:
        if r.type in _GATED_TYPES:
            lang = language_requirement_verdict(r)
            if lang == "unmet":  # demand sits further above the profile than the gap ceiling
                r = r.model_copy(update={"covered": False, "unknown": False, "covered_by": None})
            elif lang == "unknown":  # one step above — ask at review, never skip
                r = r.model_copy(update={"covered": False, "unknown": True, "covered_by": None})
            elif lang == "met":
                r = r.model_copy(update={"covered": True, "unknown": False, "covered_by": "keyword"})
            elif _is_onsite(r):  # decided by where the job is, never by the CV
                reachable = _location_reachable(job_location, places)
                r = r.model_copy(update={
                    "covered": reachable is True, "unknown": reachable is None,
                    "covered_by": "keyword" if reachable else None,
                })
        out.append(r)
    return out


def to_state_update(parsed: AnalysisOut, job_location: str) -> tuple[list[Requirement], Diagnosis, Scores]:
    reqs = [
        Requirement(
            text=a.text, type=a.type, keywords=a.keywords,
            covered=a.verdict == "met", unknown=a.verdict == "unknown",
            covered_by="llm" if a.verdict == "met" else None, matched_term=a.evidence,
        )
        for a in parsed.requirements
    ]
    reqs = _apply_rails(reqs, job_location)
    diagnosis = Diagnosis(
        hard_gaps=[r.text for r in reqs if r.type == "hard" and not r.covered and not r.unknown],
        soft_gaps=[r.text for r in reqs if r.type == "soft" and not r.covered],
        disqualifiers=[r.text for r in reqs if r.type == "disqualifier" and not r.covered and not r.unknown],
        matches=parsed.matches,
        positioning_mismatch=parsed.positioning_mismatch,
    )
    hard = [r for r in reqs if r.type == "hard"]
    soft = [r for r in reqs if r.type == "soft"]
    scores = Scores(
        hard_coverage=sum(r.covered for r in hard) / len(hard) if hard else 1.0,
        soft_coverage=sum(r.covered for r in soft) / len(soft) if soft else 1.0,
    )
    return reqs, diagnosis, scores


def analyze(state: JobState, verbose: bool = False) -> dict:
    if state.requirements and state.diagnosis is not None:  # prefilled (`_prefill`), already paid for
        return {}
    profile = Profile.load()
    messages = [
        ("system", _prompt()),
        (
            "human",
            f"<job_description>\n{state.job.jd_text.strip()}\n</job_description>\n\n"
            f"<candidate_profile>\n{_profile_brief(profile)}\n</candidate_profile>",
        ),
    ]
    parsed, usage = invoke_structured(
        _model(), messages, model=settings.diagnose_model, node="analyze", verbose=verbose
    )
    if not parsed.requirements:
        raise RuntimeError("analyze failed: model returned zero requirements")
    reqs, diagnosis, scores = to_state_update(parsed, state.job.location)
    counts = {t: sum(1 for r in reqs if r.type == t) for t in ("hard", "soft", "disqualifier")}
    return {
        "requirements": reqs,
        "diagnosis": diagnosis,
        "scores": scores,
        "graph_path": "analyze",  # see JobState.graph_path — resume must know which topology wrote this
        "notes": [
            f"analyze: {len(reqs)} requirements {counts}, {len(diagnosis.hard_gaps)} hard gaps, "
            f"{len(state.job.jd_text)} chars JD"
        ],
        "llm_calls": usage,
    }
