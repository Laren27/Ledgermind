"""
LedgerMind — Phase 4: Query State
==================================
Defines the single shared state object that flows through every LangGraph node.

Design rules:
  - Every field must have a clear owner (which node writes it).
  - Optional fields are None until their owner node runs.
  - No node reads a field it is responsible for writing (except to check defaults).
  - Fields are typed strictly — no plain `Any` unless unavoidable.

Node ownership map:
  prompt_shield       → is_blocked, block_reason
  entity_resolver     → company, ticker, fiscal_year, quarter, financial_type
  router              → path, route_reason
  semantic_engine     → retrieved_chunks, citations
  quant_engine        → dsl_object, dsl_valid, sql_query, sql_result, sql_row_count, sql_verified
  cross_engine        → contradictions (also populates retrieved_chunks + sql_result via sub-calls)
  confidence          → confidence_score, confidence_tier, crag_triggered, crag_count
  response_generator  → response_text
  audit_writer        → latency_ms (finalizes), writes audit_log row
"""

import time
from typing import Any, Dict, List, Literal, Optional, TypedDict


# ---------------------------------------------------------------------------
# Sub-types (dicts, not dataclasses — LangGraph serialises state as plain dicts)
# ---------------------------------------------------------------------------

class ChunkResult(TypedDict):
    """A single retrieved chunk with all scores attached."""
    chunk_id: str
    doc_id: str
    text: str
    page_number: int
    company: str
    fiscal_year: str
    quarter: Optional[str]
    financial_type: str        # "consolidated" | "standalone"
    chunk_type: str            # "TEXT" | "TABLE" | "RISK_DISCLOSURE" etc.
    filing_date: str           # ISO date string "YYYY-MM-DD"
    dense_score: float         # cosine similarity from dense vector
    sparse_score: float        # BM25 score (0 if sparse not available)
    rrf_score: float           # reciprocal rank fusion score
    reranker_score: float      # cross-encoder score (set after reranking, -inf before)


class Citation(TypedDict):
    """User-facing citation attached to the final response."""
    chunk_id: str
    doc_id: str
    page_number: int
    company: str
    fiscal_year: str
    financial_type: str
    filing_date: str
    reranker_score: float
    text_preview: str          # first 200 chars of chunk text


class DSLObject(TypedDict):
    """
    Controlled DSL produced by the router LLM call for Path 2.
    The SQL Compiler only ever reads from this — never from raw query text.
    """
    metric: str                # must be in METRIC_REGISTRY
    entity: str                # normalised ticker e.g. "ETERNAL"
    period: str                # "FY26" | "Q4FY26" etc.
    fiscal_year: str           # "FY26"
    quarter: Optional[str]     # "Q4" | None for annual
    financial_type: str        # "consolidated" | "standalone"
    operation: str             # must be in OPERATION_REGISTRY
    comparison_entity: Optional[str]   # for comparison operations
    comparison_period: Optional[str]   # for temporal comparison


class ContradictionFlag(TypedDict):
    """A detected disagreement between qualitative and quantitative sources."""
    type: str                  # "direction" | "magnitude" | "existence"
    qualitative_claim: str     # text excerpt from chunk
    qualitative_source: str    # chunk_id
    quantitative_value: float  # SQL result
    quantitative_metric: str
    delta_pct: Optional[float] # % difference if numeric comparison
    severity: str              # "high" | "medium" | "low"


# ---------------------------------------------------------------------------
# Root QueryState
# ---------------------------------------------------------------------------

class QueryState(TypedDict):
    """
    Complete state for a single query lifecycle.
    Instantiated at the API entry point, passed through every LangGraph node.
    """

    # ── Fixed at entry (never mutated after creation) ──────────────────────
    query: str                 # raw user query text
    tenant_id: str             # UUID string (not UUID object — JSON serialisable)
    user_id: str               # UUID string
    request_id: str            # UUID string for this specific query, used in audit
    start_time: float          # time.time() captured at API entry

    # ── prompt_shield output ───────────────────────────────────────────────
    is_blocked: bool           # True → terminate immediately, skip all engines
    block_reason: Optional[str]  # human-readable reason for the block

    # ── entity_resolver output ─────────────────────────────────────────────
    # Reuses the entity_resolver module already built in Phase 3.
    company: Optional[str]     # normalised ticker e.g. "ETERNAL"
    ticker: Optional[str]      # same as company for now; kept separate for future
    fiscal_year: Optional[str] # "FY26" | None if not detected
    quarter: Optional[str]     # "Q4" | None for annual or not detected
    financial_type: str        # "consolidated" (default) | "standalone"
    resolved_query: str        # query rewritten with normalised entities (for retrieval)

    # ── router output ──────────────────────────────────────────────────────
    path: Optional[Literal["semantic", "quantitative", "cross"]]
    route_reason: Optional[str]  # why this path was chosen (for audit/debug)

    # ── Path 1 (semantic_engine) output ───────────────────────────────────
    retrieved_chunks: List[ChunkResult]   # top-k after reranking
    citations: List[Citation]             # citation objects for response

    # ── Path 2 (quant_engine) output ──────────────────────────────────────
    dsl_object: Optional[DSLObject]  # None if path != quantitative/cross
    dsl_valid: bool                  # True if DSL passed validator
    dsl_attempts: int                # number of DSL generation attempts (max 2)
    sql_query: Optional[str]         # compiled SQL (for audit)
    sql_result: Optional[List[Dict[str, Any]]]  # rows returned from PostgreSQL
    sql_row_count: int               # 0 triggers self-healing; >1 triggers warning
    sql_verified: bool               # True if arithmetic check passed

    # ── Path 3 (cross_engine) output ──────────────────────────────────────
    contradictions: List[ContradictionFlag]

    # ── Confidence (set by confidence module, read by response_generator) ──
    confidence_score: float          # 0.0 – 1.0
    confidence_tier: Literal["high", "medium", "low"]
    crag_triggered: bool             # True if corrective RAG ran
    crag_count: int                  # 0 | 1 | 2 (max retries before hard-stop)

    # ── Final response ─────────────────────────────────────────────────────
    response_text: Optional[str]     # fully assembled response, ready to return
    restatement_disclosed: bool      # True if response notes a restatement

    # ── Error handling ─────────────────────────────────────────────────────
    error: Optional[str]             # set if any node raises an unrecoverable error
    error_node: Optional[str]        # which node raised the error

    # ── Audit (finalised by audit_writer) ──────────────────────────────────
    tokens_used: int                 # cumulative across all LLM calls in this query
    cache_hit: bool                  # True if Redis cache served this (Phase 5)
    latency_ms: int                  # computed at audit_writer from start_time


# ---------------------------------------------------------------------------
# Factory: create a blank state with correct defaults
# ---------------------------------------------------------------------------

def make_initial_state(
    query: str,
    tenant_id: str,
    user_id: str,
    request_id: str,
) -> QueryState:
    """
    Returns a fully-initialised QueryState with safe defaults.
    Every field is explicitly set so no node ever sees a missing key.

    Called once at the FastAPI endpoint before the graph is invoked.
    """
    return QueryState(
        # Entry
        query=query,
        tenant_id=tenant_id,
        user_id=user_id,
        request_id=request_id,
        start_time=time.time(),

        # Prompt shield (assumed clean until proven otherwise)
        is_blocked=False,
        block_reason=None,

        # Entity resolution
        company=None,
        ticker=None,
        fiscal_year=None,
        quarter=None,
        financial_type="consolidated",   # default per blueprint §4.1
        resolved_query=query,            # overwritten by entity_resolver

        # Routing
        path=None,
        route_reason=None,

        # Path 1
        retrieved_chunks=[],
        citations=[],

        # Path 2
        dsl_object=None,
        dsl_valid=False,
        dsl_attempts=0,
        sql_query=None,
        sql_result=None,
        sql_row_count=0,
        sql_verified=False,

        # Path 3
        contradictions=[],

        # Confidence
        confidence_score=0.0,
        confidence_tier="low",
        crag_triggered=False,
        crag_count=0,

        # Response
        response_text=None,
        restatement_disclosed=False,

        # Errors
        error=None,
        error_node=None,

        # Audit
        tokens_used=0,
        cache_hit=False,
        latency_ms=0,
    )