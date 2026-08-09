"""
LedgerMind — propagate documents.financial_type onto Qdrant chunk payloads
==========================================================================
Run (DRY RUN, the default):
    docker compose exec -T -e PYTHONPATH=/app backend \
      python -m scripts.propagate_financial_type

THIS IS A RE-BASELINE. It changes what every filtered semantic query retrieves.
Fire it on a full-quota day with an eval sweep budgeted behind it, never as a
drive-by fix.

WHAT IT DOES
    2496 of 2531 chunk payloads carry financial_type="unknown". Only the 35
    chunk_type=FINANCIAL_STATEMENT chunks were ever typed. _build_filter ORs
    "unknown" into its should-clause, so an "unknown" chunk is retrievable under
    BOTH filter values -- which is why 23 of 85 citations (27%) on Eternal's 17
    reranked questions came from the OPPOSITE statement set (measured
    2026-08-09, scripts/financial_type_leak_probe.py). This script stamps each
    document's financial_type from `documents` onto its own chunks, so the
    filter can discriminate.

WHAT IT DOES NOT DO
    It does not touch `documents`. financial_type is inside derive_doc_id's
    hashed input (register_sections -> section_checksum), so changing a label
    there is a RE-KEY, not an UPDATE -- it would orphan every chunk under the
    old doc_id. Reads only, on the relational side.

    It does not touch retrieval. _build_filter keeps its "unknown" OR; that
    clause is what keeps the transcript (below) reachable.

THE TRANSCRIPT IS SKIPPED BY EXPLICIT DOC_ID
    1d8061a3 is an earnings call. Its `documents` row is labelled
    "consolidated" because financial_type is a NOT NULL component of the
    document key, not because anyone judged the call to be consolidated -- an
    earnings call has no statement type at all. Stamping "consolidated" onto its
    124 chunks would turn a key-shaped artefact into a load-bearing retrieval
    fact. Left at "unknown", those chunks stay reachable under BOTH filter
    values via the _build_filter OR, which is the correct behaviour for a
    transcript.

    Skipped by ID, deliberately, and NOT by a "skip anything unknown" rule:
    such a rule would silently also skip whatever else is unknown for unrelated
    reasons, which is the entire population this script exists to fix.

EXPECTED_POINTS IS HARDCODED, DELIBERATELY
    Same reasoning as financial_type_leak_probe.py's DOC_MAP: a script that
    re-derives its reference from the collection it is about to mutate cannot
    disagree with that collection. These are the 2026-08-09 post-re-ingest
    figures. If the collection has drifted, every count assertion fires and
    nothing is written.
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime

from qdrant_client.models import FieldCondition, Filter, MatchValue

from app.ingestion.qdrant_writer import _get_client, COLLECTION_NAME

TENANT_ID = "a0000000-0000-0000-0000-000000000001"

LOCAL_DSN = "postgresql://ledgermind_app:app_dev_pass@postgres:5432/ledgermind"

# Earnings call. See module docstring. Skipped by ID, with a printed reason.
TRANSCRIPT_DOC_ID = "1d8061a3-cb75-5524-a897-48a7baa81a1a"

# doc_id -> expected point count. Measured 2026-08-09 against ledgermind_chunks
# after the delete-then-ingest re-ingest. Total 2531 across 9 doc_ids.
EXPECTED_POINTS = {
    "d662a604-2f8c-549c-9374-06400875e04d": 1620,  # ETERNAL FY24 AR   consolidated
    "ebaf1089-031d-5605-8090-846308d68dc7":  379,  # ETERNAL FY24 AR   standalone
    "27091929-f1d5-5c8d-897c-3d6437963418":  236,  # ETERNAL FY26 Q4   consolidated
    "e33b7e55-0b7b-5e38-9948-afb76e3df2dc":   33,  # ETERNAL FY26 Q4   standalone
    "1d8061a3-cb75-5524-a897-48a7baa81a1a":  124,  # ETERNAL FY26 transcript (SKIPPED)
    "352e249b-ca7e-508d-9a9d-377d4fe7c48c":   76,  # PAYTM   FY26 AR   consolidated
    "bbf75eac-eaa6-506f-b92b-154423882f8d":   39,  # PAYTM   FY26 AR   standalone
    "6a07229b-7084-59e4-a7be-86cf7de8d94e":   10,  # TITAN   FY26 Q1   consolidated
    "14b698c0-b6e4-58e6-89e2-e0c0e9844edf":   14,  # TITAN   FY26 Q1   standalone
}
EXPECTED_TOTAL = 2531
EXPECTED_UNKNOWN = 2496

SET_PAYLOAD_BATCH = 200


# ---------------------------------------------------------------------------
# Relational side -- READ ONLY, under explicit RLS scoping
# ---------------------------------------------------------------------------
def read_document_types(database: str) -> dict:
    """
    doc_id -> {company, fiscal_year, quarter, doc_type, financial_type}.

    ledgermind_app does NOT bypass RLS. Without SET app.tenant_id this returns
    zero rows, which is indistinguishable from an empty table -- so the scoping
    and the row count are both PRINTED, not assumed.
    """
    import psycopg2

    if database == "local":
        dsn, label = LOCAL_DSN, "LOCAL DOCKER"
    else:
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            sys.exit("ABORT: --database supabase but DATABASE_URL is unset.")
        label = "SUPABASE (DATABASE_URL)"

    print(f"documents read from : {label}  {dsn.split('@')[-1][:60]}")
    print(f"RLS scoping         : SET app.tenant_id = {TENANT_ID}")

    conn = psycopg2.connect(dsn)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SET app.tenant_id = %s", (TENANT_ID,))
            cur.execute("SELECT current_setting('app.tenant_id', true)")
            confirmed = cur.fetchone()[0]
            if confirmed != TENANT_ID:
                sys.exit(f"ABORT: app.tenant_id reads back as {confirmed!r}.")
            print(f"scoping confirmed   : current_setting -> {confirmed}")

            cur.execute(
                """SELECT doc_id, company, fiscal_year, quarter, doc_type,
                          financial_type, ingestion_state
                   FROM documents ORDER BY company, fiscal_year, doc_type"""
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    print(f"documents rows      : {len(rows)} under that scoping")
    if not rows:
        sys.exit("ABORT: zero documents rows. Under RLS that is a scoping "
                 "failure, not an empty table.")

    out = {}
    for doc_id, company, fy, quarter, doc_type, ftype, state in rows:
        out[str(doc_id)] = {
            "company": company, "fiscal_year": fy, "quarter": quarter,
            "doc_type": doc_type, "financial_type": ftype,
            "ingestion_state": state,
        }
    return out


# ---------------------------------------------------------------------------
# Qdrant side -- READ ONLY
# ---------------------------------------------------------------------------
def scroll_payload_state(client, collection: str) -> dict:
    """doc_id -> {"ids_by_type": {financial_type: [point_id, ...]}}. Full scroll."""
    state = defaultdict(lambda: defaultdict(list))
    total = 0
    nxt = None
    while True:
        points, nxt = client.scroll(
            collection_name=collection, limit=500, offset=nxt,
            with_payload=True, with_vectors=False,
        )
        for p in points:
            payload = p.payload or {}
            state[payload.get("doc_id")][payload.get("financial_type")].append(p.id)
            total += 1
        if nxt is None:
            break
    print(f"qdrant collection   : {collection}")
    print(f"points scrolled     : {total} across {len(state)} doc_ids")
    return {d: dict(v) for d, v in state.items()}


def assert_counts(state: dict, collection: str) -> None:
    """
    Every count assertion, BEFORE any write is contemplated. A drifted
    collection must abort here rather than be half-stamped.
    """
    problems = []

    total = sum(len(ids) for t in state.values() for ids in t.values())
    if total != EXPECTED_TOTAL:
        problems.append(f"total points {total} != expected {EXPECTED_TOTAL}")
    if set(state) != set(EXPECTED_POINTS):
        missing = sorted(set(EXPECTED_POINTS) - set(state))
        extra = sorted(set(state) - set(EXPECTED_POINTS))
        if missing:
            problems.append(f"doc_ids in EXPECTED_POINTS but absent from {collection}: {missing}")
        if extra:
            problems.append(f"doc_ids present in {collection} but unexpected: {extra}")
    for doc_id, expected in EXPECTED_POINTS.items():
        actual = sum(len(ids) for ids in state.get(doc_id, {}).values())
        if actual != expected:
            problems.append(f"{doc_id[:8]} has {actual} points, expected {expected}")

    unknown = sum(len(t.get("unknown", [])) for t in state.values())
    if unknown != EXPECTED_UNKNOWN:
        problems.append(f'"unknown" payloads {unknown} != expected {EXPECTED_UNKNOWN}')

    if problems:
        print("\nCOUNT ASSERTIONS FAILED — nothing will be written:")
        for p in problems:
            print(f"  - {p}")
        sys.exit("ABORT: the collection is not the one these figures describe.")
    print(f"count assertions    : OK ({total} points, {len(state)} doc_ids, "
          f"{unknown} unknown)")


def assert_typed_payloads_agree(state: dict, docs: dict) -> None:
    """
    Any payload ALREADY typed must equal its document's financial_type. A
    disagreement means the payload carries a fact this script is about to
    overwrite the rest of the document with -- diagnose it, do not stamp over it.
    """
    problems = []
    for doc_id, by_type in state.items():
        expected = (docs.get(doc_id) or {}).get("financial_type")
        for ftype, ids in by_type.items():
            if ftype in (None, "unknown"):
                continue
            if ftype != expected:
                problems.append(f"{doc_id[:8]}: {len(ids)} payloads say {ftype!r}, "
                                f"documents says {expected!r}")
    if problems:
        print("\nTYPED-PAYLOAD DISAGREEMENT — nothing will be written:")
        for p in problems:
            print(f"  - {p}")
        sys.exit("ABORT: an already-typed payload contradicts documents.")
    print("typed payloads      : OK, all agree with documents.financial_type")


# ---------------------------------------------------------------------------
# Planning -- pure. Decides, writes nothing.
# ---------------------------------------------------------------------------
def plan_document(doc_id: str, by_type: dict, doc_row: dict) -> dict:
    """What a write WOULD do for one document. No client, no I/O."""
    current = {k if k is not None else "<null>": len(v) for k, v in by_type.items()}
    unknown_ids = list(by_type.get("unknown", []))

    if doc_id == TRANSCRIPT_DOC_ID:
        return {
            "doc_id": doc_id, "action": "skip",
            "reason": "earnings transcript — documents.financial_type is a "
                      "document-key artefact, not a statement type; leaving "
                      "payloads 'unknown' keeps it retrievable under BOTH "
                      "filter values via _build_filter's unknown OR",
            "target": None, "current": current, "would_change": 0,
            "resulting": dict(current), "point_ids": [],
        }
    if doc_row is None:
        return {"doc_id": doc_id, "action": "skip",
                "reason": "no documents row under this tenant scoping",
                "target": None, "current": current, "would_change": 0,
                "resulting": dict(current), "point_ids": []}

    target = doc_row["financial_type"]
    if target in (None, "unknown"):
        return {"doc_id": doc_id, "action": "skip",
                "reason": f"documents.financial_type is {target!r} — nothing to propagate",
                "target": target, "current": current, "would_change": 0,
                "resulting": dict(current), "point_ids": []}

    resulting = dict(current)
    if unknown_ids:
        resulting.pop("unknown", None)
        resulting[target] = resulting.get(target, 0) + len(unknown_ids)

    return {
        "doc_id": doc_id,
        "action": "propagate" if unknown_ids else "noop",
        "reason": "" if unknown_ids else "no 'unknown' payloads remain",
        "target": target, "current": current,
        "would_change": len(unknown_ids), "resulting": resulting,
        "point_ids": unknown_ids,
    }


# ---------------------------------------------------------------------------
# Rollback state -- written BEFORE any write path is entered, dry run included
# ---------------------------------------------------------------------------
def write_rollback(plans: list, docs: dict, collection: str,
                   database: str, executing: bool, out_dir: str) -> str:
    """
    Records the exact point ids that currently read "unknown", per document.
    Restoring is then a set_payload of {"financial_type": "unknown"} over those
    ids -- and ONLY those, so the 35 originally-typed chunks are never touched
    by a rollback either.
    """
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(out_dir, f"propagate_financial_type_rollback_{stamp}.json")
    doc = {
        "meta": {
            "written_at": datetime.now().isoformat(timespec="seconds"),
            "collection": collection, "database": database,
            "tenant_id": TENANT_ID,
            "mode": "execute" if executing else "dry-run",
            "restore": 'set_payload {"financial_type": "unknown"} over point_ids, per doc_id',
        },
        "documents": [
            {
                "doc_id": p["doc_id"],
                "company": (docs.get(p["doc_id"]) or {}).get("company"),
                "doc_type": (docs.get(p["doc_id"]) or {}).get("doc_type"),
                "action": p["action"],
                "target_financial_type": p["target"],
                "payload_distribution_before": p["current"],
                "point_ids_that_read_unknown": [str(i) for i in p["point_ids"]],
            }
            for p in plans
        ],
    }
    with open(path, "w") as f:
        json.dump(doc, f, indent=2)
    return path


# ---------------------------------------------------------------------------
# The ONLY write. Unreachable unless --execute put us in that branch.
# ---------------------------------------------------------------------------
def apply_document(client, collection: str, plan: dict, executing: bool) -> None:
    """
    One document, one target value, explicit point ids -- never a bulk filter
    across documents, so a wrong filter can misfile one document rather than
    the corpus.

    `executing` is re-asserted here even though main() only reaches this from
    the --execute branch: this function must be impossible to call by accident
    from a future edit.
    """
    if not executing:
        raise RuntimeError(
            "apply_document reached without --execute. This is a bug: the "
            "dry-run path must never enter a write function."
        )
    ids = plan["point_ids"]
    for i in range(0, len(ids), SET_PAYLOAD_BATCH):
        batch = ids[i:i + SET_PAYLOAD_BATCH]
        client.set_payload(
            collection_name=collection,
            payload={"financial_type": plan["target"]},
            points=batch,
            wait=True,
        )
        print(f"    wrote {len(batch)} points ({i + len(batch)}/{len(ids)})")


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(
        description="Propagate documents.financial_type onto Qdrant chunk payloads")
    ap.add_argument("--execute", action="store_true",
                    help="Actually write. WITHOUT THIS NOTHING IS WRITTEN — dry run "
                         "is the default and never enters a write function.")
    ap.add_argument("--database", choices=("local", "supabase"), default="local",
                    help="Which relational side to read documents from (default: local).")
    ap.add_argument("--collection", default=COLLECTION_NAME,
                    help=f"Qdrant collection (default: {COLLECTION_NAME}).")
    ap.add_argument("--rollback-dir", default="/app/measurements",
                    help="Where the rollback record is written (default: /app/measurements).")
    args = ap.parse_args()

    mode = "EXECUTE" if args.execute else "DRY RUN"
    print(f"\n{'=' * 72}")
    print(f"propagate_financial_type — {mode}")
    print(f"{'=' * 72}")

    docs = read_document_types(args.database)
    client = _get_client()
    state = scroll_payload_state(client, args.collection)

    assert_counts(state, args.collection)
    assert_typed_payloads_agree(state, docs)

    order = sorted(state, key=lambda d: -sum(len(v) for v in state[d].values()))
    plans = [plan_document(d, state[d], docs.get(d)) for d in order]

    rollback_path = write_rollback(plans, docs, args.collection, args.database,
                                   args.execute, args.rollback_dir)
    print(f"rollback record     : {rollback_path}")

    print(f"\n{'-' * 72}")
    for p in plans:
        row = docs.get(p["doc_id"]) or {}
        n = sum(len(v) for v in state[p["doc_id"]].values())
        print(f"\n{p['doc_id']}")
        print(f"  documents        : company={row.get('company')} "
              f"fy={row.get('fiscal_year')} q={row.get('quarter')} "
              f"doc_type={row.get('doc_type')} financial_type={row.get('financial_type')}")
        print(f"  points           : {n}")
        print(f"  payload now      : {p['current']}")
        print(f"  action           : {p['action'].upper()}"
              + (f" -> {p['target']}" if p["target"] and p["action"] == "propagate" else ""))
        if p["reason"]:
            print(f"  reason           : {p['reason']}")
        print(f"  would change     : {p['would_change']} points")
        print(f"  payload after    : {p['resulting']}")

    changing = sum(p["would_change"] for p in plans)
    skipped = sum(1 for p in plans if p["action"] == "skip")
    tally = Counter(p["action"] for p in plans)
    print(f"\n{'-' * 72}")
    print(f"documents          : {len(plans)}  {dict(tally)}")
    print(f"points that WOULD change: {changing}")
    print(f"points left 'unknown'   : "
          f"{sum(len(v.get('unknown', [])) for v in state.values()) - changing}"
          f"  (skipped documents: {skipped})")

    if not args.execute:
        print("\nDRY RUN — nothing was written. No write function was entered.")
        print("Re-run with --execute to apply. THIS IS A RE-BASELINE: it changes "
              "what every filtered semantic query retrieves.")
        return

    print(f"\nEXECUTING against {args.collection} — one document at a time")
    for p in plans:
        if p["action"] != "propagate":
            print(f"\n  {p['doc_id'][:8]} SKIP ({p['reason'] or p['action']})")
            continue
        print(f"\n  {p['doc_id'][:8]} -> {p['target']} ({p['would_change']} points)")
        apply_document(client, args.collection, p, args.execute)

    after = scroll_payload_state(client, args.collection)
    print("\npost-write payload distribution:")
    for d in order:
        print(f"  {d[:8]} {({k: len(v) for k, v in after.get(d, {}).items()})}")
    print(f"\nrollback record: {rollback_path}")


if __name__ == "__main__":
    main()
