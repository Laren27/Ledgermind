"""
LedgerMind — Phase 4: DSL Compiler
=====================================
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.engines.state import DSLObject
from app.metrics.registry import dsl_registry, dsl_alias_pairs

logger = logging.getLogger(__name__)

METRIC_REGISTRY: Dict[str, Dict[str, Any]] = dsl_registry()
METRIC_ALIASES: Dict[str, str] = dsl_alias_pairs()

OPERATION_REGISTRY: Dict[str, str] = {
    "point_in_time":     "Single period value for one entity",
    "yoy_growth":        "Year-over-year % change (requires fiscal_year in DSL)",
    "comparison":        "Two entities, same period (requires comparison_entity)",
    "cagr":              "Compound annual growth rate (requires multiple periods)",
    "growth_comparison": "Compares YoY growth rate between two entities (requires comparison_entity + fiscal_year)",
}

VALID_FINANCIAL_TYPES = {"consolidated", "standalone"}


@dataclass
class ValidationResult:
    valid: bool
    dsl_object: Optional[DSLObject] = None
    error: Optional[str] = None
    repair_hint: Optional[str] = None


@dataclass
class CompileResult:
    success: bool
    error: Optional[str] = None
    operation: Optional[str] = None
    metric_label: Optional[str] = None
    queries: List[Tuple[str, tuple]] = field(default_factory=list)
    sql: Optional[str] = None
    params: Optional[tuple] = None
    sql2: Optional[str] = None
    params2: Optional[tuple] = None

    def __post_init__(self):
        if not self.queries:
            if self.sql and self.params is not None:
                self.queries.append((self.sql, self.params))
            if self.sql2 and self.params2 is not None:
                self.queries.append((self.sql2, self.params2))


class DSLValidator:
    def validate(self, raw: Dict[str, Any], preferred_operation: Optional[str] = None) -> ValidationResult:
        required = ["metric", "entity", "fiscal_year", "financial_type", "operation"]
        missing = [f for f in required if not raw.get(f)]
        if missing:
            return ValidationResult(
                valid=False,
                error=f"Missing required DSL fields: {missing}",
                repair_hint=f"Your DSL response is missing required fields: {missing}. Return only JSON.",
            )

        ft = raw["financial_type"].lower().strip()
        if ft not in VALID_FINANCIAL_TYPES:
            return ValidationResult(
                valid=False,
                error=f"Invalid financial_type: '{ft}'",
                repair_hint="financial_type must be exactly 'consolidated' or 'standalone'.",
            )

        raw_metric = raw["metric"].lower().strip()
        resolved_metric = METRIC_ALIASES.get(raw_metric, raw_metric)

        if resolved_metric not in METRIC_REGISTRY:
            return ValidationResult(
                valid=False,
                error=f"Unknown metric: '{raw_metric}'",
                repair_hint=f"Metric '{raw_metric}' not in registry. Available: {list(METRIC_REGISTRY.keys())}.",
            )

        metric_def = METRIC_REGISTRY[resolved_metric]
        if not metric_def["available"]:
            return ValidationResult(
                valid=False,
                error=f"Metric '{resolved_metric}' is registered but not yet in corpus",
                repair_hint=None,
            )

        # ── ⚡ PROGRAMMATIC OPERATION OVERRIDE (Load-Bearing Guardrail) ──
        if preferred_operation and preferred_operation in OPERATION_REGISTRY:
            current_op = raw.get("operation", "").lower().strip()
            if current_op != preferred_operation:
                logger.info(
                    "⚡ DSL Validator: Overriding LLM chosen operation '%s' -> forcing preferred '%s'",
                    current_op, preferred_operation
                )
                raw["operation"] = preferred_operation

        operation = raw["operation"].lower().strip()
        if operation not in OPERATION_REGISTRY:
            return ValidationResult(
                valid=False,
                error=f"Unknown operation: '{operation}'",
                repair_hint=f"operation must be one of: {list(OPERATION_REGISTRY.keys())}.",
            )

        if operation == "point_in_time":
            comp_period = raw.get("comparison_period")
            fy = raw.get("fiscal_year")
            if comp_period and comp_period.upper().strip() != fy.upper().strip():
                return ValidationResult(
                    valid=False,
                    error="operation='point_in_time' with distinct comparison_period spans two periods — use yoy_growth.",
                    repair_hint="If comparing across two years for the same entity, use operation='yoy_growth'.",
                )

        if operation == "comparison" and not raw.get("comparison_entity"):
            return ValidationResult(
                valid=False,
                error="operation='comparison' requires comparison_entity",
                repair_hint="Provide 'comparison_entity' with the second company's ticker.",
            )

        if operation == "comparison" and raw.get("comparison_entity"):
            if raw["entity"].upper().strip() == raw["comparison_entity"].upper().strip():
                return ValidationResult(
                    valid=False,
                    error="comparison_entity resolved to the same company as primary entity.",
                    repair_hint="A comparison requires two different entities.",
                )

        if operation == "yoy_growth" and not raw.get("fiscal_year"):
            return ValidationResult(
                valid=False,
                error="operation='yoy_growth' requires fiscal_year",
                repair_hint="Provide 'fiscal_year' (e.g. 'FY26'). Prior year is inferred automatically.",
            )

        if operation == "growth_comparison":
            if not raw.get("comparison_entity"):
                return ValidationResult(
                    valid=False,
                    error="operation='growth_comparison' requires comparison_entity",
                    repair_hint="Provide 'comparison_entity' with the second company's ticker.",
                )
            if not raw.get("fiscal_year"):
                return ValidationResult(
                    valid=False,
                    error="operation='growth_comparison' requires fiscal_year",
                    repair_hint="Provide 'fiscal_year'. Prior year is inferred automatically for both entities.",
                )
            if raw["entity"].upper().strip() == raw["comparison_entity"].upper().strip():
                return ValidationResult(
                    valid=False,
                    error="growth_comparison requires two different entities.",
                    repair_hint="Provide distinct tickers for entity and comparison_entity.",
                )

        dsl = DSLObject(
            metric=resolved_metric,
            entity=raw["entity"].upper().strip(),
            period=raw.get("period", raw["fiscal_year"]),
            fiscal_year=raw["fiscal_year"].upper().strip(),
            quarter=raw.get("quarter"),
            financial_type=ft,
            operation=operation,
            comparison_entity=raw["comparison_entity"].upper().strip() if raw.get("comparison_entity") else None,
            comparison_period=raw.get("comparison_period"),
        )

        return ValidationResult(valid=True, dsl_object=dsl)


class SQLCompiler:
    def compile(self, dsl: DSLObject, tenant_id: str) -> CompileResult:
        operation = dsl["operation"]
        if operation == "point_in_time":
            return self._compile_point_in_time(dsl, tenant_id)
        elif operation == "yoy_growth":
            return self._compile_yoy_growth(dsl, tenant_id)
        elif operation == "comparison":
            return self._compile_comparison(dsl, tenant_id)
        elif operation == "cagr":
            return self._compile_cagr(dsl, tenant_id)
        elif operation == "growth_comparison":
            return self._compile_growth_comparison(dsl, tenant_id)
        return CompileResult(success=False, error=f"No compiler for operation: {operation}")

    def _base_select(self, dsl: DSLObject, tenant_id: str) -> Tuple[str, List]:
        sql = """
            SELECT value, metric, fiscal_year, quarter, financial_type, filing_date, unit, doc_id
            FROM financials
            WHERE tenant_id = %s AND company = %s AND metric = %s AND fiscal_year = %s AND financial_type = %s AND is_latest = TRUE
        """
        params = [tenant_id, dsl["entity"], dsl["metric"], dsl["fiscal_year"], dsl["financial_type"]]
        if dsl["quarter"] is not None:
            sql += "  AND quarter = %s\n"
            params.append(dsl["quarter"])
        else:
            sql += "  AND quarter IS NULL\n"
        return sql.strip(), params

    def _compile_point_in_time(self, dsl: DSLObject, tenant_id: str) -> CompileResult:
        sql, params = self._base_select(dsl, tenant_id)
        return CompileResult(
            success=True, sql=sql, params=tuple(params), operation="point_in_time",
            metric_label=METRIC_REGISTRY[dsl["metric"]]["label"],
        )

    def _compile_yoy_growth(self, dsl: DSLObject, tenant_id: str) -> CompileResult:
        try:
            year_num = int(dsl["fiscal_year"].replace("FY", ""))
            prior_fy = f"FY{year_num - 1}"
        except ValueError:
            return CompileResult(success=False, error=f"Cannot infer prior year from: {dsl['fiscal_year']}")

        sql, params = self._base_select(dsl, tenant_id)
        prior_dsl = dict(dsl)
        prior_dsl["fiscal_year"] = prior_fy
        sql2, params2 = self._base_select(DSLObject(**prior_dsl), tenant_id)

        return CompileResult(
            success=True, sql=sql, params=tuple(params), sql2=sql2, params2=tuple(params2),
            operation="yoy_growth", metric_label=METRIC_REGISTRY[dsl["metric"]]["label"],
        )

    def _compile_comparison(self, dsl: DSLObject, tenant_id: str) -> CompileResult:
        if not dsl["comparison_entity"]:
            return CompileResult(success=False, error="comparison_entity required for comparison")

        sql, params = self._base_select(dsl, tenant_id)
        comp_dsl = dict(dsl)
        comp_dsl["entity"] = dsl["comparison_entity"]
        sql2, params2 = self._base_select(DSLObject(**comp_dsl), tenant_id)

        return CompileResult(
            success=True, sql=sql, params=tuple(params), sql2=sql2, params2=tuple(params2),
            operation="comparison", metric_label=METRIC_REGISTRY[dsl["metric"]]["label"],
        )

    def _compile_cagr(self, dsl: DSLObject, tenant_id: str) -> CompileResult:
        sql = """
            SELECT value, fiscal_year, quarter, financial_type, filing_date, unit
            FROM financials
            WHERE tenant_id = %s AND company = %s AND metric = %s AND financial_type = %s AND is_latest = TRUE AND quarter IS NULL
            ORDER BY fiscal_year ASC
        """
        params = (tenant_id, dsl["entity"], dsl["metric"], dsl["financial_type"])
        return CompileResult(
            success=True, sql=sql, params=params, operation="cagr",
            metric_label=METRIC_REGISTRY[dsl["metric"]]["label"],
        )

    def _compile_growth_comparison(self, dsl: DSLObject, tenant_id: str) -> CompileResult:
        try:
            year_num = int(dsl["fiscal_year"].replace("FY", ""))
            prior_fy = f"FY{year_num - 1}"
        except ValueError:
            return CompileResult(success=False, error=f"Cannot infer prior year from: {dsl['fiscal_year']}")

        sql_a_curr, params_a_curr = self._base_select(dsl, tenant_id)
        dsl_a_prior = dict(dsl)
        dsl_a_prior["fiscal_year"] = prior_fy
        sql_a_prior, params_a_prior = self._base_select(DSLObject(**dsl_a_prior), tenant_id)

        dsl_b_curr = dict(dsl)
        dsl_b_curr["entity"] = dsl["comparison_entity"]
        sql_b_curr, params_b_curr = self._base_select(DSLObject(**dsl_b_curr), tenant_id)
        dsl_b_prior = dict(dsl_b_curr)
        dsl_b_prior["fiscal_year"] = prior_fy
        sql_b_prior, params_b_prior = self._base_select(DSLObject(**dsl_b_prior), tenant_id)

        return CompileResult(
            success=True, operation="growth_comparison", metric_label=METRIC_REGISTRY[dsl["metric"]]["label"],
            queries=[
                (sql_a_curr, tuple(params_a_curr)),
                (sql_a_prior, tuple(params_a_prior)),
                (sql_b_curr, tuple(params_b_curr)),
                (sql_b_prior, tuple(params_b_prior)),
            ],
        )


validator = DSLValidator()
compiler = SQLCompiler()


def validate_dsl(raw_dict: Dict[str, Any], preferred_operation: Optional[str] = None) -> ValidationResult:
    return validator.validate(raw_dict, preferred_operation=preferred_operation)


def compile_dsl(dsl: DSLObject, tenant_id: str) -> CompileResult:
    return compiler.compile(dsl, tenant_id)


def resolve_metric_alias(raw_metric: str) -> Optional[str]:
    lower = raw_metric.lower().strip()
    resolved = METRIC_ALIASES.get(lower, lower)
    return resolved if resolved in METRIC_REGISTRY else None