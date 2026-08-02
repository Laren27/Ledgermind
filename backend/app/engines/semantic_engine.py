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

# Citation relevance floor — DISPLAY LAYER ONLY, Cohere scale only.
#
# A citation is a CLAIM that a passage supports the answer. Chunks scoring
# 0.02-0.03 were being rendered as numbered footnotes with the same visual
# weight as a 1.00 match, which is a Zero UI-Hallucination Mandate violation:
# the evidence list asserts support that the score says is not there.
#
# MEASURED 2026-08-02 across 4 live semantic_risk queries, 20 real citations.
# Sorted, the scores fall in two clusters with NOTHING between them:
#     noise    0.0027 0.0029 0.0065 0.0096 0.0181 0.0234 0.0290
#     genuine  0.0883 0.0948 0.4502 0.8538 0.8604 ... 0.9996
# 0.05 sits in a ~3x-wide empty band, so this is not a tuned constant --
# anything in 0.03-0.08 yields identical results on this evidence. Consistent
# with the 2026-08-01 Cohere dump, where no 'poor' query exceeded 0.0323.
#
# DOES NOT TOUCH retrieved_chunks. A weak chunk in Gemini's context is
# harmless and occasionally useful; the defect is presenting it as evidence.
# Filtering retrieval instead would change what the model sees on every
# semantic and cross query and put the eval baseline at risk for no gain.
#
# DOES NOT TOUCH CONFIDENCE. _score_confidence() reads chunks[0] and
# chunks[-1] and runs BEFORE _build_citations() at every call site, so the
# tier cannot move as a side effect of a display filter. Verified against the
# node body -- if that ordering ever changes, this guarantee changes with it.
#
# COHERE ONLY. Local ONNX returns raw logits (thresholds -4.5/-7.5) where
# 0.05 sits ABOVE nearly every legitimate score and would drop everything.
# One threshold across two scales is the bug that made every Cohere-scored
# query read HIGH. No logit-scale floor exists because none has been
# measured; inventing one for symmetry is the unmeasured-constant habit this
# project has already paid for. Local is a fallback that only runs when
# Cohere fails -- it can wait for its own measurement.
CITATION_RELEVANCE_FLOOR = 0.05

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

    # Unscored chunks (reranker_backend="none", reranker_score=-inf) mean the
    # rerank step was skipped entirely. Comparing -inf against ANY threshold
    # yields "low", so this would silently look like a legitimate refusal
    # rather than a broken pipeline. Fail loudly instead -- this is a code
    # defect, not a retrieval outcome.
    if chunks[0].get("reranker_backend") == "none" or top_score == float("-inf"):
        logger.error(
            "Unscored chunks reached _score_confidence (backend=%s score=%s) — "
            "rerank() did not run. This is a bug, not a low-confidence result.",
            chunks[0].get("reranker_backend"), top_score,
        )
        return 0.0, "low"
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

def _apply_citation_floor(chunks: List[ChunkResult]) -> List[ChunkResult]:
    """Drop Cohere-scored chunks below CITATION_RELEVANCE_FLOOR.

    NEVER returns an empty list. If every chunk falls below the floor, the
    top-scoring one is kept: an answer with zero citations violates
    Principle 2 (every answer traceable) outright, and one weak citation is
    strictly better than none. The floor removes noise from a list; it must
    not be able to empty it.

    Input is assumed sorted by reranker_score descending -- rerank() sorts
    before returning at both backends, so chunks[0] is the best.
    """
    if not chunks:
        return chunks
    if chunks[0].get("reranker_backend") != "cohere":
        return chunks

    kept = [c for c in chunks if c["reranker_score"] >= CITATION_RELEVANCE_FLOOR]
    if not kept:
        logger.info(
            "Citation floor: all %d chunks below %.2f (top=%.4f) — keeping top only",
            len(chunks), CITATION_RELEVANCE_FLOOR, chunks[0]["reranker_score"],
        )
        return [chunks[0]]

    dropped = [c for c in chunks if c["reranker_score"] < CITATION_RELEVANCE_FLOOR]
    if dropped:
        # Real scores logged so the threshold stays calibratable against
        # evidence rather than becoming folklore.
        logger.info(
            "Citation floor: dropped %d of %d below %.2f | scores=%s pages=%s",
            len(dropped), len(chunks), CITATION_RELEVANCE_FLOOR,
            [round(c["reranker_score"], 4) for c in dropped],
            [c.get("page_number") for c in dropped],
        )
    return kept


def _build_citations(chunks: List[ChunkResult]) -> List[Citation]:
    """
    Convert ChunkResult objects → Citation objects for the response layer.

    Citations are what the UI displays and what gets written to the audit log.
    text_preview is the first 200 chars — enough for a snippet, not the full chunk.
    """
    chunks = _apply_citation_floor(chunks)

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
) -> Optional[List[ChunkResult]]:
    """
    Corrective RAG retry with progressively broader filters.

    Retry 1 (crag_count=1): drop quarter constraint
    Retry 2 (crag_count=2): drop quarter AND fiscal_year constraints

    The most common cause of LOW/MEDIUM retrieval on a small corpus is
    over-specific metadata filters excluding relevant chunks.
    """
    # A retry that drops a filter which was never set re-issues the IDENTICAL
    # query and consumes a retry slot for nothing. Confirmed live 2026-07-29:
    # a query with no period extracted (fiscal_year=None, quarter=None) ran
    # three retrievals returning byte-identical reranker scores
    # (0.1364/0.0633) before refusing. Signal "nothing to broaden" with None so
    # the caller can stop rather than burn latency on a guaranteed no-op.
    if crag_count == 1 and quarter is None:
        logger.info("CRAG retry 1 skipped: quarter filter was already unset")
        return None
    if crag_count == 2 and fiscal_year is None:
        logger.info("CRAG retry 2 skipped: fiscal_year filter was already unset")
        return None

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

        broadened = _broaden_retrieval(
            query=query,
            tenant_id=tenant_id,
            company=company,
            fiscal_year=fiscal_year,
            quarter=quarter,
            financial_type=financial_type,
            crag_count=crag_count,
        )

        if broadened is None:
            # This RUNG was a no-op (the filter it drops was already unset) —
            # advance to the next rung rather than abandoning the ladder.
            # Original bug: this used `break`, so any query with quarter=None
            # (i.e. every annual query) skipped rung 2 as well, which drops
            # fiscal_year and is real broadening. That silently removed CRAG
            # recovery from most semantic queries. crag_count is the RUNG
            # INDEX reached, not the number of retrievals actually performed.
            logger.info(
                "CRAG rung %d was a no-op — advancing to next rung", crag_count
            )
            continue

        chunks = broadened
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