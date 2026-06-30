"""
LedgerMind — Phase 4: Query API Endpoint
============================================
The single FastAPI route that exposes the compiled LangGraph engine.

Request body is intentionally minimal — tenant_id/user_id are plain fields
for now because JWT auth (Phase 5) doesn't exist yet. Once Phase 5 lands,
these will be extracted from the JWT instead of trusted from the request
body directly; this endpoint signature will need a follow-up pass then.

Response body returns the FULL QueryState (not a trimmed subset). Phase 6's
Streamlit UI needs citations, contradictions, dsl_object, and confidence
all available — trimming now just means rebuilding this schema later.

graph.invoke() is synchronous (LangGraph nodes use psycopg2 sync calls and
blocking Gemini SDK calls, not asyncpg). The endpoint itself is async def
so FastAPI's event loop isn't blocked during the 2-30 second round trip —
run_in_threadpool offloads the blocking call to a worker thread.
"""

import logging
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.engines.graph import get_graph
from app.engines.state import make_initial_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["query"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="The user's natural language question")
    tenant_id: str = Field(..., description="Tenant UUID — temporary plain field until Phase 5 JWT auth")
    user_id: str = Field(..., description="User UUID — temporary plain field until Phase 5 JWT auth")


class CitationResponse(BaseModel):
    chunk_id: str
    doc_id: str
    page_number: int
    company: str
    fiscal_year: str
    financial_type: str
    filing_date: str
    reranker_score: float
    text_preview: str


class ContradictionResponse(BaseModel):
    type: str
    qualitative_claim: str
    qualitative_source: str
    quantitative_value: float
    quantitative_metric: str
    delta_pct: float | None
    severity: str


class QueryResponse(BaseModel):
    """
    Full QueryState surfaced to the client. Mirrors the internal state
    shape closely so Phase 6 Streamlit can consume citations/contradictions
    without a second round of schema design.
    """
    request_id: str
    query: str
    path: str | None
    is_blocked: bool
    block_reason: str | None

    company: str | None
    fiscal_year: str | None
    quarter: str | None
    financial_type: str

    response_text: str | None
    confidence_score: float
    confidence_tier: str
    crag_triggered: bool
    crag_count: int

    citations: list[CitationResponse]
    contradictions: list[ContradictionResponse]

    dsl_object: dict | None
    sql_query: str | None
    sql_result: list[dict] | None
    sql_verified: bool

    error: str | None
    error_node: str | None

    latency_ms: int
    tokens_used: int
    cache_hit: bool


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/query", response_model=QueryResponse)
async def query_endpoint(request: QueryRequest) -> QueryResponse:
    """
    Single entry point for all LedgerMind queries.

    Builds initial state → invokes the compiled graph in a thread pool →
    returns the full final state as the response.

    Does not raise HTTPException for query-level failures (blocked queries,
    low confidence refusals, DSL/SQL failures) — those are valid, well-formed
    responses with error fields populated, not HTTP errors. HTTPException is
    reserved for actual infrastructure failures (e.g. graph invocation itself
    throwing an unhandled exception, which audit_writer's try/except should
    already prevent in most cases, but this is the outer safety net).
    """
    request_id = str(uuid.uuid4())

    logger.info(
        "Query received | request_id=%s tenant_id=%s query='%s'",
        request_id, request.tenant_id, request.query[:80],
    )

    state = make_initial_state(
        query=request.query,
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        request_id=request_id,
    )

    try:
        graph = get_graph()
        result = await run_in_threadpool(graph.invoke, state)
    except Exception as e:
        logger.error("Graph invocation failed unexpectedly | request_id=%s error=%s", request_id, e)
        raise HTTPException(
            status_code=500,
            detail="An internal error occurred while processing your query. Please try again.",
        )

    return QueryResponse(
        request_id=result["request_id"],
        query=result["query"],
        path=result.get("path"),
        is_blocked=result["is_blocked"],
        block_reason=result.get("block_reason"),
        company=result.get("company"),
        fiscal_year=result.get("fiscal_year"),
        quarter=result.get("quarter"),
        financial_type=result.get("financial_type", "consolidated"),
        response_text=result.get("response_text"),
        confidence_score=result.get("confidence_score", 0.0),
        confidence_tier=result.get("confidence_tier", "low"),
        crag_triggered=result.get("crag_triggered", False),
        crag_count=result.get("crag_count", 0),
        citations=result.get("citations", []),
        contradictions=result.get("contradictions", []),
        dsl_object=dict(result["dsl_object"]) if result.get("dsl_object") else None,
        sql_query=result.get("sql_query"),
        sql_result=result.get("sql_result"),
        sql_verified=result.get("sql_verified", False),
        error=result.get("error"),
        error_node=result.get("error_node"),
        latency_ms=result.get("latency_ms", 0),
        tokens_used=result.get("tokens_used", 0),
        cache_hit=result.get("cache_hit", False),
    )