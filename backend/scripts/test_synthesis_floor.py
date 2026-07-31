"""
LedgerMind — synthesis-floor post-conditions
=============================================
Run: docker compose exec -T backend python -m scripts.test_synthesis_floor

Verifies the synthesis_unavailable path WITHOUT an LLM outage. The behaviour
is a pure function of "both providers raised", so generate_text is
monkeypatched to raise LLMUnavailable. Deterministic, repeatable, zero quota.

Why not tamper with GEMINI_API_KEY instead: a bad key raises 400
INVALID_ARGUMENT, which _should_fall_back() deliberately does NOT match, so
no Groq attempt is made and the failure never resembles a real outage.

Why not an LLM_FORCE_UNAVAILABLE env flag: a test-only branch in a production
code path is exactly the kind of thing that later gets left on.
"""

import sys

import app.engines.response_generator as rg
from app.engines.state import make_initial_state, record_llm_call
from app.llm.client import LLMResult, LLMUnavailable

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        FAILURES.append(name)


def _chunk(text="Management noted continued investment in delivery."):
    return {
        "chunk_id": "c1", "doc_id": "d1", "text": text, "page_number": 12,
        "company": "ETERNAL", "fiscal_year": "FY26", "quarter": None,
        "financial_type": "unknown", "chunk_type": "TEXT",
        "filing_date": "2026-05-01", "dense_score": 0.9, "sparse_score": 0.4,
        "rrf_score": 0.03, "reranker_score": 0.91, "reranker_backend": "cohere",
    }


def _state(**over):
    s = make_initial_state("q", "t-1", "u-1", "r-1")
    s.update(over)
    return s


class _Outage:
    """Monkeypatch context: both providers raise, as in a real total outage."""
    def __enter__(self):
        self._real = rg.generate_text
        def boom(*a, **k):
            raise LLMUnavailable("Both providers failed: simulated")
        rg.generate_text = boom
    def __exit__(self, *exc):
        rg.generate_text = self._real


print("\n1. semantic path, total outage")
with _Outage():
    st = _state(path="semantic", retrieved_chunks=[_chunk()])
    # Router attribution set FIRST — the real bug's ordering. If clearing is
    # missing, this survives and the outage logs as a Gemini-served answer.
    record_llm_call(st, LLMResult("{}", "gemini", "gemini-3.1-flash-lite"))
    out = rg.response_generator_node(st)

check("error is synthesis_unavailable", out["error"] == "synthesis_unavailable", out["error"])
check("error_node is response_generator", out["error_node"] == "response_generator")
check("tier capped to low", out["confidence_tier"] == "low", out["confidence_tier"])
check("llm_provider cleared", out["llm_provider"] is None, out["llm_provider"])
check("llm_model cleared", out["llm_model"] is None, out["llm_model"])
check("floor text served", "Unable to synthesise" in out["response_text"])

print("\n2. empty retrieval is NOT an outage")
st = _state(path="semantic", retrieved_chunks=[])
out = rg.response_generator_node(st)
check("no synthesis_unavailable", out["error"] != "synthesis_unavailable", out["error"])

print("\n3. cross path, outage + verified figure")
with _Outage():
    st = _state(
        path="cross", retrieved_chunks=[_chunk()], confidence_tier="high",
        sql_verified=True,
        sql_result=[{"value": 366.0, "fiscal_year": "FY26", "quarter": None,
                     "unit": "crore_inr", "metric": "pat"}],
        dsl_object={"metric": "pat", "entity": "ETERNAL", "period": "FY26",
                    "fiscal_year": "FY26", "quarter": None,
                    "financial_type": "consolidated", "operation": "point_in_time",
                    "comparison_entity": None, "comparison_period": None},
    )
    record_llm_call(st, LLMResult("{}", "gemini", "gemini-3.1-flash-lite"))
    out = rg.response_generator_node(st)

check("error is synthesis_unavailable", out["error"] == "synthesis_unavailable", out["error"])
check("tier medium (quant half intact)", out["confidence_tier"] == "medium", out["confidence_tier"])
check("verified figure preserved", "366" in out["response_text"])
check("floor apology not concatenated as analysis",
      "Unable to synthesise" not in out["response_text"])
check("llm attribution cleared", out["llm_provider"] is None and out["llm_model"] is None)

print("\n4. record_llm_call taint precedence")
a = _state()
record_llm_call(a, LLMResult("", "gemini", "gemini-3.1-flash-lite"))
record_llm_call(a, LLMResult("", "groq", "llama-3.3-70b-versatile"))
check("gemini then groq -> groq", a["llm_provider"] == "groq", a["llm_provider"])
check("model follows provider", a["llm_model"] == "llama-3.3-70b-versatile", a["llm_model"])

b = _state()
record_llm_call(b, LLMResult("", "groq", "llama-3.3-70b-versatile"))
record_llm_call(b, LLMResult("", "gemini", "gemini-3.1-flash-lite"))
check("groq then gemini -> groq (order-independent)", b["llm_provider"] == "groq", b["llm_provider"])

print("\n" + "=" * 52)
if FAILURES:
    print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
    sys.exit(1)
print("ALL CHECKS PASSED")
