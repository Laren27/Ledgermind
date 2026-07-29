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
