"""
LedgerMind — Phase 4: Semantic Engine (Path 1)
================================================
RAG path for qualitative queries: risks, strategy, governance, management commentary.

Pipeline:
  retrieve_and_rerank() → confidence scoring → CRAG retry (if MEDIUM) → citations

This module does NOT call Gemini. Response generation is handled downstream
by response_generator.py, which receives the retrieved chunks and citations.

CRAG (Corrective RAG) loop:
  - HIGH confidence   → proceed directly
  - MEDIUM confidence → retry with broader filter (drop quarter, then fiscal_year)
  - LOW confidence    → set error, skip response generation, return refusal message

Confidence thresholds are calibrated for ms-marco-MiniLM-L-6-v2 on financial text.
General web text scores higher on this model (~0 to +5); financial domain text
typically scores -3 to -8 even on strong matches. Thresholds reflect this.
"""

import logging
from typing import List, Optional, Tuple

from app.engines.retriever import retrieve_and_rerank
from app.engines.state import ChunkResult, Citation, QueryState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Confidence thresholds
# Calibrated from live test: strong match scored -3.83 on financial text.
# Adjust after Phase 7 eval once golden dataset scores are measured.
# ---------------------------------------------------------------------------

HIGH_CONFIDENCE_THRESHOLD   = -4.5   # top reranker score above this → HIGH
MEDIUM_CONFIDENCE_THRESHOLD = -7.5   # top reranker score above this → MEDIUM
# Below MEDIUM_CONFIDENCE_THRESHOLD → LOW → refuse

MIN_CHUNKS_FOR_ANSWER = 1   # refuse if fewer chunks than this after reranking

# Maximum CRAG retries — blueprint §13 specifies 2
MAX_CRAG_RETRIES = 2


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------

def _score_confidence(chunks: List[ChunkResult]) -> Tuple[float, str]:
    """
    Compute (confidence_score, confidence_tier) from reranked chunks.

    Primary signal: top reranker score (raw CrossEncoder logit).
    Secondary signal: score gap between rank 1 and rank N (spread).
    A large gap means the top result is clearly better than the rest — good signal.
    A tiny gap means results are indistinguishable — weaker signal.

    Returns (normalised_score_0_to_1, tier_string).
    The normalised score is for the audit log — the tier drives routing logic.
    """
    if not chunks:
        return 0.0, "low"

    top_score = chunks[0]["reranker_score"]
    bottom_score = chunks[-1]["reranker_score"]

    # Normalise to [0, 1] using empirical range for financial text
    # Observed range: roughly -12 (weak) to -2 (strong) for this domain
    EMPIRICAL_MIN = -12.0
    EMPIRICAL_MAX = -2.0
    normalised = (top_score - EMPIRICAL_MIN) / (EMPIRICAL_MAX - EMPIRICAL_MIN)
    normalised = max(0.0, min(1.0, normalised))   # clamp to [0, 1]

    # Gap bonus: if top chunk is clearly separated from the rest, add small boost
    gap = abs(top_score - bottom_score) if len(chunks) > 1 else 0.0
    gap_bonus = min(0.05, gap * 0.005)   # max 5% bonus, keeps tier decisions clean
    final_score = min(1.0, normalised + gap_bonus)

    # Tier decision based on raw top score (not normalised)
    if top_score >= HIGH_CONFIDENCE_THRESHOLD:
        tier = "high"
    elif top_score >= MEDIUM_CONFIDENCE_THRESHOLD:
        tier = "medium"
    else:
        tier = "low"

    logger.debug(
        "Confidence: top_score=%.4f gap=%.4f normalised=%.4f tier=%s",
        top_score, gap, final_score, tier,
    )

    return round(final_score, 4), tier


# ---------------------------------------------------------------------------
# Citation builder
# ---------------------------------------------------------------------------

def _build_citations(chunks: List[ChunkResult]) -> List[Citation]:
    """
    Convert ChunkResult objects → Citation objects for the response layer.

    Citations are what the UI displays and what gets written to the audit log.
    text_preview is the first 200 chars — enough for a snippet, not the full chunk.
    """
    citations = []
    for chunk in chunks:
        citation = Citation(
            chunk_id=chunk["chunk_id"],
            doc_id=chunk["doc_id"],
            page_number=chunk["page_number"],
            company=chunk["company"],
            fiscal_year=chunk["fiscal_year"],
            financial_type=chunk["financial_type"],
            filing_date=chunk["filing_date"],
            reranker_score=chunk["reranker_score"],
            text_preview=chunk["text"][:200].strip(),
        )
        citations.append(citation)
    return citations


# ---------------------------------------------------------------------------
# CRAG: query broadening for retry
# ---------------------------------------------------------------------------

def _broaden_retrieval(
    query: str,
    tenant_id: str,
    company: Optional[str],
    fiscal_year: Optional[str],
    quarter: Optional[str],
    financial_type: str,
    crag_count: int,
) -> List[ChunkResult]:
    """
    Corrective RAG retry with progressively broader filters.

    Retry 1 (crag_count=1): drop quarter constraint
    Retry 2 (crag_count=2): drop quarter AND fiscal_year constraints

    The most common cause of LOW/MEDIUM retrieval on a small corpus is
    over-specific metadata filters excluding relevant chunks.
    """
    if crag_count == 1:
        logger.info("CRAG retry 1: dropping quarter filter (was %s)", quarter)
        return retrieve_and_rerank(
            query=query,
            tenant_id=tenant_id,
            company=company,
            fiscal_year=fiscal_year,
            quarter=None,           # drop quarter
            financial_type=financial_type,
        )
    elif crag_count == 2:
        logger.info(
            "CRAG retry 2: dropping quarter + fiscal_year filters (were %s, %s)",
            quarter, fiscal_year,
        )
        return retrieve_and_rerank(
            query=query,
            tenant_id=tenant_id,
            company=company,
            fiscal_year=None,       # drop fiscal_year too
            quarter=None,
            financial_type=financial_type,
        )
    else:
        logger.error("CRAG called with crag_count=%d — max is %d", crag_count, MAX_CRAG_RETRIES)
        return []


# ---------------------------------------------------------------------------
# LangGraph node
# ---------------------------------------------------------------------------

def semantic_engine_node(state: QueryState) -> QueryState:
    """
    Path 1: Semantic retrieval engine.

    Steps:
      1. Initial retrieve_and_rerank with state metadata filters
      2. Score confidence
      3. If LOW → set error, return (response_generator will emit refusal)
      4. If MEDIUM → CRAG retry with broader filter (up to MAX_CRAG_RETRIES)
      5. If HIGH → proceed
      6. Build citations
      7. Write retrieved_chunks, citations, confidence_score, confidence_tier to state
    """
    # Use resolved_query for retrieval (entity-prefixed, better BM25 signal)
    query = state.get("resolved_query") or state["query"]
    tenant_id = state["tenant_id"]
    company = state.get("company")
    fiscal_year = state.get("fiscal_year")
    quarter = state.get("quarter")
    financial_type = state.get("financial_type", "consolidated")

    logger.info(
        "SemanticEngine | company=%s fiscal_year=%s quarter=%s financial_type=%s",
        company, fiscal_year, quarter, financial_type,
    )

    # ── Initial retrieval ──────────────────────────────────────────────────
    chunks = retrieve_and_rerank(
        query=query,
        tenant_id=tenant_id,
        company=company,
        fiscal_year=fiscal_year,
        quarter=quarter,
        financial_type=financial_type,
    )

    confidence_score, confidence_tier = _score_confidence(chunks)
    crag_count = 0

    # ── CRAG loop ──────────────────────────────────────────────────────────
    while confidence_tier in ("low", "medium") and crag_count < MAX_CRAG_RETRIES:
        # LOW on first attempt → always retry
        # MEDIUM → retry once (crag_count=1), then accept and add disclaimer
        if confidence_tier == "medium" and crag_count >= 1:
            # Already retried once for MEDIUM — accept with disclaimer
            logger.info("CRAG: MEDIUM after retry %d — accepting with disclaimer", crag_count)
            break

        crag_count += 1
        state["crag_triggered"] = True
        state["crag_count"] = crag_count

        chunks = _broaden_retrieval(
            query=query,
            tenant_id=tenant_id,
            company=company,
            fiscal_year=fiscal_year,
            quarter=quarter,
            financial_type=financial_type,
            crag_count=crag_count,
        )

        new_score, new_tier = _score_confidence(chunks)
        logger.info(
            "CRAG retry %d: score %.4f→%.4f tier %s→%s",
            crag_count, confidence_score, new_score, confidence_tier, new_tier,
        )
        confidence_score, confidence_tier = new_score, new_tier

    # ── LOW confidence after all retries → refuse ──────────────────────────
    if confidence_tier == "low" or len(chunks) < MIN_CHUNKS_FOR_ANSWER:
        logger.warning(
            "SemanticEngine: LOW confidence after %d CRAG retries — refusing query",
            crag_count,
        )
        state["confidence_score"] = confidence_score
        state["confidence_tier"] = "low"
        state["retrieved_chunks"] = []
        state["citations"] = []
        state["response_text"] = (
            "Insufficient information found in the available documents for this query. "
            "The corpus may not contain this company, period, or topic yet. "
            "Please verify the company and fiscal year exist in the indexed documents, "
            "or rephrase your question."
        )
        state["error"] = "low_confidence_refusal"
        state["error_node"] = "semantic_engine"
        return state

    # ── Build citations and update state ───────────────────────────────────
    citations = _build_citations(chunks)

    state["retrieved_chunks"]  = list(chunks)
    state["citations"]         = citations
    state["confidence_score"]  = confidence_score
    state["confidence_tier"]   = confidence_tier
    state["crag_count"]        = crag_count

    logger.info(
        "SemanticEngine complete | chunks=%d confidence=%.4f tier=%s crag_retries=%d",
        len(chunks), confidence_score, confidence_tier, crag_count,
    )

    return state