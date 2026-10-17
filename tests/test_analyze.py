"""analyze — the single reasoning call. The model is stubbed; what is under test is the
deterministic part: verdict -> state mapping, the language/on-site safety rails, and that
the hard-gap gate reads the result correctly."""

from __future__ import annotations

import pytest

from agentic_ai.coverage import hard_gap_gate
from agentic_ai.nodes import analyze as analyze_mod
from agentic_ai.nodes.analyze import AnalysisOut, AnalyzedRequirement, to_state_update
from agentic_ai.state import Diagnosis, Job, JobState, Requirement


def _req(text, type_, verdict, evidence="profile fact"):
    return AnalyzedRequirement(text=text, type=type_, keywords=[text.lower()], verdict=verdict, evidence=evidence)


@pytest.fixture(autouse=True)
def _no_profile_rails(monkeypatch):
    """Rails read the private profile; neutralise by default, individual tests re-enable."""
    monkeypatch.setattr(analyze_mod, "language_requirement_verdict", lambda r: None)
    monkeypatch.setattr(analyze_mod, "location_terms", lambda: frozenset({"berlin", "munich"}))


def test_verdicts_map_to_covered_and_unknown_flags() -> None:
    parsed = AnalysisOut(
        requirements=[
            _req("Python", "hard", "met", "proj.rag.b1"),
            _req("Kubernetes", "hard", "unmet", "no k8s anywhere"),
            _req("Snowflake", "hard", "unknown"),
            _req("FastAPI", "soft", "unmet"),
        ],
        matches=["RAG pipeline"],
    )
    reqs, diagnosis, scores = to_state_update(parsed, "Berlin")

    py, k8s, snow, fapi = reqs
    assert py.covered and py.covered_by == "llm" and py.matched_term == "proj.rag.b1"
    assert not k8s.covered and not k8s.unknown
    assert snow.unknown and not snow.covered
    assert diagnosis.hard_gaps == ["Kubernetes"]  # unknown is a question, not a gap
    assert diagnosis.soft_gaps == ["FastAPI"]
    assert diagnosis.matches == ["RAG pipeline"]
    assert scores.hard_coverage == pytest.approx(1 / 3)
    assert scores.soft_coverage == 0.0


def test_unknown_disqualifier_is_a_question_not_a_skip() -> None:
    parsed = AnalysisOut(requirements=[_req("Security clearance", "disqualifier", "unknown")], matches=[])
    reqs, _, _ = to_state_update(parsed, "Berlin")
    state = JobState(job=Job(id="j", source="manual", title="t", jd_text="x"), requirements=reqs)

    assert state.unmet_disqualifiers() == []
    assert [q.text for q in state.open_questions()] == ["Security clearance"]
    assert hard_gap_gate(state) == "proceed"


def test_unmet_disqualifier_skips() -> None:
    parsed = AnalysisOut(requirements=[_req("C1 German", "disqualifier", "unmet", "German A2")], matches=[])
    reqs, diagnosis, _ = to_state_update(parsed, "Berlin")
    state = JobState(job=Job(id="j", source="manual", title="t", jd_text="x"), requirements=reqs)

    assert diagnosis.disqualifiers == ["C1 German"]
    assert hard_gap_gate(state) == "skip"


def test_too_many_hard_gaps_skip_but_few_do_not() -> None:
    few = AnalysisOut(requirements=[_req(f"Tool{i}", "hard", "unmet") for i in range(3)], matches=[])
    many = AnalysisOut(requirements=[_req(f"Tool{i}", "hard", "unmet") for i in range(4)], matches=[])
    job = Job(id="j", source="manual", title="t", jd_text="x")

    assert hard_gap_gate(JobState(job=job, requirements=to_state_update(few, "")[0])) == "proceed"
    assert hard_gap_gate(JobState(job=job, requirements=to_state_update(many, "")[0])) == "skip"


def test_unmet_language_overrides_a_model_met(monkeypatch) -> None:
    """A demand further above the profile than the gap ceiling is a skip, model be damned."""
    monkeypatch.setattr(analyze_mod, "language_requirement_verdict", lambda r: "unmet")
    parsed = AnalysisOut(requirements=[_req("Fluent German and English", "disqualifier", "met")], matches=[])
    [r], _, _ = to_state_update(parsed, "Berlin")

    assert not r.covered and not r.unknown


def test_language_one_step_above_profile_asks_instead_of_skipping(monkeypatch) -> None:
    """"gute Deutschkenntnisse" against A2+B1-in-progress: a review question, not a skip.

    The old binary rail made every German demand a hard gate, which skipped postings the
    candidate can honestly answer with "A2 completed, working toward B1".
    """
    monkeypatch.setattr(analyze_mod, "language_requirement_verdict", lambda r: "unknown")
    parsed = AnalysisOut(
        requirements=[_req("Gute Deutschkenntnisse", "disqualifier", "unmet")], matches=[]
    )
    [r], _, _ = to_state_update(parsed, "Berlin")

    assert not r.covered and r.unknown


def test_language_met_overrides_a_model_unmet(monkeypatch) -> None:
    """Grundkenntnisse against A2: the model's pessimism does not get the last word."""
    monkeypatch.setattr(analyze_mod, "language_requirement_verdict", lambda r: "met")
    parsed = AnalysisOut(
        requirements=[_req("Grundkenntnisse Deutsch", "disqualifier", "unmet")], matches=[]
    )
    [r], _, _ = to_state_update(parsed, "Berlin")

    assert r.covered and not r.unknown


def test_onsite_clause_is_decided_by_job_location_not_the_model() -> None:
    parsed = AnalysisOut(requirements=[_req("On-site in Hamburg", "disqualifier", "met")], matches=[])
    [far], _, _ = to_state_update(parsed, "Hamburg, Germany")
    [near], _, _ = to_state_update(parsed, "Berlin, Germany")
    [unknown], _, _ = to_state_update(parsed, "")

    assert not far.covered and not far.unknown  # city he cannot reach
    assert near.covered
    assert unknown.unknown  # job location missing: ask, don't guess


def test_a_language_demand_typed_hard_still_reaches_the_language_rail(monkeypatch) -> None:
    """The rails are chosen by what a requirement DOES, not by the bucket the model put
    it in. `agent_cv` types and drafts in one call and does not always file an eligibility
    gate as a disqualifier — good_jd's English clause and its "Pullach, nearby Munich"
    on-site clause both came back `hard`. While the rails ran only on disqualifiers, a
    German demand filed `hard` bypassed the language rail entirely and landed in the
    `max_blocking_hard_gaps` budget, which is the one outcome "a language demand never
    skips a job" exists to rule out."""
    monkeypatch.setattr(analyze_mod, "language_requirement_verdict", lambda r: "unknown")
    parsed = AnalysisOut(requirements=[_req("Fluent German required", "hard", "met")], matches=[])
    [r], diagnosis, _ = to_state_update(parsed, "Berlin")
    state = JobState(job=Job(id="j", source="manual", title="t", jd_text="x"), requirements=[r])

    assert r.unknown and not r.covered
    assert diagnosis.hard_gaps == [], "an unknown is a question, never a gap"
    assert hard_gap_gate(state) == "proceed"


def test_an_onsite_clause_typed_hard_is_still_decided_by_the_job_location() -> None:
    parsed = AnalysisOut(requirements=[_req("On-site in Hamburg", "hard", "met")], matches=[])

    [far], _, _ = to_state_update(parsed, "Hamburg, Germany")
    [near], _, _ = to_state_update(parsed, "Berlin, Germany")
    [unknown], _, _ = to_state_update(parsed, "")

    assert not far.covered and not far.unknown
    assert near.covered
    assert unknown.unknown


def test_soft_requirements_are_left_to_the_model(monkeypatch) -> None:
    """Deliberately NOT railed. A soft item only moves a coverage number, so overriding it
    would add review questions ("German is a plus" -> unknown -> open_questions) without
    preventing anything."""
    monkeypatch.setattr(analyze_mod, "language_requirement_verdict", lambda r: "unknown")
    parsed = AnalysisOut(requirements=[_req("German is a plus", "soft", "met")], matches=[])
    [r], _, _ = to_state_update(parsed, "Berlin")

    assert r.covered and not r.unknown


class _FakeProfile:
    raw = {"constraints": {}, "identity": {"location": "Berlin"}, "education": [], "profile_summary": {}}
    bullets = []

    @staticmethod
    def load():
        return _FakeProfile()

    def all_tech(self):
        return {"python"}

    def all_stack(self):
        return {"python"}


def _state() -> JobState:
    return JobState(job=Job(id="j", source="manual", title="t", jd_text="We need Python.", location="Berlin"))


def test_analyze_makes_one_call_with_jd_and_profile(monkeypatch) -> None:
    seen = {}

    def fake_invoke(llm, messages, **kw):
        seen["messages"], seen["node"] = messages, kw["node"]
        return AnalysisOut(requirements=[_req("Python", "hard", "met")], matches=["m"]), [{"cost_usd": 0.01}]

    monkeypatch.setattr(analyze_mod, "Profile", _FakeProfile)
    monkeypatch.setattr(analyze_mod, "_model", lambda: "fake")
    monkeypatch.setattr(analyze_mod, "invoke_structured", fake_invoke)

    out = analyze_mod.analyze(_state())

    assert seen["node"] == "analyze"
    human = seen["messages"][1][1]
    assert "<candidate_profile>" in human and "We need Python." in human
    assert out["diagnosis"].matches == ["m"] and len(out["requirements"]) == 1
    assert out["llm_calls"] == [{"cost_usd": 0.01}]


def test_analyze_skips_when_prefilled(monkeypatch) -> None:
    def boom(*a, **k):
        raise AssertionError("model must not be called when requirements+diagnosis are prefilled")

    monkeypatch.setattr(analyze_mod, "_model", boom)
    s = _state().model_copy(update={
        "requirements": [Requirement(text="Python", type="hard")], "diagnosis": Diagnosis(),
    })

    assert analyze_mod.analyze(s) == {}


def test_analyze_fails_loudly_on_zero_requirements(monkeypatch) -> None:
    monkeypatch.setattr(analyze_mod, "Profile", _FakeProfile)
    monkeypatch.setattr(analyze_mod, "_model", lambda: "fake")
    monkeypatch.setattr(analyze_mod, "invoke_structured", lambda *a, **k: (AnalysisOut(requirements=[], matches=[]), []))

    with pytest.raises(RuntimeError, match="zero requirements"):
        analyze_mod.analyze(_state())
