"""
Shapes the QueryResponse (already built from QueryState by query.py) based
on the requester's role. Everyone gets a correct, cited, well-formed answer;
only analyst/admin see the machinery (DSL, SQL, raw retrieval scores) behind
it. The graph always runs in full and audit_log always gets the complete
record regardless of role -- only the HTTP response is filtered.

Operates on the QueryResponse.model_dump() dict, so field names here must
track api/query.py's QueryResponse model exactly.
"""

_VIEWER_CITATION_FIELDS = {"doc_id", "page_number", "company", "fiscal_year", "financial_type"}


def _strip_citation_scores(citations: list[dict]) -> list[dict]:
    return [{k: c.get(k) for k in _VIEWER_CITATION_FIELDS} for c in citations]


def _strip_contradiction_values(contradictions: list[dict]) -> list[dict]:
    # Viewer sees that a contradiction exists and its severity, not the
    # underlying numbers/claims that produced it.
    return [{"type": c.get("type"), "severity": c.get("severity")} for c in contradictions]


def role_filtered_response(response: dict, role: str) -> dict:
    base = {
        "request_id": response["request_id"],
        "query": response["query"],
        "path": response.get("path"),
        "is_blocked": response["is_blocked"],
        "block_reason": response.get("block_reason"),
        "company": response.get("company"),
        "fiscal_year": response.get("fiscal_year"),
        "quarter": response.get("quarter"),
        "financial_type": response.get("financial_type"),
        "response_text": response.get("response_text"),
        "confidence_tier": response.get("confidence_tier"),
        "citations": _strip_citation_scores(response.get("citations", [])),
        "has_contradictions": bool(response.get("contradictions")),
        "contradictions": _strip_contradiction_values(response.get("contradictions", [])),
        "error": response.get("error"),
    }

    if role == "viewer":
        return base

    # analyst and admin both get the full machinery
    base.update({
        "confidence_score": response.get("confidence_score"),
        "crag_triggered": response.get("crag_triggered"),
        "crag_count": response.get("crag_count"),
        "citations": response.get("citations", []),            # full, with reranker_score
        "contradictions": response.get("contradictions", []),  # full detail
        "dsl_object": response.get("dsl_object"),
        "sql_query": response.get("sql_query"),
        "sql_result": response.get("sql_result"),
        "sql_verified": response.get("sql_verified"),
        "error_node": response.get("error_node"),
    })

    if role == "analyst":
        return base

    # admin
    base.update({
        "latency_ms": response.get("latency_ms"),
        "tokens_used": response.get("tokens_used"),
        "cache_hit": response.get("cache_hit"),
    })
    return base