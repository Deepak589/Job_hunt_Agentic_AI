from __future__ import annotations

import agentic_ai.render as render_mod
from agentic_ai.profile import Profile
from agentic_ai.render import build_cover_letter_render_data, build_cv_render_data
from agentic_ai.state import Draft, DraftBullet, Job, JobState


def _draft() -> Draft:
    return Draft(
        profile_line="AI/ML Engineer targeting working-student roles.",
        section_order=["projects", "experience", "skills"],
        bullets={
            "projects": [
                DraftBullet(
                    text="Improved recall by 0.833 by building a hybrid BM25+dense retriever.",
                    metric="0.833",
                    source_bullet_id="proj.rag_pipeline.b1",
                )
            ],
            "experience": [
                DraftBullet(
                    text="Resolved 100+ production defects across two carrier platforms.",
                    metric="100+",
                    source_bullet_id="exp.valuemomentum.b1",
                )
            ],
        },
        cover_letter="Dear Hiring Team,\n\nI am excited to apply.\n\nBest,\nDeepak",
        highlighted_projects=["proj.rag_pipeline"],
    )


def test_cv_render_data_matches_existing_template_shape() -> None:
    profile = Profile.load()
    data = build_cv_render_data(_draft(), profile)
    for key in ("name", "tagline", "photo", "contact", "skills", "profile", "projects", "experience"):
        assert key in data
    assert data["projects"][0]["bullets"] == [
        "Improved recall by 0.833 by building a hybrid BM25+dense retriever."
    ]
    assert data["experience"][0]["bullets"] == [
        "Resolved 100+ production defects across two carrier platforms."
    ]


def test_cv_render_data_only_includes_sections_in_section_order() -> None:
    """A section absent from section_order (e.g. no 'skills' bullets drafted) must not
    silently pull in every profile skill — the rewriter's selection is authoritative."""
    profile = Profile.load()
    draft = _draft()
    data = build_cv_render_data(draft, profile)
    assert "skills" in data  # skills come from the profile directly, not from bullets
    assert len(data["projects"]) == 1
    assert len(data["experience"]) == 1


def test_cover_letter_render_data_has_company_and_body() -> None:
    profile = Profile.load()
    job = Job(id="t", source="manual", title="AI Engineer", company="Acme", jd_text="...")
    data = build_cover_letter_render_data(_draft(), profile, job)
    assert data["company"] == "Acme"
    assert data["title"] == "AI Engineer"
    assert "excited to apply" in data["body"]
    assert data["name"] == profile.raw["identity"]["name"]


def test_render_documents_skips_compile_when_draft_unchanged(monkeypatch, tmp_path) -> None:
    """solution.md step 2: idempotent render — a second call with the same draft must
    not re-invoke Typst, only re-run it when the draft actually changed."""
    monkeypatch.setattr(render_mod, "OUT_DIR", tmp_path)
    calls: list[str] = []

    def _fake_compile(source, output, sys_inputs, root):
        calls.append(source)
        __import__("pathlib").Path(output).write_bytes(b"%PDF-fake")

    monkeypatch.setattr(render_mod, "typst_compile", _fake_compile)

    job = Job(id="job1", source="manual", title="T", jd_text="...")
    state = JobState(job=job, draft=_draft())

    first = render_mod.render_documents(state)
    assert len(calls) == 2  # cv + cover letter
    assert first["artifacts"]["cv_pdf"]

    second = render_mod.render_documents(state)
    assert len(calls) == 2  # unchanged — no new compile calls
    assert second["artifacts"] == first["artifacts"]

    edited = state.model_copy(update={"draft": _draft().model_copy(update={"profile_line": "Changed."})})
    third = render_mod.render_documents(edited)
    assert len(calls) == 4  # draft changed — compiles again
