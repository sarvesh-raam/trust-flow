"""Upload routes — accept a matched invoice + bill-of-lading PDF pair."""
from __future__ import annotations

import mimetypes
import os
import uuid
from pathlib import Path

import aiofiles
import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from db import UPLOAD_DIR, UploadRunRow, engine
from dependencies import get_current_user
from models import (
    CountryCode,
    DocumentResponse,
    DocumentStatus,
)

log = structlog.get_logger(__name__)

router = APIRouter()

MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "25")) * 1024 * 1024

# Detected from the file's leading bytes — the browser's content type is not trusted.
_MAGIC: list[tuple[bytes, str]] = [
    (b"%PDF-", ".pdf"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"II*\x00", ".tif"),
    (b"MM\x00*", ".tif"),
]


def _detect_extension(head: bytes) -> str | None:
    for magic, ext in _MAGIC:
        if head.startswith(magic):
            return ext
    return None


async def _read_limited(upload: UploadFile, label: str) -> bytes:
    content = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"{label}: file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
        )
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{label}: file is empty")
    return content


def _get_owned_row(session: Session, run_id: str, user_id: str) -> UploadRunRow:
    row = session.get(UploadRunRow, run_id)
    # 404 (not 403) so other users' run ids are not confirmed to exist.
    if not row or row.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found")
    return row


def _to_response(row: UploadRunRow, filename: str = "invoice + bl") -> DocumentResponse:
    return DocumentResponse(
        id=uuid.UUID(row.run_id),
        filename=filename,
        country=CountryCode(row.country),
        status=DocumentStatus.PENDING,
        metadata={"run_id": row.run_id},
    )


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
    user_id: str = Depends(get_current_user),
) -> DocumentResponse:
    """Accept both trade documents in a single multipart request.

    Saves each file as uploads/<run_id>_<invoice|bl>.<ext>, then writes an
    UploadRunRow so the workflow route can look up the exact paths.
    """
    inv_content = await _read_limited(invoice_pdf, "invoice_pdf")
    bl_content  = await _read_limited(bl_pdf, "bl_pdf")

    inv_ext = _detect_extension(inv_content[:8])
    bl_ext  = _detect_extension(bl_content[:8])
    for ext, label in [(inv_ext, "invoice_pdf"), (bl_ext, "bl_pdf")]:
        if ext is None:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"{label}: must be a PDF, PNG, JPEG or TIFF file",
            )

    run_id = str(uuid.uuid4())

    inv_dest = UPLOAD_DIR / f"{run_id}_invoice{inv_ext}"
    bl_dest  = UPLOAD_DIR / f"{run_id}_bl{bl_ext}"

    async with aiofiles.open(inv_dest, "wb") as fh:
        await fh.write(inv_content)

    async with aiofiles.open(bl_dest, "wb") as fh:
        await fh.write(bl_content)

    # Persist paths so the workflow route can find them
    with Session(engine) as session:
        row = UploadRunRow(
            run_id=run_id,
            user_id=user_id,
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
    return _to_response(row, filename=f"{invoice_pdf.filename} + {bl_pdf.filename}")


@router.get(
    "/{document_id}/file/{kind}",
    summary="Download an uploaded invoice or bill of lading (owner only)",
)
async def get_document_file(
    document_id: str,
    kind: str,
    user_id: str = Depends(get_current_user),
) -> FileResponse:
    if kind not in ("invoice", "bl"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown file kind")
    with Session(engine) as session:
        row = _get_owned_row(session, document_id, user_id)
    path = Path(row.invoice_path if kind == "invoice" else row.bl_path)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File missing on disk")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Get upload record by run_id",
)
async def get_document(document_id: str, user_id: str = Depends(get_current_user)) -> DocumentResponse:
    with Session(engine) as session:
        row = _get_owned_row(session, document_id, user_id)
    return _to_response(row)


@router.get(
    "/",
    response_model=list[DocumentResponse],
    summary="List the current user's uploads",
)
async def list_documents(user_id: str = Depends(get_current_user)) -> list[DocumentResponse]:
    with Session(engine) as session:
        rows = session.exec(select(UploadRunRow).where(UploadRunRow.user_id == user_id)).all()
    return [_to_response(r) for r in rows]
