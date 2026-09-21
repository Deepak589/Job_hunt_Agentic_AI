"""Shared sourcing contract + small helpers reused across connectors (solution.md step 6).

`JobSource` is structural, not nominal — every connector module (arbeitnow, adzuna, and
the four ATS connectors below) already exposes a matching `fetch_jobs`, so none of them
need to change or subclass anything to satisfy it. It exists only so `runner.py` can
type-hint "any of these modules" without a real base class.
"""

from __future__ import annotations

import re
from html import unescape
from typing import Protocol

from ..state import Job

_TAG_RE = re.compile(r"<[^>]+>")


class JobSource(Protocol):
    def fetch_jobs(self, query: str = "", location: str = "", **kwargs) -> list[Job]: ...


def strip_html(text: str) -> str:
    """Same pattern as arbeitnow.py's `_strip_html` — shared here since 4 new
    connectors need it too."""
    return unescape(_TAG_RE.sub(" ", text)).strip()
