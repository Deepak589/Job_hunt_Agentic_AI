"""Mocked HTTP layer — no real network calls (shape per Lever's documented API,
not live-verified)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agentic_ai.sourcing.lever import fetch_jobs

FIXTURE = [
    {
        "id": "abc-1",
        "text": "Data Scientist",
        "categories": {"location": "Berlin", "team": "Data", "commitment": "Full-time"},
        "descriptionPlain": "We need Python and SQL.",
        "hostedUrl": "https://jobs.lever.co/acme/abc-1",
        "createdAt": 1789732849000,
    },
    {
        "id": "abc-2",
        "text": "Werkstudent Data Analyst",
        "categories": {"location": "Munich", "team": "Data", "commitment": "Part-time"},
        "descriptionPlain": "Werkstudent role for data analysis.",
        "hostedUrl": "https://jobs.lever.co/acme/abc-2",
        "createdAt": 1789732800000,
    },
]


def _mock_response():
    resp = MagicMock()
    resp.json.return_value = FIXTURE
    resp.raise_for_status.return_value = None
    return resp


def test_fetch_jobs_maps_fields() -> None:
    with patch("agentic_ai.sourcing.lever.httpx.get", return_value=_mock_response()) as get:
        jobs = fetch_jobs("acme")
    get.assert_called_once()
    assert len(jobs) == 2

    ds = jobs[0]
    assert ds.source == "lever"
    assert ds.title == "Data Scientist"
    assert ds.company == "acme"
    assert ds.location == "Berlin"
    assert ds.url.endswith("abc-1")
    assert ds.employment_type == "fulltime"

    werk = jobs[1]
    assert werk.employment_type == "werkstudent"


def test_fetch_jobs_filters_by_query_and_location() -> None:
    with patch("agentic_ai.sourcing.lever.httpx.get", return_value=_mock_response()):
        jobs = fetch_jobs("acme", query="Werkstudent")
    assert len(jobs) == 1

    with patch("agentic_ai.sourcing.lever.httpx.get", return_value=_mock_response()):
        jobs = fetch_jobs("acme", location="Munich")
    assert len(jobs) == 1
    assert jobs[0].title == "Werkstudent Data Analyst"


def test_fetch_jobs_defensive_against_missing_fields() -> None:
    sparse = [{"id": "x", "text": "Minimal Posting"}]
    resp = MagicMock()
    resp.json.return_value = sparse
    resp.raise_for_status.return_value = None
    with patch("agentic_ai.sourcing.lever.httpx.get", return_value=resp):
        jobs = fetch_jobs("acme")
    assert len(jobs) == 1
    assert jobs[0].title == "Minimal Posting"
    assert jobs[0].location == ""
    assert jobs[0].employment_type is None
