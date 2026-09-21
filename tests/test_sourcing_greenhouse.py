"""Mocked HTTP layer — no real network calls (shape per Greenhouse's documented API,
not live-verified)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agentic_ai.sourcing.greenhouse import fetch_jobs

FIXTURE = {
    "jobs": [
        {
            "id": 12345,
            "title": "Data Scientist",
            "location": {"name": "Berlin, Germany"},
            "content": "<div>We need Python and SQL. Full-time role.</div>",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/12345",
            "updated_at": "2026-09-01T12:00:00Z",
        },
        {
            "id": 67890,
            "title": "Sales Manager",
            "location": {"name": "Munich, Germany"},
            "content": "<div>Sales role.</div>",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/67890",
            "updated_at": "2026-09-02T12:00:00Z",
        },
    ]
}


def _mock_response():
    resp = MagicMock()
    resp.json.return_value = FIXTURE
    resp.raise_for_status.return_value = None
    return resp


def test_fetch_jobs_maps_fields() -> None:
    with patch("agentic_ai.sourcing.greenhouse.httpx.get", return_value=_mock_response()) as get:
        jobs = fetch_jobs("acme")
    get.assert_called_once()
    assert len(jobs) == 2

    ds = jobs[0]
    assert ds.source == "greenhouse"
    assert ds.title == "Data Scientist"
    assert ds.location == "Berlin, Germany"
    assert ds.url.endswith("12345")
    assert "Python and SQL" in ds.jd_text
    assert "<div>" not in ds.jd_text
    assert ds.employment_type is None


def test_fetch_jobs_filters_by_query_and_location() -> None:
    with patch("agentic_ai.sourcing.greenhouse.httpx.get", return_value=_mock_response()):
        jobs = fetch_jobs("acme", query="Data Scientist")
    assert len(jobs) == 1

    with patch("agentic_ai.sourcing.greenhouse.httpx.get", return_value=_mock_response()):
        jobs = fetch_jobs("acme", location="Munich")
    assert len(jobs) == 1
    assert jobs[0].title == "Sales Manager"


def test_fetch_jobs_defensive_against_missing_fields() -> None:
    """An unexpected/missing field must not crash the whole fetch."""
    sparse = {"jobs": [{"id": 1, "title": "Minimal Posting"}]}
    resp = MagicMock()
    resp.json.return_value = sparse
    resp.raise_for_status.return_value = None
    with patch("agentic_ai.sourcing.greenhouse.httpx.get", return_value=resp):
        jobs = fetch_jobs("acme")
    assert len(jobs) == 1
    assert jobs[0].title == "Minimal Posting"
    assert jobs[0].location == ""
    assert jobs[0].jd_text == ""
