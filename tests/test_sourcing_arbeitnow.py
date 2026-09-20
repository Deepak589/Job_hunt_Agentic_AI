"""Mocked HTTP layer — no real network calls (real shape curled 2026-09-18)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agentic_ai.sourcing.arbeitnow import fetch_jobs

FIXTURE = {
    "data": [
        {
            "slug": "data-scientist-berlin-1",
            "company_name": "Acme GmbH",
            "title": "Data Scientist",
            "description": "<p>We need Python and SQL. Full-time role.</p>",
            "remote": True,
            "url": "https://www.arbeitnow.com/jobs/companies/acme/data-scientist-berlin-1",
            "tags": ["Data Science"],
            "job_types": ["Full-time"],
            "location": "Berlin",
            "created_at": 1789732849,
        },
        {
            "slug": "praktikant-marketing-2",
            "company_name": "Beispiel AG",
            "title": "Praktikant Marketing",
            "description": "<p>Wir suchen eine Praktikantin für unser Marketing-Team in München.</p>",
            "remote": False,
            "url": "https://www.arbeitnow.com/jobs/companies/beispiel/praktikant-marketing-2",
            "tags": ["Marketing"],
            "job_types": ["Internship"],
            "location": "Munich",
            "created_at": 1789732800,
        },
    ]
}


def _mock_response():
    resp = MagicMock()
    resp.json.return_value = FIXTURE
    resp.raise_for_status.return_value = None
    return resp


def test_fetch_jobs_maps_fields() -> None:
    with patch("agentic_ai.sourcing.arbeitnow.httpx.get", return_value=_mock_response()) as get:
        jobs = fetch_jobs()
    get.assert_called_once()
    assert len(jobs) == 2

    ds = jobs[0]
    assert ds.source == "arbeitnow"
    assert ds.title == "Data Scientist"
    assert ds.company == "Acme GmbH"
    assert ds.location == "Berlin"
    assert ds.url.endswith("data-scientist-berlin-1")
    assert "Python and SQL" in ds.jd_text
    assert "<p>" not in ds.jd_text
    assert ds.lang == "en"
    assert ds.employment_type == "fulltime"

    intern = jobs[1]
    assert intern.employment_type == "intern"
    assert intern.lang == "de"


def test_fetch_jobs_filters_by_query_and_location() -> None:
    with patch("agentic_ai.sourcing.arbeitnow.httpx.get", return_value=_mock_response()):
        jobs = fetch_jobs(query="Data Scientist")
    assert len(jobs) == 1
    assert jobs[0].title == "Data Scientist"

    with patch("agentic_ai.sourcing.arbeitnow.httpx.get", return_value=_mock_response()):
        jobs = fetch_jobs(location="Munich")
    assert len(jobs) == 1
    assert jobs[0].location == "Munich"


def test_fetch_jobs_id_matches_manual_content_hash() -> None:
    """A job seen via sourcing and manual paste of the same JD text must dedupe."""
    from agentic_ai.graph import job_id

    with patch("agentic_ai.sourcing.arbeitnow.httpx.get", return_value=_mock_response()):
        jobs = fetch_jobs()
    assert jobs[0].id == job_id(jobs[0].jd_text)
