"""jobpilot profile init — LLM-extract a CV (PDF) into data/master_profile.yaml.

First-time onboarding for someone who doesn't know (or doesn't want to hand-write) the
yaml schema. Same discipline as the rest of the pipeline: the LLM proposes structured
data (`make_structured`/`invoke_structured`, same as extract_requirements.py), this
module turns it into the yaml mechanically, and nothing is trusted until the human
reviews the rendered PDF this produces. No auto-apply of corrections — if something's
wrong, the fix is a normal yaml edit (or re-run), same as any other change to the
source of truth.
"""

from __future__ import annotations

import functools
import html
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

from typing import Literal

from pydantic import BaseModel, Field
from ruamel.yaml import YAML

from .config import ROOT, settings
from .llm import invoke_structured, make_structured

SKILL_CATEGORIES = [
    "languages", "deep_learning", "llm_rag", "full_stack",
    "machine_learning", "mlops_cloud", "tools",
]


class ExtractedBullet(BaseModel):
    outcome: str
    metric: str | None = None
    method: str = ""
    tags: list[str] = Field(default_factory=list)


class ExtractedExperience(BaseModel):
    title: str
    company: str
    location: str = ""
    start: str
    end: str | None = None
    stack: list[str] = Field(default_factory=list)
    bullets: list[ExtractedBullet]


class ExtractedProject(BaseModel):
    name: str
    repo: str = ""
    context: str = ""
    stack: list[str] = Field(default_factory=list)
    bullets: list[ExtractedBullet]


class ExtractedEducation(BaseModel):
    degree: str
    institution: str
    location: str = ""
    start: str
    end: str | None = None
    # Literal, not free str: coverage.py's enrollment check matches "in_progress" exactly
    # (master_profile.example.yaml's convention) — free text let the LLM write "in
    # progress" with a space, which silently broke enrollment-based eligibility gating.
    status: Literal["completed", "in_progress"] = "completed"


class LanguageSkill(BaseModel):
    name: str
    level: str


class ExtractedSkillCategory(BaseModel):
    category: str = Field(description="one of: " + ", ".join(SKILL_CATEGORIES))
    names: list[str]


class Identity(BaseModel):
    """emit_identity — the flat fields; no nested types, always safe."""

    name: str
    headline: str
    tagline_keywords: list[str] = Field(default_factory=list)
    email: str
    phone: str = ""
    location: str = ""
    github: str = ""
    linkedin: str = ""
    profile_core: str
    proof_points: list[str] = Field(default_factory=list)
    backing: str = ""
    seeking: str = ""
    certifications: list[str] = Field(default_factory=list)


class LanguagesList(BaseModel):
    """emit_languages — spoken/written languages, one nested type."""

    languages: list[LanguageSkill] = Field(default_factory=list)


class EducationList(BaseModel):
    """emit_education — degrees, one nested type."""

    education: list[ExtractedEducation] = Field(default_factory=list)


class SkillsList(BaseModel):
    """emit_skills — skill categories, one nested type."""

    skills: list[ExtractedSkillCategory] = Field(default_factory=list)


class ExperienceList(BaseModel):
    """emit_experience — every work experience entry, atomized."""

    experience: list[ExtractedExperience] = Field(default_factory=list)


class ProjectList(BaseModel):
    """emit_projects — every project entry, atomized."""

    projects: list[ExtractedProject] = Field(default_factory=list)


class ExtractedProfile(BaseModel):
    """The candidate's whole CV, atomized — assembled from several smaller LLM calls
    (Anthropic's structured-output schema has a complexity limit a single
    profile-plus-everything schema exceeds; see `extract()`)."""

    name: str
    headline: str
    tagline_keywords: list[str] = Field(default_factory=list)
    email: str
    phone: str = ""
    location: str = ""
    github: str = ""
    linkedin: str = ""
    profile_core: str
    proof_points: list[str] = Field(default_factory=list)
    backing: str = ""
    seeking: str = ""
    languages: list[LanguageSkill] = Field(default_factory=list)
    experience: list[ExtractedExperience] = Field(default_factory=list)
    projects: list[ExtractedProject] = Field(default_factory=list)
    education: list[ExtractedEducation] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    skills: list[ExtractedSkillCategory] = Field(default_factory=list)


@functools.lru_cache(maxsize=1)
def _prompt() -> str:
    return (settings.prompts_dir / "extract_profile.md").read_text()


@functools.lru_cache(maxsize=1)
def _identity_model():
    # claude-sonnet-5 rejects an explicit `temperature` — deprecated for this model
    # (same constraint as diagnose.py/rewrite.py).
    return make_structured(settings.diagnose_model, Identity, max_tokens=2048)


@functools.lru_cache(maxsize=1)
def _languages_model():
    return make_structured(settings.diagnose_model, LanguagesList, max_tokens=1024)


@functools.lru_cache(maxsize=1)
def _education_model():
    return make_structured(settings.diagnose_model, EducationList, max_tokens=1024)


@functools.lru_cache(maxsize=1)
def _skills_model():
    return make_structured(settings.diagnose_model, SkillsList, max_tokens=2048)


@functools.lru_cache(maxsize=1)
def _experience_model():
    return make_structured(settings.diagnose_model, ExperienceList, max_tokens=8192)


@functools.lru_cache(maxsize=1)
def _projects_model():
    return make_structured(settings.diagnose_model, ProjectList, max_tokens=8192)


def _extract_pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def extract(cv_text: str, verbose: bool = False) -> tuple[ExtractedProfile, list[dict]]:
    """6 focused calls, not 1 — Anthropic's structured-output schema complexity limit
    rejects a schema with 3+ distinct nested object types side by side (confirmed live:
    3 side-by-side types => "Schema is too complex"; a single type, or a 2-type chain
    like experience->bullets, both work). Each call below stays at 0 or 1 nested type."""
    cv_block = f"<cv>\n{cv_text.strip()}\n</cv>"
    usage: list[dict] = []

    def _call(model, schema_name: str, extra: str = ""):
        parsed, u = invoke_structured(
            model, [("system", _prompt()), ("human", cv_block + (f"\n\n{extra}" if extra else ""))],
            model=settings.diagnose_model, node=f"extract_profile_{schema_name}", verbose=verbose,
        )
        usage.extend(u)
        return parsed

    identity = _call(_identity_model(), "identity")
    languages = _call(_languages_model(), "languages", "Extract only the spoken/written languages.")
    education = _call(_education_model(), "education", "Extract only the education/degree entries.")
    skills = _call(_skills_model(), "skills", "Extract only the technical skills.")
    exp = _call(
        _experience_model(), "experience",
        "Extract every work experience entry the CV lists — do not omit any, even ones that "
        "look minor or short. Count the entries yourself before answering.",
    )
    proj = _call(
        _projects_model(), "projects",
        "Extract every project entry the CV lists — do not omit any, even ones that look minor "
        "or short. Count the entries yourself before answering.",
    )

    parsed = ExtractedProfile(
        **identity.model_dump(),
        languages=languages.languages,
        education=education.education,
        skills=skills.skills,
        experience=exp.experience,
        projects=proj.projects,
    )
    return _unescape(parsed), usage


def _unescape(value):
    """The model sometimes HTML-escapes '&' as '&amp;' in structured output even
    though the source CV text has plain '&' — deterministic cleanup beats relying on
    prompt compliance for something this mechanical."""
    if isinstance(value, str):
        return html.unescape(value)
    if isinstance(value, list):
        return [_unescape(v) for v in value]
    if isinstance(value, dict):
        return {k: _unescape(v) for k, v in value.items()}
    if isinstance(value, BaseModel):
        return value.__class__(**_unescape(value.model_dump()))
    return value


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "x"


def _duration_years(start: str, end: str | None) -> float | None:
    try:
        sy, sm = (int(p) for p in start.split("-"))
    except (ValueError, AttributeError):
        return None
    if end:
        try:
            ey, em = (int(p) for p in end.split("-"))
        except ValueError:
            return None
    else:
        today = date.today()
        ey, em = today.year, today.month
    months = (ey - sy) * 12 + (em - sm)
    return round(months / 12, 1) if months > 0 else None


def _bullet_dict(b: ExtractedBullet, bullet_id: str) -> dict:
    d = {"id": bullet_id, "outcome": b.outcome, "metric": b.metric, "method": b.method}
    tags = [t for t in b.tags if t]
    if tags:
        d["tags"] = tags
    return d


def build_profile_dict(ex: ExtractedProfile) -> dict:
    """ExtractedProfile -> the raw dict data/master_profile.yaml's schema expects
    (same shape as data/master_profile.example.yaml)."""
    today = date.today().isoformat()

    experience = []
    for e in ex.experience:
        slug = _slugify(e.company or e.title)
        eid = f"exp.{slug}"
        bullets = [_bullet_dict(b, f"{eid}.b{i}") for i, b in enumerate(e.bullets, 1)]
        entry = {
            "id": eid, "title": e.title, "company": e.company, "clients": [],
            "location": e.location, "start": e.start, "end": e.end,
            "stack": e.stack, "bullets": bullets,
        }
        dur = _duration_years(e.start, e.end)
        if dur is not None:
            entry["duration_years"] = dur
        experience.append(entry)

    projects = []
    for p in ex.projects:
        slug = _slugify(p.name)
        pid = f"proj.{slug}"
        bullets = [_bullet_dict(b, f"{pid}.b{i}") for i, b in enumerate(p.bullets, 1)]
        projects.append({
            "id": pid, "name": p.name, "repo": p.repo, "context": p.context,
            "stack": p.stack, "bullets": bullets,
        })

    education = []
    for i, ed in enumerate(ex.education, 1):
        education.append({
            "id": f"edu.{_slugify(ed.degree)}_{i}", "degree": ed.degree,
            "institution": ed.institution, "location": ed.location,
            "start": ed.start, "end": ed.end, "status": ed.status,
        })

    certifications = [
        {"id": f"cert.{_slugify(c)}", "name": c} for c in ex.certifications
    ]

    skills = {}
    for cat in ex.skills:
        skills.setdefault(cat.category, [])
        for name in cat.names:
            skills[cat.category].append({"name": name, "level": "proficient", "evidence": []})

    all_bullet_metrics = [
        b["metric"] for entry in experience + projects for b in entry["bullets"] if b["metric"]
    ]
    durations = [str(e["duration_years"]) for e in experience if "duration_years" in e]

    return {
        "meta": {
            "schema_version": 1, "extracted_from": "jobpilot profile init",
            "extracted": today, "updated": today,
            "reviewed_by_human": False, "open_todos": 0,
        },
        "identity": {
            "name": ex.name, "headline": ex.headline, "tagline_keywords": ex.tagline_keywords,
            "email": ex.email, "phone": ex.phone, "location": ex.location,
            "github": ex.github, "linkedin": ex.linkedin,
        },
        "constraints": {
            "work_status": "unknown", "werkstudent_eligible": False,
            "max_hours_per_week_in_term": None, "available_from": "unknown",
            "full_time_from": "unknown", "requires_english_workplace": True,
            "relocation": "", "languages": [{"name": l.name, "level": l.level} for l in ex.languages],
        },
        "profile_summary": {
            "core": ex.profile_core, "proof_points": ex.proof_points,
            "backing": ex.backing, "seeking": ex.seeking,
        },
        "experience": experience,
        "projects": projects,
        "education": education,
        "certifications": certifications,
        "skills": skills,
        "allowed_metrics": sorted(set(all_bullet_metrics + durations)),
        "never_claim": [],
    }


_HEADER = """\
# master_profile.yaml — SINGLE SOURCE OF TRUTH
#
# Generated by `jobpilot profile init` from a CV. UNREVIEWED — read every field before
# trusting it (meta.reviewed_by_human is false until you do; flip it once you've checked).
#
# Rules:
#  - THIS FILE is authoritative on what is claimed. cv_data.json and the rendered PDF
#    are both derived from it, never the other way around.
#  - Claude may edit this file ONLY to reconcile it with a render or on explicit
#    instruction. It may never add a fact, metric or skill that has no source in
#    a render, a repo, or something you said.
#  - Facts are atomized: outcome / metric / method are separate so the rewriter can
#    recombine them per JD, and validators/facts.py can assert every number in a
#    generated bullet exists here.
#  - metric: null means "no honest number exists". Never fill it to make a bullet nicer.
#  - constraints below are placeholders (work_status/available_from/etc weren't on the
#    CV) — fill these in by hand, they gate which jobs the pipeline even considers.

"""


def write_profile_yaml(data: dict, path: Path) -> None:
    yaml = YAML()
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.default_flow_style = False
    import io

    buf = io.StringIO()
    yaml.dump(data, buf)
    path.write_text(_HEADER + buf.getvalue())


def init_profile_from_cv(cv_path: Path, *, verbose: bool = False) -> str:
    """Extract `cv_path` into data/master_profile.yaml, regenerate cv_data.json and
    main_cv_v2.pdf so there's something to review immediately. Returns a summary line."""
    cv_text = _extract_pdf_text(cv_path)
    if not cv_text.strip():
        raise RuntimeError(f"got no extractable text from {cv_path} — is it a scanned/image PDF?")

    extracted, _usage = extract(cv_text, verbose=verbose)
    profile_dict = build_profile_dict(extracted)
    write_profile_yaml(profile_dict, settings.profile_path)

    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_cv_data.py")], check=True)
    subprocess.run(["bash", str(ROOT / "build_cv.sh")], check=True, cwd=ROOT)

    n_bullets = sum(len(e["bullets"]) for e in profile_dict["experience"] + profile_dict["projects"])
    n_skills = sum(len(v) for v in profile_dict["skills"].values())
    return (
        f"extracted {len(profile_dict['experience'])} experience entries, "
        f"{len(profile_dict['projects'])} projects, {n_bullets} bullets, {n_skills} skills. "
        f"wrote {settings.profile_path}, data/cv_data.json, main_cv_v2.pdf — "
        f"constraints (work_status/available_from/relocation/etc) are placeholders, fill those in by hand."
    )
