"""Typed state threaded through tailor_graph (plan.md §2).

Phase 1 fields only. `Diagnosis` and `Draft` are in §2 but nothing in Phase 1 writes
them; they arrive with the rewriter in Phase 2.
"""

from __future__ import annotations

import operator
from typing import Annotated, Literal

from pydantic import BaseModel, Field

ReqType = Literal["hard", "soft", "disqualifier"]
EvidenceSource = Literal["cv_bullet", "project", "repo_doc", "education"]


class Evidence(BaseModel):
    """One retrieved chunk supporting a requirement."""

    source: EvidenceSource
    source_id: str  # e.g. "exp.valuemomentum.b2" or "repo:RAG_pipeline"
    text: str
    similarity: float
    # Carried through from the profile: "not_shipped" work may legitimately evidence a
    # skill, but must never be phrased as delivered. Losing the flag here is how that
    # bullet reaches a CV as a shipped claim.
    status: str = ""


class Requirement(BaseModel):
    text: str
    type: ReqType
    keywords: list[str] = []
    evidence: list[Evidence] = []  # filled by retrieve_evidence
    covered: bool = False  # filled by score_coverage
    covered_by: Literal["semantic", "keyword", None] = None  # which signal fired
    matched_term: str = ""  # the profile term a keyword hit matched; "" for semantic
    # A personal fact the profile simply does not record — a semester count, a grade
    # average. Absent evidence is not counter-evidence: treating it as failed skipped a
    # job at 100% hard coverage. Ask, don't skip.
    unknown: bool = False

    @property
    def best_similarity(self) -> float:
        return max((e.similarity for e in self.evidence), default=0.0)


class Job(BaseModel):
    id: str  # content_hash
    source: str  # adzuna | arbeitnow | manual
    url: str = ""
    title: str
    company: str = ""  # a manual --file paste may not carry one; CLI can pass --company
    location: str = ""
    posted_at: str | None = None
    jd_text: str
    lang: Literal["en", "de", "mixed"] = "en"
    employment_type: str | None = None  # werkstudent | fulltime | intern | unknown


class Scores(BaseModel):
    hard_coverage: float = 0.0  # 0..1, deterministic
    soft_coverage: float = 0.0  # 0..1, deterministic
    semantic_fit: float = 0.0  # 0..1, mean best-similarity across requirements
    # Phase 2+: review_score, recruiter, verdict


class JobState(BaseModel):
    job: Job
    requirements: list[Requirement] = []
    scores: Scores = Field(default_factory=Scores)
    skip_reason: str | None = None  # set by hard_gap_gate → log_skip
    # LangGraph needs an explicit reducer for any field multiple nodes append to,
    # otherwise concurrent writes silently overwrite (plan.md §2).
    notes: Annotated[list[str], operator.add] = []

    def hard(self) -> list[Requirement]:
        return [r for r in self.requirements if r.type == "hard"]

    def soft(self) -> list[Requirement]:
        return [r for r in self.requirements if r.type == "soft"]

    def uncovered_hard(self) -> list[Requirement]:
        return [r for r in self.hard() if not r.covered]

    def unmet_disqualifiers(self) -> list[Requirement]:
        """Disqualifiers block only when uncovered.

        "Requires C1 German" with no German evidence is blocking; "must be eligible to
        work in the EU" matched against the profile's constraints is not. Reusing the
        coverage signal keeps that distinction in one place.

        A disqualifier flagged `unknown` is neither met nor unmet — see `open_questions`.
        """
        return [
            r for r in self.requirements
            if r.type == "disqualifier" and not r.covered and not r.unknown
        ]

    def open_questions(self) -> list[Requirement]:
        """Disqualifiers the profile holds no fact for. Answerable only by Deepak."""
        return [r for r in self.requirements if r.unknown]
