"""Mocked HTTP layer — no real network calls (shape per Personio's documented XML feed,
not live-verified)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agentic_ai.sourcing.personio import fetch_jobs

FIXTURE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs>
  <position>
    <id>1001</id>
    <name>Data Scientist</name>
    <office>Berlin</office>
    <employmentType>Full-time</employmentType>
    <createdAt>2026-09-01</createdAt>
    <jobDescriptions>
      <jobDescription><name>Your role</name><value>We need Python and SQL.</value></jobDescription>
      <jobDescription><name>Your profile</name><value>Experience with ML.</value></jobDescription>
    </jobDescriptions>
  </position>
  <position>
    <id>1002</id>
    <name>Werkstudent Marketing</name>
    <office>Munich</office>
    <employmentType>Internship</employmentType>
    <createdAt>2026-09-02</createdAt>
    <jobDescriptions>
      <jobDescription><name>Your role</name><value>Marketing support.</value></jobDescription>
    </jobDescriptions>
  </position>
</workzag-jobs>
"""


def _mock_response(text=FIXTURE_XML):
    resp = MagicMock()
    resp.text = text
    resp.raise_for_status.return_value = None
    return resp


def test_fetch_jobs_maps_fields() -> None:
    with patch("agentic_ai.sourcing.personio.httpx.get", return_value=_mock_response()) as get:
        jobs = fetch_jobs("acme")
    get.assert_called_once()
    assert len(jobs) == 2

    ds = jobs[0]
    assert ds.source == "personio"
    assert ds.title == "Data Scientist"
    assert ds.company == "acme"
    assert ds.location == "Berlin"
    assert "Python and SQL" in ds.jd_text
    assert "Experience with ML" in ds.jd_text
    assert ds.employment_type == "fulltime"

    intern = jobs[1]
    assert intern.employment_type == "intern"


def test_fetch_jobs_filters_by_query_and_location() -> None:
    with patch("agentic_ai.sourcing.personio.httpx.get", return_value=_mock_response()):
        jobs = fetch_jobs("acme", query="Werkstudent")
    assert len(jobs) == 1

    with patch("agentic_ai.sourcing.personio.httpx.get", return_value=_mock_response()):
        jobs = fetch_jobs("acme", location="Munich")
    assert len(jobs) == 1
    assert jobs[0].title == "Werkstudent Marketing"


def test_fetch_jobs_accepts_personio_jobs_root_variant() -> None:
    variant = FIXTURE_XML.replace("workzag-jobs", "personio-jobs")
    with patch("agentic_ai.sourcing.personio.httpx.get", return_value=_mock_response(variant)):
        jobs = fetch_jobs("acme")
    assert len(jobs) == 2


def test_fetch_jobs_defensive_against_missing_fields() -> None:
    sparse = """<workzag-jobs><position><id>9</id><name>Minimal Posting</name></position></workzag-jobs>"""
    with patch("agentic_ai.sourcing.personio.httpx.get", return_value=_mock_response(sparse)):
        jobs = fetch_jobs("acme")
    assert len(jobs) == 1
    assert jobs[0].title == "Minimal Posting"
    assert jobs[0].location == ""
    assert jobs[0].jd_text == ""
    assert jobs[0].url.endswith("/job/9")
