"""Lever public postings API ingestion — no auth (solution.md step 6).

Shape per Lever's publicly documented API (https://github.com/lever/postings-api) — not
live-verified in this session; confirm against a real board before relying on it in
production:

    GET https://api.lever.co/v0/postings/{company}?mode=json

    [{
        "id": "...", "text": "...",  # title
        "categories": {"location": "Berlin", "team": "...", "commitment": "Full-time"},
        "descriptionPlain": "...", "description": "<div>...</div>",
        "hostedUrl": "https://jobs.lever.co/...",
        "createdAt": 1789732849000,  # epoch ms
        ...
    }, ...]
"""

from __future__ import annotations

import httpx

from ..graph import job_id
from ..state import Job
from .base import strip_html

API_URL = "https://api.lever.co/v0/postings/{company}"


def _employment_type(commitment: str) -> str | None:
    c = commitment.lower()
    if "intern" in c:
        return "intern"
    if "part" in c or "werkstudent" in c:
        return "werkstudent"
    if "full" in c:
        return "fulltime"
    return None


def fetch_jobs(company: str, query: str = "", location: str = "", **kwargs) -> list[Job]:
    resp = httpx.get(
        API_URL.format(company=company),
        params={"mode": "json"},
        timeout=kwargs.pop("timeout", 15),
    )
    resp.raise_for_status()
    rows = resp.json()

    jobs: list[Job] = []
    for row in rows if isinstance(rows, list) else []:
        title = row.get("text", "") or ""
        categories = row.get("categories") or {}
        loc = categories.get("location", "") or ""
        jd_text = row.get("descriptionPlain") or strip_html(row.get("description", "") or "")

        if query and query.lower() not in (title + " " + jd_text).lower():
            continue
        if location and location.lower() not in loc.lower():
            continue

        url = row.get("hostedUrl", "") or ""
        created_ms = row.get("createdAt")
        jobs.append(
            Job(
                id=job_id("lever", url, jd_text),
                source="lever",
                url=url,
                title=title,
                company=company,
                location=loc,
                posted_at=str(created_ms) if created_ms is not None else None,
                jd_text=jd_text,
                lang="en",
                employment_type=_employment_type(categories.get("commitment", "") or ""),
            )
        )
    return jobs
