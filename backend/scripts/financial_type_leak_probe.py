"""
LedgerMind — financial_type leak probe
======================================
Run: docker compose exec -T -e PYTHONPATH=/app backend \
       python -m scripts.financial_type_leak_probe

ONE QUESTION: does the standalone/consolidated filter change what a semantic
query retrieves?

WHY IT MIGHT NOT. Measured 2026-08-09: financial_type is "unknown" on 2496 of
2531 chunks corpus-wide. All 35 typed chunks are chunk_type=FINANCIAL_STATEMENT
(18 consolidated / 17 standalone); every TEXT, TABLE, MANAGEMENT_DISCUSSION and
RISK_DISCLOSURE chunk is "unknown". _build_filter ORs "unknown" into the
should-clause, so the two filter values differ over 35 chunks and agree on the
other 2496. For narrative queries the pools should therefore be IDENTICAL --
that is the prediction, recorded before measuring so it can be disconfirmed.

WHAT A LEAK IS. One PDF registers as TWO documents, split by financial_type
(register_sections + section_checksum), so ETERNAL FY24 is d662a604
(consolidated) and ebaf1089 (standalone). A chunk from ebaf1089 surviving into
the final five of a consolidated query is narrative retrieved from the wrong
statement set. The chunk's OWN financial_type says "unknown" and carries no
signal -- doc_id is the only thing that distinguishes them.

DOC MAP IS HARDCODED, DELIBERATELY. A probe that re-derives its reference from
the database it is measuring cannot disagree with it. These 9 rows were read
from documents under tenant scoping on 2026-08-09 and match the Qdrant scroll
exactly.

TRANSCRIPT COUNTED SEPARATELY. 1d8061a3 is labelled consolidated in documents,
but an earnings call has no statement type; the label is load-bearing only
because financial_type is inside derive_doc_id's hashed input. Folding it into
MATCH or OPPOSITE would answer by assumption.

METHOD follows cohere_score_dump.py: hybrid_search() is the real function,
Cohere is called directly rather than through rerank() (which slices to top-5
before returning), and dedup uses the REAL imported _deduplicate_near_identical
so the measured slice is the one a user sees.

LIMITATION, STATED NOT HIDDEN: fiscal_year=None on every query -- the widest
honest pool. The live pipeline resolves a fiscal year through the entity
resolver; calling it here would drag in the router. This measures retrieval
under the widest filter, not the exact pipeline pool.

Zero Gemini calls. 2 Cohere searches per question; Cohere bills per SEARCH.
"""

import json
import os
import sys
from collections import Counter
from datetime import datetime

from app.engines.retriever import (
    TOP_K_RERANK,
    TOP_K_RETRIEVAL,
    _deduplicate_near_identical,
    _get_cohere_client,
    hybrid_search,
)

TENANT_ID = "a0000000-0000-0000-0000-000000000001"
DATASET = "/app/golden_dataset/q4fy26_eternal.json"
CATEGORIES = ("semantic_", "cross_examination")
OUT = "/app/measurements/financial_type_leak_probe.json"

# doc_id -> (company, fiscal_year, doc_type, financial_type)
DOC_MAP = {
    "d662a604-2f8c-549c-9374-06400875e04d": ("ETERNAL", "FY24", "annual_report",      "consolidated"),
    "ebaf1089-031d-5605-8090-846308d68dc7": ("ETERNAL", "FY24", "annual_report",      "standalone"),
    "27091929-f1d5-5c8d-897c-3d6437963418": ("ETERNAL", "FY26", "quarterly_result",   "consolidated"),
    "e33b7e55-0b7b-5e38-9948-afb76e3df2dc": ("ETERNAL", "FY26", "quarterly_result",   "standalone"),
    "1d8061a3-cb75-5524-a897-48a7baa81a1a": ("ETERNAL", "FY26", "earnings_transcript", "consolidated"),
    "352e249b-ca7e-508d-9a9d-377d4fe7c48c": ("PAYTM",   "FY26", "annual_report",      "consolidated"),
    "bbf75eac-eaa6-506f-b92b-154423882f8d": ("PAYTM",   "FY26", "annual_report",      "standalone"),
    "6a07229b-7084-59e4-a7be-86cf7de8d94e": ("TITAN",   "FY26", "quarterly_result",   "consolidated"),
    "14b698c0-b6e4-58e6-89e2-e0c0e9844edf": ("TITAN",   "FY26", "quarterly_result",   "standalone"),
}
TRANSCRIPT_DOC_ID = "1d8061a3-cb75-5524-a897-48a7baa81a1a"


def classify(doc_id, requested):
    if doc_id == TRANSCRIPT_DOC_ID:
        return "TRANSCRIPT"
    row = DOC_MAP.get(doc_id)
    if row is None:
        return "UNMAPPED"
    return "MATCH" if row[3] == requested else "OPPOSITE"


def score_pool(client, query, company, financial_type):
    """hybrid_search -> Cohere -> dedup -> top-K. Returns list of chunks."""
    candidates = hybrid_search(
        query=query, tenant_id=TENANT_ID, company=company,
        fiscal_year=None, quarter=None, financial_type=financial_type,
    )
    if not candidates:
        return []

    # GATE 1 -- pool must arrive UNRANKED. hybrid_search builds every
    # ChunkResult with reranker_backend="none"; a pre-scored pool would mean
    # local ONNX logits mixed into a Cohere measurement.
    stale = {c.get("reranker_backend") for c in candidates} - {"none", None}
    if stale:
        sys.exit(f"ABORT: candidate pool arrived pre-ranked by {stale}.")

    texts = [c["text"] for c in candidates]
    resp = client.rerank(model="rerank-english-v3.0", query=query,
                         documents=texts, top_n=len(texts))

    # GATE 2 -- response must cover the pool and sit in Cohere's [0,1].
    # Unbounded/negative scores are ONNX logits, not Cohere probabilities.
    if len(resp.results) != len(texts):
        sys.exit(f"ABORT: rerank returned {len(resp.results)} for {len(texts)} docs.")
    bad = [h.relevance_score for h in resp.results
           if not (0.0 <= float(h.relevance_score) <= 1.0)]
    if bad:
        sys.exit(f"ABORT: scores outside [0,1]: {bad[:3]} -- ONNX logits, not Cohere.")

    scored = []
    for hit in resp.results:
        c = dict(candidates[hit.index])
        c["reranker_score"] = float(hit.relevance_score)
        c["reranker_backend"] = "cohere"
        scored.append(c)
    scored.sort(key=lambda c: c["reranker_score"], reverse=True)
    return _deduplicate_near_identical(scored)[:TOP_K_RERANK]


def main():
    if not os.getenv("COHERE_API_KEY"):
        sys.exit("ABORT: COHERE_API_KEY not set. This measures COHERE.")
    client = _get_cohere_client()
    if client is None:
        sys.exit("ABORT: Cohere client failed to initialise.")

    with open(DATASET) as f:
        golden = json.load(f)
    qs = [q for q in golden
          if q["category"].startswith(CATEGORIES[0]) or q["category"] == CATEGORIES[1]]
    print(f"{len(qs)} reranked questions from {os.path.basename(DATASET)}\n")

    report = {"meta": {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "tenant_id": TENANT_ID, "dataset": DATASET,
        "retrieval_top_k": TOP_K_RETRIEVAL, "rerank_top_k": TOP_K_RERANK,
        "note": "fiscal_year=None -- widest honest pool, not the live pipeline pool.",
    }, "questions": []}

    identical = 0
    tally = Counter()

    for i, g in enumerate(qs, 1):
        q = g["question"]
        company = g.get("expected_company") or "ETERNAL"
        want = g.get("expected_financial_type") or "consolidated"
        print(f"[{i}/{len(qs)}] {g['id']} ({g['category']})")
        print(f"  {q[:70]}")

        con = score_pool(client, q, company, "consolidated")
        sta = score_pool(client, q, company, "standalone")

        con_ids = [c["doc_id"] for c in con]
        sta_ids = [c["doc_id"] for c in sta]
        same = con_ids == sta_ids
        identical += same

        marks = [classify(d, want) for d in con_ids]
        tally.update(marks)

        print(f"  consolidated top-{len(con)}: {Counter(marks)}")
        print(f"  vs standalone run: {'IDENTICAL' if same else '*** DIFFERENT ***'}")
        if not same:
            print(f"    con: {[d[:8] for d in con_ids]}")
            print(f"    sta: {[d[:8] for d in sta_ids]}")

        report["questions"].append({
            "id": g["id"], "category": g["category"], "question": q,
            "identical_across_filter": same,
            "requested_financial_type": want,
            "consolidated": [{"doc_id": c["doc_id"], "verdict": classify(c["doc_id"], want),
                              "page": c["page_number"], "chunk_type": c["chunk_type"],
                              "score": round(c["reranker_score"], 4)} for c in con],
            "standalone_doc_ids": sta_ids,
        })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n{'='*60}")
    print(f"Identical across filter values: {identical}/{len(qs)}")
    print(f"Consolidated-run citation verdicts: {dict(tally)}")
    print(f"Written to {OUT}")


if __name__ == "__main__":
    main()
