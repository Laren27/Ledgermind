"""
LedgerMind — Shared Metric Registry
=====================================
Single source of truth for every financial metric LedgerMind knows about.

WHY THIS FILE EXISTS
---------------------
Prior to this refactor, metric definitions were split across three independently
hand-maintained dicts:
  - entity_resolver.py  METRIC_ALIASES   (ingestion-side: label text -> canonical name)
  - dsl_compiler.py     METRIC_REGISTRY  (query-side: which metrics are DSL-queryable)
  - quant_engine.py     ALIASES          (prompt-side: prose aliases for Gemini)

Every one of the following real, shipped bugs was a direct consequence of that split:
  - profit_before_tax was entirely absent from dsl_compiler's METRIC_REGISTRY,
    so Gemini had no correct option and silently substituted "pat" instead.
  - exceptional_items collapsed three distinct line items (OCI FX translation,
    OCI remeasurement of defined benefit plans, PPE disposal gain/loss) into one
    canonical name in entity_resolver, causing a genuinely-blank cell to be
    silently backfilled by an unrelated row's value.
  - Titan's segment revenue (Watches/Jewellery/Eyecare/Others) had no canonical
    home in any registry and fell through as unmapped.

This file is the only place a metric is defined. All three consumers above now
import from here and derive their own view of the data instead of maintaining
a parallel copy.

SCHEMA VS. STATE
-----------------
This registry defines *semantics*: canonical name, known aliases (including
OCR-mangled variants), whether a metric is a direct P&L/BS line item or must be
computed from other metrics, and whether it's queryable via the DSL/SQL path
at all (some canonical names below exist purely to give ingestion a correct
dedup target — e.g. OCI sub-lines — and are intentionally not user-queryable).

This file deliberately does NOT track whether a given metric has actually been
extracted for a given company/period. That is data state, not schema, and
belongs at query time: for `metric_type="raw"` metrics, the DSL compiler
issues the SQL and lets a zero-row result mean "not available for this
company/period" (already handled by quant_engine.py's no_data_found path). For
`metric_type="derived"` metrics, no SQL formula-compilation exists yet (see
NOT YET SUPPORTED below) — this is real, separate work, not something a
registry field can paper over.

NOT YET SUPPORTED (Future Phase, not silently implied by this file)
---------------------------------------------------------------------
`metric_type="derived"` metrics (ebitda, gross_profit, operating_expenses)
have a human-readable `derivation_formula` for documentation purposes only.
There is currently no SQL Compiler support for computing a derived metric from
its component raw metrics. Until that compiler work exists, derived metrics
correctly return a clean "not yet available" DSL response — same user-facing
behavior as before this refactor, just now expressed honestly as a capability
gap rather than a hardcoded corpus-availability flag.
"""

from dataclasses import dataclass, field
from typing import Literal

MetricType = Literal["raw", "derived"]


@dataclass(frozen=True)
class MetricDefinition:
    canonical_name: str
    aliases: tuple[str, ...]
    metric_type: MetricType
    dsl_enabled: bool
    label: str
    prompt_aliases: str = ""          # short prose for Gemini's DSL-generation prompt
    prompt_warning: str | None = None  # disambiguation note, e.g. PBT vs PAT
    derivation_formula: str | None = None  # human-readable only, no compiler support yet


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

ALL_METRICS: tuple[MetricDefinition, ...] = (

    # ── Revenue family ──────────────────────────────────────────────────
    MetricDefinition(
        canonical_name="revenue",
        aliases=(
            "revenue", "revenue from operations", "total income from operations",
            "income from operations", "operating revenue", "net revenue",
            "gross revenue", "sales", "net sales", "turnover", "total revenue",
            "top line", "sale of products", "sale of services", "standalone revenue",
        ),
        metric_type="raw", dsl_enabled=True, label="Revenue",
        prompt_aliases="revenue from operations, top line, turnover",
    ),
    MetricDefinition(
        canonical_name="other_operating_revenue",
        aliases=("other operating revenue", "other operating revenues"),
        metric_type="raw", dsl_enabled=True, label="Other Operating Revenue",
        prompt_aliases="other operating revenue, secondary revenue line",
        prompt_warning="Distinct from 'revenue' — this is a SEPARATE sub-line some companies report alongside main revenue.",
    ),
    MetricDefinition(
        canonical_name="gmv",
        aliases=("gmv", "gross merchandise value"),
        metric_type="raw", dsl_enabled=False, label="Gross Merchandise Value",
    ),
    MetricDefinition(
        canonical_name="gov",
        aliases=("gov", "gross order value"),
        metric_type="raw", dsl_enabled=False, label="Gross Order Value",
    ),

    # ── Income ───────────────────────────────────────────────────────────
    MetricDefinition(
        canonical_name="total_income",
        aliases=("total income", "total income i+ii", "ill total incomc 1+11"),
        metric_type="raw", dsl_enabled=True, label="Total Income",
        prompt_aliases="total income, revenue plus other income",
    ),
    MetricDefinition(
        canonical_name="other_income",
        aliases=("other income", "non operating income", "interest income"),
        metric_type="raw", dsl_enabled=True, label="Other Income",
        prompt_aliases="other income, non-operating income",
        prompt_warning="'other income' maps here — NOT to total_income.",
    ),

    # ── Costs / expenses ─────────────────────────────────────────────────
    MetricDefinition(
        canonical_name="cost_of_materials_consumed",
        aliases=("cost of materials consumed", "raw material consumed",
                  "cost of materials and components consumed"),
        metric_type="raw", dsl_enabled=False, label="Cost of Materials Consumed",
    ),
    MetricDefinition(
        canonical_name="purchases_of_stock_in_trade",
        aliases=("purchases of stock-in-trade",),
        metric_type="raw", dsl_enabled=True, label="Purchases of Stock-in-Trade",
    ),
    MetricDefinition(
        canonical_name="changes_in_inventories",
        aliases=("changes in inventories",),
        metric_type="raw", dsl_enabled=False, label="Changes in Inventories",
    ),
    MetricDefinition(
        canonical_name="employee_benefits_expense",
        aliases=("employee benefits expense", "employee cost", "staff cost", "salary expense"),
        metric_type="raw", dsl_enabled=True, label="Employee Benefits Expense",
        prompt_aliases="employee benefits, staff costs, people costs, salaries",
    ),
    MetricDefinition(
        canonical_name="finance_costs",
        aliases=("finance costs", "interest expense", "borrowing costs"),
        metric_type="raw", dsl_enabled=True, label="Finance Costs",
        prompt_aliases="finance costs, interest expense, borrowing costs",
    ),
    MetricDefinition(
        canonical_name="depreciation",
        aliases=(
            "depreciation", "depreciation and amortization expenses",
            "depreciation_and_amortisation_expense", "depreciation_and_amortization_expense",
            "depreciation_and_amortisation", "depreciation_and_amortization",
            "depreciation_expense", "amortisation_expense", "amortization_expense",
            "depreciation_and_amortisation_expenses", "depreciation_and_amortization_expenses",
            "depreciation_expenses", "depreciation and amortisation", "depreciation and amortization",
            "d&a", "da",
        ),
        metric_type="raw", dsl_enabled=True, label="Depreciation & Amortisation",
        prompt_aliases="depreciation, amortisation, D&A",
    ),
    MetricDefinition(
        canonical_name="advertising",
        aliases=("advertising", "marketing expense"),
        metric_type="raw", dsl_enabled=True, label="Advertising & Marketing",
        prompt_aliases="advertising, ad spend, marketing, sales promotion",
    ),
    MetricDefinition(
        canonical_name="advertisement_and_sales_promotion",
        aliases=("advertisement and sales promotion", "advertising and sales promotion"),
        metric_type="raw", dsl_enabled=True, label="Advertisement & Sales Promotion",
    ),
    MetricDefinition(
        canonical_name="other_expenses",
        aliases=("other expenses",),
        metric_type="raw", dsl_enabled=False, label="Other Expenses",
    ),
    MetricDefinition(
        canonical_name="total_expenses",
        aliases=("total expenses",),
        metric_type="raw", dsl_enabled=True, label="Total Expenses",
        prompt_aliases="total expenses, total costs, operating costs",
    ),
    MetricDefinition(
        canonical_name="delivery_and_related_charges",
        aliases=("delivery and related charges", "delivery and related charges n4"),
        metric_type="raw", dsl_enabled=True, label="Delivery and Related Charges",
        prompt_aliases="delivery charges, logistics costs, fulfillment costs",
    ),
    MetricDefinition(
        canonical_name="share_based_payment_expense",
        aliases=("share-based payment expense", "share based payment expense"),
        metric_type="raw", dsl_enabled=False, label="Share-Based Payment Expense",
    ),

    # ── Profitability ────────────────────────────────────────────────────
    MetricDefinition(
        canonical_name="ebit",
        aliases=("ebit",),
        metric_type="raw", dsl_enabled=False, label="EBIT",
    ),
    MetricDefinition(
        canonical_name="ebitda",
        aliases=("ebitda", "operating ebitda"),
        metric_type="derived", dsl_enabled=True, label="EBITDA",
        derivation_formula="operating_profit + depreciation + amortization",
    ),
    MetricDefinition(
        canonical_name="adjusted_ebitda",
        aliases=("adjusted ebitda",),
        metric_type="derived", dsl_enabled=False, label="Adjusted EBITDA",
    ),
    MetricDefinition(
        canonical_name="operating_profit",
        aliases=("operating profit",),
        metric_type="raw", dsl_enabled=False, label="Operating Profit",
    ),
    MetricDefinition(
        canonical_name="gross_profit",
        aliases=("gross profit",),
        metric_type="derived", dsl_enabled=True, label="Gross Profit",
        derivation_formula="revenue - cost_of_materials_consumed",
    ),
    MetricDefinition(
        canonical_name="operating_expenses",
        aliases=("operating expenses",),
        metric_type="derived", dsl_enabled=True, label="Operating Expenses",
        derivation_formula="total_expenses - finance_costs - depreciation",
    ),
    MetricDefinition(
        canonical_name="profit_before_exceptional_items",
        aliases=(
            "profit before exceptional items and tax",
            "profit before share of profit of an associate and a joint venture exceptional items and tax",
            "profit/(loss) before share of profit/(loss) of associates/joint ventures exceptional items and tax",
            "profit/(loss) before exceptional items and tax",
            "profit before exceptional items",
        ),
        metric_type="raw", dsl_enabled=False, label="Profit Before Exceptional Items",
    ),
    MetricDefinition(
        canonical_name="profit_before_tax",
        aliases=("profit before tax", "profit/(loss) before tax", "pbt", "profit before taxes"),
        metric_type="raw", dsl_enabled=True, label="Profit Before Tax",
        prompt_aliases="profit before tax, PBT (BEFORE tax is deducted — do NOT confuse with PAT)",
        prompt_warning="PBT and PAT are DIFFERENT numbers; PBT is measured BEFORE tax is subtracted, PAT is AFTER.",
    ),
    MetricDefinition(
        canonical_name="pat",
        aliases=(
            "pat", "profit after tax", "net profit", "net income", "earnings",
            "profit for the period", "profit for the year",
            "profit/(loss) for the period/year", "profit/(loss) for the period",
            "profit/(loss) for the year",
        ),
        metric_type="raw", dsl_enabled=True, label="PAT",
        prompt_aliases="profit after tax, net profit, bottom line, PAT (AFTER tax is deducted)",
    ),
    MetricDefinition(
        canonical_name="share_of_profit_of_associate",
        aliases=(
            "share of profit/(loss) of an associate and a joint",
            "share in (profit)/loss of associate/joint venture",
            "~ associate•",
        ),
        metric_type="raw", dsl_enabled=False, label="Share of Profit of Associate",
    ),

    # ── Margins ──────────────────────────────────────────────────────────
    MetricDefinition(
        canonical_name="gross_margin",
        aliases=("gross margin",),
        metric_type="derived", dsl_enabled=False, label="Gross Margin",
        derivation_formula="gross_profit / revenue * 100",
    ),
    MetricDefinition(
        canonical_name="ebitda_margin",
        aliases=("ebitda margin",),
        metric_type="derived", dsl_enabled=False, label="EBITDA Margin",
        derivation_formula="ebitda / revenue * 100",
    ),
    MetricDefinition(
        canonical_name="pat_margin",
        aliases=("pat margin",),
        metric_type="derived", dsl_enabled=False, label="PAT Margin",
        derivation_formula="pat / revenue * 100",
    ),

    # ── Tax ──────────────────────────────────────────────────────────────
    MetricDefinition(
        canonical_name="tax_expense",
        aliases=(
            "tax expense", "income tax expense", "total tax expense",
            "tax expenses", "total tax expenses", "taxation", "tax expense for the period",
        ),
        metric_type="raw", dsl_enabled=True, label="Tax Expense",
        prompt_aliases="tax expense, income tax expense, total tax",
    ),
    MetricDefinition(
        canonical_name="current_tax",
        aliases=("current tax", "current lax"),
        metric_type="raw", dsl_enabled=False, label="Current Tax",
    ),
    MetricDefinition(
        canonical_name="deferred_tax",
        aliases=("deferred tax", "deferred rnx"),
        metric_type="raw", dsl_enabled=False, label="Deferred Tax",
    ),

    # ── Balance sheet / cash flow ────────────────────────────────────────
    MetricDefinition(
        canonical_name="cash",
        aliases=("cash", "cash and cash equivalents"),
        metric_type="raw", dsl_enabled=False, label="Cash & Cash Equivalents",
    ),
    MetricDefinition(
        canonical_name="operating_cash_flow",
        aliases=("operating cash flow",),
        metric_type="raw", dsl_enabled=False, label="Operating Cash Flow",
    ),
    MetricDefinition(
        canonical_name="fcf",
        aliases=("free cash flow",),
        metric_type="derived", dsl_enabled=False, label="Free Cash Flow",
        derivation_formula="operating_cash_flow - capex",
    ),
    MetricDefinition(
        canonical_name="inventory",
        aliases=("inventory",),
        metric_type="raw", dsl_enabled=False, label="Inventory",
    ),
    MetricDefinition(
        canonical_name="receivables",
        aliases=("trade receivables",),
        metric_type="raw", dsl_enabled=False, label="Trade Receivables",
    ),
    MetricDefinition(
        canonical_name="payables",
        aliases=("trade payables",),
        metric_type="raw", dsl_enabled=False, label="Trade Payables",
    ),
    MetricDefinition(
        canonical_name="total_assets",
        aliases=("total assets",),
        metric_type="raw", dsl_enabled=False, label="Total Assets",
    ),
    MetricDefinition(
        canonical_name="total_liabilities",
        aliases=("total liabilities",),
        metric_type="raw", dsl_enabled=False, label="Total Liabilities",
    ),
    MetricDefinition(
        canonical_name="equity",
        aliases=("equity", "total equity"),
        metric_type="raw", dsl_enabled=False, label="Equity",
    ),
    MetricDefinition(
        canonical_name="other_equity",
        aliases=("other equity",),
        metric_type="raw", dsl_enabled=False, label="Other Equity",
    ),
    MetricDefinition(
        canonical_name="net_gain_on_investments",
        aliases=("net gain on mutual fund units", "net gain on mutual funds"),
        metric_type="raw", dsl_enabled=False, label="Net Gain on Investments",
    ),
    MetricDefinition(
        canonical_name="liabilities_written_back",
        aliases=("liabilities written back",),
        metric_type="raw", dsl_enabled=False, label="Liabilities Written Back",
    ),

    # ── EPS / ops metrics ────────────────────────────────────────────────
    MetricDefinition(
        canonical_name="eps_basic",
        aliases=("earnings per share", "basic earnings per share", "basic eps",
                 "basic (not annualised)", "basic"),
        metric_type="raw", dsl_enabled=True, label="Basic EPS",
        prompt_aliases="basic earnings per share, basic eps",
    ),
    MetricDefinition(
        canonical_name="eps_diluted",
        aliases=("diluted earnings per share", "diluted eps",
                 "diluted (not annualised)", "diluted"),
        metric_type="raw", dsl_enabled=True, label="Diluted EPS",
        prompt_aliases="diluted earnings per share, diluted eps",
    ),
    MetricDefinition(
        canonical_name="orders",
        aliases=("orders",), metric_type="raw", dsl_enabled=False, label="Orders",
    ),
    MetricDefinition(
        canonical_name="mtu",
        aliases=("mtu",), metric_type="raw", dsl_enabled=False, label="Monthly Transacting Users",
    ),
    MetricDefinition(
        canonical_name="mau",
        aliases=("mau",), metric_type="raw", dsl_enabled=False, label="Monthly Active Users",
    ),
    MetricDefinition(
        canonical_name="active_users",
        aliases=("active users",), metric_type="raw", dsl_enabled=False, label="Active Users",
    ),
    MetricDefinition(
        canonical_name="impairment_of_loans_and_investments_in_associates",
        aliases=(
            "impairment of loans/investment in associates",
            "impairment of loans / investment in associates",
            "impairment of loans/investments in associates",
            "impairment of loans / investments in associates",
            "provision for impairment of loans/investments in subsidiary/associate",
            "provision for impairment ofloans/investments in subsidiary/associate",
            "provision for impainnent ofloans/investments in subsidiary/associate",
        ),
        metric_type="raw", dsl_enabled=False, label="Impairment of Loans/Investments in Associates",
    ),
    MetricDefinition(
        canonical_name="segment_revenue_watches", 
        aliases=("watches",),
        metric_type="raw", dsl_enabled=False, label="Watches Segment Revenue",
    ),
    MetricDefinition(
        canonical_name="segment_revenue_jewellery", 
        aliases=("jewellery",),
        metric_type="raw", dsl_enabled=False, label="Jewellery Segment Revenue",
    ),
    MetricDefinition(
        canonical_name="segment_revenue_eyecare", 
        aliases=("eyecare",),
        metric_type="raw", dsl_enabled=False, label="Eyecare Segment Revenue",
    ),
    MetricDefinition(
        canonical_name="segment_revenue_others", 
        aliases=("others",),
        metric_type="raw", dsl_enabled=False, label="Other Segment Revenue",
    ),
    MetricDefinition(
        canonical_name="segment_unallocated", 
        aliases=("corporate (unallocated)", "corporate unallocated"),
        metric_type="raw", dsl_enabled=False, label="Corporate Unallocated",
    ),
    MetricDefinition(
        canonical_name="geo_revenue_india", 
        aliases=("india",),
        metric_type="raw", dsl_enabled=True, label="India Revenue",
    ),
    MetricDefinition(
        canonical_name="geo_revenue_rest_of_world", 
        aliases=("rest of the world",),
        metric_type="raw", dsl_enabled=True, label="Rest of World Revenue",
    ),
    MetricDefinition(
        canonical_name="oci_tax_on_remeasurement",
        aliases=("income-tax on (i) above", "income-tax on (i) above*", "income-tax on (i) above•"),
        metric_type="raw", dsl_enabled=False,
        label="Income Tax on Remeasurement of Defined Benefit Plans",
    ),
    MetricDefinition(
        canonical_name="oci_tax_on_fx_translation",
        aliases=("income-tax on (ii) above", "income-tax on (ii) above•"),
        metric_type="raw", dsl_enabled=False,
        label="Income Tax on FX Translation Differences",
    ),
    MetricDefinition(
        canonical_name="pat_attributable_to_owners",
        aliases=("owners of the group",),
        metric_type="raw", dsl_enabled=True,
        label="PAT Attributable to Owners of the Group",
    ),
    MetricDefinition(
        canonical_name="purchase_of_stock_in_trade",
        aliases=("purchase of stock-in-trade",),
        metric_type="raw", dsl_enabled=True,
        label="Purchase of Stock-in-Trade",
    ),
    MetricDefinition(
        canonical_name="total_other_comprehensive_income",
        aliases=("total other comprehensive loss", "total other comprehensive income"),
        metric_type="raw", dsl_enabled=True,
        label="Total Other Comprehensive Income/(Loss)",
    ),

    # ── Exceptional items / OCI sub-lines ───────────────────────────────
    MetricDefinition(
        canonical_name="exceptional_items",
        aliases=("exceptional items",),
        metric_type="raw", dsl_enabled=True, label="Exceptional Items",
        prompt_aliases="exceptional items, one-off items",
    ),
    MetricDefinition(
        canonical_name="oci_remeasurement_defined_benefit",
        aliases=(
            "remeasurements of the defined benefit plans",
            "remeasurements of the defined benetit plans",
            "remeasuremcnls of the defined benefit plans 0 0",
            "-remeasurement of employee defined benefit plan",
            "remeasurement of employee defined benefit plan",
        ),
        metric_type="raw", dsl_enabled=False, label="OCI: Remeasurement of Defined Benefit Plans",
    ),
    MetricDefinition(
        canonical_name="oci_fx_translation",
        aliases=(
            "exchange differences on translation of foreign operations",
            "exchange differences on trnnslation of foreign operations 8 2",
            "exchange differences on lranslation of foreign operations 0 5 i",
            "-exchange differences in translating the financial statements of foreign",
        ),
        metric_type="raw", dsl_enabled=False, label="OCI: FX Translation",
    ),
    MetricDefinition(
        canonical_name="ppe_disposal_gain_loss",
        aliases=("(profit)/ loss on sale of property, plant and equipment (net)",),
        metric_type="raw", dsl_enabled=False, label="PPE Disposal Gain/Loss",
    ),
)

# ---------------------------------------------------------------------------
# Derived lookup structures (built once at import time)
# ---------------------------------------------------------------------------

_BY_CANONICAL: dict[str, MetricDefinition] = {m.canonical_name: m for m in ALL_METRICS}

if len(_BY_CANONICAL) != len(ALL_METRICS):
    seen: dict[str, int] = {}
    for m in ALL_METRICS:
        seen[m.canonical_name] = seen.get(m.canonical_name, 0) + 1
    dupes = [name for name, count in seen.items() if count > 1]
    raise ValueError(f"Duplicate canonical_name(s) in metric registry: {dupes}")


def get_metric(canonical_name: str) -> MetricDefinition | None:
    return _BY_CANONICAL.get(canonical_name)


def all_alias_pairs() -> dict[str, str]:
    """alias (lowercase) -> canonical_name, for every metric regardless of dsl_enabled.
    Used by entity_resolver.py to build its ingestion-time alias lookup."""
    pairs: dict[str, str] = {}
    for m in ALL_METRICS:
        for alias in m.aliases:
            pairs[alias.lower().strip()] = m.canonical_name
    return pairs


def dsl_registry() -> dict[str, dict]:
    """canonical_name -> {"available": ..., "column": "value", "label": ...}
    for dsl_enabled metrics only. Mirrors dsl_compiler.py's old METRIC_REGISTRY shape."""
    return {
        m.canonical_name: {
            "available": m.metric_type == "raw",
            "column": "value",
            "label": m.label,
        }
        for m in ALL_METRICS if m.dsl_enabled
    }


def dsl_alias_pairs() -> dict[str, str]:
    """alias -> canonical_name, restricted to dsl_enabled metrics only.
    Mirrors dsl_compiler.py's old METRIC_ALIASES shape."""
    pairs: dict[str, str] = {}
    for m in ALL_METRICS:
        if not m.dsl_enabled:
            continue
        for alias in m.aliases:
            pairs[alias.lower().strip()] = m.canonical_name
    return pairs


def prompt_metric_lines() -> list[str]:
    """One line per dsl_enabled, raw (currently queryable) metric, for
    quant_engine.py's Gemini DSL-generation system prompt."""
    lines = []
    for m in ALL_METRICS:
        if not m.dsl_enabled or m.metric_type != "raw":
            continue
        aliases = m.prompt_aliases or ""
        lines.append(f'  "{m.canonical_name}" — also called: {aliases}')
    return lines


def prompt_warnings() -> list[str]:
    """Disambiguation warnings (e.g. PBT vs PAT) for the DSL prompt's
    CRITICAL MAPPING RULES section."""
    return [
        f'- {m.label} ("{m.canonical_name}"): {m.prompt_warning}'
        for m in ALL_METRICS
        if m.dsl_enabled and m.prompt_warning
    ]


def not_yet_derivable_metrics() -> list[str]:
    """dsl_enabled metrics that are metric_type='derived' — no SQL formula
    compiler exists yet, so these currently return a clean unavailable
    response rather than being computed."""
    return [m.canonical_name for m in ALL_METRICS if m.dsl_enabled and m.metric_type == "derived"]