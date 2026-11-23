"""Routes for the unified timeline."""

import json
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import CaptureSession, ClickEvent, Narration, TimelineEvent
from app.schemas import ClickEventOut, TimelineEventCreate, TimelineEventOut

router = APIRouter(prefix="/timeline", tags=["timeline"])


@router.get("", response_model=list[TimelineEventOut])
async def get_timeline(
    project_id: str = Query(...),
    process_id: str | None = Query(None),
    capture_session_id: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(TimelineEvent).where(TimelineEvent.project_id == project_id)
    if process_id:
        stmt = stmt.where(TimelineEvent.process_id == process_id)
    if capture_session_id:
        stmt = stmt.where(TimelineEvent.capture_session_id == capture_session_id)
    stmt = stmt.order_by(TimelineEvent.timestamp.asc()).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=TimelineEventOut, status_code=201)
async def create_timeline_event(data: TimelineEventCreate, db: AsyncSession = Depends(get_db)):
    event = TimelineEvent(
        project_id=data.project_id,
        process_id=data.process_id,
        capture_session_id=data.capture_session_id,
        event_type=data.event_type,
        source_id=data.source_id,
        summary=data.summary,
        metadata_json=data.metadata_json,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


@router.get("/download")
async def download_timeline(
    project_id: str = Query(...),
    process_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Download the full timeline, click/interaction events, and HAR data as a JSON bundle."""
    # Timeline events
    stmt = select(TimelineEvent).where(TimelineEvent.project_id == project_id)
    if process_id:
        stmt = stmt.where(TimelineEvent.process_id == process_id)
    stmt = stmt.order_by(TimelineEvent.timestamp.asc())
    timeline_events = (await db.execute(stmt)).scalars().all()

    # Click/interaction events
    click_stmt = select(ClickEvent).where(ClickEvent.project_id == project_id)
    if process_id:
        click_stmt = click_stmt.where(ClickEvent.process_id == process_id)
    click_stmt = click_stmt.order_by(ClickEvent.timestamp.asc())
    click_events = (await db.execute(click_stmt)).scalars().all()

    # Capture sessions with HAR files
    cs_stmt = select(CaptureSession).where(CaptureSession.project_id == project_id)
    if process_id:
        cs_stmt = cs_stmt.where(CaptureSession.process_id == process_id)
    capture_sessions = (await db.execute(cs_stmt)).scalars().all()

    # Load HAR files
    har_logs = []
    for cs in capture_sessions:
        if cs.har_file_path:
            try:
                har_data = json.loads(Path(cs.har_file_path).read_text())
                har_logs.append({
                    "capture_session_id": cs.id,
                    "started_at": cs.started_at.isoformat() if cs.started_at else None,
                    "stopped_at": cs.stopped_at.isoformat() if cs.stopped_at else None,
                    "har": har_data,
                })
            except Exception:
                pass

    bundle = {
        "project_id": project_id,
        "process_id": process_id,
        "timeline_events": [
            TimelineEventOut.model_validate(te).model_dump(mode="json")
            for te in timeline_events
        ],
        "interaction_events": [
            ClickEventOut.model_validate(ce).model_dump(mode="json")
            for ce in click_events
        ],
        "har_logs": har_logs,
    }

    return JSONResponse(
        content=bundle,
        headers={
            "Content-Disposition": f'attachment; filename="timeline-{process_id or project_id}.json"',
        },
    )


@router.delete("", status_code=204)
async def clear_timeline(
    project_id: str = Query(...),
    process_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Clear all timeline events, click events, and narrations for a project/process."""
    # Delete timeline events
    stmt = delete(TimelineEvent).where(TimelineEvent.project_id == project_id)
    if process_id:
        stmt = stmt.where(TimelineEvent.process_id == process_id)
    await db.execute(stmt)

    # Delete click events
    click_stmt = delete(ClickEvent).where(ClickEvent.project_id == project_id)
    if process_id:
        click_stmt = click_stmt.where(ClickEvent.process_id == process_id)
    await db.execute(click_stmt)

    # Delete narrations
    nar_stmt = delete(Narration).where(Narration.project_id == project_id)
    if process_id:
        nar_stmt = nar_stmt.where(Narration.process_id == process_id)
    await db.execute(nar_stmt)

    await db.commit()
