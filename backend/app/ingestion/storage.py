"""
app/ingestion/storage.py

Thin wrapper around the Supabase Storage REST API using httpx directly —
no supabase-py SDK dependency needed, since httpx is already a project
dependency and our needs are exactly two operations: upload a file,
download a file.

Why Storage and not local disk:
  FastAPI (web) and whatever actually runs ingestion (Celery worker
  locally, or an in-process BackgroundTask in production — see
  pipeline.py) do not reliably share a filesystem. A file written to
  /tmp by the web process may be invisible to whatever runs ingestion.
  Supabase Storage gives both sides a durable, shared handoff point
  regardless of container topology.
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY", "")
BUCKET_NAME = os.getenv("SUPABASE_UPLOAD_BUCKET", "ledgermind-uploads")

_STORAGE_OBJECT_URL = f"{SUPABASE_URL}/storage/v1/object/{{bucket}}/{{key}}"


def _require_config() -> None:
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SECRET_KEY must both be set to use Storage."
        )


async def upload_file_to_storage(local_path: str, storage_key: str, bucket: str = BUCKET_NAME) -> None:
    """
    Upload a local file to Supabase Storage. Async — called from the
    FastAPI upload endpoint while handling the request.
    Raises on any non-2xx response.
    """
    _require_config()
    url = _STORAGE_OBJECT_URL.format(bucket=bucket, key=storage_key)

    with open(local_path, "rb") as f:
        file_bytes = f.read()

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            url,
            content=file_bytes,
            headers={
                "apikey": SUPABASE_SECRET_KEY,
                "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
                "Content-Type": "application/pdf",
                "x-upsert": "true",
            },
        )

    if resp.status_code not in (200, 201):
        logger.error(
            "Supabase Storage upload failed key=%s status=%s body=%s",
            storage_key, resp.status_code, resp.text[:500],
        )
        raise RuntimeError(f"Storage upload failed ({resp.status_code}): {resp.text[:200]}")

    logger.info("Uploaded to Supabase Storage: %s", storage_key)


def download_file_from_storage(storage_key: str, local_path: str, bucket: str = BUCKET_NAME) -> None:
    """
    Download a file from Supabase Storage to a local path. Sync — called
    from inside the ingestion pipeline (Celery worker or BackgroundTask),
    which is itself synchronous code.
    Raises on any non-2xx response.
    """
    _require_config()
    url = _STORAGE_OBJECT_URL.format(bucket=bucket, key=storage_key)

    with httpx.Client(timeout=60.0) as client:
        resp = client.get(
            url,
            headers={
                "apikey": SUPABASE_SECRET_KEY,
                "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
            },
        )

    if resp.status_code != 200:
        logger.error(
            "Supabase Storage download failed key=%s status=%s body=%s",
            storage_key, resp.status_code, resp.text[:500],
        )
        raise RuntimeError(f"Storage download failed ({resp.status_code}): {resp.text[:200]}")

    with open(local_path, "wb") as f:
        f.write(resp.content)

    logger.info("Downloaded from Supabase Storage: %s -> %s", storage_key, local_path)
