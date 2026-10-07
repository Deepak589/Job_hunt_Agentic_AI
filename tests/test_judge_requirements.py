"""judge_requirements — LLM second opinion on hard/disqualifier requirements the
deterministic keyword/semantic check could not match (Bugs.md #2 follow-up, broadened
from disqualifier-only to also cover hard requirements).

No live API calls: `_model()` is monkeypatched with a fake structured runnable, the
same pattern `test_llm.py` uses for `invoke_structured`.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from agentic_ai.nodes import judge_requirements as jr
from agentic_ai.state import Job, JobState, Requirement


class _FakeStructured:
    def __init__(self, parsed) -> None:
        self._parsed = parsed

    def invoke(self, messages):
        return {
            "raw": AIMessage(content="x", response_metadata={"stop_reason": "end_turn"}),
            "parsed": self._parsed,
            "parsing_error": None,
        }


def _state_with(reqs: list[Requirement]) -> JobState:
    job = Job(id="t", source="manual", title="Test Role", jd_text="...")
    return JobState(job=job, requirements=reqs)


def test_covered_requirements_skip_the_llm_call_entirely(monkeypatch) -> None:
    """Nothing uncovered of either type -> no reason to spend a call."""
    def _boom():
        raise AssertionError("must not call the model when nothing needs judging")

    monkeypatch.setattr(jr, "_model", _boom)
    hard = Requirement(text="Python", type="hard", keywords=["python"], covered=True)
    disq = Requirement(text="must be EU-eligible", type="disqualifier", keywords=["eu"], covered=True)
    out = jr.judge_requirements(_state_with([hard, disq]))
    assert out == {}


def test_soft_requirements_are_never_sent_to_the_llm(monkeypatch) -> None:
    def _boom():
        raise AssertionError("soft requirements must never reach the LLM judge")

    monkeypatch.setattr(jr, "_model", _boom)
    soft = Requirement(text="nice to have: fintech", type="soft", keywords=["fintech"], covered=False)
    out = jr.judge_requirements(_state_with([soft]))
    assert out == {}


def test_unknown_new_tech_disqualifier_becomes_an_open_question_not_a_skip(monkeypatch) -> None:
    verdict = jr.VerdictList(verdicts=[jr.RequirementVerdict(verdict="unknown_new_tech", reason="Terraform not in profile")])
    monkeypatch.setattr(jr, "_model", lambda: _FakeStructured(verdict))
    req = Requirement(text="requires: Terraform", type="disqualifier", keywords=["terraform"], covered=False)
    out = jr.judge_requirements(_state_with([req]))
    updated = out["requirements"][0]
    assert updated.unknown is True
    assert updated.covered is False


def test_real_contradiction_disqualifier_stays_unmet_and_still_blocks(monkeypatch) -> None:
    """German A1/A2 vs a stated C1 requirement: a real, quotable contradiction — must stay
    a skip, not get asked about."""
    verdict = jr.VerdictList(verdicts=[jr.RequirementVerdict(verdict="unmet", reason="profile states German A1/A2, JD requires C1")])
    monkeypatch.setattr(jr, "_model", lambda: _FakeStructured(verdict))
    req = Requirement(text="fluent German (C1) required", type="disqualifier", keywords=["german"], covered=False)
    state = _state_with([req])
    out = jr.judge_requirements(state)
    updated = out["requirements"][0]
    assert updated.covered is False and updated.unknown is False
    result_state = state.model_copy(update={"requirements": out["requirements"]})
    assert result_state.unmet_disqualifiers() == [updated]


def test_disqualifier_met_by_paraphrase_is_marked_covered_via_llm(monkeypatch) -> None:
    verdict = jr.VerdictList(verdicts=[jr.RequirementVerdict(verdict="met", reason="profile states German citizenship")])
    monkeypatch.setattr(jr, "_model", lambda: _FakeStructured(verdict))
    req = Requirement(text="EU work authorization required", type="disqualifier", keywords=["eu"], covered=False)
    out = jr.judge_requirements(_state_with([req]))
    updated = out["requirements"][0]
    assert updated.covered is True
    assert updated.covered_by == "llm"


def test_hard_requirement_met_by_paraphrase_is_marked_covered_via_llm(monkeypatch) -> None:
    verdict = jr.VerdictList(
        verdicts=[jr.RequirementVerdict(verdict="met", reason="profile states owned Kubernetes/CI-CD/AWS layer")]
    )
    monkeypatch.setattr(jr, "_model", lambda: _FakeStructured(verdict))
    req = Requirement(text="cloud-native infrastructure experience", type="hard", keywords=["cloud-native"], covered=False)
    out = jr.judge_requirements(_state_with([req]))
    updated = out["requirements"][0]
    assert updated.covered is True
    assert updated.covered_by == "llm"


def test_hard_requirement_unknown_new_tech_becomes_an_open_question_not_a_gap(monkeypatch) -> None:
    """New behavior: a hard requirement naming unrecognized tech no longer silently counts
    toward the blocking-gap budget — it asks, same as a disqualifier would."""
    from agentic_ai.coverage import gate_reason

    verdict = jr.VerdictList(verdicts=[jr.RequirementVerdict(verdict="unknown_new_tech", reason="Rust not in profile")])
    monkeypatch.setattr(jr, "_model", lambda: _FakeStructured(verdict))
    req = Requirement(text="5+ years of Rust", type="hard", keywords=["rust"], covered=False)
    state = _state_with([req])
    out = jr.judge_requirements(state)
    updated = out["requirements"][0]
    assert updated.covered is False and updated.unknown is True
    result_state = state.model_copy(update={"requirements": out["requirements"]})
    assert gate_reason(result_state) is None, "an unknown gap must not block the gate"


def test_hard_requirement_confirmed_unmet_leaves_covered_false_for_the_gate_to_count(monkeypatch) -> None:
    verdict = jr.VerdictList(
        verdicts=[jr.RequirementVerdict(verdict="unmet", reason="profile has no clinical nursing experience")]
    )
    monkeypatch.setattr(jr, "_model", lambda: _FakeStructured(verdict))
    req = Requirement(text="clinical nursing experience", type="hard", keywords=["nursing"], covered=False)
    out = jr.judge_requirements(_state_with([req]))
    updated = out["requirements"][0]
    assert updated.covered is False and updated.unknown is False
