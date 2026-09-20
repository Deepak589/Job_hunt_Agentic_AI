"""render_documents — Typst PDF rendering (plan.md §3, §10).

Two data-building functions produce plain dicts matching the existing hand-written
templates' shapes (`templates/cv_two_column.typ` already consumes exactly the CV shape
below — it was hand-built from `data/cv_data.json`, which this function's output
replaces on a per-job basis). Rendering itself calls the `typst` PyPI package's
`compile()` — no shell binary, no subprocess.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path

from typst import compile as typst_compile

from .config import ROOT, settings
from .profile import Profile
from .state import Draft, Job, JobState

OUT_DIR = ROOT / "out"


def _fmt_month(ym: str | None) -> str:
    """'2025-09' -> 'Sep 2025'; None/empty -> 'Present' (open-ended education/roles)."""
    if not ym:
        return "Present"
    return datetime.strptime(str(ym), "%Y-%m").strftime("%b %Y")


def build_cv_render_data(draft: Draft, profile: Profile) -> dict:
    identity = profile.raw["identity"]
    raw = profile.raw

    def entry_for(bullet_id: str) -> dict | None:
        b = profile.by_id(bullet_id)
        if b is None:
            return None
        for section in ("experience", "projects"):
            for e in raw.get(section, []):
                if e["id"] == b.parent_id:
                    return e
        return None

    def section_block(section_id: str) -> list[dict]:
        blocks: dict[str, dict] = {}
        for db in draft.bullets.get(section_id, []):
            entry = entry_for(db.source_bullet_id)
            if entry is None:
                continue
            key = entry["id"]
            if key not in blocks:
                if section_id == "experience":
                    org = entry["company"]
                    if entry.get("clients"):
                        org += " · clients: " + ", ".join(c.split(" (")[0] for c in entry["clients"])
                    blocks[key] = {
                        "title": entry["title"], "org": org,
                        "dates": f"{entry.get('start', '')} – {entry.get('end', 'present')}",
                        "bullets": [],
                    }
                else:
                    meta = " · ".join(entry.get("stack", []))
                    blocks[key] = {"title": entry.get("title", entry.get("name")), "meta": meta, "bullets": []}
            blocks[key]["bullets"].append(db.text)
        return list(blocks.values())

    return {
        "name": identity["name"].upper(),
        "tagline": identity.get("tagline_keywords", []),
        "photo": "assets/photo.jpg",
        "contact": [
            ["mail", identity["email"]], ["phone", identity["phone"]],
            ["pin", identity["location"]], ["link", identity["github"]],
            ["link", identity["linkedin"]],
        ],
        "skills": [[cat, ", ".join(s["name"] for s in items)] for cat, items in raw.get("skills", {}).items()],
        "education": [
            [e["degree"], f"{e['institution']}, {e['location']}", f"{_fmt_month(e.get('start'))} – {_fmt_month(e.get('end'))}"]
            for e in raw.get("education", [])
        ],
        "certifications": [c["name"] for c in raw.get("certifications", [])],
        "languages": [[l["name"], l["level"]] for l in raw.get("constraints", {}).get("languages", [])],
        "profile": draft.profile_line,
        "projects": section_block("projects"),
        "experience": section_block("experience"),
        "main_sections": [
            {"kind": kind, "items": section_block(kind)}
            for kind in draft.section_order
            if kind in ("projects", "experience")
        ],
    }


def build_cover_letter_render_data(draft: Draft, profile: Profile, job: Job) -> dict:
    identity = profile.raw["identity"]
    return {
        "name": identity["name"],
        "email": identity["email"],
        "phone": identity["phone"],
        "location": identity["location"],
        "company": job.company or "Hiring Team",
        "title": job.title or "the role",
        "body": draft.cover_letter,
    }


def render_documents(state: JobState) -> dict:
    assert state.draft is not None, "render_documents requires a clean draft"
    profile = Profile.load()
    job_dir = OUT_DIR / state.job.id
    job_dir.mkdir(parents=True, exist_ok=True)

    cv_data = build_cv_render_data(state.draft, profile)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(cv_data, f, indent=2)
        cv_json_path = Path(f.name)
    cv_pdf = job_dir / "cv.pdf"
    try:
        # root="/" — Typst treats any leading-"/" path (including our absolute OS paths
        # passed via sys_inputs) as root-relative, so root must be the real filesystem
        # root for an absolute sys_inputs path to resolve to itself rather than being
        # re-prefixed onto some other root. Template-relative assets (e.g. "assets/photo.jpg",
        # no leading slash) are unaffected — those resolve relative to the .typ file itself.
        typst_compile(
            str(settings.prompts_dir.parent / "templates" / "cv_two_column.typ"),
            output=str(cv_pdf),
            sys_inputs={"data": str(cv_json_path)},
            root="/",
        )
    finally:
        cv_json_path.unlink()

    letter_data = build_cover_letter_render_data(state.draft, profile, state.job)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(letter_data, f, indent=2)
        letter_json_path = Path(f.name)
    letter_pdf = job_dir / "cover_letter.pdf"
    try:
        typst_compile(
            str(settings.prompts_dir.parent / "templates" / "cover_letter.typ"),
            output=str(letter_pdf),
            sys_inputs={"data": str(letter_json_path)},
            root="/",
        )
    finally:
        letter_json_path.unlink()

    return {
        "artifacts": {"cv_pdf": str(cv_pdf), "cover_pdf": str(letter_pdf)},
        "notes": [f"rendered {cv_pdf.name} and {letter_pdf.name} to {job_dir}"],
    }
