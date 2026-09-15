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


def embed(texts: list[str]) -> list[list[float]]:
    # normalized vectors, so cosine distance is the meaningful metric
    return _embedder().encode(texts, normalize_embeddings=True).tolist()


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
    queries: list[str], k: int | None = None, collection: Collection | None = None
) -> list[list[Evidence]]:
    """Top-k bullets for each query, most similar first. One batched encode, one query.

    Batched because a JD yields 12-18 requirements and encoding them one at a time is
    that many forward passes through bge-m3 for no reason.
    """
    if not queries:
        return []
    collection = collection or get_collection()
    res = collection.query(
        query_embeddings=embed(queries),
        n_results=k or settings.top_k,
        include=["documents", "distances", "metadatas"],
    )
    return [
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


def retrieve(query: str, k: int | None = None, collection: Collection | None = None) -> list[Evidence]:
    """Top-k bullets for one requirement, most similar first."""
    return retrieve_many([query], k=k, collection=collection)[0]


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
    hits = retrieve_many([q for q, _ in PROBES], k=1, collection=collection)
    measured = [(q, expected, hit[0]) for (q, expected), hit in zip(PROBES, hits)]
    sims = sorted({round(e.similarity, 4) for _, _, e in measured})

    # candidate thresholds sit between adjacent observed similarities
    candidates = [round((a + b) / 2, 4) for a, b in zip(sims, sims[1:])]
    best, best_errors = candidates[0], len(measured)
    for t in candidates:
        errors = sum(1 for _, expected, e in measured if (e.similarity >= t) != expected)
        if errors < best_errors:
            best, best_errors = t, errors

    return {
        "suggested_threshold": best,
        "misclassified_probes": best_errors,
        "total_probes": len(measured),
        "covered_range": [
            min(e.similarity for _, exp, e in measured if exp),
            max(e.similarity for _, exp, e in measured if exp),
        ],
        "uncovered_range": [
            min(e.similarity for _, exp, e in measured if not exp),
            max(e.similarity for _, exp, e in measured if not exp),
        ],
        "probes": [
            {
                "query": q,
                "expected_covered": exp,
                "top_id": e.source_id,
                "similarity": e.similarity,
                "correct_at_suggested": (e.similarity >= best) == exp,
            }
            for q, exp, e in measured
        ],
    }
