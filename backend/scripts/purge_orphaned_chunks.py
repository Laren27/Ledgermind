"""
Purge stale-cut orphan chunks from Qdrant.

WHY THIS EXISTS
----------------
A Qdrant point id IS the chunk_id, and chunk_id is
md5(doc_id:page_number:position:text[:100]) -- see chunker._make_chunk_id.
That makes re-ingestion idempotent as long as the CUTS land in the same
places: identical boundaries produce identical ids and the upsert overwrites
cleanly.

Change anything that moves a boundary -- OVERLAP_TOKENS, CHUNK_SIZE, the
separator list, a parser fix that alters block text -- and the same page
produces DIFFERENT chunk_ids. The new points are inserted; the old ones are
never addressed, so they persist under the SAME doc_id, still carrying
is_latest=TRUE and a correct-looking payload. They are invisible to every
metadata filter, because nothing about their metadata is wrong. Only their
TEXT is stale, and a semantic query will happily retrieve and cite it.

This is the chunk-side twin of purge_orphaned_metrics.py, and the same
argument applies: retirement is keyed on identity by design, and orphans are a
maintenance obligation of any change that moves a cut, not a defect in the
write path.

WHY A DELETE-THEN-INGEST RE-INGEST DOES NOT PRODUCE THEM
---------------------------------------------------------
The 2026-08-09 re-ingest deleted each doc_id's points before writing, so it
reproduced every chunk count exactly and left this class EMPTY. That is
exactly why a first run reporting 0 proves nothing on its own -- 0 found and
"the filter never matched" are the same output. So this script reports
FOUND vs EXPECTED, never "ran clean", and was built against a deliberately
manufactured orphan (TITAN, re-chunked at OVERLAP_TOKENS=40 into a scratch
collection without deleting: 24 chunks then 21, 18 shared, 6 orphans found
and 6 expected).

METHOD
-------
Re-parses each reference PDF, re-chunks it with the CURRENT code, and treats
the resulting chunk_ids as the truth. Any point in the collection whose id is
absent from the freshly-produced set, under a doc_id the reference documents
actually produce, is a stale cut.

SCOPE GUARD (the part that must not be removed)
------------------------------------------------
A point is only a candidate if its doc_id was produced by one of the reference
PDFs. A doc_id NO reference document produces is reported separately and never
deleted: that means the source PDF is absent from docs/raw or missing from
regression_check.DOCUMENTS, NOT that its chunks are stale. Deleting on a
partial produced-set would destroy every chunk of the absent document. Same
conservatism as purge_orphaned_metrics.py: when the rule cannot decide, report
and keep.

Usage:
  docker compose exec -T -e PYTHONPATH=/app backend \
    python -m scripts.purge_orphaned_chunks --collection scratch_xyz
  ... --execute        (required to delete; dry run is the default)
"""

import argparse
import sys
import uuid
from collections import defaultdict

from app.ingestion.pdf_parser import parse_pdf
from app.ingestion.document_classifier import (
    compute_pdf_checksum, derive_doc_id, detect_sections, section_checksum,
)
from app.ingestion.section_classifier import classify_blocks
from app.ingestion.chunker import chunk_blocks, OVERLAP_TOKENS
from app.ingestion.qdrant_writer import _get_client, COLLECTION_NAME

from scripts.regression_check import DOCUMENTS, RAW_DIR, ALPHA_TENANT

LOCAL_DSN = "postgresql://ledgermind_app:app_dev_pass@postgres:5432/ledgermind"

# The doc_ids that actually hold points in ledgermind_chunks. Measured
# 2026-08-09 (2531 points across exactly these 9). Hardcoded for the same
# reason financial_type_leak_probe.py hardcodes DOC_MAP: a coverage gate that
# derives its own expectation from the thing it is checking cannot fail.
EXPECTED_CORPUS_DOC_IDS = {
    "d662a604-2f8c-549c-9374-06400875e04d",  # ETERNAL FY24 AR   consolidated
    "ebaf1089-031d-5605-8090-846308d68dc7",  # ETERNAL FY24 AR   standalone
    "27091929-f1d5-5c8d-897c-3d6437963418",  # ETERNAL FY26 Q4   consolidated
    "e33b7e55-0b7b-5e38-9948-afb76e3df2dc",  # ETERNAL FY26 Q4   standalone
    "1d8061a3-cb75-5524-a897-48a7baa81a1a",  # ETERNAL FY26 earnings transcript
    "352e249b-ca7e-508d-9a9d-377d4fe7c48c",  # PAYTM   FY26 AR   consolidated
    "bbf75eac-eaa6-506f-b92b-154423882f8d",  # PAYTM   FY26 AR   standalone
    "6a07229b-7084-59e4-a7be-86cf7de8d94e",  # TITAN   FY26 Q1   consolidated
    "14b698c0-b6e4-58e6-89e2-e0c0e9844edf",  # TITAN   FY26 Q1   standalone
}


def roster_doc_ids() -> set:
    """
    Which corpus doc_ids the reference roster can produce AT ALL -- a property
    of regression_check.DOCUMENTS, deliberately independent of --only.

    Costs a sha256 per file and NOT a parse: doc_id is
    uuid5(NS, f"{file_sha256}_{financial_type}"), so both candidate ids per
    file are derivable without opening the PDF as a document. That matters --
    this runs on every invocation, and CLAUDE.md \u00a77 rations parses, not hashes.
    """
    ids = set()
    for doc in DOCUMENTS:
        path = RAW_DIR / doc["filename"]
        if not path.exists():
            continue
        sha = compute_pdf_checksum(str(path))
        for ftype in ("consolidated", "standalone"):
            ids.add(derive_doc_id(section_checksum(sha, ftype)))
    return ids & EXPECTED_CORPUS_DOC_IDS


# ---------------------------------------------------------------------------
# Produced-set: what the CURRENT code cuts out of the reference PDFs
# ---------------------------------------------------------------------------
def build_produced_chunk_ids(only: str | None = None) -> tuple[dict, dict]:
    """
    Returns (chunk_ids_by_doc_id, doc_meta_by_doc_id).

    register_sections() is deliberately NOT called: it WRITES to `documents`,
    and this script is a reader. doc_id is a pure function of the file
    checksum and the section's financial_type, so the ids are derived directly
    and land on exactly the ids a real ingest would use.

    Aborts if any reference PDF is missing. A partial produced-set would mark
    every chunk of the absent document as orphaned -- precisely the deletion
    this script must never make.
    """
    by_doc: dict = defaultdict(set)
    meta: dict = {}

    documents = DOCUMENTS
    if only:
        documents = [d for d in DOCUMENTS if only.lower() in d["filename"].lower()]
        if not documents:
            sys.exit(f"ABORT: --only {only!r} matched none of "
                     f"{[d['filename'] for d in DOCUMENTS]}")
        # NARROWING THE PRODUCED-SET IS SAFE, and only because of the scope
        # guard: every doc_id the remaining PDFs do not produce falls into NOT
        # EVALUATED, which is reported and never deleted. Without that guard
        # this flag would be a corpus-deletion switch.
        print(f"  --only {only!r}: {len(documents)}/{len(DOCUMENTS)} reference "
              f"documents; all other doc_ids will report as NOT EVALUATED")

    for doc in documents:
        pdf_path = RAW_DIR / doc["filename"]
        if not pdf_path.exists():
            print(f"ABORT: source PDF missing: {pdf_path}")
            print("A partial produced-set would orphan every chunk of this document.")
            sys.exit(1)

        print(f"  re-chunking {doc['filename']} ...", flush=True)
        blocks = parse_pdf(str(pdf_path))
        sections = detect_sections(blocks)
        blocks = classify_blocks(blocks, sections)

        sha = compute_pdf_checksum(str(pdf_path))
        for s in sections:
            s.doc_id = uuid.UUID(derive_doc_id(section_checksum(sha, s.financial_type)))
            meta[str(s.doc_id)] = {
                "company": doc["company"], "fiscal_year": doc["fiscal_year"],
                "financial_type": s.financial_type, "filename": doc["filename"],
            }

        chunks = chunk_blocks(
            blocks=blocks, sections=sections, tenant_id=ALPHA_TENANT,
            company=doc["company"], ticker=doc["ticker"],
            fiscal_year=doc["fiscal_year"], quarter=doc["quarter"],
            document_type=doc["doc_type"], filing_date=doc["filing_date"],
        )
        for c in chunks:
            by_doc[c.metadata.doc_id].add(c.chunk_id)
        print(f"    -> {len(chunks)} chunks across {len(sections)} sections", flush=True)

    return {str(k): v for k, v in by_doc.items()}, meta


# ---------------------------------------------------------------------------
# Live side
# ---------------------------------------------------------------------------
def scroll_live_points(client, collection: str) -> dict:
    """doc_id -> {point_id: payload}. Full scroll, no vectors."""
    live: dict = defaultdict(dict)
    total = 0
    nxt = None
    while True:
        points, nxt = client.scroll(
            collection_name=collection, limit=500, offset=nxt,
            with_payload=True, with_vectors=False,
        )
        for p in points:
            payload = p.payload or {}
            live[str(payload.get("doc_id"))][str(p.id)] = payload
            total += 1
        if nxt is None:
            break
    print(f"live points: {total} across {len(live)} doc_ids in '{collection}'")
    return dict(live)


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(
        description="Find (and optionally purge) stale-cut orphan chunks in Qdrant")
    ap.add_argument("--execute", action="store_true",
                    help="Actually delete. WITHOUT THIS NOTHING IS DELETED — dry run "
                         "is the default and never enters a delete function.")
    ap.add_argument("--collection", default=COLLECTION_NAME,
                    help=f"Qdrant collection to scan (default: {COLLECTION_NAME}).")
    ap.add_argument("--database", choices=("local", "supabase"), default="local",
                    help="Which relational side the run is associated with. This "
                         "script derives doc_ids from the PDFs and does not read "
                         "`documents`, but the side is STATED so a report cannot be "
                         "mistaken for one taken against the other.")
    ap.add_argument("--only", default=None,
                    help="Restrict the produced-set to reference PDFs whose filename "
                         "contains this substring. Every other doc_id then reports as "
                         "NOT EVALUATED (reported, never deleted). Exists because "
                         "CLAUDE.md \u00a77 warns that parsing the corpus twice exhausts "
                         "WSL RAM -- a scratch-collection run should not re-parse the "
                         "58MB annual report to check one document.")
    ap.add_argument("--expect-orphans", type=int, default=None,
                    help="Expected orphan count. Exits non-zero on mismatch. A run "
                         "that cannot say what it expected cannot tell 'none present' "
                         "from 'the filter never matched'.")
    args = ap.parse_args()

    mode = "EXECUTE" if args.execute else "DRY RUN"
    print(f"\n{'=' * 72}")
    print(f"purge_orphaned_chunks — {mode}")
    print(f"{'=' * 72}")
    print(f"collection        : {args.collection}")
    print(f"database side     : {args.database} "
          f"({LOCAL_DSN.split('@')[-1] if args.database == 'local' else 'DATABASE_URL'})")
    print(f"                    (doc_ids derived from PDF checksums; `documents` is "
          f"NOT read and NOT written)")
    print(f"chunker overlap   : OVERLAP_TOKENS={OVERLAP_TOKENS}")
    print(f"reference PDFs    : {len(DOCUMENTS)} from {RAW_DIR}"
          + (f"  (--only {args.only!r})" if args.only else "") + "\n")

    produced, meta = build_produced_chunk_ids(args.only)
    produced_total = sum(len(v) for v in produced.values())
    print(f"\nproduced: {produced_total} chunk_ids across {len(produced)} doc_ids\n")

    client = _get_client()
    if not client.collection_exists(args.collection):
        sys.exit(f"ABORT: collection '{args.collection}' does not exist.")
    live = scroll_live_points(client, args.collection)

    orphans: list = []
    unscoped: dict = defaultdict(list)
    matched = 0

    for doc_id, points in live.items():
        if doc_id not in produced:
            unscoped[doc_id].extend(points)
            continue
        current = produced[doc_id]
        for point_id, payload in points.items():
            if point_id in current:
                matched += 1
            else:
                orphans.append((doc_id, point_id, payload))

    if unscoped:
        print(f"\n{'=' * 72}")
        print("NOT EVALUATED — no reference document produces these doc_ids.")
        print("Reported, never deleted: this means a source PDF is absent from")
        print("docs/raw or missing from regression_check.DOCUMENTS, NOT that the")
        print("chunks are stale.")
        print("=" * 72)
        for doc_id, pts in sorted(unscoped.items()):
            print(f"  {doc_id}  ({len(pts)} points)")

    by_doc = defaultdict(list)
    for doc_id, point_id, payload in orphans:
        by_doc[doc_id].append((point_id, payload))

    print(f"\n{'=' * 72}")
    print(f"ORPHANED: {len(orphans)} points whose chunk boundaries the current "
          f"code no longer produces")
    print("=" * 72)
    for doc_id, rows in sorted(by_doc.items(), key=lambda kv: -len(kv[1])):
        m = meta.get(doc_id, {})
        print(f"\n{doc_id}  {m.get('company', '?')} {m.get('fiscal_year', '?')} "
              f"{m.get('financial_type', '?')}  — {len(rows)} orphaned of "
              f"{len(live[doc_id])} live ({len(produced[doc_id])} produced now)")
        for point_id, payload in sorted(rows):
            text = (payload.get("text") or "").replace("\n", " ")
            print(f"  {point_id}  p{payload.get('page_number')} "
                  f"{str(payload.get('chunk_type'))[:22]:<22} {text[:56]!r}")

    not_evaluated_points = sum(len(v) for v in unscoped.values())

    print(f"\n{'-' * 72}")
    print(f"live points       : {sum(len(v) for v in live.values())}")
    print(f"matched current   : {matched}")
    print(f"orphaned          : {len(orphans)}")
    print(f"not evaluated     : {not_evaluated_points} points across "
          f"{len(unscoped)} doc_ids")

    # ── COVERAGE GATE ────────────────────────────────────────────────────
    # Two different coverages, and conflating them would break one of them.
    #
    # ROSTER coverage is a property of regression_check.DOCUMENTS: how much of
    # the corpus the reference list can produce at all. It is reported on every
    # run and is INDEPENDENT of --only, so narrowing a run never flatters it.
    #
    # SCAN coverage is the operative gate: whether this run's produced-set saw
    # every doc_id whose points it just counted. That is what makes an orphan
    # count a measurement -- a detector that has not seen a document has no
    # opinion about it, so a number computed while some of the scanned
    # collection went unevaluated is not one.
    roster = roster_doc_ids()
    missing_roster = sorted(EXPECTED_CORPUS_DOC_IDS - roster)
    print(f"\nroster coverage   : {len(roster)} of {len(EXPECTED_CORPUS_DOC_IDS)} "
          f"expected corpus doc_ids produced by "
          f"{len(DOCUMENTS)} reference documents")
    for doc_id in missing_roster:
        print(f"  UNCOVERED BY ROSTER: {doc_id} — no reference document "
              f"produces it; its points can only ever report NOT EVALUATED")
    print(f"scan coverage     : {len(live) - len(unscoped)} of {len(live)} "
          f"scanned doc_ids evaluated")

    # FOUND vs EXPECTED. A bare "0 found" cannot distinguish an empty orphan
    # class from a detector whose comparison never matched anything.
    if args.expect_orphans is not None and unscoped:
        print(f"\nABORT: --expect-orphans was passed, but {not_evaluated_points} "
              f"points across {len(unscoped)} doc_ids went unevaluated.")
        for doc_id in sorted(unscoped):
            note = " (uncovered by the reference roster)" if doc_id in missing_roster else ""
            print(f"  {doc_id}  {len(unscoped[doc_id])} points{note}")
        sys.exit("An orphan count from a partial produced-set is not a "
                 "measurement. Cover every scanned doc_id, or scan a collection "
                 "the roster covers.")

    if args.expect_orphans is not None:
        verdict = "MATCH" if len(orphans) == args.expect_orphans else "MISMATCH"
        print(f"\nfound {len(orphans)} / expected {args.expect_orphans} — {verdict}")
        if verdict == "MISMATCH":
            sys.exit("ABORT: the detector's notion of 'orphan' does not agree with "
                     "the expected count. Do not delete on a detector that cannot "
                     "reproduce a known answer.")
    else:
        print(f"\nfound {len(orphans)} orphans; no --expect-orphans given, so this "
              f"run asserts NOTHING about whether that number is right.")

    if not orphans:
        print("\nNothing to purge.")
        return

    if not args.execute:
        print(f"\nDRY RUN — nothing deleted. Re-run with --execute to remove "
              f"these {len(orphans)} points.")
        return

    print(f"\nEXECUTING against {args.collection} — one document at a time")
    for doc_id, rows in sorted(by_doc.items()):
        ids = [r[0] for r in rows]
        print(f"  {doc_id[:8]}: deleting {len(ids)} points")
        client.delete(collection_name=args.collection,
                      points_selector=ids, wait=True)
    after = client.get_collection(args.collection).points_count
    print(f"\ncollection now holds {after} points")


if __name__ == "__main__":
    main()
