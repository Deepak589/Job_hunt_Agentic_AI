"""JD umbrella terms, and the profile evidence that makes each one honest.

`validate_facts` check 3 asks one question of every hard JD keyword a draft prints: does
the evidence IN SCOPE name this term? For a tool that question is exact and the answer is
a word — "Kubernetes" is either in the scope or fabricated. For an umbrella it is not. The
profile never spells the umbrella out; it names the things the umbrella covers. There is no
`LLM` skill in master_profile.yaml, there is LangChain, LangGraph, Ollama, transformers,
Hugging Face and Retrieval-Augmented Generation.

So a draft that used the posting's own word for work the candidate really did was reported
as fabrication. On `good_jd` (Linde, "Working Student - AI Engineering (Generative AI &
Agents)") that was 5 of 5 fact errors — `llm`, `llms`, `generative ai`, `agentic ai`,
`business applications` — every one of them true, and all five traceable to a single JD
line. The cost was not just noise: it burned a Sonnet rewrite retry per run, and twice sent
a correct draft to `log_fact_failure` after the retry budget ran out.

`prompts/agent_cv.md` already states the policy in prose — "naming the umbrella a JD uses
('generative AI', 'agentic AI') is fine when the profile's own work is that thing". This
table is that policy made deterministic, so the guardrail enforces it instead of a prompt
asking for it.

This is NOT a whitelist, and it does not loosen the matcher:

  * An alias resolves against the SAME evidence scope the term was already being checked
    in — the cited bullet's own evidence for a bullet, profile-wide for the cover letter.
    A bullet built from the insurance data migration still cannot say "LLM": nothing in
    that bullet's scope names LangChain or any other backing term.
  * Every term on the right-hand side is a real profile term today. If the profile stops
    containing the backing work, the umbrella stops passing by itself — no edit here
    needed, and none that would hide the change.
  * The table is phrase-exact, not fuzzy. Nothing is matched by prefix or similarity.

Adding a row: put the JD's umbrella on the left and, on the right, only terms already in
`Profile.all_tech() | all_stack()` or in a bullet's own outcome/method text. Never add a
bare umbrella with thin backing to make a run pass — that is the one move this file must
not become a vehicle for.
"""

from __future__ import annotations

from ..coverage import _normalize

# Left: the umbrella as a JD writes it. Right: profile terms any ONE of which makes it true.
# Both sides are normalized at import, so write them here the way a human would.
_RAW_UMBRELLA_EVIDENCE: dict[str, tuple[str, ...]] = {
    # The JD line that produced all five errors: "familiar with Large Language Models
    # (LLMs), Generative AI, Agentic AI concepts, or AI application development".
    #
    # "llms" folds to "llm" through `coverage._singular`, so one row covers both spellings.
    "llm": (
        "langchain",
        "langgraph",
        "ollama",
        "transformers",
        "hugging face",
        "distilbert",
        "retrieval-augmented generation",
        "rag",
        "sentence-transformers",
    ),
    "large language models": (
        "langchain",
        "langgraph",
        "ollama",
        "transformers",
        "hugging face",
        "distilbert",
        "retrieval-augmented generation",
        "rag",
        "sentence-transformers",
    ),
    # Generative AI: the RAG and fine-tuning work, not a claim about training foundation
    # models. CLIP/DistilBERT fine-tuning and the hybrid retriever are what back this.
    "generative ai": (
        "langchain",
        "langgraph",
        "retrieval-augmented generation",
        "rag",
        "fine-tuning",
        "clip",
        "distilbert",
        "ollama",
    ),
    # Agentic AI: the multi-agent LangGraph pipeline. "agent" appears in that project's
    # own bullet text ("built a multi-agent pipeline..."), so the bullet-scoped check
    # passes for exactly those bullets and no others. No "multi-agent" entry: the scopes
    # hold phrases only for skill and stack names, and word tokens for bullet text, so a
    # two-word phrase that exists only inside prose would never match — a dead row, which
    # `test_every_alias_target_is_a_real_profile_term` is there to keep out.
    "agentic ai": ("langgraph", "agent", "langchain"),
    # Not a technology at all — a JD's word for the kind of software the work ran inside.
    # Backed by the OneShield policy-admin system (2.7 years of insurance platform work)
    # and the production FastAPI health app, not by any AI term above.
    "business applications": ("oneshield", "insurance", "fastapi"),
    "enterprise applications": ("oneshield", "insurance", "fastapi"),
}

UMBRELLA_EVIDENCE: dict[str, tuple[str, ...]] = {
    _normalize(umbrella): tuple(_normalize(t) for t in backing)
    for umbrella, backing in _RAW_UMBRELLA_EVIDENCE.items()
}


def is_evidenced(term: str, scope: frozenset[str] | set[str]) -> bool:
    """True if `term` is named in `scope`, or is an umbrella whose backing work is.

    `term` and `scope` must both already be `coverage._normalize`d — the caller holds
    the scope, this function never widens it.
    """
    if term in scope:
        return True
    return any(backing in scope for backing in UMBRELLA_EVIDENCE.get(term, ()))
