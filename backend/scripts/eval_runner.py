"""
LedgerMind — Eval Runner
=========================
Runs all golden questions against the live API and produces a
structured evaluation report with per-category metrics.

Usage:
  cd ~/ledgermind
  python3 scripts/eval_runner.py --model <model> [--dataset golden_dataset/q_titan.json]
  Results default to eval_results/eval_<dataset-stem>.json — one file per dataset.

Scoring logic by category:
  quantitative_point                → PASS if sql_verified AND returned value == expected_value
  quantitative_yoy                  → PASS if sql_verified AND yoy_pct within ±0.5% of expected
  quantitative_standalone           → same as point (tests financial_type isolation)
  quantitative_comparison           → PASS if sql_verified AND higher-entity + values match
                                       (also guards against bug #6 negative-pct/"higher" regression)
  quantitative_growth_comparison    → PASS if sql_verified AND faster-entity + YoY growth %s match
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

Rate limiting: Gemini free tier is 5 RPM (one call per 12s). A semantic
question makes TWO Gemini calls, so the safe per-question delay is 25s.
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
import re as _re
import os
import sys
import time
from datetime import datetime
from typing import Optional

import requests

# Repo root, derived from THIS FILE rather than from the cwd:
#   backend/scripts/eval_runner.py -> backend/scripts -> backend -> repo root
# The old --out default was the relative path "golden_dataset/eval_results.json",
# which resolves differently depending on where the runner is invoked from, and
# §7 documents invocation from backend/. From there it resolved to
# backend/golden_dataset/, which os.makedirs then CREATED. That phantom
# directory exists in this repo and holds four tracked eval outputs dated
# 2026-07-18 and 2026-07-25. An absolute default cannot drift with the cwd.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# No single default filename: see the per-dataset derivation after
# parse_args(). _REPO_ROOT is still the anchor -- an absolute base cannot
# drift with the cwd, which is what created the phantom
# backend/golden_dataset/ described above.

# ---------------------------------------------------------------------------
# CLI args
# ---------------------------------------------------------------------------
parser = argparse.ArgumentParser(description="LedgerMind eval runner")
parser.add_argument("--api-base", default="http://localhost:8000")
parser.add_argument("--email",    default="admin@alpha.ledgermind.test",
                    help="Login email. MUST be admin: llm_provider is admin-tier only "
                         "in response_shaping.py, and without it the provider guard "
                         "silently reads None for every question and concludes the "
                         "whole sweep was Gemini-served.")
parser.add_argument("--password", default="demo1234")
parser.add_argument("--dataset",  default="golden_dataset/q4fy26_eternal.json")
parser.add_argument("--out",      default=None,
                    help="Where to write the full results JSON. When omitted, "
                         "derived from the dataset filename into eval_results/ "
                         "(gitignored). May NEVER resolve inside golden_dataset/ "
                         "— see the guard below.")
# 5 RPM = one call per 12s, and a semantic question makes TWO Gemini calls
# (router classification, then response synthesis): 2 x 12s = 24s, rounded
# to 25s. The previous default of 15.0 assumed one call per question, giving
# ~8 RPM against a 5 RPM limit -- over budget by construction, and why the
# Groq fallback fired mid-sweep and withheld the score (2026-07-30, Paytm).
# Do not lower this to speed up a run; an invalid baseline costs a full re-run.
parser.add_argument("--delay",    type=float, default=25.0,
                    help="Seconds between requests (Gemini 5 RPM = 12s minimum; default 15)")
parser.add_argument("--category", default=None,
                    help="Run only this category (e.g. adversarial)")
# Scoped sweeps exist because a change's blast radius is usually a subset of
# the golden set, and a full re-run costs ~170 Gemini calls against a 500/day
# bucket. Kept SEPARATE from --category rather than replacing it: the singular
# form is in shell history and the README, and silently repurposing an
# argument name is how a run gets scored against the wrong subset.
parser.add_argument("--categories", default=None,
                    help="Comma-separated categories to run (e.g. "
                         "semantic_risk,semantic_audit). A run using this flag "
                         "is marked scoped=true in the output and is NOT a "
                         "baseline for the full dataset.")
parser.add_argument("--model", required=True,
                    help="REQUIRED. The GEMINI_MODEL the target API is running "
                         "(e.g. gemini-3.1-flash-lite). Asserted against the llm_model the API "
                         "actually reports, not merely printed: "
                         "a score is meaningless without a stated model, and TQ010 is "
                         "a live example of a question that passes on one and fails on "
                         "another. Verify with: docker compose exec backend printenv "
                         "GEMINI_MODEL")
args = parser.parse_args()

# PER-DATASET DEFAULT. A single constant default meant every dataset in a
# multi-dataset sweep overwrote the previous one, so a three-dataset run ended
# holding only the LAST dataset's detail. Measured 2026-08-08: a full sweep
# (Eternal 55Q, Paytm 20Q, Titan 15Q) finished with Eternal's and Paytm's JSON
# destroyed, and only the tee'd human-readable reports survived. That sweep
# cost roughly an hour of wall time and ~80 Gemini calls against a 500/day
# bucket, so silently discarding two thirds of its structured output is
# expensive, not cosmetic.
#
# Derived BEFORE the guard below, deliberately: the guard must run on whatever
# path is actually written, and a derivation placed after it would skip it
# entirely for the default case -- the exact shape of a check that inspects
# nothing.
if args.out is None:
    _stem = os.path.splitext(os.path.basename(args.dataset))[0]
    args.out = os.path.join(_REPO_ROOT, "eval_results", f"eval_{_stem}.json")

# golden_dataset/ IS INPUTS ONLY: the three q*.json files and nothing else.
# Changing the default is not sufficient on its own -- an explicit
# `--out golden_dataset/anything.json` would still land there, and
# os.makedirs() below happily creates whatever directory the path names.
#
# WHY THIS IS WORTH A HARD ABORT. Eval outputs accumulating beside the inputs
# is not cosmetic: that directory once held 79 output files against 3 inputs,
# a glob over it picked up the stale ones, and the result was a crashed anchor
# scan and a phantom category discrepancy. The same failure is already latent
# in backend/golden_dataset/, created by the old relative default.
#
# Checked on the RESOLVED absolute path, so "../golden_dataset/x.json" and
# symlinked or nested variants are caught too, and enforced at PARSE time so it
# fails in the first second rather than after a sweep has spent its quota.
if "golden_dataset" in os.path.abspath(args.out).split(os.sep):
    sys.exit(
        f"ABORT: --out resolves inside golden_dataset/ ({os.path.abspath(args.out)}).\n"
        "That directory holds the three q*.json INPUTS and nothing else; eval\n"
        "OUTPUTS belong in eval_results/, which is gitignored. Re-run with e.g.\n"
        "  --out eval_results/<name>.json"
    )

API_BASE = args.api_base
TIMEOUT  = 120


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def get_token(email: str, password: str) -> str:
    resp = requests.post(
        f"{API_BASE}/auth/login",
        json={"email": email, "password": password},
        timeout=120,
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


def _extract_growth_comparison_values(sql_result) -> Optional[dict]:
    if not sql_result:
        return None
    row = sql_result[0] if isinstance(sql_result, list) else sql_result
    return row if isinstance(row, dict) else None


# ---------------------------------------------------------------------------
# expected_keywords matching
# ---------------------------------------------------------------------------
# An entry in expected_keywords is EITHER a bare string (required, unchanged
# behaviour) OR a list of strings meaning "any one of these satisfies this
# assertion".
#
# The alternatives form exists because a correct answer may legitimately name
# the same thing two ways across runs -- Q039's answer alternates between
# "SEBI (Listing Obligations..." and "Securities and Exchange Board of India
# (...)". Both are right, and no single required substring covers both. That is
# a property of every acronym-bearing question, not of one question.
#
# This does NOT relax §5's golden keyword rule. Each alternative must still be
# an asserted-on string in its own right; the list expresses "the filing's own
# phrase, however the model chose to render it", not "any of several weaker
# strings will do".


def _keyword_alternatives(spec) -> list[str]:
    """
    Normalise one expected_keywords entry to its list of acceptable renderings,
    lower-cased -- the SAME normalisation the bare-string path has always
    applied. A bare string is a one-element alternative set, so the bare-string
    code path and the list code path are the same path.
    """
    if isinstance(spec, str):
        return [spec.lower()]
    return [alt.lower() for alt in spec]


def _missing_keywords(golden: dict, response: str) -> list:
    """
    Entries from golden["expected_keywords"] that the (already lower-cased)
    response does not satisfy.

    A bare string is reported LOWER-CASED, byte-identical to what the
    pre-alternatives code put in the failure reason -- re-scoring the 22
    archived eval_results files must produce not just the same verdicts but the
    same reason strings, or the diff between an old sweep and a new one stops
    being readable. An alternatives entry is reported as its list, so the
    author can see which whole set went unmatched rather than one flattened
    string.
    """
    missing = []
    for spec in golden.get("expected_keywords", []) or []:
        alts = _keyword_alternatives(spec)
        if not any(alt in response for alt in alts):
            missing.append(alts[0] if isinstance(spec, str) else spec)
    return missing


def validate_expected_keywords(questions: list[dict]) -> None:
    """
    Fail at LOAD time on a malformed expected_keywords entry.

    A malformed entry must never reach the matcher, because every malformed
    shape there fails OPEN: an empty list makes any() False -> permanently
    missing; a nested list makes the `in` test raise mid-sweep, after the
    Gemini calls have already been spent. Both are worse than refusing to
    start.
    """
    errors: list[str] = []
    for q in questions:
        qid = q.get("id", "<no id>")
        spec_list = q.get("expected_keywords")
        if spec_list is None:
            continue
        if not isinstance(spec_list, list):
            errors.append(f"{qid}: expected_keywords must be a list, got {type(spec_list).__name__}")
            continue
        for i, spec in enumerate(spec_list):
            where = f"{qid}: expected_keywords[{i}]"
            if isinstance(spec, str):
                if not spec.strip():
                    errors.append(f"{where} is an empty string")
                continue
            if isinstance(spec, list):
                if not spec:
                    errors.append(f"{where} is an empty alternatives list "
                                  f"(would be unsatisfiable by construction)")
                    continue
                for j, alt in enumerate(spec):
                    if not isinstance(alt, str):
                        errors.append(f"{where}[{j}] is {type(alt).__name__}, "
                                      f"expected a string (alternatives may not nest)")
                    elif not alt.strip():
                        errors.append(f"{where}[{j}] is an empty string")
                continue
            errors.append(f"{where} is {type(spec).__name__}, expected a string "
                          f"or a list of strings")
    if errors:
        raise ValueError("malformed expected_keywords:\n  " + "\n  ".join(errors))


def score_result(golden: dict, result: Optional[dict]) -> dict:
    if result is None:
        return {"pass": False, "reason": "API call failed / no response", "actual": None}

    # ── Synthesis outage: EXCLUDED, neither pass nor fail ────────────────
    # response_generator sets error="synthesis_unavailable" when BOTH
    # providers fail, serves a raw-excerpt floor, and caps the tier to low by
    # design (asserted in scripts/test_synthesis_floor.py section 1). That cap
    # is correct and is not touched here -- but it feeds pass/fail conditions,
    # not just messages, and every one of them reads the cap as evidence:
    #
    #   out_of_corpus passes on (not sql_verified) and tier == "low"
    #   cross_examination with expected_tier_low asserts tier == "low"
    #
    # An outage satisfies both, so the questions that exist to prove a refusal
    # is honest were recorded as passing while no synthesis had happened at
    # all. That is the serious half of the defect: a vacuous PASS, not a
    # misleading FAIL.
    #
    # Excluded rather than failed. An LLM outage is an infrastructure failure,
    # not an accuracy defect; scoring it FAIL understates the system exactly
    # as passing it overstates it. Excluded questions leave BOTH numerator and
    # denominator -- see print_report.
    #
    # DELIBERATELY BEFORE every category branch, and there is no second check
    # inside out_of_corpus or cross_examination: a guard that can never fire
    # is worse than none, because the next reader has to prove it is dead.
    #
    # pass=False is carried alongside excluded=True on purpose. Every consumer
    # filters on `excluded` first; a consumer that forgets then counts this as
    # a visible failure rather than a silent pass.
    if result.get("error") == "synthesis_unavailable":
        return {
            "pass": False,
            "excluded": True,
            "reason": "EXCLUDED — synthesis_unavailable: both LLM providers failed and "
                      "the raw-excerpt floor was served. Confidence tier is capped to low "
                      "by design, so no accuracy claim is available for this question.",
            "actual": {
                "error": result.get("error"),
                "confidence_tier": result.get("confidence_tier"),
                "citation_count": len(result.get("citations") or []),
            },
        }

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

    # ── Growth comparison (YoY rate comparison between two entities) ──────
    if category == "quantitative_growth_comparison":
        sql_verified = result.get("sql_verified", False)
        gc = _extract_growth_comparison_values(result.get("sql_result"))
        expected_faster = golden.get("expected_faster_entity")
        expected_a_pct = golden.get("expected_yoy_a_pct")
        expected_b_pct = golden.get("expected_yoy_b_pct")
        tolerance = golden.get("value_tolerance", 0.5)

        if not sql_verified or gc is None:
            return {"pass": False, "reason": f"sql_verified={sql_verified}, no growth_comparison payload. error={result.get('error')}",
                     "actual": {"sql_verified": sql_verified}}

        errors = []
        actual_faster = gc.get("faster_growing_entity")
        if expected_faster and actual_faster != expected_faster:
            errors.append(f"wrong faster entity: expected={expected_faster} actual={actual_faster}")

        actual_a_pct = gc.get("yoy_a_pct")
        actual_b_pct = gc.get("yoy_b_pct")
        if expected_a_pct is not None:
            if actual_a_pct is None or abs(float(actual_a_pct) - float(expected_a_pct)) > tolerance:
                errors.append(f"entity_a yoy_pct mismatch: expected={expected_a_pct} actual={actual_a_pct}")
        if expected_b_pct is not None:
            if actual_b_pct is None or abs(float(actual_b_pct) - float(expected_b_pct)) > tolerance:
                errors.append(f"entity_b yoy_pct mismatch: expected={expected_b_pct} actual={actual_b_pct}")

        passed = len(errors) == 0
        return {"pass": passed, "reason": "Growth comparison correct" if passed else "; ".join(errors), "actual": gc}

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
        # Anchored on the SUBJECT (excerpts/documents/...), not the verb.
        # Observed live 2026-07-29 across repeated Q038 runs: the model
        # freely varies the verb — "do not contain", "do not provide",
        # "do not detail" — while the subject phrasing stays stable. An
        # enumerated verb list is a treadmill; each new phrasing silently
        # scores an honest refusal as a failure.
        refusal_subject_re = _re.compile(
            r"\b(?:excerpts?|documents?|results?|reports?|filings?|"
            r"statements?|sources?|materials?)\s+(?:do|does)\s+not\b",
            _re.IGNORECASE,
        )
        refusal_phrases = ["no information", "not addressed", "not discussed",
                           "not found", "does not explicitly state",
                           "do not explicitly state"]
        refused = bool(refusal_subject_re.search(response)) or \
                  any(p in response for p in refusal_phrases)
        return {
            "pass": refused,
            "reason": "Correctly reported absence of relevant content" if refused
                      else "Did NOT refuse — check for possible confabulation from unrelated chunks",
            "actual": {"confidence_tier": result.get("confidence_tier"), "response_preview": response[:200]},
        }

    # ── Cross-examination ────────────────────────────────────────────────
    # The FIRST category to exercise path="cross". The generic path check
    # above already asserts expected_path, so this branch owns what is
    # specific to cross-examination.
    #
    # expected_contradictions is the point of the category. LedgerMind's
    # stated value is surfacing real disagreement between narrative and
    # verified figures; a system that FABRICATES disagreement inverts that,
    # which is why a false positive here is worse than a missed detection.
    # Nothing else in the golden set asserts that a non-contradiction stays
    # unflagged — detect_magnitude_contradictions once produced eleven
    # "severity: high" flags on a query where the top-cited chunk was the
    # same cash-flow statement the SQL value came from.
    #
    # Every expectation is read from the golden entry rather than hardcoded,
    # so a change in what the quantitative path can answer for a metric is a
    # data edit here, not a code change.
    if category == "cross_examination":
        response = (result.get("response_text") or "").lower()
        tier = result.get("confidence_tier", "low")
        contradictions = result.get("contradictions") or []
        sql_verified = result.get("sql_verified", False)

        expected_contradictions = golden.get("expected_contradictions")
        if expected_contradictions is not None and len(contradictions) != expected_contradictions:
            return {
                "pass": False,
                "reason": f"Contradiction count mismatch: expected={expected_contradictions} "
                          f"actual={len(contradictions)}",
                "actual": {"contradictions": contradictions, "confidence_tier": tier},
            }

        expected_sql_verified = golden.get("expected_sql_verified")
        if expected_sql_verified is not None and sql_verified != expected_sql_verified:
            return {
                "pass": False,
                "reason": f"sql_verified mismatch: expected={expected_sql_verified} actual={sql_verified}",
                "actual": {"sql_verified": sql_verified},
            }

        # tier is asserted in ONE direction by default and the OTHER when the
        # golden entry sets expected_tier_low. Low normally means the
        # qualitative half missed its chunks -- a real failure, and the right
        # default for every cross question written so far.
        #
        # But _reconcile_cross's Quadrant 4 (both halves empty, a genuine
        # no-answer) returns tier="low" BY CONSTRUCTION, so an unconditional
        # fail made that quadrant untestable: TQ015 was authored as a
        # Quadrant 4 cross question on 2026-08-02 and had to be moved to
        # semantic_honest_refusal purely because this branch could not express
        # the expectation.
        #
        # Deliberately an INVERSION, not a skip. Skipping the check would let
        # such a question pass at any tier, asserting nothing -- the flag has
        # to buy a real assertion, not remove one.
        expect_low = bool(golden.get("expected_tier_low"))
        if expect_low and tier != "low":
            return {
                "pass": False,
                "reason": f"Expected low confidence (genuine no-answer) but got tier={tier} "
                          f"— check whether either half actually found evidence",
                "actual": {"confidence_tier": tier, "response_preview": response[:200]},
            }
        if not expect_low and tier == "low":
            return {
                "pass": False,
                "reason": "Low confidence (tier=low, not a synthesis outage) — check whether the qualitative half found its chunks",
                "actual": {"confidence_tier": tier, "response_preview": response[:200]},
            }

        keywords = golden.get("expected_keywords", []) or []
        missing = _missing_keywords(golden, response)
        if missing:
            return {
                "pass": False,
                "reason": f"Missing keywords in response: {missing}",
                "actual": {"confidence_tier": tier, "missing_keywords": missing,
                           "response_preview": response[:200]},
            }

        return {
            "pass": True,
            "reason": f"Cross-examination correct | {len(contradictions)} contradictions, "
                      f"{len(keywords)} keywords present, confidence={tier}",
            "actual": {"confidence_tier": tier, "contradictions": len(contradictions),
                       "sql_verified": sql_verified},
        }

    # ── Semantic (keyword-based) ─────────────────────────────────────────
    if category.startswith("semantic_"):
        tier     = result.get("confidence_tier", "low")
        response = (result.get("response_text") or "").lower()
        keywords = golden.get("expected_keywords", []) or []

        if tier == "low":
            return {"pass": False, "reason": "Low confidence (tier=low, not a synthesis outage) — check whether retrieval found the target chunks",
                     "actual": {"confidence_tier": tier, "response_preview": response[:200]}}

        missing = _missing_keywords(golden, response)
        if missing:
            return {"pass": False, "reason": f"Missing keywords in response: {missing}",
                     "actual": {"confidence_tier": tier, "missing_keywords": missing, "response_preview": response[:200]}}

        return {"pass": True, "reason": f"All {len(keywords)} keywords present, confidence={tier}",
                 "actual": {"confidence_tier": tier}}

    return {"pass": False, "reason": f"Unknown category: {category}", "actual": None}


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------
def _integrity_counters(results: list[dict]):
    """
    The three integrity tallies: (scored, providers, models, backends).

    Computed ONCE and shared by print_report and the meta block main() writes,
    so the recorded metadata and the printed gate cannot disagree. These three
    dicts decide whether a score is publishable at all; two copies of that
    arithmetic drifting apart would mean a withheld run whose own JSON claims
    it was clean.

    Blocked queries are excluded: prompt_shield blocks before router_node,
    which returns immediately on is_blocked, so NO LLM call is ever made and
    llm_provider is legitimately None. Counting those as "unknown" withheld
    three otherwise-clean scores on 2026-07-29 -- the unknown count matched
    the adversarial count exactly in all three datasets.

    None is excluded from `backends` rather than counted: refusal paths score
    no citations, so reranker_backend is legitimately absent there, exactly as
    llm_provider is legitimately absent on blocked queries.
    """
    from collections import Counter
    scored = [r for r in results
              if not (r.get("api_response") or {}).get("is_blocked")]
    providers = Counter(
        (r.get("api_response") or {}).get("llm_provider") or "unknown"
        for r in scored
    )
    models = Counter(
        (r.get("api_response") or {}).get("llm_model") or "unknown"
        for r in scored
    )
    backends = Counter(
        b for b in ((r.get("api_response") or {}).get("reranker_backend")
                    for r in scored)
        if b is not None
    )
    return scored, providers, models, backends


def print_report(results: list[dict], model: str):
    total    = len(results)
    # Excluded questions leave BOTH numerator and denominator. See the
    # synthesis_unavailable early return in score_result for why an outage is
    # excluded rather than failed. `.get` rather than `[...]`: the default is
    # applied once at the call site, and a caller that skipped it must read as
    # not-excluded rather than raise here.
    excluded_rows = [r for r in results if r["score"].get("excluded")]
    n_excluded    = len(excluded_rows)
    scored_rows   = [r for r in results if not r["score"].get("excluded")]
    scored_total  = len(scored_rows)
    passed   = sum(1 for r in scored_rows if r["score"]["pass"])
    failed   = scored_total - passed
    # ONE tally string, used by the withholding branches and the clean branch
    # alike. A withheld run and a clean run must never disagree about the
    # denominator, and a run that is BOTH contaminated and short some excluded
    # questions has to show both facts -- the gates below are unchanged and
    # still withhold, they simply quote this instead of a bare fraction.
    tally = f"{passed}/{scored_total}" + (
        f" ({n_excluded} excluded: synthesis_unavailable)" if n_excluded else ""
    )

    # ── Provider integrity gate ──────────────────────────────────────────
    # app/llm/client.py falls back to Groq on 429/timeout/5xx. That is
    # correct behaviour for a USER -- the answer still arrives -- but it is
    # fatal for an EVAL: a mixed-provider sweep produces a number that
    # describes neither model. Confirmed 2026-07-29 that a rate-limited run
    # returned llm_provider="groq" on every query while looking entirely
    # normal. So the headline score is withheld, not annotated: a score
    # printed with a warning above it still gets copied into a README.
    # Tallies come from _integrity_counters so this report and the meta block
    # in main() are computed once, in one place. See that docstring for why
    # blocked queries and None backends are excluded.
    scored, providers, models, backends = _integrity_counters(results)
    n_blocked = len(results) - len(scored)
    contaminated = {p for p in providers if p not in ("gemini",)}

    # Model integrity gate — SEPARATE from the provider gate above, not merged
    # into it. A mixed-provider run and a wrong-model run are different faults
    # with different remedies (wait for quota vs. fix the environment and
    # re-run), and one combined message means reading the wrong instruction at
    # the worst possible moment.
    model_mismatch = {m for m in models if m != model}

    print(f"\n{'='*60}")
    print(f"LedgerMind Eval Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Model (stated): {model}")
    print(f"Providers: {dict(providers)}"
          + (f"  (+{n_blocked} blocked, no LLM call)" if n_blocked else ""))
    print(f"{'='*60}")

    print(f"Models served: {dict(models)}")

    # Reranker integrity gate — a THIRD gate, same shape as providers and
    # models above and separate for the same reason: a mixed-backend run and a
    # mixed-provider run are different faults with different remedies.
    backend_mixed = len(backends) > 1
    print(f"Reranker backends: {dict(backends) or '(none recorded)'}")

    if model_mismatch:
        print(f"\n  *** SCORE WITHHELD — MODEL MISMATCH ***")
        print(f"  --model says '{model}', but calls were served by: "
              f"{sorted(m for m in models if m != model)}")
        print(f"  Verify with: docker compose exec -T backend printenv GEMINI_MODEL")
        print(f"  'unknown' means llm_model was absent — either the run was not "
              f"authenticated as admin (--email admin@alpha.ledgermind.test), or "
              f"synthesis fell through to the raw-excerpt floor on ALL providers, "
              f"which clears attribution deliberately.")
        print(f"  Raw tally (DO NOT publish): {tally}")

    if contaminated:
        print(f"\n  *** SCORE WITHHELD — NOT A VALID BASELINE ***")
        print(f"  {sum(v for k, v in providers.items() if k != 'gemini')}/{len(scored)} "
              f"LLM-served answers were not served by Gemini.")
        if "unknown" in contaminated:
            print(f"  'unknown' means llm_provider was absent — run as admin "
                  f"(--email admin@alpha.ledgermind.test); the field is admin-tier only.")
        if "groq" in contaminated:
            print(f"  'groq' means the fallback fired (rate limit / timeout). "
                  f"Wait for quota and re-run.")
        print(f"  Raw tally (DO NOT publish): {tally}")
    if backend_mixed:
        print(f"\n  *** SCORE WITHHELD — MIXED RERANKER BACKENDS ***")
        print(f"  {dict(backends)}. Cohere scores are probabilities in [0,1];")
        print(f"  local ONNX scores are unbounded logits. A run spanning both")
        print(f"  is two systems, and the questions that flipped are the ones")
        print(f"  whose top-5 ordering differs between the scales.")
        print(f"  Check the Cohere key, then re-run. Raw tally (DO NOT publish): {tally}")

    elif not model_mismatch and not contaminated:
        # Format is byte-identical to the previous one when nothing was
        # excluded (scored_total == total, empty suffix), so an unaffected run
        # produces an unaffected line and old sweeps stay comparable.
        if scored_total:
            print(f"Total:  {scored_total}  |  Pass: {passed}  |  Fail: {failed}  |  "
                  f"Score: {passed/scored_total*100:.1f}%"
                  + (f"  |  Excluded: {n_excluded} (synthesis_unavailable)" if n_excluded else ""))
        else:
            print(f"Total:  0 scored  |  every one of {n_excluded} questions was excluded "
                  f"(synthesis_unavailable) — this run measured nothing")

    from collections import Counter, defaultdict
    by_cat = defaultdict(list)
    for r in scored_rows:
        by_cat[r["category"]].append(r["score"]["pass"])
    exc_cat = Counter(r["category"] for r in excluded_rows)

    print(f"\nBy category:")
    # Union of both key sets: a category every one of whose questions was
    # excluded still has to appear, or it silently vanishes from the report.
    for cat in sorted(set(by_cat) | set(exc_cat)):
        outcomes = by_cat.get(cat, [])
        n = len(outcomes)
        p = sum(outcomes)
        bar = "█" * p + "░" * (n - p)
        suffix = f"  (+{exc_cat[cat]} excluded)" if exc_cat.get(cat) else ""
        print(f"  {cat:<30} {p}/{n}  {bar}{suffix}")

    # The IDs, listed, not just a count. A count cannot distinguish exclusions
    # clustered in one category -- which is a defect signature and a reason to
    # look at the code -- from exclusions scattered across the run, which is a
    # transient signature and a reason to wait for quota and re-run.
    if excluded_rows:
        print(f"\nExcluded ({n_excluded} of {total}, synthesis_unavailable — LLM outage, "
              f"NOT an accuracy result):")
        for r in excluded_rows:
            print(f"  [{r['id']:<10}] {r['category']:<30} {r['question'][:50]}")

    failures = [r for r in scored_rows if not r["score"]["pass"]]
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

    # Before the token, before the first Gemini call: a malformed
    # expected_keywords entry fails open in the matcher, so it has to stop the
    # sweep at load rather than silently pass at score time.
    try:
        validate_expected_keywords(golden_questions)
    except ValueError as exc:
        print(f"ERROR: {args.dataset}: {exc}")
        sys.exit(1)

    dataset_total = len(golden_questions)
    scoped = False

    if args.category:
        golden_questions = [q for q in golden_questions if q["category"] == args.category]
        print(f"Filtered to category '{args.category}': {len(golden_questions)} questions")
        scoped = True

    if args.categories:
        wanted = {c.strip() for c in args.categories.split(",") if c.strip()}
        unknown = wanted - {q["category"] for q in golden_questions}
        if unknown:
            print(f"ERROR: unknown categories: {sorted(unknown)}")
            sys.exit(1)
        golden_questions = [q for q in golden_questions if q["category"] in wanted]
        print(f"Filtered to categories {sorted(wanted)}: "
              f"{len(golden_questions)}/{dataset_total} questions")
        scoped = True

    if not golden_questions:
        print("ERROR: no questions selected.")
        sys.exit(1)

    token = get_token(args.email, args.password)
    print(f"Stated model: {args.model}")
    print(f"API base:     {API_BASE}")

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
        # Defaulted HERE, once, rather than in each of the ~30 verdict dicts
        # score_result can return. A branch that forgot the key would
        # otherwise be indistinguishable from an excluded one.
        score.setdefault("excluded", False)
        status = ("⊘ EXCL" if score["excluded"]
                  else "✅ PASS" if score["pass"] else "❌ FAIL")
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
                # The NUMBER behind the tier, plus what was actually cited.
                # Their absence made "scored low" and "retrieved nothing"
                # indistinguishable in this file: on 2026-07-30 that turned
                # Q026 into a six-command detour through psql, and on
                # 2026-08-02 it forced a live re-query twice to answer a
                # question about citation scores this record should hold.
                # citation_scores was what CITATION_RELEVANCE_FLOOR was
                # calibrated against. That constant was REMOVED 2026-08-08 --
                # it filtered citations but not retrieved_chunks, so a chunk
                # could supply a figure to an answer and be deleted from the
                # evidence list. These scores are still the only per-question
                # record of citation quality and are now the input to any UI
                # display-weight decision, since every response receives
                # exactly TOP_K_RERANK citations and the COUNT carries no
                # information.
                "confidence_score": result.get("confidence_score") if result else None,
                "citation_count":  len(result.get("citations") or []) if result else None,
                "citation_scores": [
                    round(c.get("reranker_score"), 4)
                    for c in (result.get("citations") or [])
                    if c.get("reranker_score") is not None
                ] if result else None,
                # WHICH RERANKER SERVED THIS. Cohere returns probabilities in
                # [0,1]; the local ONNX cross-encoder returns unbounded logits
                # that are frequently negative. The two scales are not
                # comparable, so a sweep that silently changes backend produces
                # citation_scores from two different systems in one file.
                #
                # That happened on 2026-08-09: the Cohere account lost rerank
                # access mid-sweep, rerank() fell through to ONNX exactly as
                # designed and logged an ERROR per query, and the only trace in
                # THIS file was the sign of the scores. Q038 failed because ONNX
                # ranked three duplicate page-166 chunks above page 164; on
                # Cohere the same question cites 164-166 at 0.87-0.98 and passes.
                # Three hours went into inferring the backend from minus signs.
                # None here means nothing was scored (a refusal path), which is
                # NOT the same as "backend unknown".
                "reranker_backend": result.get("reranker_backend") if result else None,
                "sql_verified":    result.get("sql_verified") if result else None,
                "error":           result.get("error") if result else None,
                "response_preview": (result.get("response_text") or "")[:200] if result else None,
                # Admin-tier field. None here means either the run was not
                # authenticated as admin, or no LLM produced the text.
                "llm_provider":    result.get("llm_provider") if result else None,
                # Admin-tier. What ACTUALLY served the call, asserted below
                # against --model. Before this existed --model was only a
                # label: on 2026-07-31 two sweeps ran with 3.5 in the command
                # while the container was on 3.1, and both result files named
                # a model that never served a single call.
                "llm_model":       result.get("llm_model") if result else None,
            } if result else None,
        })

        if i < n:
            print(f"  ⏳ waiting {args.delay}s (Gemini rate limit)…")
            time.sleep(args.delay)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    _, _providers, _models, _backends = _integrity_counters(all_results)
    payload = {
        "meta": {
            "stated_model": args.model,
            "api_base": API_BASE,
            "dataset": args.dataset,
            "dataset_total": dataset_total,
            "questions_run": len(all_results),
            "scoped": scoped,
            "category_filter": args.category,
            "categories_filter": args.categories,
            "run_at": datetime.now().isoformat(timespec="seconds"),
            # The three integrity gates, RECORDED as well as printed. The
            # printed report is transient; this file is what someone reads
            # weeks later. eval_results/*.json are whatever ran last, and
            # CLAUDE.md section 7 requires a provider/reranker header check
            # before trusting one -- that check is only possible if the run wrote
            # these down. Same three tallies the gate above prints.
            "providers": dict(_providers),
            "models_served": dict(_models),
            "reranker_backends": dict(_backends),
            # Recorded, not just printed, for the same reason as the three
            # gates above: this file is what someone reads weeks later, and a
            # score read without its exclusion count is a score over an
            # unknown denominator.
            "excluded_count": sum(1 for r in all_results if r["score"].get("excluded")),
            "excluded_ids": [r["id"] for r in all_results if r["score"].get("excluded")],
        },
        "results": all_results,
    }
    with open(args.out, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"\nFull results saved to {args.out}")

    print_report(all_results, args.model)


if __name__ == "__main__":
    main()