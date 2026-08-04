"""
Database Loader — writes FinancialRecord objects to PostgreSQL financials table.

Responsibilities:
  1. Set app.tenant_id on the connection before any DML (RLS enforcement)
  2. For each record, run a transaction that:
     a. Locks existing is_latest=TRUE rows for the same metric/period (SELECT FOR UPDATE)
     b. If a newer filing exists already: skip insert (we'd be regressing)
     c. If an older filing exists: flip it to is_latest=FALSE
     d. Insert new row ON CONFLICT DO NOTHING (handles exact re-ingestion)
  3. Return a summary dict: inserted / restated / reingested / corrected /
     skipped / errors counts. "corrected" is an opt-in parser-correction path
     (correct_values=True) that updates a value in place WITHOUT touching
     is_latest — a fixed parser re-reading a fixed document is not a
     restatement by the issuer. Off by default.

Connection:
  Uses DATABASE_URL from environment (postgresql://ledgermind_app:...@postgres:5432/ledgermind)
  Called from Celery worker — caller owns connection lifecycle.
  db_loader does NOT open or close the connection.

Design decision: psycopg2 with raw SQL.
  Consistent with Phase 2 decision (raw SQL files, no ORM).
  SQLAlchemy adds nothing for flat record inserts.
"""

import logging
import os
from dataclasses import asdict
from typing import Optional

import psycopg2
import psycopg2.extras

from .models import FinancialRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SQL statements
# ---------------------------------------------------------------------------

# Step 1 of the upsert transaction:
# Lock any existing is_latest=TRUE row for the same business key.
# Returns the existing row's filing_date so we can compare, and its value so
# the parser-correction path can tell a changed figure from an unchanged one.
# IS NOT DISTINCT FROM handles NULL quarter (annual reports).
_SQL_LOCK_LATEST = """
SELECT id, filing_date, doc_id, value
FROM   financials
WHERE  company        = %(company)s
  AND  metric         = %(metric)s
  AND  fiscal_year    = %(fiscal_year)s
  AND  financial_type = %(financial_type)s
  AND  quarter        IS NOT DISTINCT FROM %(quarter)s
  AND  is_latest      = TRUE
FOR UPDATE
"""

# Identical to _SQL_LOCK_LATEST but WITHOUT `FOR UPDATE`. For the SELECT-only
# preview path, which must classify what a run would do while taking no row
# locks and holding no write transaction. Kept beside its locking twin so the
# two predicates cannot drift.
_SQL_PEEK_LATEST = """
SELECT id, filing_date, doc_id, value
FROM   financials
WHERE  company        = %(company)s
  AND  metric         = %(metric)s
  AND  fiscal_year    = %(fiscal_year)s
  AND  financial_type = %(financial_type)s
  AND  quarter        IS NOT DISTINCT FROM %(quarter)s
  AND  is_latest      = TRUE
"""

# Step 2a: Flip old row to is_latest=FALSE (restatement case)
_SQL_RETIRE_LATEST = """
UPDATE financials
SET    is_latest = FALSE
WHERE  id = %(existing_id)s
"""

# PARSER-CORRECTION path. Opt-in only (correct_values=True), reached only when
# the doc_id AND the business key both already match and only the VALUE differs.
#
# WHY THIS IS AN UPDATE AND NOT A RESTATEMENT. is_latest / retirement / a new
# row all encode a claim about the FILING's history: the issuer published a
# revised figure. Nothing of the sort happened here. The filing never changed;
# our READING of it did, because the parser was fixed. Recording a parser
# correction through the restatement machinery would manufacture a filing
# history that does not exist -- a retired "original" row the issuer never
# filed, sitting in the audit trail as though it had. So: value only. is_latest,
# doc_id, filing_date, version lineage and created_at are all left untouched.
_SQL_CORRECT_VALUE = """
UPDATE financials
SET    value = %(value)s
WHERE  id    = %(existing_id)s
RETURNING id
"""

# Step 2b: Insert new row.
# ON CONFLICT on the idempotent-re-ingestion index (uq_financials_per_doc):
# if the exact same (doc_id, metric, fiscal_year, financial_type, quarter)
# was already inserted, silently skip.
_SQL_INSERT = """
INSERT INTO financials (
    tenant_id, doc_id, company, ticker,
    fiscal_year, quarter, financial_type,
    metric, value, unit,
    filing_date, is_latest
)
VALUES (
    %(tenant_id)s, %(doc_id)s, %(company)s, %(ticker)s,
    %(fiscal_year)s, %(quarter)s, %(financial_type)s,
    %(metric)s, %(value)s, %(unit)s,
    %(filing_date)s, %(is_latest)s
)
ON CONFLICT ON CONSTRAINT uq_financials_per_doc_coalesce
DO NOTHING
RETURNING id
"""

# The ON CONFLICT target above references a constraint name. Since we used
# CREATE UNIQUE INDEX (not a named CONSTRAINT), we use ON CONFLICT DO NOTHING
# and rely on the index being enforced. Postgres raises a unique violation
# which ON CONFLICT catches. This is correct behaviour.
_SQL_INSERT_SAFE = """
INSERT INTO financials (
    tenant_id, doc_id, company, ticker,
    fiscal_year, quarter, financial_type,
    metric, value, unit,
    filing_date, is_latest
)
VALUES (
    %(tenant_id)s, %(doc_id)s, %(company)s, %(ticker)s,
    %(fiscal_year)s, %(quarter)s, %(financial_type)s,
    %(metric)s, %(value)s, %(unit)s,
    %(filing_date)s, TRUE
)
ON CONFLICT DO NOTHING
RETURNING id
"""

# RLS: set tenant context on the session before any DML
_SQL_SET_TENANT = "SET app.tenant_id = %s"


# ---------------------------------------------------------------------------
# Connection factory (used when no connection is passed in)
# ---------------------------------------------------------------------------

def get_connection():
    """
    Open a psycopg2 connection using DATABASE_URL from environment.
    Caller is responsible for closing.
    """
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable not set")
    return psycopg2.connect(database_url)


# ---------------------------------------------------------------------------
# Core upsert logic — single record
# ---------------------------------------------------------------------------

def _stored_value_differs(existing, new) -> bool:
    """True when the stored value and the freshly-extracted one are different.

    Compared as FLOATS, deliberately, and not as Decimals. `value` is numeric,
    so psycopg2 hands it back as Decimal, while record.value is a float that
    came from the parser. Decimal.__eq__ converts the float to its EXACT binary
    expansion, so Decimal("33.33") == 33.33 is False -- the stored figure and
    the one that produced it would compare as different, and every such row
    would be "corrected" to itself on every run. float(Decimal("33.33")) round-
    trips back to the same float, so this comparison answers the question
    actually being asked: would writing this record change what is stored?
    """
    if existing is None or new is None:
        return (existing is None) != (new is None)
    return float(existing) != float(new)


def classify_upsert(
    *,
    existing_doc_id,
    existing_value,
    existing_filing_date,
    record: FinancialRecord,
    correct_values: bool = False,
) -> str:
    """THE single decision. Returns what a write WOULD do, and writes nothing.

    Pure: no cursor, no I/O, no side effects. Given the existing is_latest row's
    (doc_id, value, filing_date) — or existing_doc_id=None when there is no such
    row — and the incoming record, returns exactly one of:

        "inserted" | "corrected" | "skipped" | "restated" | "reingested"

    WHY THIS EXISTS. `_upsert_one` used to decide and act in the same pass, so
    anything that wanted to know what a run WOULD do had to re-implement the
    branch order by hand. A hand-written mirror is a copy that drifts silently:
    it agrees on the day it is written and diverges at the first change to
    either side, and the whole value of a preview is that it tells the truth
    about the writer. Now `_upsert_one` calls this and ACTS on the label rather
    than re-deciding, and the preview calls the same function with rows read by
    plain SELECT. There is one decision, in one place, exercised by both.

    Adding a branch here changes the writer and the preview together. That is
    the point.
    """
    # No prior is_latest row for this business key.
    if existing_doc_id is None:
        return "inserted"

    from datetime import date
    try:
        new_date = date.fromisoformat(record.filing_date)
    except (ValueError, TypeError):
        # Unparseable filing_date: refuse rather than guess an ordering.
        return "skipped"

    # Same doc_id: the SAME document replayed (a retried Celery task, a
    # smoke-test re-run) — not a new filing, so is_latest must not move.
    if str(existing_doc_id) == str(record.doc_id):
        # ...but "same document" holds for the DOCUMENT, not for our READING of
        # it. A fixed parser legitimately yields a different figure under an
        # unchanged business key; without correct_values that difference is
        # discarded by ON CONFLICT DO NOTHING, which is how a misread 7,292
        # survived an --apply backfill against a corrected parser.
        if correct_values and _stored_value_differs(existing_value, record.value):
            return "corrected"
        return "skipped"

    # Different doc_id, older filing: we would be regressing to a stale filing.
    if new_date < existing_filing_date:
        return "skipped"

    # Different doc_id, newer or equal filing date. Both retire the old row —
    # ON CONFLICT cannot help when doc_id differs, and without retirement
    # duplicate is_latest=TRUE rows accumulate silently (root cause of a
    # 142-row duplicate incident during Phase 3 finalization testing).
    return "restated" if new_date > existing_filing_date else "reingested"


def _upsert_one(
    cursor,
    record: FinancialRecord,
    correct_values: bool = False,
) -> str:
    """
    Upsert a single FinancialRecord within an already-open transaction.

    Returns one of: "inserted" | "restated" | "reingested" | "corrected" | "skipped"

    "inserted"  — new record, no prior is_latest row existed
    "restated"  — prior is_latest row retired, new row inserted
    "reingested"— same filing_date under a new doc_id retired a stale row
    "corrected" — same doc_id, same business key, DIFFERENT value: the stored
                  figure was updated in place. Only reachable with
                  correct_values=True. See _SQL_CORRECT_VALUE for why this is
                  not a restatement.
    "skipped"   — exact same (doc_id, metric, period) already exists

    correct_values is OFF by default so that the ingestion pipeline and the
    Celery worker are unaffected: for them a same-doc_id replay genuinely is a
    replay, and silently rewriting stored figures on every retry is not
    behaviour anyone asked for. It is opted into by an operator who has just
    changed the parser and knows the re-read is the better reading.
    """
    params = {
        "company":        record.company,
        "metric":         record.metric,
        "fiscal_year":    record.fiscal_year,
        "financial_type": record.financial_type,
        "quarter":        record.quarter,
        "tenant_id":      record.tenant_id,
        "doc_id":         record.doc_id,
        "ticker":         record.ticker,
        "value":          record.value,
        "unit":           record.unit,
        "filing_date":    record.filing_date,
        "is_latest":      record.is_latest,
    }

    # --- Step 1: read the existing is_latest row (locked) ---
    cursor.execute(_SQL_LOCK_LATEST, params)
    existing = cursor.fetchone()

    if existing:
        existing_id, existing_filing_date, existing_doc_id, existing_value = existing
    else:
        existing_id = existing_filing_date = existing_doc_id = existing_value = None

    # --- Step 2: DECIDE, once, in the one place that decides ---
    # This function does not re-derive the outcome below; it acts on this label.
    # scripts/backfill_financials.py's preview calls the SAME function against
    # rows read by plain SELECT, which is why the preview cannot drift from this
    # writer -- there is no second copy of the branch order to fall out of sync.
    outcome = classify_upsert(
        existing_doc_id=existing_doc_id,
        existing_value=existing_value,
        existing_filing_date=existing_filing_date,
        record=record,
        correct_values=correct_values,
    )

    # --- Step 3: ACT on the label ---
    if outcome == "skipped":
        # Nothing to write. Every route to this label -- an unparseable
        # filing_date, an older filing, or a same-doc_id replay whose value is
        # unchanged -- is a no-op against the table. The last of those used to
        # issue an INSERT that ON CONFLICT DO NOTHING then swallowed; the row
        # count effect is identical and the round-trip is not needed.
        logger.debug(
            "Skip: %s/%s/%s/doc_id=%s",
            record.company, record.metric, record.fiscal_year, record.doc_id,
        )
        return "skipped"

    if outcome == "corrected":
        cursor.execute(_SQL_CORRECT_VALUE,
                       {"value": record.value, "existing_id": existing_id})
        if cursor.fetchone() is None:
            return "skipped"
        logger.warning(
            "CORRECTED %s | %s | %s | %s | %s : %s -> %s (doc_id %s, "
            "is_latest untouched)",
            record.company, record.metric, record.fiscal_year,
            record.quarter, record.financial_type,
            existing_value, record.value, record.doc_id,
        )
        return "corrected"

    if outcome in ("restated", "reingested"):
        # Both retire the old row before inserting. ON CONFLICT cannot help
        # when doc_id differs, and without this retirement duplicate
        # is_latest=TRUE rows accumulate silently.
        cursor.execute(_SQL_RETIRE_LATEST, {"existing_id": existing_id})
        if outcome == "restated":
            logger.info(
                "Restatement: retired %s/%s/%s (filing_date %s) -> new filing_date %s",
                record.company, record.metric, record.fiscal_year,
                existing_filing_date, record.filing_date,
            )
        else:
            logger.info(
                "Re-ingestion: retired stale is_latest row for %s/%s/%s "
                "(same filing_date %s, new doc_id) before re-inserting",
                record.company, record.metric, record.fiscal_year, record.filing_date,
            )

    # "inserted", "restated" and "reingested" all end in an insert.
    cursor.execute(_SQL_INSERT_SAFE, params)
    inserted_id = cursor.fetchone()

    if inserted_id is None:
        # ON CONFLICT DO NOTHING fired. For "inserted" this means the row
        # appeared underneath us; for the retirement outcomes it should be
        # unreachable, since the old row was just retired.
        logger.debug(
            "Duplicate skip: %s/%s/%s/doc_id=%s",
            record.company, record.metric, record.fiscal_year, record.doc_id,
        )
        return "skipped"

    logger.debug(
        "Inserted [%s] %s/%s/%s = %s %s",
        outcome, record.company, record.metric,
        record.fiscal_year, record.value, record.unit,
    )
    return outcome


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_financial_records(
    records: list[FinancialRecord],
    tenant_id: str,
    conn=None,
    correct_values: bool = False,
) -> dict:
    """
    Write a list of FinancialRecord objects to the financials table.

    Args:
        records:   List of FinancialRecord dataclass instances.
        tenant_id: UUID string for the current tenant. Set on session
                   before any DML for RLS enforcement.
        conn:      Optional psycopg2 connection. If None, opens one
                   from DATABASE_URL and closes it after. If provided,
                   caller owns the connection lifecycle.
        correct_values:
                   OFF by default. When True, a record whose doc_id AND
                   business key already match an existing is_latest row, but
                   whose VALUE differs, updates that row's value in place
                   instead of being skipped. For re-running extraction after a
                   PARSER fix. It does not flip is_latest, insert a row, or
                   retire anything -- see _SQL_CORRECT_VALUE.

    Returns:
        {
          "inserted":   int,   # new rows, no prior is_latest row existed
          "restated":   int,   # newer filing_date retired an older is_latest row
          "reingested": int,   # same filing_date retired a stale is_latest row
                                # (happens during iterative re-ingestion with a
                                # fresh doc_id each run)
          "corrected":  int,   # same doc_id + business key, value updated in
                                # place. Always 0 unless correct_values=True.
          "skipped":    int,   # duplicates or older-filing attempts
          "errors":     int,   # records that failed (logged, not raised)
        }

    Guarantees:
        - Each record is its own transaction. One bad record does not
          roll back the rest.
        - is_latest=TRUE uniqueness is enforced via SELECT FOR UPDATE
          before INSERT, preventing race conditions.
        - RLS is respected: tenant_id is set on the session first.
    """
    if not records:
        logger.info("load_financial_records called with empty list — nothing to do")
        return {"inserted": 0, "restated": 0, "reingested": 0, "corrected": 0,
                "skipped": 0, "errors": 0}

    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()

    counts = {"inserted": 0, "restated": 0, "reingested": 0, "corrected": 0,
              "skipped": 0, "errors": 0}

    try:
        # Set tenant context once for the session (RLS enforcement)
        with conn.cursor() as cur:
            cur.execute(_SQL_SET_TENANT, (str(tenant_id),))
        conn.commit()

        for record in records:
            try:
                with conn.cursor() as cur:
                    outcome = _upsert_one(cur, record, correct_values=correct_values)
                conn.commit()
                counts[outcome] += 1

            except psycopg2.Error as e:
                conn.rollback()
                counts["errors"] += 1
                logger.error(
                    "DB error upserting %s/%s/%s: %s",
                    record.company, record.metric, record.fiscal_year, e,
                )
            except Exception as e:
                conn.rollback()
                counts["errors"] += 1
                logger.error(
                    "Unexpected error upserting %s/%s/%s: %s",
                    record.company, record.metric, record.fiscal_year, e,
                )

    finally:
        if owns_conn:
            conn.close()

    logger.info(
        "load_financial_records complete: %d inserted, %d restated, %d reingested, "
        "%d corrected, %d skipped, %d errors",
        counts["inserted"], counts["restated"], counts["reingested"],
        counts["corrected"], counts["skipped"], counts["errors"],
    )
    return counts


# ---------------------------------------------------------------------------
# Verification query — run after ingestion to confirm key figures
# ---------------------------------------------------------------------------

def verify_financials(
    company: str,
    fiscal_year: str,
    financial_type: str,
    tenant_id: str,
    conn=None,
) -> list[dict]:
    """
    Return all is_latest=TRUE rows for the given company/year/type.
    Used for post-ingestion verification against golden dataset.

    Example output:
      [{"metric": "revenue", "value": 17680.0, "unit": "crore_inr", ...}, ...]
    """
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()

    rows = []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_SQL_SET_TENANT, (str(tenant_id),))
            cur.execute(
                """
                SELECT metric, value, unit, quarter, filing_date
                FROM   financials
                WHERE  company        = %s
                  AND  fiscal_year    = %s
                  AND  financial_type = %s
                  AND  is_latest      = TRUE
                ORDER BY metric
                """,
                (company, fiscal_year, financial_type),
            )
            rows = [dict(r) for r in cur.fetchall()]
    finally:
        if owns_conn:
            conn.close()

    return rows


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

def _insert_test_document(cur, doc_id: str, tenant_id: str, filing_date: str, checksum: str):
    """
    Insert a minimal documents row for smoke test use.
    The financials table has a FK on doc_id → documents.doc_id.
    This satisfies that constraint without depending on seed data state.
    """
    cur.execute(
        """
        INSERT INTO documents (
            doc_id, tenant_id, company, ticker,
            fiscal_year, quarter, doc_type, financial_type,
            filing_date, version, is_latest,
            sha256_checksum, ingestion_state
        )
        VALUES (
            %s, %s, 'ETERNAL', 'ETERNAL',
            'FY26', 'Q4', 'quarterly_result', 'consolidated',
            %s, 'v1', TRUE,
            %s, 'processing'
        )
        ON CONFLICT DO NOTHING
        """,
        (doc_id, tenant_id, filing_date, checksum),
    )


def _cleanup_test_data(conn, tenant_id: str):
    """Remove all smoke test rows. Called at end of test regardless of outcome."""
    with conn.cursor() as cur:
        cur.execute(_SQL_SET_TENANT, (tenant_id,))
        cur.execute(
            "DELETE FROM financials WHERE company = 'ETERNAL' AND fiscal_year = 'FY26'"
        )
        cur.execute(
            "DELETE FROM documents  WHERE company = 'ETERNAL' AND fiscal_year = 'FY26'"
        )
    conn.commit()
    logger.info("Test data cleaned up.")


if __name__ == "__main__":
    import uuid
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    ALPHA_TENANT   = "a0000000-0000-0000-0000-000000000001"
    TEST_DOC_ID    = str(uuid.uuid4())
    RESTATE_DOC_ID = str(uuid.uuid4())
    RUN_TAG = uuid.uuid4().hex[:8]   # unique per invocation

    conn = get_connection()

    try:
        # ----------------------------------------------------------------
        # Prerequisite: insert parent documents rows so FK is satisfied.
        # Each doc needs a unique sha256_checksum (UNIQUE constraint).
        # ----------------------------------------------------------------
        with conn.cursor() as cur:
            cur.execute(_SQL_SET_TENANT, (ALPHA_TENANT,))
            _insert_test_document(cur, TEST_DOC_ID,    ALPHA_TENANT, "2026-04-28", f"test_checksum_v1_{RUN_TAG}")
            _insert_test_document(cur, RESTATE_DOC_ID, ALPHA_TENANT, "2026-07-01", f"test_checksum_v2_{RUN_TAG}")
        conn.commit()
        print("Test documents inserted.")

        # ----------------------------------------------------------------
        # Test records
        # ----------------------------------------------------------------
        test_records = [
            FinancialRecord(
                tenant_id=ALPHA_TENANT,
                doc_id=TEST_DOC_ID,
                company="ETERNAL", ticker="ETERNAL",
                fiscal_year="FY26", quarter="Q4",
                financial_type="consolidated",
                metric="adjusted_revenue", value=17680.0,
                unit="crore_inr", filing_date="2026-04-28",
            ),
            FinancialRecord(
                tenant_id=ALPHA_TENANT,
                doc_id=TEST_DOC_ID,
                company="ETERNAL", ticker="ETERNAL",
                fiscal_year="FY26", quarter="Q4",
                financial_type="consolidated",
                metric="blinkit_nov", value=14386.0,
                unit="crore_inr", filing_date="2026-04-28",
            ),
            FinancialRecord(
                tenant_id=ALPHA_TENANT,
                doc_id=TEST_DOC_ID,
                company="ETERNAL", ticker="ETERNAL",
                fiscal_year="FY26", quarter=None,      # annual figure
                financial_type="standalone",
                metric="revenue", value=10899.0,
                unit="crore_inr", filing_date="2026-04-28",
            ),
        ]

        # ----------------------------------------------------------------
        print("\n--- Scenario 1: First ingestion ---")
        result = load_financial_records(test_records, ALPHA_TENANT, conn)
        print(f"Result: {result}")
        assert result["inserted"] == 3, f"Expected 3 inserted, got {result}"
        assert result["errors"]   == 0

        # ----------------------------------------------------------------
        print("\n--- Scenario 2: Re-ingestion (same doc_id) → all should skip ---")
        result2 = load_financial_records(test_records, ALPHA_TENANT, conn)
        print(f"Result: {result2}")
        assert result2["skipped"] == 3, f"Expected 3 skipped, got {result2}"

        # ----------------------------------------------------------------
        print("\n--- Scenario 3: Restatement (newer filing_date, different doc_id) ---")
        restated = [
            FinancialRecord(
                tenant_id=ALPHA_TENANT,
                doc_id=RESTATE_DOC_ID,              # different document
                company="ETERNAL", ticker="ETERNAL",
                fiscal_year="FY26", quarter="Q4",
                financial_type="consolidated",
                metric="adjusted_revenue",
                value=17750.0,                      # restated figure
                unit="crore_inr",
                filing_date="2026-07-01",           # newer filing date
            ),
        ]
        result3 = load_financial_records(restated, ALPHA_TENANT, conn)
        print(f"Result: {result3}")
        assert result3["restated"] == 1, f"Expected 1 restated, got {result3}"

        # ----------------------------------------------------------------
        print("\n--- Scenario 4: Same filing_date, different doc_id → "
              "reingested, not duplicated ---")
        same_date_doc_id = str(uuid.uuid4())
        with conn.cursor() as cur:
            cur.execute(_SQL_SET_TENANT, (ALPHA_TENANT,))
            _insert_test_document(cur, same_date_doc_id, ALPHA_TENANT, "2026-04-28", f"test_checksum_v3_{RUN_TAG}")
        conn.commit()

        same_date_record = [
            FinancialRecord(
                tenant_id=ALPHA_TENANT,
                doc_id=same_date_doc_id,
                company="ETERNAL", ticker="ETERNAL",
                fiscal_year="FY26", quarter="Q4",
                financial_type="consolidated",
                metric="blinkit_nov",          # untouched by Scenario 3 — still at 2026-04-28
                value=14500.0,                 # different value, same period, same filing_date
                unit="crore_inr",
                filing_date="2026-04-28",      # matches existing blinkit_nov row exactly
            ),
        ]
        result4 = load_financial_records(same_date_record, ALPHA_TENANT, conn)
        print(f"Result: {result4}")
        assert result4["reingested"] == 1, f"Expected 1 reingested, got {result4}"

        rows = verify_financials("ETERNAL", "FY26", "consolidated", ALPHA_TENANT, conn)
        matching = [r for r in rows if r["metric"] == "blinkit_nov"]
        assert len(matching) == 1, \
            f"Expected exactly 1 is_latest row after re-ingestion, got {len(matching)}"
        assert float(matching[0]["value"]) == 14500.0, \
            f"Expected updated value 14500.0 after reingestion, got {matching[0]['value']}"

        # ----------------------------------------------------------------
        print("\n--- Verification: is_latest values after restatement ---")
        rows = verify_financials("ETERNAL", "FY26", "consolidated", ALPHA_TENANT, conn)
        for row in rows:
            print(f"  {row['metric']}: {row['value']} {row['unit']} (filing: {row['filing_date']})")
        # adjusted_revenue should show 17750 (restated), not 17680
        revenue_row = next(r for r in rows if r["metric"] == "adjusted_revenue")
        assert float(revenue_row["value"]) == 17750.0, \
            f"Expected restated value 17750, got {revenue_row['value']}"

        print("\nAll smoke tests passed.")

    finally:
        # Always clean up, even on assertion failure
        _cleanup_test_data(conn, ALPHA_TENANT)
        conn.close()