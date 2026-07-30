"""
LedgerMind — Phase 4: Contradiction Detection Engine
========================================================
Used by Path 3 (cross_engine) to compare qualitative claims against
verified quantitative SQL results.

Two contradiction types:

  1. MAGNITUDE — a numeric claim in qualitative text vs the SQL value.
  2. DIRECTIONAL — directional language ("declined", "grew") vs the SIGN of
     a YoY computation from quant_engine.

No LLM call. Pure regex + arithmetic — same philosophy as dsl_compiler.py.

WHAT COUNTS AS A CLAIM (read before loosening any of this)
-----------------------------------------------------------
The first shipped version treated EVERY crore figure in EVERY retrieved chunk
as a claim about the queried metric. Confirmed live 2026-07-30: the question
"Does ETERNAL's management commentary on profitability align with its actual
PAT for FY26?" produced ELEVEN "severity: high" contradictions against
PAT = INR 366 Cr, including +4730.6%, +7244.3% and -99.7%. None were
contradictions. They were cash-flow lines, Adjusted EBITDA and other line
items that happened to share a chunk, differenced against an unrelated metric.

Worse, the top-cited chunk was page 33 — the Consolidated Statement of Cash
Flows, which is part of the SAME document the `financials` row was extracted
from. The engine was flagging disagreement between a verified value and its
own source. Circular by construction.

Blueprint §25B's Trap 7 anticipated a narrower failure (an approximation like
"approximately INR 12,000 crore" flagged against an exact INR 12,114 crore)
and prescribed a tolerance threshold. Tolerance is necessary but was never
sufficient: the real defect is comparing numbers that are not about the metric
at all. Two additional constraints now apply:

  A. NARRATIVE CHUNKS ONLY. FINANCIAL_STATEMENT and TABLE chunks are the
     extraction source, not independent claims about it. A table of figures
     also has no prose tying any metric name to any number, so proximity
     anchoring below cannot work on it either.

  B. METRIC PROXIMITY. A figure is a claim about PAT only if a PAT alias
     appears within PROXIMITY_WINDOW characters of it ("PAT of INR 366 crore",
     "profit after tax was INR 366 crore"). Aliases come from the shared
     registry (app/metrics/registry.py), never a second hand-maintained list —
     three parallel metric dicts is the exact split that file was created to
     end.

A FALSE contradiction is worse than a missed one. This system's stated value
is surfacing disagreement instead of fabricating certainty; fabricating
disagreement is the one failure that directly inverts that claim. These rules
are deliberately strict and will miss real contradictions phrased at a
distance from the metric name. That trade is intentional.
"""

import logging
import re
from typing import List, Optional

from app.engines.state import ChunkResult, ContradictionFlag
from app.metrics.registry import get_metric

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Claims within this % of the SQL value are consistent, not flagged (Trap 7).
MAGNITUDE_TOLERANCE_PCT = 5.0

SEVERITY_HIGH_PCT   = 20.0   # >20% off → high severity
SEVERITY_MEDIUM_PCT = 10.0   # 10-20% off → medium severity
# <10% but >tolerance → low severity

# Characters either side of a figure searched for a metric alias. ~120 covers
# "profit after tax for the year ended March 31, 2026 was INR 366 crore"
# without spanning into an adjacent unrelated sentence.
PROXIMITY_WINDOW = 120

# Chunk types that can carry an independent qualitative claim. Everything else
# (FINANCIAL_STATEMENT, TABLE) is the extraction source — see module docstring.
NARRATIVE_CHUNK_TYPES = frozenset({
    "TEXT", "RISK_DISCLOSURE", "MANAGEMENT_DISCUSSION", "FOOTNOTE",
})

# ---------------------------------------------------------------------------
# Numeric claim extraction — Indian currency formats
# ---------------------------------------------------------------------------

# Matches: "₹12,114 crore", "Rs 12,114 Cr", "12,114 crore", "₹12114.5 Cr"
_CRORE_PATTERN = re.compile(
    r"(?:₹|Rs\.?|INR)?\s*"
    r"([\d,]+(?:\.\d+)?)\s*"
    r"(?:crore|cr\.?)\b",
    re.IGNORECASE,
)

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
# Metric anchoring
# ---------------------------------------------------------------------------

def _metric_alias_pattern(sql_metric: str) -> Optional[re.Pattern]:
    """
    Regex matching any alias of `sql_metric`, sourced from the shared registry.

    Returns None when the metric is unknown to the registry — the caller then
    skips detection entirely rather than falling back to unanchored matching,
    which is the behaviour that produced the false positives.
    """
    if not sql_metric:
        return None

    key = sql_metric.strip().lower().replace(" ", "_")
    metric = get_metric(key) or get_metric(sql_metric.strip().lower())
    if metric is None:
        logger.warning(
            "Metric %r not found in registry — skipping contradiction detection "
            "rather than comparing figures with no metric anchor.", sql_metric,
        )
        return None

    # Longest-first so "profit after tax" wins over "pat".
    aliases = sorted(set(metric.aliases), key=len, reverse=True)
    return re.compile(
        "|".join(rf"\b{re.escape(a)}\b" for a in aliases), re.IGNORECASE
    )


def _is_narrative(chunk: ChunkResult) -> bool:
    """True if this chunk can hold an independent claim (see module docstring)."""
    return (chunk.get("chunk_type") or "").upper() in NARRATIVE_CHUNK_TYPES


def _near(text: str, start: int, end: int, pattern: re.Pattern) -> bool:
    """True if `pattern` matches within PROXIMITY_WINDOW chars of [start, end)."""
    window = text[max(0, start - PROXIMITY_WINDOW):min(len(text), end + PROXIMITY_WINDOW)]
    return bool(pattern.search(window))


# ---------------------------------------------------------------------------
# Numeric claim extraction
# ---------------------------------------------------------------------------

def extract_numeric_claims(text: str, anchor: Optional[re.Pattern] = None) -> List[float]:
    """
    Crore-denominated figures in `text`.

    When `anchor` is supplied, only figures with a metric alias within
    PROXIMITY_WINDOW characters are returned. Without it every figure is
    returned — kept for callers that do their own filtering, but note that
    contradiction detection must always pass an anchor.
    """
    claims: List[float] = []
    for match in _CRORE_PATTERN.finditer(text):
        if anchor is not None and not _near(text, match.start(), match.end(), anchor):
            continue
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

def extract_direction(text: str, anchor: Optional[re.Pattern] = None) -> Optional[str]:
    """
    Dominant directional sentiment: 'positive', 'negative', or None.

    With `anchor`, only polarity words within PROXIMITY_WINDOW of a metric
    alias are counted. Unanchored, "revenue grew strongly" in a chunk would
    contradict a PAT decline — a different metric entirely.

    Ties are treated as ambiguous (None) rather than guessed.
    """
    def _count(pattern: re.Pattern) -> int:
        if anchor is None:
            return len(pattern.findall(text))
        return sum(
            1 for m in pattern.finditer(text)
            if _near(text, m.start(), m.end(), anchor)
        )

    pos_matches = _count(_POSITIVE_DIRECTION)
    neg_matches = _count(_NEGATIVE_DIRECTION)

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
    Compare metric-anchored numeric claims in NARRATIVE chunks against the
    verified SQL value. See module docstring for why both restrictions exist.
    """
    flags: List[ContradictionFlag] = []

    if sql_value is None or sql_value == 0:
        logger.debug("Skipping magnitude check — sql_value is None or zero")
        return flags

    anchor = _metric_alias_pattern(sql_metric)
    if anchor is None:
        return flags

    skipped_non_narrative = 0

    for chunk in chunks:
        if not _is_narrative(chunk):
            skipped_non_narrative += 1
            continue

        claims = extract_numeric_claims(chunk["text"], anchor=anchor)

        for claim_value in claims:
            delta_pct = (claim_value - sql_value) / abs(sql_value) * 100

            if abs(delta_pct) <= MAGNITUDE_TOLERANCE_PCT:
                logger.debug(
                    "Magnitude check: claim=%.2f sql=%.2f delta=%.2f%% — WITHIN TOLERANCE",
                    claim_value, sql_value, delta_pct,
                )
                continue

            severity = _classify_severity(delta_pct)
            flags.append(ContradictionFlag(
                type="magnitude",
                qualitative_claim=chunk["text"][:200].strip(),
                qualitative_source=chunk["chunk_id"],
                quantitative_value=sql_value,
                quantitative_metric=sql_metric,
                delta_pct=round(delta_pct, 2),
                severity=severity,
            ))

            logger.info(
                "Magnitude contradiction flagged | claim=%.2f sql=%.2f delta=%.2f%% "
                "severity=%s | chunk=%s",
                claim_value, sql_value, delta_pct, severity, chunk["chunk_id"],
            )

    if skipped_non_narrative:
        logger.debug(
            "Magnitude check skipped %d non-narrative chunk(s) (statement/table "
            "chunks are the extraction source, not independent claims)",
            skipped_non_narrative,
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
    Compare metric-anchored directional language in NARRATIVE chunks against
    the SIGN of a YoY growth computation.
    """
    flags: List[ContradictionFlag] = []

    if yoy_pct is None:
        logger.debug("Skipping directional check — no yoy_pct available")
        return flags

    sql_direction = "positive" if yoy_pct > 0 else ("negative" if yoy_pct < 0 else None)
    if sql_direction is None:
        return flags   # flat growth, no directional claim to contradict

    anchor = _metric_alias_pattern(sql_metric)
    if anchor is None:
        return flags

    for chunk in chunks:
        if not _is_narrative(chunk):
            continue

        text_direction = extract_direction(chunk["text"], anchor=anchor)
        if text_direction is None:
            continue

        if text_direction != sql_direction:
            flags.append(ContradictionFlag(
                type="direction",
                qualitative_claim=chunk["text"][:200].strip(),
                qualitative_source=chunk["chunk_id"],
                quantitative_value=yoy_pct,
                quantitative_metric=f"{sql_metric}_yoy_growth",
                delta_pct=None,
                severity="high",
            ))

            logger.info(
                "Directional contradiction flagged | text_direction=%s "
                "sql_direction=%s yoy_pct=%.2f | chunk=%s",
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
    Run both detectors and merge, sorted by severity (high first).

    Both detectors require `sql_metric` to resolve in the shared metric
    registry. An unresolvable metric yields NO flags — see module docstring.
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
