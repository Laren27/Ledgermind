"""
Read-only: how is `quarter` actually stored on ETERNAL chunks?

Distinguishes stored-null vs missing-key vs empty-string vs "None" string,
because IsNullCondition and IsEmptyCondition match different ones.
"""
import os
import sys
from collections import Counter, defaultdict

from dotenv import load_dotenv
from qdrant_client import QdrantClient

load_dotenv()
COLLECTION = "ledgermind_chunks"


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

    eternal = [p for p in points if p.payload.get("company") == "ETERNAL"]
    print(f"ETERNAL chunks: {len(eternal)}\n")

    def classify(pl):
        if "quarter" not in pl:
            return "KEY_ABSENT"
        v = pl["quarter"]
        if v is None:
            return "STORED_NULL"
        if v == "":
            return "EMPTY_STRING"
        if isinstance(v, str) and v.strip().lower() == "none":
            return "STRING_None"
        return f"VALUE:{v!r}"

    kinds = Counter(classify(p.payload) for p in eternal)
    print("quarter storage form:")
    for k, n in kinds.most_common():
        print(f"  {k:<20} {n}")

    # Cross-tab against doc_id: which document is which?
    per_doc = defaultdict(lambda: {"n": 0, "kinds": Counter(),
                                   "fy": Counter(), "pages": [],
                                   "latest": Counter(), "ftype": Counter()})
    for p in eternal:
        pl = p.payload
        d = per_doc[str(pl.get("doc_id"))]
        d["n"] += 1
        d["kinds"][classify(pl)] += 1
        d["fy"][pl.get("fiscal_year")] += 1
        d["latest"][str(pl.get("is_latest"))] += 1
        d["ftype"][pl.get("financial_type")] += 1
        pg = pl.get("page_number")
        if isinstance(pg, int):
            d["pages"].append(pg)

    print("\nper doc_id:")
    for doc, d in sorted(per_doc.items(), key=lambda kv: -kv[1]["n"]):
        rng = f"{min(d['pages'])}-{max(d['pages'])}" if d["pages"] else "n/a"
        print(f"  {doc}  n={d['n']:<5} pages={rng}")
        print(f"      quarter    : {dict(d['kinds'])}")
        print(f"      fiscal_year: {dict(d['fy'])}")
        print(f"      is_latest  : {dict(d['latest'])}")
        print(f"      fin_type   : {dict(d['ftype'])}")

    # The chunks the cross query needs.
    print("\nthe narrative PAT pages (4, 14, 45):")
    for p in eternal:
        if p.payload.get("page_number") in (4, 14, 45):
            pl = p.payload
            print(f"  p{pl.get('page_number'):<4} {pl.get('chunk_type'):<22} "
                  f"quarter={classify(pl):<14} fy={pl.get('fiscal_year')} "
                  f"latest={pl.get('is_latest')} ftype={pl.get('financial_type')}")


if __name__ == "__main__":
    main()
