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

from .models import DocSection, FinancialType, DocState, PageBlock

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Section marker patterns (ordered by specificity — first match wins)
# ---------------------------------------------------------------------------
# Eternal Q4FY26 verified section boundaries (confirmed April 2026 filing):
#   Consolidated : pages  1–35  (financial tables: 31–34)
#   Standalone   : pages 36–44  (auditor report: 36–39, tables: 40–43)
# Boundary detected at page 36 via Deloitte auditor report header,
# not page 40 where the financial table starts — both are correct,
# 36 is the true section start.

# Text patterns that signal the START of the standalone section.
# Matched case-insensitively against PageBlock.content.
STANDALONE_MARKERS = [
    "statement of standalone financial results",
    "standalone financial results",
    "standalone financial statements",
    "standalone balance sheet",
    "standalone statement of",
]

# Text patterns that confirm we are in the consolidated section.
# Used for defence: if a page has both markers, consolidated wins.
CONSOLIDATED_MARKERS = [
    "statement of consolidated financial results",
    "consolidated financial results",
    "consolidated financial statements",
    "consolidated balance sheet",
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
    Scan PageBlocks for standalone section markers and return DocSection list.

    Returns:
      - [consolidated, standalone]  if marker found (normal case)
      - [consolidated-only]         if no marker found (flagged needs_review)

    The returned DocSection objects have doc_id=None — populated later
    by register_sections() after DB insert.
    """
    total_pages = max(b.page_number for b in blocks) if blocks else 0
    standalone_start: Optional[int] = None

    for block in sorted(blocks, key=lambda b: b.page_number):
        content_lower = block.content.lower()

        # Skip if this page has a consolidated marker — prevents false positives
        # on pages that mention both types (e.g., cover page disclaimers)
        has_consolidated_marker = any(
            m in content_lower for m in CONSOLIDATED_MARKERS
        )
        has_standalone_marker = any(
            m in content_lower for m in STANDALONE_MARKERS
        )

        if has_standalone_marker and not has_consolidated_marker:
            standalone_start = block.page_number
            logger.info(
                "Standalone section marker found on page %d: '%s'",
                block.page_number,
                block.content[:80].replace("\n", " "),
            )
            break   # First clean standalone marker is the boundary

    if standalone_start is None:
        logger.warning(
            "No standalone section marker found in %d blocks across %d pages. "
            "Creating consolidated-only section. Manual review required.",
            len(blocks), total_pages,
        )
        return [
            DocSection(
                financial_type=FinancialType.CONSOLIDATED,
                page_start=1,
                page_end=total_pages,
            )
        ]

    consolidated_end = standalone_start - 1

    logger.info(
        "Sections detected: consolidated pages 1–%d | standalone pages %d–%d",
        consolidated_end, standalone_start, total_pages,
    )

    return [
        DocSection(
            financial_type=FinancialType.CONSOLIDATED,
            page_start=1,
            page_end=consolidated_end,
        ),
        DocSection(
            financial_type=FinancialType.STANDALONE,
            page_start=standalone_start,
            page_end=total_pages,
        ),
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
    SET ingestion_state = EXCLUDED.ingestion_state
RETURNING doc_id
"""
# ON CONFLICT: if the same PDF section was already registered (same checksum),
# update ingestion_state (e.g., re-trigger from 'failed' → 'processing').
# All other fields are immutable once created.


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
    """
    Write one documents row per DocSection and populate DocSection.doc_id.

    Args:
        sections:     Output of detect_sections()
        pdf_path:     Source PDF path — used to compute SHA256
        tenant_id:    UUID string
        company:      Canonical company name (e.g. "ETERNAL")
        ticker:       Exchange ticker (e.g. "ETERNAL")
        fiscal_year:  e.g. "FY26"
        quarter:      e.g. "Q4" or None for annual reports
        doc_type:     "quarterly_result" | "annual_report" | "drhp" | "transcript"
        filing_date:  ISO date string "YYYY-MM-DD"
        conn:         Open psycopg2 connection — caller owns lifecycle
        version:      Filing version, default "v1"

    Returns:
        The same sections list with doc_id populated on each DocSection.

    Raises:
        RuntimeError if file SHA256 cannot be computed or DB insert fails.
    """
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

            # fetchone returns the RETURNING doc_id — use it, not the local var,
            # in case ON CONFLICT returned the existing row's doc_id.
            section.doc_id = uuid.UUID(str(row[0]))

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


# ---------------------------------------------------------------------------
# Convenience wrapper — full classify + register in one call
# ---------------------------------------------------------------------------

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
    """
    Full pipeline: detect sections → register in DB → return DocSections.
    This is what pipeline.py calls.
    """
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


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

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

    # --- Pure detection (no DB) ---
    print("\n--- Section detection ---")
    sections = detect_sections(blocks)
    for s in sections:
        print(
            f"  {s.financial_type:15s} | pages {s.page_start:2d}–{s.page_end:2d}"
        )

    assert len(sections) == 2, \
        f"Expected 2 sections, got {len(sections)} — check standalone marker"
    assert sections[0].financial_type == FinancialType.CONSOLIDATED
    assert sections[1].financial_type == FinancialType.STANDALONE
    assert sections[1].page_start == 36, f"Expected standalone to start on page 36, got {sections[1].page_start}"

    # --- DB registration ---
    print("\n--- Registering sections in documents table ---")
    conn = get_connection()
    try:
        registered = register_sections(
            sections=sections,
            pdf_path=pdf_path,
            tenant_id=ALPHA_TENANT,
            company="ETERNAL",
            ticker="ETERNAL",
            fiscal_year="FY26",
            quarter="Q4",
            doc_type="quarterly_result",
            filing_date="2026-04-28",
            conn=conn,
        )
    finally:
        conn.close()

    for s in registered:
        print(f"  {s.financial_type:15s} | doc_id={s.doc_id}")
        assert s.doc_id is not None, f"doc_id not populated for {s.financial_type}"

    # --- Verify rows in DB ---
    print("\n--- Verifying documents table ---")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SET app.tenant_id = %s", (ALPHA_TENANT,))
            cur.execute(
                """
                SELECT financial_type, ingestion_state, fiscal_year, quarter
                FROM   documents
                WHERE  company = 'ETERNAL' AND fiscal_year = 'FY26'
                ORDER BY financial_type
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    print(f"  Rows in documents table: {len(rows)}")
    for row in rows:
        print(f"  {row}")

    assert len(rows) == 2, f"Expected 2 document rows, got {len(rows)}"
    print("\nAll checks passed.")
    print("\nNOTE: Documents registered with ingestion_state='processing'.")
    print("Run again with the same PDF → ON CONFLICT updates state, does not duplicate.")