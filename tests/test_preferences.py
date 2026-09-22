"""Procedural memory from review edits (plan.md §21.4, solution.md step 8)."""

from __future__ import annotations

from agentic_ai.nodes import rewrite as rewrite_mod
from agentic_ai.preferences import diff_edited_bullets, record_preferences
from agentic_ai.state import Draft, DraftBullet, Job, JobState


def _draft(**bullets_by_section) -> Draft:
    return Draft(
        profile_line="p",
        section_order=["experience"],
        bullets=bullets_by_section,
        cover_letter="c",
    )


def test_diff_edited_bullets_finds_changed_and_ignores_unchanged() -> None:
    old = _draft(experience=[
        DraftBullet(text="Built X.", source_bullet_id="exp.a.b1"),
        DraftBullet(text="Reduced Y by 10%.", source_bullet_id="exp.a.b2"),
    ])
    new = _draft(experience=[
        DraftBullet(text="Architected X.", source_bullet_id="exp.a.b1"),  # changed
        DraftBullet(text="Reduced Y by 10%.", source_bullet_id="exp.a.b2"),  # unchanged
    ])

    changed = diff_edited_bullets(old, new)

    assert changed == ["Architected X."]


def test_diff_edited_bullets_finds_new_bullet() -> None:
    old = _draft(experience=[DraftBullet(text="Built X.", source_bullet_id="exp.a.b1")])
    new = _draft(experience=[
        DraftBullet(text="Built X.", source_bullet_id="exp.a.b1"),
        DraftBullet(text="Shipped Z.", source_bullet_id="exp.a.b3"),  # new, matched by id
    ])

    assert diff_edited_bullets(old, new) == ["Shipped Z."]


def test_record_preferences_dedupes_on_overlap(tmp_path) -> None:
    path = tmp_path / "preferences.yaml"
    record_preferences(["Built X.", "Reduced Y."], path=path)
    record_preferences(["Reduced Y.", "Shipped Z."], path=path)

    from ruamel.yaml import YAML
    data = YAML(typ="safe").load(path)

    assert data["preferences"] == ["Built X.", "Reduced Y.", "Shipped Z."]


class _FakeProfile:
    bullets = []
    never_claim = "never claim things not in the CV"

    @staticmethod
    def load():
        return _FakeProfile()


def _job_state() -> JobState:
    from agentic_ai.state import Diagnosis
    job = Job(id="j1", source="manual", title="t", jd_text="We need Python.")
    return JobState(job=job, diagnosis=Diagnosis(matches=["stub"]))


def _rewrite_common_mocks(monkeypatch, draft: Draft) -> None:
    monkeypatch.setattr(rewrite_mod, "Profile", _FakeProfile)
    monkeypatch.setattr(rewrite_mod, "classify_role", lambda jd_text: ("ai_engineer", []))
    monkeypatch.setattr(rewrite_mod, "_model", lambda: "fake-model")
    monkeypatch.setattr(rewrite_mod, "invoke_structured", lambda *a, **kw: (draft, []))


def test_rewrite_includes_user_preferences_block_when_file_has_content(monkeypatch, tmp_path) -> None:
    draft = _draft(experience=[DraftBullet(text="Built X.", source_bullet_id="exp.a.b1")])
    _rewrite_common_mocks(monkeypatch, draft)
    prefs_path = tmp_path / "preferences.yaml"
    record_preferences(["Always lead with the metric."], path=prefs_path)
    monkeypatch.setattr(rewrite_mod, "load_preferences", lambda: ["Always lead with the metric."])

    captured = {}

    def _fake_invoke(structured_llm, messages, **kw):
        captured["messages"] = messages
        return draft, []

    monkeypatch.setattr(rewrite_mod, "invoke_structured", _fake_invoke)

    rewrite_mod.rewrite(_job_state())

    human_blocks = [b["text"] for b in captured["messages"][1].content]
    assert any("<user_preferences>" in t and "Always lead with the metric." in t for t in human_blocks)


def test_rewrite_omits_user_preferences_block_when_none_recorded(monkeypatch) -> None:
    draft = _draft(experience=[DraftBullet(text="Built X.", source_bullet_id="exp.a.b1")])
    _rewrite_common_mocks(monkeypatch, draft)
    monkeypatch.setattr(rewrite_mod, "load_preferences", lambda: [])

    captured = {}

    def _fake_invoke(structured_llm, messages, **kw):
        captured["messages"] = messages
        return draft, []

    monkeypatch.setattr(rewrite_mod, "invoke_structured", _fake_invoke)

    rewrite_mod.rewrite(_job_state())

    human_blocks = [b["text"] for b in captured["messages"][1].content]
    assert not any("<user_preferences>" in t for t in human_blocks)
