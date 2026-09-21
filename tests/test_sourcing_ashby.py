"""Mocked HTTP layer — no real network calls (shape per Ashby's documented API,
not live-verified)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agentic_ai.sourcing.ashby import fetch_jobs

FIXTURE = {
    "jobs": [
        {
            "id": "job-1",
            "title": "Data Scientist",
            "location": "Berlin, Germany",
            "descriptionPlain": "We need Python and SQL.",
            "jobUrl": "https://jobs.ashbyhq.com/acme/job-1",
            "publishedAt": "2026-09-01T12:00:00Z",
            "employmentType": "FullTime",
        },
        {
            "id": "job-2",
            "title": "AI Engineer Intern",
            "location": "Munich, Germany",
            "descriptionPlain": "Internship role.",
            "jobUrl": "https://jobs.ashbyhq.com/acme/job-2",
            "publishedAt": "2026-09-02T12:00:00Z",
            "employmentType": "Intern",
        },
    ]
}


def _mock_response():
    resp = MagicMock()
    resp.json.return_value = FIXTURE
    resp.raise_for_status.return_value = None
    return resp


def test_fetch_jobs_maps_fields() -> None:
    with patch("agentic_ai.sourcing.ashby.httpx.get", return_value=_mock_response()) as get:
        jobs = fetch_jobs("acme")
    get.assert_called_once()
    assert len(jobs) == 2

    ds = jobs[0]
    assert ds.source == "ashby"
    assert ds.title == "Data Scientist"
    assert ds.location == "Berlin, Germany"
    assert ds.url.endswith("job-1")
    assert ds.employment_type == "fulltime"

    intern = jobs[1]
    assert intern.employment_type == "intern"


def test_fetch_jobs_filters_by_query_and_location() -> None:
    with patch("agentic_ai.sourcing.ashby.httpx.get", return_value=_mock_response()):
        jobs = fetch_jobs("acme", query="Intern")
    assert len(jobs) == 1

    with patch("agentic_ai.sourcing.ashby.httpx.get", return_value=_mock_response()):
        jobs = fetch_jobs("acme", location="Munich")
    assert len(jobs) == 1
    assert jobs[0].title == "AI Engineer Intern"


def test_fetch_jobs_defensive_against_missing_fields() -> None:
    sparse = {"jobs": [{"id": "x", "title": "Minimal Posting"}]}
    resp = MagicMock()
    resp.json.return_value = sparse
    resp.raise_for_status.return_value = None
    with patch("agentic_ai.sourcing.ashby.httpx.get", return_value=resp):
        jobs = fetch_jobs("acme")
    assert len(jobs) == 1
    assert jobs[0].title == "Minimal Posting"
    assert jobs[0].location == ""
    assert jobs[0].employment_type is None
