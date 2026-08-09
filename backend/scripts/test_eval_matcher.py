#!/usr/bin/env python3
"""
LedgerMind — eval_runner keyword matcher checks
================================================
Covers `expected_keywords` matching in eval_runner.py: bare strings, the
alternatives-list form ("any of these satisfies this assertion"), and
load-time rejection of malformed entries.

Makes ZERO API calls. No Gemini, no Groq, no Cohere, no network at all.

Usage:
  python3 backend/scripts/test_eval_matcher.py              # unit checks only
  python3 backend/scripts/test_eval_matcher.py --archives   # + re-score archives

Exit code is 0 only if every check passed.

WHY THIS FILE EXISTS, rather than living in a throwaway:
these unit checks are the ONLY coverage of the alternatives path. The archive
re-score cannot exercise it -- no golden dataset carries a list-valued entry
yet, so for archived data the new path is unreachable by construction. The
re-score proves the bare-string path did not move; only these checks prove the
new path works at all. That evidence has to be in git before the first list
entry goes into a golden dataset.

--archives re-scores every eval_results/*.json under the pre-change matcher and
the current one and diffs the verdicts. It is OFF by default: eval_results/ is
gitignored, so on a fresh clone it is empty and its absence is not a failure.
It also can only re-score from the archived 200-char `response_preview` --
full response_text is not stored -- so it cannot reproduce an ORIGINAL verdict
and does not try. It feeds both matchers the identical input and compares them
to each other, which is the property under test.
"""
import argparse
import importlib.util
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))

_ap = argparse.ArgumentParser(description="eval_runner keyword matcher checks")
_ap.add_argument("--archives", action="store_true",
                 help="also re-score every eval_results/*.json under the old and "
                      "current matcher and diff the verdicts")
_args = _ap.parse_args()

# eval_runner parses ITS args at module scope, so importing it consumes argv.
# Ours is already parsed above; hand it a minimal valid argv.
sys.argv = ["eval_runner.py", "--model", "unused"]
_spec = importlib.util.spec_from_file_location(
    "eval_runner_under_test", os.path.join(_HERE, "eval_runner.py"))
er = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(er)


def old_missing(golden, response):
    """
    The pre-alternatives matcher, verbatim, from the two call sites it had.
    Kept here so the archive re-score compares against real old code rather
    than against a description of it.
    """
    keywords = [k.lower() for k in golden.get("expected_keywords", [])]
    return [k for k in keywords if k not in response]


new_missing = er._missing_keywords

_failures = []


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        _failures.append(name)


# ---------------------------------------------------------------------------
# Unit checks
# ---------------------------------------------------------------------------
def run_unit_checks():
    print("unit checks:")

    # The real Q039 shape: one correct answer, two legitimate renderings.
    ALTS = ["SEBI (Listing Obligations", "Securities and Exchange Board of India"]

    check("list passes when the FIRST alternative matches",
          new_missing({"expected_keywords": [ALTS]},
                      "the sebi (listing obligations and disclosure requirements) "
                      "regulations, 2015") == [])

    check("list passes when the SECOND alternative matches",
          new_missing({"expected_keywords": [ALTS]},
                      "securities and exchange board of india (lodr) regulations") == [])

    check("list FAILS when no alternative matches",
          new_missing({"expected_keywords": [ALTS]},
                      "quarterly revenue rose") == [ALTS])

    check("list reports the WHOLE alternative set on failure",
          new_missing({"expected_keywords": [ALTS]}, "nothing")[0] == ALTS)

    check("bare string unchanged: present",
          new_missing({"expected_keywords": ["FY26"]}, "fy26 revenue") == []
          and old_missing({"expected_keywords": ["FY26"]}, "fy26 revenue") == [])

    check("bare string unchanged: absent",
          new_missing({"expected_keywords": ["FY26"]}, "fy25 revenue")
          == old_missing({"expected_keywords": ["FY26"]}, "fy25 revenue")
          == ["fy26"])

    # A bare string still reports LOWER-CASED, byte-identical to the old
    # matcher, so an old sweep and a new one stay diffable.
    check("mixed bare + list, only the bare one missing",
          new_missing({"expected_keywords": ["FY26", ["SEBI", "Securities and Exchange Board"]]},
                      "sebi noted fy25") == ["fy26"])

    check("mixed bare + list, only the list missing",
          new_missing({"expected_keywords": ["FY26", ALTS]},
                      "fy26 was strong") == [ALTS])

    check("case-insensitivity applies to list members too",
          new_missing({"expected_keywords": [["SeBi (LODR)"]]},
                      "sebi (lodr) regulations") == [])

    check("no expected_keywords key -> nothing missing",
          new_missing({}, "anything") == [])

    check("empty expected_keywords -> nothing missing",
          new_missing({"expected_keywords": []}, "anything") == [])

    check("null expected_keywords -> nothing missing",
          new_missing({"expected_keywords": None}, "anything") == [])

    # ── load-time validation ────────────────────────────────────────────────
    # Every malformed shape fails OPEN in the matcher: an empty alternatives
    # list is unsatisfiable by construction, a nested list raises mid-sweep
    # after the quota is already spent. Both must stop the run at load.
    for bad, label in [
        ([{"id": "X", "expected_keywords": [[]]}],          "empty alternatives list"),
        ([{"id": "X", "expected_keywords": [[["a"]]]}],     "nested list member"),
        ([{"id": "X", "expected_keywords": [[1]]}],         "non-string member"),
        ([{"id": "X", "expected_keywords": [""]}],          "empty bare string"),
        ([{"id": "X", "expected_keywords": [["a", "  "]]}], "blank member"),
        ([{"id": "X", "expected_keywords": [None]}],        "None entry"),
        ([{"id": "X", "expected_keywords": "SEBI"}],        "string instead of list"),
        ([{"id": "X", "expected_keywords": [{"a": 1}]}],    "dict entry"),
    ]:
        try:
            er.validate_expected_keywords(bad)
            check(f"load-time error on {label}", False)
        except ValueError as e:
            print(f"  PASS  load-time error on {label}: "
                  f"{str(e).splitlines()[-1].strip()}")

    for good, label in [
        ([{"id": "X", "expected_keywords": ["FY26", ["SEBI", "Securities"]]}], "valid mixed"),
        ([{"id": "X"}],                                                        "no expected_keywords key"),
        ([{"id": "X", "expected_keywords": []}],                               "empty keyword list"),
        ([{"id": "X", "expected_keywords": None}],                             "null expected_keywords"),
    ]:
        try:
            er.validate_expected_keywords(good)
            check(f"load-time accepts {label}", True)
        except ValueError as e:
            check(f"load-time accepts {label} (raised {e})", False)

    # The live datasets must keep validating, or the next sweep will not start.
    gd = os.path.join(_REPO_ROOT, "golden_dataset")
    names = sorted(f for f in os.listdir(gd) if f.endswith(".json"))
    check("golden_dataset holds the four documented q*.json inputs", len(names) == 4)
    for fn in names:
        with open(os.path.join(gd, fn)) as f:
            questions = json.load(f)
        try:
            er.validate_expected_keywords(questions)
            check(f"live dataset validates: {fn}", True)
        except ValueError as e:
            check(f"live dataset validates: {fn} ({e})", False)


# ---------------------------------------------------------------------------
# Archive re-score (opt-in)
# ---------------------------------------------------------------------------
def run_archive_rescore():
    print()
    print("archive re-score (old matcher vs current):")

    gd = os.path.join(_REPO_ROOT, "golden_dataset")
    golden_by_key = {}
    for fn in sorted(os.listdir(gd)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(gd, fn)) as f:
            for q in json.load(f):
                golden_by_key[(q["id"], q["question"])] = q

    results_dir = os.path.join(_REPO_ROOT, "eval_results")
    if not os.path.isdir(results_dir):
        # gitignored; empty on a fresh clone. Not a failure.
        print(f"  eval_results/ not present at {results_dir} — nothing to re-score")
        return

    files = sorted(f for f in os.listdir(results_dir) if f.endswith(".json"))
    total_results = compared = unmatched = 0
    verdict_diffs, render_diffs = [], []

    for fn in files:
        with open(os.path.join(results_dir, fn)) as f:
            doc = json.load(f)
        results = doc["results"] if isinstance(doc, dict) and "results" in doc else doc
        if not isinstance(results, list):
            print(f"  {fn}: SKIP, unrecognised shape {type(doc).__name__}")
            continue
        file_diffs = 0
        for r in results:
            total_results += 1
            golden = golden_by_key.get((r.get("id"), r.get("question")))
            if golden is None:
                unmatched += 1
                continue
            if not golden.get("expected_keywords"):
                continue
            api = r.get("api_response") or {}
            actual = (r.get("score") or {}).get("actual") or {}
            response = (actual.get("response_preview")
                        or api.get("response_preview") or "").lower()
            o = old_missing(golden, response)
            n = new_missing(golden, response)
            compared += 1
            if bool(o) != bool(n):
                verdict_diffs.append((fn, r.get("id"), o, n))
                file_diffs += 1
            elif [str(x) for x in o] != [str(x) for x in n]:
                render_diffs.append((fn, r.get("id"), o, n))
                file_diffs += 1
        print(f"  {fn}: {len(results)} results, {file_diffs} diffs")

    print()
    print(f"  archives: {len(files)}  results: {total_results}  "
          f"keyword-bearing compared: {compared}  golden-unmatched: {unmatched}")
    for d in verdict_diffs:
        print("  VERDICT DIFF", d)
    for d in render_diffs:
        print("  RENDERING DIFF", d)
    check(f"0 pass/fail verdict differences across {compared} re-scored questions",
          not verdict_diffs)
    check(f"0 failure-reason rendering differences across {compared} re-scored questions",
          not render_diffs)


if __name__ == "__main__":
    run_unit_checks()
    if _args.archives:
        run_archive_rescore()
    print()
    total = len(_failures)
    print(f"FAILURES: {_failures if _failures else 'none'}")
    sys.exit(1 if total else 0)
