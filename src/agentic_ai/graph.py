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
                                                     proceed
                                                         v
                                              render_documents -> score_ats -> END

Phase 3 adds recruiter_sim, hiring_manager, the SqliteSaver checkpointer and the human
interrupt (§9). There is nothing to resume yet — one job, one invocation.
"""

from __future__ import annotations

import hashlib

from langgraph.graph import END, StateGraph

from .coverage import hard_gap_gate, log_skip, retrieve_evidence, score_coverage
from .nodes.diagnose import diagnose
from .nodes.requirements import extract_requirements
from .nodes.review import review, review_gate
from .nodes.rewrite import rewrite
from .nodes.validate_facts import fact_gate, log_fact_failure, validate_facts_node
from .render import render_documents
from .scoring.ats import score_ats
from .state import Job, JobState


def job_id(jd_text: str) -> str:
    return hashlib.sha256(jd_text.strip().encode()).hexdigest()[:16]


def load_job(state: JobState) -> dict:
    j = state.job
    return {"notes": [f"loaded job {j.id} — {j.title or '(untitled)'} @ {j.company or '(unknown)'}"]}


def build_graph(skip_render: bool = False):
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
        {"retry": "rewrite", "proceed": END if skip_render else "render_documents"},
    )
    if not skip_render:
        g.add_edge("render_documents", "score_ats")
        g.add_edge("score_ats", END)
    return g.compile()


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
    return JobState.model_validate(build_graph(skip_render=skip_render).invoke(JobState(job=job)))
