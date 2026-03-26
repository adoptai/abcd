"""Routes for voice narrations (think-aloud annotations)."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Narration
from app.schemas import NarrationCreate, NarrationOut
from app.timeline_utils import emit_timeline_event

router = APIRouter(prefix="/narrations", tags=["narrations"])


@router.post("", response_model=NarrationOut, status_code=201)
async def create_narration(data: NarrationCreate, db: AsyncSession = Depends(get_db)):
    narration = Narration(
        project_id=data.project_id,
        process_id=data.process_id,
        capture_session_id=data.capture_session_id,
        content=data.content,
        url=data.url,
    )
    db.add(narration)
    await db.commit()
    await db.refresh(narration)

    # Emit timeline event — store full narration text, not truncated
    try:
        await emit_timeline_event(
            db,
            project_id=data.project_id,
            process_id=data.process_id,
            capture_session_id=data.capture_session_id,
            event_type="narration",
            source_id=narration.id,
            summary=data.content,
            metadata={"url": data.url},
        )
        await db.commit()
    except Exception:
        pass

    return narration


@router.get("", response_model=list[NarrationOut])
async def list_narrations(
    project_id: str | None = Query(None),
    process_id: str | None = Query(None),
    capture_session_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Narration)
    if project_id:
        stmt = stmt.where(Narration.project_id == project_id)
    if process_id:
        stmt = stmt.where(Narration.process_id == process_id)
    if capture_session_id:
        stmt = stmt.where(Narration.capture_session_id == capture_session_id)
    stmt = stmt.order_by(Narration.timestamp.asc()).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{narration_id}", response_model=NarrationOut)
async def get_narration(narration_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Narration).where(Narration.id == narration_id))
    narration = result.scalar_one_or_none()
    if not narration:
        raise HTTPException(404, "Narration not found")
    return narration
