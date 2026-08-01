"""
Read-only: is any document ingested more than once in Qdrant?

SHA256 dedup guards the relational side only -- `documents` and `financials`
can look clean while Qdrant holds two copies under different doc_ids.

Detects by TEXT-HASH SET OVERLAP between doc_ids, not by page range or chunk
count: a partial or re-chunked re-ingest produces different counts over the
same content and would pass a metadata-only check.
"""
import hashlib
import os
import sys
from collections import Counter, defaultdict
from itertools import combinations

from dotenv import load_dotenv
from qdrant_client import QdrantClient

load_dotenv()
COLLECTION = "ledgermind_chunks"
OVERLAP_ALERT = 0.90


def h(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def main():
    url = os.getenv("QDRANT_URL")
    if not url:
        sys.exit("ABORT: QDRANT_URL unset.")
    if not url.startswith("https://"):
        sys.exit(f"ABORT: not Qdrant Cloud: {url}")
    client = QdrantClient(url=url, api_key=os.getenv("QDRANT_API_KEY"), timeout=60)

    points, offset = [], None
    while True:
        batch, offset = client.scroll(
            collection_name=COLLECTION, limit=256, offset=offset,
            with_payload=True, with_vectors=False,
        )
        points.extend(batch)
        if offset is None:
            break
    print(f"Scrolled {len(points)} points\n")

    by_company = defaultdict(list)
    for p in points:
        by_company[p.payload.get("company")].append(p)

    for company in sorted(by_company, key=lambda c: -len(by_company[c])):
        pts = by_company[company]
        print(f"=== {company} — {len(pts)} chunks ===")

        docs = defaultdict(lambda: {"hashes": set(), "n": 0, "pages": [],
                                    "fy": Counter(), "q": Counter(),
                                    "dtype": Counter(), "filed": Counter()})
        for p in pts:
            pl = p.payload
            d = docs[str(pl.get("doc_id"))]
            d["n"] += 1
            d["hashes"].add(h(pl.get("text") or ""))
            d["fy"][pl.get("fiscal_year")] += 1
            d["q"][str(pl.get("quarter"))] += 1
            d["dtype"][pl.get("document_type")] += 1
            d["filed"][str(pl.get("filing_date"))] += 1
            pg = pl.get("page_number")
            if isinstance(pg, int):
                d["pages"].append(pg)

        for doc, d in sorted(docs.items(), key=lambda kv: -kv[1]["n"]):
            rng = f"{min(d['pages'])}-{max(d['pages'])}" if d["pages"] else "n/a"
            dupes = d["n"] - len(d["hashes"])
            print(f"  {doc}  n={d['n']:<5} distinct_text={len(d['hashes']):<5} "
                  f"intra_dupe={dupes:<4} pages={rng}")
            print(f"      fy={dict(d['fy'])} q={dict(d['q'])}")
            print(f"      type={dict(d['dtype'])} filed={dict(d['filed'])}")

        flagged = False
        for (a, da), (b, db) in combinations(docs.items(), 2):
            inter = len(da["hashes"] & db["hashes"])
            ratio = inter / min(len(da["hashes"]), len(db["hashes"]))
            if ratio >= OVERLAP_ALERT:
                flagged = True
                print(f"  !! DUPLICATE: {a[:8]} vs {b[:8]} — "
                      f"{ratio*100:.1f}% shared text ({inter} chunks)")
            elif ratio > 0.10:
                print(f"  ?  partial overlap: {a[:8]} vs {b[:8]} — {ratio*100:.1f}%")
        if not flagged:
            print("  no duplicate doc_ids")
        print()


if __name__ == "__main__":
    main()
