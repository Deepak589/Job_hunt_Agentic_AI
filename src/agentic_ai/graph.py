"""tailor_graph — per-job state machine (plan.md §1).

    load_job -> extract_requirements -> retrieve_evidence -> score_coverage
                                                                 |
                                       hard_gap_gate --skip--> log_skip -> END
                                                 \\--proceed
                                                        v
                                                    diagnose -> rewrite -> validate_facts
                                                         ^                       |
                                                         |          errors, attempts left
                                                         +-----------------------+
                                                         |
                                            errors, attempts exhausted -> log_fact_failure -> END
                                                         |
                                                       clean
                                                         v
                                                      review
                                                         |
                                     score<min, attempts left -> back to rewrite (loop above)
                                                         |
                                                     proceed  (skip_render=True stops here -> END)
                                                         v
                                                  recruiter_sim
                                                    /          \\
                                            hard_fail          pass/soft_fail
                                                 v                  v
                                    log_recruiter_fail -> END   hiring_manager
                                                                    |
                                                       [only when review_pause=True]
                                                                    v
                                                              human_review
                                                            /      |      \\
                                                      reject     edit    approve
                                                         v         |        v
                                                        END   recruiter_sim  render_documents -> score_ats -> END
                                                              (loop above, fresh verdict)

recruiter_sim/hiring_manager (roles 4-5, CLAUDE.md) only run on the full path —
`skip_render=True` stops at `review` exactly as in Phase 1/2, unchanged.

Durability (solution.md step 2): `run()`/`run_many()`/`run_for_review()`/`resume_review()`
each open ONE `AsyncSqliteSaver` per call (WAL mode — a sync `SqliteSaver` would block
the event loop under `run_many`'s concurrent `ainvoke`s), `thread_id = job.id`, and
invoke with `durability="sync"` so a crashed process can pick a job back up via
`resume()` without re-paying for already-checkpointed nodes.

The old `interrupt_before=["render_documents"]` (compile-time, static) is replaced by a
`human_review` node that calls `interrupt()` (dynamic) — only wired in when
`build_graph(review_pause=True)`. This is what makes "edit" able to route back through
`recruiter_sim` for a fresh verdict instead of leaving `recruiter`/`hiring_manager`
stale against an edited draft (README's old caveat).
"""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager
from typing import Literal

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from .config import settings
from .coverage import hard_gap_gate, log_skip, retrieve_evidence, score_coverage
from .nodes.diagnose import diagnose
from .nodes.hiring_manager import hiring_manager
from .nodes.recruiter_sim import log_recruiter_fail, recruiter_gate, recruiter_sim
from .nodes.requirements import extract_requirements
from .nodes.review import review, review_gate
from .nodes.rewrite import rewrite
from .nodes.validate_facts import fact_gate, log_fact_failure, validate_facts_node
from .render import render_documents
from .scoring.ats import score_ats
from .state import Draft, Job, JobState


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


def job_id(source: str, url: str, jd_text: str) -> str:
    """Identifies the POSTING, not the text (solution.md step 1): same source+url fetched
    twice is the same job even if the board re-serves slightly different whitespace. Manual
    pastes carry no url, so they fall back to the old content-hash id — content_hash
    (state.Job) already covers text identity for those."""
    if url:
        return hashlib.sha256(f"{source}:{url}".encode()).hexdigest()[:16]
    return hashlib.sha256(jd_text.strip().encode()).hexdigest()[:16]


def load_job(state: JobState) -> dict:
    j = state.job
    return {"notes": [f"loaded job {j.id} — {j.title or '(untitled)'} @ {j.company or '(unknown)'}"]}


def human_review(state: JobState) -> dict:
    """Pauses for approve/edit/reject (§9). Only reachable when `build_graph(review_pause=True)`
    wired it in — automated `run()`/`run_many()` batch runs never hit this node.

    `interrupt()` re-runs this node from the top on resume, so the whole body is cheap
    and side-effect-free apart from returning the decision."""
    decision = interrupt(
        {"draft": state.draft, "recruiter": state.recruiter, "hiring_manager": state.hiring_manager}
    )
    action = decision.get("action", "approve")
    if action == "edit":
        return {
            "draft": Draft.model_validate(decision["draft"]),
            "human_decision": "edit",
            "notes": ["human_review: edited — re-running recruiter_sim/hiring_manager"],
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
    g.add_node("extract_requirements", extract_requirements)
    g.add_node("retrieve_evidence", retrieve_evidence)
    g.add_node("score_coverage", score_coverage)
    g.add_node("log_skip", log_skip)
    g.add_node("diagnose", diagnose)
    g.add_node("rewrite", rewrite)
    g.add_node("validate_facts", validate_facts_node)
    g.add_node("log_fact_failure", log_fact_failure)
    g.add_node("review", review)
    g.add_node("recruiter_sim", recruiter_sim)
    g.add_node("log_recruiter_fail", log_recruiter_fail)
    g.add_node("hiring_manager", hiring_manager)
    g.add_node("render_documents", render_documents)
    g.add_node("score_ats", score_ats)

    g.set_entry_point("load_job")
    g.add_edge("load_job", "extract_requirements")
    g.add_edge("extract_requirements", "retrieve_evidence")
    g.add_edge("retrieve_evidence", "score_coverage")
    g.add_conditional_edges(
        "score_coverage", hard_gap_gate, {"skip": "log_skip", "proceed": "diagnose"}
    )
    g.add_edge("log_skip", END)
    g.add_edge("diagnose", "rewrite")
    g.add_edge("rewrite", "validate_facts")
    g.add_conditional_edges(
        "validate_facts",
        fact_gate,
        {"retry": "rewrite", "clean": "review", "give_up": "log_fact_failure"},
    )
    g.add_edge("log_fact_failure", END)
    g.add_conditional_edges(
        "review", review_gate,
        {"retry": "rewrite", "proceed": END if skip_render else "recruiter_sim"},
    )
    if not skip_render:
        g.add_conditional_edges(
            "recruiter_sim", recruiter_gate,
            {"hard_fail": "log_recruiter_fail", "proceed": "hiring_manager"},
        )
        g.add_edge("log_recruiter_fail", END)
        if review_pause:
            g.add_node("human_review", human_review)
            g.add_edge("hiring_manager", "human_review")
            g.add_conditional_edges(
                "human_review", human_review_gate,
                {"approve": "render_documents", "edit": "recruiter_sim", "reject": END},
            )
        else:
            g.add_edge("hiring_manager", "render_documents")
        g.add_edge("render_documents", "score_ats")
        g.add_edge("score_ats", END)
    return g.compile(checkpointer=checkpointer)


@asynccontextmanager
async def _open_checkpointer():
    """One `AsyncSqliteSaver` per call, WAL mode — concurrent `ainvoke`s (run_many) need
    WAL, not the default rollback journal, or writers block each other."""
    async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_db_path)) as saver:
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


async def _run(jd_text: str, title: str, company: str, job_fields: dict) -> JobState:
    skip_render = job_fields.pop("_skip_render", False)
    job = _build_job(jd_text, title, company, job_fields)
    async with _open_checkpointer() as saver:
        graph = build_graph(skip_render=skip_render, checkpointer=saver)
        config = {"configurable": {"thread_id": job.id}, "callbacks": _tracing_callbacks()}
        result = await graph.ainvoke(JobState(job=job), config=config, durability="sync")
    return JobState.model_validate(result)


def run(jd_text: str, title: str = "", company: str = "", **job_fields) -> JobState:
    """Run one JD through the graph and return the final state. Sync wrapper — the
    checkpointer (durability, §2) is async, so this opens its own event loop."""
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
    """
    from .budget import BudgetGuard, estimated_job_cost
    from .db.repo import already_processed

    skip_render = shared_job_fields.pop("_skip_render", False)
    semaphore = asyncio.Semaphore(settings.max_concurrent_jobs)
    config_callbacks = _tracing_callbacks()
    guard = BudgetGuard.for_today()

    async def _run_one(jd_text: str, graph) -> JobState:
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
                result = await graph.ainvoke(JobState(job=job), config=config, durability="sync")
                state = JobState.model_validate(result)
            except Exception:
                await guard.release(est_cost)
                raise
            await guard.settle(est_cost, state.total_cost_usd)
        return state

    async with _open_checkpointer() as saver:
        graph = build_graph(skip_render=skip_render, checkpointer=saver)
        return list(await asyncio.gather(*(_run_one(jd, graph) for jd in jd_texts)))


async def _run_for_review(jd_text: str, title: str, company: str, job_fields: dict) -> JobState:
    job = _build_job(jd_text, title, company, job_fields)
    async with _open_checkpointer() as saver:
        graph = build_graph(skip_render=False, checkpointer=saver, review_pause=True)
        config = {"configurable": {"thread_id": job.id}, "callbacks": _tracing_callbacks()}
        result = await graph.ainvoke(JobState(job=job), config=config, durability="sync")
    return JobState.model_validate(result)


def run_for_review(jd_text: str, title: str = "", company: str = "", **job_fields) -> JobState:
    """Like `run()`, but pauses at `human_review` (§9) — nothing is written to disk until
    `resume_review(..., action="approve")` renders it. The paused state has `draft`,
    `recruiter` and `hiring_manager` filled in; `artifacts`/`ats` are not yet set."""
    return asyncio.run(_run_for_review(jd_text, title, company, job_fields))


async def _resume_review(job_id_: str, action: Literal["approve", "edit", "reject"], draft: Draft | None) -> JobState:
    async with _open_checkpointer() as saver:
        graph = build_graph(skip_render=False, checkpointer=saver, review_pause=True)
        config = {"configurable": {"thread_id": job_id_}, "callbacks": _tracing_callbacks()}
        if action == "reject":
            # Never re-invokes the graph — just reads back the paused state and marks it
            # rejected, so no render happens and no further LLM calls are made.
            snapshot = await graph.aget_state(config)
            return JobState.model_validate(
                {**snapshot.values, "skip_reason": "rejected by user at review"}
            )
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
    back through `recruiter_sim`/`hiring_manager`). `action="edit"` re-runs those two
    nodes against the new draft and pauses again with a fresh verdict — this is what
    keeps `recruiter`/`hiring_manager` from going stale against an edited draft."""
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
