"""
scripts/process_pending_uploads.py

Polls the pending_uploads table for rows awaiting ingestion and runs
them through the existing storage-backed pipeline (ingest_from_storage_sync),
locally — same proven-safe execution path as the FY23/24 backfill and
every other real ingestion in this project's history.

Why this exists instead of auto-triggering on upload: loading the
embedding model in-process OOM-killed Render's 512MB free-tier web
service. This script runs on the developer's own machine (8GB+ RAM,
already proven safe), completely isolated from the live query-serving
process.

Why tenant-by-tenant, not a single cross-tenant query: pending_uploads
has RLS enabled (tenant_isolation policy), and this script connects as
ledgermind_app (not the superuser) — same role, same rules, as every
other part of this codebase. A query with no app.tenant_id set fails
RLS's uuid cast outright rather than silently returning nothing, so we
loop over each known tenant explicitly and SET app.tenant_id before
each query, matching pipeline.py's _update_document_states pattern.

Usage:
    python -m scripts.process_pending_uploads --once
    python -m scripts.process_pending_uploads --watch --interval 30
"""

import argparse
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Known tenants — matches the seeded tenants table (Alpha/Beta).
# If a new tenant is ever added, add its UUID here too.
KNOWN_TENANT_IDS = [
    "a0000000-0000-0000-0000-000000000001",  # Alpha
    "b0000000-0000-0000-0000-000000000002",  # Beta
]

_SQL_SET_TENANT = "SET app.tenant_id = %s"
_SQL_FETCH_PENDING = """
SELECT id, tenant_id, storage_key, company, ticker, fiscal_year,
       quarter, doc_type, filing_date, version
FROM pending_uploads
WHERE status = 'pending'
ORDER BY created_at ASC
LIMIT %s
"""
_SQL_MARK_STATUS = """
UPDATE pending_uploads
SET status = %s, error_message = %s, updated_at = now()
WHERE id = %s
"""


def _load_env():
    env_path = Path.home() / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def _fetch_pending_for_tenant(conn, tenant_id: str, limit: int):
    with conn.cursor() as cur:
        cur.execute(_SQL_SET_TENANT, (tenant_id,))
        cur.execute(_SQL_FETCH_PENDING, (limit,))
        cols = [desc[0] for desc in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    return rows


def _mark(conn, tenant_id: str, row_id, status: str, error_message: str = None):
    with conn.cursor() as cur:
        cur.execute(_SQL_SET_TENANT, (tenant_id,))
        cur.execute(_SQL_MARK_STATUS, (status, error_message, row_id))
    conn.commit()


def process_batch(limit: int = 5) -> int:
    from app.ingestion.db_loader import get_connection
    from app.ingestion.pipeline import ingest_from_storage_sync

    all_pending = []
    for tenant_id in KNOWN_TENANT_IDS:
        conn = get_connection()
        try:
            rows = _fetch_pending_for_tenant(conn, tenant_id, limit)
            all_pending.extend(rows)
        finally:
            conn.close()

    if not all_pending:
        return 0

    for row in all_pending:
        row_id = row["id"]
        tenant_id = str(row["tenant_id"])
        logger.info(
            "Processing pending upload id=%s tenant=%s company=%s fiscal_year=%s",
            row_id, tenant_id, row["company"], row["fiscal_year"],
        )

        conn = get_connection()
        try:
            _mark(conn, tenant_id, row_id, "processing")
        finally:
            conn.close()

        try:
            result = ingest_from_storage_sync(
                storage_key=row["storage_key"],
                tenant_id=tenant_id,
                company=row["company"],
                ticker=row["ticker"],
                fiscal_year=row["fiscal_year"],
                quarter=row["quarter"],
                doc_type=row["doc_type"],
                filing_date=row["filing_date"],
                version=row["version"],
            )
            conn = get_connection()
            try:
                _mark(conn, tenant_id, row_id, "done")
            finally:
                conn.close()
            logger.info("Done id=%s result=%s", row_id, result)

        except Exception as e:
            logger.exception("Failed id=%s", row_id)
            conn = get_connection()
            try:
                _mark(conn, tenant_id, row_id, "failed", str(e)[:500])
            finally:
                conn.close()

    return len(all_pending)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    _load_env()

    parser = argparse.ArgumentParser(description="Process pending document uploads")
    parser.add_argument("--once", action="store_true", help="Process one batch and exit")
    parser.add_argument("--watch", action="store_true", help="Poll continuously")
    parser.add_argument("--interval", type=int, default=30, help="Seconds between polls in --watch mode")
    parser.add_argument("--limit", type=int, default=5, help="Max pending rows per batch (per tenant)")
    args = parser.parse_args()

    if not args.once and not args.watch:
        parser.error("Specify --once or --watch")

    if args.once:
        n = process_batch(args.limit)
        print(f"Processed {n} pending upload(s).")
    else:
        print(f"Watching for pending uploads every {args.interval}s. Ctrl+C to stop.")
        try:
            while True:
                n = process_batch(args.limit)
                if n:
                    print(f"Processed {n} pending upload(s).")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nStopped.")
