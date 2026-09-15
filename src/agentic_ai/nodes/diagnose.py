"""diagnose — LLM call 2, Sonnet (plan.md §3, §7.1). Sees the full profile — unlike
extract_requirements, this node's entire job is comparing JD demands against a real CV.
"""

from __future__ import annotations

import functools
import json

from langchain_anthropic import ChatAnthropic

from ..config import settings
from ..profile import Profile
from ..state import Diagnosis, JobState


@functools.lru_cache(maxsize=1)
def _prompt() -> str:
    return (settings.prompts_dir / "diagnose.md").read_text()


@functools.lru_cache(maxsize=1)
def _model():
    # claude-sonnet-5 rejects an explicit `temperature` — the param is deprecated for
    # this model (confirmed live: "`temperature` is deprecated for this model").
    llm = ChatAnthropic(model=settings.diagnose_model, max_tokens=4096)
    return llm.with_structured_output(Diagnosis, include_raw=True)


def _requirements_brief(state: JobState) -> str:
    rows = []
    for r in state.requirements:
        ev = ", ".join(f"{e.source_id} (sim={e.similarity:.3f})" for e in r.evidence[:3]) or "none retrieved"
        rows.append(f"- [{r.type}] {r.text}\n  gate coverage: {'covered' if r.covered else 'uncovered'} via {r.covered_by or '—'}\n  top evidence: {ev}")
    return "\n".join(rows)


def _profile_brief(profile: Profile) -> str:
    return json.dumps(
        {
            "skills": sorted(profile.all_tech()),
            "bullets": [
                {"id": b.id, "outcome": b.outcome, "metric": b.metric, "method": b.method, "status": b.status}
                for b in profile.bullets
            ],
        },
        indent=2,
    )


def diagnose(state: JobState, verbose: bool = False) -> dict:
    profile = Profile.load()
    human = (
        f"<job_description>\n{state.job.jd_text.strip()}\n</job_description>\n\n"
        f"<extracted_requirements>\n{_requirements_brief(state)}\n</extracted_requirements>\n\n"
        f"<candidate_profile>\n{_profile_brief(profile)}\n</candidate_profile>"
    )
    messages = [("system", _prompt()), ("human", human)]

    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            result = _model().invoke(messages)
            if verbose:
                print(f"--- diagnose raw response (attempt {attempt}) ---")
                print(result["raw"].content)
            if result["parsing_error"]:
                raise ValueError(result["parsing_error"])
            diagnosis = result["parsed"]
            return {
                "diagnosis": diagnosis,
                "notes": [
                    f"diagnose: {len(diagnosis.hard_gaps)} hard gaps, "
                    f"{len(diagnosis.matches)} matches, "
                    f"positioning={'mismatch' if diagnosis.positioning_mismatch else 'ok'}"
                ],
            }
        except Exception as exc:  # noqa: BLE001 — retried once, then surfaced
            last_error = exc

    raise RuntimeError(f"diagnose failed twice: {last_error}")
