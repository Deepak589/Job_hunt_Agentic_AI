"""extract_requirements / diagnose skip their LLM call when the field they'd fill is
already populated on entry (solution.md step 7 — batch mode prefills these via
`graph.run`/`run_many`'s `_prefill` kwarg). Neither node has a retry edge back to
itself in the graph, so this is safe for the live single-job path too — it always
starts with both fields empty."""

from __future__ import annotations

from agentic_ai.nodes import diagnose as diagnose_mod
from agentic_ai.nodes import requirements as requirements_mod
from agentic_ai.state import Diagnosis, Job, JobState, Requirement


def _raise_if_called(*a, **kw):
    raise AssertionError("_model() should not be called when the field is already prefilled")


def _job() -> Job:
    return Job(id="j1", source="manual", title="t", jd_text="We need Python.")


def test_extract_requirements_skips_when_requirements_already_set(monkeypatch) -> None:
    monkeypatch.setattr(requirements_mod, "_model", _raise_if_called)
    state = JobState(job=_job(), requirements=[Requirement(text="Python", type="hard")])

    result = requirements_mod.extract_requirements(state)

    assert result == {}


def test_diagnose_skips_when_diagnosis_already_set(monkeypatch) -> None:
    monkeypatch.setattr(diagnose_mod, "_model", _raise_if_called)
    state = JobState(job=_job(), diagnosis=Diagnosis(matches=["stub"]))

    result = diagnose_mod.diagnose(state)

    assert result == {}
