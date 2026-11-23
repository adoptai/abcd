"""Routes for file attachments on projects/processes."""

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Attachment
from app.schemas import AttachmentOut

router = APIRouter(tags=["attachments"])

ATTACHMENTS_DIR = Path(settings.data_dir) / "attachments"


@router.post("/projects/{project_id}/attachments", response_model=AttachmentOut, status_code=201)
async def upload_attachment(
    project_id: str,
    file: UploadFile = File(...),
    process_id: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    # Save file to disk
    ext = Path(file.filename or "file").suffix
    unique_name = f"{uuid.uuid4()}{ext}"
    file_path = ATTACHMENTS_DIR / unique_name
    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)

    contents = await file.read()
    file_path.write_bytes(contents)

    attachment = Attachment(
        project_id=project_id,
        process_id=process_id,
        filename=file.filename or "file",
        file_path=str(file_path),
        content_type=file.content_type or "",
        file_size=len(contents),
    )
    db.add(attachment)
    await db.commit()
    await db.refresh(attachment)
    return attachment


@router.get("/projects/{project_id}/attachments", response_model=list[AttachmentOut])
async def list_attachments(
    project_id: str,
    process_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Attachment).where(Attachment.project_id == project_id)
    if process_id:
        stmt = stmt.where(Attachment.process_id == process_id)
    stmt = stmt.order_by(Attachment.created_at.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/attachments/{attachment_id}/download")
async def download_attachment(attachment_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Attachment).where(Attachment.id == attachment_id))
    attachment = result.scalar_one_or_none()
    if not attachment:
        raise HTTPException(404, "Attachment not found")

    file_path = Path(attachment.file_path)
    if not file_path.exists():
        raise HTTPException(404, "Attachment file missing from disk")

    return FileResponse(
        path=str(file_path),
        filename=attachment.filename,
        media_type=attachment.content_type or "application/octet-stream",
    )


@router.delete("/attachments/{attachment_id}", status_code=204)
async def delete_attachment(attachment_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Attachment).where(Attachment.id == attachment_id))
    attachment = result.scalar_one_or_none()
    if not attachment:
        raise HTTPException(404, "Attachment not found")

    # Delete file from disk
    file_path = Path(attachment.file_path)
    if file_path.exists():
        os.remove(file_path)

    await db.delete(attachment)
    await db.commit()
