"""Upload routes — accept a matched invoice + bill-of-lading PDF pair."""
from __future__ import annotations

import uuid
from pathlib import Path

import aiofiles
import structlog
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from sqlmodel import Session, select

from workflow_db import db
from db import UploadRunRow, engine
from models import (
    CountryCode,
    DocumentRecord,
    DocumentResponse,
    DocumentStatus,
)

log = structlog.get_logger(__name__)

router = APIRouter()

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/tiff",
    # Windows sometimes sends these for PDFs
    "application/octet-stream",
    "application/x-pdf",
}


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

    Saves them as  uploads/{run_id}_invoice.pdf  and  uploads/{run_id}_bl.pdf
    then writes an UploadRunRow to SQLite so the workflow route can look up the
    exact paths without relying on filename conventions.
    """
    # Validate content types (be lenient — browsers vary)
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

    async with aiofiles.open(inv_dest, "wb") as fh:
        await fh.write(inv_content)

    async with aiofiles.open(bl_dest, "wb") as fh:
        await fh.write(bl_content)

    # Persist paths so the workflow route can find them
    with db.session() as session:

        row = UploadRunRow(
            run_id=run_id,
            invoice_path=str(inv_dest),
            bl_path=str(bl_dest),
            country=country.value,
            status="uploaded",
        )
        session.add(row)
        session.commit()
        session.refresh(row)

    log.info(
        "documents.uploaded",
        run_id=run_id,
        invoice=invoice_pdf.filename,
        bl=bl_pdf.filename,
        country=country,
        inv_bytes=len(inv_content),
        bl_bytes=len(bl_content),
    )

    # Return DocumentResponse using run_id as the document id so the frontend
    # can pass it unchanged to POST /workflow/
    return DocumentResponse(
        id=uuid.UUID(run_id),
        filename=f"{invoice_pdf.filename} + {bl_pdf.filename}",
        country=country,
        status=DocumentStatus.PENDING,
        metadata={
            "run_id":        run_id,
            "invoice_path":  str(inv_dest),
            "bl_path":       str(bl_dest),
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
    with db.session() as session:
        row = session.get(UploadRunRow, document_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found")
    return DocumentResponse(
        id=uuid.UUID(row.run_id),
        filename=f"invoice + bl",
        country=CountryCode(row.country),
        status=DocumentStatus.PENDING,
        metadata={"invoice_path": row.invoice_path, "bl_path": row.bl_path},
    )


@router.get(
    "/",
    response_model=list[DocumentResponse],
    summary="List all uploads",
)
async def list_documents() -> list[DocumentResponse]:
    with db.session() as session:
        rows = session.exec(select(UploadRunRow)).all()
    return [
        DocumentResponse(
            id=uuid.UUID(r.run_id),
            filename="invoice + bl",
            country=CountryCode(r.country),
            status=DocumentStatus.PENDING,
            metadata={"invoice_path": r.invoice_path, "bl_path": r.bl_path},
        )
        for r in rows
    ]
