"""
LedgerMind — Phase 4: Router
==============================
"""

import json
import logging
import os
import re
from typing import Literal, Optional
from app.ingestion.entity_resolver import COMPANY_REGISTRY as COMPANY_PROFILES, resolve_ticker
from pydantic import BaseModel

from app.engines.dsl_compiler import METRIC_ALIASES, METRIC_REGISTRY
from app.engines.state import QueryState, record_llm_call
from app.llm.client import LLMUnavailable, generate_structured

logger = logging.getLogger(__name__)


class RouterResponse(BaseModel):
    company: Optional[str]
    # F2: the raw issuer name AS SEEN, independent of resolvability. `company`
    # is normalise-or-null, so when the model saw a company it could not
    # normalise it had nowhere to say so and returned null -- measured
    # 2026-08-11, where it explained the condition in route_reason prose
    # instead. Same shape as the DSL period-invention bug: a schema that
    # cannot express what the model observed.
    company_mentioned: Optional[str]
    # NO prompt block instructs this field, deliberately. One was written and
    # then removed: the model populates the field readily without it
    # (measured 2026-08-12, 'Reliance Industries' returned three times out of
    # three on gemini with no instruction present), so the instruction bought
    # a coverage guarantee that was never needed while adding a prompt block
    # among the entity fields the PATH CLASSIFICATION rules read.
    # NOT the reason: TQ008's semantic->cross routing. That was suspected and
    # DISPROVED -- TQ008 still routes cross with this block absent. Its cause
    # is unknown and predates this field. Do not re-add an instruction here;
    # if a probe ever shows omissions, split extraction and classification
    # into two calls rather than adding another prompt line.
    fiscal_year: Optional[str]
    quarter: Optional[str]
    financial_type: str
    path: Literal["semantic", "quantitative", "cross"]
    route_reason: str


# Model resolution and client construction live ONLY in app/llm/client.py.
# This module previously kept its own GEMINI_MODEL (defaulting to 2.5) and its
# own genai client, both dead since generate_structured landed — but a stale
# default is worse than dead code: it is a second, wrong answer to "which
# model actually runs", one grep away from the real one. Same failure class as
# this project's two formula copies and three metric registries.


_KNOWN_TICKERS = sorted({p.ticker for p in COMPANY_PROFILES})
_KNOWN_METRICS = sorted(METRIC_REGISTRY.keys())

ROUTER_SYSTEM_PROMPT = f"""You are the query router for LedgerMind, a financial research platform for Indian capital markets.

Given a user query, extract entities and classify the query path.

## ENTITY EXTRACTION

company:
  - Identify the Indian company being asked about
  - Normalise to canonical ticker from this list: {_KNOWN_TICKERS}
  - If no company mentioned, return null

fiscal_year:
  - Indian fiscal year runs April to March
  - Format: FY26, FY25, FY24, FY23 (2-digit year ending March)
  - "last year" -> infer from context; if unclear return null
  - If no year mentioned, return null

quarter:
  - Q1 (Apr-Jun), Q2 (Jul-Sep), Q3 (Oct-Dec), Q4 (Jan-Mar)
  - Return null if the query is about annual/full-year figures or no quarter mentioned

financial_type:
  - "consolidated" (default — parent + subsidiaries)
  - "standalone" ONLY if the user explicitly says "standalone", "parent only", or "excluding subsidiaries"
  - Default is always "consolidated"

## PATH CLASSIFICATION

quantitative:
  - Query asks for a specific financial metric value
  - Known metrics: {_KNOWN_METRICS}

semantic:
  - Query asks for qualitative/textual information

cross:
  - Query asks to verify or compare qualitative claims against financial numbers

Return ONLY a valid JSON object matching the requested schema. No explanation.
"""


def _resolve_mentioned_issuers(company_mentioned):
    """
    Split company_mentioned into (resolved, unresolved) ticker lists.

    F2 step 2. The refusal CANNOT key on `company is None`: RouterResponse
    holds ONE company, so a multi-entity query nulls it even when every
    issuer named is in the corpus. Measured 2026-08-12 on golden Q051 ("Who
    grew revenue faster in FY26, Eternal or Paytm?") -- company=None,
    company_mentioned='Eternal, Paytm', both resolvable. Refusing on
    nullness would have refused a question that passes today, which is worse
    than the unfiltered search F2 closes.

    Same defect class as F2 one level up: a single-valued field overloading
    null with two incompatible meanings -- "not in corpus" and "more than
    one". This separates them.
    """
    if not company_mentioned:
        return [], []
    parts = re.split(r",|\bvs\.?\b|\band\b|\bor\b", company_mentioned, flags=re.I)
    resolved, unresolved = [], []
    for raw in (x.strip() for x in parts):
        if not raw:
            continue
        r = resolve_ticker(raw)
        (resolved if r in _KNOWN_TICKERS else unresolved).append(r if r in _KNOWN_TICKERS else raw)
    return resolved, unresolved


def _classify_query(query: str) -> dict:
    # The whole LLMResult is carried out, not just .provider, so the
    # caller can record provider AND model in one attributed write.
    llm_result = None
    try:
        llm = generate_structured(
            system=ROUTER_SYSTEM_PROMPT,
            user=query,
            schema=RouterResponse,
            temperature=0.0,
            max_tokens=200,
        )
        llm_result = llm
        raw_text = llm.text
        try:
            result = json.loads(raw_text)
        except json.JSONDecodeError:
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.DOTALL).strip()
            result = json.loads(cleaned)

        company_raw = result.get("company")
        company = None
        company_unresolved = None
        if company_raw and company_raw.lower() != "null":
            resolved = resolve_ticker(company_raw)
            if resolved in _KNOWN_TICKERS:
                company = resolved
            else:
                # F2: resolve_ticker NEVER returns None -- it uppercases its
                # input -- so this gate, not resolve_ticker, is where an
                # unknown company is actually detected. Recording it keeps
                # "named a company we do not hold" distinguishable from "named
                # no company", which company=None alone cannot express.
                company_unresolved = company_raw
                logger.warning(
                    "Router named an unknown company: %r (resolved: %r) -- "
                    "not in _KNOWN_TICKERS", company_raw, resolved,
                )

        company_mentioned = result.get("company_mentioned")
        if company_mentioned and company_mentioned.lower() == "null":
            company_mentioned = None
        # Refuse only when NOTHING mentioned resolves. A multi-entity query
        # resolves several and nulls `company`; that must proceed, not refuse.
        _res, _unres = _resolve_mentioned_issuers(company_mentioned)
        if company_mentioned and not _res:
            company_unresolved = company_mentioned
            logger.warning(
                "Router named only unknown issuers: %r -- refusing",
                company_mentioned,
            )
        elif company is None and len(_res) > 1:
            logger.info(
                "Multi-entity query (%s) -- proceeding unfiltered", _res,
            )

        path = result.get("path", "semantic").lower()
        if path not in ("semantic", "quantitative", "cross"):
            path = "semantic"

        fiscal_year = result.get("fiscal_year")
        if fiscal_year and fiscal_year.lower() == "null":
            fiscal_year = None
        if fiscal_year:
            fiscal_year = fiscal_year.upper().strip()

        quarter = result.get("quarter")
        if quarter and quarter.lower() == "null":
            quarter = None
        if quarter:
            quarter = quarter.upper().strip()
            match = re.search(r"(Q[1-4])", quarter)
            quarter = match.group(1) if match else quarter

        financial_type = result.get("financial_type", "consolidated").lower().strip()
        if financial_type not in ("consolidated", "standalone"):
            financial_type = "consolidated"

        return {
            "company": company,
            "ticker": company,
            "company_unresolved": company_unresolved,
            "company_mentioned": company_mentioned,
            "fiscal_year": fiscal_year,
            "quarter": quarter,
            "financial_type": financial_type,
            "path": path,
            "route_reason": result.get("route_reason", ""),
            "llm_result": llm_result,
        }

    except LLMUnavailable as e:
        logger.error("Router classification unavailable on ALL providers: %s", e)
    except Exception as e:
        logger.error("Router classification failed: %s", e)

    # Both providers failed (or the response was unparseable). The audit trail
    # must be able to tell this apart from a genuine semantic classification —
    # an error-masked-as-semantic route is indistinguishable otherwise, which
    # is the defect class that cost two sessions of investigation.
    return {
        "company": None,
        "ticker": None,
        "company_unresolved": None,
        "company_mentioned": None,
        "fiscal_year": None,
        "quarter": None,
        "financial_type": "consolidated",
        "path": "semantic",
        "route_reason": "FALLBACK_ERROR: classification failed on all providers",
        "llm_result": None,
    }


def _build_resolved_query(
    original_query: str,
    company: Optional[str],
    fiscal_year: Optional[str],
    quarter: Optional[str],
    financial_type: str,
) -> str:
    prefix_parts = [p for p in [company, fiscal_year, quarter, financial_type] if p]
    return f"{' '.join(prefix_parts)} {original_query}" if prefix_parts else original_query


def router_node(state: QueryState) -> QueryState:
    if state["is_blocked"]:
        return state

    context = state.get("execution_context") or {}

    # 1. Always run Gemini to preserve entity & period extraction
    result = _classify_query(state["query"])

    state["company"]        = result["company"]
    state["ticker"]         = result["ticker"]
    state["fiscal_year"]    = result["fiscal_year"]
    state["quarter"]        = result["quarter"]
    state["financial_type"] = result["financial_type"]
    if result.get("llm_result") is not None:
        record_llm_call(state, result["llm_result"])
    state["resolved_query"] = _build_resolved_query(
        original_query=state["query"],
        company=result["company"],
        fiscal_year=result["fiscal_year"],
        quarter=result["quarter"],
        financial_type=result["financial_type"],
    )
    state["company_unresolved"] = result.get("company_unresolved")

    # ── F2: refuse rather than search unfiltered ──────────────────────────
    # retriever._build_filter appends a company condition only `if company:`,
    # so a null company silently widens the search to the whole tenant. Three
    # companies in three sectors currently mask that; at N+20 with several
    # issuers in one sector an unfiltered search retrieves a competitor's
    # chunk, the reranker scores it highly because it IS topically relevant,
    # and the answer cites a real page from the wrong company.
    #
    # Placed BEFORE the UI workflow override deliberately: forcing a desk does
    # not fix an entity that failed to resolve, so an override must not be
    # able to route past this.
    #
    # NOT refused here: a query that legitimately names no company. No golden
    # question exercises that path (all 91 name theirs in the text) and no
    # caller needs it, so it keeps today's behaviour rather than acquiring a
    # contract with no consumer.
    #
    # PARTIAL BY CONSTRUCTION -- READ BEFORE ASSUMING THIS CLOSES F2.
    # `company_not_in_corpus` fires only when the model RETURNS a name that
    # fails the _KNOWN_TICKERS gate (a misspelling, a subsidiary, a renamed
    # entity). It does NOT fire on the common case, because
    # ROUTER_SYSTEM_PROMPT offers the model only two options -- "normalise to
    # canonical ticker from this list" or "if no company mentioned, return
    # null" -- and RouterResponse.company is Optional[str], so null is legal.
    # Measured 2026-08-11 on "What were Reliance Industries revenue drivers in
    # FY26?": company=None, company_unresolved=None, and route_reason reading
    # "a company not in the supported list". The model OBSERVED the condition
    # and had no field in which to express it, so it explained itself in prose
    # and took the only exit the schema allowed. That query still runs
    # unfiltered over the whole tenant and answers at tier=high.
    # The fix is a schema addition (a `company_mentioned` field carrying the
    # raw name as seen, always, leaving `company`'s normalise-or-null contract
    # untouched) plus the matching prompt line -- a prompt edit, so it needs
    # explicit approval and a sweep behind it. Do not "fix" this by appending
    # an instruction that contradicts the normalise rule two lines above it;
    # that is the shape that lost three times already.
    _refusal = None
    # `route_reason` is not assigned to state before this point, so only the
    # result-side check does work. The state-side half read as though a stale
    # value could exist and would have been defended later.
    if (result.get("route_reason") or "").startswith("FALLBACK_ERROR"):
        _refusal = (
            "routing_unavailable",
            "The query could not be classified because no language model "
            "provider was reachable. LedgerMind does not answer from an "
            "unscoped search, so no result is returned. Please retry shortly.",
        )
    elif result.get("company_unresolved"):
        _refusal = (
            "company_not_in_corpus",
            "This query names a company that is not present in the available "
            "filings. LedgerMind can only answer questions about documents "
            "that have been ingested for your organisation.",
        )

    if _refusal is not None:
        state["error"], state["response_text"] = _refusal
        state["error_node"] = "router"
        state["path"] = result["path"]
        state["route_reason"] = result["route_reason"]
        state["confidence_tier"] = "low"
        state["confidence_score"] = 0.0
        logger.warning(
            "Router refusing | error=%s company_unresolved=%r",
            state["error"], state.get("company_unresolved"),
        )
        return state

    # 2. ⚡ DETERMINISTIC WORKFLOW OVERRIDE: Override classification path & inject DSL hint
    if context.get("enforce_path") and context.get("intended_path"):
        intended_path = context["intended_path"]
        logger.info(
            "⚡ UI Workflow Override: Forcing path '%s' (ignoring Gemini classification '%s')",
            intended_path, result["path"]
        )
        state["path"] = intended_path
        state["route_reason"] = f"UI Workflow Override: Routed directly to {intended_path} desk"
        
        if context.get("intended_operation"):
            state["preferred_operation"] = context["intended_operation"]
            
        return state

    # --- STANDARD PATH ---
    state["path"]         = result["path"]
    state["route_reason"] = result["route_reason"]
    return state


def route_after_shield(state: QueryState) -> str:
    return "blocked" if state["is_blocked"] else "router"


def route_after_router(state: QueryState) -> str:
    # F2 step 0: a refusal written by router_node must actually terminate.
    # This function is the ONLY thing the graph consults after the router --
    # it reads `path`, never `error` -- so before this branch existed a
    # refused state still dispatched into an engine and ran the unfiltered
    # search the refusal exists to prevent. Measured 2026-08-12 on a Reliance
    # query: 5 citations returned, all TITAN/ZOMATO pages, tier=high.
    # Keyed on error_node, not bare `error`: `error` is written by several
    # nodes and only the router's belongs upstream of the engine dispatch.
    if state.get("error_node") == "router" and state.get("error"):
        return "refused"

    path = state.get("path")
    if path == "quantitative":
        return "quant_engine"
    if path == "cross":
        return "cross_engine"
    return "semantic_engine"