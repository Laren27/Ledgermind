# 2026-07-29
BASELINE: 81/83 on gemini-3.1-flash-lite, LOCAL Docker stack
  Eternal 51/52 (Q038 fail) | Titan 13/14 (TQ010 fail) | Paytm 17/17
  All quantitative + adversarial + out_of_corpus at 100%.
  Eval numbers are only comparable against a stated model AND a stated
  environment — prod semantic_engine is ~14x faster than local (Cohere vs
  local ONNX), so a prod sweep may not reproduce this figure.

CLOSED (locally — NOT yet verified in prod, see OPEN 0)
  - Paytm Qdrant payload drift (FY99) — repaired in place, crag_count 0
  - Titan "undersized" — non-bug, 24 chunks correct for 18-page release
  - EBITDA silent substitution — sql_verified wrong answer, now refused pre-LLM
  - ON CONFLICT wrote only ingestion_state — root cause of the drift
  - _is_refusal_text short-response short-circuit
  - Keyword brittleness: PQ010, PQ011, Q028, Q039

OPEN (priority order)
  0. Render deploy + verify PQ017/EBITDA in prod (GATES all CLOSED items above)
  1. Groq fallback — CONFIRMED NEVER BUILT. config.py has groq_api_key and
     nothing else; zero call sites in app/. Blueprint 17 claims a guarantee
     that does not exist in code.
  2. TQ010 — 'chartered accountants' didn't survive model change.
     Prefer invariant: audit date or 'unmodified opinion'.
  3. Q038 — failed on 2 models, several keyword sets. Recommend retire;
     Q036 covers same SRE 2410 limitation cleanly.
  4. Paytm source PDF missing from docs/raw/
  5. Minor: basicConfig timing swallows router model log; qdrant_writer .env
     path bug; regression_check.py L47 Titan filing_date 2025-07-31 → 2025-08-07;
     _format_citations_block still renders "(unknown)"

LESSONS
  - Verify across MODELS, not just across runs (TQ010 is the counterexample)
  - 500/day quota: full sweeps first while fresh, hand-debugging second
  - Read full response text, not the 200-char eval preview (cost time 4x today)

## FOUND 2026-07-29 (post-deploy verification)
PRIORITY 0 CLOSED — EBITDA guard confirmed live in prod:
  error=metric_not_computable, error_node=quant_engine, sql_verified=false,
  path=quantitative (router classified correctly, Stage 0 fired pre-DSL).
  All five of the session's local fixes are deployed at 099b369.

NEW LEAD ITEM — UNBOUNDED GEMINI TAIL LATENCY (outranks Groq):
  Same query ("Eternal revenue FY26") x3: 3.07s / 120.0s (curl --max-time
  hit, still waiting) / 3.00s. Fast runs match the recorded 3.2s baseline,
  so nothing is structurally slow — the tail is unbounded.
  Confirmed from Render logs it is ONE call, not SDK retry:
    14:44:42 AFC is enabled  →  14:46:00 AFC remote call 1 is done  (78s)
    everything downstream (quant_engine, Stage 0 guard, audit) < 1s.
  No Gemini call site sets a timeout, so a slow provider blocks the request
  with no ceiling. Returns 200, looks normal in the audit log — same silent
  degradation class as user_id="anonymous" and the router exception-swallow.
  On Vercel this is a hard-kill with no application hook, not a slow answer.
  FIX ORDER: (1) explicit per-call timeout via types.HttpOptions — converts
  an unbounded hang into a catchable exception at a chosen bound;
  (2) Groq fallback catches that exception. Timeout first — it is a real
  improvement standalone, and without it a fallback keyed on exceptions
  would never fire on this failure mode.
  Proposed bounds from measured p50: router/DSL 8s (200 tok), synthesis 20s
  (400 tok).

GROQ: blueprint 17's llama-3.1-70b is RETIRED. Pin llama-3.3-70b-versatile
  (JSON mode, 128k ctx, free tier). 17 needs a correction commit.

## SHIPPED 2026-07-29 — app/llm/client.py (10 commits)
Two defects fixed together because they are causally linked:
  (a) unbounded Gemini tail latency — no call site set a timeout
  (b) blueprint 17's Groq fallback — confirmed never implemented
A timeout is what converts an unbounded hang into a catchable exception;
without it a fallback keyed on exceptions can never fire.

DESIGN (verified live, not assumed):
  - Bounds: structured 8s, text 20s, groq 20s.
  - Fallback triggers: timeout, transport failure, 429, 5xx.
    NOT auth/invalid-argument — VERIFIED: an invalid GEMINI_API_KEY raises
    400 INVALID_ARGUMENT and does NOT fall back, so a bad key cannot be
    masked by the fallback.
  - 429 is ambiguous: Google labels the quotaId "...PerDay..." for BOTH the
    per-minute and per-day limit, so only retryDelay distinguishes them.
    <=5s -> sleep once, retry Gemini. Otherwise -> Groq.
    VERIFIED both branches live: 2.8s delay retried; 51s delay fell through.
  - Groq has no response_schema, only json_object. generate_structured
    injects the schema into the prompt and validates with Pydantic; an
    off-schema response is a PROVIDER failure, never passed to validate_dsl.
    VERIFIED: llama-3.3-70b-versatile returned a valid RouterResponse.
  - quant_engine: LLMUnavailable BREAKS the self-healing loop (that loop
    repairs bad DSL; a repair hint cannot fix an absent provider).
  - llm_provider recorded on QueryState, admin-tier only. This paid for
    itself within the hour: all three local end-to-end tests returned
    llm_provider="groq" and would have looked completely normal otherwise.

ALSO FIXED: requirements.txt had no trailing newline, so "echo groq >>"
produced "cohere==7.0.8groq" — would have failed the next Render build.
frontend/.dockerignore was missing; host node_modules overwrote the image's
and broke COPY (pre-existing, unrelated, surfaced by the rebuild).

NEW MUST HAVE — eval_runner.py must record llm_provider per question and
refuse to report a clean score if ANY answer was Groq-served. Mixed-provider
sweeps are not comparable, and TQ010 is already a model-sensitivity failure.
DO NOT run a sweep before this lands.

NEXT: (1) add GROQ_API_KEY + GROQ_MODEL to Render env — prod has NO fallback
without them; (2) correct blueprint 17 (llama-3.1-70b is retired);
(3) eval_runner provider guard; (4) TQ010/Q038 on fresh quota.
