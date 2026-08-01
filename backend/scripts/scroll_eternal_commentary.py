"""
Read-only Qdrant scroll: does ETERNAL's corpus contain any PAT /
profitability commentary at all?

Answers the gate for backlog #1. Makes ZERO LLM and ZERO Cohere calls.

Run:
    docker compose exec -T backend python -m scripts.scroll_eternal_commentary
Copy result out:
    docker compose exec -T backend cat /app/measurements/eternal_scroll.json \
        > docs/measurements/eternal_scroll_$(date +%F).json
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict

from dotenv import load_dotenv
from qdrant_client import QdrantClient

load_dotenv()

COLLECTION = "ledgermind_chunks"
TARGET = "ETERNAL"
OUT_DIR = "/app/measurements"
OUT_PATH = os.path.join(OUT_DIR, "eternal_scroll.json")

# Tier A: the metric itself. \bPAT\b is case-SENSITIVE (path/patent/pattern).
TIER_A = [
    ("profit_after_tax", re.compile(r"profit after tax", re.I)),
    ("PAT_acronym",      re.compile(r"\bPAT\b")),
    ("net_profit",       re.compile(r"net profit", re.I)),
    ("net_loss",         re.compile(r"net loss", re.I)),
    ("loss_for_the",     re.compile(r"loss for the", re.I)),
    ("profit_for_the",   re.compile(r"profit for the", re.I)),
]

# Tier B: profitability commentary without naming PAT.
TIER_B = [
    ("profitability",    re.compile(r"profitabilit", re.I)),
    ("ebitda",           re.compile(r"\bEBITDA\b", re.I)),
    ("margin",           re.compile(r"\bmargins?\b", re.I)),
    ("unit_economics",   re.compile(r"unit econom", re.I)),
    ("breakeven",        re.compile(r"break[- ]?even", re.I)),
    ("contribution",     re.compile(r"contribution margin", re.I)),
]

WINDOW = 200


def scroll_all(client):
    points, offset = [], None
    while True:
        batch, offset = client.scroll(
            collection_name=COLLECTION,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        points.extend(batch)
        if offset is None:
            break
    return points


def snippets(text, pattern):
    out = []
    for m in pattern.finditer(text):
        lo = max(0, m.start() - WINDOW)
        hi = min(len(text), m.end() + WINDOW)
        out.append(text[lo:hi].replace("\n", " "))
        if len(out) >= 2:
            break
    return out


def main():
    url = os.getenv("QDRANT_URL")
    if not url:
        sys.exit("ABORT: QDRANT_URL unset.")
    client = QdrantClient(url=url, api_key=os.getenv("QDRANT_API_KEY"), timeout=60)

    points = scroll_all(client)
    if not points:
        sys.exit(f"ABORT: collection '{COLLECTION}' returned zero points.")

    keys = sorted(points[0].payload.keys())
    print(f"Scrolled {len(points)} points")
    print(f"Payload keys: {keys}\n")

    # Hard guard. A scan against a missing field looks identical to "no evidence".
    for required in ("text", "company"):
        if required not in keys:
            sys.exit(f"ABORT: payload has no '{required}' field. Keys: {keys}")

    companies = Counter(p.payload.get("company") for p in points)
    print(f"Companies in collection: {dict(companies)}\n")
    if TARGET not in companies:
        sys.exit(
            f"ABORT: no company == '{TARGET}'. Casing/value mismatch, "
            f"not a coverage result. Seen: {list(companies)}"
        )

    eternal = [p for p in points if p.payload.get("company") == TARGET]

    by_type = Counter(p.payload.get("chunk_type") for p in eternal)
    by_page = Counter(p.payload.get("page_number") for p in eternal)
    by_tenant = Counter(str(p.payload.get("tenant_id")) for p in eternal)
    by_section = Counter(p.payload.get("section") for p in eternal)

    print(f"{TARGET}: {len(eternal)} chunks")
    print(f"  chunk_type : {dict(by_type)}")
    print(f"  tenant_id  : {dict(by_tenant)}")
    print(f"  section    : {dict(by_section)}")
    print(f"  top pages  : {by_page.most_common(12)}\n")

    tier_a_hits, tier_b_hits = [], []
    a_ids = set()

    for p in eternal:
        text = p.payload.get("text") or ""
        matched = [(n, pat) for n, pat in TIER_A if pat.search(text)]
        if matched:
            a_ids.add(p.id)
            tier_a_hits.append({
                "point_id": str(p.id),
                "page": p.payload.get("page_number"),
                "chunk_type": p.payload.get("chunk_type"),
                "section": p.payload.get("section"),
                "patterns": [n for n, _ in matched],
                "snippets": snippets(text, matched[0][1]),
                "char_len": len(text),
            })

    for p in eternal:
        if p.id in a_ids:
            continue
        text = p.payload.get("text") or ""
        matched = [(n, pat) for n, pat in TIER_B if pat.search(text)]
        if matched:
            tier_b_hits.append({
                "point_id": str(p.id),
                "page": p.payload.get("page_number"),
                "chunk_type": p.payload.get("chunk_type"),
                "section": p.payload.get("section"),
                "patterns": [n for n, _ in matched],
                "snippets": snippets(text, matched[0][1]),
            })

    print(f"TIER A (PAT / profit-loss named): {len(tier_a_hits)} chunks")
    for h in tier_a_hits[:25]:
        print(f"  p{h['page']:>4} {h['chunk_type']:<22} {h['patterns']}")
        for s in h["snippets"][:1]:
            print(f"        ...{s[:240]}...")

    narrative_a = [
        h for h in tier_a_hits
        if h["chunk_type"] not in ("FINANCIAL_STATEMENT", "TABLE")
    ]
    print(f"\n  of which NON-statement (narrative): {len(narrative_a)}")

    print(f"\nTIER B only (profitability, no PAT): {len(tier_b_hits)} chunks")
    for h in tier_b_hits[:20]:
        print(f"  p{h['page']:>4} {h['chunk_type']:<22} {h['patterns']}")

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump({
            "meta": {
                "collection": COLLECTION,
                "target": TARGET,
                "total_points": len(points),
                "eternal_chunks": len(eternal),
                "payload_keys": keys,
                "companies": {str(k): v for k, v in companies.items()},
            },
            "distribution": {
                "chunk_type": {str(k): v for k, v in by_type.items()},
                "page_number": {str(k): v for k, v in by_page.items()},
                "section": {str(k): v for k, v in by_section.items()},
                "tenant_id": dict(by_tenant),
            },
            "tier_a": tier_a_hits,
            "tier_b_only": tier_b_hits,
            "tier_a_narrative_count": len(narrative_a),
        }, f, indent=2)
    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    main()