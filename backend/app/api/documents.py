"""
app/api/documents.py

Document upload endpoint. Runs the pre-ingestion gate synchronously
(cheap: ~2-page text scan) BEFORE enqueueing the real Celery ingestion
task from app.ingestion.pipeline. A rejected document never reaches the
worker queue.

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
from app.ingestion.gate import GateDecision, check_is_financial_filing
from app.ingestion.pdf_text import extract_first_n_pages_text
from app.ingestion.pipeline import get_ingest_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])

UPLOAD_DIR = Path("/tmp/ledgermind_uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50MB — generous for annual reports

# Memoized task handle — get_ingest_task() re-registers the task with Celery
# on every call (see pipeline._get_celery_task); calling it once at import
# time and reusing the handle avoids repeated task registration per-request.
_INGEST_TASK = get_ingest_task()


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

    # --- Size-guarded write (fail closed before disk exhaustion) ---
    written = 0
    with temp_path.open("wb") as f:
        while chunk := await file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                f.close()
                temp_path.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="File exceeds 50MB limit.")
            f.write(chunk)

    # --- Pre-ingestion gate ---
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

    # --- Enqueue real ingestion pipeline ---
    if _INGEST_TASK is None:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=503,
            detail="Ingestion worker unavailable — Celery task registration failed.",
        )

    _INGEST_TASK.delay(
        pdf_path=str(temp_path),
        tenant_id=user["tenant_id"],
        company=company,
        ticker=ticker,
        fiscal_year=fiscal_year,
        quarter=quarter,
        doc_type=doc_type,
        filing_date=filing_date,
        version=version,
    )

    return {
        "doc_id": doc_id,
        "status": "queued",
        "gate_score": gate_result.score,
    }