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
import re
from typing import Optional

from app.engines.contradiction import detect_contradictions
from app.engines.quant_engine import quant_engine_node
from app.engines.semantic_engine import semantic_engine_node
from app.engines.state import QueryState
from app.metrics.registry import metric_anchor_phrases

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


def resolve_parent_entities(entities: list) -> list:
    """
    F14: the list form. Maps every named issuer through the subsidiary table,
    preserving order and dropping duplicates -- two subsidiaries of one parent
    must not produce that parent twice in an any-of filter.

    The scalar version above is kept and unchanged: it is the single-entity
    contract the rest of this module's DSL handling still speaks.
    """
    out = []
    for e in entities or []:
        r = resolve_parent_entity(e)
        if r is not None and r not in out:
            out.append(r)
    return out



# ---------------------------------------------------------------------------
# Stage 0c — no-metric-anchor guard
# ---------------------------------------------------------------------------
# GeminiDSLResponse.metric is a REQUIRED field. A cross-routed query that
# names no metric therefore cannot produce "no metric" -- the model invents
# one, it compiles, it executes, and it is appended with sql_verified=True.
# See registry.metric_anchor_phrases() for the measured case (PQ012).
#
# Sibling of quant_engine's Stage 0 / Stage 0b, same discipline: deterministic
# regex over the RAW query, before any LLM call, because the raw query is the
# only place the user's actual intent still exists.
#
# SCOPED TO THE CROSS PATH BY PLACEMENT, NOT BY A CONDITIONAL. On path=
# quantitative the router has already asserted the user wants a number, and
# refusing there would risk legitimate queries phrased outside registry
# vocabulary. Here the quant half is an ADJUNCT to a qualitative answer, so
# suppressing it degrades to qualitative-only -- a case _reconcile_cross
# already handles as Quadrant 3. Living in this module means Path 2 is
# untouched by construction rather than by a check someone could later move.
#
# Word boundaries are (?<!\w)...(?!\w), NOT \b: many phrases end or begin
# with non-word characters ("d&a", "impairment of loans/investment in
# associates") where \b asserts the opposite of what is wanted.

_ANCHOR_RE = re.compile(
    "|".join(
        rf"(?<!\w){re.escape(p)}(?!\w)"
        for p in sorted(metric_anchor_phrases(), key=len, reverse=True)
    ),
    re.IGNORECASE,
)


def _query_lacks_metric_anchor(query: str) -> bool:
    """True if the raw query names no known metric in any vocabulary."""
    return not _ANCHOR_RE.search(query or "")


# ---------------------------------------------------------------------------
# LangGraph node
# ---------------------------------------------------------------------------

def cross_engine_node(state: QueryState) -> QueryState:
    """
    Path 3: Cross-examination engine.

    Steps:
      1. Resolve subsidiary entity to parent (if applicable) for sub-engine calls
      2. Run quant_engine logic (quantitative SQL) FIRST — only if a metric is
         identifiable; cross-examination queries don't always have one
      3. Run semantic_engine logic (qualitative retrieval), so the verified
         figure is available to the synthesis call downstream
      4. Run contradiction detection on the combined output
      5. Merge state from both sub-engines + contradictions

    AUTHORITY: the confidence_tier / error values set here are an INPUT to
    response_generator._reconcile_cross(), which is the final word for this
    path. Step 4's error-clearing below runs BEFORE response_generator and
    was previously undone by it. Do not re-implement reconciliation here.
    """
    original_entities = state.get("companies") or []
    resolved_entities = resolve_parent_entities(original_entities)

    if resolved_entities != original_entities:
        logger.info(
            "CrossEngine: subsidiaries %s resolved to parents %s for retrieval/SQL",
            original_entities, resolved_entities,
        )

    # ── Step 1: Run quant engine with resolved entity ──────────────────────
    # QUANT RUNS FIRST — deliberate, and the fix for the cross-path
    # self-contradiction. Previously semantic ran first and the two halves
    # were assembled independently, so the semantic half wrote its answer
    # from narrative chunks alone. Asked "does commentary align with FY26
    # PAT?", it correctly reported that the excerpts contain no PAT figure —
    # a true statement about ITS context window — and the quant template then
    # appended "PAT was ₹366 Cr" directly underneath it.
    #
    # Two earlier attempts failed because both tried to SUPPRESS that
    # sentence (post-hoc rewriting in response_generator, then a prompt
    # instruction not to say it). Neither worked, because the model was being
    # asked to withhold something true about the evidence it was given. The
    # working fix is to make it false: run quant first and inject the
    # verified figure into the synthesis context as an established fact, so
    # the model writes one coherent answer with nothing left to contradict.
    #
    # Safe to reorder: quant_engine reads only the DSL-relevant state fields
    # (company / fiscal_year / quarter / financial_type / query) and never
    # touches retrieved_chunks or citations. The two sub-engines are genuinely
    # independent; only the ASSEMBLY was coupled.
    #
    # Cross-examination queries don't always map cleanly to a DSL metric.
    # quant_engine_node already handles "could not interpret" gracefully via
    # its own error path — we treat that as "no quantitative side available"
    # rather than a hard failure for the whole cross-examination.
    # Stage 0c: no metric named => nothing for the quant half to verify.
    # quant_result stays {} so the dsl_object copy below yields None, which is
    # what tells _reconcile_cross this query never asked for a figure (say
    # nothing) rather than that a metric was identified but unverifiable
    # (disclose the gap). Emitting CROSS_NO_VERIFIED_FIGURE_NOTE here would be
    # false: no metric was identified.
    if _query_lacks_metric_anchor(state["query"]):
        logger.info(
            "CrossEngine Stage 0c: query names no known metric — skipping "
            "quant half, qualitative-only result"
        )
        quant_result: dict = {}
        quant_succeeded = False
    else:
        quant_state = dict(state)
        quant_state["companies"] = resolved_entities
        quant_result = quant_engine_node(QueryState(**quant_state))
        quant_succeeded = quant_result.get("error") is None and quant_result.get("sql_verified")

    # Copied UNCONDITIONALLY. dsl_object presence is how response_generator's
    # cross reconciliation tells "a metric was identified but produced no
    # verified figure" (a real gap worth disclosing) apart from "this query
    # never asked for a figure" (nothing to disclose — emitting a gap note
    # there is noise). Copying it only on success made the two cases
    # indistinguishable downstream. .get() because quant_engine can fail
    # before a DSL object exists at all.
    state["dsl_object"] = quant_result.get("dsl_object")

    if quant_succeeded:
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

    # ── Step 2: Run semantic engine with resolved entity ───────────────────
    # Runs AFTER quant so response_generator can hand the verified figure to
    # the synthesis call. Temporarily substitute resolved entity, restore after.
    semantic_state = dict(state)
    semantic_state["companies"] = resolved_entities
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