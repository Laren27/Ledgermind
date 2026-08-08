"""
Assert that Qdrant and `documents` agree about which documents exist.

WHY THIS EXISTS
----------------
Measured 2026-08-08: 139 of 2555 Qdrant points carried a doc_id absent from
`documents` -- PAYTM's ENTIRE vector corpus (115) and half of TITAN's (24). A
live PAYTM query returned path=semantic, tier=high, five citations, and every
one of those citations pointed at a document that does not exist.

IT FAILED SILENTLY AND CONFIDENTLY. semantic_engine._build_citations
constructs a Citation entirely from the Qdrant payload and never joins back to
`documents`, so a dead reference renders identically to a good one. No code
path anywhere could notice. The answer was fluent, apparently well-sourced,
and completely untraceable.

That was the third defect of one class found in a single session -- the
citation floor produced a figure with no citation; this produced citations
with no document. The class is: NOTHING VERIFIES THAT A CITATION RESOLVES.
This script is that verification.

TWO DIRECTIONS OF ONE QUESTION
-------------------------------
A. DANGLING CHUNKS  -- a Qdrant doc_id with no `documents` row. Citations
   resolve to nothing.
B. EMPTY DOCUMENTS  -- a `documents` row at ingestion_state='indexed' with no
   Qdrant chunks. A failed ingest wearing a success label. Nothing else
   detects this: the Phase 3 gate asserts on the chunk count its OWN run
   produced, so a document that silently lost its chunks later stays 'indexed'
   forever.

REPORTS, NEVER DELETES -- and that is not caution, it is measured
----------------------------------------------------------------
The obvious repair for direction A is to delete the dangling points. On
2026-08-08 that would have removed PAYTM from retrieval entirely, because the
dangling chunks were the only PAYTM chunks in existence. The correct repair
was a RE-INGEST under the live doc_ids. A script cannot tell those cases
apart, so it does not try. Same rule as purge_orphaned_metrics' NOT EVALUATED
bucket: when the rule cannot decide, report and keep.

DELIBERATELY NOT INSIDE regression_check
-----------------------------------------
`regression_check` is hermetic -- it parses PDFs and asserts on extraction
output, and runs identically whether Postgres and Qdrant are up or down. A
network check inside it either destroys that property or, worse, PASSES
HAVING INSPECTED NOTHING when a store is unreachable. Both presence
assertions below exist for the same reason: RLS returns ZERO ROWS on a wrong
tenant rather than erroring, so "no dangling ids found" over an empty result
set is not a pass.

Usage:
  docker compose exec -T backend python -m scripts.check_citation_integrity
  docker compose exec -T backend python -m scripts.check_citation_integrity --tenant <uuid>
"""

import argparse
import sys
from collections import Counter

from app.engines.retriever import _get_qdrant_client, COLLECTION_NAME
from app.ingestion.db_loader import get_connection

ALPHA_TENANT = "a0000000-0000-0000-0000-000000000001"


def scan_qdrant(tenant_id: str) -> Counter:
    """(doc_id, company, fiscal_year, document_type) -> point count.

    Scrolls the whole collection rather than filtering on tenant_id, then
    filters in Python, so a payload MISSING tenant_id is visible rather than
    silently excluded by the filter that is supposed to be checking it.
    """
    client = _get_qdrant_client()
    seen: Counter = Counter()
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION_NAME, limit=500,
            offset=offset, with_payload=True,
        )
        for p in points:
            pl = p.payload or {}
            if pl.get("tenant_id") != tenant_id:
                continue
            seen[(
                pl.get("doc_id"),
                pl.get("company"),
                pl.get("fiscal_year"),
                pl.get("document_type"),
            )] += 1
        if offset is None:
            break
    return seen


def fetch_documents(tenant_id: str) -> dict:
    """doc_id -> (company, fiscal_year, quarter, doc_type, financial_type, state)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SET app.tenant_id = %s", (tenant_id,))
            cur.execute(
                "SELECT doc_id, company, fiscal_year, quarter, doc_type, "
                "       financial_type, ingestion_state "
                "FROM documents ORDER BY company, fiscal_year, financial_type"
            )
            return {str(r[0]): tuple(r[1:]) for r in cur.fetchall()}
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default=ALPHA_TENANT)
    args = ap.parse_args()

    print("Citation integrity — Qdrant vs documents")
    print(f"tenant: {args.tenant}\n")

    chunks = scan_qdrant(args.tenant)
    docs = fetch_documents(args.tenant)

    total_points = sum(chunks.values())
    print(f"Qdrant points for this tenant : {total_points}")
    print(f"documents rows for this tenant: {len(docs)}\n")

    # PRESENCE ASSERTIONS. An empty result on either side means the check
    # inspected nothing -- a wrong tenant, an unreachable store, an empty
    # collection. Not a pass.
    if total_points == 0:
        print("FAIL: zero Qdrant points for this tenant — nothing was inspected.")
        return 1
    if not docs:
        print("FAIL: zero documents rows for this tenant — nothing was inspected.")
        return 1

    failed = False

    # ── Direction A: dangling chunks ──────────────────────────────────────
    dangling = {k: v for k, v in chunks.items() if k[0] not in docs}
    print("=" * 72)
    if dangling:
        failed = True
        n = sum(dangling.values())
        print(f"FAIL — {n} point(s) across {len(dangling)} doc_id(s) have NO "
              f"`documents` row.")
        print("Citations built from these payloads resolve to nothing.")
        print("=" * 72)
        for (doc_id, company, fy, dtype), count in sorted(
            dangling.items(), key=lambda kv: -kv[1]
        ):
            print(f"  {count:6d}  {company or '?':<8} {fy or '?':<5} "
                  f"{dtype or '?':<20} {doc_id}")
        print("\nDO NOT DELETE THESE WITHOUT CHECKING WHAT ELSE COVERS THE")
        print("DOCUMENT. On 2026-08-08 the dangling PAYTM chunks were the only")
        print("PAYTM chunks in existence; the repair was a re-ingest under the")
        print("live doc_id, not a purge.")
    else:
        print("PASS — every Qdrant doc_id resolves to a `documents` row.")
    print("=" * 72)

    # ── Direction B: indexed documents with no chunks ─────────────────────
    doc_ids_with_chunks = {k[0] for k in chunks}
    empty = {
        d: meta for d, meta in docs.items()
        if meta[5] == "indexed" and d not in doc_ids_with_chunks
    }
    print()
    print("=" * 72)
    if empty:
        failed = True
        print(f"FAIL — {len(empty)} document(s) marked 'indexed' have ZERO "
              f"Qdrant chunks.")
        print("A failed or lost ingest wearing a success label.")
        print("=" * 72)
        for d, (company, fy, q, dtype, ftype, state) in sorted(
            empty.items(), key=lambda kv: str(kv[1])
        ):
            print(f"  {company:<8} {fy:<5} {str(q):<5} {dtype:<20} "
                  f"{ftype:<13} {d}")
    else:
        print("PASS — every 'indexed' document has at least one Qdrant chunk.")
    print("=" * 72)

    # Non-'indexed' rows are reported, never failed: 'processing' is a
    # legitimate transient state, and a document mid-ingest legitimately has
    # no chunks yet.
    other = {d: m for d, m in docs.items() if m[5] != "indexed"}
    if other:
        print(f"\nNOT EVALUATED — {len(other)} document(s) not in state "
              f"'indexed' (reported, not failed):")
        for d, (company, fy, q, dtype, ftype, state) in sorted(
            other.items(), key=lambda kv: str(kv[1])
        ):
            have = sum(v for k, v in chunks.items() if k[0] == d)
            print(f"  {company:<8} {fy:<5} {dtype:<20} state={state:<12} "
                  f"chunks={have}")

    print()
    if failed:
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
