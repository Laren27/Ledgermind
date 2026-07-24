import uuid
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth.dependencies import get_current_user
from app.engines.graph import compiled_graph
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
    user_id = str(current_user.get("id", "anonymous"))

    # 💡 Thread the execution_context directly into the state factory
    initial_state = make_initial_state(
        query=payload.query,
        tenant_id=tenant_id,
        user_id=user_id,
        request_id=request_id,
        execution_context=payload.execution_context,
    )

    try:
        # Invoke the LangGraph workflow
        final_state = await compiled_graph.ainvoke(initial_state)
        return final_state
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Pipeline execution failed: {str(e)}",
        )