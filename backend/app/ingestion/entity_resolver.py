"""
Entity Resolver — deterministic company identity normalization.

Responsibilities:
  1. Map alias / rebrand names → canonical company name + ticker
  2. Resolve ticker to exchange-qualified form (ZOMATO.NS)
  3. Provide metric name normalization (for financials table)

Design: static registry only. No LLM, no external API call.
Adding a new company = add one entry to COMPANY_REGISTRY.

Called once per ingestion job, before document_classifier.
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Company registry
# Each entry covers all known aliases for the same legal entity.
# "primary" is what gets stored in the DB and Qdrant payload.
# ---------------------------------------------------------------------------

@dataclass
class CompanyProfile:
    primary: str          # canonical company name stored everywhere
    ticker: str           # NSE ticker (used in Qdrant + financials table)
    aliases: list[str]    # all names that resolve to this profile
    sector: str = ""


COMPANY_REGISTRY: list[CompanyProfile] = [
    CompanyProfile(
        primary="ETERNAL",
        ticker="ETERNAL",
        aliases=[
            "eternal", "eternal limited", "zomato", "zomato limited",
            "zomato ltd", "eternal ltd",
        ],
        sector="quick_commerce",
    ),
    CompanyProfile(
        primary="PAYTM",
        ticker="PAYTM",
        aliases=[
            "paytm", "one97 communications", "one97", "paytm payments bank",
        ],
        sector="fintech",
    ),
    CompanyProfile(
        primary="NYKAA",
        ticker="NYKAA",
        aliases=[
            "nykaa", "fsg nykaa", "fsn e-commerce", "fsn ecommerce",
        ],
        sector="ecommerce",
    ),
    CompanyProfile(
        primary="POLICYBAZAAR",
        ticker="POLICYBZR",
        aliases=[
            "policybazaar", "pb fintech", "pbfintech",
        ],
        sector="insurtech",
    ),
    CompanyProfile(
        primary="DELHIVERY",
        ticker="DELHIVERY",
        aliases=[
            "delhivery", "delhivery limited",
        ],
        sector="logistics",
    ),
    CompanyProfile(
        primary="SWIGGY",
        ticker="SWIGGY",
        aliases=[
            "swiggy", "bundl technologies", "bundl",
        ],
        sector="quick_commerce",
    ),
    # Added Titan for generalization test
    CompanyProfile(
        primary="TITAN",
        ticker="TITAN",
        aliases=[
            "titan", "titan company", "titan company limited", "titan ltd", "titan company ltd"
        ],
        sector="consumer_goods",
    ),
]

# Build a fast lookup dict: lowercase alias → CompanyProfile
_ALIAS_INDEX: dict[str, CompanyProfile] = {}
for _profile in COMPANY_REGISTRY:
    for _alias in _profile.aliases:
        _ALIAS_INDEX[_alias.lower().strip()] = _profile


# ---------------------------------------------------------------------------
# Metric name normalization
# Maps any surface form found in PDF tables → canonical metric name
# used in the financials table `metric` column.
# ---------------------------------------------------------------------------

METRIC_ALIASES: dict[str, str] = {
    # Revenue
    "revenue from operations": "revenue",
    "sale of products/ services": "revenue",      # TITAN variant
    "sale of products/services": "revenue",       # TITAN variant (no space)
    "sale of products": "revenue",
    "net revenue": "revenue",
    "adjusted revenue": "adjusted_revenue",
    "gross order value": "gov",
    "gov": "gov",

    # Expenses (including OCR artifact healing)
    "cost of materials consumed": "cost_of_materials_consumed",
    "purchases of stock-in-trade": "purchases_of_stock-in-trade",
    "purchase of stock-in-trade": "purchases_of_stock-in-trade",
    "purchase of stock-in": "purchases_of_stock-in-trade",
    "employee benefits expense": "employee_benefits_expense",
    "finance costs": "finance_costs",
    "fina nee costs": "finance_costs",            # OCR variant
    "advertising": "advertising",
    "other expenses": "other_expenses",
    "total expenses": "total_expenses",

    # Profitability
    "ebitda": "ebitda",
    "adjusted ebitda": "adjusted_ebitda",
    "ebit": "ebit",
    "pat": "pat",
    "profit after tax": "pat",
    "profit / (loss) after tax": "pat",
    "profit/(loss) after tax": "pat",

    # Margins
    "gross margin": "gross_margin",
    "ebitda margin": "ebitda_margin",
    "pat margin": "pat_margin",

    # Cash / Balance sheet
    "cash and cash equivalents": "cash",
    "closing cash": "closing_cash",
    "cash and equivalents": "cash",
    "free cash flow": "fcf",

    # Operational
    "number of orders": "orders",
    "monthly transacting users": "mtu",
    "active restaurants": "active_restaurants",
    "number of stores": "stores",
    "total stores": "stores",
    "blinkit nov": "blinkit_nov",            
    "food delivery adjusted ebitda": "food_delivery_adjusted_ebitda",

    # General / Table specific
    "standalone revenue": "revenue",
    "standalone pat": "pat",
    "total income": "total_income",
    "total income (i+ii)": "total_income",
    "total income (l+ll)": "total_income",       
    "profit for the period": "pat",
    "profit for the year": "pat",
    "profit/(loss) for the period": "pat",
    "profit/(loss) for the year": "pat",
    "other income": "other_income",
    "exceptional items": "exceptional_items",
    
    # Tax metrics (including OCR artifact healing)
    "tax expense": "tax_expense",
    "profit before tax": "profit_before_tax",
    "profit before ta": "profit_before_tax",      # OCR variant
    "current tax": "current_tax",
    "current ta": "current_tax",                  # OCR variant
    "deferred tax": "deferred_tax",
    "deferred ta": "deferred_tax",                # OCR variant
}

def normalize_metric(raw: str) -> str:
    """
    Normalize a raw metric string from a PDF table to the canonical
    metric name used in the financials table.

    Returns the raw string lowercased if no mapping found —
    caller should log a warning for unknown metrics.
    """
    normalized = METRIC_ALIASES.get(raw.lower().strip())
    if normalized:
        return normalized

    # Partial match fallback — catches minor wording variations
    raw_lower = raw.lower().strip()
    for alias, canonical in METRIC_ALIASES.items():
        if alias in raw_lower or raw_lower in alias:
            logger.debug(
                "Metric partial match: '%s' → '%s' via alias '%s'",
                raw, canonical, alias,
            )
            return canonical

    logger.warning("Unknown metric: '%s' — storing as-is", raw)
    return raw_lower.replace(" ", "_")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_company(raw_name: str) -> Optional[CompanyProfile]:
    """
    Resolve a raw company name string to its canonical CompanyProfile.

    Returns None if the company is not in the registry.
    Caller is responsible for raising an error or flagging for review.

    Examples:
      resolve_company("Eternal Limited") → CompanyProfile(primary="ETERNAL", ...)
      resolve_company("Zomato")          → CompanyProfile(primary="ETERNAL", ...)
      resolve_company("Unknown Corp")    → None
    """
    key = raw_name.lower().strip()
    profile = _ALIAS_INDEX.get(key)

    if profile:
        logger.info(
            "Entity resolved: '%s' → %s (%s)", raw_name, profile.primary, profile.ticker
        )
        return profile

    # Partial match: handle names like "Eternal Limited (formerly Zomato)"
    for alias, prof in _ALIAS_INDEX.items():
        if alias in key:
            logger.info(
                "Entity partial match: '%s' → %s via alias '%s'",
                raw_name, prof.primary, alias,
            )
            return prof

    logger.warning("Could not resolve company: '%s'", raw_name)
    return None


def resolve_ticker(raw_name: str) -> str:
    """
    Convenience wrapper — returns just the ticker string.
    Returns raw_name uppercased if resolution fails (fail-open for now).
    """
    profile = resolve_company(raw_name)
    return profile.ticker if profile else raw_name.upper().strip()


# ---------------------------------------------------------------------------
# Quick smoke test (run this file directly to verify)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    test_cases = [
        ("Eternal Limited", "ETERNAL"),
        ("Zomato", "ETERNAL"),
        ("ZOMATO LIMITED", "ETERNAL"),
        ("Paytm", "PAYTM"),
        ("One97 Communications", "PAYTM"),
        ("Titan Company Ltd", "TITAN"),
        ("Unknown Corp XYZ", None),
    ]

    print("\n--- Entity Resolver Smoke Test ---")
    all_pass = True
    for raw, expected_primary in test_cases:
        profile = resolve_company(raw)
        actual = profile.primary if profile else None
        status = "PASS" if actual == expected_primary else "FAIL"
        if status == "FAIL":
            all_pass = False
        print(f"  [{status}] '{raw}' → {actual} (expected {expected_primary})")

    print("\n--- Metric Normalizer Smoke Test ---")
    metric_cases = [
        ("Revenue from operations", "revenue"),
        ("Adjusted Revenue", "adjusted_revenue"),
        ("-Sale of products/ services", "revenue"),
        ("Blinkit NOV", "blinkit_nov"),
        ("profit after tax", "pat"),
        ("Current ta", "current_tax"),
    ]
    for raw, expected in metric_cases:
        actual = normalize_metric(raw)
        status = "PASS" if actual == expected else "FAIL"
        print(f"  [{status}] '{raw}' → '{actual}' (expected '{expected}')")

    print(f"\n{'All tests passed.' if all_pass else 'FAILURES detected — check registry.'}")