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
from dataclasses import dataclass
from typing import Optional, Type

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

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# Milliseconds. Bounds are multiples of measured production p50 (~1s for
# 200-token calls, ~1.3s for synthesis), tight enough that the 78s tail
# observed on 2026-07-29 cannot recur.
TIMEOUT_STRUCTURED_MS = 8_000
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


def _should_fall_back(exc: Exception) -> bool:
    """
    Narrow trigger — see module docstring. Matches on the string form because
    the SDK surfaces status codes inconsistently across error types, and a
    missed match degrades to "no fallback" (the current behaviour) rather
    than to a wrong answer.
    """
    s = f"{type(exc).__name__}: {exc}".lower()
    markers = (
        "timeout", "timed out", "deadline",
        "429", "resource_exhausted", "rate limit", "rate_limit",
        "500", "502", "503", "504", "unavailable", "internal server error",
    )
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
        client = _get_gemini(timeout_ms)
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=temperature,
                max_output_tokens=max_tokens,
            ),
        )
        return LLMResult(resp.text.strip(), "gemini", GEMINI_MODEL)
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
        client = _get_gemini(timeout_ms)
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=temperature,
                max_output_tokens=max_tokens,
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        return LLMResult(resp.text.strip(), "gemini", GEMINI_MODEL)
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
