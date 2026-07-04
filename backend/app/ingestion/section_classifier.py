"""
Section Classifier — refines PageBlock.block_type using the intersection
of three independent signals: structure, location, and content.

Why intersection matters:
  - "Revenue from operations" appears in MD&A prose (content alone → false positive)
  - A table on page 7 contains financial-ish words (content + structure → still wrong)
  - Only a TABLE block, inside a DocSection page range, with financial keywords
    is unambiguously a financial statement. All three must align.

This structural tagging is what makes financial_extractor.py robust —
it targets block_type == FINANCIAL_STATEMENT, not floating text anchors.

Inputs:
  List[PageBlock]  — from pdf_parser; block_type is TEXT or TABLE at this point
  List[DocSection] — from document_classifier; provides page ranges + financial_type

Output:
  Same list with block_type and metadata refined in-place.
  No new objects created — downstream modules read the updated list.

Pure function: no DB, no file I/O. Fully testable without infrastructure.
"""

import logging
import re
from .models import BlockType, DocSection, FinancialType, PageBlock

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Content signal dictionaries
# ---------------------------------------------------------------------------

# Minimum number of these keywords required in a TABLE block's content
# for it to be classified as FINANCIAL_STATEMENT (not just any table).
# Using 2 prevents false positives from related-party or segment tables
# that contain one financial word but are not P&L / balance sheet tables.
FINANCIAL_STATEMENT_MIN_KEYWORDS = 2

FINANCIAL_KEYWORDS = {
    # Income statement
    "revenue",
    "income",
    "expenses",
    "profit",
    "loss",
    "ebitda",
    "depreciation",
    "amortisation",
    "amortization",
    "tax",
    "earnings",
    # Balance sheet
    "assets",
    "liabilities",
    "equity",
    "borrowings",
    "payable",
    "receivable",
    # Cash flow
    "cash",
    "flows",
   # Indian-specific & Structural
    "crore",
    "lakh",
    "inr",
    "particulars",
    "financial",    # single word
    "results",      # single word
    "statement",    # single word
    "standalone",
    "consolidated",
}

# OCR-corrupted variants of financial keywords seen in this PDF corpus.
# Extend as new documents reveal new artifacts.
FINANCIAL_KEYWORD_TYPOS = {
    "revenuc",       # Revenue
    "incomc",        # Income
    "cosis",         # Costs
    "particulan",    # Particulars
    "proflt",        # Profit
    "liabilites",    # Liabilities
}

ALL_FINANCIAL_SIGNALS = FINANCIAL_KEYWORDS | FINANCIAL_KEYWORD_TYPOS


# --- Risk disclosure signals ---
# Any of these → RISK_DISCLOSURE (threshold: 1 match, strong signals)
RISK_KEYWORDS = {
    "risk factor",
    "material uncertainty",
    "going concern",
    "contingent liabilit",   # catches both "liability" and "liabilities"
    "legal proceeding",
    "regulatory risk",
    "show cause",
    "litigation",
    "indemnit",
    "pending litigation",
    "compliance risk",
    "sebi",                  # regulatory mentions in risk context
    "enforcement",
    "penalty",
}

# Require 2+ risk keywords to avoid tagging every mention of "risk" in MD&A
RISK_MIN_KEYWORDS = 2


# --- Management Discussion & Analysis signals ---
MANAGEMENT_DISCUSSION_KEYWORDS = {
    "management discussion",
    "management's discussion",
    "business overview",
    "our performance",
    "key highlights",
    "operating performance",
    "growth strategy",
    "competitive landscape",
    "market opportunity",
    "unit economics",
    "contribution margin",
    # Formal SEBI / Companies Act Schedule V MD&A terminology
    # (traditional annual reports use this language; Eternal's shareholder
    # letter used informal tech-company phrasing above — both needed)
    "industry structure",
    "segment-wise performance",
    "segment wise performance",
    "review of operations",
    "state of the company",
    "internal control systems",
    "internal financial control",
    "human resources",
    "human capital",
    "cautionary statement",
    "risks and concerns",
    "opportunities and threats",
    "financial performance review",
    "operational review",
}

MANAGEMENT_DISCUSSION_MIN_KEYWORDS = 2

MD_LETTERED_HEADER_RE = re.compile(
    r'\b[A-Z]\.\s+(Income|Expenses?|Revenue|Results?|Other Income|Cost)\b'
)
MD_VARIANCE_NARRATIVE_RE = re.compile(
    r'\b(increased|decreased|grew|declined)\b.{0,40}?'
    r'(\d+(\.\d+)?%\s*(YoY|y-o-y|year-on-year)|(INR|Rs\.?)\s*[\d,]+\s*crore)',
    re.IGNORECASE,
)
MD_VARIANCE_SUPPORT_KEYWORDS = {"revenue", "expenses", "income", "results", "cost"}


# --- Auditor report signals ---
# These stay as TEXT — auditor reports are qualitative content but
# distinct from MD&A. Tagging them separately allows future filtering.
AUDITOR_KEYWORDS = {
    "independent auditor",
    "chartered accountant",
    "deloitte",
    "emphasis of matter",
    "opinion",
    "material misstatement",
    "audit procedures",
    "engagement partner",
}

AUDITOR_MIN_KEYWORDS = 1


# Anchor phrases that appear ONLY on genuine primary statement pages —
# never in Notes, Board Report, CGR, or the running page header/footer.
# This is the actual distinguishing signal; keyword-count alone (below)
# cannot tell "page about financial statements" from "page mentioning
# financial concepts," since the document's running header contains
# "Financial Statements" on every single page.
STATEMENT_TITLE_ANCHORS = {
    "statement of profit and loss",
    "statement of profit & loss",
    "balance sheet",
    "statement of cash flow",
    "consolidated balance sheet",
    "standalone balance sheet",
    # SEBI quarterly results filings use a regulatory heading instead of
    # the Companies Act statement titles above — different document class,
    # same underlying content.
    "statement of standalone",
    "statement of consolidated",
    "financial results for the quarter",
    "financial results for the year",
    "unaudited financial results",
    "audited financial results",
}

# Multi-page statement handling: only the FIRST page of a statement carries
# the title (e.g. "Statement of Cash Flows"); continuation pages are bare
# numeric tables. ANCHOR_HEADING_CHARS restricts anchor matching to the top
# of the page (where titles physically sit) so the same phrase appearing
# mid-paragraph in Notes-to-Accounts prose doesn't trigger a false positive.
# CONTINUATION_MAX_PAGES lets a small, bounded run of subsequent TABLE pages
# in the same section inherit the label without re-matching the anchor —
# this is the direct fix for the blueprint's Trap 6 (headers lost on
# multi-page tables) applied one layer up, at classification rather than
# chunking.
ANCHOR_HEADING_CHARS = 400
CONTINUATION_MAX_PAGES = 4


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _count_keyword_matches(content_lower: str, keywords: set[str]) -> int:
    """Count how many keywords from the set appear in the lowercased content."""
    return sum(1 for kw in keywords if kw in content_lower)


def _build_page_to_section(sections: list[DocSection]) -> dict[int, DocSection]:
    """
    Build a lookup dict: page_number → DocSection.
    Pages not covered by any section map to None.
    """
    mapping: dict[int, DocSection] = {}
    for section in sections:
        for page in range(section.page_start, section.page_end + 1):
            mapping[page] = section
    return mapping


# ---------------------------------------------------------------------------
# Core classification logic
# ---------------------------------------------------------------------------

def _classify_table_block(
    block: PageBlock,
    section: DocSection | None,
    true_anchor_page: Optional[int],
    true_anchor_financial_type,
) -> str:
    if section is None:
        return BlockType.TABLE

    content_lower = block.content.lower()
    heading_zone = content_lower[:ANCHOR_HEADING_CHARS]

    has_anchor = any(anchor in heading_zone for anchor in STATEMENT_TITLE_ANCHORS)
    if has_anchor:
        return BlockType.FINANCIAL_STATEMENT

    # Continuation distance is measured from the TRUE anchor page only —
    # never from a prior continuation page. This bounds the window to a
    # fixed span regardless of how many continuation pages appear inside
    # it, preventing the chain from propagating indefinitely through a
    # long, keyword-rich section like Notes to Accounts.
    is_continuation = (
        true_anchor_page is not None
        and section.financial_type == true_anchor_financial_type
        and 0 < (block.page_number - true_anchor_page) <= CONTINUATION_MAX_PAGES
    )
    if is_continuation:
        keyword_count = _count_keyword_matches(content_lower, ALL_FINANCIAL_SIGNALS)
        if keyword_count >= FINANCIAL_STATEMENT_MIN_KEYWORDS:
            return BlockType.FINANCIAL_STATEMENT

    return BlockType.TABLE


def _classify_text_block(block: PageBlock) -> str:
    """
    Classify a TEXT block by content signals.

    Priority order (most specific first):
      RISK_DISCLOSURE       — legal/regulatory risk language
      MANAGEMENT_DISCUSSION — MD&A / business narrative
      TEXT                  — everything else (letters, preambles, auditor reports)

    Note: auditor report pages (36-39 in Eternal Q4FY26) stay as TEXT.  
    They're qualitative content but not MD&A — a distinct category handled
    by the retrieval layer's metadata filtering.
    """
    content_lower = block.content.lower()

    # Risk disclosure check
    risk_matches = _count_keyword_matches(content_lower, RISK_KEYWORDS)
    if risk_matches >= RISK_MIN_KEYWORDS:
        logger.debug(
            "Page %d TEXT → RISK_DISCLOSURE (%d risk keywords)",
            block.page_number, risk_matches,
        )
        return BlockType.RISK_DISCLOSURE

    # MD&A check
    md_matches = _count_keyword_matches(content_lower, MANAGEMENT_DISCUSSION_KEYWORDS)
    if md_matches >= MANAGEMENT_DISCUSSION_MIN_KEYWORDS:
        logger.debug(
            "Page %d TEXT → MANAGEMENT_DISCUSSION (%d MD&A keywords)",
            block.page_number, md_matches,
        )
        return BlockType.MANAGEMENT_DISCUSSION

    return BlockType.TEXT


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_blocks(
    blocks: list[PageBlock],
    sections: list[DocSection],
) -> list[PageBlock]:
    """
    Refine block_type on every PageBlock using structural + location + content signals.

    Mutates block_type in place and returns the same list.
    Also injects financial_type into blocks via a new attribute for downstream use.

    Args:
        blocks:   Output of pdf_parser.parse_pdf() — block_type is TEXT or TABLE
        sections: Output of document_classifier.detect_sections()

    Returns:
        Same list with block_type refined. Never removes blocks.
    """
    page_to_section = _build_page_to_section(sections)

    counts = {t: 0 for t in [
        BlockType.FINANCIAL_STATEMENT,
        BlockType.RISK_DISCLOSURE,
        BlockType.MANAGEMENT_DISCUSSION,
        BlockType.TABLE,
        BlockType.TEXT,
        BlockType.UNKNOWN,
    ]}

    true_anchor_page = None
    true_anchor_financial_type = None

    for block in blocks:
        section = page_to_section.get(block.page_number)
        content_lower = block.content.lower()
        original_type = block.block_type

        risk_matches = _count_keyword_matches(content_lower, RISK_KEYWORDS)
        md_matches = _count_keyword_matches(content_lower, MANAGEMENT_DISCUSSION_KEYWORDS)
        is_narrative_md = (
            original_type == BlockType.TEXT
            and (
                MD_LETTERED_HEADER_RE.search(block.content)
                or (
                    MD_VARIANCE_NARRATIVE_RE.search(block.content)
                    and _count_keyword_matches(content_lower, MD_VARIANCE_SUPPORT_KEYWORDS) >= 1
                )
            )
        )

        heading_zone = content_lower[:ANCHOR_HEADING_CHARS]
        is_true_anchor = (
            original_type == BlockType.TABLE
            and any(anchor in heading_zone for anchor in STATEMENT_TITLE_ANCHORS)
        )

        if risk_matches >= RISK_MIN_KEYWORDS:
            block.block_type = BlockType.RISK_DISCLOSURE
        elif md_matches >= MANAGEMENT_DISCUSSION_MIN_KEYWORDS or is_narrative_md:
            block.block_type = BlockType.MANAGEMENT_DISCUSSION
        elif original_type == BlockType.TABLE:
            block.block_type = _classify_table_block(block, section, true_anchor_page, true_anchor_financial_type)
        else:
            block.block_type = BlockType.TEXT

        block.financial_type = section.financial_type if section else FinancialType.UNKNOWN

        # Only a genuine title-anchor hit resets the anchor point — a
        # continuation page inheriting the label does NOT extend the window.
        if is_true_anchor:
            true_anchor_page = block.page_number
            true_anchor_financial_type = block.financial_type

    return blocks

def get_blocks_by_type(
    blocks: list[PageBlock],
    block_type: str,
) -> list[PageBlock]:
    """
    Filter helper — returns all blocks matching a given block_type.
    Used by financial_extractor.py:
      get_blocks_by_type(blocks, BlockType.FINANCIAL_STATEMENT)
    """
    return [b for b in blocks if b.block_type == block_type]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import os
    import logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from pathlib import Path
    from .pdf_parser import parse_pdf
    from .document_classifier import detect_sections

    pdf_path = Path(
        sys.argv[1] if len(sys.argv) > 1
        else os.path.expanduser(
            "~/ledgermind/docs/raw/"
            "ETERNAL_Q4FY26_SHAREHOLDER_LETTER_AND_RESULTS.pdf"
        )
    )

    print(f"\nParsing: {pdf_path.name}")
    blocks = parse_pdf(str(pdf_path))
    print(f"Raw blocks: {len(blocks)}")

    sections = detect_sections(blocks)
    print(f"Sections: {[(s.financial_type, s.page_start, s.page_end) for s in sections]}")

    # Run classifier
    blocks = classify_blocks(blocks, sections)

    # --- Summary by type ---
    print("\n--- Block type distribution ---")
    from collections import Counter
    type_counts = Counter(b.block_type for b in blocks)
    for block_type, count in sorted(type_counts.items()):
        print(f"  {block_type:30s} : {count}")

    # --- FINANCIAL_STATEMENT blocks (the critical ones) ---
    financial_blocks = get_blocks_by_type(blocks, BlockType.FINANCIAL_STATEMENT)
    print(f"\n--- FINANCIAL_STATEMENT blocks ({len(financial_blocks)}) ---")
    for b in financial_blocks:
        financial_type = getattr(b, "financial_type", "unknown")
        preview = b.content[:80].replace("\n", " ")
        print(f"  [Page {b.page_number:2d}] [{financial_type:13s}] {preview}")

    # --- Assertions ---
    assert len(financial_blocks) >= 1, \
        "Expected at least 1 FINANCIAL_STATEMENT block — check keyword signals"

    # Every FINANCIAL_STATEMENT block must have known financial_type
    for b in financial_blocks:
        ft = getattr(b, "financial_type", None)
        assert ft in (FinancialType.CONSOLIDATED, FinancialType.STANDALONE), \
            f"Page {b.page_number} FINANCIAL_STATEMENT has unexpected financial_type: {ft}"

    # Consolidated financial blocks should be pages 1-35
    consol_blocks = [
        b for b in financial_blocks
        if getattr(b, "financial_type", None) == FinancialType.CONSOLIDATED
    ]
    for b in consol_blocks:
        assert b.page_number <= 35, \
            f"Consolidated block found on page {b.page_number} — expected ≤35"

    # Standalone financial blocks should be pages 36-44
    standalone_blocks = [
        b for b in financial_blocks
        if getattr(b, "financial_type", None) == FinancialType.STANDALONE
    ]
    for b in standalone_blocks:
        assert b.page_number >= 36, \
            f"Standalone block found on page {b.page_number} — expected ≥36"

    # Spot check: MD&A or risk blocks should exist (Eternal has both)
    risk_blocks = get_blocks_by_type(blocks, BlockType.RISK_DISCLOSURE)
    md_blocks   = get_blocks_by_type(blocks, BlockType.MANAGEMENT_DISCUSSION)
    print(f"\n--- Risk disclosure blocks  : {len(risk_blocks)} ---")
    print(f"--- Management discussion   : {len(md_blocks)} ---")

    print("\nAll assertions passed.")