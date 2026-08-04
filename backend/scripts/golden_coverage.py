"""
READ-ONLY: which live metrics does the golden dataset actually assert?

An eval score bounds the correctness of what it ASSERTS and says nothing about
anything else. On 2026-08-04 the three datasets scored 100% while 28 figures in
`financials` were wrong -- including PAYTM `cash` at -710 for a balance-sheet
metric that cannot be negative -- because not one of those 28 was covered by any
question. This reports that gap directly instead of leaving it to be discovered
by a correction run.

Answers, per company and overall: of the distinct metrics currently live in
`financials` (is_latest = TRUE), how many are asserted anywhere in the golden
datasets, and which are not.

NO LLM CALLS. NO PDF PARSING. One SELECT and three JSON reads.

COVERAGE IS COMPANY-SCOPED, deliberately. A question asserting ETERNAL's revenue
protects ETERNAL's revenue row and nothing of PAYTM's, so a metric is counted as
covered for company C only if some question FOR C asserts it. The `--any-company`
flag relaxes this to the weaker global reading; the difference between the two
numbers is itself worth looking at.

THREE MATCH KINDS, reported separately because they are NOT equally strong:

  metric    `expected_metric == <metric>`. The only kind that pins a VALUE.
            A failure here means the number is wrong.
  keyword   the metric name appears in `expected_keywords`. Asserts that a
            string is present in prose, not that any figure is right.
  text      the metric name appears in the question text. The WEAKEST kind, and
            not really an assertion at all -- it means the question is ABOUT the
            metric, which is evidence the area is exercised but no evidence any
            stored value was checked. Reported so that a metric covered only by
            `text` is visibly not the same as one covered by `metric`.

Read the `metric`-kind column as the real coverage number. The others are
context.

RUNNING IT. Needs BOTH the database and golden_dataset/, which do not currently
live in the same place: the host has no psycopg2, and docker-compose mounts only
./backend and ./docs/raw into the backend container, so /app/golden_dataset does
not exist. Until golden_dataset/ is mounted read-only, copy it in for the run:

    docker compose cp golden_dataset backend:/app/golden_dataset
    docker compose exec -T backend python -m scripts.golden_coverage
    docker compose exec -T backend rm -rf /app/golden_dataset

If it becomes a standing check, add to the backend service instead:

    - ./golden_dataset:/app/golden_dataset:ro

Usage:
  docker compose exec -T backend python -m scripts.golden_coverage
  docker compose exec -T backend python -m scripts.golden_coverage --any-company
"""

import argparse
import glob
import json
import os
import re
import sys
from collections import defaultdict

from app.ingestion.db_loader import get_connection

from scripts.regression_check import ALPHA_TENANT

DEFAULT_GOLDEN_DIR = "/app/golden_dataset"

# Dataset filename -> the company its questions are about, used only when a
# question carries no expected_company (adversarial / out_of_corpus entries
# frequently do not).
_DATASET_COMPANY = {
    "q4fy26_eternal.json": "ETERNAL",
    "q_paytm.json":        "PAYTM",
    "q_titan.json":        "TITAN",
}


def _metric_patterns(metric: str):
    """Word-boundary patterns for a snake_case metric name.

    Matches both the stored form (`total_income`) and the prose form
    (`total income`). Anchored with \\b so short names cannot match inside a
    longer word -- `pat` must not match "patent", `cash` must not match
    "cashflow" written solid.
    """
    spaced = metric.replace("_", " ")
    pats = {metric, spaced}
    return [re.compile(rf"\b{re.escape(p)}\b", re.IGNORECASE) for p in pats]


def load_questions(golden_dir: str):
    files = sorted(glob.glob(os.path.join(golden_dir, "q*.json")))
    if not files:
        sys.exit(
            f"ABORT: no q*.json under {golden_dir}.\n"
            "This script needs the golden datasets AND the database, which do not\n"
            "currently share a filesystem. See the module docstring — either copy\n"
            "golden_dataset/ into the container for the run, or mount it read-only."
        )
    out = []
    for f in files:
        base = os.path.basename(f)
        fallback = _DATASET_COMPANY.get(base)
        for q in json.load(open(f)):
            out.append({
                "id": q.get("id"),
                "company": q.get("expected_company") or fallback,
                "expected_metric": q.get("expected_metric"),
                "question": q.get("question") or "",
                "keywords": q.get("expected_keywords") or [],
            })
    return files, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden-dir", default=DEFAULT_GOLDEN_DIR)
    ap.add_argument("--tenant", default=ALPHA_TENANT)
    ap.add_argument("--any-company", action="store_true",
                    help="Count a metric covered if ANY question asserts it, "
                         "regardless of company. Weaker; off by default.")
    args = ap.parse_args()

    files, questions = load_questions(args.golden_dir)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # RLS: financials is tenant-scoped and silently returns 0 rows
            # without this. Zero rows here would read as "no metrics live",
            # which is why the guard below treats an empty result as an abort
            # rather than as a clean 100% coverage report.
            cur.execute("SET app.tenant_id = %s", (str(args.tenant),))
            cur.execute("SELECT current_database()")
            dbname = cur.fetchone()[0]
            cur.execute("""
                SELECT company, metric, count(*)
                FROM   financials
                WHERE  is_latest = TRUE
                GROUP  BY company, metric
                ORDER  BY company, metric
            """)
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        sys.exit("ABORT: zero is_latest rows visible. Either the tenant is wrong "
                 "or RLS filtered everything — this is not a coverage result.")

    live = defaultdict(dict)                 # company -> metric -> row count
    for company, metric, n in rows:
        live[company][metric] = n

    print("=" * 78)
    print("GOLDEN COVERAGE — live metrics vs golden assertions")
    print("=" * 78)
    print(f"database   : {dbname}")
    print(f"tenant     : {args.tenant}")
    print(f"datasets   : {', '.join(os.path.basename(f) for f in files)}")
    print(f"questions  : {len(questions)}")
    print(f"scope      : {'ANY company (weak)' if args.any_company else 'per-company (strict)'}")

    all_metrics, all_covered = set(), set()
    unasserted_freq = defaultdict(int)

    for company in sorted(live):
        metrics = live[company]
        qs = questions if args.any_company else [
            q for q in questions if q["company"] == company]

        by_metric = {q["expected_metric"] for q in qs if q["expected_metric"]}

        covered, rows_out = {}, []
        for metric in sorted(metrics):
            kinds = []
            if metric in by_metric:
                kinds.append("metric")
            pats = _metric_patterns(metric)
            if any(p.search(k) for q in qs for k in q["keywords"] for p in pats):
                kinds.append("keyword")
            if any(p.search(q["question"]) for q in qs for p in pats):
                kinds.append("text")
            if kinds:
                covered[metric] = kinds
            else:
                unasserted_freq[metric] += metrics[metric]
            rows_out.append((metric, metrics[metric], kinds))

        all_metrics |= {(company, m) for m in metrics}
        all_covered |= {(company, m) for m in covered}

        n_val = sum(1 for _, _, k in rows_out if "metric" in k)
        print(f"\n{'-'*78}")
        print(f"{company}: {len(metrics)} distinct live metrics | "
              f"{len(covered)} asserted ({n_val} pin a VALUE) | "
              f"{len(metrics)-len(covered)} unasserted")
        print(f"{'-'*78}")
        for metric, n, kinds in rows_out:
            if kinds:
                print(f"  [{'+'.join(kinds):<19}] {metric:<52} {n:>4} rows")
        misses = [(m, n) for m, n, k in rows_out if not k]
        if misses:
            print(f"  --- UNASSERTED ({len(misses)}) ---")
            for metric, n in sorted(misses, key=lambda t: (-t[1], t[0])):
                print(f"  [{'':<19}] {metric:<52} {n:>4} rows")

    print(f"\n{'='*78}")
    print("TOTALS")
    print(f"{'='*78}")
    print(f"  distinct (company, metric) pairs live : {len(all_metrics)}")
    print(f"  asserted by any means                 : {len(all_covered)}")
    print(f"  UNASSERTED                            : {len(all_metrics)-len(all_covered)}"
          f"  ({100.0*(len(all_metrics)-len(all_covered))/len(all_metrics):.1f}%)")
    # NOT "never asserted anywhere". This counter accumulates a metric's rows
    # from each company where it is UNCOVERED, so a name asserted for one
    # company and missed for another appears here with only the missed
    # companies' rows -- `depreciation` is asserted for ETERNAL via a keyword
    # and unasserted for PAYTM and TITAN, and belongs in this list for exactly
    # that reason. Coverage is company-scoped; so is this tally.
    print(f"\n  metric names unasserted for AT LEAST ONE company: "
          f"{len(unasserted_freq)}")
    print("  (counting only rows in the companies where it is unasserted, "
          "descending)\n")
    for metric, n in sorted(unasserted_freq.items(), key=lambda t: (-t[1], t[0])):
        print(f"    {metric:<56} {n:>5} rows")
    print("\nDONE")


if __name__ == "__main__":
    main()
