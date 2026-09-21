"""Ashby Job Board API ingestion — public, no auth (solution.md step 6).

Shape per Ashby's publicly documented Job Board API
(https://developers.ashbyhq.com/reference/jobpostingapi) — not live-verified in this
session; confirm against a real board before relying on it in production:

    GET https://api.ashbyhq.com/posting-api/job-board/{job_board_name}

    {"jobs": [{
        "id": "...", "title": "...", "location": "Berlin, Germany",
        "descriptionHtml": "<div>...</div>", "descriptionPlain": "...",
        "jobUrl": "https://jobs.ashbyhq.com/...",
        "publishedAt": "2026-09-01T12:00:00Z",
        "employmentType": "FullTime", ...
    }, ...]}
"""

from __future__ import annotations

import httpx

from ..graph import job_id
from ..state import Job
from .base import strip_html

API_URL = "https://api.ashbyhq.com/posting-api/job-board/{job_board_name}"


def _employment_type(raw: str) -> str | None:
    r = raw.lower()
    if "intern" in r:
        return "intern"
    if "parttime" in r or "part_time" in r or "part-time" in r:
        return "werkstudent"
    if "fulltime" in r or "full_time" in r or "full-time" in r:
        return "fulltime"
    return None


def fetch_jobs(job_board_name: str, query: str = "", location: str = "", **kwargs) -> list[Job]:
    resp = httpx.get(
        API_URL.format(job_board_name=job_board_name),
        timeout=kwargs.pop("timeout", 15),
    )
    resp.raise_for_status()
    payload = resp.json()

    jobs: list[Job] = []
    for row in payload.get("jobs", []):
        title = row.get("title", "") or ""
        loc = row.get("location", "") or ""
        jd_text = row.get("descriptionPlain") or strip_html(row.get("descriptionHtml", "") or "")

        if query and query.lower() not in (title + " " + jd_text).lower():
            continue
        if location and location.lower() not in loc.lower():
            continue

        url = row.get("jobUrl", "") or ""
        jobs.append(
            Job(
                id=job_id("ashby", url, jd_text),
                source="ashby",
                url=url,
                title=title,
                company="",  # not in this payload — job_board_name isn't a display name
                location=loc,
                posted_at=row.get("publishedAt"),
                jd_text=jd_text,
                lang="en",
                employment_type=_employment_type(row.get("employmentType", "") or ""),
            )
        )
    return jobs
