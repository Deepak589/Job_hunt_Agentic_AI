"""Greenhouse Job Board API ingestion — public, no auth (solution.md step 6).

Shape per Greenhouse's publicly documented Job Board API
(https://developers.greenhouse.io/job-board.html) — not live-verified in this session;
confirm against a real board before relying on it in production:

    GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true

    {"jobs": [{
        "id": 12345, "title": "...", "location": {"name": "Berlin, Germany"},
        "content": "<div>...</div>",  # HTML
        "absolute_url": "https://boards.greenhouse.io/...",
        "updated_at": "2026-09-01T12:00:00Z", ...
    }, ...]}

No standard employment-type field on this API — `employment_type` is always None here.
"""

from __future__ import annotations

import httpx

from ..graph import job_id
from ..state import Job
from .base import strip_html

API_URL = "https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs"


def fetch_jobs(board_token: str, query: str = "", location: str = "", **kwargs) -> list[Job]:
    resp = httpx.get(
        API_URL.format(board_token=board_token),
        params={"content": "true"},
        timeout=kwargs.pop("timeout", 15),
    )
    resp.raise_for_status()
    payload = resp.json()

    jobs: list[Job] = []
    for row in payload.get("jobs", []):
        title = row.get("title", "") or ""
        loc = (row.get("location") or {}).get("name", "") or ""
        jd_text = strip_html(row.get("content", "") or "")

        if query and query.lower() not in (title + " " + jd_text).lower():
            continue
        if location and location.lower() not in loc.lower():
            continue

        url = row.get("absolute_url", "") or ""
        jobs.append(
            Job(
                id=job_id("greenhouse", url, jd_text),
                source="greenhouse",
                url=url,
                title=title,
                company="",  # not in this payload — board_token isn't a display name
                location=loc,
                posted_at=row.get("updated_at"),
                jd_text=jd_text,
                lang="en",
                employment_type=None,
            )
        )
    return jobs
