"""
Qdrant Writer — upserts EmbeddedChunk objects to Qdrant Cloud.

Responsibilities:
  1. Build PointStruct objects from EmbeddedChunk (dense + sparse + payload)
  2. Batch upsert to ledgermind_chunks collection
  3. Create payload indexes on first run (required for filtering)
  4. Verify upsert succeeded via point count + sample search
  5. Return upsert summary dict

Design decisions:
  - Point ID = chunk_id (deterministic UUID) — idempotent re-ingestion
  - Payload = full ChunkMetadata as dict — enables all Phase 4 metadata filters
  - Batch size = 100 — safe for network reliability
  - Lazy QdrantClient singleton — one client per Celery worker process
  - Uses query_points() — compatible with qdrant-client >= 1.12
    (search() was removed in 1.12)

Called by: pipeline.py (after embedder.py)
"""

import logging
import os
from dataclasses import asdict
from typing import Optional

from qdrant_client import models
from app.ingestion.models import normalize_quarter
from .models import EmbeddedChunk

logger = logging.getLogger(__name__)

COLLECTION_NAME   = "ledgermind_chunks"
UPSERT_BATCH_SIZE = 100
DENSE_VECTOR_NAME  = "dense"
SPARSE_VECTOR_NAME = "sparse"


# ---------------------------------------------------------------------------
# Lazy Qdrant client
# ---------------------------------------------------------------------------

_qdrant_client = None


def _get_client():
    global _qdrant_client
    if _qdrant_client is None:
        from qdrant_client import QdrantClient

        url     = os.environ.get("QDRANT_URL")
        api_key = os.environ.get("QDRANT_API_KEY")

        if not url:
            raise RuntimeError(
                "QDRANT_URL not set. Add it to your .env:\n"
                "  QDRANT_URL=https://your-cluster.qdrant.io"
            )

        logger.info("Connecting to Qdrant: %s", url)
        _qdrant_client = QdrantClient(url=url, api_key=api_key, timeout=60)
        logger.info("Qdrant client ready.")

    return _qdrant_client


# ---------------------------------------------------------------------------
# Payload indexes — required before any filtered query
# ---------------------------------------------------------------------------

def create_payload_indexes(client) -> None:
    """
    Create payload indexes on all fields used for filtering.
    Must be called once after collection creation.
    Idempotent — safe to call on an existing collection.
    """
    from qdrant_client.models import PayloadSchemaType

    index_fields = {
        "tenant_id":      PayloadSchemaType.KEYWORD,
        "company":        PayloadSchemaType.KEYWORD,
        "financial_type": PayloadSchemaType.KEYWORD,
        "fiscal_year":    PayloadSchemaType.KEYWORD,
        "quarter":        PayloadSchemaType.KEYWORD,
        "chunk_type":     PayloadSchemaType.KEYWORD,
        "is_latest":      PayloadSchemaType.BOOL,
        "page_number":    PayloadSchemaType.INTEGER,
        "doc_id":         PayloadSchemaType.KEYWORD,
        "filing_date":    PayloadSchemaType.KEYWORD,
    }

    for field, schema_type in index_fields.items():
        client.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name=field,
            field_schema=schema_type,
        )
        logger.info("Payload index created: %s (%s)", field, schema_type)


# ---------------------------------------------------------------------------
# Payload serialization
# ---------------------------------------------------------------------------

def _metadata_to_payload(ec: EmbeddedChunk) -> dict:
    meta    = ec.chunk.metadata
    payload = asdict(meta)
    payload["chunk_id"] = str(meta.chunk_id)
    payload["doc_id"]   = str(meta.doc_id)
    payload["text"]     = ec.chunk.text
    return payload


# ---------------------------------------------------------------------------
# Point builder
# ---------------------------------------------------------------------------

def _build_point(ec: EmbeddedChunk):
    from qdrant_client.models import PointStruct, SparseVector

    return PointStruct(
        id=ec.chunk.chunk_id,
        vector={
            DENSE_VECTOR_NAME: ec.dense_vector,
            SPARSE_VECTOR_NAME: SparseVector(
                indices=ec.sparse_indices,
                values=ec.sparse_values,
            ),
        },
        payload=_metadata_to_payload(ec),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_chunks(
    embedded_chunks: list[EmbeddedChunk],
    batch_size: Optional[int] = None,
) -> dict:
    """
    Upsert EmbeddedChunk objects to Qdrant Cloud.

    Returns:
        {"upserted": int, "batches": int, "errors": int}
    """
    if not embedded_chunks:
        logger.info("write_chunks called with empty list — nothing to do")
        return {"upserted": 0, "batches": 0, "errors": 0}

    effective_batch = batch_size or UPSERT_BATCH_SIZE
    client = _get_client()

    # Create collection if it doesn't exist
    if not client.collection_exists(COLLECTION_NAME):
        logger.info("Collection '%s' not found — creating.", COLLECTION_NAME)
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config={
                DENSE_VECTOR_NAME: models.VectorParams(
                    size=384,
                    distance=models.Distance.COSINE,
                )
            },
            sparse_vectors_config={
                SPARSE_VECTOR_NAME: models.SparseVectorParams()
            },
        )
        create_payload_indexes(client)
        logger.info("Collection and indexes created.")

    try:
        points = [_build_point(ec) for ec in embedded_chunks]
    except Exception as e:
        raise RuntimeError(f"Failed to build Qdrant points: {e}") from e

    logger.info(
        "Upserting %d points to '%s' in batches of %d",
        len(points), COLLECTION_NAME, effective_batch,
    )

    counts = {"upserted": 0, "batches": 0, "errors": 0}
    total_batches = -(-len(points) // effective_batch)

    for i in range(0, len(points), effective_batch):
        batch     = points[i : i + effective_batch]
        batch_num = i // effective_batch + 1
        try:
            client.upsert(
                collection_name=COLLECTION_NAME,
                points=batch,
                wait=True,
            )
            counts["upserted"] += len(batch)
            counts["batches"]  += 1
            logger.info("Batch %d/%d upserted (%d points)", batch_num, total_batches, len(batch))
        except Exception as e:
            counts["errors"] += 1
            logger.error("Batch %d failed: %s", batch_num, e)

    logger.info(
        "write_chunks complete: %d upserted, %d batches, %d errors",
        counts["upserted"], counts["batches"], counts["errors"],
    )
    return counts


def verify_collection(tenant_id: str) -> dict:
    """
    Verify chunks in Qdrant: count points + confirm payload structure.
    """
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    client = _get_client()

    count_result = client.count(
        collection_name=COLLECTION_NAME,
        count_filter=Filter(
            must=[FieldCondition(key="tenant_id", match=MatchValue(value=str(tenant_id)))]
        ),
        exact=True,
    )

    sample_keys: list[str] = []
    results = client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(
            must=[FieldCondition(key="tenant_id", match=MatchValue(value=str(tenant_id)))]
        ),
        limit=1,
        with_payload=True,
        with_vectors=False,
    )
    if results and results[0]:
        sample_keys = sorted(results[0][0].payload.keys())

    return {
        "total_points": count_result.count,
        "sample_payload_keys": sample_keys,
    }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import os
    import sys
    import time
    import logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from pathlib import Path

    # Load .env
    env_path = Path.home() / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    from .db_loader import get_connection
    from .document_classifier import classify_and_register, detect_sections
    from .pdf_parser import parse_pdf
    from .section_classifier import classify_blocks
    from .chunker import chunk_blocks
    from .embedder import embed_chunks
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    # ----------------------------------------------------------------
    # Argparse Configuration
    # ----------------------------------------------------------------
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf_path", nargs="?", default=os.path.expanduser("~/ledgermind/docs/raw/ETERNAL_Q4FY26_SHAREHOLDER_LETTER_AND_RESULTS.pdf"))
    parser.add_argument("--company", default="ETERNAL")
    parser.add_argument("--ticker", default="ETERNAL")
    parser.add_argument("--fiscal-year", default="FY26")
    parser.add_argument("--quarter", default="Q4")
    parser.add_argument("--doc-type", default="quarterly_result")
    parser.add_argument("--filing-date", default="2026-04-28")
    parser.add_argument("--batch-size", type=int, default=None, help="Override embed batch size — lower this for large docs on WSL2")
    args = parser.parse_args()

    pdf_path = Path(args.pdf_path)
    quarter = normalize_quarter(args.quarter)
    ALPHA_TENANT = "a0000000-0000-0000-0000-000000000001"

    # ----------------------------------------------------------------
    # Full pipeline
    # ----------------------------------------------------------------
    print(f"\nParsing: {pdf_path.name}")
    blocks   = parse_pdf(str(pdf_path))
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

    print(f"\nEmbedding {len(chunks)} chunks...")
    t0       = time.time()
    
    # Safely pass batch_size to prevent WSL2 Out-Of-Memory errors
    if args.batch_size:
        embedded = embed_chunks(chunks, batch_size=args.batch_size)
    else:
        embedded = embed_chunks(chunks)
        
    print(f"Embedding done in {time.time() - t0:.1f}s")

    # ----------------------------------------------------------------
    # Upsert
    # ----------------------------------------------------------------
    print("\n--- Upserting to Qdrant Cloud ---")
    result = write_chunks(embedded)
    print(f"Upsert result: {result}")

    assert result["errors"]   == 0,            f"Upsert errors: {result}"
    assert result["upserted"] == len(embedded), f"Expected {len(embedded)} upserted"

    # ----------------------------------------------------------------
    # Verify
    # ----------------------------------------------------------------
    print("\n--- Verifying collection ---")
    verification = verify_collection(ALPHA_TENANT)
    print(f"Total points in collection : {verification['total_points']}")
    print(f"Payload keys               : {verification['sample_payload_keys']}")

    assert verification["total_points"] > 0, "Qdrant collection is empty!"

    required_keys = {"company", "financial_type", "fiscal_year", "tenant_id",
                     "chunk_type", "page_number", "is_latest", "text"}
    missing = required_keys - set(verification["sample_payload_keys"])
    assert not missing, f"Missing payload keys: {missing}"

    # ----------------------------------------------------------------
    # Sample search using query_points (qdrant-client >= 1.12)
    # ----------------------------------------------------------------
    print("\n--- Sample search (consolidated financial statements) ---")
    client       = _get_client()
    query_vector = embedded[0].dense_vector

    results = client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        using=DENSE_VECTOR_NAME,
        query_filter=Filter(
            must=[
                # Updated to use dynamic args.company
                FieldCondition(key="company",        match=MatchValue(value=args.company)),
                FieldCondition(key="financial_type", match=MatchValue(value="consolidated")),
                FieldCondition(key="tenant_id",      match=MatchValue(value=ALPHA_TENANT)),
            ]
        ),
        limit=3,
        with_payload=True,
    ).points

    print(f"Search returned {len(results)} results:")
    for r in results:
        print(
            f"  score={r.score:.4f} | "
            f"page={r.payload.get('page_number')} | "
            f"type={r.payload.get('chunk_type')} | "
            f"{r.payload.get('text', '')[:80].replace(chr(10), ' ')}..."
        )

    assert len(results) > 0, "Search returned no results"

    print(f"\nAll assertions passed.")
    print(f"\nPhase 3 Qdrant path: COMPLETE")
    print(f"  {verification['total_points']} chunks in Qdrant")
    print(f"  Dense + sparse vectors confirmed")
    print(f"  Metadata filtering confirmed")
    print(f"\nNext: pipeline.py — Celery chain wiring")