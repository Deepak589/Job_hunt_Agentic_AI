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
                                                     [interrupt_before, when a checkpointer is set]
                                                                    v
                                              render_documents -> score_ats -> END

recruiter_sim/hiring_manager (roles 4-5, CLAUDE.md) only run on the full path —
`skip_render=True` stops at `review` exactly as in Phase 1/2, unchanged. The
SqliteSaver checkpointer + `interrupt_before=["render_documents"]` (§9) is opt-in via
`run_for_review`/`resume_review` below; `run()` (plain `jobpilot add`) has no
checkpointer and runs straight through as before, just with 2 more LLM calls.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Literal

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph

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


def job_id(jd_text: str) -> str:
    return hashlib.sha256(jd_text.strip().encode()).hexdigest()[:16]


def load_job(state: JobState) -> dict:
    j = state.job
    return {"notes": [f"loaded job {j.id} — {j.title or '(untitled)'} @ {j.company or '(unknown)'}"]}


def build_graph(skip_render: bool = False, checkpointer=None):
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
        g.add_edge("hiring_manager", "render_documents")
        g.add_edge("render_documents", "score_ats")
        g.add_edge("score_ats", END)
    interrupt_before = ["render_documents"] if checkpointer is not None and not skip_render else []
    return g.compile(checkpointer=checkpointer, interrupt_before=interrupt_before)


def run(jd_text: str, title: str = "", company: str = "", **job_fields) -> JobState:
    """Run one JD through the graph and return the final state."""
    skip_render = job_fields.pop("_skip_render", False)
    job = Job(
        id=job_id(jd_text),
        source=job_fields.pop("source", "manual"),
        title=title,
        company=company,
        jd_text=jd_text,
        **job_fields,
    )
    config = {"callbacks": _tracing_callbacks()}
    return JobState.model_validate(
        build_graph(skip_render=skip_render).invoke(JobState(job=job), config=config)
    )


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
    """
    from .budget import BudgetGuard, estimated_job_cost
    from .db.repo import already_processed

    skip_render = shared_job_fields.pop("_skip_render", False)
    graph = build_graph(skip_render=skip_render)
    semaphore = asyncio.Semaphore(settings.max_concurrent_jobs)
    config = {"callbacks": _tracing_callbacks()}
    guard = BudgetGuard.for_today()

    async def _run_one(jd_text: str) -> JobState:
        job = Job(
            id=job_id(jd_text),
            source=shared_job_fields.get("source", "manual"),
            title=shared_job_fields.get("title", ""),
            company=shared_job_fields.get("company", ""),
            jd_text=jd_text,
            **{k: v for k, v in shared_job_fields.items() if k not in ("source", "title", "company")},
        )
        async with semaphore:
            if already_processed(job.id):
                return JobState(job=job, skip_reason=f"already_processed (job_id={job.id})")

            est_cost = estimated_job_cost()
            if not await guard.try_reserve(est_cost):
                return JobState(job=job, skip_reason="budget_cap_hit")

            try:
                result = await graph.ainvoke(JobState(job=job), config=config)
                state = JobState.model_validate(result)
            except Exception:
                await guard.release(est_cost)
                raise
            await guard.settle(est_cost, state.total_cost_usd)
        return state

    return list(await asyncio.gather(*(_run_one(jd) for jd in jd_texts)))


def run_for_review(jd_text: str, title: str = "", company: str = "", **job_fields) -> JobState:
    """Like `run()`, but pauses before `render_documents` (§9) — nothing is written to
    disk until `resume_review` approves it. The paused state has `draft`, `recruiter`
    and `hiring_manager` filled in; `artifacts`/`ats` are not yet set."""
    job = Job(
        id=job_id(jd_text),
        source=job_fields.pop("source", "manual"),
        title=title,
        company=company,
        jd_text=jd_text,
        **job_fields,
    )
    with SqliteSaver.from_conn_string(str(settings.checkpoint_db_path)) as saver:
        graph = build_graph(skip_render=False, checkpointer=saver)
        config = {"configurable": {"thread_id": job.id}, "callbacks": _tracing_callbacks()}
        result = graph.invoke(JobState(job=job), config=config)
    return JobState.model_validate(result)


def resume_review(job_id_: str, approve: bool) -> JobState:
    """Resume a job paused by `run_for_review`. `approve=False` never re-invokes the
    graph — it just reads back the paused state and marks it rejected, so no render
    happens and no further LLM calls are made."""
    with SqliteSaver.from_conn_string(str(settings.checkpoint_db_path)) as saver:
        graph = build_graph(skip_render=False, checkpointer=saver)
        config: dict = {"configurable": {"thread_id": job_id_}, "callbacks": _tracing_callbacks()}
        if not approve:
            snapshot = graph.get_state(config)
            return JobState.model_validate(
                {**snapshot.values, "skip_reason": "rejected by user at review"}
            )
        result = graph.invoke(None, config=config)
    return JobState.model_validate(result)


def update_draft(job_id_: str, draft: Draft) -> None:
    """Overwrite the checkpointed `draft` for a job paused by `run_for_review` (§9 edit).
    Call before `resume_review` — `recruiter`/`hiring_manager` are NOT re-run, they still
    reflect the original draft."""
    with SqliteSaver.from_conn_string(str(settings.checkpoint_db_path)) as saver:
        graph = build_graph(skip_render=False, checkpointer=saver)
        config = {"configurable": {"thread_id": job_id_}}
        graph.update_state(config, {"draft": draft})


def get_paused_state(job_id_: str) -> JobState:
    """Read back a job paused by `run_for_review`, without resuming it."""
    with SqliteSaver.from_conn_string(str(settings.checkpoint_db_path)) as saver:
        graph = build_graph(skip_render=False, checkpointer=saver)
        config = {"configurable": {"thread_id": job_id_}}
        snapshot = graph.get_state(config)
    if not snapshot.values:
        raise KeyError(f"no paused job found for id {job_id_!r}")
    return JobState.model_validate(snapshot.values)
