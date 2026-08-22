"""
Shapes the raw QueryState dict returned by the graph, based on the
requester's role. NOTE: there is no intermediate QueryResponse model —
api/query.py passes graph.ainvoke()'s final_state directly to this function,
so the keys read below are QueryState keys. An earlier version of this
docstring claimed otherwise and cost a wrong prediction about where a new
field had to be threaded (2026-07-31, llm_model). Everyone gets a correct, cited, well-formed answer;
only analyst/admin see the machinery (DSL, SQL, raw retrieval scores) behind
it. The graph always runs in full and audit_log always gets the complete
record regardless of role -- only the HTTP response is filtered.

Field names here must therefore track app/engines/state.py's QueryState
exactly. A key that does not exist there returns None silently — .get() will
not tell you the field was never populated.
"""

_KNOWN_ROLES = frozenset({"viewer", "analyst", "admin"})

# chunk_id is included deliberately: it carries no information (opaque UUID)
# but the frontend needs it as a stable DOM anchor id to tie inline
# superscripts to their numbered footnotes. Scores stay stripped.
_VIEWER_CITATION_FIELDS = {"chunk_id", "doc_id", "page_number", "company", "fiscal_year", "financial_type"}


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
        # F14: OMITTED, not substituted. A multi-issuer result has no single
        # correct value for a scalar "company", and the zero-UI-hallucination
        # mandate says omit rather than pick one. `companies` carries the real
        # answer; a frontend that wants to show issuers reads that.
        "companies": response.get("companies") or [],
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

    # Fail closed. Any role that isn't explicitly recognised -- a typo, a null,
    # a future role added to the DB but not here -- gets the most restrictive
    # payload, never the least. Without this the function falls through every
    # `if` and returns the full admin response to unknown roles.
    if role not in _KNOWN_ROLES or role == "viewer":
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
        # Which provider actually served this answer. Admin-only: it is
        # operational detail, but it must be visible SOMEWHERE -- a
        # Groq-served answer is a different artifact from a Gemini one and
        # cannot be allowed to look identical in the record.
        "llm_provider": response.get("llm_provider"),
        # Which MODEL served it, e.g. "gemini-3.1-flash-lite". Admin-tier for
        # the same reason as llm_provider, and required by the eval gate:
        # scripts/eval_runner.py asserts this against its --model argument
        # rather than trusting the label. On 2026-07-31 two full sweeps were
        # reported under a model that never served a single call, because
        # --model was only ever a label and nothing recorded the truth.
        "llm_model": response.get("llm_model"),
        # WHICH RERANKER SCORED THE CITATIONS. Admin-tier, same reasoning as
        # llm_provider: an operational fact that must be visible SOMEWHERE
        # because it changes what the numbers beside it MEAN.
        #
        # citations carry reranker_score with no unit. Cohere returns 0-1;
        # the local ONNX cross-encoder returns raw logits (~-12 to +2). The
        # confidence thresholds are split accordingly (COHERE 0.5/0.15,
        # LOCAL -4.5/-7.5, see _score_confidence) so the TIER is correct on
        # either backend -- but the SCORE was being handed to the reader with
        # nothing saying which scale it was on.
        #
        # This is not hypothetical. Cohere is primary with local ONNX as an
        # automatic fallback on API failure, and on 2026-08-02 that fallback
        # fired mid-session from WSL2 network flap (raw socket connects to
        # api.cohere.com succeeded 5 of 8 attempts, failing at random). The
        # same query returned tier=medium on one run and tier=high on another
        # purely because a different backend scored it. Reading -3.39 as a
        # Cohere score rather than an ONNX logit then produced a wrong
        # conclusion about threshold calibration that reached this repo's
        # documentation before it was caught. scripts/cohere_score_dump.py has
        # a hard abort for exactly this mistake; the query response had
        # nothing.
        #
        # Derived from retrieved_chunks rather than recomputed: retriever.py
        # tags every chunk at the point of scoring, and a second derivation
        # here would be one more copy of a fact that already exists -- the
        # failure class behind the three metric registries and the two
        # independent formula copies.
        #
        # Reports what is actually TAGGED, and None when nothing is. That is
        # deliberately NOT _score_confidence's `.get("reranker_backend",
        # "local")` default: there, "local" is a safety choice (assume the
        # stricter scale when unsure). Here it would be an observation, and
        # reporting an assumption as an observation is how this went wrong in
        # the first place. None on a blocked or pure-quantitative query is
        # correct -- nothing was reranked.
        "reranker_backend": _reranker_backend(response),
    })
    return base


def _reranker_backend(response: dict):
    """Backend that scored the citations, or None if nothing was reranked.

    One rerank call per query, so one backend for the whole set -- this is a
    response-level fact, not a per-citation one, and attaching it to each
    citation would imply a variability that does not exist.
    """
    chunks = response.get("retrieved_chunks") or []
    if not chunks:
        return None
    return chunks[0].get("reranker_backend")