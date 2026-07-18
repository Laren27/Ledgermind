"""
Embedder — generates dense and sparse vectors for Chunk objects.

Produces:
  Dense  : bge-small-en-v1.5 (384-dim, normalized, COSINE distance)
  Sparse : fastembed BM25 (Qdrant/bm25 model, indices + values)

Design decisions:
  - Lazy model loading: models load on first embed_chunks() call, stay warm
    in Celery worker process for all subsequent tasks in the same worker
  - No prefix on document side (BGE asymmetric retrieval convention)
    Query side adds prefix in Phase 4: "Represent this sentence for searching
    relevant passages: "
  - batch_size=32: safe for 8GB WSL2 cap, fast enough on CPU
  - normalize_embeddings=True: required for cosine similarity in Qdrant

Called by: pipeline.py (after chunker.py)
Returns:   List[EmbeddedChunk] ready for qdrant_writer.py
"""

import logging
from typing import Optional
from app.ingestion.models import normalize_quarter
from .models import Chunk, EmbeddedChunk

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DENSE_MODEL_NAME  = "BAAI/bge-small-en-v1.5"
SPARSE_MODEL_NAME = "Qdrant/bm25"
BATCH_SIZE        = 32
DENSE_DIMENSIONS  = 384   # bge-small output dimension — must match Qdrant collection

# ---------------------------------------------------------------------------
# Lazy model registry
# ---------------------------------------------------------------------------

_dense_model  = None
_sparse_model = None


def _get_dense_model():
    """
    Load sentence-transformers dense model on first call.
    Subsequent calls return the cached instance.
    """
    global _dense_model
    if _dense_model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading dense model: %s", DENSE_MODEL_NAME)
        _dense_model = SentenceTransformer(DENSE_MODEL_NAME)
        logger.info("Dense model loaded — output dim: %d", DENSE_DIMENSIONS)
    return _dense_model


def _get_sparse_model():
    """
    Load fastembed BM25 sparse model on first call.
    Downloads ~50MB model to ~/.cache/fastembed on first use.
    """
    global _sparse_model
    if _sparse_model is None:
        from fastembed import SparseTextEmbedding
        logger.info("Loading sparse model: %s", SPARSE_MODEL_NAME)
        _sparse_model = SparseTextEmbedding(model_name=SPARSE_MODEL_NAME)
        logger.info("Sparse model loaded.")
    return _sparse_model


# ---------------------------------------------------------------------------
# Core embedding functions
# ---------------------------------------------------------------------------

def _embed_dense(texts: list[str]) -> list[list[float]]:
    """
    Generate normalized dense embeddings for a list of texts.
    Returns list of 384-dim float vectors.
    """
    model = _get_dense_model()
    embeddings = model.encode(
        texts,
        batch_size=BATCH_SIZE,
        normalize_embeddings=True,   # required for cosine similarity
        show_progress_bar=False,
    )
    return embeddings.tolist()


def _embed_sparse(texts: list[str]) -> list[tuple[list[int], list[float]]]:
    """
    Generate BM25 sparse embeddings for a list of texts.
    Returns list of (indices, values) tuples for Qdrant sparse vector format.
    """
    model = _get_sparse_model()
    sparse_embeddings = list(model.embed(texts))

    result = []
    for emb in sparse_embeddings:
        # fastembed returns objects with .indices and .values attributes
        indices = emb.indices.tolist()
        values  = emb.values.tolist()
        result.append((indices, values))

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def embed_chunks(
    chunks: list[Chunk],
    batch_size: Optional[int] = None,
) -> list[EmbeddedChunk]:
    """
    Generate dense and sparse vectors for a list of Chunk objects.

    Args:
        chunks:     List[Chunk] from chunker.chunk_blocks()
        batch_size: Override default batch size (useful for testing)

    Returns:
        List[EmbeddedChunk] with dense_vector, sparse_indices, sparse_values populated.
        Same order as input chunks.

    Raises:
        RuntimeError if embedding fails (model load error, OOM, etc.)
        Individual chunk failures are logged and skipped — never raise on partial failure.
    """
    if not chunks:
        logger.info("embed_chunks called with empty list — nothing to do")
        return []

    effective_batch = batch_size or BATCH_SIZE
    texts = [c.text for c in chunks]

    logger.info(
        "Embedding %d chunks in batches of %d (dense + sparse)",
        len(chunks), effective_batch,
    )

    # --- Dense embeddings ---
    try:
        dense_vectors = _embed_dense(texts)
        logger.info("Dense embeddings complete: %d vectors", len(dense_vectors))
    except Exception as e:
        raise RuntimeError(f"Dense embedding failed: {e}") from e

    # --- Sparse embeddings ---
    try:
        sparse_pairs = _embed_sparse(texts)
        logger.info("Sparse embeddings complete: %d vectors", len(sparse_pairs))
    except Exception as e:
        raise RuntimeError(f"Sparse embedding failed: {e}") from e

    # --- Assemble EmbeddedChunk objects ---
    embedded: list[EmbeddedChunk] = []
    skipped = 0

    for chunk, dense_vec, (sparse_idx, sparse_val) in zip(chunks, dense_vectors, sparse_pairs):

        # Validate dense vector
        if len(dense_vec) != DENSE_DIMENSIONS:
            logger.error(
                "Dense vector dim mismatch: expected %d, got %d — skipping chunk %s",
                DENSE_DIMENSIONS, len(dense_vec), chunk.chunk_id,
            )
            skipped += 1
            continue

        # Validate sparse vector (not empty)
        if not sparse_idx:
            logger.warning(
                "Empty sparse vector for chunk %s (text: '%s...') — storing with zero sparse",
                chunk.chunk_id, chunk.text[:50],
            )
            # Don't skip — zero sparse vector is valid (very short text)

        embedded.append(EmbeddedChunk(
            chunk=chunk,
            dense_vector=dense_vec,
            sparse_indices=sparse_idx,
            sparse_values=sparse_val,
        ))

    if skipped:
        logger.warning("Skipped %d chunks due to embedding errors", skipped)

    logger.info(
        "Embedding complete: %d EmbeddedChunks produced (%d skipped)",
        len(embedded), skipped,
    )
    return embedded


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import os
    import sys
    import time
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from pathlib import Path
    from .db_loader import get_connection
    from .document_classifier import classify_and_register, detect_sections
    from .pdf_parser import parse_pdf
    from .section_classifier import classify_blocks
    from .chunker import chunk_blocks

    parser = argparse.ArgumentParser()
    parser.add_argument("pdf_path", nargs="?", default=os.path.expanduser(
        "~/ledgermind/docs/raw/ETERNAL_Q4FY26_SHAREHOLDER_LETTER_AND_RESULTS.pdf"))
    parser.add_argument("--company", default="ETERNAL")
    parser.add_argument("--ticker", default="ETERNAL")
    parser.add_argument("--fiscal-year", default="FY26")
    parser.add_argument("--quarter", default="Q4")
    parser.add_argument("--doc-type", default="quarterly_result")
    parser.add_argument("--filing-date", default="2026-04-28")
    parser.add_argument("--batch-size", type=int, default=None,
                         help="Override embed batch size — lower this for large docs on WSL2")
    args = parser.parse_args()

    pdf_path = Path(args.pdf_path)
    quarter = normalize_quarter(args.quarter)
    ALPHA_TENANT = "a0000000-0000-0000-0000-000000000001"

    print(f"\nParsing: {pdf_path.name}")
    blocks = parse_pdf(str(pdf_path))
    sections = detect_sections(blocks)

    conn = get_connection()
    try:
        sections = classify_and_register(
            blocks=blocks, pdf_path=pdf_path, tenant_id=ALPHA_TENANT,
            company=args.company, ticker=args.ticker, fiscal_year=args.fiscal_year,
            quarter=quarter, doc_type=args.doc_type,
            filing_date=args.filing_date, conn=conn,
        )
    finally:
        conn.close()

    blocks = classify_blocks(blocks, sections)
    chunks = chunk_blocks(
        blocks=blocks, sections=sections, tenant_id=ALPHA_TENANT,
        company=args.company, ticker=args.ticker, fiscal_year=args.fiscal_year,
        quarter=quarter, document_type=args.doc_type, filing_date=args.filing_date,
    )
    print(f"Chunks to embed: {len(chunks)}")

    print("\n--- Embedding ---")
    t0 = time.time()
    embedded = embed_chunks(chunks, batch_size=args.batch_size)
    elapsed = time.time() - t0

    print(f"\nEmbedded      : {len(embedded)} chunks")
    print(f"Time elapsed  : {elapsed:.1f}s")
    print(f"Throughput    : {len(embedded)/elapsed:.1f} chunks/sec")

    print("\n--- Vector spot checks ---")
    first = embedded[0]
    print(f"  chunk_id     : {first.chunk.chunk_id}")
    print(f"  text preview : {first.chunk.text[:80].replace(chr(10), ' ')}...")
    print(f"  dense dim    : {len(first.dense_vector)}")
    print(f"  sparse terms : {len(first.sparse_indices)} non-zero indices")

    import math
    norm = math.sqrt(sum(x**2 for x in first.dense_vector))
    print(f"  dense norm   : {norm:.6f} (should be ≈ 1.0)")

    assert len(embedded) == len(chunks)
    for ec in embedded:
        assert len(ec.dense_vector) == DENSE_DIMENSIONS
        assert ec.chunk.chunk_id
        assert ec.chunk.metadata.doc_id

    for ec in embedded[:10]:
        n = math.sqrt(sum(x**2 for x in ec.dense_vector))
        assert 0.99 < n < 1.01, f"Vector not normalized: norm={n}"

    fs_chunks = [ec for ec in embedded if ec.chunk.metadata.chunk_type == "FINANCIAL_STATEMENT"]
    for ec in fs_chunks:
        assert len(ec.sparse_indices) > 0

    print(f"\nAll assertions passed.")
    print(f"\nEmbeddedChunks are ready for qdrant_writer.py")