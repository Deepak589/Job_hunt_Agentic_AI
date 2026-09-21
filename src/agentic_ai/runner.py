"""jobpilot run — poll every configured company, diff against last-seen postings,
prefilter deterministically (0 LLM calls), then run genuinely-new postings through the
full pipeline and return a digest (solution.md step 6).

`run_many` is reused as-is for the actual pipeline execution — it already carries
`BudgetGuard` + `already_processed` dedupe (solution.md step 1); this module builds no
second cap-enforcement mechanism. `run_many`'s `shared_job_fields` are genuinely shared
across every jd_text in one call (see its docstring / cli.py's `add --dir` use), which
doesn't fit postings from different companies with different urls/titles — so each
survivor gets its own single-item `run_many([...])` call instead, run sequentially
(one company's postings after another). That trades away run_many's intra-batch
concurrency for correctness: each call's `BudgetGuard` snapshots persisted spend fresh,
and the previous job's cost is already persisted before the next call starts, so there
is no double-count race across companies the way there would be if these ran
concurrently under separate BudgetGuards.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from ruamel.yaml import YAML

from .config import ROOT
from .sourcing import ashby, greenhouse, lever, personio

DEFAULT_COMPANIES_PATH = ROOT / "config" / "companies.yaml"

# Module refs, not bound functions — so a test's `monkeypatch.setattr(greenhouse,
# "fetch_jobs", fake)` is picked up (a bound-function dict captured at import time
# would not see that patch).
_CONNECTOR_MODULES = {
    "personio": personio,
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
}

FAILURE_NOTIFY_THRESHOLD = 3


def _load_companies(path: Path) -> list[dict]:
    yaml = YAML(typ="safe")
    raw = yaml.load(path.read_text()) or {}
    return raw.get("companies", [])


def run(companies_path: Path | None = None) -> dict:
    """Poll every company in `companies_path` (default `config/companies.yaml`), run
    survivors through the pipeline, and return a digest dict (JSON-serializable)."""
    from .db.repo import (
        get_company_snapshot,
        persist_run,
        record_snapshot_failure,
        record_snapshot_success,
    )
    from .graph import run_many

    companies = _load_companies(companies_path or DEFAULT_COMPANIES_PATH)

    digest = {
        "companies_polled": 0,
        "new_postings": 0,
        "prefiltered_out": 0,
        "run": 0,
        "skipped": 0,
        "verdicts": {"apply": 0, "fix_then_apply": 0, "skip": 0},
        "prefiltered_reasons": [],
        "notify": [],
    }

    for entry in companies:
        name = entry["name"]
        source = entry["source"]
        identifier = entry["identifier"]
        location_filter = entry.get("location")
        module = _CONNECTOR_MODULES.get(source)
        digest["companies_polled"] += 1

        if module is None:
            digest["notify"].append(f"{name} ({source}): unknown source, skipped")
            continue

        try:
            jobs = module.fetch_jobs(identifier)
        except Exception as exc:
            streak = record_snapshot_failure(name, source)
            if streak >= FAILURE_NOTIFY_THRESHOLD:
                digest["notify"].append(
                    f"{name} ({source}): {streak} consecutive failures — last error: {exc}"
                )
            continue

        snapshot = get_company_snapshot(name, source)
        seen_ids = set(snapshot["posting_ids"]) if snapshot else set()
        new_jobs = [j for j in jobs if j.id not in seen_ids]
        digest["new_postings"] += len(new_jobs)

        # Snapshot updates right after a successful fetch, independent of what happens
        # downstream — a pipeline failure on one posting must not re-surface every
        # posting from this company as "new" on the next run.
        record_snapshot_success(name, source, [j.id for j in jobs])

        survivors = []
        for job in new_jobs:
            if job.lang != "en":
                digest["prefiltered_out"] += 1
                digest["prefiltered_reasons"].append(f"{job.title} @ {job.company or name}: lang={job.lang}")
                continue
            if location_filter and location_filter.lower() not in (job.location or "").lower():
                digest["prefiltered_out"] += 1
                digest["prefiltered_reasons"].append(
                    f"{job.title} @ {job.company or name}: location {job.location!r} "
                    f"doesn't match {location_filter!r}"
                )
                continue
            survivors.append(job)

        for job in survivors:
            states = asyncio.run(run_many(
                [job.jd_text],
                source=job.source,
                url=job.url,
                title=job.title,
                company=job.company or name,
                location=job.location,
                posted_at=job.posted_at,
                employment_type=job.employment_type,
            ))
            state = states[0]
            persist_run(state)
            if state.skip_reason:
                digest["skipped"] += 1
                continue
            digest["run"] += 1
            verdict = state.ats.verdict if state.ats else (state.hiring_manager.verdict if state.hiring_manager else None)
            if verdict:
                digest["verdicts"][verdict] += 1

    return digest
