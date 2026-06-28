"""
Database Loader — writes FinancialRecord objects to PostgreSQL financials table.

Responsibilities:
  1. Set app.tenant_id on the connection before any DML (RLS enforcement)
  2. For each record, run a transaction that:
     a. Locks existing is_latest=TRUE rows for the same metric/period (SELECT FOR UPDATE)
     b. If a newer filing exists already: skip insert (we'd be regressing)
     c. If an older filing exists: flip it to is_latest=FALSE
     d. Insert new row ON CONFLICT DO NOTHING (handles exact re-ingestion)
  3. Return a summary dict: inserted / skipped / restated counts

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
# Returns the existing row's filing_date so we can compare.
# IS NOT DISTINCT FROM handles NULL quarter (annual reports).
_SQL_LOCK_LATEST = """
SELECT id, filing_date
FROM   financials
WHERE  company        = %(company)s
  AND  metric         = %(metric)s
  AND  fiscal_year    = %(fiscal_year)s
  AND  financial_type = %(financial_type)s
  AND  quarter        IS NOT DISTINCT FROM %(quarter)s
  AND  is_latest      = TRUE
FOR UPDATE
"""

# Step 2a: Flip old row to is_latest=FALSE (restatement case)
_SQL_RETIRE_LATEST = """
UPDATE financials
SET    is_latest = FALSE
WHERE  id = %(existing_id)s
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

def _upsert_one(
    cursor,
    record: FinancialRecord,
) -> str:
    """
    Upsert a single FinancialRecord within an already-open transaction.

    Returns one of: "inserted" | "restated" | "skipped"

    "inserted"  — new record, no prior is_latest row existed
    "restated"  — prior is_latest row retired, new row inserted
    "skipped"   — exact same (doc_id, metric, period) already exists
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

    # --- Step 1: Lock existing is_latest row ---
    cursor.execute(_SQL_LOCK_LATEST, params)
    existing = cursor.fetchone()

    outcome = "inserted"

    if existing:
        existing_id, existing_filing_date = existing

        # Compare filing dates
        # record.filing_date is an ISO string "YYYY-MM-DD"
        # existing_filing_date comes back as a datetime.date object from psycopg2
        from datetime import date
        new_date_str = record.filing_date
        try:
            new_date = date.fromisoformat(new_date_str)
        except (ValueError, TypeError):
            logger.error(
                "Invalid filing_date '%s' for %s/%s/%s — skipping",
                new_date_str, record.company, record.metric, record.fiscal_year,
            )
            return "skipped"

        if new_date < existing_filing_date:
            # The filing we're trying to load is OLDER than what's already in DB.
            # This would be a regression. Skip entirely.
            logger.warning(
                "Skipping %s/%s/%s: new filing_date %s is older than existing %s",
                record.company, record.metric, record.fiscal_year,
                new_date, existing_filing_date,
            )
            return "skipped"

        if new_date == existing_filing_date:
            # Same filing date — likely a re-ingestion of the same document.
            # The ON CONFLICT DO NOTHING in the INSERT handles the exact duplicate.
            # Fall through to INSERT — it will no-op if doc_id+metric already exists.
            pass

        if new_date > existing_filing_date:
            # New filing is genuinely newer — this is a restatement.
            # Retire the old row first.
            cursor.execute(_SQL_RETIRE_LATEST, {"existing_id": existing_id})
            logger.info(
                "Restatement: retired %s/%s/%s (filing_date %s) → new filing_date %s",
                record.company, record.metric, record.fiscal_year,
                existing_filing_date, new_date,
            )
            outcome = "restated"

    # --- Step 2: Insert new row ---
    cursor.execute(_SQL_INSERT_SAFE, params)
    inserted_id = cursor.fetchone()

    if inserted_id is None:
        # ON CONFLICT DO NOTHING fired — exact duplicate
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

    Returns:
        {
          "inserted":  int,   # new rows
          "restated":  int,   # rows where an older is_latest was retired
          "skipped":   int,   # duplicates or older-filing attempts
          "errors":    int,   # records that failed (logged, not raised)
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
        return {"inserted": 0, "restated": 0, "skipped": 0, "errors": 0}

    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()

    counts = {"inserted": 0, "restated": 0, "skipped": 0, "errors": 0}

    try:
        # Set tenant context once for the session (RLS enforcement)
        with conn.cursor() as cur:
            cur.execute(_SQL_SET_TENANT, (str(tenant_id),))
        conn.commit()

        for record in records:
            try:
                with conn.cursor() as cur:
                    outcome = _upsert_one(cur, record)
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
        "load_financial_records complete: %d inserted, %d restated, %d skipped, %d errors",
        counts["inserted"], counts["restated"], counts["skipped"], counts["errors"],
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

    ALPHA_TENANT  = "a0000000-0000-0000-0000-000000000001"
    TEST_DOC_ID   = str(uuid.uuid4())
    RESTATE_DOC_ID = str(uuid.uuid4())

    conn = get_connection()

    try:
        # ----------------------------------------------------------------
        # Prerequisite: insert parent documents rows so FK is satisfied.
        # Each doc needs a unique sha256_checksum (UNIQUE constraint).
        # ----------------------------------------------------------------
        with conn.cursor() as cur:
            cur.execute(_SQL_SET_TENANT, (ALPHA_TENANT,))
            _insert_test_document(cur, TEST_DOC_ID,    ALPHA_TENANT, "2026-04-28", "test_checksum_v1")
            _insert_test_document(cur, RESTATE_DOC_ID, ALPHA_TENANT, "2026-07-01", "test_checksum_v2")
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