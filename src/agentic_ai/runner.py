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

`run(batch=True)` (solution.md step 7): instead of paying for extract+diagnose live per
survivor, submit every survivor's extract call as one Anthropic Batch, then every
extract-succeeded survivor's diagnose call as a second Batch (needs the parsed
requirements + deterministic evidence/coverage first — coverage.py's retrieve_evidence/
score_coverage are non-LLM, so they run locally same as the live graph path would).
Batch-succeeded jobs are then run through `run_many` with `_prefill_by_index` so
`extract_requirements`/`diagnose` skip their (already-paid-for) LLM call; a job whose
batch call errored falls back to the normal live per-job path rather than being dropped.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from ruamel.yaml import YAML

from .config import ROOT, settings
from .sourcing import ashby, greenhouse, lever, personio
from .state import Job, Requirement

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
    verdict = state.ats.verdict if state.ats else (state.hiring_manager.verdict if state.hiring_manager else None)
    if verdict:
        digest["verdicts"][verdict] += 1


def _run_batch(survivors: list[tuple[Job, str]], digest: dict) -> None:
    """Batch-mode processing for every survivor (solution.md step 7)."""
    from .batch import BatchClient, BatchRequest
    from .coverage import retrieve_evidence, score_coverage
    from .graph import job_id as compute_job_id
    from .nodes import diagnose as diagnose_mod
    from .nodes import requirements as requirements_mod
    from .profile import Profile
    from .state import Diagnosis, JobState

    if not survivors:
        return

    client = BatchClient()
    jobs = [(compute_job_id(job.source, job.url, job.jd_text), job, name) for job, name in survivors]

    extract_requests = [
        BatchRequest(
            custom_id=f"{jid}:extract",
            model=settings.extract_model,
            system=requirements_mod._prompt(),
            messages=[{
                "role": "user",
                "content": f"<job_description>\n{job.jd_text.strip()}\n</job_description>",
            }],
            schema_=requirements_mod.RequirementList,
            max_tokens=4096,
        )
        for jid, job, _ in jobs
    ]
    extract_batch_id = client.submit(extract_requests)
    client.poll(extract_batch_id)
    extract_results = client.fetch_results(extract_batch_id)

    profile = Profile.load()
    diagnose_requests = []
    # jid -> {"requirements": [...], "usage": [extract usage, diagnose usage]}
    survivor_data: dict[str, dict] = {}
    fallback_jids: set[str] = set()

    for jid, job, _name in jobs:
        res = extract_results.get(f"{jid}:extract")
        reqs = [Requirement(**r.model_dump()) for r in res["parsed"].requirements] if res and res["error"] is None else []
        if res is None or res["error"] is not None or not reqs:
            fallback_jids.add(jid)
            continue

        # Deterministic, no LLM — same as the live graph's retrieve_evidence/score_coverage.
        state = JobState(job=job, requirements=reqs)
        state = state.model_copy(update=retrieve_evidence(state))
        state = state.model_copy(update=score_coverage(state))
        survivor_data[jid] = {"requirements": state.requirements, "usage": [res["usage"]]}

        content = [
            {
                "type": "text",
                "text": f"<candidate_profile>\n{diagnose_mod._profile_brief(profile)}\n</candidate_profile>",
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            },
            {"type": "text", "text": f"<job_description>\n{job.jd_text.strip()}\n</job_description>"},
            {
                "type": "text",
                "text": f"<extracted_requirements>\n{diagnose_mod._requirements_brief(state)}\n</extracted_requirements>",
            },
        ]
        diagnose_requests.append(BatchRequest(
            custom_id=f"{jid}:diagnose",
            model=settings.diagnose_model,
            system=[{"type": "text", "text": diagnose_mod._prompt(), "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": content}],
            schema_=Diagnosis,
            max_tokens=4096,
        ))

    diagnose_results: dict = {}
    if diagnose_requests:
        diagnose_batch_id = client.submit(diagnose_requests)
        client.poll(diagnose_batch_id)
        diagnose_results = client.fetch_results(diagnose_batch_id)

    for jid, job, name in jobs:
        if jid in fallback_jids:
            continue
        dres = diagnose_results.get(f"{jid}:diagnose")
        if dres is None or dres["error"] is not None:
            fallback_jids.add(jid)
            continue
        data = survivor_data[jid]
        prefill = {
            "requirements": data["requirements"],
            "diagnosis": dres["parsed"],
            "llm_calls": [*data["usage"], dres["usage"]],
        }
        _run_one_live(job, name, digest, _prefill_by_index={0: prefill})

    for jid, job, name in jobs:
        if jid in fallback_jids:
            _run_one_live(job, name, digest)

    if fallback_jids:
        digest["notify"].append(f"batch fallback: {len(fallback_jids)} jobs")


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
        _run_batch(all_survivors, digest)
    else:
        for job, name in all_survivors:
            _run_one_live(job, name, digest)

    return digest
