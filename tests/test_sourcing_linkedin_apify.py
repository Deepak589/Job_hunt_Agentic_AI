"""Mocked HTTP layer — no real network/Apify calls (shape per a live dataset pull)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentic_ai.sourcing.linkedin_apify import MissingCredentialsError, fetch_jobs

FIXTURE = [
    {
        "id": "1",
        "url": "https://de.linkedin.com/jobs/view/data-scientist-intern-1",
        "title": "Data Scientist Intern",
        "location": "Munich, Bavaria, Germany",
        "postedDate": "2026-09-07T00:00:00.000Z",
        "companyName": "Acme",
        "contractType": "Internship",
        "description": "We need Python and SQL.",
    },
    {
        "id": "2",
        "url": "https://de.linkedin.com/jobs/view/werkstudent-data-2",
        "title": "Werkstudent Data Analyst",
        "location": "Berlin, Germany",
        "postedDate": "2026-09-04T00:00:00.000Z",
        "companyName": "Beta GmbH",
        "contractType": "Part-time",
        "description": "Werkstudent role for data analysis.",
    },
]


def _mock_response():
    resp = MagicMock()
    resp.json.return_value = FIXTURE
    resp.raise_for_status.return_value = None
    return resp


def test_fetch_jobs_maps_fields(monkeypatch) -> None:
    monkeypatch.setenv("APIFY_TOKEN", "fake-token")
    with patch(
        "agentic_ai.sourcing.linkedin_apify.httpx.post", return_value=_mock_response()
    ) as post:
        jobs = fetch_jobs("Data Scientist", location="Germany")
    post.assert_called_once()
    assert len(jobs) == 2

    ds = jobs[0]
    assert ds.source == "linkedin_apify"
    assert ds.title == "Data Scientist Intern"
    assert ds.company == "Acme"
    assert ds.location == "Munich, Bavaria, Germany"
    assert ds.url.endswith("data-scientist-intern-1")
    assert ds.employment_type == "intern"

    werk = jobs[1]
    assert werk.employment_type == "werkstudent"


def test_fetch_jobs_raises_without_token(monkeypatch) -> None:
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    with pytest.raises(MissingCredentialsError):
        fetch_jobs("Data Scientist")


def test_fetch_jobs_defensive_against_missing_fields(monkeypatch) -> None:
    monkeypatch.setenv("APIFY_TOKEN", "fake-token")
    sparse = [{"id": "x", "title": "Minimal Posting"}]
    resp = MagicMock()
    resp.json.return_value = sparse
    resp.raise_for_status.return_value = None
    with patch("agentic_ai.sourcing.linkedin_apify.httpx.post", return_value=resp):
        jobs = fetch_jobs("anything")
    assert len(jobs) == 1
    assert jobs[0].title == "Minimal Posting"
    assert jobs[0].location == ""
    assert jobs[0].employment_type is None
