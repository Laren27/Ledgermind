"""
LedgerMind — eval scorer exclusion post-conditions
==================================================
Run: docker compose exec -T -w /app backend python3 -m scripts.test_eval_exclusion

Verifies the synthesis_unavailable EXCLUSION path in scripts/eval_runner.py
(commit d827dd1) without an LLM outage, without a network call and without
quota. Both units under test -- score_result() and print_report() -- are pure
functions over dicts, so every fixture here is a literal.

Fixtures are literal dicts and never read golden_dataset/. A test that loads
live golden data starts failing when that data legitimately changes, which
teaches the reader to ignore it.

Why sys.argv is patched before the import: eval_runner calls parse_args() at
MODULE level (line 114) and --model is required, so a bare import dies on
whatever argv the caller happened to have. Patching argv is the small,
reversible option. Restructuring eval_runner to move parse_args() under main()
would be a larger change than the fix under test, and it is deliberately not
done here. Nothing else runs at import: the only work after parse_args is the
--out derivation and the golden_dataset guard, neither of which touches disk.
"""

import io
import sys
from contextlib import redirect_stdout

MODEL = "gemini-3.1-flash-lite"

_real_argv = sys.argv
sys.argv = ["eval_runner", "--model", MODEL]
try:
    import scripts.eval_runner as er
finally:
    sys.argv = _real_argv

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        FAILURES.append(name)


def score(golden, result):
    """
    Mirrors main()'s call site exactly: score_result(), then the one
    setdefault that gives every verdict an `excluded` key. Tests go through
    this rather than score_result() directly so they assert the contract as
    the runner actually applies it -- score_result's ~30 other branches
    deliberately do NOT set the key themselves.
    """
    s = er.score_result(golden, result)
    s.setdefault("excluded", False)
    return s


def row(qid, category, passed, excluded=False, provider="gemini",
        model=MODEL, backend="cohere", blocked=False):
    """One entry of the list print_report() consumes."""
    return {
        "id": qid,
        "category": category,
        "question": f"question text for {qid}",
        "score": {"pass": passed, "excluded": excluded, "reason": "fixture"},
        "api_response": {
            "is_blocked": blocked, "llm_provider": provider,
            "llm_model": model, "reranker_backend": backend,
        },
    }


def report(rows):
    buf = io.StringIO()
    with redirect_stdout(buf):
        er.print_report(rows, MODEL)
    return buf.getvalue()


# An outage as the API reports it: error set, tier capped to low by design,
# nothing SQL-verified. See scripts/test_synthesis_floor.py section 1.
OUTAGE = {"error": "synthesis_unavailable", "confidence_tier": "low",
          "sql_verified": False, "citations": [], "response_text": ""}

G_OOC = {"id": "X1", "category": "out_of_corpus",
         "expected_error": "company_not_in_corpus"}
G_CROSS_LOW = {"id": "X2", "category": "cross_examination",
               "expected_tier_low": True, "expected_keywords": []}
G_SEM = {"id": "X3", "category": "semantic_risk",
         "expected_keywords": ["attrition"]}
G_QUANT = {"id": "X4", "category": "quantitative_point", "expected_value": 366.0}


print("\n1. the early return — the vacuous passes it closes")
# BEFORE d827dd1 THIS FIXTURE RETURNED pass=True. out_of_corpus passes on
# (not sql_verified) and tier == "low"; a synthesis outage forces both, so the
# question was recorded as a correct refusal while no synthesis had happened
# at all. The pass was satisfied by the outage, not by the behaviour the
# question exists to test. That is what this check is for -- do not delete it
# because "out_of_corpus obviously passes on a refusal".
s = score(G_OOC, OUTAGE)
check("out_of_corpus outage is excluded", s["excluded"] is True, s)
check("out_of_corpus outage is not a pass", s["pass"] is False, s["pass"])

# Same shape: expected_tier_low asserts tier == "low", and the outage supplies
# exactly that. The assertion the question exists to make was satisfied by the
# infrastructure failing.
s = score(G_CROSS_LOW, OUTAGE)
check("cross_examination expected_tier_low outage is excluded", s["excluded"] is True, s)
check("cross_examination outage is not a pass", s["pass"] is False, s["pass"])

s = score(G_SEM, OUTAGE)
check("semantic outage is excluded", s["excluded"] is True, s)
check("exclusion reason names the outage",
      "synthesis_unavailable" in s["reason"], s["reason"])


print("\n2. non-interference — an over-broad guard would break these")
# A genuine refusal, no outage: must still PASS on its own merits.
genuine_refusal = {"sql_verified": False, "confidence_tier": "low",
                   "error": "company_not_in_corpus", "citations": []}
s = score(G_OOC, genuine_refusal)
check("genuine out_of_corpus refusal still passes", s["pass"] is True, s["reason"])
check("genuine refusal is not excluded", s["excluded"] is False, s)

# A genuine low tier with no outage: must still FAIL, and with the reason
# string that states what was actually checked rather than asserting a cause.
genuine_low = {"confidence_tier": "low", "response_text": "some answer",
               "error": None, "sql_verified": False}
s = score(G_SEM, genuine_low)
check("genuine low-tier semantic still fails", s["pass"] is False, s["reason"])
check("semantic reason states tier, not a guessed cause",
      "not a synthesis outage" in s["reason"], s["reason"])
check("semantic reason no longer says 'likely'",
      "likely" not in s["reason"], s["reason"])

# The cross branch's OWN reason string, same change.
cross_low_unexpected = {"confidence_tier": "low", "response_text": "text",
                        "error": None, "sql_verified": False, "contradictions": []}
s = score({"id": "X5", "category": "cross_examination", "expected_keywords": []},
          cross_low_unexpected)
check("cross reason states tier, not a guessed cause",
      "not a synthesis outage" in s["reason"], s["reason"])

# A cross question that legitimately expects tier=low must still pass when the
# low tier is real rather than an outage artifact.
s = score(G_CROSS_LOW, cross_low_unexpected)
check("legitimate expected_tier_low still passes", s["pass"] is True, s["reason"])
check("legitimate expected_tier_low not excluded", s["excluded"] is False, s)

# An ordinary passing question in an unrelated category is untouched.
good_quant = {"sql_verified": True, "error": None, "is_blocked": False,
              "sql_result": [{"value": 366.0, "metric": "pat"}]}
s = score(G_QUANT, good_quant)
check("ordinary quantitative pass untouched", s["pass"] is True, s["reason"])
check("ordinary pass reports excluded False", s["excluded"] is False, s)


print("\n3. the tally")
# 3a. Nothing excluded: the score line must be BYTE-IDENTICAL to the format
# that produced every recorded baseline. If this drifts, an old sweep and a
# new one stop being comparable by eye.
out = report([row("A1", "semantic_risk", True), row("A2", "semantic_risk", True),
              row("A3", "semantic_risk", True), row("A4", "semantic_risk", False)])
check("clean run prints the pre-change score line verbatim",
      "Total:  4  |  Pass: 3  |  Fail: 1  |  Score: 75.0%" in out,
      [l for l in out.splitlines() if l.startswith("Total:")])
check("clean run says nothing about exclusions",
      "excluded" not in out.lower(), "unexpected exclusion text in a clean run")

# 3b. Some excluded: numerator AND denominator both drop.
out = report([row("B1", "semantic_risk", True), row("B2", "semantic_risk", True),
              row("B3", "semantic_risk", True), row("B4", "semantic_risk", False),
              row("B5", "out_of_corpus", False, excluded=True)])
check("excluded question leaves the denominator",
      "Total:  4  |  Pass: 3  |  Fail: 1  |  Score: 75.0%" in out,
      [l for l in out.splitlines() if l.startswith("Total:")])
check("score line carries the exclusion count",
      "Excluded: 1 (synthesis_unavailable)" in out)
check("excluded id is listed, not just counted", "[B5" in out, out)
check("excluded listing names the outage",
      "Excluded (1 of 5, synthesis_unavailable" in out)
check("excluded question is not listed as a failure",
      "B5" not in out.split("Failures")[-1] if "Failures" in out else True)

# 3c. Everything excluded: the run measured nothing and must say so rather
# than dividing by zero. Attribution is kept clean in this fixture so the
# 0-scored branch is reachable in isolation; in a REAL total outage llm_model
# is cleared too, so the model gate would also fire.
out = report([row("C1", "semantic_risk", False, excluded=True),
              row("C2", "semantic_risk", False, excluded=True),
              row("C3", "out_of_corpus", False, excluded=True)])
check("all-excluded run reports zero scored",
      "Total:  0 scored" in out, [l for l in out.splitlines() if l.startswith("Total:")])
check("all-excluded run states it measured nothing",
      "measured nothing" in out)

# 3d. A category whose every question was excluded must still appear. If it
# silently vanished, a reader would conclude it was never run.
out = report([row("D1", "semantic_risk", False, excluded=True),
              row("D2", "semantic_risk", False, excluded=True),
              row("D3", "quantitative_point", True),
              row("D4", "quantitative_point", True)])
cat_block = out.split("By category:")[1]
check("fully-excluded category still appears under By category",
      "semantic_risk" in cat_block, cat_block)
check("fully-excluded category shows 0/0 plus its excluded count",
      "0/0" in cat_block and "(+2 excluded)" in cat_block, cat_block)
check("partially scored category unaffected", "2/2" in cat_block, cat_block)

# 3e. Contaminated AND carrying exclusions: BOTH facts must be visible. The
# withholding gate is unchanged; it now quotes the shared tally string.
out = report([row("E1", "semantic_risk", True), row("E2", "semantic_risk", False),
              row("E3", "semantic_risk", True, provider="groq"),
              row("E4", "out_of_corpus", False, excluded=True)])
check("contaminated run still withholds the score",
      "SCORE WITHHELD — NOT A VALID BASELINE" in out, out[:200])
check("withheld raw tally carries the exclusion suffix",
      "Raw tally (DO NOT publish): 2/3 (1 excluded: synthesis_unavailable)" in out,
      [l for l in out.splitlines() if "Raw tally" in l])
check("withheld run does not also print the clean score line",
      "Score: " not in out, [l for l in out.splitlines() if "Score:" in l])


print("\n4. the JSON contract")
# main()'s meta block is inline and cannot be imported without running the
# sweep, so this asserts the ROW-LEVEL contract that block is computed from,
# using the same expressions main() uses. It does not execute main().
rows = [
    {"id": "F1", "score": score(G_SEM, {"confidence_tier": "high",
                                        "response_text": "attrition risk noted",
                                        "error": None})},
    {"id": "F2", "score": score(G_OOC, OUTAGE)},
    {"id": "F3", "score": score(G_QUANT, good_quant)},
    {"id": "F4", "score": score(G_CROSS_LOW, OUTAGE)},
]
check("every row's score carries an excluded key",
      all("excluded" in r["score"] for r in rows),
      [r["id"] for r in rows if "excluded" not in r["score"]])

excluded_count = sum(1 for r in rows if r["score"].get("excluded"))
excluded_ids = [r["id"] for r in rows if r["score"].get("excluded")]
check("excluded_count equals len(excluded_ids)",
      excluded_count == len(excluded_ids), (excluded_count, excluded_ids))
check("excluded_ids are exactly the outage questions",
      excluded_ids == ["F2", "F4"], excluded_ids)
check("non-excluded rows are excluded=False, not missing",
      all(r["score"]["excluded"] is False for r in rows if r["id"] not in ("F2", "F4")),
      [(r["id"], r["score"].get("excluded")) for r in rows])


print("\n" + "=" * 52)
if FAILURES:
    print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
    sys.exit(1)
print("ALL CHECKS PASSED")
