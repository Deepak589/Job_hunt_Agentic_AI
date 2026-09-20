"""Evidence store — per-bullet retrieval over the master profile (plan.md §5.2).

Phase 1 indexes CV bullets only: 20 chunks, one per bullet, text = outcome + metric +
method. Skill names are deliberately NOT embedded — they are the keyword half of the §6
two-signal match, and embedding them too would let one signal answer for both. Repo
READMEs and docs/*.md are §5.3, Phase 4.

Decomposing per bullet is the point (§5): "embed whole CV, embed whole JD, cosine" rates
"both documents are about tech" highly and tells you nothing actionable. A per-requirement
score against a specific bullet id does.
"""

from __future__ import annotations

import functools

import chromadb
from chromadb.api.models.Collection import Collection

from .config import settings
from .profile import Profile
from .state import Evidence


@functools.lru_cache(maxsize=1)
def _embedder():
    """bge-m3, loaded once. Multilingual: German JDs appear even when filtering for
    English roles, and an English-only embedder scores them near-random (§5.2)."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(settings.embedding_model)


@functools.lru_cache(maxsize=1)
def _reranker():
    """Cross-encoder, loaded once, same lazy pattern as _embedder(). Reads (query,
    candidate) jointly instead of comparing separately-embedded vectors, so it can catch
    cases where the bi-encoder's cosine top-1 isn't really the best match (config.py
    sem_threshold comment: the documented upgrade path)."""
    from sentence_transformers import CrossEncoder

    return CrossEncoder(settings.reranker_model)


def embed(texts: list[str]) -> list[list[float]]:
    # normalized vectors, so cosine distance is the meaningful metric
    return _embedder().encode(texts, normalize_embeddings=True).tolist()


def rerank(query: str, candidates: list[Evidence]) -> list[Evidence]:
    """Rescore retrieved candidates against `query` with the cross-encoder, most relevant
    first. Additive: sets `rerank_score` on a copy of each candidate, never touches
    `similarity` (cosine) — coverage.py's threshold gate reads that field, unchanged."""
    if not candidates:
        return candidates
    scores = _reranker().predict([(query, c.text) for c in candidates])
    rescored = [c.model_copy(update={"rerank_score": round(float(s), 4)}) for c, s in zip(candidates, scores)]
    return sorted(rescored, key=lambda e: -e.rerank_score)


def _client() -> chromadb.ClientAPI:
    settings.chroma_path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(settings.chroma_path))


def get_collection() -> Collection:
    """The existing collection. Raises if the index has not been built."""
    return _client().get_collection(settings.collection_name)


def build_index(profile: Profile | None = None, force: bool = False) -> int:
    """(Re)build the evidence collection. Returns the chunk count.

    20 chunks rebuild in seconds, so there is no SHA-based incremental caching — that is
    §5.3's problem, once real repo docs are in here.
    """
    profile = profile or Profile.load()
    client = _client()
    exists = settings.collection_name in {c.name for c in client.list_collections()}

    if exists and not force:
        return client.get_collection(settings.collection_name).count()
    if exists:
        client.delete_collection(settings.collection_name)

    collection = client.create_collection(
        settings.collection_name,
        # MUST be set explicitly. Chroma defaults to l2, where the `1 - distance`
        # conversion below is meaningless and every threshold downstream is garbage.
        metadata={"hnsw:space": "cosine"},
    )

    bullets = profile.bullets
    texts = [b.as_evidence_text() for b in bullets]
    collection.add(
        ids=[b.id for b in bullets],
        documents=texts,
        embeddings=embed(texts),
        metadatas=[
            {
                "source": b.source,
                "parent_id": b.parent_id,
                "tags": ",".join(b.tags),
                "status": b.status or "",
            }
            for b in bullets
        ],
    )
    return collection.count()


def retrieve_many(
    queries: list[str], k: int | None = None, collection: Collection | None = None, use_rerank: bool = False
) -> list[list[Evidence]]:
    """Top-k bullets for each query, most similar first. One batched encode, one query.

    Batched because a JD yields 12-18 requirements and encoding them one at a time is
    that many forward passes through bge-m3 for no reason.

    `rerank=False` by default — the hot path (coverage.py, every job run) re-sorts by
    cosine `similarity` anyway (see comment below), so loading the cross-encoder there
    bought nothing but latency. Only `calibrate()` needs the reranked order, to compare
    cosine-top-1 vs cross-encoder-top-1.
    """
    if not queries:
        return []
    collection = collection or get_collection()
    res = collection.query(
        query_embeddings=embed(queries),
        n_results=k or settings.top_k,
        include=["documents", "distances", "metadatas"],
    )
    groups = [
        [
            Evidence(
                source="cv_bullet",
                source_id=sid,
                text=doc,
                # cosine space: distance in [0, 2], similarity = 1 - distance
                similarity=round(1.0 - dist, 4),
                status=str(meta.get("status") or ""),
            )
            for sid, doc, dist, meta in zip(ids, docs, dists, metas)
        ]
        for ids, docs, dists, metas in zip(
            res["ids"], res["documents"], res["distances"], res["metadatas"]
        )
    ]
    if not use_rerank:
        return groups
    # Reorder each group by cross-encoder score. Harmless downstream: coverage.py's
    # retrieve_evidence merges evidence across subqueries and re-sorts by `similarity`
    # (cosine) before applying the top_k cutoff, so the coverage gate's evidence selection
    # is unaffected — this reordering is only visible to direct callers (retrieve(),
    # calibrate()) that consume the returned order/top-1 as-is.
    return [rerank(q, group) for q, group in zip(queries, groups)]


def retrieve(
    query: str, k: int | None = None, collection: Collection | None = None, use_rerank: bool = False
) -> list[Evidence]:
    """Top-k bullets for one requirement, most similar first."""
    return retrieve_many([query], k=k, collection=collection, use_rerank=use_rerank)[0]


# --------------------------------------------------------------------- calibration

# Hand-labeled probes: requirement-shaped queries the profile genuinely covers, and ones
# it genuinely does not. bge-m3 similarities sit high even for unrelated text, so a round
# number like 0.5 is wrong in an unknown direction — and a midpoint between one positive
# and one negative is wrong too: measured, that landed at 0.577, which would have marked
# "SQL data migration" (0.5721, real coverage via exp.valuemomentum.b2) as a gap.
#
# Keep these fixed so the number stays comparable across rebuilds. Add a probe whenever a
# real JD is misjudged; that is the intended way to retune this.
PROBES: list[tuple[str, bool]] = [
    # covered
    ("hybrid retrieval with BM25 and reranking", True),
    ("retrieval-augmented generation over a vector database", True),
    ("SQL data migration between systems", True),
    ("Docker and Kubernetes deployment", True),
    ("React frontend development", True),
    ("CI/CD pipelines with GitHub Actions", True),
    ("training and fine-tuning deep learning models in PyTorch", True),
    # not covered
    ("Rust systems programming and embedded firmware", False),
    ("fluent German language skills at C1 level", False),
    ("3 years of commercial production machine learning experience", False),
    ("Scala and Apache Spark big data engineering", False),
    ("Salesforce administration and Apex development", False),
    # observed false positives from real JD text, added 2026-09-14. Requirement text in a
    # live posting is longer and more contextual than a bare skill phrase, and these two
    # scored just above a threshold calibrated without them.
    ("Production experience with Apache Kafka", False),
    ("4+ years of professional backend engineering experience", False),
    ("Work with Scala for parts of our data platform", False),
]


def calibrate(collection: Collection | None = None) -> dict[str, object]:
    """Measure every probe and pick the threshold that separates them best.

    Returns the per-probe similarities plus the best threshold and how many probes it
    still gets wrong. A non-zero error count is the honest signal that the semantic
    signal alone cannot separate these — which is why §6 pairs it with keyword matching.
    """
    collection = collection or get_collection()
    # k=top_k, not 1: gives the reranker a pool to reorder. `max(group, key=similarity)`
    # below recovers the same cosine top-1 that k=1 used to return directly (chroma already
    # returns nearest-first, so the argmax over a top-k pool == the top-1 of a top-1 query),
    # so the cosine-only calibration below is unchanged by this.
    hits = retrieve_many([q for q, _ in PROBES], k=settings.top_k, collection=collection, use_rerank=True)
    measured = [
        (q, expected, max(group, key=lambda e: e.similarity), group[0])
        for (q, expected), group in zip(PROBES, hits)
    ]  # (query, expected_covered, cosine_top, rerank_top)
    sims = sorted({round(e.similarity, 4) for _, _, e, _ in measured})

    # candidate thresholds sit between adjacent observed similarities
    candidates = [round((a + b) / 2, 4) for a, b in zip(sims, sims[1:])]
    best, best_errors = candidates[0], len(measured)
    for t in candidates:
        errors = sum(1 for _, expected, e, _ in measured if (e.similarity >= t) != expected)
        if errors < best_errors:
            best, best_errors = t, errors

    # Reranked-accuracy comparison (upgrade-path evidence for config.py's sem_threshold
    # comment): does swapping which candidate is "top" — cosine top-1 vs cross-encoder
    # top-1 — change how many probes land correctly at the CURRENT production threshold?
    # Same threshold both columns, on purpose: this measures whether reranking picks a
    # better top-1, not whether rerank_score needs its own cutoff.
    current = settings.sem_threshold
    cosine_correct = sum(1 for _, expected, e, _ in measured if (e.similarity >= current) == expected)
    rerank_correct = sum(1 for _, expected, _, r in measured if (r.similarity >= current) == expected)
    rerank_changed_pick = sum(1 for _, _, e, r in measured if e.source_id != r.source_id)

    return {
        "suggested_threshold": best,
        "misclassified_probes": best_errors,
        "total_probes": len(measured),
        "current_threshold": current,
        "cosine_correct_at_current_threshold": cosine_correct,
        "rerank_correct_at_current_threshold": rerank_correct,
        "rerank_changed_top_pick": rerank_changed_pick,
        "covered_range": [
            min(e.similarity for _, exp, e, _ in measured if exp),
            max(e.similarity for _, exp, e, _ in measured if exp),
        ],
        "uncovered_range": [
            min(e.similarity for _, exp, e, _ in measured if not exp),
            max(e.similarity for _, exp, e, _ in measured if not exp),
        ],
        "probes": [
            {
                "query": q,
                "expected_covered": exp,
                "top_id": e.source_id,
                "similarity": e.similarity,
                "correct_at_suggested": (e.similarity >= best) == exp,
                "rerank_top_id": r.source_id,
                "rerank_score": r.rerank_score,
                "rerank_correct_at_current": (r.similarity >= current) == exp,
            }
            for q, exp, e, r in measured
        ],
    }
