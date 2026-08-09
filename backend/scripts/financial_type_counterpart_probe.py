"""
LedgerMind — does the correct-type document contain the content?
=================================================================
Run: docker compose exec -T -e PYTHONPATH=/app backend \
       python -m scripts.financial_type_counterpart_probe

FOLLOWS financial_type_leak_probe.py, which measured that 23 of 85 citations
on the 17 reranked ETERNAL questions come from the OPPOSITE statement set
(2026-08-09). That established the leak exists. It did NOT establish what the
correct behaviour is, and the fix depends entirely on that.

THE QUESTION. For a consolidated-scoped query whose evidence currently comes
from the standalone document: is the same content ALSO in the consolidated
document, ranked lower? Or is it only in the standalone one?

  - If also present -> the filter is picking wrong, and scoping it correctly
    is an improvement.
  - If only in standalone -> the two PDFs' sections genuinely differ, and
    tightening the filter makes these questions return nothing. That would be
    a regression wearing the costume of a fix. Audit and governance passages
    describe the FILING, not the consolidation basis, so this outcome is
    entirely plausible and would argue those chunk types should not be
    financial_type-scoped at all.

METHOD, and why it is a score comparison rather than a judgement. The tempting
version -- read both chunks, decide whether they say the same thing -- is the
exact shape of the 0.4834 mislabel that cohere_score_dump.py's header exists to
prevent: a label assigned by eye and then read back as evidence. Instead: ONE
retrieval per question, ONE Cohere scoring of the whole pool, then PARTITION
the scored pool by doc_id. Both documents' chunks are already in that pool --
_build_filter's should-clause admits every "unknown" chunk regardless of which
document it belongs to, and 2496 of 2531 chunks are "unknown". So the
comparison is between two subsets of one scored list, on one scale, from one
call.

Reading the result:
  best_correct   -- top Cohere score among chunks from the correct-type doc
  best_opposite  -- top score among chunks from the opposite-type doc
  A small gap means both documents carry the content and ranking chose badly.
  A large gap means the content is genuinely one-sided.

No prediction is recorded. The prior probe's prediction was disconfirmed, and a
band this measurement has not observed does not need another guess attached.

NO PRODUCTION PATH IS TOUCHED. _build_filter has no doc_id parameter; adding
one to serve a measurement would modify a production path. Partitioning the
returned candidates achieves the same thing and touches nothing.

Zero Gemini calls. One Cohere search per question.
"""

import json
import os
import sys
from datetime import datetime

from app.engines.retriever import (
    TOP_K_RERANK,
    _deduplicate_near_identical,
    _get_cohere_client,
    hybrid_search,
)

TENANT_ID = "a0000000-0000-0000-0000-000000000001"
DATASET = "/app/golden_dataset/q4fy26_eternal.json"
OUT = "/app/measurements/financial_type_counterpart_probe.json"

# The six questions with >=2 OPPOSITE citations in the leak probe run of
# 2026-08-09. Listed explicitly rather than re-derived, so this file records
# exactly which questions produced the committed JSON beside it.
QUESTION_IDS = ["Q032", "Q034", "Q035", "Q037", "Q038", "Q039"]

DOC_TYPE = {
    "d662a604-2f8c-549c-9374-06400875e04d": "consolidated",
    "ebaf1089-031d-5605-8090-846308d68dc7": "standalone",
    "27091929-f1d5-5c8d-897c-3d6437963418": "consolidated",
    "e33b7e55-0b7b-5e38-9948-afb76e3df2dc": "standalone",
    "352e249b-ca7e-508d-9a9d-377d4fe7c48c": "consolidated",
    "bbf75eac-eaa6-506f-b92b-154423882f8d": "standalone",
    "6a07229b-7084-59e4-a7be-86cf7de8d94e": "consolidated",
    "14b698c0-b6e4-58e6-89e2-e0c0e9844edf": "standalone",
}
TRANSCRIPT_DOC_ID = "1d8061a3-cb75-5524-a897-48a7baa81a1a"


def main():
    if not os.getenv("COHERE_API_KEY"):
        sys.exit("ABORT: COHERE_API_KEY not set.")
    client = _get_cohere_client()
    if client is None:
        sys.exit("ABORT: Cohere client failed to initialise.")

    with open(DATASET) as f:
        golden = {q["id"]: q for q in json.load(f)}

    report = {"meta": {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "tenant_id": TENANT_ID,
        "note": "One pool per question, scored once, partitioned by doc_id. "
                "fiscal_year=None -- widest honest pool.",
    }, "questions": []}

    for i, qid in enumerate(QUESTION_IDS, 1):
        g = golden[qid]
        query = g["question"]
        company = g.get("expected_company") or "ETERNAL"
        want = g.get("expected_financial_type") or "consolidated"

        print(f"\n[{i}/{len(QUESTION_IDS)}] {qid} (want={want})")
        print(f"  {query[:70]}")

        candidates = hybrid_search(
            query=query, tenant_id=TENANT_ID, company=company,
            fiscal_year=None, quarter=None, financial_type=want,
        )
        if not candidates:
            print("  -> 0 candidates")
            continue

        stale = {c.get("reranker_backend") for c in candidates} - {"none", None}
        if stale:
            sys.exit(f"ABORT: pool arrived pre-ranked by {stale}.")

        texts = [c["text"] for c in candidates]
        resp = client.rerank(model="rerank-english-v3.0", query=query,
                             documents=texts, top_n=len(texts))
        if len(resp.results) != len(texts):
            sys.exit(f"ABORT: {len(resp.results)} results for {len(texts)} docs.")
        bad = [h.relevance_score for h in resp.results
               if not (0.0 <= float(h.relevance_score) <= 1.0)]
        if bad:
            sys.exit(f"ABORT: scores outside [0,1]: {bad[:3]} -- ONNX logits.")

        scored = []
        for hit in resp.results:
            c = dict(candidates[hit.index])
            c["reranker_score"] = float(hit.relevance_score)
            c["reranker_backend"] = "cohere"
            scored.append(c)
        scored.sort(key=lambda c: c["reranker_score"], reverse=True)

        correct, opposite, transcript = [], [], []
        for c in scored:
            d = c["doc_id"]
            if d == TRANSCRIPT_DOC_ID:
                transcript.append(c)
            elif DOC_TYPE.get(d) == want:
                correct.append(c)
            elif d in DOC_TYPE:
                opposite.append(c)

        best_c = correct[0]["reranker_score"] if correct else None
        best_o = opposite[0]["reranker_score"] if opposite else None
        gap = (best_c - best_o) if (best_c is not None and best_o is not None) else None

        kept = _deduplicate_near_identical(scored)[:TOP_K_RERANK]

        print(f"  pool: {len(correct)} correct-type / {len(opposite)} opposite / "
              f"{len(transcript)} transcript")
        print(f"  best correct  = {best_c if best_c is None else round(best_c, 4)}")
        print(f"  best opposite = {best_o if best_o is None else round(best_o, 4)}")
        if gap is not None:
            verdict = ("CONTENT IN BOTH — ranking chose badly" if gap > -0.15
                       else "CONTENT ONE-SIDED — filtering would starve this question")
            print(f"  gap = {gap:+.4f}   {verdict}")

        report["questions"].append({
            "id": qid, "question": query, "requested_financial_type": want,
            "n_correct": len(correct), "n_opposite": len(opposite),
            "n_transcript": len(transcript),
            "best_correct": best_c, "best_opposite": best_o, "gap": gap,
            "correct_top3": [{"doc_id": c["doc_id"], "page": c["page_number"],
                              "score": round(c["reranker_score"], 4),
                              "chunk_type": c["chunk_type"],
                              "text": c["text"].replace("\n", " ")[:1200]}
                             for c in correct[:3]],
            "opposite_top3": [{"doc_id": c["doc_id"], "page": c["page_number"],
                               "score": round(c["reranker_score"], 4),
                               "chunk_type": c["chunk_type"],
                               "text": c["text"].replace("\n", " ")[:1200]}
                              for c in opposite[:3]],
            "final_five_doc_ids": [c["doc_id"] for c in kept],
        })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\n{'='*60}\nWritten to {OUT}")
    print("\nRead the stored text before believing any gap. The score says which")
    print("ranked higher; only the text says whether they answer the same question.")


if __name__ == "__main__":
    main()
