"""Cross-encoder reranking (evidence.rerank), tested in isolation — no network, no real
model. `_reranker()` is monkeypatched with a stub so the test exercises rerank()'s own
logic (reordering, score attachment, cosine preserved) rather than the model."""

from __future__ import annotations

import chromadb

from agentic_ai import evidence, repo_docs
from agentic_ai.profile import Profile
from agentic_ai.state import Evidence


def _candidates() -> list[Evidence]:
    return [
        Evidence(source="cv_bullet", source_id="a", text="alpha", similarity=0.60),
        Evidence(source="cv_bullet", source_id="b", text="beta", similarity=0.55),
    ]


class _StubCrossEncoder:
    def __init__(self, scores: list[float]) -> None:
        self.scores = scores
        self.calls: list[list[tuple[str, str]]] = []

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        self.calls.append(pairs)
        return self.scores


def test_rerank_can_reorder_by_cross_encoder_score(monkeypatch) -> None:
    # cosine ranks "a" first (0.60 > 0.55); cross-encoder disagrees and prefers "b".
    stub = _StubCrossEncoder(scores=[1.0, 9.0])
    monkeypatch.setattr(evidence, "_reranker", lambda: stub)

    out = evidence.rerank("query text", _candidates())

    assert [e.source_id for e in out] == ["b", "a"]
    assert stub.calls == [[("query text", "alpha"), ("query text", "beta")]]


def test_rerank_sets_rerank_score_without_touching_similarity(monkeypatch) -> None:
    stub = _StubCrossEncoder(scores=[1.0, 9.0])
    monkeypatch.setattr(evidence, "_reranker", lambda: stub)

    out = evidence.rerank("q", _candidates())

    by_id = {e.source_id: e for e in out}
    assert by_id["a"].rerank_score == 1.0
    assert by_id["b"].rerank_score == 9.0
    # cosine similarity is untouched — coverage.py's threshold gate depends on this
    assert by_id["a"].similarity == 0.60
    assert by_id["b"].similarity == 0.55


def test_rerank_empty_list_is_a_noop(monkeypatch) -> None:
    called = False

    def _fail():
        nonlocal called
        called = True
        raise AssertionError("reranker should not be loaded for an empty candidate list")

    monkeypatch.setattr(evidence, "_reranker", _fail)

    assert evidence.rerank("q", []) == []
    assert not called


def test_build_index_tags_repo_doc_chunks_and_retrieve_many_reports_their_source(monkeypatch):
    """build_index() must embed repo_doc chunks alongside bullets, and retrieve_many()
    must read `source` back from metadata instead of hardcoding "cv_bullet" — otherwise a
    retrieved repo_doc chunk silently relabels as a cv_bullet (the bug this step fixes)."""
    monkeypatch.setattr(evidence, "_client", lambda: chromadb.EphemeralClient())
    monkeypatch.setattr(evidence, "embed", lambda texts: [[0.1, 0.2, 0.3] for _ in texts])
    monkeypatch.setattr(
        repo_docs, "fetch_repo_files",
        lambda owner, repo: [{
            "path": "README.md",
            "sha": "abc123",
            "content": "# Built Agents\nI built autonomous agents myself using LangGraph.",
        }],
    )

    profile = Profile(
        raw={"projects": [{"id": "proj.rag", "repo": "github.com/Deepak589/RAG_pipeline"}]},
        bullets=[],
        skills=[],
    )
    count = evidence.build_index(profile=profile, force=True)
    assert count == 1

    collection = evidence.get_collection()
    got = collection.get(ids=["repo:proj.rag:README.md#built-agents"], include=["metadatas"])
    assert got["metadatas"][0]["source"] == "repo_doc"
    assert got["metadatas"][0]["parent_id"] == "proj.rag"

    [results] = evidence.retrieve_many(["built agents myself"], collection=collection)
    assert results[0].source == "repo_doc"
