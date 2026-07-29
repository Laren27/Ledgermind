"""
Purge all Qdrant points for a given tenant + company.

Used when the vector store has drifted from the documents table and the
cleanest fix is delete-then-reindex. Deliberately filters on `company`
rather than `doc_id` so that orphaned points written under stale metadata
are swept too.

Does NOT touch PostgreSQL. documents and financials rows are untouched.

Usage:
    python -m scripts.purge_qdrant_company --company PAYTM          # dry run
    python -m scripts.purge_qdrant_company --company PAYTM --confirm
"""

import argparse
import logging
import os
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

env_path = Path.home() / "ledgermind" / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from qdrant_client.models import Filter, FieldCondition, MatchValue, FilterSelector
from app.ingestion.qdrant_writer import _get_client, COLLECTION_NAME

ALPHA_TENANT = "a0000000-0000-0000-0000-000000000001"


def build_filter(tenant_id: str, company: str) -> Filter:
    return Filter(must=[
        FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
        FieldCondition(key="company",   match=MatchValue(value=company)),
    ])


def count(client, flt: Filter) -> int:
    return client.count(
        collection_name=COLLECTION_NAME, count_filter=flt, exact=True
    ).count


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", required=True)
    ap.add_argument("--tenant", default=ALPHA_TENANT)
    ap.add_argument("--confirm", action="store_true",
                    help="Actually delete. Without this, counts only.")
    a = ap.parse_args()

    client = _get_client()
    flt = build_filter(a.tenant, a.company)

    before = count(client, flt)
    print(f"\ncollection : {COLLECTION_NAME}")
    print(f"tenant     : {a.tenant}")
    print(f"company    : {a.company}")
    print(f"matching   : {before} points")

    if before == 0:
        print("\nNothing to delete.")
        return

    if not a.confirm:
        print("\nDRY RUN — re-run with --confirm to delete.")
        return

    client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=FilterSelector(filter=flt),
        wait=True,
    )
    after = count(client, flt)
    print(f"\ndeleted    : {before - after}")
    print(f"remaining  : {after}")
    if after != 0:
        raise SystemExit(f"FAILED — {after} points survived the delete")
    print("Purge clean.")


if __name__ == "__main__":
    main()
