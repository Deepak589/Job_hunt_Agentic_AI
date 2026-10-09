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

from .config import ROOT, settings
from .sourcing import ashby, greenhouse, lever, personio
from .state import Job

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


def _new_digest() -> dict:
    return {
        "companies_polled": 0,
        "new_postings": 0,
        "prefiltered_out": 0,
        "run": 0,
        "skipped": 0,
        "verdicts": {"apply": 0, "fix_then_apply": 0, "skip": 0},
        "prefiltered_reasons": [],
        "notify": [],
    }


def _run_one_live(job: Job, name: str, digest: dict, *, _prefill_by_index: dict | None = None) -> None:
    """One survivor through the full pipeline via `run_many`, persisted, folded into
    `digest`. Shared by the default live path and by `run(batch=True)`'s per-job
    fallback when that job's batch call errored."""
    from .db.repo import persist_run
    from .graph import run_many

    states = asyncio.run(run_many(
        [job.jd_text],
        source=job.source,
        url=job.url,
        title=job.title,
        company=job.company or name,
        location=job.location,
        posted_at=job.posted_at,
        employment_type=job.employment_type,
        **({"_prefill_by_index": _prefill_by_index} if _prefill_by_index else {}),
    ))
    state = states[0]
    persist_run(state)
    if state.skip_reason:
        digest["skipped"] += 1
        return
    digest["run"] += 1
    verdict = state.ats.verdict if state.ats else None
    if verdict:
        digest["verdicts"][verdict] += 1


def run(companies_path: Path | None = None, batch: bool = False) -> dict:
    """Poll every company in `companies_path` (default `config/companies.yaml`), run
    survivors through the pipeline, and return a digest dict (JSON-serializable).

    `batch=True` (solution.md step 7): submits every survivor's extract+diagnose calls
    through the Anthropic Batches API (~50% cheaper) instead of paying live per job."""
    from .db.repo import (
        get_company_snapshot,
        record_snapshot_failure,
        record_snapshot_success,
    )

    companies = _load_companies(companies_path or DEFAULT_COMPANIES_PATH)
    digest = _new_digest()
    all_survivors: list[tuple[Job, str]] = []

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
            all_survivors.append((job, name))

    if batch:
        digest["notify"].append("--batch is retired: the pipeline is one analyze call per job; ran live")
    for job, name in all_survivors:
        _run_one_live(job, name, digest)

    return digest
