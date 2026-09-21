"""Mocked HTTP layer against Adzuna's documented response shape — no real network calls,
and no real credentials required (missing-creds path must fail clearly, not raw KeyError)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentic_ai.sourcing.adzuna import MissingCredentialsError, fetch_jobs

FIXTURE = {
    "results": [
        {
            "title": "Machine Learning Engineer",
            "company": {"display_name": "Acme GmbH"},
            "location": {"display_name": "Berlin, Germany"},
            "redirect_url": "https://www.adzuna.de/details/123",
            "description": "Build and deploy ML models with Python.",
            "created": "2026-09-01T12:00:00Z",
            "contract_time": "full_time",
        }
    ],
    "count": 1,
}


def _mock_response():
    resp = MagicMock()
    resp.json.return_value = FIXTURE
    resp.raise_for_status.return_value = None
    return resp


def test_fetch_jobs_maps_fields_with_explicit_credentials() -> None:
    with patch("agentic_ai.sourcing.adzuna.httpx.get", return_value=_mock_response()) as get:
        jobs = fetch_jobs(query="ML Engineer", location="Berlin", app_id="id123", app_key="key123")
    get.assert_called_once()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "adzuna"
    assert job.title == "Machine Learning Engineer"
    assert job.company == "Acme GmbH"
    assert job.location == "Berlin, Germany"
    assert job.url == "https://www.adzuna.de/details/123"
    assert job.employment_type == "fulltime"
    assert job.posted_at == "2026-09-01T12:00:00Z"


def test_fetch_jobs_id_matches_source_and_url() -> None:
    from agentic_ai.graph import job_id

    with patch("agentic_ai.sourcing.adzuna.httpx.get", return_value=_mock_response()):
        jobs = fetch_jobs(query="ML Engineer", app_id="id123", app_key="key123")
    assert jobs[0].id == job_id("adzuna", jobs[0].url, jobs[0].jd_text)


def test_fetch_jobs_missing_credentials_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    with pytest.raises(MissingCredentialsError):
        fetch_jobs(query="ML Engineer")
