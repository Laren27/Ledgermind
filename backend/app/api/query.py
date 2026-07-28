import asyncio
import json
import time
import uuid
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.auth.dependencies import get_current_user
from app.api.response_shaping import role_filtered_response
from app.engines.graph import get_graph
from app.engines.state import make_initial_state

router = APIRouter(prefix="/api", tags=["Query"])

# Human-readable label per graph node. Keys must match the node names
# registered in app/engines/graph.py exactly -- an unmapped node still
# streams, it just falls back to its raw name.
_NODE_LABELS = {
    "prompt_shield": "PROMPT SHIELD",
    "router": "ROUTER",
    "semantic_engine": "SEMANTIC RETRIEVAL",
    "quant_engine": "DSL \u2192 SQL",
    "cross_engine": "CROSS-EXAMINATION",
    "confidence": "CONFIDENCE",
    "response_generator": "RESPONSE",
    "audit_writer": "AUDIT",
}

_HEARTBEAT_SECONDS = 15


class QueryRequest(BaseModel):
    query: str
    tenant_id: Optional[str] = None
    execution_context: Optional[Dict[str, Any]] = None


def _trace_detail(node: str, partial: Dict[str, Any], role: str) -> Optional[str]:
    """
    One short, backend-sourced line describing what a node actually did.

    Every value here is read from real state -- nothing is inferred or
    invented, per the Zero UI-Hallucination Mandate. Returns None when the
    node has nothing meaningful to report, in which case the UI shows the
    label alone rather than filler text.
    """
    if node == "prompt_shield":
        return "Blocked by policy" if partial.get("is_blocked") else None

    if node == "router":
        # The moment the UI's unresolved engine slot becomes concrete.
        path = partial.get("path")
        return path.upper() if path else None

    if node == "semantic_engine":
        n = len(partial.get("retrieved_chunks") or [])
        return f"{n} chunk{'s' if n != 1 else ''} retrieved" if n else None

    if node == "quant_engine":
        if role == "viewer":
            # DSL/SQL machinery is analyst+ only; mirror role_filtered_response.
            return "Verified" if partial.get("sql_verified") else None
        dsl = partial.get("dsl_object") or {}
        op = dsl.get("operation")
        return op.replace("_", " ") if op else None

    if node == "confidence":
        tier = partial.get("confidence_tier")
        return tier.upper() if tier else None

    return None


async def _run_graph(initial_state: Dict[str, Any], queue: asyncio.Queue) -> None:
    """
    Drives the graph, pushing one item per completed node into `queue`.

    Runs as its own task so that a client disconnect kills only the SSE
    generator, never this. The pipeline always runs to completion --
    audit_writer_node included -- regardless of whether anyone is listening.
    """
    accumulated: Dict[str, Any] = dict(initial_state)
    try:
        graph = get_graph()
        async for update in graph.astream(initial_state, stream_mode="updates"):
            for node_name, partial in update.items():
                if partial:
                    accumulated.update(partial)
                await queue.put(("node", node_name, partial or {}))
        await queue.put(("complete", None, accumulated))
    except Exception as exc:  # noqa: BLE001 - surfaced to the client as an event
        await queue.put(("error", None, {"message": str(exc)}))
    finally:
        await queue.put(None)  # sentinel: generator stops draining


def _sse(event: str, payload: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


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
    return role_filtered_response(final_state, current_user["role"])


@router.post("/query/stream")
async def execute_query_stream(
    payload: QueryRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Same pipeline as POST /api/query, streamed as Server-Sent Events.

    Deliberately NOT a second execution path: the graph, the state factory
    and role_filtered_response are all shared with /api/query, so the two
    endpoints cannot drift. The only difference is transport -- this one
    reports node boundaries as they happen instead of returning once at the
    end. /api/query remains the fallback if a stream fails mid-flight.

    Node boundaries come from LangGraph's own astream("updates") rather than
    from instrumentation inside the nodes, so the trace is a byproduct of
    real execution and a node cannot silently forget to report itself.
    """
    request_id = str(uuid.uuid4())
    tenant_id = payload.tenant_id or current_user.get("tenant_id", "default")
    user_id = str(current_user["user_id"])
    role = current_user["role"]

    initial_state = make_initial_state(
        query=payload.query,
        tenant_id=tenant_id,
        user_id=user_id,
        request_id=request_id,
        execution_context=payload.execution_context,
    )

    async def event_stream() -> AsyncIterator[str]:
        # Unbounded on purpose: a bounded queue would let a disconnected
        # client block the producer mid-pipeline and strand the audit write.
        queue: asyncio.Queue = asyncio.Queue()
        task = asyncio.create_task(_run_graph(initial_state, queue))
        last_tick = time.perf_counter()

        yield _sse("start", {"request_id": request_id})

        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    # Keeps idle proxies from closing the connection during a
                    # slow LLM call. Comment lines are ignored by SSE clients.
                    yield ": heartbeat\n\n"
                    continue

                if item is None:
                    break

                kind, node_name, data = item

                if kind == "node":
                    now = time.perf_counter()
                    event: Dict[str, Any] = {
                        "node": node_name,
                        "label": _NODE_LABELS.get(node_name, node_name.upper()),
                        "status": "done",
                    }
                    detail = _trace_detail(node_name, data, role)
                    if detail:
                        event["detail"] = detail
                    if role == "admin":
                        # Same restriction as role_filtered_response's
                        # latency_ms -- per-node timing is admin-only.
                        event["duration_ms"] = int((now - last_tick) * 1000)
                    last_tick = now
                    yield _sse("node", event)

                elif kind == "complete":
                    yield _sse("complete", role_filtered_response(data, role))

                elif kind == "error":
                    # Headers are long gone by now, so a 500 is impossible --
                    # the failure has to travel as an event.
                    yield _sse("error", data)
        finally:
            # Never cancel `task`. If the client vanished mid-query the
            # pipeline must still finish so audit_writer_node writes its row.
            if task.done():
                task.exception()  # retrieve so asyncio doesn't warn

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Render/nginx buffer proxied responses by default, which would
            # hold every event until the pipeline finished and defeat the
            # entire point. Must be verified live on Render, not just locally.
            "X-Accel-Buffering": "no",
        },
    )
