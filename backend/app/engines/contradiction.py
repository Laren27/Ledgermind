"""
LedgerMind — Phase 4: Contradiction Detection Engine
========================================================
Used by Path 3 (cross_engine) to compare qualitative claims against
verified quantitative SQL results.

Two contradiction types:

  1. MAGNITUDE — a numeric claim in qualitative text vs the SQL value.
     Tolerance-based: claims within TOLERANCE_PCT of the SQL value are
     treated as consistent, not flagged. This directly addresses Trap 7
     (blueprint §25B): "approximately ₹12,000 crore" vs SQL "₹12,114 crore"
     must NOT be flagged — they're within 1%.

  2. DIRECTIONAL — qualitative text uses absolute directional language
     ("revenue declined", "grew strongly") that contradicts the SIGN of
     a YoY computation from quant_engine. No numeric extraction needed —
     just polarity comparison.

No LLM call. Pure regex + arithmetic — same philosophy as dsl_compiler.py.
Deterministic, auditable, no hallucination risk in the comparison itself.
"""

import logging
import re
from typing import List, Optional

from app.engines.state import ChunkResult, ContradictionFlag

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Tolerance for magnitude comparisons — claims within this % of SQL value
# are treated as consistent (Trap 7 fix). 5% per blueprint §25B example.
MAGNITUDE_TOLERANCE_PCT = 5.0

# Severity bands for magnitude contradictions that DO exceed tolerance
SEVERITY_HIGH_PCT   = 20.0   # >20% off → high severity
SEVERITY_MEDIUM_PCT = 10.0   # 10-20% off → medium severity
# <10% but >tolerance → low severity

# ---------------------------------------------------------------------------
# Numeric claim extraction — Indian currency formats
# ---------------------------------------------------------------------------

# Matches: "₹12,114 crore", "Rs 12,114 Cr", "12,114 crore", "₹12114.5 Cr"
# Captures the numeric value (with commas/decimals) before the crore unit.
_CRORE_PATTERN = re.compile(
    r"(?:₹|Rs\.?|INR)?\s*"
    r"([\d,]+(?:\.\d+)?)\s*"
    r"(?:crore|cr\.?)\b",
    re.IGNORECASE,
)

# "approximately X" / "around X" / "nearly X" / "about X" — signals an
# approximation, used to widen tolerance interpretation in logging only.
_APPROXIMATION_SIGNAL = re.compile(
    r"\b(approximately|around|nearly|about|roughly|~)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Directional language patterns
# ---------------------------------------------------------------------------

_POSITIVE_DIRECTION = re.compile(
    r"\b(grew|grow|growth|increased?|increasing|rose|rising|surged?|"
    r"improved?|improving|expanded?|expanding|strengthened?|gained?|"
    r"higher|stronger|robust|accelerat\w+)\b",
    re.IGNORECASE,
)

_NEGATIVE_DIRECTION = re.compile(
    r"\b(declined?|declining|decreased?|decreasing|fell|falling|dropped?|"
    r"dropping|contracted?|contracting|weakened?|weakening|lost|lower|"
    r"weaker|slowdown|deteriorat\w+|shrank|shrinking)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Numeric claim extraction
# ---------------------------------------------------------------------------

def extract_numeric_claims(text: str) -> List[float]:
    """
    Extract all crore-denominated numeric claims from a text chunk.
    Returns a list of float values (commas stripped, parsed).
    Handles "12,114" → 12114.0
    """
    claims = []
    for match in _CRORE_PATTERN.finditer(text):
        raw_number = match.group(1).replace(",", "")
        try:
            claims.append(float(raw_number))
        except ValueError:
            continue
    return claims


def has_approximation_language(text: str) -> bool:
    """True if text contains hedging language like 'approximately', 'around'."""
    return bool(_APPROXIMATION_SIGNAL.search(text))


# ---------------------------------------------------------------------------
# Directional sentiment extraction
# ---------------------------------------------------------------------------

def extract_direction(text: str) -> Optional[str]:
    """
    Determine the dominant directional sentiment in a text chunk.
    Returns 'positive', 'negative', or None if no clear signal / mixed signal.

    Simple heuristic: count matches for each polarity, return the dominant one.
    If counts are equal and non-zero, treat as ambiguous (None) — don't guess.
    """
    pos_matches = len(_POSITIVE_DIRECTION.findall(text))
    neg_matches = len(_NEGATIVE_DIRECTION.findall(text))

    if pos_matches == 0 and neg_matches == 0:
        return None
    if pos_matches > neg_matches:
        return "positive"
    if neg_matches > pos_matches:
        return "negative"
    return None   # tied — ambiguous, don't flag


# ---------------------------------------------------------------------------
# Severity classification
# ---------------------------------------------------------------------------

def _classify_severity(delta_pct: float) -> str:
    """Classify contradiction severity based on % deviation."""
    abs_delta = abs(delta_pct)
    if abs_delta >= SEVERITY_HIGH_PCT:
        return "high"
    elif abs_delta >= SEVERITY_MEDIUM_PCT:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Core: magnitude contradiction detection
# ---------------------------------------------------------------------------

def detect_magnitude_contradictions(
    chunks: List[ChunkResult],
    sql_value: float,
    sql_metric: str,
) -> List[ContradictionFlag]:
    """
    Compare numeric claims in qualitative chunks against the verified SQL value.

    For each chunk, extract all crore-denominated numbers and compare each
    against sql_value. A claim within MAGNITUDE_TOLERANCE_PCT is consistent
    and NOT flagged (Trap 7 fix). Claims outside tolerance ARE flagged with
    severity based on how far off they are.

    Returns a list of ContradictionFlag — empty if everything is consistent.
    """
    flags: List[ContradictionFlag] = []

    if sql_value is None or sql_value == 0:
        logger.debug("Skipping magnitude check — sql_value is None or zero")
        return flags

    for chunk in chunks:
        claims = extract_numeric_claims(chunk["text"])

        for claim_value in claims:
            delta_pct = (claim_value - sql_value) / abs(sql_value) * 100

            if abs(delta_pct) <= MAGNITUDE_TOLERANCE_PCT:
                # Within tolerance — consistent, not a contradiction
                logger.debug(
                    "Magnitude check: claim=%.2f sql=%.2f delta=%.2f%% — WITHIN TOLERANCE",
                    claim_value, sql_value, delta_pct,
                )
                continue

            severity = _classify_severity(delta_pct)
            flag = ContradictionFlag(
                type="magnitude",
                qualitative_claim=chunk["text"][:200].strip(),
                qualitative_source=chunk["chunk_id"],
                quantitative_value=sql_value,
                quantitative_metric=sql_metric,
                delta_pct=round(delta_pct, 2),
                severity=severity,
            )
            flags.append(flag)

            logger.info(
                "Magnitude contradiction flagged | claim=%.2f sql=%.2f delta=%.2f%% severity=%s | chunk=%s",
                claim_value, sql_value, delta_pct, severity, chunk["chunk_id"],
            )

    return flags


# ---------------------------------------------------------------------------
# Core: directional contradiction detection
# ---------------------------------------------------------------------------

def detect_directional_contradictions(
    chunks: List[ChunkResult],
    yoy_pct: Optional[float],
    sql_metric: str,
) -> List[ContradictionFlag]:
    """
    Compare directional language in qualitative chunks against the SIGN
    of a YoY growth computation.

    If quant_engine computed yoy_pct = +31.69% (positive growth) but a
    chunk says "revenue declined", that's a directional contradiction —
    regardless of the exact magnitude.

    Returns a list of ContradictionFlag — empty if directions align or
    yoy_pct is None (no YoY computation available to compare against).
    """
    flags: List[ContradictionFlag] = []

    if yoy_pct is None:
        logger.debug("Skipping directional check — no yoy_pct available")
        return flags

    sql_direction = "positive" if yoy_pct > 0 else ("negative" if yoy_pct < 0 else None)
    if sql_direction is None:
        return flags   # flat growth, no directional claim to contradict

    for chunk in chunks:
        text_direction = extract_direction(chunk["text"])

        if text_direction is None:
            continue   # no clear directional language in this chunk

        if text_direction != sql_direction:
            flag = ContradictionFlag(
                type="direction",
                qualitative_claim=chunk["text"][:200].strip(),
                qualitative_source=chunk["chunk_id"],
                quantitative_value=yoy_pct,
                quantitative_metric=f"{sql_metric}_yoy_growth",
                delta_pct=None,   # not applicable for directional flags
                severity="high",  # directional mismatches are always high severity
            )
            flags.append(flag)

            logger.info(
                "Directional contradiction flagged | text_direction=%s sql_direction=%s "
                "yoy_pct=%.2f | chunk=%s",
                text_direction, sql_direction, yoy_pct, chunk["chunk_id"],
            )

    return flags


# ---------------------------------------------------------------------------
# Public interface — used by cross_engine.py
# ---------------------------------------------------------------------------

def detect_contradictions(
    chunks: List[ChunkResult],
    sql_value: Optional[float] = None,
    sql_metric: str = "",
    yoy_pct: Optional[float] = None,
) -> List[ContradictionFlag]:
    """
    Run both contradiction detectors and merge results.

    Args:
      chunks:     Retrieved chunks from semantic_engine (qualitative side).
      sql_value:  Point-in-time SQL value to compare numeric claims against.
                  None skips magnitude detection.
      sql_metric: Human-readable metric name for the flag (e.g. "Revenue").
      yoy_pct:    YoY growth % from quant_engine, for directional comparison.
                  None skips directional detection.

    Returns combined list of ContradictionFlag, sorted by severity (high first).
    """
    all_flags: List[ContradictionFlag] = []

    if sql_value is not None:
        all_flags.extend(
            detect_magnitude_contradictions(chunks, sql_value, sql_metric)
        )

    if yoy_pct is not None:
        all_flags.extend(
            detect_directional_contradictions(chunks, yoy_pct, sql_metric)
        )

    severity_order = {"high": 0, "medium": 1, "low": 2}
    all_flags.sort(key=lambda f: severity_order.get(f["severity"], 3))

    logger.info(
        "Contradiction detection complete | total_flags=%d (magnitude+directional)",
        len(all_flags),
    )

    return all_flags