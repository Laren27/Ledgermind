"""
LedgerMind — Cohere score-distribution measurement
===================================================
Run: docker compose exec -T backend python -m scripts.cohere_score_dump

Answers three coupled questions, in this order (they cannot be answered
independently):
  1. Does Cohere discriminate at all on a corpus this small?
  2. Does COHERE_HIGH (currently 0.5) sit in the right place?
  3. Where should a citation relevance floor go?

Evidence motivating it: PQ018's citations scored 1.0000/0.9986/0.9388/0.9000/
0.7413 and TQ010's five all landed 0.99-1.00 -- possibly no discrimination.
Meanwhile an ETERNAL cross query returned tier=high off a 0.584 top score on a
BSE/NSE COVER LETTER, and post-dedup slots 4/5 have held 0.026 and 0.014.

Zero Gemini calls. Cohere bills per SEARCH, not per document, so scoring the
full candidate set costs the same as scoring five.

METHOD NOTE: hybrid_search() is the real function. Cohere is called directly
rather than through rerank(), because rerank() dedups and slices to top-5
before returning -- the full scored set exists only inside it. Adding a
return_all= parameter would modify a production path to serve a measurement,
and copying the call would create a second copy of rerank logic. Calling
Cohere directly is calling the thing under measurement, not reimplementing a
pipeline. Dedup below uses the REAL imported functions, not a copy.
"""

import json
import os
import statistics
import sys
from datetime import datetime

from app.engines.retriever import (
    NEAR_DUPLICATE_THRESHOLD,
    TOP_K_RERANK,
    TOP_K_RETRIEVAL,
    _deduplicate_near_identical,
    _get_cohere_client,
    _token_overlap,
    hybrid_search,
)

TENANT_ID = "a0000000-0000-0000-0000-000000000001"

# expectation is a PREDICTION recorded before measurement, so the results can
# disconfirm it. "poor" queries are plausible disclosures this corpus does not
# contain -- right company, wrong topic. That is the case a citation floor must
# survive; a query about a company absent from the corpus returns nothing and
# tests nothing.
QUERIES = [
    ("ETERNAL", "Does Eternal's management commentary align with its PAT decline?", "known_weak_ranking"),
    ("ETERNAL", "What did Eternal management say about quick commerce profitability?", "good"),
    ("PAYTM",   "What Show Cause Notice did Paytm's subsidiaries receive regarding FEMA compliance?", "good"),
    ("TITAN",   "Who is Titan's Managing Director and what did they say about Q1FY26 performance?", "good"),
    ("PAYTM",   "Paytm states it has no exposure to PPBL. Is that consistent with the 207 crore impairment of loans and investments in associates recorded in FY26?", "known_flat"),
    ("TITAN",   "Who audited Titan's Q1FY26 financial results?", "known_flat"),
    ("ETERNAL", "What is Eternal's employee attrition rate and hiring outlook?", "poor"),
    ("TITAN",   "What did Titan disclose about raw material gold price hedging?", "poor"),
    ("PAYTM",   "What are Paytm's data privacy and customer data retention policies?", "poor"),
    ("ETERNAL", "How does Eternal's board compose its audit committee?", "poor"),
]

OUT = "/mnt/user-data/outputs/cohere_score_dump.json"


def spread(scores):
    if not scores:
        return {}
    s = sorted(scores, reverse=True)
    return {
        "n": len(s),
        "top1": round(s[0], 4),
        "top5": round(s[4], 4) if len(s) > 4 else None,
        "median": round(statistics.median(s), 4),
        "min": round(s[-1], 4),
        "stdev": round(statistics.stdev(s), 4) if len(s) > 1 else 0.0,
        "top1_minus_top5": round(s[0] - s[4], 4) if len(s) > 4 else None,
        "top1_minus_median": round(s[0] - statistics.median(s), 4),
    }


def main():
    if not os.getenv("COHERE_API_KEY"):
        print("ABORT: COHERE_API_KEY not set. This measures COHERE, not the local fallback.")
        sys.exit(1)

    client = _get_cohere_client()
    if client is None:
        print("ABORT: Cohere client failed to initialise.")
        sys.exit(1)

    report = {
        "meta": {
            "run_at": datetime.now().isoformat(timespec="seconds"),
            "tenant_id": TENANT_ID,
            "retrieval_top_k": TOP_K_RETRIEVAL,
            "rerank_top_k": TOP_K_RERANK,
            "dedup_threshold": NEAR_DUPLICATE_THRESHOLD,
            "cohere_model": "rerank-english-v3.0",
            "note": "fiscal_year=None on every query -- widest honest candidate pool.",
        },
        "queries": [],
    }

    for i, (company, query, expectation) in enumerate(QUERIES, 1):
        print(f"\n[{i}/{len(QUERIES)}] {company} ({expectation})")
        print(f"  {query[:72]}")

        candidates = hybrid_search(
            query=query, tenant_id=TENANT_ID, company=company,
            fiscal_year=None, quarter=None, financial_type="consolidated",
        )
        if not candidates:
            print("  -> 0 candidates retrieved")
            report["queries"].append({
                "company": company, "query": query, "expectation": expectation,
                "candidates": 0, "error": "no_candidates",
            })
            continue

        texts = [c["text"] for c in candidates]
        resp = client.rerank(model="rerank-english-v3.0", query=query,
                             documents=texts, top_n=len(texts))

        scored = []
        for hit in resp.results:
            c = dict(candidates[hit.index])
            c["reranker_score"] = float(hit.relevance_score)
            c["reranker_backend"] = "cohere"
            scored.append(c)
        scored.sort(key=lambda c: c["reranker_score"], reverse=True)

        # Gate: mixing Cohere 0-1 scores with local ONNX logits on one axis is
        # the exact §13 miscalibration this measurement exists to avoid.
        backends = {c["reranker_backend"] for c in scored}
        if backends != {"cohere"}:
            print(f"  ABORT: non-Cohere backend present: {backends}")
            sys.exit(1)

        kept = _deduplicate_near_identical(scored)
        kept_ids = {c["chunk_id"] for c in kept[:TOP_K_RERANK]}

        # Full pairwise overlap >= 0.50 -- deliberately BELOW the 0.70 cut, so
        # near-misses are visible. The INFO logs only ever show what was
        # DROPPED; false positives live on the kept side and never appear.
        overlaps = []
        for a in range(len(scored)):
            for b in range(a + 1, len(scored)):
                r = _token_overlap(scored[a]["text"], scored[b]["text"])
                if r >= 0.50:
                    overlaps.append({
                        "rank_a": a + 1, "rank_b": b + 1,
                        "page_a": scored[a]["page_number"], "page_b": scored[b]["page_number"],
                        "score_a": round(scored[a]["reranker_score"], 4),
                        "score_b": round(scored[b]["reranker_score"], 4),
                        "overlap": round(r, 4),
                        "suppressed": r >= NEAR_DUPLICATE_THRESHOLD,
                    })

        scores = [c["reranker_score"] for c in scored]
        st = spread(scores)
        kept_st = spread([c["reranker_score"] for c in kept[:TOP_K_RERANK]])

        print(f"  {len(scored)} scored | top1={st['top1']} median={st['median']} "
              f"min={st['min']} stdev={st['stdev']}")
        print(f"  post-dedup top-{TOP_K_RERANK}: "
              f"{[round(c['reranker_score'], 4) for c in kept[:TOP_K_RERANK]]}")
        if overlaps:
            print(f"  {len(overlaps)} pairs >=0.50 overlap "
                  f"({sum(o['suppressed'] for o in overlaps)} suppressed)")

        report["queries"].append({
            "company": company, "query": query, "expectation": expectation,
            "candidates": len(scored),
            "spread_all": st,
            "spread_kept_topk": kept_st,
            "overlap_pairs": overlaps,
            "chunks": [{
                "rank": r + 1,
                "score": round(c["reranker_score"], 4),
                "page": c["page_number"],
                "chunk_type": c["chunk_type"],
                "financial_type": c["financial_type"],
                "fiscal_year": c["fiscal_year"],
                "rrf_score": round(c["rrf_score"], 5),
                "survives_dedup_topk": c["chunk_id"] in kept_ids,
                "preview": c["text"][:180].replace("\n", " "),
            } for r, c in enumerate(scored)],
        })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n{'='*60}\nWritten to {OUT}")

    ok = [q for q in report["queries"] if q.get("spread_all")]
    print("\nSpread by expectation (does Cohere discriminate?):")
    for exp in ("good", "known_flat", "known_weak_ranking", "poor"):
        rows = [q for q in ok if q["expectation"] == exp]
        if rows:
            print(f"  {exp:<20} top1 {min(q['spread_all']['top1'] for q in rows):.3f}"
                  f"-{max(q['spread_all']['top1'] for q in rows):.3f} | "
                  f"median stdev {statistics.median(q['spread_all']['stdev'] for q in rows):.3f}")

    print("\nCOHERE_HIGH=0.5 check -- top1 by expectation:")
    for q in ok:
        flag = " <-- 'poor' scoring above 0.5" if q["expectation"] == "poor" and q["spread_all"]["top1"] > 0.5 else ""
        print(f"  {q['spread_all']['top1']:.4f}  {q['expectation']:<20} {q['query'][:44]}{flag}")


if __name__ == "__main__":
    main()
