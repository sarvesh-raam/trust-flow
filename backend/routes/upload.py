"""Upload routes — accept a matched invoice + bill-of-lading PDF pair."""
from __future__ import annotations

import uuid
from pathlib import Path

import aiofiles
import structlog
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from models import (
    CountryCode,
    DocumentRecord,
    DocumentResponse,
    DocumentStatus,
)

import firebase_client
from repositories.run_repository import _repo as run_repo

log = structlog.get_logger(__name__)

router = APIRouter()

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/tiff",
    "application/octet-stream",
    "application/x-pdf",
}

def upload_to_storage(run_id: str, source: str, local_path: Path) -> str | None:
    """Upload a file to Firebase Storage and return its public/GCS URL.
    
    Path format: uploads/{run_id}/{source}.pdf
    """
    if not firebase_client.storage_bucket:
        log.warning("storage.disabled", run_id=run_id, source=source)
        return None
    
    try:
        blob_path = f"uploads/{run_id}/{source}.pdf"
        blob = firebase_client.storage_bucket.blob(blob_path)
        blob.upload_from_filename(str(local_path))
        
        # We return the gs:// path or a public URL. Usually for backend-to-backend 
        # or internal use, gs:// is standard. For frontend, a signed URL or 
        # download token is needed. Here we follow the task's implied blob focus.
        gcs_url = f"gs://{firebase_client.storage_bucket.name}/{blob_path}"
        log.info("storage.uploaded", run_id=run_id, source=source, gcs_url=gcs_url)
        return gcs_url
    except Exception as e:
        log.error("storage.upload_failed", run_id=run_id, source=source, error=str(e))
        return None


@router.post(
    "/",
    response_model=DocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload an invoice PDF + bill-of-lading PDF for processing",
)
async def upload_documents(
    invoice_pdf: UploadFile = File(..., description="Commercial invoice (PDF or image)"),
    bl_pdf: UploadFile = File(..., description="Bill of lading (PDF or image)"),
    country: CountryCode = Form(CountryCode.US, description="Jurisdiction for rule validation"),
) -> DocumentResponse:
    """Accept both trade documents in a single multipart request.

    Saves them locally as fallback, then uploads to Firebase Storage.
    Persists metadata in both SQLite and Firestore.
    """
    # Validate content types
    for upload, label in [(invoice_pdf, "invoice_pdf"), (bl_pdf, "bl_pdf")]:
        ct = upload.content_type or ""
        if ct and ct not in ALLOWED_CONTENT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"{label}: unsupported content type '{ct}'",
            )

    run_id = str(uuid.uuid4())
    inv_dest = UPLOAD_DIR / f"{run_id}_invoice.pdf"
    bl_dest  = UPLOAD_DIR / f"{run_id}_bl.pdf"

    inv_content = await invoice_pdf.read()
    bl_content  = await bl_pdf.read()

    # 1. Save locally (Fallback)
    async with aiofiles.open(inv_dest, "wb") as fh:
        await fh.write(inv_content)
    async with aiofiles.open(bl_dest, "wb") as fh:
        await fh.write(bl_content)

    # 2. Upload to Firebase Storage
    inv_gcs_url = upload_to_storage(run_id, "invoice", inv_dest)
    bl_gcs_url  = upload_to_storage(run_id, "bl", bl_dest)

    # 3. Transmit to Firestore (Parallel Persistence now becomes Primary)
    if firebase_client.db:
        try:
            await run_repo.create(
                run_id=run_id,
                invoice_path=str(inv_dest),
                bl_path=str(bl_dest),
                country=country.value,
                invoice_gcs_url=inv_gcs_url,
                bl_gcs_url=bl_gcs_url,
            )
        except Exception as e:
            log.error("firestore.sync_failed", run_id=run_id, error=str(e))

    log.info(
        "documents.uploaded",
        run_id=run_id,
        invoice=invoice_pdf.filename,
        bl=bl_pdf.filename,
        inv_gcs=inv_gcs_url,
        bl_gcs=bl_gcs_url,
    )

    return DocumentResponse(
        id=uuid.UUID(run_id),
        filename=f"{invoice_pdf.filename} + {bl_pdf.filename}",
        country=country,
        status=DocumentStatus.PENDING,
        metadata={
            "run_id":        run_id,
            "invoice_path":  str(inv_dest),
            "bl_path":       str(bl_dest),
            "gcs_invoice_url": inv_gcs_url,
            "gcs_bl_url":      bl_gcs_url,
            "inv_bytes":     len(inv_content),
            "bl_bytes":      len(bl_content),
        },
    )


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Get upload record by run_id",
)
async def get_document(document_id: str) -> DocumentResponse:
    row = await run_repo.get(document_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found")
    
    return DocumentResponse(
        id=uuid.UUID(row.get("run_id", document_id)),
        filename=f"invoice + bl",
        country=CountryCode(row.get("country", "US")),
        status=DocumentStatus.PENDING,
        metadata={
            "invoice_path": row.get("invoice_path"), 
            "bl_path": row.get("bl_path"),
            "gcs_invoice_url": row.get("invoice_gcs_url"),
            "gcs_bl_url": row.get("bl_gcs_url"),
        },
    )


@router.get(
    "/",
    response_model=list[DocumentResponse],
    summary="List all uploads",
)
async def list_documents() -> list[DocumentResponse]:
    rows = await run_repo.list_all()
    return [
        DocumentResponse(
            id=uuid.UUID(r.get("run_id")),
            filename="invoice + bl",
            country=CountryCode(r.get("country", "US")),
            status=DocumentStatus.PENDING,
            metadata={
                "invoice_path": r.get("invoice_path"), 
                "bl_path": r.get("bl_path"),
                "gcs_invoice_url": r.get("invoice_gcs_url"),
                "gcs_bl_url": r.get("bl_gcs_url"),
            },
        )
        for r in rows
    ]
