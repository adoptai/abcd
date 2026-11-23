"""Routes for screenshots."""

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Screenshot
from app.schemas import ScreenshotOut
from app.timeline_utils import emit_timeline_event

router = APIRouter(prefix="/screenshots", tags=["screenshots"])


@router.post("", response_model=ScreenshotOut, status_code=201)
async def upload_screenshot(
    file: UploadFile,
    project_id: str | None = Form(None),
    process_id: str | None = Form(None),
    url: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    screenshots_dir = Path(settings.data_dir) / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid.uuid4()}.png"
    file_path = screenshots_dir / filename

    content = await file.read()
    file_path.write_bytes(content)

    screenshot = Screenshot(
        project_id=project_id,
        process_id=process_id,
        file_path=str(file_path),
        url=url,
    )
    db.add(screenshot)
    await db.commit()
    await db.refresh(screenshot)

    # Emit timeline event
    if screenshot.project_id:
        try:
            domain = ""
            if url:
                try:
                    domain = url.split("//")[1].split("/")[0]
                except Exception:
                    domain = url[:50]
            await emit_timeline_event(
                db,
                project_id=screenshot.project_id,
                process_id=screenshot.process_id,
                event_type="screenshot",
                source_id=screenshot.id,
                summary=f"Screenshot captured{' of ' + domain if domain else ''}",
                metadata={"url": url},
            )
            await db.commit()
        except Exception:
            pass  # Don't fail screenshot upload for timeline

    return screenshot


@router.get("", response_model=list[ScreenshotOut])
async def list_screenshots(
    project_id: str | None = Query(None),
    process_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Screenshot)
    if project_id:
        stmt = stmt.where(Screenshot.project_id == project_id)
    if process_id:
        stmt = stmt.where(Screenshot.process_id == process_id)
    stmt = stmt.order_by(Screenshot.timestamp.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{screenshot_id}/image")
async def get_screenshot_image(screenshot_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Screenshot).where(Screenshot.id == screenshot_id))
    screenshot = result.scalar_one_or_none()
    if not screenshot:
        raise HTTPException(404, "Screenshot not found")
    file_path = Path(screenshot.file_path)
    if not file_path.exists():
        raise HTTPException(404, "Screenshot file not found")
    return FileResponse(file_path, media_type="image/png")
