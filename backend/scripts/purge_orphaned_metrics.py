"""
Purge orphaned metric rows from `financials`.

WHY THIS EXISTS
----------------
db_loader's _SQL_LOCK_LATEST matches on the full business key INCLUDING
`metric`, so a re-ingest only retires rows whose metric name it re-produces.
When an extraction fix stops emitting a name -- a guard starts skipping a
prose row, or an alias starts resolving to a different canonical -- the old
row is never looked up, never retired, and stays is_latest=TRUE forever.

That is not a loader bug: retirement is keyed on the business key by design,
and a blanket "delete all rows for this doc_id first" would break the
restatement path, where prior rows must be RETIRED (preserved, is_latest
flipped) rather than destroyed. The orphans are a maintenance obligation of
extraction changes, not a defect to fix in the write path.

Confirmed live 2026-08-01 on TITAN: 15 rows written 2026-07-18 survived a
2026-08-01 re-ingest, including narrative prose stored as a metric
('during_the_quarter_ended_june_2025_the_group_sold_gold-ingots...') and
pure OCR noise ('.1_203' = 1751.0). Both shapes are caught by guards added
2026-07-31; neither row was removed, because the current extractor no longer
emits those names for anything to match against.

Same phenomenon produces the alias-duplication rows (other_operating_revenue
alongside other_operating_revenues, pat alongside pat_attributable_to_owners):
the coverage floor changed which canonical a label resolves to, and the
pre-floor name was orphaned rather than replaced.

METHOD
-------
Re-runs extraction over every reference PDF in regression_check.DOCUMENTS to
build the set of business keys the CURRENT code produces, then reports any
is_latest=TRUE row in `financials` whose key is absent from that set.

SCOPE GUARD (the part that must not be removed)
------------------------------------------------
The produced-set is unioned across ALL documents before any comparison, and a
row is only a deletion candidate if its (company, fiscal_year, quarter,
financial_type) group was produced by at least one document. ETERNAL spans two
source PDFs (FY24 annual + FY26 quarterly); comparing its rows against a
single document's output would mark every row from the other filing as
orphaned. A group NO document produces is reported separately and never
deleted -- that means the source PDF is missing from docs/raw, not that the
rows are stale. Same conservatism as purge_mangled_metrics.py: when the rule
cannot decide, report and keep.

Read-only by default. --apply is required to delete, and deletes run in one
transaction so a partial purge cannot happen.

Usage:
  docker compose exec -T backend python -m scripts.purge_orphaned_metrics
  docker compose exec -T backend python -m scripts.purge_orphaned_metrics --apply
"""

import argparse
import logging
import sys
from collections import defaultdict

from app.db.session import db_transaction
from app.ingestion.pdf_parser import parse_pdf
from app.ingestion.document_classifier import detect_sections
from app.ingestion.section_classifier import classify_blocks
from app.ingestion.financial_extractor import extract_all_financial_records

from scripts.regression_check import DOCUMENTS, RAW_DIR, ALPHA_TENANT

logger = logging.getLogger(__name__)

# Business key WITHOUT metric -- identifies a period/statement group.
GroupKey = tuple  # (company, fiscal_year, quarter, financial_type)
# Full business key, matching db_loader's _SQL_LOCK_LATEST.
RowKey = tuple    # (company, fiscal_year, quarter, financial_type, metric)


def build_produced_sets() -> tuple[set, set]:
    """Re-run extraction over every reference PDF.

    Returns (produced_row_keys, produced_group_keys). Aborts if any document
    is missing: a partial produced-set would mark every row from the absent
    document as orphaned, which is precisely the deletion this script must
    never make.
    """
    row_keys: set = set()
    group_keys: set = set()

    for doc in DOCUMENTS:
        pdf_path = RAW_DIR / doc["filename"]
        if not pdf_path.exists():
            print(f"ABORT: source PDF missing: {pdf_path}")
            print("A partial produced-set would orphan every row from this document.")
            sys.exit(1)

        print(f"  extracting {doc['filename']} ...", flush=True)
        blocks = parse_pdf(str(pdf_path))
        sections = detect_sections(blocks)
        blocks = classify_blocks(blocks, sections)
        doc_id_map = {s.financial_type: f"diagnostic-{s.financial_type}" for s in sections}

        records = extract_all_financial_records(
            blocks=blocks, pdf_path=str(pdf_path), tenant_id=ALPHA_TENANT,
            company=doc["company"], ticker=doc["ticker"],
            filing_date=doc["filing_date"], doc_id_map=doc_id_map,
        )
        print(f"    -> {len(records)} records", flush=True)

        for r in records:
            g = (r.company, r.fiscal_year, r.quarter, r.financial_type)
            group_keys.add(g)
            row_keys.add(g + (r.metric,))

    return row_keys, group_keys


def fetch_live_rows(tenant_id: str) -> list:
    with db_transaction(tenant_id) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT f.id, d.company, f.fiscal_year, f.quarter,
                   f.financial_type, f.metric, f.value, f.created_at
            FROM financials f
            JOIN documents d ON d.doc_id = f.doc_id
            WHERE f.is_latest = TRUE
            ORDER BY d.company, f.fiscal_year, f.quarter, f.metric
        """)
        return cur.fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="Actually delete. Without this the script is read-only.")
    ap.add_argument("--tenant", default=ALPHA_TENANT)
    args = ap.parse_args()

    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")

    print("Rebuilding produced-set from source PDFs...")
    produced_rows, produced_groups = build_produced_sets()
    print(f"\nProduced: {len(produced_rows)} row keys across "
          f"{len(produced_groups)} period groups\n")

    live = fetch_live_rows(args.tenant)
    print(f"Live is_latest rows: {len(live)}\n")

    orphans = []
    unscoped = defaultdict(list)

    for row in live:
        row_id, company, fy, q, ftype, metric, value, created = row
        g = (company, fy, q, ftype)
        if g not in produced_groups:
            unscoped[g].append((metric, value, created))
            continue
        if g + (metric,) not in produced_rows:
            orphans.append((row_id, company, fy, q, ftype, metric, value, created))

    if unscoped:
        print("=" * 72)
        print("NOT EVALUATED — no reference document produces these period groups.")
        print("Reported, never deleted: this means a source PDF is absent from")
        print("docs/raw or missing from regression_check.DOCUMENTS, NOT that the")
        print("rows are stale.")
        print("=" * 72)
        for g, rows in sorted(unscoped.items(), key=lambda kv: str(kv[0])):
            print(f"  {g}  ({len(rows)} rows)")
        print()

    print("=" * 72)
    print(f"ORPHANED: {len(orphans)} rows whose metric the current extractor "
          f"no longer produces")
    print("=" * 72)
    by_company = defaultdict(list)
    for o in orphans:
        by_company[o[1]].append(o)
    for company, rows in sorted(by_company.items()):
        print(f"\n{company} — {len(rows)} rows")
        for _id, _c, fy, q, ftype, metric, value, created in rows:
            print(f"  {fy} {str(q):<5} {ftype:<12} {str(value):>12}  "
                  f"{created:%Y-%m-%d}  {metric}")

    if not orphans:
        print("\nNothing to purge.")
        return

    if not args.apply:
        print(f"\nDRY RUN — nothing deleted. Re-run with --apply to remove "
              f"these {len(orphans)} rows.")
        return

    ids = [o[0] for o in orphans]
    with db_transaction(args.tenant) as conn:
        cur = conn.cursor()
        # ::uuid[] cast is required: psycopg2 adapts Python UUIDs as text,
        # and Postgres has no implicit uuid = text operator.
        cur.execute(
            "DELETE FROM financials WHERE id = ANY(%s::uuid[])",
            ([str(i) for i in ids],),
        )
        deleted = cur.rowcount
    print(f"\nDeleted {deleted} rows.")


if __name__ == "__main__":
    main()
