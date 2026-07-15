"""
LedgerMind — Phase 4: Hybrid Retriever
========================================
Provides two public functions:
  hybrid_search() — Qdrant native RRF (dense + BM25 sparse) → top-N ChunkResults
  rerank()        — CrossEncoder reranking → top-K sorted ChunkResults

Design decisions:
  - Models are lazy-loaded singletons (loaded on first call, never at import).
    Docker startup stays fast; model load happens on first warm query.
  - Qdrant FusionQuery(Fusion.RRF) is used for native RRF — no manual score merging.
  - Filter runs INSIDE each prefetch leg (not at fusion level) so both dense and
    sparse candidates are pre-filtered before RRF. This is the correct pattern —
    filtering at fusion level would allow unfiltered candidates to pollute ranking.
  - is_latest=True is ALWAYS applied unless explicitly bypassed (historical queries).
  - tenant_id is ALWAYS applied — multi-tenant isolation is non-negotiable.
  - quarter filtering is OPTIONAL: if state.quarter is None, we retrieve across
    all periods for that fiscal_year (annual figures have quarter=None in payload).
"""

import logging
import os
from typing import List, Optional

import numpy as np
from fastembed import SparseTextEmbedding
from qdrant_client import QdrantClient
from qdrant_client.models import (
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchValue,
    Prefetch,
    SparseVector,
)
from sentence_transformers import CrossEncoder, SentenceTransformer

from app.engines.state import ChunkResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COLLECTION_NAME = "ledgermind_chunks"
DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"

TOP_K_RETRIEVAL = 20
TOP_K_RERANK = 5

DENSE_MODEL_NAME = "BAAI/bge-small-en-v1.5"
SPARSE_MODEL_NAME = "Qdrant/bm25"
RERANKER_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# ---------------------------------------------------------------------------
# Singleton model registry — lazy-loaded, one instance per Python process
# ---------------------------------------------------------------------------

_dense_model: Optional[SentenceTransformer] = None
_sparse_model: Optional[SparseTextEmbedding] = None
_reranker_model: Optional[CrossEncoder] = None
_qdrant_client: Optional[QdrantClient] = None


def _get_dense_model() -> SentenceTransformer:
    global _dense_model
    if _dense_model is None:
        logger.info("Loading dense embedding model: %s", DENSE_MODEL_NAME)
        _dense_model = SentenceTransformer(DENSE_MODEL_NAME)
    return _dense_model


def _get_sparse_model() -> SparseTextEmbedding:
    global _sparse_model
    if _sparse_model is None:
        logger.info("Loading BM25 sparse model: %s", SPARSE_MODEL_NAME)
        _sparse_model = SparseTextEmbedding(model_name=SPARSE_MODEL_NAME)
    return _sparse_model


def _get_reranker() -> CrossEncoder:
    global _reranker_model
    if _reranker_model is None:
        logger.info("Loading CrossEncoder reranker: %s", RERANKER_MODEL_NAME)
        _reranker_model = CrossEncoder(RERANKER_MODEL_NAME)
    return _reranker_model


def _get_qdrant_client() -> QdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        qdrant_url = os.getenv("QDRANT_URL")
        qdrant_api_key = os.getenv("QDRANT_API_KEY")
        if not qdrant_url:
            raise RuntimeError("QDRANT_URL environment variable not set")
        logger.info("Connecting to Qdrant: %s", qdrant_url)
        _qdrant_client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
    return _qdrant_client


# ---------------------------------------------------------------------------
# Query encoding
# ---------------------------------------------------------------------------

def _encode_dense(query: str) -> List[float]:
    """Encode query with bge-small-en-v1.5 → 384-dim dense vector."""
    model = _get_dense_model()
    prefixed = f"Represent this sentence for searching relevant passages: {query}"
    vector = model.encode(prefixed, normalize_embeddings=True)
    return vector.tolist()


def _encode_sparse(query: str) -> SparseVector:
    """Encode query with BM25 fastembed → SparseVector for Qdrant."""
    model = _get_sparse_model()
    sparse_result = next(model.query_embed(query))
    return SparseVector(
        indices=sparse_result.indices.tolist(),
        values=sparse_result.values.tolist(),
    )


# ---------------------------------------------------------------------------
# Filter construction
# ---------------------------------------------------------------------------

def _build_filter(
    tenant_id: str,
    company: Optional[str] = None,
    fiscal_year: Optional[str] = None,
    quarter: Optional[str] = None,
    financial_type: Optional[str] = None,
    is_latest: bool = True,
) -> Filter:
    """
    Build Qdrant Filter from query metadata.

    tenant_id and is_latest are ALWAYS applied.
    quarter is applied ONLY when explicitly provided — None means retrieve all periods.
    This allows annual-level queries (quarter=None) to match annual table chunks.
    """
    must_conditions = [
        FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
        FieldCondition(key="is_latest", match=MatchValue(value=is_latest)),
    ]

    if company:
        must_conditions.append(
            FieldCondition(key="company", match=MatchValue(value=company))
        )

    if fiscal_year:
        must_conditions.append(
            FieldCondition(key="fiscal_year", match=MatchValue(value=fiscal_year))
        )

    if quarter is not None:
        must_conditions.append(
            FieldCondition(key="quarter", match=MatchValue(value=quarter))
        )

    if financial_type:
        # Match the requested financial_type OR chunks that were never
        # scoped to either type (narrative/general content — see
        # section_classifier.py's classify_blocks for why FINANCIAL_STATEMENT
        # is the only block_type that gets a real financial_type tag).
        # A plain MatchValue-only filter would silently exclude all
        # untagged narrative chunks regardless of which financial_type
        # the query defaults to.
        must_conditions.append(
            Filter(
                should=[
                    FieldCondition(key="financial_type", match=MatchValue(value=financial_type)),
                    FieldCondition(key="financial_type", match=MatchValue(value="unknown")),
                ]
            )
        )

    return Filter(must=must_conditions)


# ---------------------------------------------------------------------------
# Core: hybrid_search
# ---------------------------------------------------------------------------

def hybrid_search(
    query: str,
    tenant_id: str,
    company: Optional[str] = None,
    fiscal_year: Optional[str] = None,
    quarter: Optional[str] = None,
    financial_type: str = "consolidated",
    top_k: int = TOP_K_RETRIEVAL,
) -> List[ChunkResult]:
    """
    Hybrid retrieval using Qdrant native RRF fusion.

    Both prefetch legs share the same metadata filter — this ensures the RRF
    pool only contains chunks matching the tenant/company/period constraints.
    Returns [] on Qdrant failure (caller checks length and sets confidence=LOW).
    """
    client = _get_qdrant_client()

    dense_vector = _encode_dense(query)
    sparse_vector = _encode_sparse(query)

    search_filter = _build_filter(
        tenant_id=tenant_id,
        company=company,
        fiscal_year=fiscal_year,
        quarter=quarter,
        financial_type=financial_type,
    )

    logger.debug(
        "hybrid_search | company=%s fiscal_year=%s quarter=%s financial_type=%s top_k=%d",
        company, fiscal_year, quarter, financial_type, top_k,
    )

    try:
        result = client.query_points(
            collection_name=COLLECTION_NAME,
            prefetch=[
                Prefetch(
                    query=dense_vector,
                    using=DENSE_VECTOR_NAME,
                    limit=top_k,
                    filter=search_filter,
                ),
                Prefetch(
                    query=sparse_vector,
                    using=SPARSE_VECTOR_NAME,
                    limit=top_k,
                    filter=search_filter,
                ),
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=top_k,
            with_payload=True,
        )
    except Exception as e:
        logger.error("Qdrant hybrid_search failed: %s", e)
        return []

    points = result.points
    logger.info("hybrid_search returned %d points", len(points))

    chunks: List[ChunkResult] = []
    for point in points:
        payload = point.payload or {}
        chunk = ChunkResult(
            chunk_id=str(point.id),
            doc_id=payload.get("doc_id", ""),
            text=payload.get("text", ""),
            page_number=payload.get("page_number", 0),
            company=payload.get("company", ""),
            fiscal_year=payload.get("fiscal_year", ""),
            quarter=payload.get("quarter"),
            financial_type=payload.get("financial_type", ""),
            chunk_type=payload.get("chunk_type", ""),
            filing_date=payload.get("filing_date", ""),
            dense_score=0.0,
            sparse_score=0.0,
            rrf_score=point.score,
            reranker_score=float("-inf"),
        )
        chunks.append(chunk)

    return chunks


# ---------------------------------------------------------------------------
# Core: rerank
# ---------------------------------------------------------------------------

def rerank(
    query: str,
    chunks: List[ChunkResult],
    top_k: int = TOP_K_RERANK,
) -> List[ChunkResult]:
    """
    Cross-encoder reranking of retrieved chunks.

    Scores every (query, chunk_text) pair with ms-marco-MiniLM-L-6-v2.
    Returns top_k chunks sorted by reranker_score (descending).
    reranker_score is a raw CrossEncoder logit — higher = better, range unbounded.
    Returns input unchanged if empty.
    """
    if not chunks:
        return chunks

    reranker = _get_reranker()
    pairs = [(query, chunk["text"]) for chunk in chunks]

    logger.debug("Reranking %d chunks with CrossEncoder", len(pairs))
    scores: np.ndarray = reranker.predict(pairs)

    scored_chunks = []
    for chunk, score in zip(chunks, scores):
        updated = dict(chunk)
        updated["reranker_score"] = float(score)
        scored_chunks.append(ChunkResult(**updated))

    scored_chunks.sort(key=lambda c: c["reranker_score"], reverse=True)
    top_chunks = scored_chunks[:top_k]

    if top_chunks:
        logger.info(
            "Reranking complete | top_score=%.4f | bottom_score=%.4f",
            top_chunks[0]["reranker_score"],
            top_chunks[-1]["reranker_score"],
        )

    return top_chunks


# ---------------------------------------------------------------------------
# Convenience: retrieve_and_rerank
# ---------------------------------------------------------------------------

def retrieve_and_rerank(
    query: str,
    tenant_id: str,
    company: Optional[str] = None,
    fiscal_year: Optional[str] = None,
    quarter: Optional[str] = None,
    financial_type: str = "consolidated",
    retrieval_top_k: int = TOP_K_RETRIEVAL,
    rerank_top_k: int = TOP_K_RERANK,
) -> List[ChunkResult]:
    """
    Single-call wrapper: hybrid_search → rerank.
    Used by semantic_engine and cross_engine.
    Returns [] if nothing retrieved — caller must check and set confidence=LOW.
    """
    candidates = hybrid_search(
        query=query,
        tenant_id=tenant_id,
        company=company,
        fiscal_year=fiscal_year,
        quarter=quarter,
        financial_type=financial_type,
        top_k=retrieval_top_k,
    )

    if not candidates:
        logger.warning("hybrid_search returned 0 results — skipping rerank")
        return []

    return rerank(query=query, chunks=candidates, top_k=rerank_top_k)