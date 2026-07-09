"""Routes for URL navigation events. Stored as TimelineEvents (no separate model)."""

from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.timeline_utils import emit_timeline_event

router = APIRouter(prefix="/url-events", tags=["url-events"])


class UrlEventCreate(BaseModel):
    project_id: str
    process_id: str | None = None
    capture_session_id: str | None = None
    from_url: str = ""
    to_url: str


@router.post("", status_code=201)
async def create_url_event(data: UrlEventCreate, db: AsyncSession = Depends(get_db)):
    try:
        domain = urlparse(data.to_url).netloc or data.to_url
    except Exception:
        domain = data.to_url

    event = await emit_timeline_event(
        db,
        project_id=data.project_id,
        process_id=data.process_id,
        capture_session_id=data.capture_session_id,
        event_type="url_change",
        summary=f"Navigated to {domain}",
        metadata={"from_url": data.from_url, "to_url": data.to_url},
    )
    await db.commit()
    return {"id": event.id, "summary": event.summary}
