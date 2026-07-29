"""
Repair corrupted Qdrant payload metadata for PAYTM chunks.

Background: a live test of the upload endpoint re-registered the real Paytm
PDF with junk form metadata (fiscal_year FY99, Eternal's filing_date). The
documents table was protected by ON CONFLICT DO UPDATE, which only writes
ingestion_state — but chunk_blocks received the junk values as arguments and
wrote them into every Qdrant payload.

The chunk TEXT and vectors are correct (chunk text is raw PDF content; no
metadata is injected before embedding), so this is a payload-only repair.
No re-parse, no re-embed, no PostgreSQL involvement.

The filter is self-limiting: once repaired, fiscal_year=FY99 matches nothing,
so re-running is a no-op.

Usage:
    python -m scripts.repair_paytm_payload            # dry run
    python -m scripts.repair_paytm_payload --confirm
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

from qdrant_client.models import Filter, FieldCondition, MatchValue
from app.ingestion.qdrant_writer import _get_client, COLLECTION_NAME

TENANT = "a0000000-0000-0000-0000-000000000001"
COMPANY = "PAYTM"
BAD_FISCAL_YEAR = "FY99"

CORRECT = {
    "fiscal_year": "FY26",
    "filing_date": "2026-05-06",
    "valid_from":  "2026-05-06",
}


def bad_filter() -> Filter:
    return Filter(must=[
        FieldCondition(key="tenant_id",   match=MatchValue(value=TENANT)),
        FieldCondition(key="company",     match=MatchValue(value=COMPANY)),
        FieldCondition(key="fiscal_year", match=MatchValue(value=BAD_FISCAL_YEAR)),
    ])


def fixed_filter() -> Filter:
    return Filter(must=[
        FieldCondition(key="tenant_id",   match=MatchValue(value=TENANT)),
        FieldCondition(key="company",     match=MatchValue(value=COMPANY)),
        FieldCondition(key="fiscal_year", match=MatchValue(value=CORRECT["fiscal_year"])),
        FieldCondition(key="filing_date", match=MatchValue(value=CORRECT["filing_date"])),
    ])


def count(client, flt: Filter) -> int:
    return client.count(
        collection_name=COLLECTION_NAME, count_filter=flt, exact=True
    ).count


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", action="store_true",
                    help="Actually write. Without this, counts only.")
    a = ap.parse_args()

    client = _get_client()
    before = count(client, bad_filter())

    print(f"\ncollection    : {COLLECTION_NAME}")
    print(f"company       : {COMPANY}")
    print(f"matching FY99 : {before} points")
    print(f"will set      : {CORRECT}")

    if before == 0:
        print("\nNothing to repair — already clean, or filter does not match.")
        return

    if not a.confirm:
        print("\nDRY RUN — re-run with --confirm to write.")
        return

    client.set_payload(
        collection_name=COLLECTION_NAME,
        payload=CORRECT,
        points=bad_filter(),
        wait=True,
    )

    remaining = count(client, bad_filter())
    repaired  = count(client, fixed_filter())
    print(f"\nremaining FY99 : {remaining}")
    print(f"now correct    : {repaired}")

    if remaining != 0:
        raise SystemExit(f"FAILED — {remaining} points still on {BAD_FISCAL_YEAR}")
    if repaired != before:
        raise SystemExit(f"FAILED — expected {before} repaired, found {repaired}")
    print("Repair clean.")


if __name__ == "__main__":
    main()
