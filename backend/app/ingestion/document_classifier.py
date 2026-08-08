"""
Document Classifier — detects section boundaries within a PDF and registers
two entries in the documents table (consolidated + standalone).

Responsibilities:
  1. Scan List[PageBlock] for standalone section marker
  2. Emit two DocSection objects with correct page ranges
  3. Compute SHA256 of source PDF for deduplication
  4. Write two rows to documents table (one per section)
  5. Return DocSection objects with doc_ids populated

Design decisions:
  - detect_sections() is pure — no DB, fully testable without a connection
  - register_sections() owns all DB writes
  - If no marker found: logs a warning, creates ONE consolidated-only section
    and sets needs_review=True — never silently defaults to wrong financial_type
  - SHA256 stored as "{file_sha256}_{financial_type}" to allow two documents
    rows from one PDF while still catching duplicate uploads

Called by: pipeline.py (Celery task chain), after pdf_parser.parse_pdf()
"""

import hashlib
import logging
import uuid
from pathlib import Path
from typing import Optional

import psycopg2

from .models import DocSection, FinancialType, DocState, PageBlock , BlockType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Section marker patterns (ordered by specificity — first match wins)
# ---------------------------------------------------------------------------
# Strict SEBI markers to avoid triggering on Press Release prose.
# We demand "statement of" or auditor report language to ensure we are 
# at the actual tables, not just a casual mention in a paragraph.

STANDALONE_MARKERS = [
    "statement of standalone",
    "statement of unaudited standalone",
    "statement of audited standalone",
    "standalone financial statements",
    "standalone balance sheet",
    "standalone statement of",
    "review of the standalone",
    "audit of the standalone",
]

CONSOLIDATED_MARKERS = [
    "statement of consolidated",
    "statement of unaudited consolidated",
    "statement of audited consolidated",
    "consolidated financial statements",
    "consolidated balance sheet",
    "consolidated statement of",
    "review of the consolidated",
    "audit of the consolidated",
]


# ---------------------------------------------------------------------------
# SHA256 utility
# ---------------------------------------------------------------------------

def compute_pdf_checksum(pdf_path: str | Path) -> str:
    """
    Compute SHA256 hex digest of the PDF file.
    Reads in 8MB chunks — safe for large annual report PDFs.
    """
    sha = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def section_checksum(file_sha256: str, financial_type: str) -> str:
    """
    Per-section checksum: allows two documents rows from one PDF
    while still catching duplicate uploads of the same file.
    """
    return f"{file_sha256}_{financial_type}"


# ---------------------------------------------------------------------------
# Pure section detection — no DB
# ---------------------------------------------------------------------------

def detect_sections(blocks: list[PageBlock]) -> list[DocSection]:
    """
    Scan TABLE blocks for standalone and consolidated section markers.
    By filtering out TEXT blocks, we completely ignore prose-heavy press releases 
    that might casually mention "consolidated results" in a paragraph.
    """
    total_pages = max(b.page_number for b in blocks) if blocks else 0

    first_standalone_page = None
    first_consolidated_page = None

    # THE FIX: Only scan pages that actually contain financial tables
    table_blocks = [b for b in blocks if b.block_type == BlockType.TABLE]

    for block in sorted(table_blocks, key=lambda b: b.page_number):
        content_lower = block.content.lower()

        has_consolidated_marker = any(m in content_lower for m in CONSOLIDATED_MARKERS)
        has_standalone_marker = any(m in content_lower for m in STANDALONE_MARKERS)

        if has_standalone_marker and not has_consolidated_marker:
            if first_standalone_page is None:
                first_standalone_page = block.page_number
                logger.info("Standalone section marker found on page %d", block.page_number)
                
        if has_consolidated_marker and not has_standalone_marker:
            if first_consolidated_page is None:
                first_consolidated_page = block.page_number
                logger.info("Consolidated section marker found on page %d", block.page_number)

    # ... (Keep the rest of the detect_sections logic exactly the same) ...
    
    if first_standalone_page is None and first_consolidated_page is None:
        # NO MARKER OF EITHER KIND. The module docstring claims this path
        # "never silently defaults to wrong financial_type" and sets
        # needs_review=True -- it does neither; it defaults to CONSOLIDATED
        # over the whole document with no signal at all. Measured 2026-08-08.
        #
        # Correct for a transcript or press release, which have no statutory
        # financial_type: CONSOLIDATED is true at document level even though no
        # row-level type exists. NOT correct for a filing whose markers failed
        # to parse, which is indistinguishable from here.
        #
        # Logged rather than blocked: the default is right more often than it
        # is wrong, and refusing would break the transcript path. But it must
        # be audible -- a wrongly-typed document is invisible downstream, where
        # the retrieval filter silently excludes it.
        logger.warning(
            "No standalone OR consolidated marker found in any TABLE block "
            "(%d pages) — defaulting to CONSOLIDATED for the whole document. "
            "Expected for transcripts/press releases; for a filing this means "
            "marker detection FAILED and financial_type is unverified.",
            total_pages,
        )
        return [DocSection(financial_type=FinancialType.CONSOLIDATED, page_start=1, page_end=total_pages)]

    if first_standalone_page is None:
        return [DocSection(financial_type=FinancialType.CONSOLIDATED, page_start=1, page_end=total_pages)]

    if first_consolidated_page is None:
        return [DocSection(financial_type=FinancialType.STANDALONE, page_start=1, page_end=total_pages)]

    if first_standalone_page == first_consolidated_page:
        return [DocSection(financial_type=FinancialType.CONSOLIDATED, page_start=1, page_end=total_pages)]

    boundaries = sorted(
        [(first_standalone_page, FinancialType.STANDALONE), (first_consolidated_page, FinancialType.CONSOLIDATED)],
        key=lambda x: x[0],
    )
    (first_page, first_type), (second_page, second_type) = boundaries

    logger.info("Sections detected: %s pages 1-%d | %s pages %d-%d", first_type, second_page - 1, second_type, second_page, total_pages)

    return [
        DocSection(financial_type=first_type, page_start=1, page_end=second_page - 1),
        DocSection(financial_type=second_type, page_start=second_page, page_end=total_pages),
    ]


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

_SQL_SET_TENANT = "SET app.tenant_id = %s"

_SQL_INSERT_DOCUMENT = """
INSERT INTO documents (
    doc_id, tenant_id, company, ticker,
    fiscal_year, quarter, doc_type, financial_type,
    filing_date, version, is_latest,
    sha256_checksum, ingestion_state
)
VALUES (
    %(doc_id)s, %(tenant_id)s, %(company)s, %(ticker)s,
    %(fiscal_year)s, %(quarter)s, %(doc_type)s, %(financial_type)s,
    %(filing_date)s, %(version)s, TRUE,
    %(sha256_checksum)s, %(ingestion_state)s
)
ON CONFLICT (sha256_checksum) DO UPDATE
    SET ingestion_state = EXCLUDED.ingestion_state,
        company         = EXCLUDED.company,
        ticker          = EXCLUDED.ticker,
        fiscal_year     = EXCLUDED.fiscal_year,
        quarter         = EXCLUDED.quarter,
        doc_type        = EXCLUDED.doc_type,
        filing_date     = EXCLUDED.filing_date,
        version         = EXCLUDED.version
RETURNING doc_id, (xmax <> 0) AS was_existing
"""

# WHY THE CONFLICT CLAUSE UPDATES METADATA (do not revert to ingestion_state only)
# --------------------------------------------------------------------------------
# Before 2026-07-29 this clause set ingestion_state alone. That left two
# independent sources of truth for the same fields: `documents` kept whatever
# the FIRST ingest inserted, while chunker.chunk_blocks() wrote whatever the
# CURRENT call's arguments said into every Qdrant payload. Re-registering the
# same PDF with different metadata silently diverged the two stores.
#
# Confirmed live: a test of the upload endpoint re-registered the real Paytm
# PDF with junk form values. `documents` correctly read FY26 (protected by
# this clause) while all 115 Qdrant chunks were written as FY26->FY99, which
# meant the fiscal_year retrieval filter could never match and every Paytm
# semantic query survived only via CRAG dropping the filter entirely.
#
# Last-write-wins was chosen over first-write-wins deliberately: under
# first-write-wins a document ingested once with wrong metadata could never
# be corrected by re-ingesting, which is a worse operational position than
# the bug being fixed. Re-ingesting with correct values is now the repair.
#
# tenant_id is NOT updated — a conflicting checksum from another tenant must
# not reassign row ownership. financial_type is NOT updated — it is part of
# the conflict key via section_checksum() and cannot mismatch. is_latest is
# NOT updated — the Truth Resolution Engine owns it, not ingestion.

# ---------------------------------------------------------------------------
# DB registration
# ---------------------------------------------------------------------------

def register_sections(
    sections: list[DocSection],
    pdf_path: str | Path,
    tenant_id: str,
    company: str,
    ticker: str,
    fiscal_year: str,
    quarter: Optional[str],
    doc_type: str,
    filing_date: str,
    conn,
    version: str = "v1",
) -> list[DocSection]:
    file_sha256 = compute_pdf_checksum(pdf_path)
    logger.info("PDF SHA256: %s", file_sha256)

    with conn.cursor() as cur:
        cur.execute(_SQL_SET_TENANT, (str(tenant_id),))

        for section in sections:
            doc_id = str(uuid.uuid4())
            checksum = section_checksum(file_sha256, section.financial_type)

            params = {
                "doc_id":           doc_id,
                "tenant_id":        str(tenant_id),
                "company":          company,
                "ticker":           ticker,
                "fiscal_year":      fiscal_year,
                "quarter":          quarter,
                "doc_type":         doc_type,
                "financial_type":   section.financial_type,
                "filing_date":      filing_date,
                "version":          version,
                "sha256_checksum":  checksum,
                "ingestion_state":  DocState.PROCESSING,
            }

            cur.execute(_SQL_INSERT_DOCUMENT, params)
            row = cur.fetchone()

            section.doc_id = uuid.UUID(str(row[0]))
            was_existing   = bool(row[1])

            if was_existing:
                # Re-registration of an already-known checksum. Metadata is
                # now overwritten by this call's arguments on both sides
                # (documents here, Qdrant payloads via chunk_blocks). Logged
                # loudly because a silent version of this is exactly what
                # corrupted PAYTM's vector metadata undetected.
                logger.warning(
                    "Re-registering existing document (checksum match) — metadata "
                    "overwritten: %s | %s %s %s | filing_date=%s | doc_id=%s",
                    section.financial_type, company, fiscal_year,
                    quarter or "annual", filing_date, section.doc_id,
                )
            else:
                logger.info(
                    "Registered document: %s | %s | pages %d–%d | doc_id=%s",
                    section.financial_type,
                    fiscal_year,
                    section.page_start,
                    section.page_end,
                    section.doc_id,
                )

    conn.commit()
    return sections


def classify_and_register(
    blocks: list[PageBlock],
    pdf_path: str | Path,
    tenant_id: str,
    company: str,
    ticker: str,
    fiscal_year: str,
    quarter: Optional[str],
    doc_type: str,
    filing_date: str,
    conn,
) -> list[DocSection]:
    sections = detect_sections(blocks)
    return register_sections(
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
    )


if __name__ == "__main__":
    import os
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from .db_loader import get_connection
    from .pdf_parser import parse_pdf

    pdf_path = Path(
        sys.argv[1] if len(sys.argv) > 1
        else os.path.expanduser(
            "~/ledgermind/docs/raw/"
            "ETERNAL_Q4FY26_SHAREHOLDER_LETTER_AND_RESULTS.pdf"
        )
    )

    ALPHA_TENANT = "a0000000-0000-0000-0000-000000000001"

    print(f"\nParsing: {pdf_path.name}")
    blocks = parse_pdf(pdf_path)
    print(f"Blocks parsed: {len(blocks)}")

    print("\n--- Section detection ---")
    sections = detect_sections(blocks)
    for s in sections:
        print(
            f"  {s.financial_type:15s} | pages {s.page_start:2d}–{s.page_end:2d}"
        )

    assert len(sections) == 2, \
        f"Expected 2 sections, got {len(sections)} — check standalone marker"
    
    print("\nAll checks passed.")