"""validate_facts graph node — thin wrapper over validators.facts (plan.md §3, §8).

The routing decision (retry vs. give up) lives here, not in the pure validator, because
it needs `state.attempt_count` — a graph concern, not a fact-checking concern.
"""

from __future__ import annotations

import re
from typing import Literal

from ..config import settings
from ..coverage import _is_onsite, language_requirement_verdict
from ..profile import Profile
from ..state import JobState, Requirement
from ..validators.facts import validate_facts


# `coverage._is_onsite` does not match `analyze`'s plainest paraphrase of a location
# clause — "Location: Pullach, nearby Munich, Germany" — because none of its patterns
# cover a bare leading "Location:". That is a real hole in the GATE too (the on-site rail
# never fires on that phrasing, so `_location_reachable` never runs), but widening
# ONSITE_PATTERNS changes which jobs skip, and a Munich posting becoming a skip is a
# decision about where he will commute, not a cleanup. So this regex is deliberately
# scoped to the fact checker, where narrowing what gets policed can only remove false
# positives and can never pass or skip a job.
_LOCATION_REQ_RE = re.compile(
    r"^\s*(location|standort|arbeitsort|work location|office location|based in|on[- ]?site)\b",
    re.IGNORECASE,
)


def _rail_owned(req: Requirement) -> bool:
    """A requirement whose verdict the deterministic rails decide, not the fact-checker.

    A language demand and an on-site clause are settled by `coverage.py` against the
    profile's own level and the job's own location. Their keywords are places and language
    names, never fabricable tech, so feeding them to the tech-fabrication check only
    produces false positives — and `agent_cv` is what exposed this: it files such clauses
    as `hard`, where the old code's exclusion-by-bucket ("a DISQUALIFIER is already proven
    true by `hard_gap_gate`") no longer catches them. On good_jd that flagged
    `cover_letter: claims unevidenced tech 'pullach'` for naming the office town — the
    one thing a cover letter obviously should do. Same principle as `analyze._GATED_TYPES`:
    decide by what the requirement IS, not by the bucket the model gave it.
    """
    return (
        _is_onsite(req)
        or _LOCATION_REQ_RE.match(req.text) is not None
        or language_requirement_verdict(req) is not None
    )


def hard_keywords_for_facts(requirements: list[Requirement]) -> list[str]:
    """The keyword set `validate_facts` is allowed to police, derived in ONE place.

    Three callers need it identically — this node, `graph.human_review`'s "edit" branch,
    and the `agent-draft` comparison harness. It was inlined in all three, so a fix to one
    silently left the others reporting fabrications the pipeline would not.
    """
    return [kw for r in requirements if r.type == "hard" and not _rail_owned(r) for kw in r.keywords]


def validate_facts_node(state: JobState) -> dict:
    assert state.draft is not None, "validate_facts requires rewrite to have run first"
    profile = Profile.load()
    # Only hard keywords. A soft nice-to-have is the generic, non-committal kind of term
    # ("data", "learning") an extractor tags loosely — checking it at hard-requirement
    # severity produced false positives in live testing. A disqualifier's keywords are
    # never checked here either: by the time a draft exists, `hard_gap_gate` has already
    # sent every unmet disqualifier to skip/END (state.py: "met at the gate already").
    # Re-checking them here for fabrication is redundant by construction, and the word-list
    # needed to tell a real tech disqualifier ("Kubernetes") apart from a bare eligibility
    # keyword the profile already states ("degree", "enrolled") just keeps growing one
    # word at a time as new JDs use new phrasing — the kind of fix that never finishes.
    errors = validate_facts(
        state.draft, profile, jd_keywords=hard_keywords_for_facts(state.requirements)
    )
    return {
        "validation_errors": errors,
        "notes": [f"validate_facts: {len(errors)} error(s)" if errors else "validate_facts: clean"],
    }


def fact_gate(state: JobState) -> Literal["retry", "clean", "give_up"]:
    if not state.validation_errors:
        return "clean"
    if state.attempt_count < settings.max_rewrite_attempts:
        return "retry"
    return "give_up"


def log_fact_failure(state: JobState) -> dict:
    """A draft that never passed fact-checking must not reach render_documents."""
    reason = (
        f"fact validation failed after {state.attempt_count} rewrite attempt(s): "
        + "; ".join(state.validation_errors)
    )
    return {"skip_reason": reason, "notes": [f"SKIP — {reason}"]}
