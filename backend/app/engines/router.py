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
"""

import json
import logging
import os
import re
from typing import Literal, Optional
from app.ingestion.entity_resolver import COMPANY_REGISTRY as COMPANY_PROFILES, resolve_ticker
from google import genai
from google.genai import types
from pydantic import BaseModel

from app.engines.dsl_compiler import METRIC_ALIASES, METRIC_REGISTRY
from app.engines.state import QueryState

logger = logging.getLogger(__name__)

class RouterResponse(BaseModel):
    company: Optional[str]
    fiscal_year: Optional[str]
    quarter: Optional[str]
    financial_type: str
    path: Literal["semantic", "quantitative", "cross"]
    route_reason: str

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
_gemini_client: Optional[genai.Client] = None

def _get_gemini_client() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY environment variable not set")
        _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client

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


def _classify_query(query: str) -> dict:
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

        try:
            result = json.loads(raw_text)
        except json.JSONDecodeError:
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.DOTALL).strip()
            logger.warning("Router: primary JSON parse failed, retrying after fence-strip")
            result = json.loads(cleaned)

        company_raw = result.get("company")
        company = None
        if company_raw and company_raw.lower() != "null":
            resolved = resolve_ticker(company_raw)
            if resolved in _KNOWN_TICKERS:
                company = resolved

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
            match = re.search(r"(Q[1-4])", quarter)
            quarter = match.group(1) if match else quarter

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

    return {
        "company": None,
        "ticker": None,
        "fiscal_year": None,
        "quarter": None,
        "financial_type": "consolidated",
        "path": "semantic",
        "route_reason": "Fallback to semantic: Gemini classification failed",
    }


def _build_resolved_query(
    original_query: str,
    company: Optional[str],
    fiscal_year: Optional[str],
    quarter: Optional[str],
    financial_type: str,
) -> str:
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


def router_node(state: QueryState) -> QueryState:
    if state["is_blocked"]:
        logger.debug("Router skipped — query already blocked by prompt_shield")
        return state

    context = state.get("execution_context") or {}
    
    # ⚡ FAST PATH: UI Workflow Override (e.g., Peer Comparison desk)
    # Bypasses Gemini classification entirely for 100% determinism & 0ms LLM latency.
    if context.get("enforce_path") and context.get("intended_path"):
        intended = context["intended_path"]
        logger.info(
            "⚡ UI Workflow Override: Bypassing Gemini classifier -> forcing path '%s'",
            intended
        )
        state["path"]           = intended
        state["financial_type"] = context.get("financial_type", "consolidated")
        state["route_reason"]   = f"UI Workflow Override: Routed directly to {intended} (bypassed LLM classification)"
        state["resolved_query"] = state["query"]
        return state

    # --- STANDARD PATH: Fall back to Gemini LLM classification ---
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


def route_after_shield(state: QueryState) -> str:
    if state["is_blocked"]:
        return "blocked"
    return "router"


def route_after_router(state: QueryState) -> str:
    path = state.get("path")
    if path == "quantitative":
        return "quant_engine"
    if path == "cross":
        return "cross_engine"
    return "semantic_engine"