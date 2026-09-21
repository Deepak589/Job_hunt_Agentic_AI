"""Personio public XML job feed ingestion — no auth (solution.md step 6).

Shape per Personio's publicly documented XML job feed
(https://developer.personio.de/docs/retrieving-open-positions-via-xml) — not
live-verified in this session; confirm against a real board before relying on it in
production:

    GET https://{company}.jobs.personio.de/xml?language=en

    <workzag-jobs>            <!-- or <personio-jobs> — feeds vary, accept either -->
      <position>
        <id>...</id>
        <name>...</name>            <!-- title -->
        <office>Berlin</office>
        <employmentType>Internship</employmentType>
        <createdAt>2026-09-01</createdAt>
        <jobDescriptions>
          <jobDescription><name>Your role</name><value>...</value></jobDescription>
          <jobDescription><name>Your profile</name><value>...</value></jobDescription>
        </jobDescriptions>
      </position>
    </workzag-jobs>

Parsed with stdlib `xml.etree.ElementTree` — no new XML dependency.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx

from ..graph import job_id
from ..state import Job

FEED_URL = "https://{company}.jobs.personio.de/xml"


def _employment_type(raw: str) -> str | None:
    r = raw.lower()
    if "intern" in r:
        return "intern"
    if "part" in r or "werkstudent" in r:
        return "werkstudent"
    if "full" in r:
        return "fulltime"
    return None


def _jd_text(position: ET.Element) -> str:
    sections = []
    for desc in position.findall("./jobDescriptions/jobDescription"):
        value = desc.findtext("value", "") or ""
        if value.strip():
            sections.append(value.strip())
    return "\n\n".join(sections)


def fetch_jobs(company: str, query: str = "", location: str = "", **kwargs) -> list[Job]:
    resp = httpx.get(
        FEED_URL.format(company=company),
        params={"language": "en"},
        timeout=kwargs.pop("timeout", 15),
    )
    resp.raise_for_status()
    # Root tag varies by feed (<workzag-jobs> vs <personio-jobs>) — search for <position>
    # anywhere under it rather than asserting a specific root, so either shape works.
    root = ET.fromstring(resp.text)

    jobs: list[Job] = []
    for position in root.findall(".//position"):
        title = position.findtext("name", "") or ""
        loc = position.findtext("office", "") or ""
        jd_text = _jd_text(position)

        if query and query.lower() not in (title + " " + jd_text).lower():
            continue
        if location and location.lower() not in loc.lower():
            continue

        pos_id = position.findtext("id", "") or ""
        # <url> isn't documented consistently across Personio feed variants — fall back
        # to the standard public job-page URL pattern when absent.
        url = position.findtext("url", "") or f"https://{company}.jobs.personio.de/job/{pos_id}"
        jobs.append(
            Job(
                id=job_id("personio", url, jd_text),
                source="personio",
                url=url,
                title=title,
                company=company,
                location=loc,
                posted_at=position.findtext("createdAt"),
                jd_text=jd_text,
                lang="en",
                employment_type=_employment_type(position.findtext("employmentType", "") or ""),
            )
        )
    return jobs
