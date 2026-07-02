"""
LedgerMind — Phase 4: Cross-Examination Engine (Path 3)
==========================================================
Hybrid verification path: runs Path 1 (qualitative) and Path 2 (quantitative)
against the same query context, then checks for contradictions.

Used for queries like:
  "Does management's commentary on Blinkit align with consolidated revenue?"
  "Is what the CEO said about profitability consistent with actual numbers?"

Subsidiary mapping fix:
  The router may extract a subsidiary name (BLINKIT) as the entity when the
  user mentions it, but Blinkit's data lives inside ETERNAL's consolidated
  filing — there is no standalone BLINKIT document in the corpus. Before
  calling semantic_engine or quant_engine, subsidiary tickers are mapped to
  their parent entity for retrieval/SQL purposes. The original subsidiary
  name is preserved for response generation (so the answer still says
  "Blinkit" where appropriate).

This module reuses semantic_engine_node and quant_engine_node directly
rather than duplicating their logic — DRY principle, and any fix to those
modules automatically benefits Path 3.
"""

import logging
from typing import Optional

from app.engines.contradiction import detect_contradictions
from app.engines.quant_engine import quant_engine_node
from app.engines.semantic_engine import semantic_engine_node
from app.engines.state import QueryState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Subsidiary → parent entity mapping
# Expand as corpus grows. Only entities with NO standalone filing of their
# own belong here — they must be resolved to the parent for SQL/retrieval.
# ---------------------------------------------------------------------------

SUBSIDIARY_TO_PARENT = {
    "BLINKIT": "ETERNAL",
    "HYPERPURE": "ETERNAL",
    # Add as corpus expands: e.g. future subsidiaries of Paytm, Nykaa, etc.
}


def resolve_parent_entity(entity: Optional[str]) -> Optional[str]:
    """
    Map a subsidiary ticker to its parent entity for retrieval/SQL purposes.
    Returns the entity unchanged if it's not a known subsidiary.
    """
    if entity is None:
        return None
    return SUBSIDIARY_TO_PARENT.get(entity, entity)


# ---------------------------------------------------------------------------
# LangGraph node
# ---------------------------------------------------------------------------

def cross_engine_node(state: QueryState) -> QueryState:
    """
    Path 3: Cross-examination engine.

    Steps:
      1. Resolve subsidiary entity to parent (if applicable) for sub-engine calls
      2. Run semantic_engine logic (qualitative retrieval)
      3. Run quant_engine logic (quantitative SQL) — only if a metric is
         identifiable; cross-examination queries don't always have one
      4. Run contradiction detection on the combined output
      5. Merge state from both sub-engines + contradictions
    """
    original_entity = state.get("company")
    resolved_entity = resolve_parent_entity(original_entity)

    if resolved_entity != original_entity:
        logger.info(
            "CrossEngine: subsidiary '%s' resolved to parent '%s' for retrieval/SQL",
            original_entity, resolved_entity,
        )

    # ── Step 1: Run semantic engine with resolved entity ───────────────────
    # Temporarily substitute resolved entity for the sub-call, restore after.
    semantic_state = dict(state)
    semantic_state["company"] = resolved_entity
    semantic_result = semantic_engine_node(QueryState(**semantic_state))

    # Pull qualitative results back into main state
    state["retrieved_chunks"] = semantic_result["retrieved_chunks"]
    state["citations"]        = semantic_result["citations"]
    qual_confidence_score     = semantic_result["confidence_score"]
    qual_confidence_tier      = semantic_result["confidence_tier"]
    state["crag_triggered"]   = semantic_result["crag_triggered"]
    state["crag_count"]       = semantic_result["crag_count"]

    if semantic_result.get("error") == "low_confidence_refusal":
        logger.warning("CrossEngine: semantic side returned low confidence")
        # Don't hard-fail yet — quant side might still produce a usable answer.
        # contradiction detection will simply skip if no chunks available.

    # ── Step 2: Run quant engine with resolved entity ───────────────────────
    # Cross-examination queries don't always map cleanly to a DSL metric.
    # quant_engine_node already handles "could not interpret" gracefully via
    # its own error path — we treat that as "no quantitative side available"
    # rather than a hard failure for the whole cross-examination.
    quant_state = dict(state)
    quant_state["company"] = resolved_entity
    quant_result = quant_engine_node(QueryState(**quant_state))

    quant_succeeded = quant_result.get("error") is None and quant_result.get("sql_verified")

    if quant_succeeded:
        state["dsl_object"]    = quant_result["dsl_object"]
        state["sql_query"]     = quant_result["sql_query"]
        state["sql_result"]    = quant_result["sql_result"]
        state["sql_row_count"] = quant_result["sql_row_count"]
        state["sql_verified"]  = True
    else:
        logger.info(
            "CrossEngine: quant side unavailable (%s) — proceeding with qualitative-only result",
            quant_result.get("error"),
        )
        state["sql_verified"] = False

    # ── Step 3: Contradiction detection ─────────────────────────────────────
    sql_value: Optional[float] = None
    yoy_pct: Optional[float] = None
    metric_label = ""

    if quant_succeeded and state["sql_result"]:
        result_row = state["sql_result"][0]
        # point_in_time result has 'value'; yoy_growth result has 'yoy_pct' and 'current_value'
        if "value" in result_row:
            sql_value = float(result_row["value"])
            metric_label = result_row.get("metric", "")
        elif "yoy_pct" in result_row:
            yoy_pct = result_row.get("yoy_pct")
            sql_value = result_row.get("current_value")
            metric_label = result_row.get("metric", "")

    contradictions = []
    if state["retrieved_chunks"] and (sql_value is not None or yoy_pct is not None):
        contradictions = detect_contradictions(
            chunks=state["retrieved_chunks"],
            sql_value=sql_value,
            sql_metric=metric_label,
            yoy_pct=yoy_pct,
        )
    else:
        logger.info(
            "CrossEngine: skipping contradiction detection — "
            "insufficient data (chunks=%d, sql_value=%s, yoy_pct=%s)",
            len(state["retrieved_chunks"]), sql_value, yoy_pct,
        )

    state["contradictions"] = contradictions

    # ── Step 4: Combined confidence ─────────────────────────────────────────
    # Cross-examination confidence reflects the WEAKER of the two sides —
    # a strong qualitative answer paired with a failed quant lookup is still
    # only as trustworthy as its weakest link.
    if quant_succeeded:
        combined_score = min(qual_confidence_score, 1.0)
        combined_tier = qual_confidence_tier  # quant side is always "high" when verified
    else:
        # Quant unavailable — fall back entirely to qualitative confidence,
        # but cap at medium since cross-examination promised both sides.
        combined_score = min(qual_confidence_score, 0.75)
        combined_tier = "medium" if qual_confidence_tier == "high" else qual_confidence_tier

    state["confidence_score"] = combined_score
    state["confidence_tier"]  = combined_tier

    # ── Clear any error set by sub-engines — cross_engine itself succeeded
    # in producing a result even if one side was partial ──────────────────
    if state["retrieved_chunks"] or quant_succeeded:
        state["error"] = None
        state["error_node"] = None

    logger.info(
        "CrossEngine complete | chunks=%d quant_available=%s contradictions=%d "
        "confidence=%.2f tier=%s",
        len(state["retrieved_chunks"]), quant_succeeded, len(contradictions),
        combined_score, combined_tier,
    )

    return state