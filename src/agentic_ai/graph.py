"""tailor_graph — per-job state machine, slimmed to one reasoning call.

    load_job -> analyze -> hard_gap_gate --skip--> log_skip -> END
                                  \\--proceed
                                       v
                                    rewrite -> validate_facts
                                       ^            |
                                       |   errors, attempts left
                                       +------------+
                                                    |
                          errors, attempts exhausted -> log_fact_failure -> END
                                                    |
                                                  clean
                                                    |
                          (skip_render=True stops here -> END)
                                                    v
                                      [review_pause=True] human_review
                                          /        |         \\
                                     reject      edit      approve
                                       v           |           v
                                      END    human_review   render_documents -> score_ats -> END

What was removed and why (see Examples/CLAUDE.md, the manual flow this mirrors): the separate
extract / evidence-retrieval / coverage / judge / diagnose nodes are one `analyze` call that
reads JD and profile together; the LLM `review`, `recruiter_sim` and `hiring_manager` nodes
are gone because the human review step is the reviewer. What stays deterministic and
non-negotiable: the hard-gap gate, `validate_facts` (no invented claims), rendering and
`score_ats`.

Durability: `run()`/`run_many()`/`run_for_review()`/`resume_review()` each open ONE
`AsyncSqliteSaver` per call (WAL mode), `thread_id = job.id`, `durability="sync"`, so a crashed
process can pick a job back up via `resume()` without re-paying for checkpointed nodes.

`human_review` calls `interrupt()` (dynamic) and is only wired in when
`build_graph(review_pause=True)`. "edit" validates the edited draft's facts and pauses again.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Literal

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from .config import settings
from .coverage import hard_gap_gate, log_skip
from .nodes.analyze import analyze
from .nodes.rewrite import rewrite
from .nodes.validate_facts import fact_gate, log_fact_failure, validate_facts_node
from .render import render_documents
from .scoring.ats import score_ats
from .state import Draft, Job, JobState, job_id  # job_id re-exported: `from .graph import job_id` still works


def _tracing_callbacks() -> list:
    """Optional Langfuse tracing. No-op (no import, no network) unless both keys are set."""
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return []
    from langfuse import Langfuse
    from langfuse.langchain import CallbackHandler

    Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )
    return [CallbackHandler(public_key=settings.langfuse_public_key)]


def load_job(state: JobState) -> dict:
    j = state.job
    return {"notes": [f"loaded job {j.id} — {j.title or '(untitled)'} @ {j.company or '(unknown)'}"]}


def human_review(state: JobState) -> dict:
    """Pauses for approve/edit/reject. Only reachable when `build_graph(review_pause=True)`
    wired it in — automated `run()`/`run_many()` batch runs never hit this node.

    `interrupt()` re-runs this node from the top on resume, so the whole body is cheap
    and side-effect-free apart from returning the decision."""
    decision = interrupt({"draft": state.draft, "diagnosis": state.diagnosis})
    action = decision.get("action", "approve")
    if action == "edit":
        from .profile import Profile
        from .validators.facts import validate_facts

        draft = Draft.model_validate(decision["draft"])
        jd_keywords = [kw for r in state.requirements if r.type == "hard" for kw in r.keywords]
        errors = validate_facts(draft, Profile.load(), jd_keywords=jd_keywords)
        return {
            "draft": draft,
            "human_decision": "edit",
            "validation_errors": errors,
            "notes": [
                "human_review: edited — "
                + (f"{len(errors)} fact error(s): {errors}" if errors else "facts clean")
            ],
        }
    if action == "reject":
        return {
            "skip_reason": "rejected by user at review",
            "human_decision": "reject",
            "notes": ["human_review: rejected"],
        }
    return {"human_decision": "approve", "notes": ["human_review: approved"]}


def human_review_gate(state: JobState) -> Literal["approve", "edit", "reject"]:
    return state.human_decision or "approve"


def build_graph(skip_render: bool = False, checkpointer=None, review_pause: bool = False):
    g = StateGraph(JobState)
    g.add_node("load_job", load_job)
    g.add_node("analyze", analyze)
    g.add_node("log_skip", log_skip)
    g.add_node("rewrite", rewrite)
    g.add_node("validate_facts", validate_facts_node)
    g.add_node("log_fact_failure", log_fact_failure)
    g.add_node("render_documents", render_documents)
    g.add_node("score_ats", score_ats)

    g.set_entry_point("load_job")
    g.add_edge("load_job", "analyze")
    g.add_conditional_edges("analyze", hard_gap_gate, {"skip": "log_skip", "proceed": "rewrite"})
    g.add_edge("log_skip", END)
    g.add_edge("rewrite", "validate_facts")

    if skip_render:
        after_facts = END
    elif review_pause:
        g.add_node("human_review", human_review)
        g.add_conditional_edges(
            "human_review", human_review_gate,
            {"approve": "render_documents", "edit": "human_review", "reject": END},
        )
        after_facts = "human_review"
    else:
        after_facts = "render_documents"
    g.add_conditional_edges(
        "validate_facts", fact_gate,
        {"retry": "rewrite", "clean": after_facts, "give_up": "log_fact_failure"},
    )
    g.add_edge("log_fact_failure", END)
    if not skip_render:
        g.add_edge("render_documents", "score_ats")
        g.add_edge("score_ats", END)
    return g.compile(checkpointer=checkpointer)


# Every pydantic model `state.py` defines ends up in a checkpoint sooner or later
# (JobState aggregates all of them) — LangGraph's default (permissive) serializer
# warns on every one of them it deserializes ("will be blocked in a future
# version"), and the warning fires unconditionally in permissive mode regardless
# of any extra allowlist passed in (`with_allowlist` is a no-op there — only an
# EXPLICIT, non-permissive allowlist suppresses it, per jsonplus.py's
# `_create_msgpack_ext_hook`: `if allowed_modules is True: ... warn; return True`
# fires before the allowlist is ever consulted). These are our own trusted
# models, not attacker-controlled data, so passing them explicitly — which also
# blocks anything NOT in this list, same spirit as LANGGRAPH_STRICT_MSGPACK=true
# but scoped to this project instead of every third-party type — is correct.
_STATE_MSGPACK_ALLOWLIST = [
    ("agentic_ai.state", name)
    for name in (
        "AtsScore", "Evidence", "Requirement", "Job", "Diagnosis", "DraftBullet",
        "Draft", "RecruiterResult", "HiringManagerVerdict", "Scores", "JobState",
    )
]


@asynccontextmanager
async def _open_checkpointer():
    """One `AsyncSqliteSaver` per call, WAL mode — concurrent `ainvoke`s (run_many) need
    WAL, not the default rollback journal, or writers block each other."""
    async with aiosqlite.connect(str(settings.checkpoint_db_path)) as conn:
        saver = AsyncSqliteSaver(
            conn, serde=JsonPlusSerializer(allowed_msgpack_modules=_STATE_MSGPACK_ALLOWLIST)
        )
        await saver.conn.execute("PRAGMA journal_mode=WAL")
        yield saver


def _build_job(jd_text: str, title: str, company: str, job_fields: dict) -> Job:
    source = job_fields.pop("source", "manual")
    return Job(
        id=job_id(source, job_fields.get("url", ""), jd_text),
        source=source,
        title=title,
        company=company,
        jd_text=jd_text,
        **job_fields,
    )


async def _invoke_tracked(graph, initial_state: JobState, config: dict) -> JobState:
    """`graph.ainvoke`, but on any exception persists whatever cost the checkpointer
    already captured before re-raising — Anthropic bills per call made, not per run
    completed, so a crash on e.g. `rewrite` must not lose the llm_calls spent on
    `extract_requirements`/`diagnose`/`judge_requirements` before it. `db/repo.py`'s
    `already_processed` docstring already promised this ("a crashed run leaves no row
    (or a row with verdict NULL)") — this is that NULL-row half, previously missing."""
    try:
        result = await graph.ainvoke(initial_state, config=config, durability="sync")
        return JobState.model_validate(result)
    except Exception as exc:
        from .db.repo import persist_run

        snapshot = await graph.aget_state(config)
        if snapshot.values:
            crashed_state = JobState.model_validate(
                {**snapshot.values, "skip_reason": snapshot.values.get("skip_reason") or f"crashed: {exc}"}
            )
            persist_run(crashed_state)
        raise


async def _run(jd_text: str, title: str, company: str, job_fields: dict) -> JobState:
    skip_render = job_fields.pop("_skip_render", False)
    prefill = job_fields.pop("_prefill", None) or {}
    job = _build_job(jd_text, title, company, job_fields)
    async with _open_checkpointer() as saver:
        graph = build_graph(skip_render=skip_render, checkpointer=saver)
        config = {"configurable": {"thread_id": job.id}, "callbacks": _tracing_callbacks()}
        return await _invoke_tracked(graph, JobState(job=job, **prefill), config)


def run(jd_text: str, title: str = "", company: str = "", **job_fields) -> JobState:
    """Run one JD through the graph and return the final state. Sync wrapper — the
    checkpointer (durability, §2) is async, so this opens its own event loop.

    `_prefill` (solution.md step 7, private kwarg, same convention as `_skip_render`):
    `{"requirements": [...], "diagnosis": ...}` merged into the initial `JobState` before
    `ainvoke` — a batch run already paid for extract/diagnose, so `extract_requirements`/
    `diagnose` see the fields already set and skip (nodes/requirements.py,
    nodes/diagnose.py); every other node still runs live exactly as today."""
    return asyncio.run(_run(jd_text, title, company, job_fields))


async def run_many(jd_texts: list[str], **shared_job_fields) -> list[JobState]:
    """Run multiple JDs through the graph concurrently (bounded by
    `Settings.max_concurrent_jobs`, since the Anthropic rate limit is shared across
    every concurrent call). Results come back in the same order as `jd_texts` —
    `asyncio.gather` preserves input order regardless of completion order.

    Two guards apply before a job is actually run, both INSIDE the semaphore so
    they see up-to-date state from sibling jobs in this same batch (solution.md
    step 1): re-running an already-processed job (same content-hash id) is skipped
    rather than re-paying for extract/diagnose/rewrite, and the daily budget cap is
    checked against persisted + in-flight-reserved spend, not persisted alone —
    persisted-only would let every job in the batch pass the same stale check and
    all run over cap, since nothing is persisted until run_many returns.

    All jobs in the batch share ONE `AsyncSqliteSaver` (§2) — aiosqlite serializes
    writes internally, so concurrent `ainvoke`s on separate `thread_id`s are safe.

    `_prefill_by_index` (solution.md step 7, private kwarg): `{index: {"requirements":
    [...], "diagnosis": ...}}`, keyed by position in `jd_texts` — same skip mechanism as
    `run`'s `_prefill`, for runner.py's `--batch` path where extract/diagnose already
    ran via the Batches API for some (not necessarily all) jobs in this call.
    """
    from .budget import BudgetGuard, estimated_job_cost
    from .db.repo import already_processed

    skip_render = shared_job_fields.pop("_skip_render", False)
    prefill_by_index = shared_job_fields.pop("_prefill_by_index", None) or {}
    semaphore = asyncio.Semaphore(settings.max_concurrent_jobs)
    config_callbacks = _tracing_callbacks()
    guard = BudgetGuard.for_today()

    async def _run_one(jd_text: str, graph, prefill: dict) -> JobState:
        # shared_job_fields is one url (if any) shared across the whole batch — real per-job
        # urls never flow through this path (sourcing modules run one at a time via
        # graph.run(), not run_many); job_id falls back to content-hash whenever url is "".
        job = Job(
            id=job_id(shared_job_fields.get("source", "manual"), shared_job_fields.get("url", ""), jd_text),
            source=shared_job_fields.get("source", "manual"),
            title=shared_job_fields.get("title", ""),
            company=shared_job_fields.get("company", ""),
            jd_text=jd_text,
            **{k: v for k, v in shared_job_fields.items() if k not in ("source", "title", "company")},
        )
        async with semaphore:
            if already_processed(job.id, job.content_hash):
                return JobState(job=job, skip_reason=f"already_processed (job_id={job.id})")

            est_cost = estimated_job_cost()
            if not await guard.try_reserve(est_cost):
                return JobState(job=job, skip_reason="budget_cap_hit")

            config = {"configurable": {"thread_id": job.id}, "callbacks": config_callbacks}
            try:
                state = await _invoke_tracked(graph, JobState(job=job, **prefill), config)
            except Exception:
                await guard.release(est_cost)
                raise
            await guard.settle(est_cost, state.total_cost_usd)
        return state

    async with _open_checkpointer() as saver:
        graph = build_graph(skip_render=skip_render, checkpointer=saver)
        return list(await asyncio.gather(
            *(_run_one(jd, graph, prefill_by_index.get(i, {})) for i, jd in enumerate(jd_texts))
        ))


async def _run_for_review(jd_text: str, title: str, company: str, job_fields: dict) -> JobState:
    job = _build_job(jd_text, title, company, job_fields)
    async with _open_checkpointer() as saver:
        graph = build_graph(skip_render=False, checkpointer=saver, review_pause=True)
        config = {"configurable": {"thread_id": job.id}, "callbacks": _tracing_callbacks()}
        return await _invoke_tracked(graph, JobState(job=job), config)


def run_for_review(jd_text: str, title: str = "", company: str = "", **job_fields) -> JobState:
    """Like `run()`, but pauses at `human_review` (§9) — nothing is written to disk until
    `resume_review(..., action="approve")` renders it. The paused state has `draft`,
    `diagnosis` filled in; `artifacts`/`ats` are not yet set."""
    return asyncio.run(_run_for_review(jd_text, title, company, job_fields))


async def _resume_review(job_id_: str, action: Literal["approve", "edit", "reject"], draft: Draft | None) -> JobState:
    async with _open_checkpointer() as saver:
        graph = build_graph(skip_render=False, checkpointer=saver, review_pause=True)
        config = {"configurable": {"thread_id": job_id_}, "callbacks": _tracing_callbacks()}
        resume_payload: dict = {"action": action}
        if action == "edit":
            assert draft is not None, "action='edit' requires draft"
            resume_payload["draft"] = draft.model_dump(mode="json")
        result = await graph.ainvoke(Command(resume=resume_payload), config=config, durability="sync")
    return JobState.model_validate(result)


def resume_review(
    job_id_: str, action: Literal["approve", "edit", "reject"], draft: Draft | None = None
) -> JobState:
    """Resume a job paused by `run_for_review` (or paused again after an "edit" loops
    back through `human_review`). `action="edit"` fact-checks the new draft and pauses
    again with its fact errors, if any."""
    return asyncio.run(_resume_review(job_id_, action, draft))


async def _get_paused_state(job_id_: str) -> JobState:
    async with _open_checkpointer() as saver:
        graph = build_graph(skip_render=False, checkpointer=saver, review_pause=True)
        config = {"configurable": {"thread_id": job_id_}}
        snapshot = await graph.aget_state(config)
    if not snapshot.values:
        raise KeyError(f"no paused job found for id {job_id_!r}")
    return JobState.model_validate(snapshot.values)


def get_paused_state(job_id_: str) -> JobState:
    """Read back a job paused by `run_for_review`, without resuming it."""
    return asyncio.run(_get_paused_state(job_id_))


async def _list_pending_review_ids() -> list[str]:
    import sqlite3

    if not settings.checkpoint_db_path.exists():
        return []
    with sqlite3.connect(settings.checkpoint_db_path) as conn:
        thread_ids = [row[0] for row in conn.execute("SELECT DISTINCT thread_id FROM checkpoints")]

    pending: list[str] = []
    async with _open_checkpointer() as saver:
        probe = build_graph(skip_render=False, checkpointer=saver, review_pause=True)
        for tid in thread_ids:
            config = {"configurable": {"thread_id": tid}}
            snapshot = await probe.aget_state(config)
            if snapshot.values and "human_review" in snapshot.next:
                pending.append(tid)
    return pending


def list_pending_review_ids() -> list[str]:
    """Job ids currently paused at `human_review`. `jobpilot.db`'s `jobs` table has no
    "paused" status to query (a row can exist from an earlier `add --review` call, but
    nothing in it says the checkpoint is still sitting at human_review) so this scans
    `checkpoints.db`'s thread ids directly instead."""
    return asyncio.run(_list_pending_review_ids())


async def _resume(job_id_: str) -> JobState:
    async with _open_checkpointer() as saver:
        config = {"configurable": {"thread_id": job_id_}, "callbacks": _tracing_callbacks()}
        # `human_review` is a node the plain (non-review) graph never registers, so
        # `next` under that topology silently omits a pending human_review task rather
        # than naming it — probe with the review_pause=True (superset) topology first,
        # which registers every node either graph could have paused at.
        probe = build_graph(skip_render=False, checkpointer=saver, review_pause=True)
        snapshot = await probe.aget_state(config)
        if not snapshot.values:
            raise KeyError(f"no in-progress job found for id {job_id_!r}")
        if "human_review" in snapshot.next:
            raise ValueError(f"job {job_id_!r} is paused for review — use 'jobpilot review' instead")
        if not snapshot.next:
            return JobState.model_validate(snapshot.values)  # already finished
        graph = build_graph(skip_render=False, checkpointer=saver)
        result = await graph.ainvoke(None, config=config, durability="sync")
    return JobState.model_validate(result)


def resume(job_id_: str) -> JobState:
    """Continue a job whose process crashed mid-run (§2 durability) — replays from the
    last checkpointed node, not from `load_job`, so `extract_requirements`/`diagnose`/
    `rewrite` already paid for are not re-run. Only for the plain (non `--review`) path;
    a job paused at `human_review` is continued via `resume_review` instead."""
    return asyncio.run(_resume(job_id_))
