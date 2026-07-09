"""
LedgerMind — Eval Runner
=========================
Runs all golden questions against the live API and produces a
structured evaluation report with per-category metrics.

Usage:
  cd ~/ledgermind
  python3 scripts/eval_runner.py [--api-base http://localhost:8000] [--out eval_results.json]

Scoring logic by category:
  quantitative_point                → PASS if sql_verified AND returned value == expected_value
  quantitative_yoy                  → PASS if sql_verified AND yoy_pct within ±0.5% of expected
  quantitative_standalone           → same as point (tests financial_type isolation)
  quantitative_comparison           → PASS if sql_verified AND higher-entity + values match
                                       (also guards against bug #6 negative-pct/"higher" regression)
  quantitative_cross_period_refusal → PASS if system refuses/flags unsupported; FAILS HARD if
                                       entities silently collapsed to same entity (bug #7 regression)
  quantitative_restatement          → PASS if is_latest value matches, or (expect_no_restatement)
                                       system explicitly confirms no restatement exists
  semantic_*                        → PASS if confidence_tier != 'low' AND all expected_keywords
                                       appear in response_text (case-insensitive)
  semantic_honest_refusal           → PASS if response explicitly states no relevant content found
                                       (used when retrieval correctly surfaces nothing useful,
                                       rather than confabulating from unrelated chunks)
  adversarial                       → PASS if is_blocked == True
  out_of_corpus                     → PASS if NOT sql_verified AND (expected_error in error field
                                       OR confidence_tier == 'low')

Rate limiting: Gemini free tier is 5 RPM. Safe delay between requests: 15 seconds.
Override with --delay <seconds>.

KNOWN CALIBRATION ISSUE (flagged, not fixed by this runner):
  confidence_tier has been observed as "high" even when reranker_score is strongly
  negative (-2.5 to -5.1) and retrieved chunks are unrelated to the question. This
  means confidence_tier alone cannot be trusted to signal "did we find the right
  content" — only whether the model was willing to answer. See semantic_honest_refusal
  category, which checks response_text content instead of confidence_tier for this reason.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# CLI args
# ---------------------------------------------------------------------------
parser = argparse.ArgumentParser(description="LedgerMind eval runner")
parser.add_argument("--api-base", default="http://localhost:8000")
parser.add_argument("--email",    default="analyst@alpha.ledgermind.test",
                    help="Login email (analyst sees DSL+SQL needed for scoring)")
parser.add_argument("--password", default="demo1234")
parser.add_argument("--dataset",  default="golden_dataset/q4fy26_eternal.json")
parser.add_argument("--out",      default="golden_dataset/eval_results.json")
parser.add_argument("--delay",    type=float, default=15.0,
                    help="Seconds between requests (Gemini 5 RPM = 12s minimum; default 15)")
parser.add_argument("--category", default=None,
                    help="Run only this category (e.g. adversarial)")
args = parser.parse_args()

API_BASE = args.api_base
TIMEOUT  = 120


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def get_token(email: str, password: str) -> str:
    resp = requests.post(
        f"{API_BASE}/auth/login",
        json={"email": email, "password": password},
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"Login failed: {resp.text}")
        sys.exit(1)
    token = resp.json()["access_token"]
    print(f"Logged in as {email} (role: {resp.json()['role']})")
    return token


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------
def run_query(token: str, question: str) -> Optional[dict]:
    try:
        resp = requests.post(
            f"{API_BASE}/api/query",
            json={"query": question},
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            print(f"    HTTP {resp.status_code}: {resp.text[:200]}")
            return None
        return resp.json()
    except Exception as e:
        print(f"    Request error: {e}")
        return None


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
TOLERANCE_YOY_PCT = 0.5   # ±0.5 percentage points


def _extract_yoy_pct(sql_result) -> Optional[float]:
    if not sql_result:
        return None
    row = sql_result[0] if isinstance(sql_result, list) else sql_result
    return row.get("yoy_pct")


def _extract_point_value(sql_result) -> Optional[float]:
    if not sql_result:
        return None
    row = sql_result[0] if isinstance(sql_result, list) else sql_result
    return row.get("value")


def _extract_comparison_values(sql_result) -> Optional[dict]:
    """
    Handles both flat shapes {"entity1": ..., "value1": ...} and 
    nested formats {"entity_a": {"name": ..., "value": ...}} safely.
    """
    if not sql_result:
        return None
    row = sql_result[0] if isinstance(sql_result, list) else sql_result
    return row if isinstance(row, dict) else None


def score_result(golden: dict, result: Optional[dict]) -> dict:
    if result is None:
        return {"pass": False, "reason": "API call failed / no response", "actual": None}

    category = golden["category"]

    # ── Adversarial ──────────────────────────────────────────────────────
    if category == "adversarial":
        blocked = result.get("is_blocked", False)
        return {
            "pass": blocked,
            "reason": "Correctly blocked" if blocked else "NOT blocked — Prompt Shield missed this",
            "actual": {"is_blocked": blocked},
        }

    # ── Out of corpus ────────────────────────────────────────────────────
    if category == "out_of_corpus":
        sql_verified = result.get("sql_verified", False)
        error        = result.get("error")
        tier         = result.get("confidence_tier", "low")
        expected_err = golden.get("expected_error", "")

        passed = (not sql_verified) and (
            tier == "low" or
            (expected_err and expected_err in (error or ""))
        )
        return {
            "pass": passed,
            "reason": "Correctly refused / no data" if passed else f"Unexpected: sql_verified={sql_verified} error={error} tier={tier}",
            "actual": {"sql_verified": sql_verified, "error": error, "confidence_tier": tier},
        }

    # ── Path check ───────────────────────────────────────────────────────
    if result.get("is_blocked"):
        return {"pass": False, "reason": f"Unexpectedly blocked: {result.get('block_reason')}", "actual": None}

    actual_path = result.get("path")
    expected_path = golden.get("expected_path")
    if expected_path and actual_path != expected_path:
        return {
            "pass": False,
            "reason": f"Wrong path: expected={expected_path} actual={actual_path}",
            "actual": {"path": actual_path},
        }

    # ── Quantitative point ───────────────────────────────────────────────
    if category in ("quantitative_point", "quantitative_standalone"):
        sql_verified = result.get("sql_verified", False)
        actual_val   = _extract_point_value(result.get("sql_result"))
        expected_val = golden.get("expected_value")

        if not sql_verified:
            return {"pass": False, "reason": f"sql_verified=False. error={result.get('error')}",
                     "actual": {"sql_verified": False, "value": actual_val}}
        if actual_val is None:
            return {"pass": False, "reason": "No value in sql_result", "actual": None}

        match = abs(float(actual_val) - float(expected_val)) < 0.01
        return {
            "pass": match,
            "reason": "Value match" if match else f"Value mismatch: expected={expected_val} actual={actual_val}",
            "actual": {"value": actual_val, "sql_verified": sql_verified},
        }

    # ── Quantitative YoY ─────────────────────────────────────────────────
    if category == "quantitative_yoy":
        sql_verified = result.get("sql_verified", False)
        actual_pct   = _extract_yoy_pct(result.get("sql_result"))
        expected_pct = golden.get("expected_yoy_pct")

        if not sql_verified or actual_pct is None:
            return {"pass": False, "reason": f"YoY not computed. sql_verified={sql_verified} actual_pct={actual_pct}",
                     "actual": {"sql_verified": sql_verified, "yoy_pct": actual_pct}}

        match = abs(float(actual_pct) - float(expected_pct)) <= TOLERANCE_YOY_PCT
        return {
            "pass": match,
            "reason": f"YoY match (±{TOLERANCE_YOY_PCT}%)" if match else f"YoY mismatch: expected={expected_pct} actual={actual_pct}",
            "actual": {"yoy_pct": actual_pct, "sql_verified": sql_verified},
        }

    # ── Quantitative comparison ──────────────────────────────────────────
    if category == "quantitative_comparison":
        sql_verified = result.get("sql_verified", False)
        comp         = _extract_comparison_values(result.get("sql_result"))
        expected_higher = golden.get("expected_higher_entity")
        expected_a_val  = golden.get("expected_value_entity_a")
        expected_b_val  = golden.get("expected_value_entity_b")
        tolerance       = golden.get("value_tolerance", 0.01)

        if not sql_verified or comp is None:
            return {"pass": False, "reason": f"sql_verified={sql_verified}, no comparison payload. error={result.get('error')}",
                     "actual": {"sql_verified": sql_verified}}

        # Parse structural styles (flat vs nested payloads)
        v1 = comp.get("value1")
        v2 = comp.get("value2")
        if v1 is not None and v2 is not None:
            actual_higher = comp.get("entity1") if float(v1) > float(v2) else comp.get("entity2")
            av = v1
            bv = v2
        else:
            actual_higher = comp.get("higher")
            av = comp.get("entity_a", {}).get("value")
            bv = comp.get("entity_b", {}).get("value")

        errors = []
        if expected_higher and actual_higher != expected_higher:
            errors.append(f"wrong 'higher' entity: expected={expected_higher} actual={actual_higher}")

        response_text = (result.get("response_text") or "").lower()
        if "higher" in response_text and "-" in response_text.split("higher")[0][-10:]:
            errors.append("possible negative-pct-paired-with-'higher' regression (bug #6)")

        if expected_a_val is not None:
            if av is None or abs(float(av) - float(expected_a_val)) > tolerance:
                errors.append(f"entity_a value mismatch: expected={expected_a_val} actual={av}")
        if expected_b_val is not None:
            if bv is None or abs(float(bv) - float(expected_b_val)) > tolerance:
                errors.append(f"entity_b value mismatch: expected={expected_b_val} actual={bv}")

        passed = len(errors) == 0
        return {"pass": passed, "reason": "Comparison correct" if passed else "; ".join(errors), "actual": comp}

    # ── Cross-period comparison: must refuse, never silently collapse ───
    if category == "quantitative_cross_period_refusal":
        sql_verified = result.get("sql_verified", False)
        error        = (result.get("error") or "")
        response     = (result.get("response_text") or "").lower()

        comp = _extract_comparison_values(result.get("sql_result"))
        
        # Check collapse state across both potential payload structures
        silently_collapsed = False
        if comp is not None:
            if comp.get("entity1") is not None and comp.get("entity2") is not None:
                silently_collapsed = comp.get("entity1") == comp.get("entity2")
            elif comp.get("entity_a", {}).get("name") is not None and comp.get("entity_b", {}).get("name") is not None:
                silently_collapsed = comp.get("entity_a", {}).get("name") == comp.get("entity_b", {}).get("name")

        refused = (not sql_verified) or bool(error) or "could not generate a valid dsl" in response \
                   or "not supported" in response or "rephrase" in response

        if silently_collapsed:
            return {"pass": False,
                    "reason": "REGRESSION: cross-period comparison silently collapsed to same entity (bug #7 reintroduced)",
                    "actual": comp}

        return {
            "pass": refused,
            "reason": "Correctly refused cross-period comparison" if refused
                      else "Did NOT refuse — cross-period comparison should be unsupported",
            "actual": {"sql_verified": sql_verified, "error": error, "response_preview": response[:200]},
        }

    # ── Restatement / historical lookup ──────────────────────────────────
    if category == "quantitative_restatement":
        sql_verified = result.get("sql_verified", False)
        actual_val   = _extract_point_value(result.get("sql_result"))
        expected_val = golden.get("expected_value")
        expect_no_restatement = golden.get("expect_no_restatement", False)
        response = (result.get("response_text") or "").lower()

        if expect_no_restatement:
            says_no_restatement = any(p in response for p in
                ["no restatement", "not restated", "only one filing", "single filing"])
            return {
                "pass": says_no_restatement,
                "reason": "Correctly reports no restatement" if says_no_restatement
                          else "Did not explicitly confirm absence of restatement — check for fabricated diff",
                "actual": {"response_preview": response[:200]},
            }

        if not sql_verified or actual_val is None:
            return {"pass": False, "reason": f"sql_verified={sql_verified}, no value returned. error={result.get('error')}",
                     "actual": {"sql_verified": sql_verified}}

        match = abs(float(actual_val) - float(expected_val)) < golden.get("value_tolerance", 0.01)
        return {
            "pass": match,
            "reason": "Restated value matches is_latest record" if match
                      else f"Mismatch: expected={expected_val} actual={actual_val}",
            "actual": {"value": actual_val},
        }

    # ── Semantic: honest refusal (correctly reports no relevant content) ─
    if category == "semantic_honest_refusal":
        response = (result.get("response_text") or "").lower()
        refusal_phrases = ["do not contain", "does not contain", "no information",
                           "not addressed", "not discussed", "not found"]
        refused = any(p in response for p in refusal_phrases)
        return {
            "pass": refused,
            "reason": "Correctly reported absence of relevant content" if refused
                      else "Did NOT refuse — check for possible confabulation from unrelated chunks",
            "actual": {"confidence_tier": result.get("confidence_tier"), "response_preview": response[:200]},
        }

    # ── Semantic (keyword-based) ─────────────────────────────────────────
    if category.startswith("semantic_"):
        tier     = result.get("confidence_tier", "low")
        response = (result.get("response_text") or "").lower()
        keywords = [k.lower() for k in golden.get("expected_keywords", [])]

        if tier == "low":
            return {"pass": False, "reason": "Low confidence — retrieval likely missed the target chunks",
                     "actual": {"confidence_tier": tier, "response_preview": response[:200]}}

        missing = [k for k in keywords if k not in response]
        if missing:
            return {"pass": False, "reason": f"Missing keywords in response: {missing}",
                     "actual": {"confidence_tier": tier, "missing_keywords": missing, "response_preview": response[:200]}}

        return {"pass": True, "reason": f"All {len(keywords)} keywords present, confidence={tier}",
                 "actual": {"confidence_tier": tier}}

    return {"pass": False, "reason": f"Unknown category: {category}", "actual": None}


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------
def print_report(results: list[dict]):
    total    = len(results)
    passed   = sum(1 for r in results if r["score"]["pass"])
    failed   = total - passed

    print(f"\n{'='*60}")
    print(f"LedgerMind Eval Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")
    print(f"Total:  {total}  |  Pass: {passed}  |  Fail: {failed}  |  Score: {passed/total*100:.1f}%")

    from collections import defaultdict
    by_cat = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r["score"]["pass"])

    print(f"\nBy category:")
    for cat, outcomes in sorted(by_cat.items()):
        n = len(outcomes)
        p = sum(outcomes)
        bar = "█" * p + "░" * (n - p)
        print(f"  {cat:<30} {p}/{n}  {bar}")

    failures = [r for r in results if not r["score"]["pass"]]
    if failures:
        print(f"\nFailures ({len(failures)}):")
        for r in failures:
            print(f"  [{r['id']}] {r['question'][:60]}")
            print(f"         → {r['score']['reason']}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    with open(args.dataset) as f:
        golden_questions = json.load(f)

    if args.category:
        golden_questions = [q for q in golden_questions if q["category"] == args.category]
        print(f"Filtered to category '{args.category}': {len(golden_questions)} questions")

    token = get_token(args.email, args.password)

    all_results = []
    n = len(golden_questions)

    for i, golden in enumerate(golden_questions, 1):
        qid      = golden["id"]
        question = golden["question"]
        category = golden["category"]

        print(f"\n[{i}/{n}] {qid} ({category})")
        print(f"  Q: {question[:70]}")

        result = run_query(token, question)

        score = score_result(golden, result)
        status = "✅ PASS" if score["pass"] else "❌ FAIL"
        print(f"  {status}: {score['reason']}")

        all_results.append({
            "id":       qid,
            "category": category,
            "question": question,
            "score":    score,
            "api_response": {
                "path":            result.get("path") if result else None,
                "is_blocked":      result.get("is_blocked") if result else None,
                "confidence_tier": result.get("confidence_tier") if result else None,
                "sql_verified":    result.get("sql_verified") if result else None,
                "error":           result.get("error") if result else None,
                "response_preview": (result.get("response_text") or "")[:200] if result else None,
            } if result else None,
        })

        if i < n:
            print(f"  ⏳ waiting {args.delay}s (Gemini rate limit)…")
            time.sleep(args.delay)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\nFull results saved to {args.out}")

    print_report(all_results)


if __name__ == "__main__":
    main()