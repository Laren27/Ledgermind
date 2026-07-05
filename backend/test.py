import os
from pathlib import Path

# Load .env the same way every other script in this session does
env_path = Path.home() / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from app.ingestion.qdrant_writer import _get_client, COLLECTION_NAME, DENSE_VECTOR_NAME
from qdrant_client.models import Filter, FieldCondition, MatchValue

client = _get_client()

results = client.query_points(
    collection_name=COLLECTION_NAME,
    query=[0.0] * 384,
    using=DENSE_VECTOR_NAME,
    query_filter=Filter(must=[
        FieldCondition(key="company", match=MatchValue(value="ETERNAL")),
        FieldCondition(key="fiscal_year", match=MatchValue(value="FY24")),
        FieldCondition(key="is_latest", match=MatchValue(value=True)),
    ]),
    limit=5,
    with_payload=True,
).points

for r in results:
    print(r.payload.get("fiscal_year"), r.payload.get("page_number"), r.payload.get("chunk_type"))

results = client.query_points(
    collection_name=COLLECTION_NAME,
    query=[0.0] * 384,
    using=DENSE_VECTOR_NAME,
    query_filter=Filter(must=[
        FieldCondition(key="company", match=MatchValue(value="ETERNAL")),
        FieldCondition(key="fiscal_year", match=MatchValue(value="FY24")),
        FieldCondition(key="is_latest", match=MatchValue(value=True)),
        FieldCondition(key="chunk_type", match=MatchValue(value="FINANCIAL_STATEMENT")),
    ]),
    limit=5,
    with_payload=True,
).points

print(f"FINANCIAL_STATEMENT chunks found: {len(results)}")
for r in results:
    print(r.payload.get("page_number"), r.payload.get("text", "")[:80])

count = client.count(
    collection_name=COLLECTION_NAME,
    count_filter=Filter(must=[
        FieldCondition(key="company", match=MatchValue(value="ETERNAL")),
        FieldCondition(key="fiscal_year", match=MatchValue(value="FY24")),
    ]),
    exact=True,
)
print("Total FY24 points:", count.count)