"""
LedgerMind — Phase 4: Prompt Shield
======================================
First node in the LangGraph graph. Runs before entity resolution, before routing,
before any engine. Blocks or passes every query.

Two block categories:

  1. TRADING / INVESTMENT ADVICE
     Blocks first-person requests for buy/sell/invest decisions.
     Does NOT block legitimate research language ("invest", "investment",
     "buy" in a business context, "portfolio" in a company context).
     Pattern design principle: match the ADVICE REQUEST structure, not the word.

  2. PROMPT INJECTION / JAILBREAK
     Blocks attempts to override system instructions or impersonate identities.

No LLM call. No network. Pure regex. Must be synchronous and zero-latency —
this gate runs on every single query including cached ones.

Trap 4 (blueprint §25B) is the primary design constraint:
  "should I buy Zomato?"          → BLOCK  (first-person buy decision)
  "what did Zomato buy?"          → PASS   (third-party factual)
  "investing in delivery infra"   → PASS   (business context, not advice)
  "is Zomato a good investment?"  → BLOCK  (investment recommendation request)
  "what was Zomato's investment in Blinkit?" → PASS (factual acquisition query)
"""

import logging
import re
from typing import List, NamedTuple, Optional

from app.engines.state import QueryState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Block pattern definitions
# Each pattern has: regex, category, user-facing reason
# ---------------------------------------------------------------------------

class BlockPattern(NamedTuple):
    pattern: re.Pattern
    category: str
    reason: str


def _p(pattern: str) -> re.Pattern:
    """Compile a case-insensitive pattern with word-boundary awareness."""
    return re.compile(pattern, re.IGNORECASE)


# ── Category 1: Trading and investment advice ─────────────────────────────
#
# Design: match the ADVICE REQUEST structure.
# "should I" + any financial action = advice request
# "is X a good investment" = recommendation request
# "buy / sell / invest" only blocked when directed at the USER's own decision
#
TRADING_ADVICE_PATTERNS: List[BlockPattern] = [
    # First-person buy/sell decisions
    BlockPattern(
        _p(r"\bshould\s+i\s+(buy|sell|short|invest|hold|exit|enter)\b"),
        "trading_advice",
        "LedgerMind cannot provide trading recommendations. Please rephrase as a factual research question.",
    ),
    BlockPattern(
        _p(r"\bwould\s+you\s+recommend\s+(buying|selling|investing|shorting|holding)\b"),
        "trading_advice",
        "LedgerMind cannot provide investment recommendations. Please rephrase as a factual research question.",
    ),
    # "is it a good time to buy/sell"
    BlockPattern(
        _p(r"\bis\s+it\s+(a\s+good|the\s+right|a\s+bad)\s+time\s+to\s+(buy|sell|invest|exit)\b"),
        "trading_advice",
        "LedgerMind cannot provide market timing advice. Please rephrase as a factual research question.",
    ),
    # "is [company] a good investment / buy / stock to buy"
    BlockPattern(
        _p(r"\bis\s+\w+(\s+\w+)?\s+(a\s+good|a\s+great|a\s+bad|a\s+strong)\s+(investment|buy|stock|pick|bet)\b"),
        "investment_advice",
        "LedgerMind cannot provide investment opinions. Please rephrase as a factual research question.",
    ),
    # "should I invest in [company]"
    BlockPattern(
        _p(r"\bshould\s+i\s+invest\s+in\b"),
        "investment_advice",
        "LedgerMind cannot provide investment advice. Please rephrase as a factual research question.",
    ),
    # "worth buying / worth investing in"
    BlockPattern(
        _p(r"\b(worth\s+(buying|selling|investing\s+in|holding)|worth\s+my\s+(money|time|investment))\b"),
        "investment_advice",
        "LedgerMind cannot provide investment opinions. Please rephrase as a factual research question.",
    ),
    # "will the stock go up / will price increase" (price prediction)
    BlockPattern(
        _p(r"\bwill\s+(the\s+)?(stock|share|price|scrip)\s+(go\s+up|go\s+down|rise|fall|increase|decrease|rally|crash)\b"),
        "price_prediction",
        "LedgerMind cannot make price predictions. Please rephrase as a factual research question.",
    ),
    # "what is the target price / price target for"
    BlockPattern(
        _p(r"\b(target\s+price|price\s+target)\s+(for|of)\b"),
        "price_prediction",
        "LedgerMind does not provide price targets. Please rephrase as a factual research question.",
    ),
    # "buy or sell", "buy/sell signal"
    BlockPattern(
        _p(r"\b(buy\s+or\s+sell|sell\s+or\s+hold|buy\s+signal|sell\s+signal)\b"),
        "trading_advice",
        "LedgerMind cannot provide trading signals. Please rephrase as a factual research question.",
    ),
    # "undervalued / overvalued / fairly valued" — valuation opinions
    BlockPattern(
        _p(r"\b(is\s+\w+\s+(undervalued|overvalued|fairly\s+valued)|(stock|company|share)\s+is\s+(under|over)valued)\b"),
        "investment_advice",
        "LedgerMind cannot provide valuation opinions. Please rephrase as a factual research question.",
    ),
    # Portfolio management requests
    BlockPattern(
        _p(r"\b(add|include|allocate|put)\s+.{0,30}\s+(to|in|into)\s+(my\s+)?(portfolio|holdings|watchlist)\b"),
        "portfolio_advice",
        "LedgerMind cannot provide portfolio management advice. Please rephrase as a factual research question.",
    ),
]

# ── Category 2: Prompt injection and jailbreak ────────────────────────────
#
# These patterns target instruction-override attempts, not financial content.
# Kept separate so block_reason can clearly state "security" vs "compliance".
#
INJECTION_PATTERNS: List[BlockPattern] = [
    BlockPattern(
        _p(r"\bignore\s+(all\s+)?(previous|prior|above|your)\s+(instructions|rules|guidelines|constraints|system)\b"),
        "prompt_injection",
        "This request cannot be processed.",
    ),
    BlockPattern(
        _p(r"\bdisregard\s+(all\s+)?(previous|prior|your)\s+(instructions|rules|guidelines)\b"),
        "prompt_injection",
        "This request cannot be processed.",
    ),
    BlockPattern(
        _p(r"\byou\s+are\s+now\s+(a|an)\s+\w+"),
        "jailbreak",
        "This request cannot be processed.",
    ),
    BlockPattern(
        _p(r"\bact\s+as\s+(if\s+you\s+are|a|an)\s+\w+\s+(without|that\s+has\s+no)\s+(restrictions|limitations|guidelines)\b"),
        "jailbreak",
        "This request cannot be processed.",
    ),
    BlockPattern(
        _p(r"\bDAN\b|\bdo\s+anything\s+now\b"),
        "jailbreak",
        "This request cannot be processed.",
    ),
    BlockPattern(
        _p(r"\bpretend\s+(you\s+are|to\s+be)\s+.{0,40}(no\s+restrictions|no\s+limits|no\s+rules)\b"),
        "jailbreak",
        "This request cannot be processed.",
    ),
    BlockPattern(
        _p(r"\bsystem\s*prompt\b|\bsystem\s*instructions\b"),
        "prompt_injection",
        "This request cannot be processed.",
    ),
]

ALL_PATTERNS: List[BlockPattern] = TRADING_ADVICE_PATTERNS + INJECTION_PATTERNS

# ---------------------------------------------------------------------------
# SEBI compliance response template
# Returned to user when a trading/investment advice query is blocked.
# Injection blocks return a minimal message (don't explain what triggered).
# ---------------------------------------------------------------------------

COMPLIANCE_RESPONSE = (
    "LedgerMind is a financial research tool and cannot provide {reason_detail}. "
    "This is by design to remain SEBI-compliant. "
    "Please rephrase your question as a factual research query — for example:\n"
    "  • 'What was Zomato's revenue in FY26?'\n"
    "  • 'What risk factors does Eternal disclose in their Q4FY26 filing?'\n"
    "  • 'How has Paytm's EBITDA margin trended over the past 3 years?'"
)

INJECTION_RESPONSE = (
    "This request cannot be processed. "
    "If you have a financial research question, please ask it directly."
)

_CATEGORY_DETAIL = {
    "trading_advice":    "trading recommendations or buy/sell advice",
    "investment_advice": "investment recommendations or opinions on stock quality",
    "price_prediction":  "price predictions or target price estimates",
    "portfolio_advice":  "portfolio management advice",
    "prompt_injection":  None,
    "jailbreak":         None,
}


# ---------------------------------------------------------------------------
# Core shield function
# ---------------------------------------------------------------------------

def check_query(query: str) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Run all block patterns against the query.

    Returns:
      (is_blocked, block_reason, user_response)
      is_blocked=False → query is clean, proceed
      is_blocked=True  → return user_response to caller immediately

    Does NOT mutate state — pure function. The LangGraph node wraps this.
    """
    query_stripped = query.strip()

    if not query_stripped:
        return True, "empty_query", "Please enter a question to begin."

    for bp in ALL_PATTERNS:
        if bp.pattern.search(query_stripped):
            detail = _CATEGORY_DETAIL.get(bp.category)

            if detail is None:
                # Injection / jailbreak — minimal response
                user_response = INJECTION_RESPONSE
            else:
                user_response = COMPLIANCE_RESPONSE.format(reason_detail=detail)

            logger.info(
                "Prompt Shield BLOCKED | category=%s | query_preview='%s'",
                bp.category,
                query_stripped[:60],
            )
            return True, f"{bp.category}: {bp.reason}", user_response

    logger.debug("Prompt Shield PASSED | query_preview='%s'", query_stripped[:60])
    return False, None, None


# ---------------------------------------------------------------------------
# LangGraph node
# ---------------------------------------------------------------------------

def prompt_shield_node(state: QueryState) -> QueryState:
    """
    LangGraph node: runs prompt shield, sets is_blocked + block_reason.

    If blocked, response_text is also set so the graph can terminate
    immediately and return the compliance message to the user.
    """
    is_blocked, block_reason, user_response = check_query(state["query"])

    state["is_blocked"] = is_blocked
    state["block_reason"] = block_reason

    if is_blocked:
        state["response_text"] = user_response

    return state