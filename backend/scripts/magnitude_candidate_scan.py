"""
LedgerMind — magnitude contradiction candidate scan
====================================================
Run:
  docker compose exec -T -e PYTHONPATH=/app backend \
    python -m scripts.magnitude_candidate_scan

READ-ONLY. Zero LLM calls, zero Cohere calls, zero writes to Qdrant or
Postgres. Its only output is a JSON file under /app/measurements/.

WHAT IT ANSWERS
    detect_magnitude_contradictions only ever sees the handful of chunks a
    single query retrieved, so nobody has ever seen the whole population it
    would flag. This sweeps EVERY chunk in the collection against EVERY
    is_latest metric for that chunk's company and fiscal year, and reports the
    claims whose delta from the stored value exceeds MAGNITUDE_TOLERANCE_PCT.

    A row here is a CANDIDATE, not a defect. The detector's own restrictions
    are reproduced exactly, but the scan deliberately widens one thing the
    live path narrows -- see SCOPING below -- so a candidate needs reading
    before it is called a contradiction.

EVERY PREDICATE IS THE IMPORTED ONE
    _is_claim_eligible, _metric_alias_pattern, extract_numeric_claims and
    MAGNITUDE_TOLERANCE_PCT are imported from app.engines.contradiction and
    called, never reimplemented. A hand-written mirror agrees on the day it is
    written and diverges at the first change to either side; the whole value of
    a sweep like this is that it reports what the real detector would do.
    The delta formula is copied verbatim from
    detect_magnitude_contradictions, including its sql_value None/0 guard.

SCOPING, STATED NOT HIDDEN
    Stored rows are selected by (company, fiscal_year) only, per the scan's
    brief -- NOT by quarter or financial_type. One narrative figure is
    therefore compared against every quarter/statement-type instance of that
    metric in the same year, so a single claim can raise several candidates,
    at most one of which can be the intended comparison. Each row carries the
    stored quarter and financial_type so that noise is attributable rather
    than mysterious. This is wider than the live detector, which compares
    against the one value its DSL query returned.

FAST PATH, SEMANTICS-PRESERVING
    A chunk with no crore-denominated figure at all cannot produce an anchored
    claim, because anchoring only ever REMOVES figures. So each chunk is first
    passed through the real extract_numeric_claims with no anchor, and skipped
    entirely when that returns nothing. This changes runtime, not results.
"""

import argparse
import json
import logging
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime

import psycopg2
from qdrant_client.models import FieldCondition, Filter, MatchValue

from app.engines.contradiction import (
    MAGNITUDE_TOLERANCE_PCT,
    _is_claim_eligible,
    _is_narrative,
    _metric_alias_pattern,
    _speaker_permits_claim,
    extract_numeric_claims,
)
from app.ingestion.qdrant_writer import _get_client, COLLECTION_NAME

ALPHA_TENANT = "a0000000-0000-0000-0000-000000000001"
LOCAL_DSN = "postgresql://ledgermind_app:app_dev_pass@postgres:5432/ledgermind"
DEFAULT_OUT_DIR = "/app/measurements"


# ---------------------------------------------------------------------------
# Relational side — READ ONLY, under explicit RLS scoping
# ---------------------------------------------------------------------------
def load_financials(database: str, tenant_id: str) -> dict:
    """
    (company, fiscal_year) -> [ {metric, value, quarter, financial_type}, ... ]

    is_latest = TRUE only: a retired row is by definition not what the system
    would answer with, so a claim disagreeing with one is not a contradiction.

    ledgermind_app does NOT bypass RLS. Without SET app.tenant_id this returns
    zero rows, which is indistinguishable from an empty table — so the scoping
    is printed, read back, and a zero-row result is a hard abort rather than a
    quietly empty scan.
    """
    if database == "local":
        dsn, label = LOCAL_DSN, "LOCAL DOCKER"
    else:
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            sys.exit("ABORT: --database supabase but DATABASE_URL is unset.")
        label = "SUPABASE (DATABASE_URL)"

    print(f"financials read from : {label}  {dsn.split('@')[-1][:60]}")
    print(f"RLS scoping          : SET app.tenant_id = {tenant_id}")

    conn = psycopg2.connect(dsn)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SET app.tenant_id = %s", (tenant_id,))
            cur.execute("SELECT current_setting('app.tenant_id', true)")
            confirmed = cur.fetchone()[0]
            if confirmed != tenant_id:
                sys.exit(f"ABORT: app.tenant_id reads back as {confirmed!r}.")
            print(f"scoping confirmed    : current_setting -> {confirmed}")
            cur.execute(
                """SELECT company, fiscal_year, quarter, financial_type,
                          metric, value
                   FROM financials
                   WHERE is_latest = TRUE
                   ORDER BY company, fiscal_year, metric"""
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        sys.exit("ABORT: zero is_latest financials rows. Under RLS that is a "
                 "scoping failure, not an empty table.")

    by_key = defaultdict(list)
    for company, fy, quarter, ftype, metric, value in rows:
        by_key[(company, fy)].append({
            "metric": metric,
            "value": float(value) if value is not None else None,
            "quarter": quarter,
            "financial_type": ftype,
        })
    print(f"is_latest rows       : {len(rows)} across {len(by_key)} company/fiscal_year keys")
    return dict(by_key)


# ---------------------------------------------------------------------------
# Qdrant side — READ ONLY
# ---------------------------------------------------------------------------
def scroll_chunks(client, collection: str, tenant_id: str) -> list:
    """Every point for this tenant, as ChunkResult-shaped dicts."""
    flt = Filter(must=[FieldCondition(key="tenant_id",
                                      match=MatchValue(value=tenant_id))])
    chunks = []
    nxt = None
    while True:
        points, nxt = client.scroll(
            collection_name=collection, limit=500, offset=nxt,
            scroll_filter=flt, with_payload=True, with_vectors=False,
        )
        for p in points:
            pl = p.payload or {}
            # ChunkResult is a TypedDict — plain dict access, never getattr.
            chunks.append({
                "chunk_id": str(p.id),
                "doc_id": pl.get("doc_id"),
                "text": pl.get("text") or "",
                "page_number": pl.get("page_number"),
                "company": pl.get("company"),
                "fiscal_year": pl.get("fiscal_year"),
                "quarter": pl.get("quarter"),
                "financial_type": pl.get("financial_type"),
                "chunk_type": pl.get("chunk_type"),
                "speaker_role": pl.get("speaker_role"),
                "filing_date": pl.get("filing_date"),
            })
        if nxt is None:
            break
    print(f"qdrant collection    : {collection}")
    print(f"chunks scrolled      : {len(chunks)} under tenant filter")
    return chunks


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(
        description="Sweep every chunk for magnitude-contradiction candidates")
    ap.add_argument("--collection", default=COLLECTION_NAME)
    ap.add_argument("--tenant", default=ALPHA_TENANT)
    ap.add_argument("--database", choices=("local", "supabase"), default="local")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    args = ap.parse_args()

    print(f"\n{'=' * 72}")
    print("magnitude_candidate_scan — READ ONLY, zero LLM calls")
    print(f"{'=' * 72}")
    print(f"tolerance            : MAGNITUDE_TOLERANCE_PCT = {MAGNITUDE_TOLERANCE_PCT}")

    financials = load_financials(args.database, args.tenant)
    client = _get_client()
    chunks = scroll_chunks(client, args.collection, args.tenant)

    # _metric_alias_pattern WARNs once per unregistered metric. 223 distinct
    # metrics against a registry that does not know all of them would bury the
    # report in repeated warnings, so the module logger is quietened and the
    # unanchorable names are COUNTED and REPORTED instead — the information is
    # kept, not discarded.
    logging.getLogger("app.engines.contradiction").setLevel(logging.ERROR)

    anchors = {}          # metric -> compiled pattern or None
    unanchorable = set()

    def anchor_for(metric: str):
        if metric not in anchors:
            pat = _metric_alias_pattern(metric)
            anchors[metric] = pat
            if pat is None:
                unanchorable.add(metric)
        return anchors[metric]

    stats = Counter()
    skipped_reasons = Counter()
    candidates = []
    no_financials_keys = Counter()

    for chunk in chunks:
        stats["chunks_total"] += 1

        if not _is_claim_eligible(chunk):
            stats["chunks_not_claim_eligible"] += 1
            # Attribution only — the gate above is the imported predicate.
            if not _is_narrative(chunk):
                skipped_reasons["non_narrative_chunk_type"] += 1
            elif not _speaker_permits_claim(chunk):
                skipped_reasons["non_claimant_speaker_role"] += 1
            continue
        stats["chunks_claim_eligible"] += 1

        # Fast path: no crore figure at all -> no anchored claim is possible.
        if not extract_numeric_claims(chunk["text"]):
            stats["chunks_no_crore_figure"] += 1
            continue
        stats["chunks_with_crore_figure"] += 1

        key = (chunk["company"], chunk["fiscal_year"])
        rows = financials.get(key)
        if not rows:
            stats["chunks_no_financials_for_key"] += 1
            no_financials_keys[str(key)] += 1
            continue
        stats["chunks_compared"] += 1

        for row in rows:
            stored = row["value"]
            # Verbatim from detect_magnitude_contradictions.
            if stored is None or stored == 0:
                stats["stored_rows_skipped_none_or_zero"] += 1
                continue

            pattern = anchor_for(row["metric"])
            if pattern is None:
                stats["stored_rows_unanchorable"] += 1
                continue

            claims = extract_numeric_claims(chunk["text"], anchor=pattern)
            for claim in claims:
                stats["anchored_claims"] += 1
                delta_pct = (claim - stored) / abs(stored) * 100
                if abs(delta_pct) <= MAGNITUDE_TOLERANCE_PCT:
                    stats["claims_within_tolerance"] += 1
                    continue
                stats["claims_over_tolerance"] += 1
                candidates.append({
                    "chunk_id": chunk["chunk_id"],
                    "doc_id": chunk["doc_id"],
                    "page_number": chunk["page_number"],
                    "speaker_role": chunk["speaker_role"],
                    "chunk_type": chunk["chunk_type"],
                    "company": chunk["company"],
                    "fiscal_year": chunk["fiscal_year"],
                    "metric": row["metric"],
                    "claim_value": claim,
                    "stored_value": stored,
                    "delta_pct": round(delta_pct, 2),
                    "stored_quarter": row["quarter"],
                    "stored_financial_type": row["financial_type"],
                    "chunk_quarter": chunk["quarter"],
                    "chunk_financial_type": chunk["financial_type"],
                    "text_excerpt": chunk["text"][:200].strip(),
                })

    candidates.sort(key=lambda c: -abs(c["delta_pct"]))

    os.makedirs(args.out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(args.out_dir, f"magnitude_candidate_scan_{stamp}.json")
    with open(out_path, "w") as f:
        json.dump({
            "meta": {
                "run_at": datetime.now().isoformat(timespec="seconds"),
                "collection": args.collection,
                "database": args.database,
                "tenant_id": args.tenant,
                "magnitude_tolerance_pct": MAGNITUDE_TOLERANCE_PCT,
                "scoping": "stored rows matched on (company, fiscal_year) only — "
                           "NOT quarter or financial_type; wider than the live "
                           "detector, so one claim can raise several candidates",
                "llm_calls": 0,
            },
            "stats": dict(stats),
            "skipped_reasons": dict(skipped_reasons),
            "unanchorable_metrics": sorted(unanchorable),
            "candidates": candidates,
        }, f, indent=2)

    print(f"\n{'-' * 72}")
    for k in ("chunks_total", "chunks_not_claim_eligible", "chunks_claim_eligible",
              "chunks_no_crore_figure", "chunks_with_crore_figure",
              "chunks_no_financials_for_key", "chunks_compared",
              "anchored_claims", "claims_within_tolerance", "claims_over_tolerance"):
        print(f"  {k:<34} {stats[k]}")
    print(f"  {'skipped: non-narrative type':<34} {skipped_reasons['non_narrative_chunk_type']}")
    print(f"  {'skipped: non-claimant speaker':<34} {skipped_reasons['non_claimant_speaker_role']}")
    print(f"  {'metrics with no registry anchor':<34} {len(unanchorable)}")
    if no_financials_keys:
        print(f"\n  chunk keys with no is_latest financials:")
        for k, n in no_financials_keys.most_common():
            print(f"    {k}  {n} chunks")

    print(f"\n{'=' * 72}")
    print(f"CANDIDATES over {MAGNITUDE_TOLERANCE_PCT}% : {len(candidates)}")
    print("A candidate is a claim to READ, not a defect. See SCOPING in the docstring.")
    print(f"{'=' * 72}")
    for c in candidates[:40]:
        print(f"\n  {c['chunk_id']}  p{c['page_number']}  "
              f"{c['speaker_role']}/{c['chunk_type']}  {c['company']} {c['fiscal_year']}")
        print(f"    {c['metric']}: claim {c['claim_value']:,.1f} vs stored "
              f"{c['stored_value']:,.1f} ({c['stored_financial_type']}, "
              f"q={c['stored_quarter']})  delta {c['delta_pct']:+.2f}%")
        print(f"    {c['text_excerpt'][:110]!r}")
    if len(candidates) > 40:
        print(f"\n  ... {len(candidates) - 40} more in the JSON")

    print(f"\nWritten to {out_path}")


if __name__ == "__main__":
    main()
