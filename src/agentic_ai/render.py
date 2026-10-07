"""render_documents — HTML-to-PDF rendering via headless Chrome (plan.md §3, §10).

Two data-building functions produce plain dicts matching the existing hand-written
templates' shapes (`templates/cv_two_column.html` consumes exactly the CV shape below).
Rendering fills the Jinja2 template, writes it to a temp HTML file, then shells out to
Chrome/Chromium `--headless --print-to-pdf` — same approach as the Job_finder repo's
render_cv.sh, ported in so both CV pipelines render the same way.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import ROOT
from .profile import Profile
from .state import Draft, Job, JobState

OUT_DIR = ROOT / "out"
TEMPLATES_DIR = ROOT / "templates"

_CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]


def _find_chrome() -> str:
    for path in _CHROME_CANDIDATES:
        if Path(path).exists():
            return path
    for name in ("chromium", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("No Chrome/Chromium found — install one or set CHROME env var")


_jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),  # JD/company text is attacker-influenced input
)


def _html_to_pdf(html_path: Path, pdf_path: Path) -> None:
    chrome = _find_chrome()
    subprocess.run(
        [chrome, "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
         f"--print-to-pdf={pdf_path}", f"file://{html_path}"],
        check=True, capture_output=True,
    )


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "job"


def job_dir_name(company: str, title: str, job_id: str) -> str:
    """Human-readable output folder name — company-title-<first 8 hex of job.id>.

    job.id itself stays the checkpointer thread_id / DB key / dedupe key untouched;
    this is purely the on-disk folder name, derived deterministically so a lookup by
    job_id (e.g. the `applied` CLI command) can recompute it without storing it twice.
    The short id suffix guards against two postings slugifying to the same name.
    """
    slug = _slugify(f"{company}-{title}")[:60]
    return f"{slug}-{job_id[:8]}"


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
        "photo": "assets/photo.jpg",  # kept for data-shape compat; render_documents resolves the real file:// URI separately
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


def _draft_hash(draft: Draft) -> str:
    return hashlib.sha256(draft.model_dump_json().encode()).hexdigest()


def render_documents(state: JobState) -> dict:
    assert state.draft is not None, "render_documents requires a clean draft"
    profile = Profile.load()
    job_dir = OUT_DIR / job_dir_name(state.job.company, state.job.title, state.job.id)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "jd.txt").write_text(state.job.jd_text)

    cv_pdf = job_dir / "cv.pdf"
    letter_pdf = job_dir / "cover_letter.pdf"
    sha_path = job_dir / "draft.sha"
    draft_hash = _draft_hash(state.draft)
    if (
        cv_pdf.exists() and letter_pdf.exists() and sha_path.exists()
        and sha_path.read_text().strip() == draft_hash
    ):
        return {
            "artifacts": {"cv_pdf": str(cv_pdf), "cover_pdf": str(letter_pdf)},
            "notes": [f"render skipped (draft unchanged) for {job_dir}"],
        }

    photo_uri = f"file://{TEMPLATES_DIR / 'assets' / 'photo.jpg'}"
    cv_data = build_cv_render_data(state.draft, profile)
    cv_html = _jinja_env.get_template("cv_two_column.html").render(d=cv_data, photo_uri=photo_uri)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
        f.write(cv_html)
        cv_html_path = Path(f.name)
    try:
        _html_to_pdf(cv_html_path, cv_pdf)
    finally:
        cv_html_path.unlink()

    letter_data = build_cover_letter_render_data(state.draft, profile, state.job)
    letter_html = _jinja_env.get_template("cover_letter.html").render(
        d=letter_data, today=datetime.today().strftime("%B %-d, %Y")
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
        f.write(letter_html)
        letter_html_path = Path(f.name)
    try:
        _html_to_pdf(letter_html_path, letter_pdf)
    finally:
        letter_html_path.unlink()

    sha_path.write_text(draft_hash)
    return {
        "artifacts": {"cv_pdf": str(cv_pdf), "cover_pdf": str(letter_pdf)},
        "notes": [f"rendered {cv_pdf.name} and {letter_pdf.name} to {job_dir}"],
    }
