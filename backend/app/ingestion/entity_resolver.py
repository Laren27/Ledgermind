import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional
from app.ingestion.models import normalize_quarter
from app.metrics.registry import all_alias_pairs, ALL_METRICS

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
# after — accept either bracket style
META_RE = re.compile(r"[\(\[]\s*(?:unaudited|audited|standalone|consolidated|restated|continuing\s+operations|refer\s+note.*?|note.*?|(?:₹|rs\.?|inr).*?|in\s+(?:crores?|millions?|lakhs?|thousands?))\s*[\)\]]", re.IGNORECASE | re.VERBOSE)
UNITS_OUTSIDE_PARENS_RE = re.compile(r"\b(?:₹|rs\.?|inr)\s*(?:in\s+)?(?:crores?|millions?|lakhs?|thousands?)\b", re.IGNORECASE)
TRAILING_PUNCT_RE = re.compile(r"[\.\:\-,;]+$")
LEADING_PUNCT_RE = re.compile(r"^[\s:;,\-•]+")
MULTISPACE_RE = re.compile(r"\s+")
SLASH_RE = re.compile(r"\s*/\s*")
HYPHEN_SPACE_RE = re.compile(r"\s*-\s*")
MULTIHYPHEN_RE = re.compile(r"-{2,}")
FOOTNOTE_RE = re.compile(r"[\(\[]\d+[\)\]]$")
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
    "impainnent": "impairment",
    "ofloans": "of loans",
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

# ---------------------------------------------------------------------------
# METRIC_ALIASES is now derived from the single shared registry
# (app/metrics/registry.py) instead of being hand-maintained here.
#
# This is the exact fix for the recurring "three independent metric dicts
# drift apart" bug class (missing profit_before_tax entry, exceptional_items
# collision, Titan segment revenue falling through unmapped — see
# registry.py's module docstring for full history). Any new metric or
# alias now only needs to be added in ONE place.
# ---------------------------------------------------------------------------
METRIC_ALIASES: dict[str, str] = all_alias_pairs()

def resolve_metric(raw: str) -> str:
    normalized_text = normalize_metric_label(raw)
    if not normalized_text: return "unmapped_metric"
    canonical = METRIC_ALIASES.get(normalized_text)
    if canonical: return canonical

    # Tier 2 — whole-word (token-set) matching, longest-alias-first.
    #
    # WHY THIS REPLACED RAW SUBSTRING MATCHING: `alias in normalized_text`
    # matches on ANY shared character sequence, including partial words
    # inside unrelated longer words (e.g. alias "tax" would substring-match
    # inside "taxation", "syntax", etc. even though those are different
    # concepts). Longest-alias-first sorting (kept from the prior fix)
    # already solved one concrete collision class — a longer, more specific
    # phrase correctly wins over a shorter generic one — but substring
    # matching itself remained a structural risk for any FUTURE
    # OCR-mangled phrase that happens to share a character sequence with
    # an existing alias, without sharing its actual words.
    #
    # Token-set containment requires every WORD of the shorter phrase to
    # appear as a whole word in the other, not just a matching character
    # run. This still catches genuine paraphrases/OCR word-order noise
    # (e.g. "profit before tax" alias matching within an OCR-noisy row
    # containing all three words) while rejecting pure substring
    # coincidences that share no actual words in common.
    # Split on slashes as well as whitespace: normalize_metric_label's
    # SLASH_RE collapses "products / services" to "products/services" with
    # no surrounding space, which would otherwise glue two real words into
    # one token and cause word-set matching to miss OCR-normal patterns
    # like "sale of products/services" against the alias "sale of products"
    # (confirmed regression: this exact case broke TITAN's revenue
    # extraction on first attempt).
    _WORD_SPLIT_RE = re.compile(r"[\s/]+")
    normalized_words = set(_WORD_SPLIT_RE.split(normalized_text)) - {""}
    best_match: Optional[tuple[int, str]] = None  # (alias_word_count, canonical_name)
    for alias, canonical_name in METRIC_ALIASES.items():
        alias_words = set(_WORD_SPLIT_RE.split(alias)) - {""}
        if not alias_words:
            continue
        if alias_words <= normalized_words or normalized_words <= alias_words:
            # Prefer the alias with the most words (most specific match),
            # same intent as the old longest-string-first rule but now
            # measured in shared whole words rather than raw character length.
            if best_match is None or len(alias_words) > best_match[0]:
                best_match = (len(alias_words), canonical_name)
    if best_match:
        return best_match[1]

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