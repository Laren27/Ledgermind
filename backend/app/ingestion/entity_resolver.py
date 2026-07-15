import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

@dataclass
class CompanyProfile:
    primary: str
    ticker: str
    aliases: list[str]
    sector: str = ""

COMPANY_REGISTRY: list[CompanyProfile] = [
    CompanyProfile(primary="ETERNAL", ticker="ETERNAL", aliases=["eternal", "eternal limited", "zomato", "zomato limited", "zomato ltd", "eternal ltd"], sector="quick_commerce"),
    CompanyProfile(primary="PAYTM", ticker="PAYTM", aliases=["paytm", "one97 communications", "one97", "paytm payments bank", "one 97 communications", "one 97 communications limited", "one 97", "one97 communications limited"], sector="fintech"),
    CompanyProfile(primary="NYKAA", ticker="NYKAA", aliases=["nykaa", "fsg nykaa", "fsn e-commerce", "fsn ecommerce"], sector="ecommerce"),
    CompanyProfile(primary="POLICYBAZAAR", ticker="POLICYBZR", aliases=["policybazaar", "pb fintech", "pbfintech"], sector="insurtech"),
    CompanyProfile(primary="DELHIVERY", ticker="DELHIVERY", aliases=["delhivery", "delhivery limited"], sector="logistics"),
    CompanyProfile(primary="SWIGGY", ticker="SWIGGY", aliases=["swiggy", "bundl technologies", "bundl"], sector="quick_commerce"),
    CompanyProfile(primary="TITAN", ticker="TITAN", aliases=["titan", "titan company", "titan company limited", "titan ltd", "titan company ltd"], sector="consumer_goods"),
]

_ALIAS_INDEX: dict[str, CompanyProfile] = {}
for _profile in COMPANY_REGISTRY:
    for _alias in _profile.aliases:
        _ALIAS_INDEX[_alias.lower().strip()] = _profile

PREFIX_RE = re.compile(r"^(?:\(?\d+\)?|\(?[ivxlcdm]+\)?|\([a-z]\)|[a-z][.)])[.:-]?\s+", re.IGNORECASE | re.VERBOSE)
META_RE = re.compile(r"\(\s*(?:unaudited|audited|standalone|consolidated|restated|continuing\s+operations|refer\s+note.*?|note.*?|(?:₹|rs\.?|inr).*?|in\s+(?:crores?|millions?|lakhs?|thousands?))\s*\)", re.IGNORECASE | re.VERBOSE)
UNITS_OUTSIDE_PARENS_RE = re.compile(r"\b(?:₹|rs\.?|inr)\s*(?:in\s+)?(?:crores?|millions?|lakhs?|thousands?)\b", re.IGNORECASE)
TRAILING_PUNCT_RE = re.compile(r"[\.\:\-,;]+$")
LEADING_PUNCT_RE = re.compile(r"^[\s:;,\-•]+")
MULTISPACE_RE = re.compile(r"\s+")
SLASH_RE = re.compile(r"\s*/\s*")
HYPHEN_SPACE_RE = re.compile(r"\s*-\s*")
MULTIHYPHEN_RE = re.compile(r"-{2,}")
FOOTNOTE_RE = re.compile(r"\(\d+\)$")
TRAILING_NUMERIC_NOISE_RE = re.compile(r"(?:[/_\s]+\d{2,4}\\?)+\s*$")

# --- EXPANDED OCR FIXES ---
OCR_FIXES = {
    # --- Existing entries (untouched) ---
    "fina nee": "finance", "benefi ts": "benefits", "empl oyee": "employee",
    "operati ons": "operations", "equival ents": "equivalents", "invent ories": "inventories",
    "recei vables": "receivables", "paya bles": "payables", "taxa tion": "taxation",
    "depre ciation": "depreciation", "amorti sation": "amortization", "amorti zation": "amortization",
    "l+ll": "i+ii", "lntcrcst": "interest", "e<1uity": "equity", "capit:1i": "capital",

    # --- ETERNAL Q4FY26 FIXES ---
    "profil": "profit",
    "ror the": "for the",
    "exce1uional": "exceptional",
    "tcm1ination": "termination",
    "tennination": "termination",
    "oflcasc": "of lease",
    "contrncts": "contracts",
    "lmpainnent": "impairment",
    "lmpairmcnt": "impairment",
    "co11ected": "collected",

    # --- PAYTM Q4FY26 FIXES ---
    "pe1iod": "period",
    "cmtent": "current",
    "vvritten": "written",
    "mitten": "written",
    "vrith": "with",
    "proft": "profit",
}

def normalize_metric_label(raw_label: str) -> str:
    if not raw_label: return ""
    label = unicodedata.normalize("NFKC", raw_label).casefold()
    label = label.replace("\ufeff", "").replace("\u200b", "").replace("\xa0", " ")
    label = PREFIX_RE.sub("", label)
    label = META_RE.sub("", label)
    label = UNITS_OUTSIDE_PARENS_RE.sub("", label)
    label = FOOTNOTE_RE.sub("", label)
    label = TRAILING_NUMERIC_NOISE_RE.sub("", label)
    label = re.sub(r"[*#†%]+", "", label)
    for bad, good in OCR_FIXES.items(): label = label.replace(bad, good)
    label = re.sub(r"\bta\b", "tax", label)
    label = label.replace("&", "and").replace(",", " ")
    label = SLASH_RE.sub("/", label)
    label = HYPHEN_SPACE_RE.sub("-", label)
    label = MULTIHYPHEN_RE.sub("-", label)
    label = LEADING_PUNCT_RE.sub("", label)
    label = TRAILING_PUNCT_RE.sub("", label)
    return MULTISPACE_RE.sub(" ", label).strip()

METRIC_ALIASES: dict[str, str] = {
    "revenue": "revenue", "revenue from operations": "revenue", "total income from operations": "revenue",
    "income from operations": "revenue", "operating revenue": "revenue", "net revenue": "revenue",
    "gross revenue": "revenue", "sales": "revenue", "net sales": "revenue", "turnover": "revenue",
    "sale of products": "revenue", "sale of services": "revenue", "standalone revenue": "revenue",
    "gmv": "gmv", "gross merchandise value": "gmv", "gov": "gov", "gross order value": "gov",
    "total income": "total_income", "total income i+ii": "total_income", "ill total incomc 1+11": "total_income",
    "other income": "other_income", "non operating income": "other_income", "interest income": "other_income",
    "cost of materials consumed": "cost_of_materials_consumed", "raw material consumed": "cost_of_materials_consumed",
    "cost of materials and components consumed": "cost_of_materials_consumed",
    "purchases of stock-in-trade": "purchases_of_stock_in_trade", "changes in inventories": "changes_in_inventories",
    "employee benefits expense": "employee_benefits_expense", "employee cost": "employee_benefits_expense",
    "staff cost": "employee_benefits_expense", "salary expense": "employee_benefits_expense",
    "finance costs": "finance_costs", "interest expense": "finance_costs", "borrowing costs": "finance_costs",
    "depreciation": "depreciation", "depreciation and amortization expenses": "depreciation",
    "advertising": "advertising", "marketing expense": "advertising",
    "other expenses": "other_expenses", "total expenses": "total_expenses",
    "ebitda": "ebitda", "operating ebitda": "ebitda", "adjusted ebitda": "adjusted_ebitda",
    "ebit": "ebit", "operating profit": "operating_profit",
    "advertisement and sales promotion": "advertisement_and_sales_promotion",
    "advertising and sales promotion": "advertisement_and_sales_promotion",

    # Safely isolating PBT from greedy exceptional matches
    "profit before exceptional items and tax": "profit_before_exceptional_items",
    "profit before share of profit of an associate and a joint venture exceptional items and tax": "profit_before_exceptional_items",
    "profit/(loss) before share of profit/(loss) of associates/joint ventures exceptional items and tax": "profit_before_exceptional_items",
    "profit/(loss) before exceptional items and tax": "profit_before_exceptional_items",
    "profit before exceptional items": "profit_before_exceptional_items",
    "profit before tax": "profit_before_tax", "profit/(loss) before tax": "profit_before_tax",
    "pbt": "profit_before_tax",
    "pat": "pat", "profit after tax": "pat", "net profit": "pat", "profit for the period": "pat", "profit for the year": "pat",
    "profit/(loss) for the period/year": "pat", "profit/(loss) for the period": "pat", "profit/(loss) for the year": "pat",
    "profit/(loss) for the period/year": "pat",
    "profit/(loss) for the period": "pat",

    # Margins and Tax
    "gross margin": "gross_margin", "ebitda margin": "ebitda_margin", "pat margin": "pat_margin",
    "tax expense": "tax_expense", "income tax expense": "tax_expense", "total tax expense": "tax_expense",
    "tax expenses": "tax_expense", "total tax expenses": "tax_expense", "taxation": "tax_expense",
    "current tax": "current_tax", "current lax": "current_tax", "deferred tax": "deferred_tax",
    "deferred rnx": "deferred_tax", "tax expense for the period": "tax_expense",

    # Cash Flow, Balance Sheet, & Newly Added High-Value Items
    "cash": "cash", "cash and cash equivalents": "cash", "operating cash flow": "operating_cash_flow",
    "free cash flow": "fcf", "inventory": "inventory", "trade receivables": "receivables",
    "trade payables": "payables", "total assets": "total_assets", "total liabilities": "total_liabilities",
    "equity": "equity", "total equity": "equity", "other equity": "other_equity",
    "delivery and related charges": "delivery_and_related_charges",
    "delivery and related charges n4": "delivery_and_related_charges",
    "share-based payment expense": "share_based_payment_expense",
    "share based payment expense": "share_based_payment_expense",
    "net gain on mutual fund units": "net_gain_on_investments",
    "net gain on mutual funds": "net_gain_on_investments",
    "liabilities written back": "liabilities_written_back",

    # Associate Profits (Fixes the PBT/PAT gap for Zomato and Titan)
    "share of profit/(loss) of an associate and a joint": "share_of_profit_of_associate",
    "share in (profit)/loss of associate/joint venture": "share_of_profit_of_associate",
    "~ associate•": "share_of_profit_of_associate",

    # EPS and Ops Metrics
    "earnings per share": "eps_basic", "basic earnings per share": "eps_basic", "basic eps": "eps_basic",
    "diluted earnings per share": "eps_diluted", "diluted eps": "eps_diluted",
    "orders": "orders", "mtu": "mtu", "mau": "mau", "active users": "active_users",

    # Exceptional Items Mappings — kept narrowly scoped to the actual
    # "Exceptional items" P&L line only. Previously several distinct OCI/
    # notes line items (FX translation, remeasurement of defined benefit
    # plans, PPE disposal gain/loss) were all collapsed into this same
    # canonical name. When the real Exceptional Items row had a genuinely
    # blank cell for a period, one of these unrelated rows' value for that
    # same period silently backfilled the gap via first-write-wins dedup
    # in extract_all_financial_records (confirmed: PAYTM FY26 Q3
    # consolidated showed exceptional_items=43.0, which is actually that
    # period's "Exchange differences on translation" value — the real
    # Exceptional Items row is blank for Q3FY26). Splitting these into
    # their own canonical names eliminates the whole collision class.
    "exceptional items": "exceptional_items",
    "remeasurements of the defined benefit plans": "oci_remeasurement_defined_benefit",
    "remeasurements of the defined benetit plans": "oci_remeasurement_defined_benefit",
    "remeasuremcnls of the defined benefit plans 0 0": "oci_remeasurement_defined_benefit",
    "exchange differences on translation of foreign operations": "oci_fx_translation",
    "exchange differences on trnnslation of foreign operations 8 2": "oci_fx_translation",
    "exchange differences on lranslation of foreign operations 0 5 i": "oci_fx_translation",
    "-remeasurement of employee defined benefit plan": "oci_remeasurement_defined_benefit",
    "remeasurement of employee defined benefit plan": "oci_remeasurement_defined_benefit",
    "-exchange differences in translating the financial statements of foreign": "oci_fx_translation",
    "(profit)/ loss on sale of property, plant and equipment (net)": "ppe_disposal_gain_loss",

    # Depreciation & Amortization mappings
    "depreciation_and_amortisation_expense": "depreciation",
    "depreciation_and_amortization_expense": "depreciation",
    "depreciation_and_amortisation": "depreciation",
    "depreciation_and_amortization": "depreciation",
    "depreciation_expense": "depreciation",
    "amortisation_expense": "depreciation",
    "amortization_expense": "depreciation",
    "depreciation_and_amortisation_expenses": "depreciation",
    "depreciation_and_amortization_expenses": "depreciation",
    "depreciation_expenses": "depreciation",
    "depreciation and amortisation": "depreciation",
    "depreciation and amortization": "depreciation",
    "d&a": "depreciation",
    "da": "depreciation",
}

def resolve_metric(raw: str) -> str:
    normalized_text = normalize_metric_label(raw)
    if not normalized_text: return "unmapped_metric"
    canonical = METRIC_ALIASES.get(normalized_text)
    if canonical: return canonical
    # Longest-alias-first: a more specific/longer phrase match should win
    # over a shorter generic one that happens to be a substring of it.
    # Confirmed root cause of a real bug: "profit before exceptional items
    # and tax" (OCR-typo'd as "proft...") and "...share of profit...
    # exceptional items and tax" were both falling through to the
    # generic "exceptional items" alias, silently overwriting the real
    # Exceptional Items line's values.
    for alias, canonical_name in sorted(METRIC_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if alias in normalized_text or normalized_text in alias:
            return canonical_name
    logger.warning("Unknown metric: '%s' (normalized: '%s') — storing as-is", raw, normalized_text)
    return normalized_text.replace(" ", "_")

def resolve_company(raw_name: str) -> Optional[CompanyProfile]:
    key = raw_name.lower().strip()
    profile = _ALIAS_INDEX.get(key)
    if profile: return profile
    for alias, prof in _ALIAS_INDEX.items():
        if alias in key: return prof
    return None

def resolve_ticker(raw_name: str) -> str:
    profile = resolve_company(raw_name)
    return profile.ticker if profile else raw_name.upper().strip()