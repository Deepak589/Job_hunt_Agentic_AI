"""judge_requirements — LLM second opinion, Sonnet. Runs only on hard/disqualifier
requirements the deterministic keyword/semantic check in `coverage.score_coverage` could
not match.

The deterministic gate stays the first pass for everything (plan.md §6, CLAUDE.md "do not
undo": the hard-gap gate is categorical, never a threshold). This node does not replace it
— it only re-examines the subset that pass would otherwise send straight toward a gap/skip,
the same way `coverage._is_unrecorded_fact`/`_is_onsite` already carve out known blind spots
for a narrower set of cases. Unlike those two, real JD phrasing for a requirement is
open-ended, so a fixed pattern list keeps missing new wording (Bugs.md #2) — this replaces
"match a growing word list" with "ask a model to read the actual facts."

Guardrail: a requirement naming a technology/tool the profile never mentions must come back
`unknown_new_tech`, never `unmet` — that becomes an open question at review
(state.open_questions), not an automatic gap/skip. `unmet` is reserved for a confirmed gap:
an explicit contradiction already in the profile for a DISQUALIFIER (German A1/A2 vs. a
stated C1 requirement stays a real skip, same as today), or a plain, unambiguous domain
mismatch for a HARD requirement.

Deliberate asymmetry downstream (coverage.py, unchanged by this node): a DISQUALIFIER
confirmed `unmet` here still triggers an immediate, uncounted skip via
`state.unmet_disqualifiers()` — `scoring/ats.py`'s final gate forces any unmet disqualifier
to a 0/skip score regardless of draft quality, so there is no point spending a drafting
cycle on it. A HARD requirement confirmed `unmet` instead joins the `max_blocking_hard_gaps`
budget (now 3) — that is the part actually worth attempting a rewrite for.
"""

from __future__ import annotations

import functools
import json
from typing import Literal

from pydantic import BaseModel, Field

from ..config import settings
from ..llm import invoke_structured, make_structured
from ..profile import Profile
from ..state import JobState, Requirement


class RequirementVerdict(BaseModel):
    verdict: Literal["met", "unmet", "unknown_new_tech"]
    reason: str = Field(description="One sentence: the quoted/stated profile fact, or the unrecognized term.")


class VerdictList(BaseModel):
    """emit_verdicts — one verdict per requirement, same order as given."""

    verdicts: list[RequirementVerdict]


@functools.lru_cache(maxsize=1)
def _prompt() -> str:
    return (settings.prompts_dir / "judge_requirements.md").read_text()


@functools.lru_cache(maxsize=1)
def _model():
    return make_structured(settings.requirement_judge_model, VerdictList, max_tokens=4096)


def _profile_facts(profile: Profile) -> str:
    raw = profile.raw
    return json.dumps(
        {
            "constraints": raw.get("constraints", {}),
            "identity_location": raw.get("identity", {}).get("location"),
            "education": raw.get("education", []),
            "profile_summary": raw.get("profile_summary", {}),
            "skills": sorted(profile.all_tech()),
            "stack": sorted(profile.all_stack()),
            # Bullet text, not just names — a skill phrased as free text in a proof point
            # or a bullet's outcome/method is real evidence a names-only list would miss
            # (the same class of gap Bugs.md #1 found in the fact validator).
            "bullets": [
                {"id": b.id, "outcome": b.outcome, "metric": b.metric, "method": b.method}
                for b in profile.bullets
            ],
        },
        indent=2,
        default=str,
    )


def judge_requirements(state: JobState, verbose: bool = False) -> dict:
    """Graph node. No-op if no hard/disqualifier requirement was left uncovered by the
    deterministic pass."""
    to_judge = [
        r for r in state.requirements
        if r.type in ("hard", "disqualifier") and not r.covered and not r.unknown
    ]
    if not to_judge:
        return {}

    profile = Profile.load()
    messages = [
        ("system", _prompt()),
        (
            "human",
            f"<profile_facts>\n{_profile_facts(profile)}\n</profile_facts>\n\n"
            f"<requirements>\n"
            + "\n".join(f"{i + 1}. [{r.type}] {r.text}" for i, r in enumerate(to_judge))
            + "\n</requirements>",
        ),
    ]
    parsed, usage = invoke_structured(
        _model(), messages, model=settings.requirement_judge_model, node="judge_requirements", verbose=verbose
    )

    verdicts = parsed.verdicts
    if len(verdicts) != len(to_judge):
        # A length mismatch means the model dropped or merged an item — cannot be safely
        # mapped back by position, so this pass is skipped rather than guessed; the
        # deterministic result (uncovered) stands and the job gates as it would have
        # before this node existed.
        return {
            "notes": [f"judge_requirements: verdict count {len(verdicts)} != {len(to_judge)} items, skipped"],
            "llm_calls": usage,
        }

    by_text = {r.text: v for r, v in zip(to_judge, verdicts)}
    updated: list[Requirement] = []
    notes: list[str] = []
    for r in state.requirements:
        v = by_text.get(r.text)
        if v is None:
            updated.append(r)
            continue
        if v.verdict == "met":
            r = r.model_copy(update={"covered": True, "covered_by": "llm", "matched_term": v.reason})
            notes.append(f"judge_requirements: MET [{r.type}] — {r.text!r}: {v.reason}")
        elif v.verdict == "unknown_new_tech":
            r = r.model_copy(update={"unknown": True})
            notes.append(f"judge_requirements: UNKNOWN (ask) [{r.type}] — {r.text!r}: {v.reason}")
        else:
            notes.append(f"judge_requirements: UNMET (confirmed gap) [{r.type}] — {r.text!r}: {v.reason}")
        updated.append(r)

    return {"requirements": updated, "notes": notes, "llm_calls": usage}
