"""
LedgerMind — Phase 4: Audit Writer
======================================
Final node in the graph. Writes a row to audit_log (append-only, never
updated or deleted) per blueprint §22.

Runs even on blocked/errored queries — refusals and blocks are audit-worthy
events too (blueprint §13: "Log refusal to audit store").

Uses the ledgermind_app connection (RLS-enforced) — audit_log has tenant_id
RLS like every other table, so writes are automatically scoped correctly.
"""

import json
import logging
import os
import time
from typing import Any, Dict

import psycopg2

from app.engines.state import QueryState

logger = logging.getLogger(__name__)


def _get_db_connection():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable not set")
    return psycopg2.connect(database_url)


def _safe_json(value: Any) -> Any:
    """
    psycopg2 JSONB adapter needs plain JSON-serialisable values.
    Decimal types from SQL results need explicit float conversion.
    """
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return None


def audit_writer_node(state: QueryState) -> QueryState:
    """
    Write the final query state to audit_log.

    Failure to write audit log is logged but does NOT raise — the user's
    response has already been generated and should not be blocked by an
    audit infrastructure failure. This matches blueprint §17 graceful
    degradation philosophy (no single point of failure kills the system).
    """
    latency_ms = int((time.time() - state["start_time"]) * 1000)
    state["latency_ms"] = latency_ms

    retrieved_chunk_ids = [c["chunk_id"] for c in state.get("retrieved_chunks", [])]
    vector_scores = [c["rrf_score"] for c in state.get("retrieved_chunks", [])]
    reranker_scores = [c["reranker_score"] for c in state.get("retrieved_chunks", [])]

    response_summary = (state.get("response_text") or "")[:500]

    try:
        conn = _get_db_connection()
        with conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL app.tenant_id = %s", (str(state["tenant_id"]),))
                cur.execute(
                    """
                    INSERT INTO audit_log (
                        tenant_id, user_id, query_text, query_path,
                        retrieved_chunk_ids, vector_scores, reranker_scores,
                        dsl_generated, sql_executed, confidence_score,
                        response_text, latency_ms, tokens_used, created_at
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, NOW()
                    )
                    """,
                    (
                        state["tenant_id"],
                        state["user_id"],
                        state["query"],
                        state.get("path") or ("blocked" if state["is_blocked"] else "unknown"),
                        retrieved_chunk_ids,
                        vector_scores,
                        reranker_scores,
                        json.dumps(_safe_json(state.get("dsl_object"))) if state.get("dsl_object") else None,
                        state.get("sql_query"),
                        state.get("confidence_score", 0.0),
                        response_summary,
                        latency_ms,
                        state.get("tokens_used", 0),
                    ),
                )
        conn.close()

        logger.info(
            "Audit log written | request_id=%s path=%s latency_ms=%d confidence=%.2f",
            state["request_id"], state.get("path"), latency_ms,
            state.get("confidence_score", 0.0),
        )

    except Exception as e:
        # Never let audit failure block the response from reaching the user
        logger.error(
            "Audit log write FAILED (response still delivered) | request_id=%s error=%s",
            state["request_id"], e,
        )

    return state