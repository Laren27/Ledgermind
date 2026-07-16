"""
Chunker — converts classified PageBlocks into Chunk objects with full metadata.

Responsibilities:
  1. Apply block-type-appropriate splitting strategy
  2. Inject complete ChunkMetadata on every chunk
  3. Return List[Chunk] ready for embedder.py

Splitting strategies:
  FINANCIAL_STATEMENT / TABLE  → whole block = one chunk (never split tables)
  RISK_DISCLOSURE              → recursive split, target 500 tokens (~2000 chars)
  MANAGEMENT_DISCUSSION        → recursive split, target 350 tokens (~1400 chars)
  TEXT                         → recursive split, target 400 tokens (~1600 chars)

Design decisions:
  - No LangChain dependency — recursive splitter implemented in ~30 lines
  - Token counting is character-based approximation (1 token ≈ 4 chars)
  - Chunk IDs are DETERMINISTIC: hash(doc_id + page + position + text[:100])
    Same PDF re-ingested → same chunk_ids → Qdrant upsert overwrites cleanly
  - Parent-child chunking deferred to Phase 7

Called by: pipeline.py
"""

import hashlib
import logging
import uuid
from typing import Optional

from .models import (
    BlockType,
    Chunk,
    ChunkMetadata,
    DocSection,
    FinancialType,
    PageBlock,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Chunk size configuration
# ---------------------------------------------------------------------------

CHARS_PER_TOKEN = 4

TARGET_TOKENS = {
    BlockType.TEXT:                  200,
    BlockType.RISK_DISCLOSURE:       250,
    BlockType.MANAGEMENT_DISCUSSION: 200,
    BlockType.FINANCIAL_STATEMENT:   None,
    BlockType.TABLE:                 None,
    BlockType.UNKNOWN:               200,
}

OVERLAP_TOKENS = 150
OVERLAP_CHARS  = OVERLAP_TOKENS * CHARS_PER_TOKEN

SPLIT_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]

MIN_CHUNK_CHARS = 50


# ---------------------------------------------------------------------------
# Section label mapping
# ---------------------------------------------------------------------------

SECTION_LABELS = {
    BlockType.FINANCIAL_STATEMENT:   "Financial Statements",
    BlockType.TABLE:                 "Tables",
    BlockType.RISK_DISCLOSURE:       "Risk Disclosures",
    BlockType.MANAGEMENT_DISCUSSION: "Management Discussion",
    BlockType.TEXT:                  "General",
    BlockType.UNKNOWN:               "General",
}


# ---------------------------------------------------------------------------
# Deterministic chunk ID
# ---------------------------------------------------------------------------

def _make_chunk_id(doc_id: str, page_number: int, position: int, text: str) -> str:
    """
    Deterministic chunk ID — same content always produces the same UUID.
    Enables true idempotent upserts in Qdrant: re-ingesting the same PDF
    produces the same chunk_ids and overwrites existing points cleanly.

    Components:
      doc_id      — scopes to this specific document version
      page_number — scopes to the page
      position    — index of this chunk within the page's splits
      text[:100]  — content fingerprint (guards against position collisions)
    """
    fingerprint = f"{doc_id}:{page_number}:{position}:{text[:100]}"
    return str(uuid.UUID(hashlib.md5(fingerprint.encode()).hexdigest()))


# ---------------------------------------------------------------------------
# Recursive character splitter
# ---------------------------------------------------------------------------

def _recursive_split(
    text: str,
    max_chars: int,
    overlap_chars: int,
    separators: list[str],
) -> list[str]:
    """
    Recursively split text into chunks of at most max_chars characters.
    Tries each separator in order; falls back to the next if chunks are too large.
    Adds overlap_chars of overlap between adjacent chunks.
    """
    if len(text) <= max_chars:
        return [text] if text.strip() else []

    for sep in separators:
        if sep == "":
            chunks = []
            start = 0
            while start < len(text):
                end = start + max_chars
                chunks.append(text[start:end])
                start = end - overlap_chars
            return [c for c in chunks if c.strip()]

        if sep not in text:
            continue

        parts = text.split(sep)
        chunks: list[str] = []
        current = ""

        for part in parts:
            candidate = current + (sep if current else "") + part
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current.strip():
                    chunks.append(current)
                overlap_text = current[-overlap_chars:] if overlap_chars else ""
                current = overlap_text + (sep if overlap_text else "") + part

        if current.strip():
            chunks.append(current)

        if all(len(c) <= max_chars for c in chunks):
            return [c for c in chunks if c.strip()]

        result: list[str] = []
        remaining_separators = separators[separators.index(sep) + 1:]
        for chunk in chunks:
            if len(chunk) > max_chars:
                result.extend(_recursive_split(chunk, max_chars, overlap_chars, remaining_separators))
            else:
                result.append(chunk)
        return [c for c in result if c.strip()]

    return [text] if text.strip() else []


# ---------------------------------------------------------------------------
# Metadata builder
# ---------------------------------------------------------------------------

def _build_metadata(
    block: PageBlock,
    doc_id: str,
    tenant_id: str,
    company: str,
    ticker: str,
    fiscal_year: str,
    quarter: Optional[str],
    document_type: str,
    filing_date: str,
    version: str,
    chunk_id: str,
) -> ChunkMetadata:
    financial_type = getattr(block, "financial_type", FinancialType.UNKNOWN)

    return ChunkMetadata(
        doc_id=doc_id,
        tenant_id=tenant_id,
        chunk_id=chunk_id,
        company=company,
        ticker=ticker,
        fiscal_year=fiscal_year,
        quarter=quarter,
        financial_type=financial_type,
        document_type=document_type,
        reporting_standard="Ind AS",
        filing_date=filing_date,
        valid_from=filing_date,
        valid_to=None,
        is_latest=True,
        version=version,
        page_number=block.page_number,
        section=SECTION_LABELS.get(block.block_type, "General"),
        subsection="",
        chunk_type=block.block_type,
        table_header=None,
        needs_review=getattr(block, "needs_review", False),
    )


# ---------------------------------------------------------------------------
# Block-level chunking
# ---------------------------------------------------------------------------

def _chunk_unsplittable_block(
    block: PageBlock,
    doc_id: str,
    tenant_id: str,
    company: str,
    ticker: str,
    fiscal_year: str,
    quarter: Optional[str],
    document_type: str,
    filing_date: str,
    version: str,
) -> list[Chunk]:
    """TABLE and FINANCIAL_STATEMENT: one block = one chunk. Never split."""
    text = block.content.strip()
    if len(text) < MIN_CHUNK_CHARS:
        return []

    chunk_id = _make_chunk_id(doc_id, block.page_number, 0, text)
    metadata = _build_metadata(
        block=block, doc_id=doc_id, tenant_id=tenant_id,
        company=company, ticker=ticker, fiscal_year=fiscal_year,
        quarter=quarter, document_type=document_type,
        filing_date=filing_date, version=version, chunk_id=chunk_id,
    )
    return [Chunk(chunk_id=chunk_id, text=text, metadata=metadata)]


def _chunk_text_block(
    block: PageBlock,
    doc_id: str,
    tenant_id: str,
    company: str,
    ticker: str,
    fiscal_year: str,
    quarter: Optional[str],
    document_type: str,
    filing_date: str,
    version: str,
) -> list[Chunk]:
    """TEXT, RISK_DISCLOSURE, MANAGEMENT_DISCUSSION: recursive split."""
    target_tokens = TARGET_TOKENS.get(block.block_type, 200)
    max_chars = target_tokens * CHARS_PER_TOKEN

    text_pieces = _recursive_split(
        text=block.content.strip(),
        max_chars=max_chars,
        overlap_chars=OVERLAP_CHARS,
        separators=SPLIT_SEPARATORS,
    )

    chunks: list[Chunk] = []
    for position, piece in enumerate(text_pieces):
        if len(piece.strip()) < MIN_CHUNK_CHARS:
            continue

        chunk_id = _make_chunk_id(doc_id, block.page_number, position, piece)
        metadata = _build_metadata(
            block=block, doc_id=doc_id, tenant_id=tenant_id,
            company=company, ticker=ticker, fiscal_year=fiscal_year,
            quarter=quarter, document_type=document_type,
            filing_date=filing_date, version=version, chunk_id=chunk_id,
        )
        chunks.append(Chunk(chunk_id=chunk_id, text=piece, metadata=metadata))

    return chunks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def chunk_blocks(
    blocks: list[PageBlock],
    sections: list[DocSection],
    tenant_id: str,
    company: str,
    ticker: str,
    fiscal_year: str,
    quarter: Optional[str],
    document_type: str,
    filing_date: str,
    version: str = "v1",
) -> list[Chunk]:
    """
    Convert classified PageBlocks into Chunk objects with full metadata.

    Args:
        blocks:        Classified output of section_classifier.classify_blocks()
        sections:      Registered DocSections with doc_id populated
        tenant_id:     UUID string for RLS and Qdrant filtering
        company:       Canonical company name e.g. "ETERNAL"
        ticker:        e.g. "ETERNAL"
        fiscal_year:   e.g. "FY26"
        quarter:       e.g. "Q4" or None for annual reports
        document_type: "quarterly_result" | "annual_report" | "drhp" | "transcript"
        filing_date:   ISO date string "YYYY-MM-DD"
        version:       Filing version, default "v1"

    Returns:
        List[Chunk] — ready to pass to embedder.py
    """
    page_to_doc_id: dict[int, str] = {}
    for section in sections:
        if section.doc_id is None:
            logger.warning(
                "DocSection %s has no doc_id — was register_sections() called?",
                section.financial_type,
            )
            continue
        for page in range(section.page_start, section.page_end + 1):
            page_to_doc_id[page] = str(section.doc_id)

    all_chunks: list[Chunk] = []
    skipped_blocks = 0

    for block in blocks:
        doc_id = page_to_doc_id.get(block.page_number)
        if not doc_id:
            skipped_blocks += 1
            continue

        kwargs = dict(
            block=block, doc_id=doc_id, tenant_id=tenant_id,
            company=company, ticker=ticker, fiscal_year=fiscal_year,
            quarter=quarter, document_type=document_type,
            filing_date=filing_date, version=version,
        )

        if block.block_type in (BlockType.FINANCIAL_STATEMENT, BlockType.TABLE):
            chunks = _chunk_unsplittable_block(**kwargs)
        else:
            chunks = _chunk_text_block(**kwargs)

        all_chunks.extend(chunks)

    from collections import Counter
    type_counts = Counter(c.metadata.chunk_type for c in all_chunks)
    logger.info(
        "Chunking complete: %d chunks from %d blocks (%d skipped) | %s",
        len(all_chunks), len(blocks), skipped_blocks,
        " | ".join(f"{k}={v}" for k, v in sorted(type_counts.items())),
    )

    return all_chunks


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import os
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from pathlib import Path
    from collections import Counter

    from .db_loader import get_connection
    from .document_classifier import classify_and_register, detect_sections
    from .pdf_parser import parse_pdf
    from .section_classifier import classify_blocks, get_blocks_by_type

    parser = argparse.ArgumentParser()
    parser.add_argument("pdf_path", nargs="?", default=os.path.expanduser(
        "~/ledgermind/docs/raw/ETERNAL_Q4FY26_SHAREHOLDER_LETTER_AND_RESULTS.pdf"))
    parser.add_argument("--company", default="ETERNAL")
    parser.add_argument("--ticker", default="ETERNAL")
    parser.add_argument("--fiscal-year", default="FY26")
    parser.add_argument("--quarter", default="Q4")
    parser.add_argument("--doc-type", default="quarterly_result")
    parser.add_argument("--filing-date", default="2026-04-28")
    parser.add_argument("--min-chunks", type=int, default=100)
    args = parser.parse_args()

    pdf_path = Path(args.pdf_path)
    quarter = args.quarter if args.quarter.lower() != "none" else None
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

    print("\n--- Chunking ---")
    chunks = chunk_blocks(
        blocks=blocks, sections=sections, tenant_id=ALPHA_TENANT,
        company=args.company, ticker=args.ticker, fiscal_year=args.fiscal_year,
        quarter=quarter, document_type=args.doc_type, filing_date=args.filing_date,
    )

    type_counts = Counter(c.metadata.chunk_type for c in chunks)
    ft_counts   = Counter(c.metadata.financial_type for c in chunks)

    print(f"\nTotal chunks      : {len(chunks)}")
    print(f"By block type     : {dict(type_counts)}")
    print(f"By financial_type : {dict(ft_counts)}")

    chunks2 = chunk_blocks(
        blocks=blocks, sections=sections, tenant_id=ALPHA_TENANT,
        company=args.company, ticker=args.ticker, fiscal_year=args.fiscal_year,
        quarter=quarter, document_type=args.doc_type, filing_date=args.filing_date,
    )
    ids1 = {c.chunk_id for c in chunks}
    ids2 = {c.chunk_id for c in chunks2}
    assert ids1 == ids2, "Chunk IDs not deterministic — upserts will create duplicates"
    print("\nDeterminism check: PASS — same chunk_ids on second run")

    assert len(chunks) >= args.min_chunks, \
        f"Expected >= {args.min_chunks} chunks, got {len(chunks)}"
    for c in chunks:
        assert c.chunk_id
        assert c.text.strip()
        assert c.metadata.doc_id
        assert c.metadata.tenant_id

    print("\nAll assertions passed.")