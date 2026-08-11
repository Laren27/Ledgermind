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

# Rejoins a first letter that the PDF typeset as its own text run.
# ZOMATO FY24's cash-flow and OCI tables render as "I nterest expense",
# "L oan given", "P ayment of principal portion" — pdfplumber faithfully
# reports the space. This MUST run before PREFIX_RE: after casefolding,
# a bare leading "i " or "l " is a legal roman numeral, so PREFIX_RE
# stripped it and produced metrics named `nterest_expense` / `oan_given`.
#
# The {2,} lookahead means a genuine "(i) ..." or "i. ..." prefix is
# untouched (both carry punctuation and are handled by PREFIX_RE).
# KNOWN AMBIGUITY: a BARE single-char roman prefix ("V Total income")
# is indistinguishable from a split initial and would be rejoined
# wrongly. Accepted because this corpus uses multi-char ("VII", "IX")
# or parenthesised forms; regression_check is the guard.
SPLIT_INITIAL_RE = re.compile(r"^([A-Z])\s+(?=[a-z]{2,})")

# The POSITIONAL row extractor (extract_financials_positional) sorts words
# by x-coordinate, and a capital "I" is narrow enough that it sorts AFTER
# the rest of its own label: 'nterest expense I', 'nvestment in mutual fund
# units I'. Measured on ZOMATO FY24 p176 — only "I" does this; every wider
# capital (P, S, C, L, B, T, A, N) stays in front and is handled above.
# Applied to the RAW label before casefolding, so the trailing capital is
# still distinguishable from an ordinary word.
# Guard: fires only when the label STARTS lowercase, i.e. the initial is
# genuinely missing from the front. A normal label ending in a capital is
# untouched.
STRAY_TRAILING_MARKER_RE = re.compile(r"(?<=[a-z\)])\s+I$")

TRAILING_INITIAL_RE = re.compile(r"^([a-z].*?)\s+([A-Z])$")

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
    raw_label = TRAILING_INITIAL_RE.sub(r"\2\1", raw_label.strip())
    # MUST run on RAW text, before casefold. Case is the only thing
    # distinguishing a split initial from a roman-numeral prefix:
    # "I nterest expense" (initial + lowercase remainder) vs "V Profit
    # before..." (numeral + capitalised word). Casefolding first made
    # them identical, and this rule glued "v profit" -> "vprofit",
    # destroying a PREFIX_RE match and losing
    # profit_before_exceptional_items from the derivation chain.
    raw_label = SPLIT_INITIAL_RE.sub(r"\1", raw_label)
    # A bare trailing "I" on a label that already starts with a capital
    # is a stray roman column marker the positional extractor sorted to
    # the end ("Revenue from operations I", "Income taxes (paid) refund
    # (net) I") — NOT a displaced initial, which TRAILING_INITIAL_RE
    # handles above and which leaves the label starting lowercase.
    # Scoped to exactly one "I": a scan of all FINANCIAL_STATEMENT pages
    # across all three source PDFs found 13 trailing-capital labels, and
    # the only legitimate one is TITAN's "...through OCI" (3 chars).
    raw_label = STRAY_TRAILING_MARKER_RE.sub("", raw_label)
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

    # A LABEL THAT IS ONLY THE WORD "TOTAL" NAMES NO METRIC.
    #
    # "total" is not an alias of anything, so it falls to Tier 2 and matches
    # four different canonicals at one word each -- a declared tie broken by
    # registry declaration order, which returns `revenue`. That is the
    # resolver asserting something it cannot know: a bare Total carries no
    # meaning without its section, and the same label means revenue, segment
    # assets and segment liabilities on a single TITAN page.
    #
    # NO LIVE VICTIM TODAY. Census 2026-08-08: 43 rows in the corpus have
    # desc_lower == "total" (TITAN 8, ETERNAL 2, ZOMATO AR 33, PAYTM 0), and
    # none is the kept row for any tuple -- 35 sit on pages never classified
    # FINANCIAL_STATEMENT, and the other 8 are caught by _should_skip_row,
    # for which "total" is an explicit _SKIP_DESCRIPTIONS member.
    #
    # THIS IS THE SECOND DEFENCE, AND IT EXISTS BECAUSE THE FIRST IS EXACT.
    # _should_skip_row tests `desc_lower in _SKIP_DESCRIPTIONS`, an equality
    # test on the RAW-lowercased label, while everything else here reasons
    # about the NORMALISED form. A label normalising to "total" but not
    # lowercasing to exactly "total" -- "Total:", "Total*", "Total (1)" --
    # clears the skip and reaches this function. Zero such labels exist
    # today; the OCR family that produced (I)->(1) and I+7,292->17,292 is
    # how one appears.
    #
    # Returns the unmapped form rather than raising: an unrecognised label is
    # a normal outcome here, and "total" as a stored metric name is visible
    # and harmless, where a silent `revenue` is neither.
    if normalized_text == "total":
        logger.warning(
            "Refusing to resolve bare 'Total' (raw: %r) — a total row names no "
            "metric without its section. Storing as-is.", raw,
        )
        return "total"

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
    # Every alias matching at best_match's word count, winner first. Tracked
    # only so a TIE can be REPORTED -- see the block after the loop.
    tied: list[tuple[str, str]] = []  # (alias, canonical_name)
    for alias, canonical_name in METRIC_ALIASES.items():
        alias_words = set(_WORD_SPLIT_RE.split(alias)) - {""}
        if not alias_words:
            continue
        # COVERAGE FLOOR. `alias_words <= normalized_words` alone let a
        # one-word alias swallow a seven-word label: "cash" matched inside
        # "net cash generated from/(used in) investing activities", and
        # "equity" inside "proceeds from issue of equity shares" — four
        # distinct cash-flow lines collapsing onto `cash`/`equity` with
        # wrong values in a queryable metric.
        #
        # Measured on ZOMATO FY24 pages 169/170/176/292 (2026-08-01): every
        # coincidental match scored <=0.43 coverage, every genuine paraphrase
        # >=0.60. 0.5 sits in the empty band with ~0.07 margin either side.
        # Raw ratios recorded in docs/measurements/.
        #
        # Applies ONLY to the alias-inside-label direction. The reverse
        # (label is a fragment of a longer alias) has coverage >1 by
        # construction and is a different, working case.
        if alias_words <= normalized_words:
            if len(alias_words) / len(normalized_words) < 0.5:
                continue
        if alias_words <= normalized_words or normalized_words <= alias_words:
            # Prefer the alias with the most words (most specific match),
            # same intent as the old longest-string-first rule but now
            # measured in shared whole words rather than raw character length.
            if best_match is None or len(alias_words) > best_match[0]:
                best_match = (len(alias_words), canonical_name)
                tied = [(alias, canonical_name)]
            elif len(alias_words) == best_match[0]:
                tied.append((alias, canonical_name))
    if best_match:
        # A TIE AT THE WINNING WORD COUNT IS RESOLVED BY DICT INSERTION ORDER,
        # i.e. by declaration order in registry.ALL_METRICS. That is arbitrary
        # with respect to correctness, and until 2026-08-02 it was also silent.
        #
        # THE BUG THAT MOTIVATED THIS. PAYTM's P&L prints 'Deferred tax
        # expense/ (credit)', 4 tokens. Both "deferred tax" (-> deferred_tax)
        # and "tax expense" (-> tax_expense) are 2-word subsets at exactly 0.50
        # coverage, so both cleared the floor and tied. tax_expense is declared
        # earlier in ALL_METRICS, so it won; the deferred row was stored AS
        # tax_expense, and financial_extractor's seen_keys (first-wins) then
        # discarded the genuine 'Total Tax expense' row as a duplicate key.
        # PAYTM consolidated tax_expense held the DEFERRED figure (FY26 annual
        # 10 against the true 30), producing three PAT identity failures that
        # stood for weeks. Nothing anywhere recorded that a choice had been
        # made. Fixed by adding a 3-word "deferred tax expense" alias, which
        # wins outright -- but the next such collision deserves one log line
        # rather than a multi-session diagnosis.
        #
        # A static scan of the registry (2026-08-02) found 189 same-length
        # alias pairs mapping to different canonicals and sharing at least one
        # word. That is an upper bound, not a risk count: most pairs share only
        # a stopword and could never both be subsets of one real label. Which
        # ties are REACHABLE is a property of the labels documents actually
        # contain, which is exactly what this log measures and a static scan
        # cannot.
        #
        # Reports only when the tied aliases name DIFFERENT canonicals. Two
        # aliases of the same metric tying is the registry working as intended
        # (pat carries both "profit for the period" and "profit for the year").
        #
        # Deliberately does NOT change the outcome. Which candidate is correct
        # is a per-label judgement, and the fix is normally an alias edit in
        # registry.py so the two stop colliding at all. Same reasoning as
        # seen_keys: make it visible, do not guess.
        rivals = [(a, c) for a, c in tied if c != best_match[1]]
        if rivals:
            shared = set.intersection(*[
                set(_WORD_SPLIT_RE.split(a)) - {""} for a, _ in tied
            ])
            logger.warning(
                "  [METRIC TIE] '%s' (normalized: '%s') — %d aliases matched at "
                "%d words; kept '%s' by registry declaration order, rejected %s. "
                "Shared tokens: %s. Disambiguate with a longer alias in registry.py.",
                raw, normalized_text, len(tied), best_match[0], best_match[1],
                sorted({c for _, c in rivals}), sorted(shared) or "none",
            )
        return best_match[1]

    logger.warning("Unknown metric: '%s' (normalized: '%s') — storing as-is", raw, normalized_text)
    return normalized_text.replace(" ", "_")

def resolve_company(raw_name: str) -> Optional[CompanyProfile]:
    """Exact alias match only. No substring fallback -- see F1.

    F1 (audit 2026-08-11): an unanchored substring-containment loop lived here
    and silently misfiled distinct companies into incumbents. Measured live:
    "Titan Biotech Limited" (BSE 524717, a separately listed company) resolved
    to TITAN; "Titanium Industries" likewise; "ONE97 REALTY" to PAYTM. Which
    incumbent won depended on dict insertion order, so the misfile was also
    non-deterministic.

    Two consequences, both silent. On the ingest path resolve_company's result
    overwrites `company`, so another issuer's financials land under the
    incumbent's key with nothing in the audit trail recording a substitution.
    On the query path resolve_ticker() feeds the router's _KNOWN_TICKERS gate,
    so a question about an unheld company was filtered to a different one and
    answered with real citations from the wrong filing.

    No golden question reached the fallback: all 91 name their company in a
    form already indexed exactly. Do not reintroduce it -- add an explicit
    alias to COMPANY_REGISTRY instead.
    """
    return _ALIAS_INDEX.get(raw_name.lower().strip())

def resolve_ticker(raw_name: str) -> str:
    profile = resolve_company(raw_name)
    return profile.ticker if profile else raw_name.upper().strip()