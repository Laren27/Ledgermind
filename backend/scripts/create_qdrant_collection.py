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
import os
import sys

QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
COLLECTION_NAME = "ledgermind_chunks"
DENSE_DIM = 384  # bge-small-en-v1.5 — immutable after creation


def main():
    client = QdrantClient(url=QDRANT_URL)

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