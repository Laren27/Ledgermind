"""
LedgerMind — Phase 4: Quantitative Engine (Path 2)
====================================================
DSL → SQL → PostgreSQL → verified result

The LLM (Gemini) generates a controlled DSL object.
A deterministic Python compiler converts that to SQL.
The LLM never writes SQL. The LLM never sees the schema.

Self-healing loop (max 2 retries):
  Generate DSL → validate → if invalid + repair_hint → re-generate with hint → validate
  After 2nd failure: structured error response, no SQL execution.

Verification rules:
  point_in_time → expect exactly 1 row (0 or >1 = self-healing trigger)
  yoy_growth    → expect 1 row per year (prior year may be missing → error)
  comparison    → expect 1 row per entity
  cagr          → expect ≥2 rows (fewer = cannot compute)

Trap 2 (blueprint §25B): financial_type MUST always be in the WHERE clause.
  Enforced by dsl_compiler — cannot be bypassed from here.

DSL prompt metric list and aliases below are now DERIVED from the single
shared registry at app/metrics/registry.py instead of a hand-maintained
ALIASES dict. See that file's module docstring for the rationale.
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras
from google import genai
from google.genai import types
from pydantic import BaseModel

from app.engines.dsl_compiler import (
    METRIC_REGISTRY,
    OPERATION_REGISTRY,
    compile_dsl,
    validate_dsl,
)
from app.engines.router import GEMINI_MODEL
from app.engines.state import DSLObject, QueryState
from app.metrics.registry import prompt_metric_lines, prompt_warnings

logger = logging.getLogger(__name__)

MAX_DSL_ATTEMPTS = 2   # blueprint §10: max 2 retries in self-healing loop

# ---------------------------------------------------------------------------
# Pydantic schema for Gemini DSL generation
# Mirrors DSLObject but with all-optional fields so Gemini can omit unknowns.
# Validation is done by DSLValidator, not by Pydantic here.
# ---------------------------------------------------------------------------

class GeminiDSLResponse(BaseModel):
    metric: str
    entity: str
    fiscal_year: str
    quarter: Optional[str]
    financial_type: str
    operation: str
    comparison_entity: Optional[str]
    comparison_period: Optional[str]

# ---------------------------------------------------------------------------
# Gemini client singleton (reuses same pattern as router)
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
# DSL generation prompt
# ---------------------------------------------------------------------------

_AVAILABLE_METRICS = [k for k, v in METRIC_REGISTRY.items() if v["available"]]
_ALL_METRICS = list(METRIC_REGISTRY.keys())
_OPERATIONS = list(OPERATION_REGISTRY.keys())

def _build_dsl_system_prompt() -> str:
    # Metric lines and disambiguation warnings now come from the shared
    # registry (app/metrics/registry.py) instead of a hand-maintained
    # ALIASES dict here — this was the exact spot where profit_before_tax's
    # PBT-vs-PAT warning previously had to be hand-kept in sync with
    # dsl_compiler.py's registry entry. Now both read from one place.
    metric_lines = prompt_metric_lines()
    mapping_warnings = prompt_warnings()

    unavailable = {k: v for k, v in METRIC_REGISTRY.items() if not v["available"]}
    unavail_keys = list(unavailable.keys())

    warnings_block = "\n".join(f"    {w}" for w in mapping_warnings)

    return f"""You are the DSL generator for LedgerMind, a financial data platform for Indian capital markets.

    Convert the user's financial query into a structured DSL object.

    ## AVAILABLE METRICS — use EXACTLY these strings, character-for-character
    {chr(10).join(metric_lines)}

    ## UNAVAILABLE METRICS — registered but not yet in corpus, return these names if asked
    {unavail_keys}

    ## CRITICAL MAPPING RULES
    - "other income" → "other_income"  (NOT "total_income")
    - "net profit" / "profit after tax" → "pat"
    - "total income" → "total_income"  (revenue PLUS other income)
    - "other operating revenue" → "other_operating_revenue"  (NOT "revenue" —
      this is a SEPARATE sub-line some companies report alongside main revenue)
    - "revenue" / "revenue from operations" → "revenue"
    - You MUST use the exact metric key string from the AVAILABLE list above.
    - If the user asks for a metric not in either list, pick the closest available match.

    ## DISAMBIGUATION WARNINGS
{warnings_block}

    ## OPERATIONS
    - point_in_time  : single value for one entity and one period
    - yoy_growth     : year-over-year % change
    - comparison     : two entities, same period (needs comparison_entity)
    - cagr           : compound annual growth rate across all available years

    ## RULES
    - entity: canonical ticker (ETERNAL, PAYTM, NYKAA, etc.)
    - For comparison operations: "entity" MUST be the company named FIRST in the
      query, and "comparison_entity" MUST be the company named SECOND.
      Example: "Compare Eternal's and Paytm's revenue" → entity="ETERNAL",
      comparison_entity="PAYTM" — NOT the reverse, regardless of which company
      the question focuses on afterward.
    - fiscal_year: Indian format FY26, FY25, FY24
    - quarter: Q1/Q2/Q3/Q4 only if query is about a specific quarter; null for annual
    - financial_type: "consolidated" (default) or "standalone" only if explicitly requested

    Return ONLY a valid JSON object. No explanation. No markdown."""

DSL_SYSTEM_PROMPT = _build_dsl_system_prompt()

def _build_dsl_user_message(
    query: str,
    company: Optional[str],
    fiscal_year: Optional[str],
    quarter: Optional[str],
    financial_type: str,
    repair_hint: Optional[str] = None,
) -> str:
    """Build the user message for DSL generation, optionally injecting a repair hint."""
    context = (
        f"Query: {query}\n"
        f"Already extracted from query:\n"
        f"  entity: {company or 'unknown'}\n"
        f"  fiscal_year: {fiscal_year or 'unknown'}\n"
        f"  quarter: {quarter or 'null (annual)'}\n"
        f"  financial_type: {financial_type}\n"
    )
    if repair_hint:
        context += f"\nPREVIOUS ATTEMPT FAILED. Fix this issue:\n{repair_hint}\n"
    return context


# ---------------------------------------------------------------------------
# DSL generation with self-healing loop
# ---------------------------------------------------------------------------

def _generate_dsl(
    query: str,
    company: Optional[str],
    fiscal_year: Optional[str],
    quarter: Optional[str],
    financial_type: str,
) -> Tuple[Optional[DSLObject], int, Optional[str]]:
    """
    Generate and validate a DSL object, with up to MAX_DSL_ATTEMPTS retries.

    Returns: (dsl_object, attempts_used, error_message)
      - dsl_object is None on failure
      - error_message describes what went wrong (for user-facing response)
    """
    client = _get_gemini_client()
    repair_hint: Optional[str] = None
    attempts = 0

    while attempts < MAX_DSL_ATTEMPTS:
        attempts += 1
        logger.info("DSL generation attempt %d/%d", attempts, MAX_DSL_ATTEMPTS)

        user_message = _build_dsl_user_message(
            query=query,
            company=company,
            fiscal_year=fiscal_year,
            quarter=quarter,
            financial_type=financial_type,
            repair_hint=repair_hint,
        )

        try:
            response = _get_gemini_client().models.generate_content(
                model=GEMINI_MODEL,
                contents=user_message,
                config=types.GenerateContentConfig(
                    system_instruction=DSL_SYSTEM_PROMPT,
                    temperature=0.0,
                    max_output_tokens=200,
                    response_mime_type="application/json",
                    response_schema=GeminiDSLResponse,
                ),
            )

            raw_text = response.text.strip()
            logger.debug("DSL raw response (attempt %d): %s", attempts, raw_text)

            try:
                raw_dict = json.loads(raw_text)
            except json.JSONDecodeError:
                # Fence-strip fallback
                cleaned = re.sub(
                    r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.DOTALL
                ).strip()
                raw_dict = json.loads(cleaned)

            # Override entity fields with router-extracted values (more reliable)
            # EXCEPT for comparison operations — router only extracts a single
            # "company" field, which is structurally insufficient when two
            # entities are named. Gemini's own entity/comparison_entity
            # pairing must be preserved here.
            is_comparison = raw_dict.get("operation") == "comparison"

            if company and not is_comparison:
                raw_dict["entity"] = company
            if fiscal_year and not raw_dict.get("fiscal_year"):
                raw_dict["fiscal_year"] = fiscal_year
            if quarter is not None and not raw_dict.get("quarter"):
                raw_dict["quarter"] = quarter
            raw_dict.setdefault("financial_type", financial_type)

        except Exception as e:
            logger.error("DSL Gemini call failed (attempt %d): %s", attempts, e)
            repair_hint = f"Previous call failed with error: {e}. Try again with valid JSON."
            continue

        # Validate the generated DSL
        validation = validate_dsl(raw_dict)

        if validation.valid:
            logger.info(
                "DSL valid on attempt %d | metric=%s operation=%s",
                attempts, validation.dsl_object["metric"], validation.dsl_object["operation"],
            )
            return validation.dsl_object, attempts, None

        # Invalid — check if it's recoverable
        logger.warning(
            "DSL invalid (attempt %d): %s | repair_hint: %s",
            attempts, validation.error, validation.repair_hint,
        )

        if validation.repair_hint is None:
            # Non-recoverable (e.g., metric registered but unavailable)
            # Return the error as a clean user message — no retry
            return None, attempts, validation.error

        repair_hint = validation.repair_hint

    # Exhausted retries
    return None, attempts, f"Could not generate a valid DSL after {MAX_DSL_ATTEMPTS} attempts."


# ---------------------------------------------------------------------------
# PostgreSQL execution
# ---------------------------------------------------------------------------

def _get_db_connection():
    """Open a psycopg2 connection. Caller is responsible for closing."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable not set")
    return psycopg2.connect(database_url)


def _execute_sql(
    sql: str,
    params: tuple,
    tenant_id: str,
) -> List[Dict[str, Any]]:
    """
    Execute a single parameterised SQL query with RLS tenant isolation.

    SET LOCAL app.tenant_id scopes the RLS policy to this transaction only.
    Uses DictCursor so results are accessible by column name.
    """
    conn = None
    try:
        conn = _get_db_connection()
        with conn:  # auto-commit/rollback context manager
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Set RLS tenant context — scoped to this transaction
                cur.execute(
                    "SET LOCAL app.tenant_id = %s", (str(tenant_id),)
                )
                cur.execute(sql, params)
                rows = cur.fetchall()
                return [dict(row) for row in rows]
    except psycopg2.Error as e:
        logger.error("SQL execution failed: %s | SQL: %s", e, sql[:200])
        raise
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# Derived metric computation (Python, not SQL)
# ---------------------------------------------------------------------------

def _compute_yoy_growth(
    current_rows: List[Dict],
    prior_rows: List[Dict],
    metric_label: str,
) -> Dict[str, Any]:
    """
    Compute YoY growth % from two SQL result sets.
    Returns a structured dict for the state and response generator.
    """
    if not current_rows or not prior_rows:
        return {
            "error": "Missing data for one or both periods",
            "current": None, "prior": None, "yoy_pct": None,
        }

    current_val = float(current_rows[0]["value"])
    prior_val   = float(prior_rows[0]["value"])
    current_fy  = current_rows[0]["fiscal_year"]
    prior_fy    = prior_rows[0]["fiscal_year"]

    if prior_val == 0:
        yoy_pct = None
        yoy_note = "Prior year value is zero — growth % undefined"
    else:
        yoy_pct = round((current_val - prior_val) / abs(prior_val) * 100, 2)
        yoy_note = None

    return {
        "metric": metric_label,
        "current_value": current_val,
        "current_fy": current_fy,
        "prior_value": prior_val,
        "prior_fy": prior_fy,
        "yoy_pct": yoy_pct,
        "unit": current_rows[0].get("unit", "crore_inr"),
        "note": yoy_note,
    }


def _compute_comparison(
    entity1_rows: List[Dict],
    entity2_rows: List[Dict],
    entity1: str,
    entity2: str,
    metric_label: str,
) -> Dict[str, Any]:
    """Compute comparison between two entities for the same metric/period."""
    if not entity1_rows or not entity2_rows:
        return {
            "error": f"Missing data for {'entity 1' if not entity1_rows else 'entity 2'}",
            "entity1": None, "entity2": None,
        }

    v1 = float(entity1_rows[0]["value"])
    v2 = float(entity2_rows[0]["value"])
    diff = round(v1 - v2, 2)
    diff_pct = round((v1 - v2) / abs(v2) * 100, 2) if v2 != 0 else None

    return {
        "metric": metric_label,
        "entity1": entity1, "value1": v1,
        "entity2": entity2, "value2": v2,
        "difference": diff,
        "difference_pct": diff_pct,
        "unit": entity1_rows[0].get("unit", "crore_inr"),
        "fiscal_year": entity1_rows[0].get("fiscal_year"),
    }


def _compute_cagr(rows: List[Dict], metric_label: str, entity: str) -> Dict[str, Any]:
    """Compute CAGR from multiple annual data points."""
    if len(rows) < 2:
        return {
            "error": f"Need ≥2 data points for CAGR, found {len(rows)}",
            "cagr_pct": None,
        }

    first = rows[0]
    last  = rows[-1]
    v_start = float(first["value"])
    v_end   = float(last["value"])
    n_years = len(rows) - 1

    if v_start <= 0:
        return {"error": "Starting value ≤0, CAGR undefined", "cagr_pct": None}

    cagr_pct = round(((v_end / v_start) ** (1 / n_years) - 1) * 100, 2)

    return {
        "metric": metric_label,
        "entity": entity,
        "start_fy": first["fiscal_year"],
        "end_fy": last["fiscal_year"],
        "start_value": v_start,
        "end_value": v_end,
        "n_years": n_years,
        "cagr_pct": cagr_pct,
        "unit": first.get("unit", "crore_inr"),
        "data_points": [
            {"fiscal_year": r["fiscal_year"], "value": float(r["value"])} for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# LangGraph node
# ---------------------------------------------------------------------------

def quant_engine_node(state: QueryState) -> QueryState:
    """
    Path 2: Quantitative analytics engine.

    Steps:
      1. Generate + validate DSL (with self-healing retry)
      2. Compile DSL → SQL
      3. Execute SQL against PostgreSQL with RLS
      4. Verify row counts
      5. Compute derived metrics (yoy, comparison, cagr) in Python
      6. Write dsl_object, sql_query, sql_result to state
    """
    query         = state["query"]
    tenant_id     = state["tenant_id"]
    company       = state.get("company")
    fiscal_year   = state.get("fiscal_year")
    quarter       = state.get("quarter")
    financial_type = state.get("financial_type", "consolidated")

    logger.info(
        "QuantEngine | company=%s fiscal_year=%s quarter=%s financial_type=%s",
        company, fiscal_year, quarter, financial_type,
    )

    # ── Stage 1: DSL generation ────────────────────────────────────────────
    dsl, attempts, dsl_error = _generate_dsl(
        query=query,
        company=company,
        fiscal_year=fiscal_year,
        quarter=quarter,
        financial_type=financial_type,
    )

    state["dsl_attempts"] = attempts

    if dsl is None:
        logger.error("DSL generation failed after %d attempts: %s", attempts, dsl_error)
        # Check if it's an "unavailable metric" error — give a clean user message
        is_unavailable = dsl_error and "not yet in corpus" in dsl_error
        state["error"] = "dsl_generation_failed"
        state["error_node"] = "quant_engine"
        state["response_text"] = (
            f"The metric you asked about ({dsl_error}) is registered in LedgerMind "
            f"but has not yet been extracted for this company. "
            f"Currently available metrics: {_AVAILABLE_METRICS}."
            if is_unavailable else
            f"Could not interpret your financial query as a structured data request. "
            f"Please rephrase using specific metric names like 'revenue', 'total income'. "
            f"Error: {dsl_error}"
        )
        return state

    state["dsl_object"] = dsl
    state["dsl_valid"]  = True

    # ── Stage 2: SQL compilation ───────────────────────────────────────────
    compile_result = compile_dsl(dsl, tenant_id)

    if not compile_result.success:
        logger.error("SQL compilation failed: %s", compile_result.error)
        state["error"] = "sql_compilation_failed"
        state["error_node"] = "quant_engine"
        state["response_text"] = f"Internal error during query compilation: {compile_result.error}"
        return state

    state["sql_query"] = compile_result.sql
    logger.debug("Compiled SQL: %s | params: %s", compile_result.sql[:150], compile_result.params)

    # ── Stage 3: SQL execution ─────────────────────────────────────────────
    try:
        rows = _execute_sql(compile_result.sql, compile_result.params, tenant_id)
    except Exception as e:
        logger.error("SQL execution failed: %s", e)
        state["error"] = "sql_execution_failed"
        state["error_node"] = "quant_engine"
        state["response_text"] = (
            "Database query failed. The requested data may not be available for this company/period."
        )
        return state

    state["sql_row_count"] = len(rows)
    logger.info("SQL returned %d rows for operation=%s", len(rows), compile_result.operation)

    # ── Stage 4+5: Verify + compute derived metrics ────────────────────────
    operation = compile_result.operation

    if operation == "point_in_time":
        if len(rows) == 0:
            state["error"] = "no_data_found"
            state["error_node"] = "quant_engine"
            state["response_text"] = (
                f"No data found for {dsl['entity']} {dsl['metric']} "
                f"{dsl['fiscal_year']} {dsl['financial_type']}. "
                f"This period may not yet be indexed."
            )
            return state

        if len(rows) > 1:
            # Trap 2 from blueprint: multiple rows means financial_type filter may have failed
            logger.error(
                "point_in_time returned %d rows — expected 1. "
                "Check financial_type filter. Rows: %s", len(rows), rows
            )
            state["error"] = "ambiguous_result"
            state["error_node"] = "quant_engine"
            state["response_text"] = (
                f"Ambiguous result: {len(rows)} rows returned for a single-value query. "
                f"Please specify 'consolidated' or 'standalone' explicitly."
            )
            return state

        state["sql_result"] = rows
        state["sql_verified"] = True
        state["confidence_score"] = 1.0
        state["confidence_tier"] = "high"

    elif operation == "yoy_growth":
        # Execute second SQL for prior year
        try:
            prior_rows = _execute_sql(compile_result.sql2, compile_result.params2, tenant_id)
        except Exception as e:
            logger.error("YoY prior year SQL failed: %s", e)
            prior_rows = []

        computed = _compute_yoy_growth(rows, prior_rows, compile_result.metric_label)
        state["sql_result"] = [computed]
        state["sql_verified"] = computed.get("error") is None
        state["confidence_score"] = 1.0 if state["sql_verified"] else 0.4
        state["confidence_tier"] = "high" if state["sql_verified"] else "low"

    elif operation == "comparison":
        try:
            entity2_rows = _execute_sql(compile_result.sql2, compile_result.params2, tenant_id)
        except Exception as e:
            logger.error("Comparison entity2 SQL failed: %s", e)
            entity2_rows = []

        computed = _compute_comparison(
            rows, entity2_rows,
            dsl["entity"], dsl["comparison_entity"],
            compile_result.metric_label,
        )
        state["sql_result"] = [computed]
        state["sql_verified"] = computed.get("error") is None
        state["confidence_score"] = 1.0 if state["sql_verified"] else 0.4
        state["confidence_tier"] = "high" if state["sql_verified"] else "low"

    elif operation == "cagr":
        if len(rows) < 2:
            state["error"] = "insufficient_data_for_cagr"
            state["error_node"] = "quant_engine"
            state["response_text"] = (
                f"CAGR requires at least 2 years of data. "
                f"Only {len(rows)} year(s) found for {dsl['entity']}. "
                f"Ingest more historical filings to enable CAGR computation."
            )
            return state

        computed = _compute_cagr(rows, compile_result.metric_label, dsl["entity"])
        state["sql_result"] = [computed]
        state["sql_verified"] = computed.get("error") is None
        state["confidence_score"] = 1.0 if state["sql_verified"] else 0.4
        state["confidence_tier"] = "high" if state["sql_verified"] else "low"

    logger.info(
        "QuantEngine complete | operation=%s rows=%d verified=%s",
        operation, len(rows), state["sql_verified"],
    )

    return state