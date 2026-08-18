"""
LedgerMind — Shared LLM Client
================================
One place where an LLM call is made. Three call sites use it: router
classification, DSL generation, and semantic synthesis.

WHY THIS MODULE EXISTS
----------------------
Two independent defects motivated it, both found in production 2026-07-29:

1. UNBOUNDED TAIL LATENCY. No Gemini call site set a timeout. Measured the
   same query three times: 3.07s / 120.0s (client gave up, call still
   running) / 3.00s. Render logs confirmed a SINGLE call — "AFC remote call
   1 is done" 78s after "AFC is enabled" — not SDK retry. Everything
   downstream completed in under a second. The request returned 200 and
   looked normal in the audit log, which is the same silent-degradation
   class as the old user_id="anonymous" bug.

2. NO FALLBACK. Blueprint §17 promises "Gemini rate-limited → route to
   Groq". Confirmed never implemented: config.py had a groq_api_key field
   and zero call sites. During a real 429 storm all three sites logged the
   error and fell through to degraded paths.

The two are causally linked, which is why they are fixed together: a
timeout converts an unbounded hang into a catchable exception at a bound
we choose, and only then is there anything for a fallback to catch. A
fallback keyed on exceptions would never have fired on defect 1.

Three call sites rather than one wrapper would mean three fallback ladders
that drift apart — the same failure class as this project's two formula
copies and three metric registries. One module, two entry points.

FALLBACK TRIGGER IS DELIBERATELY NARROW
---------------------------------------
Timeout, 429, and 5xx only. A parse error or empty response is model
behaviour Groq will likely reproduce; retrying those just doubles latency
before the same failure. One attempt per provider, no backoff — if
Gemini's daily bucket is exhausted, sleeping inside a request cannot help.

STRUCTURED OUTPUT IS NOT SYMMETRIC
-----------------------------------
Gemini's response_schema guarantees the output parses into the Pydantic
model. Groq offers only response_format={"type":"json_object"}, which
guarantees valid JSON — not the requested shape. So the Groq path
serialises the schema into the system prompt and validates the result
against the model itself. A schema miss on Groq is treated as a PROVIDER
FAILURE, not a parse error: it raises rather than handing a malformed DSL
to validate_dsl.
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Callable, Optional, Type

from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# os.getenv rather than app.core.config.Settings: every engine already reads
# keys this way, and Settings' llm fields are currently unused. Introducing a
# third pattern would be worse than the existing inconsistency. Consolidating
# onto Settings is a separate, deliberate cleanup.

# NO DEFAULT for GEMINI_MODEL, deliberately. A plausible-but-wrong default is
# strictly worse than a crash here: on 2026-07-31 two full eval sweeps were
# reported under a model that never served a single call, and the environment
# is the only thing that decides which model actually runs. The crash costs
# five minutes; the wrong default cost ~60 calls and two unusable result files.
#
# Resolved at CALL time rather than import time so a missing var fails in the
# entrypoints that actually make LLM calls, rather than crashing the startup
# of every entrypoint that merely imports this module (the Celery worker
# imports it transitively and may never call one).
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")


def _resolve_gemini_model() -> str:
    model = os.getenv("GEMINI_MODEL")
    if not model:
        raise RuntimeError(
            "GEMINI_MODEL environment variable not set. Refusing to guess: the "
            "model that served a call is recorded as evidence on every audit "
            "row and asserted by the eval gate, so a defaulted value would "
            "silently misattribute every downstream number."
        )
    return model

# Milliseconds. Bounds were multiples of measured production p50 (~1s for
# 200-token calls, ~1.3s for synthesis), tight enough that the 78s tail
# observed on 2026-07-29 cannot recur.
#
# 2026-08-13: structured raised 8_000 -> 20_000. The claim is about the TAIL,
# not the median -- two eight-call samples an hour apart gave medians of
# ~5.7s and ~2.9s, so neither is a p50 and the distribution is wide and
# unstable. What both samples agree on: calls routinely exceed 8s. Sample A
# (bound at 8s): 3 of 8 timed out at the ceiling. Sample B (bound at 20s):
# 8 of 8 served, including calls at 9511 and 9555 ms that the old bound
# would have killed. A sweep between the two lost 15 of 48 answers to the
# fallback and was withheld on the provider gate.
# NOT a separate hang population: no call resembled the 120s case above.
#
# The tight bound was also SLOWER than a generous one: a timeout costs the
# full 8s and then a Groq call (~8.8s observed) versus ~5.7s served
# correctly. 20s still bounds the hang this header describes.
#
# Env-overridable because this constant has been wrong once and the right
# value is empirical. Default lives HERE, not in compose -- a deploy without
# the var must get the correct value, and two answers to "what is the
# timeout" is the defect this module criticises in GEMINI_MODEL.
# Cost of the change: a genuine Gemini outage now takes 20s per call before
# falling back, up from 8s. Correctness over latency; outages are rare.
TIMEOUT_STRUCTURED_MS = int(os.getenv("TIMEOUT_STRUCTURED_MS", "20000"))
TIMEOUT_TEXT_MS = 20_000
GROQ_TIMEOUT_S = 20.0


@dataclass
class LLMResult:
    """
    text     — raw model output
    provider — "gemini" | "groq"
    model    — resolved model id

    provider and model are carried all the way to the audit log. An answer
    produced by the fallback is a materially different artifact from one
    produced by the primary, and must not be indistinguishable from it.
    """
    text: str
    provider: str
    model: str


class LLMUnavailable(RuntimeError):
    """Both providers failed. Callers must not retry — see module docstring."""


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

_gemini_client: Optional[genai.Client] = None
_groq_client = None


def _get_gemini(timeout_ms: int) -> genai.Client:
    # Timeout is a per-client property in this SDK, and the two entry points
    # need different bounds, so the client is rebuilt when the bound changes.
    # Construction is cheap (no connection is opened here).
    global _gemini_client
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY environment variable not set")
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=timeout_ms),
    )


def _get_groq():
    global _groq_client
    if _groq_client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise LLMUnavailable("GROQ_API_KEY not set — no fallback available")
        from groq import Groq  # imported lazily so a missing dep can't break startup
        _groq_client = Groq(api_key=api_key, timeout=GROQ_TIMEOUT_S)
    return _groq_client


# Gemini enforces BOTH a per-minute and a per-day quota, and returns 429 for
# either. They need opposite handling: an RPM limit refills in seconds and the
# server tells us exactly how long to wait, so falling straight to Groq there
# abandons the better model — and a Groq-served answer is not eval-comparable
# to a Gemini one. A daily limit does not refill, so sleeping is pointless.
#
# Confirmed live 2026-07-29: a 429 arrived with retryDelay 2s while the daily
# counter stood at 277/500. Google's error body labels the quotaId
# "...PerDay..." in BOTH cases, so the id cannot be trusted to tell them
# apart — the retry delay can.
_RETRY_DELAY_RE = re.compile(
    r"'retryDelay':\s*'(\d+(?:\.\d+)?)s'|retry in (\d+(?:\.\d+)?)s",
    re.IGNORECASE,
)
MAX_RPM_RETRY_WAIT_S = 5.0


def _short_retry_delay(exc: Exception) -> Optional[float]:
    """Server-advised wait, if it is short enough to be worth honouring."""
    m = _RETRY_DELAY_RE.search(str(exc))
    if not m:
        return None
    delay = float(m.group(1) or m.group(2))
    return delay if 0 < delay <= MAX_RPM_RETRY_WAIT_S else None


def _call_gemini(fn: Callable):
    """
    One attempt, plus exactly one retry if the server asked for a short wait.
    No ladder: a second failure means the condition is not transient and the
    caller should fall through to Groq rather than keep a user waiting.
    """
    try:
        return fn()
    except Exception as e:
        delay = _short_retry_delay(e)
        if delay is None:
            raise
        logger.info("Gemini rate-limited — honouring server retryDelay %.1fs", delay)
        time.sleep(delay + 0.2)
        return fn()


def _should_fall_back(exc: Exception) -> bool:
    """
    Narrow trigger — see module docstring. Matches on the string form because
    the SDK surfaces status codes inconsistently across error types, and a
    missed match degrades to "no fallback" (the current behaviour) rather
    than to a wrong answer.
    """
    s = f"{type(exc).__name__}: {exc}".lower()
    markers = (
        # transport-level: a bound was hit, or the socket never opened
        "timeout", "timed out", "deadline", "connection", "connecterror",
        "network", "unreachable", "max retries", "remote end closed",
        # provider-level
        "429", "resource_exhausted", "rate limit", "rate_limit",
        "500", "502", "503", "504", "unavailable", "internal server error",
    )
    # Deliberately NOT here: 401/403/invalid-argument. A bad key or a
    # malformed request is a config error, and serving those from the
    # fallback would hide the real fault (2026-07-29: the 1ms probe
    # surfaced as ConnectionError, not "timeout", and was missed by an
    # earlier, narrower list — transport failures are exactly the case
    # blueprint 17 exists for).
    return any(m in s for m in markers)


# ---------------------------------------------------------------------------
# Entry point 1 — free text
# ---------------------------------------------------------------------------

def generate_text(
    system: str,
    user: str,
    temperature: float = 0.2,
    max_tokens: int = 400,
    timeout_ms: int = TIMEOUT_TEXT_MS,
) -> LLMResult:
    try:
        gemini_model = _resolve_gemini_model()
        client = _get_gemini(timeout_ms)
        resp = _call_gemini(lambda: client.models.generate_content(
            model=gemini_model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=temperature,
                max_output_tokens=max_tokens,
            ),
        ))
        return LLMResult(resp.text.strip(), "gemini", gemini_model)
    except Exception as e:
        if not _should_fall_back(e):
            logger.error("Gemini text call failed (no fallback): %s", e)
            raise LLMUnavailable(str(e)) from e
        logger.warning("Gemini text call failed (%s) — falling back to Groq", e)

    try:
        resp = _get_groq().chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            raise LLMUnavailable("Groq returned empty text")
        logger.info("Groq fallback served text call | model=%s", GROQ_MODEL)
        return LLMResult(text, "groq", GROQ_MODEL)
    except LLMUnavailable:
        raise
    except Exception as e:
        logger.error("Groq fallback failed: %s", e)
        raise LLMUnavailable(f"Both providers failed: {e}") from e


# ---------------------------------------------------------------------------
# Entry point 2 — structured JSON
# ---------------------------------------------------------------------------

def generate_structured(
    system: str,
    user: str,
    schema: Type[BaseModel],
    temperature: float = 0.0,
    max_tokens: int = 200,
    timeout_ms: int = TIMEOUT_STRUCTURED_MS,
) -> LLMResult:
    try:
        gemini_model = _resolve_gemini_model()
        client = _get_gemini(timeout_ms)
        resp = _call_gemini(lambda: client.models.generate_content(
            model=gemini_model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=temperature,
                max_output_tokens=max_tokens,
                response_mime_type="application/json",
                response_schema=schema,
            ),
        ))
        return LLMResult(resp.text.strip(), "gemini", gemini_model)
    except Exception as e:
        if not _should_fall_back(e):
            logger.error("Gemini structured call failed (no fallback): %s", e)
            raise LLMUnavailable(str(e)) from e
        logger.warning("Gemini structured call failed (%s) — falling back to Groq", e)

    # Groq has no response_schema. The shape is described in the prompt and
    # then verified here; an unverifiable response is a provider failure.
    schema_json = json.dumps(schema.model_json_schema(), indent=2)
    groq_system = (
        f"{system}\n\n"
        f"Return ONLY a JSON object conforming to this schema. "
        f"No markdown, no commentary.\n\n{schema_json}"
    )

    try:
        resp = _get_groq().chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": groq_system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        text = (resp.choices[0].message.content or "").strip()
        schema.model_validate_json(text)  # shape guarantee Gemini gives for free
        logger.info("Groq fallback served structured call | model=%s", GROQ_MODEL)
        return LLMResult(text, "groq", GROQ_MODEL)
    except (ValidationError, json.JSONDecodeError) as e:
        logger.error("Groq fallback returned off-schema JSON: %s", e)
        raise LLMUnavailable(f"Groq output failed schema validation: {e}") from e
    except Exception as e:
        logger.error("Groq fallback failed: %s", e)
        raise LLMUnavailable(f"Both providers failed: {e}") from e
