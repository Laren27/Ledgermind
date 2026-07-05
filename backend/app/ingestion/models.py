"""
Shared dataclasses for the LedgerMind ingestion pipeline.

These types flow through every stage:
  pdf_parser → [PageBlock]
  document_classifier → [DocSection]
  section_classifier → PageBlock.block_type filled in
  chunker → [Chunk]
  embedder → [EmbeddedChunk]

Rule: never pass raw dicts between pipeline stages. Use these types.
"""

from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID
import datetime


# ---------------------------------------------------------------------------
# Block types (assigned by section_classifier)
# ---------------------------------------------------------------------------

class BlockType:
    TEXT                = "TEXT"
    TABLE               = "TABLE"
    RISK_DISCLOSURE     = "RISK_DISCLOSURE"
    MANAGEMENT_DISCUSSION = "MANAGEMENT_DISCUSSION"
    FOOTNOTE            = "FOOTNOTE"
    FINANCIAL_STATEMENT = "FINANCIAL_STATEMENT"
    UNKNOWN             = "UNKNOWN"


# ---------------------------------------------------------------------------
# Financial type constants
# ---------------------------------------------------------------------------

class FinancialType:
    CONSOLIDATED = "consolidated"
    STANDALONE   = "standalone"
    UNKNOWN      = "unknown"


# ---------------------------------------------------------------------------
# Document state (mirrors ingestion_state column in documents table)
# ---------------------------------------------------------------------------

class DocState:
    UPLOADED   = "uploaded"
    PROCESSING = "processing"
    INDEXED    = "indexed"
    FAILED     = "failed"


# ---------------------------------------------------------------------------
# Raw extraction output from pdf_parser
# ---------------------------------------------------------------------------

@dataclass
class RawTable:
    """
    A table extracted by pdfplumber from a single page.
    rows: list of rows; each row is a list of cell strings.
    Example: [["Revenue", "12,114", "9,651"], ["EBITDA", "532", "410"]]
    """
    page_number: int
    rows: list[list[str]]           # raw cell values (strings, may be None)
    bbox: Optional[tuple] = None    # (x0, y0, x1, y1) — for debugging


@dataclass
class PageBlock:
    """
    One logical content block extracted from a single PDF page.
    A page may produce multiple PageBlocks (e.g., a paragraph + a table).

    block_type is UNKNOWN at parse time; section_classifier fills it in.
    """
    page_number: int
    content: str                            # raw text for TEXT blocks
    block_type: str = BlockType.UNKNOWN
    table: Optional[RawTable] = None        # populated for TABLE blocks
    is_continuation: bool = False           # True if this table spans from prev page


# ---------------------------------------------------------------------------
# Section boundary detected by document_classifier
# ---------------------------------------------------------------------------

@dataclass
class DocSection:
    """
    Represents one logical section within a PDF (consolidated or standalone).
    page_start and page_end define the page range (inclusive, 1-indexed).
    """
    financial_type: str          # FinancialType.CONSOLIDATED or STANDALONE
    page_start: int
    page_end: int
    doc_id: Optional[UUID] = None  # assigned after DB insert


# ---------------------------------------------------------------------------
# Metadata payload attached to every chunk
# (mirrors the Qdrant payload spec from the blueprint)
# ---------------------------------------------------------------------------

@dataclass
class ChunkMetadata:
    # Document identity
    doc_id: str
    tenant_id: str
    chunk_id: str

    # Company identity
    company: str
    ticker: str
    fiscal_year: str
    quarter: Optional[str]
    financial_type: str
    document_type: str                  # quarterly_result / annual_report / drhp
    reporting_standard: str = "Ind AS"

    # Temporal
    filing_date: str = ""              # ISO date string YYYY-MM-DD
    valid_from: str = ""
    valid_to: Optional[str] = None
    is_latest: bool = True
    version: str = "v1"

    # Location in document
    page_number: int = 0
    section: str = ""
    subsection: str = ""
    chunk_type: str = BlockType.UNKNOWN

    # Table header (populated only for TABLE chunks)
    table_header: Optional[list[str]] = None

    # Flags
    needs_review: bool = False          # True if header detection failed


# ---------------------------------------------------------------------------
# A fully processed chunk ready for embedding
# ---------------------------------------------------------------------------

@dataclass
class Chunk:
    chunk_id: str
    text: str                          # the content that gets embedded
    metadata: ChunkMetadata


# ---------------------------------------------------------------------------
# A chunk with vectors attached, ready for Qdrant upsert
# ---------------------------------------------------------------------------

@dataclass
class EmbeddedChunk:
    chunk: Chunk
    dense_vector: list[float]
    sparse_indices: list[int]
    sparse_values: list[float]


# ---------------------------------------------------------------------------
# Structured financial record ready for PostgreSQL insert
# ---------------------------------------------------------------------------

@dataclass
class FinancialRecord:
    tenant_id: str
    doc_id: str
    company: str
    ticker: str
    fiscal_year: str
    quarter: Optional[str]
    financial_type: str
    metric: str                        # normalized metric name (see entity_resolver)
    value: float
    unit: str = "crore_inr"
    filing_date: str = ""              # ISO date string
    is_latest: bool = True


def normalize_quarter(raw: str | None) -> str | None:
    """
    Canonical form: None means 'no quarter' (annual data).
    Collapses argparse '--quarter none' / '--quarter ""' / omitted flag
    to the same value, so every downstream consumer (chunker, extractor,
    pipeline, db_loader) compares like-for-like.
    """
    if raw is None:
        return None
    if raw.strip().lower() in ("", "none", "null"):
        return None
    return raw