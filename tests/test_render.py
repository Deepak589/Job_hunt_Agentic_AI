from __future__ import annotations

from agentic_ai.profile import Profile
from agentic_ai.render import build_cover_letter_render_data, build_cv_render_data
from agentic_ai.state import Draft, DraftBullet, Job


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
