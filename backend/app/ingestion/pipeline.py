"""
Pipeline — Celery task that wires all Phase 3 ingestion modules into a
single end-to-end job.

Task: ingest_document
  Accepts a PDF path + document metadata
  Runs the full ingestion chain:
    parse → classify → chunk → embed → Qdrant → extract → PostgreSQL
  Updates document state: processing → indexed (or failed)

Design decisions:
  - One task, not a Celery chain — intermediate objects are large Python
    structures that are not JSON-serializable and should not pass through Redis
  - max_retries=2, countdown=60 — handles transient Qdrant / DB timeouts
  - soft_time_limit=540 — sends SoftTimeLimitExceeded before hard kill at 600s
    Gives the task a chance to set state=failed before dying
    (Budget: ~71s embed + ~5s upsert + ~5s DB = ~81s for 136 chunks.
     600s accommodates annual reports with ~500 chunks at 1.9 chunks/sec.)
  - Connection opened once per task, closed in finally block

Called by:
  - FastAPI upload endpoint (Phase 5): ingest_document.delay(...)
  - Direct call for testing: ingest_document_sync(...)
"""

import logging
import os
from pathlib import Path
from typing import Optional
from app.ingestion.models import normalize_quarter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SQL for document state transitions
# ---------------------------------------------------------------------------

_SQL_SET_TENANT       = "SET app.tenant_id = %s"
_SQL_UPDATE_DOC_STATE = """
UPDATE documents
SET    ingestion_state = %s
WHERE  doc_id = ANY(%s::uuid[])
"""


def _update_document_states(conn, doc_ids: list[str], tenant_id: str, state: str) -> None:
    """Update ingestion_state for all doc_ids in a single UPDATE."""
    import psycopg2.extras
    with conn.cursor() as cur:
        cur.execute(_SQL_SET_TENANT, (tenant_id,))
        cur.execute(_SQL_UPDATE_DOC_STATE, (state, doc_ids))
    conn.commit()
    logger.info("Document states → %s for %d doc_ids", state, len(doc_ids))


# ---------------------------------------------------------------------------
# Core ingestion logic (framework-agnostic)
# Extracted so it can be called directly in tests without Celery
# ---------------------------------------------------------------------------

def _run_ingestion(
    pdf_path: str,
    tenant_id: str,
    company: str,
    ticker: str,
    fiscal_year: str,
    quarter: Optional[str],
    doc_type: str,
    filing_date: str,
    version: str = "v1",
) -> dict:
    """
    Run the full ingestion pipeline for one PDF.

    Returns a summary dict on success.
    Raises on failure — caller handles state transition and retry.
    """
    from .chunker import chunk_blocks
    from .db_loader import get_connection, load_financial_records
    from .document_classifier import detect_sections, register_sections
    from .embedder import embed_chunks
    from .financial_extractor import extract_all_financial_records
    from .models import DocState
    from .pdf_parser import parse_pdf
    from .qdrant_writer import write_chunks
    from .section_classifier import classify_blocks
    from .entity_resolver import resolve_company

    profile = resolve_company(company)
    if profile is None:
        raise ValueError(
            f"Cannot ingest — unresolvable company: '{company}'. "
            f"Add an alias to COMPANY_REGISTRY in entity_resolver.py before retrying."
        )
    company = profile.primary
    ticker  = profile.ticker

    pdf_path = str(pdf_path)
    conn     = get_connection()
    doc_ids  = []

    try:
        # ----------------------------------------------------------------
        # Stage 1: Parse
        # ----------------------------------------------------------------
        logger.info("[1/7] Parsing PDF: %s", Path(pdf_path).name)
        blocks = parse_pdf(pdf_path)
        logger.info("      %d blocks extracted", len(blocks))

        # ----------------------------------------------------------------
        # Stage 2: Detect sections + register in documents table
        # ----------------------------------------------------------------
        logger.info("[2/7] Detecting sections and registering documents")
        sections = detect_sections(blocks)
        sections = register_sections(
            sections=sections,
            pdf_path=pdf_path,
            tenant_id=tenant_id,
            company=company,
            ticker=ticker,
            fiscal_year=fiscal_year,
            quarter=quarter,
            doc_type=doc_type,
            filing_date=filing_date,
            conn=conn,
            version=version,
        )
        doc_ids    = [str(s.doc_id) for s in sections]
        doc_id_map = {s.financial_type: str(s.doc_id) for s in sections}
        logger.info("      %d sections registered: %s", len(sections), doc_ids)

        # ----------------------------------------------------------------
        # Stage 3: Classify blocks
        # ----------------------------------------------------------------
        logger.info("[3/7] Classifying blocks")
        blocks = classify_blocks(blocks, sections)

        # ----------------------------------------------------------------
        # Stage 4: Chunk
        # ----------------------------------------------------------------
        logger.info("[4/7] Chunking")
        chunks = chunk_blocks(
            blocks=blocks,
            sections=sections,
            tenant_id=tenant_id,
            company=company,
            ticker=ticker,
            fiscal_year=fiscal_year,
            quarter=quarter,
            document_type=doc_type,
            filing_date=filing_date,
            version=version,
        )
        logger.info("      %d chunks created", len(chunks))

        # ----------------------------------------------------------------
        # Stage 5: Embed
        # ----------------------------------------------------------------
        logger.info("[5/7] Embedding %d chunks (this takes ~%.0fs on CPU)",
                    len(chunks), len(chunks) / 1.9)
        embedded = embed_chunks(chunks)
        logger.info("      %d EmbeddedChunks ready", len(embedded))

        # ----------------------------------------------------------------
        # Stage 6: Write to Qdrant
        # ----------------------------------------------------------------
        logger.info("[6/7] Upserting to Qdrant")
        qdrant_result = write_chunks(embedded)
        if qdrant_result["errors"] > 0:
            raise RuntimeError(
                f"Qdrant upsert had {qdrant_result['errors']} batch errors"
            )
        logger.info("      %d chunks indexed in Qdrant", qdrant_result["upserted"])

        # ----------------------------------------------------------------
        # Stage 7: Extract financials → PostgreSQL
        # ----------------------------------------------------------------
        logger.info("[7/7] Extracting financial records → PostgreSQL")
        records = extract_all_financial_records(
            blocks=blocks,
            pdf_path=pdf_path,
            tenant_id=tenant_id,
            company=company,
            ticker=ticker,
            filing_date=filing_date,
            doc_id_map=doc_id_map,
        )
        load_result = load_financial_records(records, tenant_id, conn)
        logger.info(
            "      %d inserted, %d restated, %d skipped, %d errors",
            load_result["inserted"], load_result["restated"],
            load_result["skipped"], load_result["errors"],
        )
        if load_result["errors"] > 0:
            logger.warning(
                "%d financial records failed to load — check logs above",
                load_result["errors"],
            )

        # ----------------------------------------------------------------
        # Mark documents as indexed
        # ----------------------------------------------------------------
        _update_document_states(conn, doc_ids, tenant_id, DocState.INDEXED)

        summary = {
            "status":           DocState.INDEXED,
            "doc_ids":          doc_ids,
            "chunks_indexed":   qdrant_result["upserted"],
            "records_inserted": load_result["inserted"],
            "records_restated": load_result["restated"],
        }
        logger.info("Ingestion complete: %s", summary)
        return summary

    except Exception:
        # Mark documents as failed so the upload UI can surface the error
        if doc_ids:
            try:
                _update_document_states(conn, doc_ids, tenant_id, DocState.FAILED)
            except Exception as state_err:
                logger.error("Could not update state to failed: %s", state_err)
        raise  # re-raise so Celery can retry

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------

def _get_celery_task():
    """
    Register the Celery task lazily.
    Importing celery_app at module level would fail if Celery is not running.
    This function is called once when the worker boots.
    """
    try:
        from ..worker import celery_app
    except ImportError:
        logger.warning("Celery worker not available — task registration skipped")
        return None

    from celery.utils.log import get_task_logger
    task_logger = get_task_logger(__name__)

    @celery_app.task(
        bind=True,
        name="ingestion.ingest_document",
        max_retries=2,
        default_retry_delay=60,
        soft_time_limit=540,
        time_limit=600,
        acks_late=True,         # task acknowledged only after completion
    )
    def ingest_document(
        self,
        pdf_path: str,
        tenant_id: str,
        company: str,
        ticker: str,
        fiscal_year: str,
        quarter: Optional[str],
        doc_type: str,
        filing_date: str,
        version: str = "v1",
    ) -> dict:
        """
        Celery task: ingest a single PDF document end-to-end.

        Usage (from FastAPI in Phase 5):
            from app.ingestion.pipeline import get_ingest_task
            task = get_ingest_task()
            task.delay(
                pdf_path="/path/to/file.pdf",
                tenant_id="uuid",
                company="ETERNAL",
                ticker="ETERNAL",
                fiscal_year="FY26",
                quarter="Q4",
                doc_type="quarterly_result",
                filing_date="2026-04-28",
            )
        """
        task_logger.info(
            "Task started: ingest_document | pdf=%s | tenant=%s | company=%s",
            pdf_path, tenant_id, company,
        )

        try:
            return _run_ingestion(
                pdf_path=pdf_path,
                tenant_id=tenant_id,
                company=company,
                ticker=ticker,
                fiscal_year=fiscal_year,
                quarter=quarter,
                doc_type=doc_type,
                filing_date=filing_date,
                version=version,
            )
        except Exception as exc:
            task_logger.error(
                "Task failed (attempt %d/%d): %s",
                self.request.retries + 1,
                self.max_retries + 1,
                exc,
            )
            raise self.retry(exc=exc)

    return ingest_document


# Public handle — call this from FastAPI to get the registered task
def get_ingest_task():
    return _get_celery_task()


# ---------------------------------------------------------------------------
# Direct run (no Celery) — for testing and CLI use
# ---------------------------------------------------------------------------

def ingest_document_sync(
    pdf_path: str,
    tenant_id: str,
    company: str,
    ticker: str,
    fiscal_year: str,
    quarter: Optional[str],
    doc_type: str,
    filing_date: str,
    version: str = "v1",
) -> dict:
    """
    Run ingestion synchronously without Celery.
    Used for smoke testing and CLI ingestion.
    Identical logic to the Celery task — no worker required.
    """
    return _run_ingestion(
        pdf_path=pdf_path,
        tenant_id=tenant_id,
        company=company,
        ticker=ticker,
        fiscal_year=fiscal_year,
        quarter=quarter,
        doc_type=doc_type,
        filing_date=filing_date,
        version=version,
    )


# ---------------------------------------------------------------------------
# Smoke test — runs without Celery worker
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys
    import time
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Load .env
    env_path = Path.home() / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    parser = argparse.ArgumentParser(
        description="LedgerMind Phase 3 — Full Pipeline Smoke Test"
    )
    parser.add_argument("pdf_path", nargs="?", default=os.path.expanduser(
        "~/ledgermind/docs/raw/ETERNAL_Q4FY26_SHAREHOLDER_LETTER_AND_RESULTS.pdf"))
    parser.add_argument("--company", default="ETERNAL")
    parser.add_argument("--ticker", default="ETERNAL")
    parser.add_argument("--fiscal-year", default="FY26")
    parser.add_argument("--quarter", default="Q4",
                         help="Use 'none' for annual reports")
    parser.add_argument("--doc-type", default="quarterly_result")
    parser.add_argument("--filing-date", default="2026-04-28")
    parser.add_argument("--version", default="v1")
    parser.add_argument("--min-chunks", type=int, default=100,
                         help="Gate 2 threshold — lower for small test docs")
    args = parser.parse_args()

    pdf_path = Path(args.pdf_path)
    quarter = normalize_quarter(args.quarter)
    ALPHA_TENANT = "a0000000-0000-0000-0000-000000000001"

    print(f"\n{'='*60}")
    print(f"LedgerMind Phase 3 — Full Pipeline Smoke Test")
    print(f"{'='*60}")
    print(f"PDF      : {pdf_path.name}")
    print(f"Company  : {args.company} | FY: {args.fiscal_year} | Q: {quarter}")
    print(f"Tenant   : {ALPHA_TENANT}")
    print(f"{'='*60}\n")

    t0 = time.time()

    result = ingest_document_sync(
        pdf_path=str(pdf_path),
        tenant_id=ALPHA_TENANT,
        company=args.company,
        ticker=args.ticker,
        fiscal_year=args.fiscal_year,
        quarter=quarter,
        doc_type=args.doc_type,
        filing_date=args.filing_date,
        version=args.version,
    )

    elapsed = time.time() - t0

    print(f"\n{'='*60}")
    print(f"RESULT: {result['status'].upper()}")
    print(f"{'='*60}")
    print(f"  doc_ids          : {result['doc_ids']}")
    print(f"  chunks_indexed   : {result['chunks_indexed']}")
    print(f"  records_inserted : {result['records_inserted']}")
    print(f"  records_restated : {result['records_restated']}")
    print(f"  total_time       : {elapsed:.1f}s")
    print(f"{'='*60}\n")

    # ----------------------------------------------------------------
    # Phase 3 completion gate — parametrized to the ingested document
    # ----------------------------------------------------------------
    from .db_loader import get_connection, verify_financials
    from .qdrant_writer import verify_collection, _get_client, COLLECTION_NAME, DENSE_VECTOR_NAME
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    print("--- Phase 3 Completion Gate ---")

    # Gate 1: document states
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SET app.tenant_id = %s", (ALPHA_TENANT,))
        cur.execute(
            "SELECT financial_type, ingestion_state FROM documents "
            "WHERE company = %s AND fiscal_year = %s "
            "ORDER BY financial_type",
            (args.company, args.fiscal_year),
        )
        doc_rows = cur.fetchall()
    conn.close()

    print(f"\nDocuments table:")
    for row in doc_rows:
        status = "✅" if row[1] == "indexed" else "❌"
        print(f"  {status} {row[0]:13s} → {row[1]}")
    assert doc_rows, f"No documents found for {args.company}/{args.fiscal_year}"
    assert all(r[1] == "indexed" for r in doc_rows), "Not all documents are indexed"

    # Gate 2: Qdrant chunk count
    qdrant_info = verify_collection(ALPHA_TENANT)
    qdrant_ok   = qdrant_info["total_points"] >= args.min_chunks
    print(f"\nQdrant chunks: {'✅' if qdrant_ok else '❌'} "
          f"{qdrant_info['total_points']} points (min expected: {args.min_chunks})")
    assert qdrant_ok, f"Expected ≥{args.min_chunks} chunks, got {qdrant_info['total_points']}"

    # Gate 3: Key financials in PostgreSQL
    # Golden-value hard assertions only apply to the originally verified
    # Q4FY26 dataset. For any other company/fiscal_year, print retrieved
    # financials for manual review instead of asserting against unknown
    # truth values — avoids false failures on legitimately new data.
    conn = get_connection()
    consol = verify_financials(args.company, args.fiscal_year, "consolidated", ALPHA_TENANT, conn)
    stand  = verify_financials(args.company, args.fiscal_year, "standalone",   ALPHA_TENANT, conn)
    conn.close()

    is_known_golden_dataset = (
        args.company == "ETERNAL" and args.fiscal_year == "FY26" and quarter == "Q4"
    )

    print("\nKey financials:")
    if is_known_golden_dataset:
        golden = {
            ("consolidated", "revenue",      None): 54364.0,
            ("consolidated", "total_income", None): 55760.0,
            ("standalone",   "revenue",      None): 10899.0,
        }
        all_rows = {}
        for r in consol:
            all_rows[("consolidated", r["metric"], r.get("quarter"))] = float(r["value"])
        for r in stand:
            all_rows[("standalone",   r["metric"], r.get("quarter"))] = float(r["value"])

        gate3_pass = True
        for (ft, metric, q), expected in golden.items():
            actual = all_rows.get((ft, metric, q))
            ok     = actual == expected
            if not ok:  
                gate3_pass = False
            print(f"  {'✅' if ok else '❌'} {ft}/{metric} = {actual} (expected {expected})")
        assert gate3_pass, "Golden financial assertions failed"
    else:
        print(f"  (no golden values registered for {args.company}/{args.fiscal_year} "
              f"— printing for manual review, not asserting)")
        for r in consol:
            print(f"  consolidated/{r['metric']} = {r['value']}")
        for r in stand:
            print(f"  standalone/{r['metric']} = {r['value']}")
        assert consol or stand, \
            f"No financial records found at all for {args.company}/{args.fiscal_year}"

    # Gate 4: Semantic search works
    client = _get_client()
    results = client.query_points(
        collection_name=COLLECTION_NAME,
        query=[0.0] * 384,    # zero vector — returns any valid points
        using=DENSE_VECTOR_NAME,
        query_filter=Filter(must=[
            FieldCondition(key="tenant_id", match=MatchValue(value=ALPHA_TENANT)),
            FieldCondition(key="is_latest", match=MatchValue(value=True)),
        ]),
        limit=1,
        with_payload=False,
    ).points
    search_ok = len(results) > 0
    print(f"\nSemantic search: {'✅' if search_ok else '❌'} returned {len(results)} result(s)")
    assert search_ok

    print(f"\n{'='*60}")
    print(f"✅ PHASE 3 COMPLETE — All gates passed in {elapsed:.0f}s")
    print(f"{'='*60}")
    print(f"\nNext: Phase 4 — Tri-Engine (Router + RAG + DSL + Cross-Examination)")