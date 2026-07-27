"""
app/api/documents.py

Document upload endpoint.

Runs the pre-ingestion gate synchronously (cheap: ~2-page text scan),
uploads the file to Supabase Storage, then records a `pending_uploads`
row — it does NOT trigger ingestion automatically.

Why no auto-trigger: loading the bge-small-en-v1.5 embedding model
in-process OOM-killed Render's 512MB free-tier web service (confirmed
via repeated "Exited with status 137" events). Running that step inside
the same process that serves live queries is unsafe on this tier
regardless of whether it's triggered via Celery or BackgroundTasks.

Instead: backend/scripts/process_pending_uploads.py polls this table
and runs ingestion locally (proven-safe RAM), matching every other
ingestion in this project's history (always CLI/admin-triggered, never
fully self-service).

financial_type is NOT collected here — it is auto-detected per-section
from document content inside pipeline._run_ingestion (detect_sections /
register_sections), per the Trap 1 fix (classify from content, never
from filename or user input).
"""

import logging
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile

from app.auth.dependencies import require_role
from app.db.session import db_transaction, get_connection
from app.ingestion.gate import GateDecision, check_is_financial_filing
from app.ingestion.pdf_text import extract_first_n_pages_text
from app.ingestion.storage import upload_file_to_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])

UPLOAD_DIR = Path("/tmp/ledgermind_uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50MB — generous for annual reports

_SQL_INSERT_PENDING = """
INSERT INTO pending_uploads
    (tenant_id, storage_key, company, ticker, fiscal_year, quarter,
     doc_type, filing_date, version, status)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
RETURNING id
"""


@router.post("/upload")
async def upload_document(
    file: UploadFile,
    company: str = Form(...),
    ticker: str = Form(...),
    fiscal_year: str = Form(...),
    doc_type: str = Form(...),          # annual_report / quarterly_result / drhp / transcript
    filing_date: str = Form(...),       # YYYY-MM-DD
    quarter: Optional[str] = Form(None),  # null for annual reports
    version: str = Form("v1"),
    user: dict = Depends(require_role("admin")),  # upload is admin-only per RBAC table
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF uploads are supported.")

    doc_id = str(uuid.uuid4())
    temp_path = UPLOAD_DIR / f"{doc_id}.pdf"

    # --- Size-guarded write to local scratch space (fail closed before disk exhaustion) ---
    written = 0
    with temp_path.open("wb") as f:
        while chunk := await file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                f.close()
                temp_path.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="File exceeds 50MB limit.")
            f.write(chunk)

    # --- Pre-ingestion gate (cheap local read, same container/request) ---
    try:
        first_pages_text = extract_first_n_pages_text(str(temp_path), n=2)
    except Exception as e:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Could not read PDF: {e}")

    gate_result = check_is_financial_filing(first_pages_text)

    logger.info(
        "ingestion_gate doc_id=%s filename=%s decision=%s score=%d categories=%s",
        doc_id, file.filename, gate_result.decision.value,
        gate_result.score, gate_result.matched_categories,
    )

    if gate_result.decision == GateDecision.REJECT:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=gate_result.reason)

    # --- Hand off to Supabase Storage (durable, shared with the local script) ---
    tenant_id = user["tenant_id"]
    storage_key = f"{tenant_id}/{doc_id}.pdf"

    try:
        await upload_file_to_storage(str(temp_path), storage_key)
    except Exception as e:
        logger.error("Storage upload failed doc_id=%s: %s", doc_id, e)
        raise HTTPException(status_code=502, detail="Could not store uploaded file.")
    finally:
        temp_path.unlink(missing_ok=True)

    # --- Record as pending — NOT auto-triggered (see module docstring) ---
    with db_transaction(tenant_id) as conn:
        with conn.cursor() as cur:
            cur.execute(
                _SQL_INSERT_PENDING,
                (tenant_id, storage_key, company, ticker, fiscal_year,
                 quarter, doc_type, filing_date, version),
            )
            pending_id = cur.fetchone()[0]

    logger.info("Recorded pending upload doc_id=%s pending_id=%s", doc_id, pending_id)

    return {
        "doc_id": doc_id,
        "pending_id": str(pending_id),
        "status": "pending",
        "gate_score": gate_result.score,
        "message": "File stored. Run process_pending_uploads.py to ingest.",
    }


_SQL_FETCH_PENDING_FOR_TENANT = """
SELECT id, storage_key, company, ticker, fiscal_year, quarter,
       doc_type, filing_date, version, status, error_message,
       created_at, updated_at
FROM pending_uploads
ORDER BY created_at DESC
LIMIT 50
"""


@router.get("/pending")
async def list_pending_uploads(
    user: dict = Depends(require_role("admin")),
):
    """
    Admin-only. Lists this tenant's pending_uploads rows so the frontend
    can show real ingestion status (pending/processing/done/failed)
    instead of a static "check back later" message.
    """
    tenant_id = user["tenant_id"]
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SET app.tenant_id = %s", (tenant_id,))
            cur.execute(_SQL_FETCH_PENDING_FOR_TENANT)
            cols = [desc[0] for desc in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()

    return {
        "pending_uploads": [
            {
                "id": str(r["id"]),
                "storage_key": r["storage_key"],
                "company": r["company"],
                "ticker": r["ticker"],
                "fiscal_year": r["fiscal_year"],
                "quarter": r["quarter"],
                "doc_type": r["doc_type"],
                "filing_date": r["filing_date"],
                "version": r["version"],
                "status": r["status"],
                "error_message": r["error_message"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            }
            for r in rows
        ]
    }
