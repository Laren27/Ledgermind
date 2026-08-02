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

    # --- absent_cross, added 2026-08-02 -------------------------------------
    # PURPOSE: stress the 0.15-0.5 band, which the 2026-08-01 run left empty.
    # That run was good-versus-poor: poor topped out at 0.0323, good started at
    # 0.329, nothing between. A live cross query on 2026-08-02 landed at
    # 0.3211/0.1734 -- inside the gap -- and returned tier=medium on what was
    # by content a genuine no-answer, which is what currently makes
    # _reconcile_cross Quadrant 4 unreachable by any question.
    #
    # HYPOTHESIS UNDER TEST, recorded before measuring so it can be
    # disconfirmed: the separating variable is FRAMING, not topic absence.
    # 'poor' queries above are bare factual lookups ("What is Eternal's
    # attrition rate"). These are cross-style comparisons naming "financial
    # exposure", and Cohere may be scoring that framing against the financial
    # statements -- matching the FRAME of the question rather than its subject,
    # the same behaviour recorded when the ETERNAL cross query ranked
    # forward-looking-statements boilerplate above genuine margin commentary.
    #
    # CONTROLLED PAIR: data privacy and audit committee appear in BOTH sets,
    # bare above and cross-framed here. Same topic, same corpus, different
    # framing. If those two pairs diverge the hypothesis holds; if all ten
    # interleave it is wrong, which is the more useful outcome.
    #
    # If absent_cross clusters near 0.3 while poor stays near 0.01, the band
    # has structure and COHERE_MEDIUM has an evidence-backed value to move to.
    # Do not move it on fewer points than this.
    ("ETERNAL", "Does Eternal's disclosed approach to franchisee dispute resolution align with its financial exposure to those disputes?", "absent_cross"),
    ("PAYTM",   "Is Paytm's stated customer information policy consistent with its financial exposure to regulatory penalties?", "absent_cross"),
    ("ETERNAL", "Does Eternal's audit committee composition align with its financial exposure to related-party transactions?", "absent_cross"),
    ("TITAN",   "Does Titan's disclosed gold price hedging approach align with its financial exposure to commodity movements?", "absent_cross"),
    ("ETERNAL", "Is Eternal's stated approach to rider safety consistent with its financial exposure to insurance claims?", "absent_cross"),
    ("PAYTM",   "Does Paytm's disclosed information security posture align with its financial exposure to breach liability?", "absent_cross"),
    ("TITAN",   "Is Titan's stated store-expansion strategy consistent with its financial exposure to lease commitments?", "absent_cross"),
]

# --- specificity set, added 2026-08-03 ---------------------------------------
# A SEPARATE list rather than more entries in QUERIES, because it asks a
# different question and mixing them would make the per-label summaries
# meaningless.
#
# Two labels only, both recorded before measuring:
#
#   absent  — topics this corpus structurally cannot answer. Targeted at TITAN
#             and PAYTM specifically: their chunk counts are small enough that
#             absence is a property of the corpus, not a judgement call about
#             whether some passage half-addresses the question. All six are
#             governance/policy topics (board evaluation, RPT approvals, vigil
#             mechanism, sitting fees, CSR, risk committee charter) of the kind
#             that live in an annual report's statutory section, which these
#             quarterly filings do not carry.
#
#   genuine — real corpus content, deliberately PERIPHERAL rather than headline.
#             Headline topics (revenue, PAT) are already known to score high;
#             they would measure the easy case. ESOPs, lease-liability cash-flow
#             treatment, store counts, segment reporting, impairment of loans to
#             associates and order-mix commentary are all genuinely present and
#             all genuinely secondary.
#
# No prediction is recorded about WHERE these land. The prior absent_cross run
# disconfirmed its own stated hypothesis, and a band this measurement has not
# yet observed does not need another guess attached to it.
SPECIFICITY_QUERIES = [
    ("TITAN",   "How does Titan's board evaluate the performance of its independent directors?", "absent"),
    ("TITAN",   "What is Titan's policy on related party transaction approvals?", "absent"),
    ("PAYTM",   "What vigil mechanism does Paytm maintain for whistleblower complaints?", "absent"),
    ("TITAN",   "How does Titan determine sitting fees for non-executive directors?", "absent"),
    ("PAYTM",   "What is Paytm's stated approach to CSR expenditure allocation?", "absent"),
    ("TITAN",   "How does Titan's risk management committee define its charter?", "absent"),

    ("ETERNAL", "What does Zomato disclose about its employee stock option plans?", "genuine"),
    ("ETERNAL", "How are lease liabilities treated in Zomato's cash flow statement?", "genuine"),
    ("ETERNAL", "What does Eternal report about store count changes in the quarter?", "genuine"),
    ("TITAN",   "What does Titan report for its Watches segment this quarter?", "genuine"),
    ("PAYTM",   "What does Paytm disclose about impairment of loans to associates?", "genuine"),
    ("ETERNAL", "What does Eternal say about order mix shifting toward lower-value orders?", "genuine"),
]

# Which set this invocation measures. Kept as an explicit name rather than a
# CLI flag so the committed file records exactly which set produced the
# committed JSON alongside it.
QUERIES_TO_RUN = SPECIFICITY_QUERIES

# Written inside the container; copy out with:
#   docker compose exec -T backend cat /app/measurements/cohere_score_dump.json > docs/measurements/<name>.json
OUT = "/app/measurements/cohere_score_dump.json"


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

    for i, (company, query, expectation) in enumerate(QUERIES_TO_RUN, 1):
        print(f"\n[{i}/{len(QUERIES_TO_RUN)}] {company} ({expectation})")
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
            # RECORDING ONLY. doc_id and the wider preview were added
            # 2026-08-03 after a stored result could not answer a question
            # asked of it: the audit-committee chunk in
            # cohere_band_stress_2026-08-02.json was recorded at 180 chars
            # with no doc_id, so establishing whether the corpus actually
            # contained audit-committee disclosure -- and which document it
            # came from -- had to be inferred from page + fiscal_year rather
            # than read. The label on that query turned out to be wrong. A
            # measurement whose provenance cannot be checked from the file it
            # wrote is one re-run away from being worthless.
            #
            # .get() rather than [] on doc_id per the ChunkResult TypedDict
            # convention; it is a declared field populated from the Qdrant
            # payload, but a chunk written before that payload key existed
            # should degrade to "" rather than raise mid-measurement.
            "chunks": [{
                "rank": r + 1,
                "score": round(c["reranker_score"], 4),
                "doc_id": c.get("doc_id", ""),
                "page": c["page_number"],
                "chunk_type": c["chunk_type"],
                "financial_type": c["financial_type"],
                "fiscal_year": c["fiscal_year"],
                "rrf_score": round(c["rrf_score"], 5),
                "survives_dedup_topk": c["chunk_id"] in kept_ids,
                "preview": c["text"][:600].replace("\n", " "),
            } for r, c in enumerate(scored)],
        })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n{'='*60}\nWritten to {OUT}")

    ok = [q for q in report["queries"] if q.get("spread_all")]
    print("\nSpread by expectation (does Cohere discriminate?):")
    # Derived from the data rather than hardcoded, so a run of any query set
    # summarises its own labels instead of silently printing nothing.
    for exp in sorted({q["expectation"] for q in ok}):
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
