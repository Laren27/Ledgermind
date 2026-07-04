"""
LedgerMind — Phase 4: Router
==============================
Second node in the LangGraph graph (runs after prompt_shield).

Two responsibilities in one Gemini call:
  1. Entity extraction  — company → ticker, fiscal year, quarter, financial_type
  2. Path classification — semantic | quantitative | cross

Why one call instead of two:
  Entity extraction needs the same context as path classification.
  Combining avoids an extra round trip and keeps routing latency low.

Path semantics:
  semantic     — qualitative question: risks, strategy, management commentary, ESG
  quantitative — numerical question: revenue, income, growth, CAGR, margins
  cross        — verification question: does qualitative claim match the numbers?

Fallback on any Gemini failure → semantic (safe default — text retrieval cannot
return wrong financial figures; wrong SQL can).

financial_type default → consolidated per blueprint §4.1 router rule.
"""

import json
import logging
import os
import re
from typing import Literal, Optional

from google import genai
from google.genai import types
from pydantic import BaseModel

from app.engines.dsl_compiler import METRIC_ALIASES, METRIC_REGISTRY
from app.engines.state import QueryState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic schema — enforces structured output from Gemini.
# Passed as response_schema to GenerateContentConfig.
# Stronger guarantee than response_mime_type alone: the SDK validates the
# returned JSON against this model before handing it to our code.
# ---------------------------------------------------------------------------

class RouterResponse(BaseModel):
    company: Optional[str]
    fiscal_year: Optional[str]
    quarter: Optional[str]
    financial_type: str
    path: Literal["semantic", "quantitative", "cross"]
    route_reason: str


# ---------------------------------------------------------------------------
# Company registry — maps natural language names → canonical tickers
# Kept in this module; engines package stays independent of ingestion.
# Expand as corpus grows.
# ---------------------------------------------------------------------------

COMPANY_REGISTRY = {
    # Eternal / Zomato (same entity, rebranded)
    "eternal":          "ETERNAL",
    "zomato":           "ETERNAL",
    "zomato limited":   "ETERNAL",
    "eternal limited":  "ETERNAL",

    # Paytm
    "paytm":            "PAYTM",
    "one 97":           "PAYTM",
    "one97":            "PAYTM",

    # Nykaa
    "nykaa":            "NYKAA",
    "fss":              "NYKAA",

    # PolicyBazaar
    "policybazaar":     "POLICYBAZAAR",
    "pb fintech":       "POLICYBAZAAR",

    # Swiggy
    "swiggy":           "SWIGGY",
    "bundl":            "SWIGGY",

    # Delhivery
    "delhivery":        "DELHIVERY",

    # Blinkit (subsidiary of Eternal — kept separate for cross-examination queries)
    "blinkit":          "BLINKIT",
}

# ---------------------------------------------------------------------------
# Gemini model — read from env so you can swap models without touching code.
# Add GEMINI_MODEL=gemini-2.5-flash-lite (or any other) to your .env file.
# ---------------------------------------------------------------------------

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

# ---------------------------------------------------------------------------
# Gemini client singleton
# ---------------------------------------------------------------------------

_gemini_client: Optional[genai.Client] = None


def _get_gemini_client() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY environment variable not set")
        _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client


# ---------------------------------------------------------------------------
# System prompt — controls Gemini's extraction and classification behaviour
# ---------------------------------------------------------------------------

_KNOWN_TICKERS = sorted(set(COMPANY_REGISTRY.values()))
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
  - "last year" → infer from context; if unclear return null
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
  - Examples: revenue, income, profit, growth rate, CAGR, margin, EBITDA
  - Known metrics: {_KNOWN_METRICS}
  - Signal words: "how much", "what was the revenue", "growth", "margin", "compare revenue"

semantic:
  - Query asks for qualitative/textual information
  - Examples: risks, strategy, governance, ESG, regulatory disclosures, management commentary
  - Signal words: "what did management say", "what risks", "explain", "describe", "summarize"

cross:
  - Query asks to verify or compare qualitative claims against financial numbers
  - Signal words: "consistent with", "align with", "does management's claim match", "verify",
    "contradict", "is what they said true given the numbers"

## RESPONSE FORMAT

Return ONLY a valid JSON object. No explanation. No markdown. No code blocks.

{{
  "company": "TICKER or null",
  "fiscal_year": "FYxx or null",
  "quarter": "Qx or null",
  "financial_type": "consolidated",
  "path": "semantic or quantitative or cross",
  "route_reason": "one sentence explaining why this path was chosen"
}}"""


# ---------------------------------------------------------------------------
# Gemini classification call
# ---------------------------------------------------------------------------

def _classify_query(query: str) -> dict:
    """
    Call Gemini to extract entities and classify path.

    Returns a dict with keys: company, fiscal_year, quarter, financial_type, path, route_reason.
    Returns safe defaults on any failure — never raises.
    """
    client = _get_gemini_client()

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=query,
            config=types.GenerateContentConfig(
                system_instruction=ROUTER_SYSTEM_PROMPT,
                temperature=0.0,
                max_output_tokens=200,
                response_mime_type="application/json",
                response_schema=RouterResponse,
            ),
        )

        raw_text = response.text.strip()
        logger.debug("Router Gemini raw response: %s", raw_text)

        # Primary parse — should always succeed with response_schema set.
        # Fence-stripper is a last-resort fallback for any SDK edge case.
        try:
            result = json.loads(raw_text)
        except json.JSONDecodeError:
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.DOTALL).strip()
            logger.warning("Router: primary JSON parse failed, retrying after fence-strip")
            result = json.loads(cleaned)

        # Normalise and validate fields
        company_raw = result.get("company")
        company = None
        if company_raw and company_raw.lower() != "null":
            # Accept ticker directly if already in registry values
            upper = company_raw.strip().upper()
            if upper in _KNOWN_TICKERS:
                company = upper
            else:
                # Try lookup via lower-case registry keys
                company = COMPANY_REGISTRY.get(company_raw.strip().lower())

        path = result.get("path", "semantic").lower()
        if path not in ("semantic", "quantitative", "cross"):
            logger.warning("Router returned unknown path '%s', defaulting to semantic", path)
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

        financial_type = result.get("financial_type", "consolidated").lower().strip()
        if financial_type not in ("consolidated", "standalone"):
            financial_type = "consolidated"

        route_reason = result.get("route_reason", "")

        logger.info(
            "Router classified | company=%s fiscal_year=%s quarter=%s "
            "financial_type=%s path=%s",
            company, fiscal_year, quarter, financial_type, path,
        )

        return {
            "company": company,
            "ticker": company,
            "fiscal_year": fiscal_year,
            "quarter": quarter,
            "financial_type": financial_type,
            "path": path,
            "route_reason": route_reason,
        }

    except json.JSONDecodeError as e:
        logger.error("Router failed to parse Gemini JSON response: %s", e)
    except Exception as e:
        logger.error("Router Gemini call failed: %s", e)

    # Safe fallback — semantic path, no entities extracted
    return {
        "company": None,
        "ticker": None,
        "fiscal_year": None,
        "quarter": None,
        "financial_type": "consolidated",
        "path": "semantic",
        "route_reason": "Fallback to semantic: Gemini classification failed",
    }


# ---------------------------------------------------------------------------
# Resolved query builder
# ---------------------------------------------------------------------------

def _build_resolved_query(
    original_query: str,
    company: Optional[str],
    fiscal_year: Optional[str],
    quarter: Optional[str],
    financial_type: str,
) -> str:
    """
    Rewrite query with normalised entities for cleaner retrieval.

    Example:
      "What did Zomato say about quick commerce last year?"
      → "ETERNAL FY26 consolidated What did Zomato say about quick commerce?"

    The entity prefix improves BM25 exact-match retrieval on ticker/year terms.
    The original phrasing is preserved for semantic meaning.
    """
    prefix_parts = []
    if company:
        prefix_parts.append(company)
    if fiscal_year:
        prefix_parts.append(fiscal_year)
    if quarter:
        prefix_parts.append(quarter)
    prefix_parts.append(financial_type)

    if prefix_parts:
        return f"{' '.join(prefix_parts)} {original_query}"
    return original_query


# ---------------------------------------------------------------------------
# LangGraph node
# ---------------------------------------------------------------------------

def router_node(state: QueryState) -> QueryState:
    """
    LangGraph node: entity extraction + path classification.

    Skips Gemini call if prompt_shield already blocked the query.
    Populates: company, ticker, fiscal_year, quarter, financial_type,
               path, route_reason, resolved_query.
    """
    # Short-circuit if prompt_shield blocked the query
    if state["is_blocked"]:
        logger.debug("Router skipped — query already blocked by prompt_shield")
        return state

    result = _classify_query(state["query"])

    state["company"]        = result["company"]
    state["ticker"]         = result["ticker"]
    state["fiscal_year"]    = result["fiscal_year"]
    state["quarter"]        = result["quarter"]
    state["financial_type"] = result["financial_type"]
    state["path"]           = result["path"]
    state["route_reason"]   = result["route_reason"]
    state["resolved_query"] = _build_resolved_query(
        original_query=state["query"],
        company=result["company"],
        fiscal_year=result["fiscal_year"],
        quarter=result["quarter"],
        financial_type=result["financial_type"],
    )

    return state


# ---------------------------------------------------------------------------
# Conditional edge function — used by graph.py to route between nodes
# ---------------------------------------------------------------------------

def route_after_shield(state: QueryState) -> str:
    """
    Edge function after prompt_shield node.
    Returns the next node name for LangGraph to invoke.
    """
    if state["is_blocked"]:
        return "blocked"
    return "router"


def route_after_router(state: QueryState) -> str:
    """
    Edge function after router node.
    Returns the engine node name for LangGraph to invoke.
    """
    path = state.get("path")
    if path == "quantitative":
        return "quant_engine"
    if path == "cross":
        return "cross_engine"
    return "semantic_engine"   # default for "semantic" and any unknown value