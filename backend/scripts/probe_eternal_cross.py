"""
Resolve a contradiction in the Cohere measurement, not a general tool.

The ETERNAL PAT cross query scored top1=0.0174 with fiscal_year=None -- inside
the 'poor' band (0.003-0.032) measured on queries this corpus cannot answer.
The historical record for the same query reads 0.584 (a BSE/NSE cover letter)
against 0.245 (the only relevant management-commentary chunk).

Neither number appeared in the 2026-08-01 run. The one known difference is
scoping: the router supplies fiscal_year, the score dump passed None. If that
is the whole explanation, FY26 scoping should reproduce the historical shape.
If it does not, the Eternal cross path is retrieving noise and that outranks
the citation-floor work.

Zero Gemini calls.
"""

from app.engines.retriever import _get_cohere_client, hybrid_search

TENANT = "a0000000-0000-0000-0000-000000000001"
Q = "Does Eternal's management commentary align with its PAT decline?"


def probe(label, **kw):
    cands = hybrid_search(query=Q, tenant_id=TENANT, company="ETERNAL",
                          financial_type="consolidated", **kw)
    print(f"\n--- {label} | {len(cands)} candidates ---")
    if not cands:
        print("  none")
        return
    r = _get_cohere_client().rerank(
        model="rerank-english-v3.0", query=Q,
        documents=[c["text"] for c in cands], top_n=len(cands))
    for i, hit in enumerate(sorted(r.results, key=lambda h: -h.relevance_score)[:8], 1):
        c = cands[hit.index]
        print(f"  {i}. {hit.relevance_score:.4f}  p{c['page_number']:<4} "
              f"{c['chunk_type']:<22} {c['text'][:88].replace(chr(10),' ')}")


probe("fiscal_year=None (as measured 2026-08-01)", fiscal_year=None, quarter=None)
probe("fiscal_year=FY26 (as the router would scope it)", fiscal_year="FY26", quarter=None)
