"""
LedgerMind — Phase 4: Query State
==================================
Defines the single shared state object that flows through every LangGraph node.
"""

import time
from typing import Any, Dict, List, Literal, Optional, TypedDict


class ChunkResult(TypedDict):
    chunk_id: str
    doc_id: str
    text: str
    page_number: int
    company: str
    fiscal_year: str
    quarter: Optional[str]
    financial_type: str
    chunk_type: str
    filing_date: str
    dense_score: float
    sparse_score: float
    rrf_score: float
    reranker_score: float


class Citation(TypedDict):
    chunk_id: str
    doc_id: str
    page_number: int
    company: str
    fiscal_year: str
    financial_type: str
    filing_date: str
    reranker_score: float
    text_preview: str


class DSLObject(TypedDict):
    metric: str
    entity: str
    period: str
    fiscal_year: str
    quarter: Optional[str]
    financial_type: str
    operation: str
    comparison_entity: Optional[str]
    comparison_period: Optional[str]


class ContradictionFlag(TypedDict):
    type: str
    qualitative_claim: str
    qualitative_source: str
    quantitative_value: float
    quantitative_metric: str
    delta_pct: Optional[float]
    severity: str


class QueryState(TypedDict):
    # ── Fixed at entry ─────────────────────────────────────────────────────
    query: str
    tenant_id: str
    user_id: str
    request_id: str
    start_time: float

    # ── UI Workflow Overrides & Routing Hints ──────────────────────────────
    execution_context: Optional[Dict[str, Any]]
    preferred_operation: Optional[str]

    # ── prompt_shield output ───────────────────────────────────────────────
    is_blocked: bool
    block_reason: Optional[str]

    # ── entity_resolver output ─────────────────────────────────────────────
    company: Optional[str]
    ticker: Optional[str]
    fiscal_year: Optional[str]
    quarter: Optional[str]
    financial_type: str
    resolved_query: str

    # ── router output ──────────────────────────────────────────────────────
    path: Optional[Literal["semantic", "quantitative", "cross"]]
    route_reason: Optional[str]

    # ── Path 1 (semantic_engine) output ───────────────────────────────────
    retrieved_chunks: List[ChunkResult]
    citations: List[Citation]

    # ── Path 2 (quant_engine) output ──────────────────────────────────────
    dsl_object: Optional[DSLObject]
    dsl_valid: bool
    dsl_attempts: int
    sql_query: Optional[str]
    sql_result: Optional[List[Dict[str, Any]]]
    sql_row_count: int
    sql_verified: bool

    # ── Path 3 (cross_engine) output ──────────────────────────────────────
    contradictions: List[ContradictionFlag]

    # ── Confidence ─────────────────────────────────────────────────────────
    confidence_score: float
    confidence_tier: Literal["high", "medium", "low"]
    crag_triggered: bool
    crag_count: int

    # ── Final response ─────────────────────────────────────────────────────
    response_text: Optional[str]
    restatement_disclosed: bool

    # ── Error handling ─────────────────────────────────────────────────────
    error: Optional[str]
    error_node: Optional[str]

    # ── Audit ──────────────────────────────────────────────────────────────
    tokens_used: int
    cache_hit: bool
    latency_ms: int


def make_initial_state(
    query: str,
    tenant_id: str,
    user_id: str,
    request_id: str,
    execution_context: Optional[Dict[str, Any]] = None,
) -> QueryState:
    """Returns a fully-initialised QueryState with safe, explicit defaults."""
    return QueryState(
        query=query,
        tenant_id=tenant_id,
        user_id=user_id,
        request_id=request_id,
        start_time=time.time(),
        
        execution_context=execution_context,
        preferred_operation=None,

        is_blocked=False,
        block_reason=None,

        company=None,
        ticker=None,
        fiscal_year=None,
        quarter=None,
        financial_type="consolidated",
        resolved_query=query,

        path=None,
        route_reason=None,

        retrieved_chunks=[],
        citations=[],

        dsl_object=None,
        dsl_valid=False,
        dsl_attempts=0,
        sql_query=None,
        sql_result=None,
        sql_row_count=0,
        sql_verified=False,

        contradictions=[],

        confidence_score=0.0,
        confidence_tier="low",
        crag_triggered=False,
        crag_count=0,

        response_text=None,
        restatement_disclosed=False,

        error=None,
        error_node=None,

        tokens_used=0,
        cache_hit=False,
        latency_ms=0,
    )