"""Adzuna job-board ingestion — REST API, requires ADZUNA_APP_ID/ADZUNA_APP_KEY.

No credentials in this repo's .env — tested against Adzuna's documented response shape
(https://developer.adzuna.com/docs/search), not a live call:

    {"results": [{
        "title": "...", "company": {"display_name": "..."},
        "location": {"display_name": "Berlin, Germany"},
        "redirect_url": "https://...", "description": "...",
        "created": "2026-09-01T12:00:00Z", "contract_time": "full_time"
    }, ...], "count": N}
"""

from __future__ import annotations

import os

import httpx

from ..graph import job_id
from ..state import Job

API_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"


class MissingCredentialsError(ValueError):
    """ADZUNA_APP_ID / ADZUNA_APP_KEY not set."""


def _employment_type(contract_time: str | None) -> str | None:
    if not contract_time:
        return None
    if "part" in contract_time:
        return "werkstudent"
    if "full" in contract_time:
        return "fulltime"
    return None


def fetch_jobs(
    query: str,
    location: str = "",
    country: str = "de",
    page: int = 1,
    results_per_page: int = 20,
    **kwargs,
) -> list[Job]:
    app_id = kwargs.pop("app_id", None) or os.environ.get("ADZUNA_APP_ID")
    app_key = kwargs.pop("app_key", None) or os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        raise MissingCredentialsError(
            "ADZUNA_APP_ID and ADZUNA_APP_KEY must be set (developer.adzuna.com) to use Adzuna sourcing."
        )

    params = {
        "app_id": app_id,
        "app_key": app_key,
        "what": query,
        "results_per_page": results_per_page,
    }
    if location:
        params["where"] = location

    url = API_URL.format(country=country, page=page)
    resp = httpx.get(url, params=params, timeout=kwargs.pop("timeout", 15))
    resp.raise_for_status()
    payload = resp.json()

    jobs: list[Job] = []
    for row in payload.get("results", []):
        title = row.get("title", "")
        company = (row.get("company") or {}).get("display_name", "")
        loc = (row.get("location") or {}).get("display_name", "")
        jd_text = row.get("description", "")

        jobs.append(
            Job(
                id=job_id(jd_text),
                source="adzuna",
                url=row.get("redirect_url", ""),
                title=title,
                company=company,
                location=loc,
                posted_at=row.get("created"),
                jd_text=jd_text,
                lang="en",  # Adzuna DE search results are predominantly German-market English/German mix;
                # no reliable per-posting language field in the response — default en, same as Job's default.
                employment_type=_employment_type(row.get("contract_time")),
            )
        )
    return jobs
