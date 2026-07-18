"""
LedgerMind — Phase 4: DSL Compiler
=====================================
The safety boundary between LLM output and PostgreSQL execution.

The LLM (in quant_engine.py) produces a JSON dict matching DSLObject.
This file:
  1. Validates that dict against controlled registries (DSLValidator)
  2. Compiles the validated object into parameterised SQL (SQLCompiler)

The LLM never writes SQL. The LLM never sees the schema.
This file is the only thing that touches SQL construction.

METRIC_REGISTRY and METRIC_ALIASES below are now DERIVED from the single
shared registry at app/metrics/registry.py instead of being hand-maintained
here. See that file's module docstring for the full rationale and bug
history this consolidation fixes.

"available" here reflects metric_type only (raw=True, derived=False) — NOT
per-company/per-period corpus extraction state. A raw metric being
"available" means the DSL Compiler will issue SQL for it and let a
zero-row result mean "not found for this company/period" (handled in
quant_engine.py's no_data_found path). A derived metric being registered
here documents intent to support it once formula-compilation exists (see
registry.py's NOT YET SUPPORTED section) — it is not yet queryable.

Operation types:
  point_in_time  — single value for one entity/period
  yoy_growth     — this year vs last year (two SQL reads, % computed in Python)
  comparison     — two entities, same period (two SQL reads)
  cagr           — multiple years (Python post-SQL, needs ≥2 data points)
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.engines.state import DSLObject
from app.metrics.registry import dsl_registry, dsl_alias_pairs

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Metric Registry — derived from app/metrics/registry.py
# Maps DSL metric name → financials table metric column value
# Only metrics in this registry can be queried via Path 2.
# ---------------------------------------------------------------------------

METRIC_REGISTRY: Dict[str, Dict[str, Any]] = dsl_registry()

METRIC_ALIASES: Dict[str, str] = dsl_alias_pairs()

# ---------------------------------------------------------------------------
# Operation Registry
# ---------------------------------------------------------------------------

OPERATION_REGISTRY: Dict[str, str] = {
    "point_in_time": "Single period value for one entity",
    "yoy_growth":    "Year-over-year % change (requires fiscal_year in DSL)",
    "comparison":    "Two entities, same period (requires comparison_entity)",
    "cagr":          "Compound annual growth rate (requires multiple periods)",
}

# Valid financial_type values
VALID_FINANCIAL_TYPES = {"consolidated", "standalone"}

# ---------------------------------------------------------------------------
# Validation result types
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    valid: bool
    dsl_object: Optional[DSLObject] = None
    error: Optional[str] = None
    repair_hint: Optional[str] = None   # sent back to LLM in self-healing loop


@dataclass
class CompileResult:
    success: bool
    sql: Optional[str] = None
    params: Optional[tuple] = None      # psycopg2 parameterised values
    operation: Optional[str] = None     # which operation was compiled
    metric_label: Optional[str] = None  # human-readable metric name
    error: Optional[str] = None
    # For yoy_growth and comparison: second SQL needed
    sql2: Optional[str] = None
    params2: Optional[tuple] = None


# ---------------------------------------------------------------------------
# DSL Validator
# ---------------------------------------------------------------------------

class DSLValidator:
    """
    Validates a raw dict from the LLM against the metric and operation registries.
    Validation order:
      1. Required field presence
      2. financial_type is valid
      3. metric resolves (with alias expansion)
      4. metric is available in corpus
      5. operation is valid
      6. operation-specific required fields present
    """

    def validate(self, raw: Dict[str, Any]) -> ValidationResult:
        # ── 1. Required field presence ─────────────────────────────────────
        required = ["metric", "entity", "fiscal_year", "financial_type", "operation"]
        missing = [f for f in required if not raw.get(f)]
        if missing:
            return ValidationResult(
                valid=False,
                error=f"Missing required DSL fields: {missing}",
                repair_hint=(
                    f"Your DSL response is missing these required fields: {missing}. "
                    f"You must include all of: metric, entity, fiscal_year, "
                    f"financial_type, operation. Return only the JSON object."
                ),
            )

        # ── 2. financial_type ─────────────────────────────────────────────
        ft = raw["financial_type"].lower().strip()
        if ft not in VALID_FINANCIAL_TYPES:
            return ValidationResult(
                valid=False,
                error=f"Invalid financial_type: '{ft}'",
                repair_hint=(
                    f"financial_type must be exactly 'consolidated' or 'standalone'.\n"
                    f"You provided '{ft}'.\nDefault to 'consolidated' if unsure."
                ),
            )

        # ── 3. Metric resolution (alias expansion) ────────────────────────
        raw_metric = raw["metric"].lower().strip()
        resolved_metric = METRIC_ALIASES.get(raw_metric, raw_metric)

        if resolved_metric not in METRIC_REGISTRY:
            available = list(METRIC_REGISTRY.keys())
            return ValidationResult(
                valid=False,
                error=f"Unknown metric: '{raw_metric}'",
                repair_hint=(
                    f"The metric '{raw_metric}' is not in the metric registry. "
                    f"Available metrics: {available}. "
                    f"Choose the closest match from this list."
                ),
            )

        # ── 4. Metric availability ────────────────────────────────────────
        metric_def = METRIC_REGISTRY[resolved_metric]
        if not metric_def["available"]:
            return ValidationResult(
                valid=False,
                error=f"Metric '{resolved_metric}' is registered but not yet in corpus",
                repair_hint=None,  # Not a DSL error — clean user-facing response needed
            )

        # ── 5. Operation validation ───────────────────────────────────────
        operation = raw["operation"].lower().strip()
        if operation not in OPERATION_REGISTRY:
            return ValidationResult(
                valid=False,
                error=f"Unknown operation: '{operation}'",
                repair_hint=(
                    f"operation must be one of: {list(OPERATION_REGISTRY.keys())}.\n"
                    f"You provided '{operation}'."
                ),
            )

        # ── 6. Operation-specific field checks ────────────────────────────
        if operation == "comparison" and not raw.get("comparison_entity"):
            return ValidationResult(
                valid=False,
                error="operation='comparison' requires comparison_entity",
                repair_hint=(
                    "For a comparison operation you must provide 'comparison_entity' "
                    "with the second company's ticker (e.g. 'PAYTM')."
                ),
            )

        # ADD — reject self-comparisons
        if operation == "comparison" and raw.get("comparison_entity"):
            primary_resolved = raw["entity"].upper().strip()
            comp_resolved = raw["comparison_entity"].upper().strip()
            
            if primary_resolved == comp_resolved:
                return ValidationResult(
                    valid=False,
                    error=f"comparison_entity resolved to the same company as entity "
                          f"('{primary_resolved}') — comparison requires two distinct entities.",
                    repair_hint=(
                        "A comparison requires two different entities. You provided the same "
                        "company for both. This may mean the requested comparison spans two "
                        "different periods per entity, which is not yet supported."
                    ),
                )

        if operation == "yoy_growth" and not raw.get("fiscal_year"):
            return ValidationResult(
                valid=False,
                error="operation='yoy_growth' requires fiscal_year",
                repair_hint=(
                    "For yoy_growth you must provide 'fiscal_year' "
                    "(e.g. 'FY26'). The previous year is inferred automatically."
                ),
            )

        # ── All checks passed — build DSLObject ───────────────────────────
        dsl = DSLObject(
            metric=resolved_metric,
            entity=raw["entity"].upper().strip(),
            period=raw.get("period", raw["fiscal_year"]),
            fiscal_year=raw["fiscal_year"].upper().strip(),
            quarter=raw.get("quarter"),          # None = annual
            financial_type=ft,
            operation=operation,
            comparison_entity=(
                raw["comparison_entity"].upper().strip()
                if raw.get("comparison_entity") else None
            ),
            comparison_period=raw.get("comparison_period"),
        )

        logger.info(
            "DSL validated | metric=%s entity=%s fiscal_year=%s quarter=%s "
            "financial_type=%s operation=%s",
            dsl["metric"], dsl["entity"], dsl["fiscal_year"],
            dsl["quarter"], dsl["financial_type"], dsl["operation"],
        )

        return ValidationResult(valid=True, dsl_object=dsl)


# ---------------------------------------------------------------------------
# SQL Compiler
# ---------------------------------------------------------------------------

class SQLCompiler:
    """
    Compiles a validated DSLObject into parameterised SQL.
    Rules:
      - Never string-interpolate user values — always use %s placeholders.
      - Always include financial_type in WHERE (Trap 2 from blueprint §25B).
      - Always include is_latest = TRUE.
      - Always include tenant_id.
      - For point_in_time: expect exactly 1 row — zero or multiple triggers
        self-healing in quant_engine.
    """

    def compile(
        self,
        dsl: DSLObject,
        tenant_id: str,
    ) -> CompileResult:
        operation = dsl["operation"]

        if operation == "point_in_time":
            return self._compile_point_in_time(dsl, tenant_id)
        elif operation == "yoy_growth":
            return self._compile_yoy_growth(dsl, tenant_id)
        elif operation == "comparison":
            return self._compile_comparison(dsl, tenant_id)
        elif operation == "cagr":
            return self._compile_cagr(dsl, tenant_id)
        else:
            return CompileResult(
                success=False,
                error=f"No SQL compiler implementation for operation: {operation}",
            )

    def _base_select(self, dsl: DSLObject, tenant_id: str) -> Tuple[str, List]:
        """
        Shared WHERE clause builder for single-entity queries.
        Returns (where_clause_sql, params_list).
        quarter=None means annual — we filter WHERE quarter IS NULL.
        """
        db_metric = dsl["metric"]

        sql = """
            SELECT
                value,
                metric,
                fiscal_year,
                quarter,
                financial_type,
                filing_date,
                unit,
                doc_id
            FROM financials
            WHERE tenant_id = %s
              AND company   = %s
              AND metric    = %s
              AND fiscal_year = %s
              AND financial_type = %s
              AND is_latest = TRUE
        """
        params = [
            tenant_id,
            dsl["entity"],
            db_metric,
            dsl["fiscal_year"],
            dsl["financial_type"],
        ]

        # Quarter handling: if quarter specified → filter by it
        # If quarter is None → filter for annual rows (WHERE quarter IS NULL)
        if dsl["quarter"] is not None:
            sql += "  AND quarter = %s\n"
            params.append(dsl["quarter"])
        else:
            sql += "  AND quarter IS NULL\n"

        return sql.strip(), params

    def _compile_point_in_time(
        self, dsl: DSLObject, tenant_id: str
    ) -> CompileResult:
        sql, params = self._base_select(dsl, tenant_id)
        metric_def = METRIC_REGISTRY[dsl["metric"]]

        logger.debug("Compiled point_in_time SQL for metric=%s entity=%s",
                     dsl["metric"], dsl["entity"])

        return CompileResult(
            success=True,
            sql=sql,
            params=tuple(params),
            operation="point_in_time",
            metric_label=metric_def["label"],
        )

    def _compile_yoy_growth(
        self, dsl: DSLObject, tenant_id: str
    ) -> CompileResult:
        """
        Two SQL reads: current fiscal_year and (fiscal_year - 1).
        YoY % = (current - prior) / prior * 100
        Computed in Python in quant_engine after both reads.
        """
        # Infer prior fiscal year: "FY26" → "FY25"
        try:
            year_num = int(dsl["fiscal_year"].replace("FY", ""))
            prior_fiscal_year = f"FY{year_num - 1}"
        except ValueError:
            return CompileResult(
                success=False,
                error=f"Cannot infer prior year from fiscal_year: {dsl['fiscal_year']}",
            )

        sql, params = self._base_select(dsl, tenant_id)

        # Prior year — same query but different fiscal_year
        prior_dsl = dict(dsl)
        prior_dsl["fiscal_year"] = prior_fiscal_year
        sql2, params2 = self._base_select(DSLObject(**prior_dsl), tenant_id)

        logger.debug(
            "Compiled yoy_growth SQL | current=%s prior=%s",
            dsl["fiscal_year"], prior_fiscal_year,
        )

        return CompileResult(
            success=True,
            sql=sql,
            params=tuple(params),
            sql2=sql2,
            params2=tuple(params2),
            operation="yoy_growth",
            metric_label=METRIC_REGISTRY[dsl["metric"]]["label"],
        )

    def _compile_comparison(
        self, dsl: DSLObject, tenant_id: str
    ) -> CompileResult:
        """
        Two SQL reads: primary entity and comparison_entity, same period.
        Difference and % gap computed in Python in quant_engine.
        """
        if not dsl["comparison_entity"]:
            return CompileResult(
                success=False,
                error="comparison_entity is required for operation=comparison",
            )

        sql, params = self._base_select(dsl, tenant_id)

        # Second entity — same metric/period/financial_type
        comp_dsl = dict(dsl)
        comp_dsl["entity"] = dsl["comparison_entity"]
        sql2, params2 = self._base_select(DSLObject(**comp_dsl), tenant_id)

        logger.debug(
            "Compiled comparison SQL | entity1=%s entity2=%s",
            dsl["entity"], dsl["comparison_entity"],
        )

        return CompileResult(
            success=True,
            sql=sql,
            params=tuple(params),
            sql2=sql2,
            params2=tuple(params2),
            operation="comparison",
            metric_label=METRIC_REGISTRY[dsl["metric"]]["label"],
        )

    def _compile_cagr(
        self, dsl: DSLObject, tenant_id: str
    ) -> CompileResult:
        """
        CAGR requires all available data points for the entity/metric.
        Returns all fiscal years sorted ascending — Python computes CAGR.
        Requires at least 2 data points; enforced in quant_engine verification.
        """
        db_metric = dsl["metric"]

        sql = """
            SELECT
                value,
                fiscal_year,
                quarter,
                financial_type,
                filing_date,
                unit
            FROM financials
            WHERE tenant_id = %s
              AND company   = %s
              AND metric    = %s
              AND financial_type = %s
              AND is_latest = TRUE
              AND quarter IS NULL
            ORDER BY fiscal_year ASC
        """
        params = (
            tenant_id,
            dsl["entity"],
            db_metric,
            dsl["financial_type"],
        )

        logger.debug("Compiled CAGR SQL for entity=%s metric=%s", dsl["entity"], dsl["metric"])

        return CompileResult(
            success=True,
            sql=sql,
            params=params,
            operation="cagr",
            metric_label=METRIC_REGISTRY[dsl["metric"]]["label"],
        )


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

validator = DSLValidator()
compiler = SQLCompiler()


# ---------------------------------------------------------------------------
# Public interface used by quant_engine.py
# ---------------------------------------------------------------------------

def validate_dsl(raw_dict: Dict[str, Any]) -> ValidationResult:
    """Validate a raw LLM-generated dict.
    Returns ValidationResult."""
    return validator.validate(raw_dict)


def compile_dsl(dsl: DSLObject, tenant_id: str) -> CompileResult:
    """Compile a validated DSLObject to SQL.
    Returns CompileResult."""
    return compiler.compile(dsl, tenant_id)


def resolve_metric_alias(raw_metric: str) -> Optional[str]:
    """
    Resolve a raw metric string to its canonical name.
    Returns None if not found in registry (even after alias expansion).
    Used by the router to detect quantitative queries without calling the LLM.
    """
    lower = raw_metric.lower().strip()
    resolved = METRIC_ALIASES.get(lower, lower)
    return resolved if resolved in METRIC_REGISTRY else None