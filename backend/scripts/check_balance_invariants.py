"""
Balance-sheet invariants over the LIVE database. Hard assertions, exit 1 on
failure.

Currently one invariant: no `is_latest` row for a balance-sheet cash STOCK may
be negative.

Read-only. No writes, no LLM calls, no PDF parsing.

Usage:
  docker compose exec -T backend python -m scripts.check_balance_invariants


WHY THIS IS A SEPARATE SCRIPT AND NOT PART OF regression_check
---------------------------------------------------------------
It was written inside `regression_check` first, and moved out deliberately.

`regression_check` is HERMETIC: every one of its assertions runs against
extraction output held in memory — parse the PDF, run the extractor, inspect the
records. It has zero database references and runs identically whether postgres
is up or down. That property is worth protecting on its own.

A database read inside it would break that in two ways, one obvious and one not:

  1. IT COULD GO RED FOR ENVIRONMENTAL REASONS. An unset DATABASE_URL, a stopped
     container, a WSL2 network flap — any of these fails the check while
     extraction is perfectly healthy. A gate that goes red on a connection issue
     is a gate people learn to ignore, and then it is protecting nothing.

  2. WORSE, IT COULD GO GREEN WITHOUT CHECKING ANYTHING. `financials` is
     tenant-scoped under RLS, and RLS does NOT error on a wrong or missing
     tenant — it returns ZERO ROWS. "No negative rows" is then satisfied
     VACUOUSLY: the check reports PASS having inspected nothing at all. This is
     the exact hazard CLAUDE.md §6 records, and putting it inside the extraction
     gate would put a silent false-pass inside the project's most-trusted signal.

Split out, both problems become survivable. This script failing says "the
database could not be checked", which is a different and separately actionable
statement from "extraction regressed". `regression_check` stays hermetic and
keeps meaning exactly what it has always meant.

The vacuous-pass risk does not disappear by moving, so it is closed here
directly: each named metric must be FOUND, not merely non-negative. See
`_assert_metric` — zero visible rows is a FAILURE, never a pass.
"""

import sys

from app.ingestion.db_loader import get_connection

from scripts.regression_check import ALPHA_TENANT


# NAMED EXPLICITLY, NEVER INFERRED. NO NAME-PATTERN RULE MAY BE USED HERE.
#
# The tempting generalisations — "any metric containing 'cash'", "anything that
# looks like a balance-sheet line" — are all wrong, and measurably so. These
# metrics are live in the corpus and legitimately NEGATIVE as of 2026-08-04:
#
#     cash_generated_(used_in)/from_operations   PAYTM    -710, -139, -26
#     cash_generated_from/(used_in)_operations   ETERNAL  -813
#
# Cash-FLOW lines are movements and go negative by their nature; a substring
# rule on "cash" would fail on four correct rows immediately. Only balance-sheet
# STOCKS are covered below. Adding a name to this tuple requires the same
# evidence: that the metric is a stock and not a flow.
#
# WHY THESE TWO SPECIFICALLY. PAYTM `cash` held -710 (FY26 annual consolidated)
# and -139 (FY25) from the 2026-07-15 ingest until 2026-08-04 — three weeks of a
# headline balance-sheet figure being both sign-wrong and magnitude-wrong.
#
# The defect was MISATTRIBUTION, NOT MISREADING, and that is the useful part:
# those exact values were read correctly and still exist correctly under
# `cash_generated_(used_in)/from_operations`. An older parser claimed the
# cash-flow movement line for the balance-sheet metric, so the number was right
# and the row it landed in was wrong. Nothing in the value itself was corrupt,
# which is precisely why no extraction-level check could have seen it — only a
# semantic claim about the metric ("a stock cannot be negative") catches it.
#
# It survived because no golden question asserts `cash` for any company:
# verified across all three datasets, 90 questions, zero assertions, whether as
# expected_metric, in question text, or in expected_keywords.
_NON_NEGATIVE_METRICS = (
    "cash",
    "cash_and_cash_equivalents_as_at_the_end_of_the_year",
)


def _assert_metric(cur, metric: str) -> bool:
    cur.execute(
        "SELECT company, fiscal_year, quarter, financial_type, value "
        "FROM   financials "
        "WHERE  is_latest = TRUE AND metric = %s "
        "ORDER  BY company, fiscal_year, quarter NULLS FIRST",
        (metric,),
    )
    rows = cur.fetchall()

    # PRESENCE FIRST. Zero rows means RLS or the tenant hid them, not that the
    # invariant holds. Never let an empty result read as a pass.
    if not rows:
        print(f"  ❌ {metric}")
        print("       ZERO live rows visible. This asserts NOTHING — it is not a")
        print("       pass. RLS returns zero rows rather than erroring on a wrong")
        print("       tenant; check app.tenant_id before believing this result.")
        return False

    negatives = [r for r in rows if r[4] is not None and r[4] < 0]
    if not negatives:
        print(f"  ✅ {metric}: {len(rows)} rows, all non-negative")
        return True

    print(f"  ❌ {metric}: {len(negatives)} of {len(rows)} rows NEGATIVE")
    for company, fy, q, ftype, value in negatives:
        print(f"       {company} | {fy} | {q or 'ANNUAL'} | {ftype} = {value}")
    print("       A balance-sheet stock cannot be negative. The likeliest cause is")
    print("       MISATTRIBUTION rather than a misread digit: a cash-FLOW movement")
    print("       line claimed for this balance-sheet metric. Check which printed")
    print("       row the value came from before assuming the number is wrong —")
    print("       it may be a correct number in the wrong row.")
    return False


def main() -> int:
    print("=" * 70)
    print("BALANCE-SHEET INVARIANTS — live database")
    print("=" * 70)

    try:
        conn = get_connection()
    except Exception as e:
        print(f"  ❌ ENVIRONMENTAL: cannot connect ({type(e).__name__}: {e})")
        print("     This says nothing about extraction. regression_check is")
        print("     hermetic and is unaffected by this failure.")
        return 1

    try:
        with conn.cursor() as cur:
            # RLS: financials is tenant-scoped and returns zero rows — not an
            # error — without this. Every SELECT below depends on it.
            cur.execute("SET app.tenant_id = %s", (str(ALPHA_TENANT),))
            cur.execute("SELECT current_database()")
            print(f"  database : {cur.fetchone()[0]}")
            print(f"  tenant   : {ALPHA_TENANT}\n")

            ok = True
            for metric in _NON_NEGATIVE_METRICS:
                ok = _assert_metric(cur, metric) and ok
    except Exception as e:
        print(f"  ❌ ENVIRONMENTAL: query failed ({type(e).__name__}: {e})")
        return 1
    finally:
        conn.close()

    print()
    if not ok:
        print("❌ FAIL — a balance-sheet invariant is violated, or could not be")
        print("   evaluated. Do not treat this as an extraction regression until")
        print("   you have established which.")
        return 1
    print("✅ PASS — all balance-sheet invariants hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
