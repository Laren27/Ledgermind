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

# Local ONNX CrossEncoder (ms-marco-MiniLM-L-6-v2) — raw logit scale, roughly -12 to +2
LOCAL_HIGH_CONFIDENCE_THRESHOLD   = -4.5
LOCAL_MEDIUM_CONFIDENCE_THRESHOLD = -7.5

# Cohere Rerank API (rerank-english-v3.0) — relevance_score scale, 0.0 to 1.0
# CALIBRATED 2026-07-27: validated against real production scores logged across
# all 83 golden-dataset questions (COHERE_CALIBRATION debug logging, since removed).
# Every "high" result scored >=0.88; the one genuine "medium" (Q031, ambiguous
# cross-period question) scored 0.4656, correctly below 0.5. No query in this
# run fell between 0.15-0.5 or below 0.15, so the MEDIUM/LOW boundary itself
# remains unstressed by real data — revisit if a future query's tier looks wrong
# given its logged score. 0.5/0.15 held up against everything checked; keeping.
COHERE_HIGH_CONFIDENCE_THRESHOLD   = 0.5
COHERE_MEDIUM_CONFIDENCE_THRESHOLD = 0.15
# Below the relevant MEDIUM threshold → LOW → refuse

# Bug history: prior to this fix, a single fixed threshold pair (-4.5 / -7.5,
# calibrated for the LOCAL reranker's logit scale) was applied to scores from
# EITHER backend. Cohere's 0-1 relevance_score is always >= -4.5, so any query
# that got Cohere-scored was silently classified HIGH confidence regardless of
# actual relevance — while the same query hitting the local fallback (e.g. on
# a Cohere API hiccup) was scored correctly. This produced confidence_tier
# results that changed run-to-run for the same query, depending purely on
# which reranker backend happened to serve that request.

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
    backend = chunks[0].get("reranker_backend", "local")  # default to stricter/local scale if untagged

    # Backend-specific scale: Cohere returns 0-1 relevance_score; local
    # CrossEncoder returns raw logits (~-12 weak to ~-2 strong). Using the
    # wrong scale's thresholds silently misclassifies confidence — see
    # bug history note above the threshold constants.
    if backend == "cohere":
        high_threshold = COHERE_HIGH_CONFIDENCE_THRESHOLD
        medium_threshold = COHERE_MEDIUM_CONFIDENCE_THRESHOLD
        EMPIRICAL_MIN = 0.0
        EMPIRICAL_MAX = 1.0
    else:
        high_threshold = LOCAL_HIGH_CONFIDENCE_THRESHOLD
        medium_threshold = LOCAL_MEDIUM_CONFIDENCE_THRESHOLD
        EMPIRICAL_MIN = -12.0
        EMPIRICAL_MAX = -2.0

    normalised = (top_score - EMPIRICAL_MIN) / (EMPIRICAL_MAX - EMPIRICAL_MIN)
    normalised = max(0.0, min(1.0, normalised))   # clamp to [0, 1]

    # Gap bonus: if top chunk is clearly separated from the rest, add small boost
    gap = abs(top_score - bottom_score) if len(chunks) > 1 else 0.0
    gap_bonus = min(0.05, gap * 0.005)   # max 5% bonus, keeps tier decisions clean
    final_score = min(1.0, normalised + gap_bonus)

    # Tier decision based on raw top score (not normalised), using the
    # threshold pair that matches this chunk's actual scoring backend
    if top_score >= high_threshold:
        tier = "high"
    elif top_score >= medium_threshold:
        tier = "medium"
    else:
        tier = "low"

    logger.debug(
        "Confidence: backend=%s top_score=%.4f gap=%.4f normalised=%.4f tier=%s",
        backend, top_score, gap, final_score, tier,
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