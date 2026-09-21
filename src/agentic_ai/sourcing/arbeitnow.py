"""Arbeitnow job-board ingestion — public API, no auth (plan.md sourcing).

Real response shape (curled 2026-09-18 against
https://www.arbeitnow.com/api/job-board-api):

    {"data": [{
        "slug": "...", "company_name": "...", "title": "...",
        "description": "<p>...</p>",  # HTML
        "remote": true, "url": "https://...",
        "tags": [...], "job_types": ["Full-time", ...],
        "location": "Berlin", "created_at": 1789732849  # unix seconds
    }, ...]}
"""

from __future__ import annotations

import re
from html import unescape

import httpx

from ..graph import job_id
from ..state import Job

API_URL = "https://www.arbeitnow.com/api/job-board-api"

_TAG_RE = re.compile(r"<[^>]+>")

# ponytail: naive stopword heuristic, not a language-detection library — good enough to
# split Arbeitnow's DE/EN mix. Upgrade to lingua (already a dependency) if this misjudges
# postings in practice.
_DE_WORDS = {"und", "der", "die", "das", "mit", "für", "wir", "sie", "ein", "eine", "du", "ist", "von", "im"}


def _strip_html(text: str) -> str:
    return unescape(_TAG_RE.sub(" ", text)).strip()


def _detect_lang(text: str) -> str:
    words = re.findall(r"[a-zA-Zäöüß]+", text.lower())
    if not words:
        return "en"
    de_hits = sum(1 for w in words if w in _DE_WORDS)
    ratio = de_hits / len(words)
    if ratio > 0.03:
        return "de"
    return "en"


def _employment_type(job_types: list[str]) -> str | None:
    joined = " ".join(job_types).lower()
    if "intern" in joined:
        return "intern"
    if "part" in joined or "werkstudent" in joined:
        return "werkstudent"
    if "full" in joined:
        return "fulltime"
    return None


def fetch_jobs(query: str = "", location: str = "", **kwargs) -> list[Job]:
    """Fetch postings from Arbeitnow and map them into `Job`.

    Arbeitnow's public API has no server-side query params for keyword/location search —
    it returns a page of recent postings; filter client-side on `query`/`location`.
    """
    resp = httpx.get(API_URL, timeout=kwargs.pop("timeout", 15))
    resp.raise_for_status()
    payload = resp.json()

    jobs: list[Job] = []
    for row in payload.get("data", []):
        title = row.get("title", "")
        company = row.get("company_name", "")
        loc = row.get("location", "")
        jd_html = row.get("description", "")
        jd_text = _strip_html(jd_html)

        if query and query.lower() not in (title + " " + jd_text).lower():
            continue
        if location and location.lower() not in loc.lower():
            continue

        url = row.get("url", "")
        jobs.append(
            Job(
                id=job_id("arbeitnow", url, jd_text),
                source="arbeitnow",
                url=url,
                title=title,
                company=company,
                location=loc,
                posted_at=str(row.get("created_at", "")) or None,
                jd_text=jd_text,
                lang=_detect_lang(jd_text),
                employment_type=_employment_type(row.get("job_types", [])),
            )
        )
    return jobs
