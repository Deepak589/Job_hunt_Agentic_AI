"""LinkedIn Jobs ingestion via the Apify actor `valig/linkedin-jobs-scraper` — requires
APIFY_TOKEN. Calls Apify's REST API directly (run-sync-get-dataset-items) so a single
request both runs the actor and returns its results — no apify-client SDK dependency
needed since httpx already covers it.

Input schema per apify.com/valig/linkedin-jobs-scraper/input-schema (fetched 2026-09-22):

    {"keywords": "...", "location": "...", "limit": 100, "datePosted": "...",
     "companyName": "...", "easyApply": bool, "titleInclude": [...], "titleExclude": [...]}

Output row shape (observed from a live dataset, run 2026-09-15):

    {"id": "...", "url": "https://.../jobs/view/...", "title": "...", "location": "...",
     "postedDate": "2026-09-07T00:00:00.000Z", "companyName": "...", "companyUrl": "...",
     "contractType": "Internship" | "Part-time" | "Full-time" | ...,
     "applyType": "EASY_APPLY" | "EXTERNAL", "applyUrl": "...",
     "description": "...", "descriptionHtml": "..."}
"""

from __future__ import annotations

import os

import httpx

from ..graph import job_id
from ..state import Job

ACTOR_ID = "RIGGeqD6RqKmlVoQU"  # valig/linkedin-jobs-scraper
RUN_SYNC_URL = f"https://api.apify.com/v2/acts/{ACTOR_ID}/run-sync-get-dataset-items"


class MissingCredentialsError(ValueError):
    """APIFY_TOKEN not set."""


def _employment_type(contract_type: str | None) -> str | None:
    if not contract_type:
        return None
    c = contract_type.lower()
    if "intern" in c:
        return "intern"
    if "part" in c:
        return "werkstudent"
    if "full" in c:
        return "fulltime"
    return None


def fetch_jobs(query: str, location: str = "", limit: int = 20, **kwargs) -> list[Job]:
    token = kwargs.pop("token", None) or os.environ.get("APIFY_TOKEN")
    if not token:
        raise MissingCredentialsError(
            "APIFY_TOKEN must be set (console.apify.com/settings/integrations) to use LinkedIn sourcing."
        )

    payload = {"keywords": query, "location": location, "limit": limit}
    resp = httpx.post(
        RUN_SYNC_URL,
        params={"token": token},
        json=payload,
        timeout=kwargs.pop("timeout", 120),
    )
    resp.raise_for_status()
    rows = resp.json()

    jobs: list[Job] = []
    for row in rows if isinstance(rows, list) else []:
        title = row.get("title", "") or ""
        company = row.get("companyName", "") or ""
        loc = row.get("location", "") or ""
        jd_text = row.get("description", "") or ""
        url = row.get("url", "") or ""

        jobs.append(
            Job(
                id=job_id("linkedin_apify", url, jd_text),
                source="linkedin_apify",
                url=url,
                title=title,
                company=company,
                location=loc,
                posted_at=row.get("postedDate"),
                jd_text=jd_text,
                lang="en",
                employment_type=_employment_type(row.get("contractType")),
            )
        )
    return jobs
