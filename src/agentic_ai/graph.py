"""tailor_graph — per-job state machine, slimmed to one reasoning call.

    load_job -> agent_cv -> hard_gap_gate --skip--> log_skip -> END
                                  \\--proceed
                                       v
                                 validate_facts
                                       |
                             errors, attempts left
                                       v
                                    rewrite ----> validate_facts
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
extract / evidence-retrieval / coverage / judge / diagnose nodes are one reasoning call that
reads JD and profile together; the LLM `review`, `recruiter_sim` and `hiring_manager` nodes
are gone because the human review step is the reviewer. What stays deterministic and
non-negotiable: the hard-gap gate, `validate_facts` (no invented claims), rendering and
`score_ats`.

Who writes the first draft is `settings.use_agent_cv` (default True):

  * True  — `agent_cv` (nodes/agent_cv.py): one call returns the requirement verdicts AND
            the draft. The gate therefore runs after a draft exists, so a skipped job pays
            for one it never uses; config.py has the cost note.
  * False — `analyze` then `rewrite`, two calls, the gate between them.

The flag moves one node. It cannot move the guarantees: both topologies run the same
categorical hard-gap gate, the same BLOCKING `validate_facts` with the same retry budget,
the same `interrupt()` checkpoint, and the same render/score tail, and both are reached
through the same `run`/`run_many`/`run_for_review` entry points — so `record_run`, content-
hash dedupe, the cost ledger, `preferences.yaml` and MCP review visibility apply to either.
That is the whole reason the agent call lives in here instead of beside the graph: as a
second entry point (`jobpilot agent-draft`) it reached none of them, and its `validate_facts`
ran in report mode, which looks like a gate and is not.

A fact error always retries through `rewrite`, on both topologies — never back through
`agent_cv`, which would re-pay for extraction and could hand back a different requirement
list after `hard_gap_gate` had already ruled on the first one.

Durability: `run()`/`run_many()`/`run_for_review()`/`resume_review()` each open ONE
`AsyncSqliteSaver` per call (WAL mode), `thread_id = job.id`, `durability="sync"`, so a crashed
process can pick a job back up via `resume()` without re-paying for checkpointed nodes.

`human_review` calls `interrupt()` (dynamic) and is only wired in when
`build_graph(review_pause=True)`. "edit" validates the edited draft's facts and pauses again.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, Literal

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from .config import settings
from .coverage import hard_gap_gate, log_skip
from .nodes.agent_cv import agent_cv_node
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
    """First node of every run. Also clears the terminal fields a PREVIOUS run on this
    thread may have left behind.

    `thread_id` is the job id, so re-running a job resumes the same checkpoint, and a
    plain-valued channel with no reducer simply survives. `skip_reason` is the one that
    matters: the railway posting (2026-10-17) was skipped by the gate, then re-run after
    the typing fix and passed — reaching `human_review` with `validation_errors: []` while
    still carrying "6 uncovered hard requirements" from the run before. `persist_run` wrote
    that stale reason, so a successful run was recorded as a skip, with no verdict and no
    ats, and `already_processed` would never see it as finished.

    Cleared here rather than in the entry points because this node runs exactly when a new
    run begins: a crash-resume replays from the last unfinished node, and `load_job` has
    already completed by then, so a resumed run keeps its counters.
    """
    j = state.job
    return {
        "skip_reason": None,
        "validation_errors": [],
        "attempt_count": 0,
        "notes": [f"loaded job {j.id} — {j.title or '(untitled)'} @ {j.company or '(unknown)'}"],
    }


# `load_job`, plus the topology stamped onto the state.
#
# Stamped HERE and not by the drafting node, even though that node knows its own name: a
# node that crashes records nothing, and a crash inside the drafting node is precisely the
# case `_resume` needs the topology for (see `_snapshot_for`). `load_job` always completes,
# so the stamp is on the checkpoint before anything can fail. The drafting nodes write the
# same value again, which keeps them self-describing when unit-tested on their own.
#
# Two plain functions rather than one factory: `add_node` is overloaded on the node
# callable, and a `Callable`-typed value does not match any of its overloads under
# `mypy --strict`, while an ordinary `(JobState) -> dict` function does.
def load_job_agent_path(state: JobState) -> dict[str, Any]:
    return {**load_job(state), "graph_path": "agent_cv"}


def load_job_analyze_path(state: JobState) -> dict[str, Any]:
    return {**load_job(state), "graph_path": "analyze"}


def human_review(state: JobState) -> dict:
    """Pauses for approve/edit/reject. Only reachable when `build_graph(review_pause=True)`
    wired it in — automated `run()`/`run_many()` batch runs never hit this node.

    `interrupt()` re-runs this node from the top on resume, so the whole body is cheap
    and side-effect-free apart from returning the decision."""
    decision = interrupt({"draft": state.draft, "diagnosis": state.diagnosis})
    action = decision.get("action", "approve")
    if action == "edit":
        from .nodes.validate_facts import hard_keywords_for_facts
        from .profile import Profile
        from .validators.facts import validate_facts

        draft = Draft.model_validate(decision["draft"])
        # Same keyword derivation as the `validate_facts` node — an edited draft must be
        # held to exactly the standard the automatic check applies, not a second copy of it.
        errors = validate_facts(
            draft, Profile.load(), jd_keywords=hard_keywords_for_facts(state.requirements)
        )
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


def build_graph(
    skip_render: bool = False,
    checkpointer=None,
    review_pause: bool = False,
    use_agent_cv: bool | None = None,
):
    """`use_agent_cv=None` takes `settings.use_agent_cv`. Pass it explicitly only to
    rebuild the topology that wrote an existing checkpoint (see `_snapshot_for`)."""
    agent_path = settings.use_agent_cv if use_agent_cv is None else use_agent_cv
    g = StateGraph(JobState)
    g.add_node("load_job", load_job_agent_path if agent_path else load_job_analyze_path)
    g.add_node("log_skip", log_skip)
    g.add_node("rewrite", rewrite)
    g.add_node("validate_facts", validate_facts_node)
    g.add_node("log_fact_failure", log_fact_failure)
    g.add_node("render_documents", render_documents)
    g.add_node("score_ats", score_ats)

    g.set_entry_point("load_job")
    # The only structural difference between the two topologies. `agent_cv` arrives at the
    # gate with a draft already in state, so `proceed` goes straight to validation;
    # `analyze` arrives with requirements only, so `proceed` has to draft first.
    if agent_path:
        g.add_node("agent_cv", agent_cv_node)
        g.add_edge("load_job", "agent_cv")
        g.add_conditional_edges(
            "agent_cv", hard_gap_gate, {"skip": "log_skip", "proceed": "validate_facts"}
        )
    else:
        g.add_node("analyze", analyze)
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
        config = {"configurable": {"thread_id": job_id_}, "callbacks": _tracing_callbacks()}
        # Resume on the topology that paused this job. An "edit" loops back through
        # `human_review` and an "approve" runs render/score — nodes both topologies share —
        # but the graph being resumed still has to be the one the checkpoint was written
        # against, not whatever `settings.use_agent_cv` happens to say today.
        snapshot = await _snapshot_for(saver, {"configurable": {"thread_id": job_id_}})
        graph = build_graph(
            skip_render=False,
            checkpointer=saver,
            review_pause=True,
            use_agent_cv=_wrote_with_agent_cv(snapshot.values or {}),
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
    back through `human_review`). `action="edit"` fact-checks the new draft and pauses
    again with its fact errors, if any."""
    return asyncio.run(_resume_review(job_id_, action, draft))


async def _get_paused_state(job_id_: str) -> JobState:
    async with _open_checkpointer() as saver:
        snapshot = await _snapshot_for(saver, {"configurable": {"thread_id": job_id_}})
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


def _wrote_with_agent_cv(values: dict[str, Any]) -> bool:
    """Which topology wrote this checkpoint. `graph_path` is absent on every checkpoint
    written before that field existed, and those are all `analyze` runs."""
    return values.get("graph_path") == "agent_cv"


async def _snapshot_for(saver: AsyncSqliteSaver, config: dict[str, Any]) -> Any:
    """A thread's state read under the topology that WROTE it.

    Two separate ways `aget_state` under-reports, both the same root cause — it can only
    name a pending task whose node the probe graph registers:
      * `human_review` is absent from the plain (non-review) graph, so probe with
        review_pause=True, which is a superset of the plain topology;
      * `agent_cv` is absent from the analyze graph and `analyze` from the agent graph, so
        a thread that crashed mid-draft reads back with an EMPTY `next` under the other
        one — which `_resume` would take for "already finished" and return half-built
        state as a result. `graph_path` on the state says which to build.
    Reading twice is free: both calls are checkpoint reads, no node runs.
    """
    first = await build_graph(skip_render=False, checkpointer=saver, review_pause=True).aget_state(config)
    if not first.values:
        return first
    wrote_agent = _wrote_with_agent_cv(first.values)
    if wrote_agent == settings.use_agent_cv:
        return first
    graph = build_graph(
        skip_render=False, checkpointer=saver, review_pause=True, use_agent_cv=wrote_agent
    )
    return await graph.aget_state(config)


async def _resume(job_id_: str) -> JobState:
    async with _open_checkpointer() as saver:
        config = {"configurable": {"thread_id": job_id_}, "callbacks": _tracing_callbacks()}
        snapshot = await _snapshot_for(saver, config)
        if not snapshot.values:
            raise KeyError(f"no in-progress job found for id {job_id_!r}")
        if "human_review" in snapshot.next:
            raise ValueError(f"job {job_id_!r} is paused for review — use 'jobpilot review' instead")
        if not snapshot.next:
            return JobState.model_validate(snapshot.values)  # already finished
        # Finish it on the topology that started it, not on today's default: the remaining
        # nodes have to be the ones this checkpoint was partway through.
        graph = build_graph(
            skip_render=False,
            checkpointer=saver,
            use_agent_cv=_wrote_with_agent_cv(snapshot.values),
        )
        result = await graph.ainvoke(None, config=config, durability="sync")
    return JobState.model_validate(result)


def resume(job_id_: str) -> JobState:
    """Continue a job whose process crashed mid-run (§2 durability) — replays from the
    last checkpointed node, not from `load_job`, so `extract_requirements`/`diagnose`/
    `rewrite` already paid for are not re-run. Only for the plain (non `--review`) path;
    a job paused at `human_review` is continued via `resume_review` instead."""
    return asyncio.run(_resume(job_id_))
