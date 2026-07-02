"""
app/ingestion/gate.py

Pre-ingestion gate: rejects PDFs that are not SEBI-filing-class financial
documents (annual reports, quarterly results, DRHPs, auditor's reports)
before any parsing, table reconstruction, or embedding cost is incurred.

Deterministic keyword/regex scoring — NOT an ML classifier or LLM call.
Per architecture decision (Blueprint Trap 1 / v2 review Challenge 1 resolution).
"""

import re
from dataclasses import dataclass, field
from enum import Enum


class GateDecision(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"


@dataclass
class GateResult:
    decision: GateDecision
    score: int
    matched_categories: list[str] = field(default_factory=list)
    matched_signals: list[str] = field(default_factory=list)
    reason: str = ""


# Signal categories. Each signal is (regex_pattern, weight).
# A document must hit signals from at least MIN_CATEGORIES distinct
# categories AND clear MIN_SCORE to pass.
SIGNAL_CATEGORIES: dict[str, list[tuple[str, int]]] = {
    "regulatory_citation": [
        (r"\bSEBI\b", 2),
        (r"Regulation\s+3[03]", 3),
        (r"Regulation\s+52", 3),
        (r"Listing Obligations and Disclosure Requirements", 3),
        (r"\bLODR\b", 2),
    ],
    "statement_type": [
        (r"Financial Results", 3),
        (r"Statement of Profit and Loss", 3),
        (r"Statement of.*Financial Results", 3),
        (r"Balance Sheet", 2),
        (r"Segment Information", 2),
    ],
    "audit_and_accounting": [
        (r"Ind\s?AS[\s-]?\d*", 2),
        (r"Auditor'?s Report", 3),
        (r"Limited Review Report", 3),
        (r"Chartered Accountants", 2),
        (r"\bCIN\s*:", 2),
    ],
    "financial_type": [
        (r"\bConsolidated\b", 1),
        (r"\bStandalone\b", 1),
        (r"Total income", 1),
        (r"Total Income", 1),
    ],
}

MIN_SCORE = 6
MIN_CATEGORIES = 2

# First N characters of extracted text to scan (roughly first 2 pages).
SCAN_CHAR_LIMIT = 6000


def check_is_financial_filing(first_pages_text: str) -> GateResult:
    """
    Score raw extracted text from the first ~2 pages of an uploaded PDF.

    Args:
        first_pages_text: plain text extracted via pdfplumber.extract_text()
            (or equivalent) from page 1-2 only. No layout mode needed —
            this is a scoring pass, not a parsing pass.

    Returns:
        GateResult with ACCEPT/REJECT decision and diagnostic detail.
    """
    text = first_pages_text[:SCAN_CHAR_LIMIT]

    total_score = 0
    matched_categories = []
    matched_signals = []

    for category, signals in SIGNAL_CATEGORIES.items():
        category_hit = False
        for pattern, weight in signals:
            if re.search(pattern, text, re.IGNORECASE):
                total_score += weight
                matched_signals.append(pattern)
                category_hit = True
        if category_hit:
            matched_categories.append(category)

    passes_score = total_score >= MIN_SCORE
    passes_categories = len(matched_categories) >= MIN_CATEGORIES

    if passes_score and passes_categories:
        return GateResult(
            decision=GateDecision.ACCEPT,
            score=total_score,
            matched_categories=matched_categories,
            matched_signals=matched_signals,
            reason="Document matches SEBI-filing signal profile.",
        )

    reason_parts = []
    if not passes_score:
        reason_parts.append(f"score {total_score} < required {MIN_SCORE}")
    if not passes_categories:
        reason_parts.append(
            f"matched {len(matched_categories)} categories "
            f"({matched_categories}) < required {MIN_CATEGORIES}"
        )

    return GateResult(
        decision=GateDecision.REJECT,
        score=total_score,
        matched_categories=matched_categories,
        matched_signals=matched_signals,
        reason=(
            "Document does not appear to be a SEBI-filing-class financial "
            "document (annual report, quarterly result, DRHP, or auditor's "
            f"report). {'; '.join(reason_parts)}."
        ),
    )