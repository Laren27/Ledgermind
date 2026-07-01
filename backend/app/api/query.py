"""
LedgerMind — Phase 5: Query API Endpoint (JWT-authenticated)
============================================
Phase 5 changes from the Phase 4 version:

1. tenant_id/user_id are NO LONGER trusted from the request body. They come
   from the verified JWT (via require_role), so a caller cannot claim to be
   a different tenant by editing the request payload. QueryRequest now only
   carries `query`.

2. No injected DB connection is needed here. quant_engine.py's
   _execute_sql() already opens its own connection per call and runs
   `SET LOCAL app.tenant_id = %s` using tenant_id read from state — and
   state["tenant_id"] is set below from the verified JWT, not client input.
   That's the entire RLS correctness requirement for Phase 5: as long as
   this endpoint never lets request.tenant_id reach state, every downstream
   SQL call (quant_engine, presumably contradiction.py/audit_writer.py
   following the same pattern) is automatically scoped to the right tenant.
   (Earlier draft of this file added a Depends(get_db_conn) connection-
   injection layer assuming engines needed a shared connection -- they
   don't, since each engine call is already self-contained. Removed.)

3. Full QueryResponse is still built exactly as in Phase 4 (nothing trimmed
   at the graph layer -- audit_log always gets the complete record).
   Only the HTTP response returned to the client is filtered by role via
   role_filtered_response(), so viewers don't see DSL/SQL/raw scores while
   analysts and admins do.

NOTE: db/session.py's db_transaction()/get_db_conn() are still used, but
only by auth/service.py for the login lookup (the one query that runs
before a tenant_id is known at all). They are not used here.
"""

import logging
import uuid

from fastapi import APIRouter, HTTPException, Depends
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.engines.graph import get_graph
from app.engines.state import make_initial_state

from app.auth.dependencies import require_role
from app.api.response_shaping import role_filtered_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["query"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    # tenant_id / user_id REMOVED as of Phase 5 -- sourced from the verified
    # JWT instead (see require_role dependency below), never from client input.
    query: str = Field(..., min_length=1, max_length=2000, description="The user's natural language question")


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
    Full QueryState surfaced internally. role_filtered_response() trims this
    down per-role before it goes out over HTTP -- this model itself stays
    the complete, ungated shape, same as Phase 4.
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

@router.post("/query")
async def query_endpoint(
    request: QueryRequest,
    user: dict = Depends(require_role("viewer")),  # any authenticated role may query;
                                                      # field-level restriction happens in
                                                      # role_filtered_response below
):
    """
    Single entry point for all LedgerMind queries.

    tenant_id and user_id come from the verified JWT (`user`), not from the
    request body -- this is the entire Phase 5 security boundary for this
    endpoint. Builds initial state -> invokes the compiled graph in a thread
    pool -> filters the full result by the caller's role -> returns.

    Does not raise HTTPException for query-level failures (blocked queries,
    low confidence refusals, DSL/SQL failures) -- those are valid, well-formed
    responses with error fields populated, not HTTP errors. HTTPException is
    reserved for actual infrastructure failures.
    """
    request_id = str(uuid.uuid4())

    logger.info(
        "Query received | request_id=%s tenant_id=%s user_id=%s role=%s query='%s'",
        request_id, user["tenant_id"], user["user_id"], user["role"], request.query[:80],
    )

    state = make_initial_state(
        query=request.query,
        tenant_id=user["tenant_id"],   # from JWT -- never client input
        user_id=user["user_id"],       # from JWT -- never client input
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

    full_response = QueryResponse(
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

    return role_filtered_response(full_response.model_dump(), user["role"])