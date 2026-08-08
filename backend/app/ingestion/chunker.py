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
import re
import uuid
from typing import Optional
from app.ingestion.models import normalize_quarter
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
# Speaker-turn splitting (earnings_transcript only)
# ---------------------------------------------------------------------------
# A transcript's natural unit is the speaker turn, not an arbitrary character
# window. Measured 2026-08-08 on ETERNAL Q4FY26: the generic TEXT path produced
# 187 chunks from 17 pages, and chunk 92 opened mid-sentence on an ANALYST'S
# premise ("your advertising promotion cost ... was flat sequentially") with no
# attribution, while carrying a different speaker's name later in the same
# chunk. That is the false-contradiction generator for Path 3: an analyst's
# assertion, unowned, reads as a company claim -- and in this document several
# such premises are DENIED by management in the next turn (inventory days p10,
# A&P flat p9, orders per customer p8).
#
# The pattern is deliberately bounded: line-initial, at most 5 capitalised
# words before the colon. Validated across all 532 lines of the transcript --
# 127 matches, 17 distinct names (3 management + Moderator + 13 analysts),
# ZERO spurious hits inside prose. Moderator is treated as a speaker: its
# turns carry the analyst's name and firm, which is the attribution for the
# question that follows.
SPEAKER_LINE_RE = re.compile(
    r"^([A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){0,4}):\s"
)

TRANSCRIPT_DOC_TYPE = "earnings_transcript"

# Overlap between TURNS is zero -- a turn is complete on its own, and overlap
# across turns is exactly what orphaned the premise above. Overlap WITHIN a
# long turn keeps the normal value: the 50->150 raise was made to stop a
# mid-sentence split orphaning Paytm's PPBL impairment fact, and that reason
# still applies inside a single speaker's answer.


def _split_speaker_turns(
    text: str, incoming_speaker: str = ""
) -> tuple[list[tuple[str, str]], str]:
    """Segment transcript text into (speaker, turn_text) pairs.

    Returns the pairs AND the speaker still talking at the end of the text, so
    the caller can thread it into the next block.

    WHY THE THREAD EXISTS. parse_pdf emits ONE BLOCK PER PAGE, so a turn
    spanning a page break has its speaker line on the previous page and its
    remainder starts bare. Measured 2026-08-08 before this parameter existed:
    10 of 129 chunks classified "unknown" that way, and they were not cover
    text -- they included Akshant's 3,000-store guidance (p3), Albinder on
    unhealthy growth (p10) and Akshant on customer retention (p12). Real
    management speech, attribution lost at the page boundary, which is the
    exact failure this whole mechanism exists to prevent.

    incoming_speaker="" is correct for the first block: text before any speaker
    line (cover page, disclaimers) genuinely has no speaker and must classify
    "unknown" rather than inheriting one.
    """
    turns: list[tuple[str, str]] = []
    speaker = incoming_speaker
    buf: list[str] = []

    for line in text.split("\n"):
        m = SPEAKER_LINE_RE.match(line.strip())
        if m:
            if buf and "\n".join(buf).strip():
                turns.append((speaker, "\n".join(buf).strip()))
            speaker = m.group(1)
            buf = [line]
        else:
            buf.append(line)

    if buf and "\n".join(buf).strip():
        turns.append((speaker, "\n".join(buf).strip()))

    return turns, speaker


# Carries the speaker still talking at the end of one block into the next.
# A module-level cell rather than a return value because _chunk_text_block is
# called through a shared **kwargs dict alongside _chunk_unsplittable_block,
# and changing its return type would change both call sites for one document
# type. Written and read only within a single chunk_blocks() call, which is
# synchronous and single-threaded; chunk_blocks resets it before the loop.
_OUTGOING: dict[str, str] = {"speaker": ""}

ROSTER_ANCHOR = "Management representatives:"

# "1. Albinder Singh Dhindsa - Chief Executive Officer, Eternal Limited"
# EN DASH (U+2013), which is what the filing prints -- verified against the
# parsed page-1 text, not assumed. A hyphen is accepted too so a differently
# typeset transcript is not silently unparseable.
_ROSTER_ENTRY_RE = re.compile(r"^\d+[.)]\s*(.+?)\s*[\u2013\u2014-]\s*.+$")

MODERATOR_SPEAKER = "Moderator"

# ZERO, and the zero is the point.
#
# OVERLAP_CHARS (600) exists to stop a chunk boundary severing a fact from its
# subject -- it was raised from 50 to 150 tokens specifically for PAYTM's PPBL
# impairment line. That reasoning holds for a FILING, where a split can orphan
# a fact with nothing left to reconnect it.
#
# IT DOES NOT HOLD FOR A TRANSCRIPT TURN, AND ONLY BECAUSE OF THE CHANGE THAT
# LANDED FIRST. Speaker threading + the "(cont.)" prefix reconnect a
# continuation by ATTRIBUTION rather than by repeated text, so the overlap is
# now doing no work here. The sequence matters: dropping this before threading
# existed would have created the orphans it was protecting against.
#
# It was also actively harmful. 600 against max_chars 800 leaves 200 chars of
# forward progress, so _recursive_split's separator loop overflows repeatedly
# and falls through to its `sep == ""` character-slice branch -- which cuts
# inside words. Measured 2026-08-08 before this change: continuation chunks
# opening 'usiness to work', 'urav's previous question', 'een the principle'.
#
# Zero rather than a smaller non-zero value: ~100 chars does not reliably span
# a sentence, so it would not deliver the safety it implies, and an unmeasured
# constant chosen "just in case" is the habit this project has already paid
# for. Applies to transcript turns only; OVERLAP_CHARS is unchanged everywhere
# else.
TRANSCRIPT_TURN_OVERLAP_CHARS = 0


def _parse_management_roster(blocks: list[PageBlock]) -> set[str]:
    """Names declared under the transcript's own 'Management representatives:'.

    Returns an EMPTY SET when the block is absent or unparseable. The caller
    must treat that as a hard failure for a transcript, never as a default:
    an empty roster classifies every speaker as an analyst, which suppresses
    every claim and produces a clean-looking "no contradictions" result for
    entirely the wrong reason.
    """
    names: set[str] = set()
    for block in blocks:
        text = block.content or ""
        if ROSTER_ANCHOR not in text:
            continue
        lines = text.split("\n")
        start = next(i for i, ln in enumerate(lines) if ROSTER_ANCHOR in ln)
        for line in lines[start + 1:]:
            m = _ROSTER_ENTRY_RE.match(line.strip())
            if not m:
                break          # the list is contiguous; first miss ends it
            names.add(m.group(1).strip())
        if names:
            break
    return names


def _classify_speaker(speaker: str, roster: set[str]) -> str:
    """management / analyst / moderator / unknown."""
    if not speaker:
        return "unknown"       # cover page, disclaimers, pre-roster text
    if speaker == MODERATOR_SPEAKER:
        return "moderator"
    if speaker in roster:
        return "management"
    return "analyst"


def _split_long_turn(speaker: str, turn: str, max_chars: int) -> list[str]:
    """Split one turn, re-prepending the speaker to every continuation piece.

    "(cont.)" IS LOAD-BEARING AND MUST NOT BE DROPPED. The source does not
    repeat the speaker's name; a continuation piece that reads as a fresh
    verbatim attribution is text this system invented at the data-entry point.
    The marker keeps the synthesis visible to anything reading the chunk,
    including the model that will quote it.
    """
    if len(turn) <= max_chars:
        return [turn]

    pieces = _recursive_split(
        text=turn,
        max_chars=max_chars,
        overlap_chars=TRANSCRIPT_TURN_OVERLAP_CHARS,
        separators=SPLIT_SEPARATORS,
    )
    if not pieces:
        return []
    if not speaker:
        return pieces

    prefix = f"{speaker} (cont.): "
    return [pieces[0]] + [prefix + p.lstrip() for p in pieces[1:]]


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
    speaker_role: str = "unknown",
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
        speaker_role=speaker_role,
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
    roster: Optional[set[str]] = None,
    incoming_speaker: str = "",
) -> list[Chunk]:
    """TEXT, RISK_DISCLOSURE, MANAGEMENT_DISCUSSION: recursive split.

    earnings_transcript splits on speaker turns instead -- see
    _split_speaker_turns for why attribution cannot be left to chance.
    """
    target_tokens = TARGET_TOKENS.get(block.block_type, 200)
    max_chars = target_tokens * CHARS_PER_TOKEN

    # (piece_text, speaker_role) pairs. The role must be carried from the turn
    # that produced the piece -- re-deriving it from the chunk text downstream
    # would fail on every continuation piece, which is exactly the population
    # whose attribution matters most.
    pieces: list[tuple[str, str]] = []
    if document_type == TRANSCRIPT_DOC_TYPE:
        turns, outgoing = _split_speaker_turns(
            block.content.strip(), incoming_speaker=incoming_speaker
        )
        for idx, (speaker, turn) in enumerate(turns):
            role = _classify_speaker(speaker, roster or set())
            # A first turn carrying an INHERITED speaker is the tail of a turn
            # that began on the previous page: the text does not name its
            # speaker, so mark it, exactly as _split_long_turn does. Same
            # honesty constraint -- a carried attribution is inferred, not
            # printed, and must not read as verbatim.
            if idx == 0 and incoming_speaker and speaker == incoming_speaker:
                if not turn.startswith(f"{speaker}:"):
                    turn = f"{speaker} (cont.): {turn}"
            for piece in _split_long_turn(speaker, turn, max_chars):
                pieces.append((piece, role))
        _OUTGOING["speaker"] = outgoing
    else:
        for piece in _recursive_split(
            text=block.content.strip(),
            max_chars=max_chars,
            overlap_chars=OVERLAP_CHARS,
            separators=SPLIT_SEPARATORS,
        ):
            pieces.append((piece, "unknown"))
    text_pieces = [t for t, _ in pieces]

    chunks: list[Chunk] = []
    for position, (piece, role) in enumerate(pieces):
        if len(piece.strip()) < MIN_CHUNK_CHARS:
            continue

        chunk_id = _make_chunk_id(doc_id, block.page_number, position, piece)
        metadata = _build_metadata(
            block=block, doc_id=doc_id, tenant_id=tenant_id,
            company=company, ticker=ticker, fiscal_year=fiscal_year,
            quarter=quarter, document_type=document_type,
            filing_date=filing_date, version=version, chunk_id=chunk_id,
            speaker_role=role,
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
        document_type: "quarterly_result" | "annual_report" | "drhp" | "earnings_transcript"
        filing_date:   ISO date string "YYYY-MM-DD"
        version:       Filing version, default "v1"

    Returns:
        List[Chunk] — ready to pass to embedder.py
    """
    # ROSTER IS PARSED ONCE, OVER ALL BLOCKS, BEFORE THE PER-BLOCK LOOP.
    # It is declared on page 1 and needed on every page, so a per-block
    # function cannot see it.
    #
    # RAISES ON AN EMPTY ROSTER rather than warning. An empty roster
    # classifies every speaker as an analyst, the gate then suppresses every
    # claim, and contradiction detection reports a clean "none found" -- the
    # correct-looking answer for entirely the wrong reason. An ingest that
    # cannot identify management is not a successful transcript ingest.
    roster: set[str] = set()
    if document_type == TRANSCRIPT_DOC_TYPE:
        roster = _parse_management_roster(blocks)
        if not roster:
            raise ValueError(
                f"No management roster found: expected a '{ROSTER_ANCHOR}' block "
                f"with a numbered 'Name - Title' list. Without it every speaker "
                f"is classified analyst and every claim is suppressed silently."
            )
        logger.info("Management roster (%d): %s", len(roster), sorted(roster))
    _OUTGOING["speaker"] = ""

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
            # ADVANCE THE SPEAKER ANYWAY. A skipped page still contains speech,
            # and dropping it here would hand the NEXT page a stale speaker from
            # two pages back -- a wrong attribution, which is worse than none.
            if document_type == TRANSCRIPT_DOC_TYPE:
                _, _OUTGOING["speaker"] = _split_speaker_turns(
                    (block.content or "").strip(), _OUTGOING["speaker"]
                )
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
            chunks = _chunk_text_block(
                roster=roster, incoming_speaker=_OUTGOING["speaker"], **kwargs
            )

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