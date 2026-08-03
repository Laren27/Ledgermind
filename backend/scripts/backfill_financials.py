"""
Re-run ONLY Stage 7 (financial extraction -> PostgreSQL) for existing documents.

WHY THIS EXISTS
----------------
When an extraction fix changes WHICH rows the extractor emits, the database
needs the new rows -- but a full pipeline run would also re-chunk, re-embed and
re-upsert Qdrant, all of which produce byte-identical output for an unchanged
document. That is pure risk: rewriting Qdrant payloads is exactly what produced
the PAYTM FY99 metadata drift, and re-registering documents re-enters
document_classifier's ON CONFLICT path for no benefit.

pipeline.py has --skip-financials (stages 1-6 only). This is the inverse, and
there is no flag for it.

SAFETY
-------
db_loader._SQL_LOCK_LATEST matches on the BUSINESS KEY (company, metric,
fiscal_year, financial_type, quarter) -- NOT doc_id. So re-running Stage 7
against the same filing_date is a supported path: genuinely new metric names
come back as "inserted", and everything already present is left alone. Each
record is its own transaction.

doc_ids are READ from the documents table, never minted. A new doc_id would
orphan the rows from their source document and break Principle 3's lineage.

WHAT THIS SCRIPT CANNOT DO WITHOUT --correct-values, and why
------------------------------------------------------------
Reading the existing doc_ids has a consequence that is easy to miss and cost a
real correction: because record.doc_id always EQUALS the stored doc_id,
_upsert_one takes its same-doc_id branch every single time. That branch reasons
"same document replayed, so nothing can have changed" and falls through to an
insert that the (doc_id, metric, period) unique index swallows. The result is
"skipped", and a CHANGED VALUE under an unchanged business key is silently
discarded. It never reaches the "reingested" path -- that needs a DIFFERENT
doc_id, which this script deliberately never mints.

Measured 2026-08-04: after the fragment-joining parser fix, a plain --apply run
against ETERNAL reported 0 inserted / 0 restated / 0 reingested / 460 skipped
and left the misread 7,292 in place.

--correct-values closes exactly that gap and nothing else. When doc_id and
business key both match and only the value differs, the value is UPDATEd in
place. is_latest is NOT flipped, no row is inserted, nothing is retired: the
issuer did not restate anything, our parser was wrong. Off by default, because
for the ingestion pipeline and the Celery worker a same-doc_id replay really is
a replay and must stay inert.

Usage:
  docker compose exec -T backend python -m scripts.backfill_financials --company PAYTM
  docker compose exec -T backend python -m scripts.backfill_financials --company PAYTM --apply

  # after a parser fix, to push corrected readings into existing rows:
  docker compose exec -T backend python -m scripts.backfill_financials \
      --company ETERNAL --apply --correct-values
"""

import argparse
import logging
import sys

from app.ingestion.pdf_parser import parse_pdf
from app.ingestion.document_classifier import detect_sections
from app.ingestion.section_classifier import classify_blocks
from app.ingestion.financial_extractor import extract_all_financial_records
from app.ingestion.db_loader import get_connection, load_financial_records

from scripts.regression_check import DOCUMENTS, RAW_DIR, ALPHA_TENANT

logger = logging.getLogger(__name__)


def fetch_doc_id_map(conn, tenant_id: str, company: str, fiscal_year: str) -> dict:
    """financial_type -> doc_id, from the EXISTING documents rows."""
    with conn.cursor() as cur:
        cur.execute("SET app.tenant_id = %s", (str(tenant_id),))
        cur.execute(
            "SELECT financial_type, doc_id FROM documents "
            "WHERE company = %s AND fiscal_year = %s AND is_latest = TRUE",
            (company, fiscal_year),
        )
        return {row[0]: str(row[1]) for row in cur.fetchall()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", required=True,
                    help="Company key as it appears in regression_check.DOCUMENTS")
    ap.add_argument("--apply", action="store_true",
                    help="Actually write. Without this the script only reports.")
    ap.add_argument("--correct-values", action="store_true",
                    help="Re-extraction correction: when doc_id AND business key "
                         "match an existing is_latest row but the VALUE differs, "
                         "UPDATE that value in place. Use after a PARSER fix. Does "
                         "not flip is_latest, insert, or retire anything.")
    ap.add_argument("--tenant", default=ALPHA_TENANT)
    args = ap.parse_args()

    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
    if args.correct_values:
        # Corrections are logged by db_loader at WARNING, and this script
        # otherwise runs at ERROR to keep the extractor's "Unknown metric"
        # chatter out of the way. Raise the level on that ONE logger so every
        # old -> new line is actually visible. Setting the child logger is what
        # matters: basicConfig's handler has no level of its own, so the record
        # reaches stderr once it clears this logger's effective level.
        logging.getLogger("app.ingestion.db_loader").setLevel(logging.WARNING)

    docs = [d for d in DOCUMENTS if d["company"] == args.company]
    if not docs:
        print(f"ABORT: no document in regression_check.DOCUMENTS for {args.company}")
        sys.exit(1)

    conn = get_connection()
    try:
        for doc in docs:
            pdf_path = RAW_DIR / doc["filename"]
            if not pdf_path.exists():
                print(f"ABORT: source PDF missing: {pdf_path}")
                sys.exit(1)

            doc_id_map = fetch_doc_id_map(
                conn, args.tenant, doc["company"], doc["fiscal_year"])
            if not doc_id_map:
                print(f"ABORT: no documents rows for {doc['company']}/{doc['fiscal_year']}. "
                      f"This script backfills EXISTING documents; ingest first.")
                sys.exit(1)

            print(f"\n{doc['filename']}")
            print(f"  existing doc_ids: {doc_id_map}")

            blocks = parse_pdf(str(pdf_path))
            sections = detect_sections(blocks)
            blocks = classify_blocks(blocks, sections)

            records = extract_all_financial_records(
                blocks=blocks, pdf_path=str(pdf_path), tenant_id=args.tenant,
                company=doc["company"], ticker=doc["ticker"],
                filing_date=doc["filing_date"], doc_id_map=doc_id_map,
            )
            print(f"  extracted: {len(records)} records")

            if not args.apply:
                print("  DRY RUN — nothing written. Re-run with --apply.")
                continue

            result = load_financial_records(
                records, args.tenant, conn, correct_values=args.correct_values)
            print(f"  loaded: {result}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
