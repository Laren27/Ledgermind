"""
Run once to create the Qdrant collection.
Safe to re-run — skips creation if collection already exists.

Usage (from ~/ledgermind/):
    docker exec ledgermind-backend-1 python scripts/create_qdrant_collection.py
"""

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    SparseVectorParams,
    SparseIndexParams,
)
from dotenv import load_dotenv
import os
import sys

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
COLLECTION_NAME = "ledgermind_chunks"
DENSE_DIM = 384  # bge-small-en-v1.5 — immutable after creation


def main():
    if not QDRANT_URL:
        print("QDRANT_URL not set. Add it to your .env:")
        print("  QDRANT_URL=https://your-cluster.qdrant.io")
        sys.exit(1)

    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)

    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME in existing:
        print(f"Collection '{COLLECTION_NAME}' already exists. Skipping.")
        info = client.get_collection(COLLECTION_NAME)
        print(f"  Vectors count : {info.vectors_count}")
        print(f"  Points count  : {info.points_count}")
        return

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config={
            "dense": VectorParams(
                size=DENSE_DIM,
                distance=Distance.COSINE,
            )
        },
        sparse_vectors_config={
            "sparse": SparseVectorParams(
                index=SparseIndexParams(on_disk=False)
            )
        },
    )

    print(f"Collection '{COLLECTION_NAME}' created.")
    print(f"  Dense dim : {DENSE_DIM} (COSINE)")
    print(f"  Sparse    : enabled (BM25 via on-disk=False)")


if __name__ == "__main__":
    main()