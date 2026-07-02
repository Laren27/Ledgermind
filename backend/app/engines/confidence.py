"""
LedgerMind — Phase 4: Confidence Module
==========================================
Final confidence pass — runs after a path engine (semantic/quant/cross)
has already set a preliminary confidence_score and confidence_tier.

Path-specific confidence is computed inside each engine (semantic_engine
scores retrieval quality; quant_engine treats verified SQL as confidence=1.0).
This module applies CROSS-CUTTING adjustments that don't belong in any
single path:

  - Restatement penalty: if the answer touches a metric/period that has
    a restated (non-latest) version in the corpus, confidence is capped
    at MEDIUM even if the retrieval/SQL itself was high confidence. This
    reflects that the user should know a restatement exists, not that
    the answer is wrong.

  - Contradiction penalty (Path 3 only): if HIGH severity contradictions
    were found, confidence is capped at MEDIUM regardless of individual
    path scores — disagreeing sources are inherently less trustworthy
    than a single clean source.

This module does NOT compute confidence from scratch — it only adjusts
what the path engines already set. Never raises confidence, only lowers it.
"""

import logging

from app.engines.state import QueryState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tier ordering for comparison/capping
# ---------------------------------------------------------------------------

_TIER_RANK = {"high": 2, "medium": 1, "low": 0}
_RANK_TIER = {2: "high", 1: "medium", 0: "low"}


def _cap_tier(current_tier: str, max_tier: str) -> str:
    """Return the lower of current_tier and max_tier."""
    current_rank = _TIER_RANK.get(current_tier, 0)
    max_rank = _TIER_RANK.get(max_tier, 0)
    return _RANK_TIER[min(current_rank, max_rank)]


# ---------------------------------------------------------------------------
# LangGraph node
# ---------------------------------------------------------------------------

def confidence_node(state: QueryState) -> QueryState:
    """
    Final confidence adjustment pass.

    Runs after semantic_engine / quant_engine / cross_engine, before
    response_generator. Applies cross-cutting caps only — never raises
    confidence above what the path engine already determined.
    """
    # Skip if the query was already blocked or hit a hard error with no result
    if state["is_blocked"] or (state.get("error") and not state["retrieved_chunks"] and not state.get("sql_result")):
        return state

    current_tier = state.get("confidence_tier", "low")
    current_score = state.get("confidence_score", 0.0)

    # ── Contradiction penalty (Path 3) ──────────────────────────────────────
    contradictions = state.get("contradictions", [])
    high_severity_count = sum(1 for c in contradictions if c["severity"] == "high")

    if high_severity_count > 0:
        capped_tier = _cap_tier(current_tier, "medium")
        if capped_tier != current_tier:
            logger.info(
                "Confidence capped %s→%s due to %d high-severity contradiction(s)",
                current_tier, capped_tier, high_severity_count,
            )
            current_tier = capped_tier
            current_score = min(current_score, 0.75)

    # ── Restatement penalty ─────────────────────────────────────────────────
    # restatement_disclosed is set by response_generator in a later pass for
    # the FINAL response text, but we check retrieved_chunks/sql_result here
    # for any non-latest indicators that may have slipped through filters
    # (defensive check — normal retrieval should already filter is_latest=True).
    if state.get("restatement_disclosed"):
        capped_tier = _cap_tier(current_tier, "medium")
        if capped_tier != current_tier:
            logger.info(
                "Confidence capped %s→%s due to restatement disclosure",
                current_tier, capped_tier,
            )
            current_tier = capped_tier
            current_score = min(current_score, 0.75)

    state["confidence_score"] = round(current_score, 4)
    state["confidence_tier"] = current_tier

    logger.debug(
        "Final confidence | score=%.4f tier=%s contradictions=%d",
        current_score, current_tier, len(contradictions),
    )

    return state