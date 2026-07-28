import uuid
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth.dependencies import get_current_user
from app.api.response_shaping import role_filtered_response
from app.engines.graph import get_graph
from app.engines.state import make_initial_state

router = APIRouter(prefix="/api", tags=["Query"])


class QueryRequest(BaseModel):
    query: str
    tenant_id: Optional[str] = None
    execution_context: Optional[Dict[str, Any]] = None


@router.post("/query")
async def execute_query(
    payload: QueryRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    request_id = str(uuid.uuid4())
    tenant_id = payload.tenant_id or current_user.get("tenant_id", "default")
    user_id = str(current_user["user_id"])

    # Thread the execution_context directly into the state factory
    initial_state = make_initial_state(
        query=payload.query,
        tenant_id=tenant_id,
        user_id=user_id,
        request_id=request_id,
        execution_context=payload.execution_context,
    )

    try:
        # Call get_graph() to retrieve the compiled LangGraph singleton
        graph = get_graph()
        final_state = await graph.ainvoke(initial_state)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Pipeline execution failed: {str(e)}",
        )

    # Field-level RBAC. The graph always runs in full and audit_log always
    # receives the complete record -- only the HTTP response is filtered.
    # This also drops retrieved_chunks (full chunk text) from every response,
    # which the raw-state return was previously shipping to the browser.
    return role_filtered_response(final_state, current_user["role"])
