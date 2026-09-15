"""tailor_graph — per-job state machine (plan.md §1), Phase 1 subset.

    load_job -> extract_requirements -> retrieve_evidence -> score_coverage
                                                                 |
                                       hard_gap_gate --skip--> log_skip -> END
                                                 \\--proceed--------------> END

Phase 2 attaches `diagnose -> rewrite -> validate_facts` to the proceed branch; Phase 3
adds the SqliteSaver checkpointer and the human interrupt. There is nothing to resume in
Phase 1 — one job, one invocation — so no checkpointer yet (§9).
"""

from __future__ import annotations

import hashlib

from langgraph.graph import END, StateGraph

from .coverage import hard_gap_gate, log_skip, retrieve_evidence, score_coverage
from .nodes.diagnose import diagnose
from .nodes.requirements import extract_requirements
from .nodes.rewrite import rewrite
from .state import Job, JobState


def job_id(jd_text: str) -> str:
    """Content hash — the dedupe key (§1) and the future checkpointer thread_id."""
    return hashlib.sha256(jd_text.strip().encode()).hexdigest()[:16]


def load_job(state: JobState) -> dict:
    j = state.job
    return {"notes": [f"loaded job {j.id} — {j.title or '(untitled)'} @ {j.company or '(unknown)'}"]}


def build_graph():
    g = StateGraph(JobState)
    g.add_node("load_job", load_job)
    g.add_node("extract_requirements", extract_requirements)
    g.add_node("retrieve_evidence", retrieve_evidence)
    g.add_node("score_coverage", score_coverage)
    g.add_node("log_skip", log_skip)
    g.add_node("diagnose", diagnose)
    g.add_node("rewrite", rewrite)

    g.set_entry_point("load_job")
    g.add_edge("load_job", "extract_requirements")
    g.add_edge("extract_requirements", "retrieve_evidence")
    g.add_edge("retrieve_evidence", "score_coverage")
    g.add_conditional_edges(
        "score_coverage",
        hard_gap_gate,
        {"skip": "log_skip", "proceed": END},  # Phase 2: "proceed" -> "diagnose"
    )
    g.add_edge("log_skip", END)
    return g.compile()


def run(jd_text: str, title: str = "", company: str = "", **job_fields) -> JobState:
    """Run one JD through the graph and return the final state."""
    job = Job(
        id=job_id(jd_text),
        source=job_fields.pop("source", "manual"),
        title=title,
        company=company,
        jd_text=jd_text,
        **job_fields,
    )
    return JobState.model_validate(build_graph().invoke(JobState(job=job)))
