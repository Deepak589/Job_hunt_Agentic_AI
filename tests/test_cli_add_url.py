"""`jobpilot add --url` — JSON-LD JobPosting extraction, trafilatura fallback
(solution.md step 6). Mocked HTTP only, no real network calls."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agentic_ai.cli import _fetch_job_posting

HTML_WITH_JSONLD = """
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org/",
  "@type": "JobPosting",
  "title": "Data Scientist",
  "description": "<p>We need Python and SQL.</p>",
  "hiringOrganization": {"@type": "Organization", "name": "Acme GmbH"},
  "jobLocation": {"@type": "Place", "address": {"addressLocality": "Berlin"}}
}
</script>
</head><body><h1>Data Scientist at Acme GmbH</h1></body></html>
"""

HTML_WITHOUT_JSONLD = """
<html><head><title>Data Scientist - Acme</title></head>
<body>
<article>
<h1>Data Scientist</h1>
<p>We are looking for a data scientist with strong Python and SQL skills to join our
growing team in Berlin. You will build machine learning pipelines and work closely
with product.</p>
</article>
</body></html>
"""


def _mock_response(html: str):
    resp = MagicMock()
    resp.text = html
    resp.raise_for_status.return_value = None
    return resp


def test_fetch_job_posting_extracts_jsonld_jobposting() -> None:
    with patch("httpx.get", return_value=_mock_response(HTML_WITH_JSONLD)):
        result = _fetch_job_posting("https://example.com/job/1")

    assert result["title"] == "Data Scientist"
    assert result["company"] == "Acme GmbH"
    assert result["location"] == "Berlin"
    assert "Python and SQL" in result["jd_text"]
    assert "<p>" not in result["jd_text"]


def test_fetch_job_posting_falls_back_to_trafilatura_without_jsonld() -> None:
    with patch("httpx.get", return_value=_mock_response(HTML_WITHOUT_JSONLD)):
        result = _fetch_job_posting("https://example.com/job/2")

    assert result["title"] == ""
    assert result["company"] == ""
    assert result["jd_text"].strip() != ""
    assert "Python" in result["jd_text"]
