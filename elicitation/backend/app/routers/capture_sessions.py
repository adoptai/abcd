"""Routes for capture sessions (replaces recordings)."""

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import CaptureSession, Process
from app.schemas import CaptureSessionCreate, CaptureSessionOut, CaptureSessionUpdate
from app.timeline_utils import emit_timeline_event

router = APIRouter(tags=["capture-sessions"])


@router.post("/processes/{process_id}/capture-sessions", response_model=CaptureSessionOut, status_code=201)
async def create_capture_session(
    process_id: str,
    data: CaptureSessionCreate = CaptureSessionCreate(),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Process).where(Process.id == process_id))
    process = result.scalar_one_or_none()
    if not process:
        raise HTTPException(404, "Process not found")
    session = CaptureSession(
        process_id=process_id,
        project_id=process.project_id,
        click_tracking=data.click_tracking,
        url_monitoring=data.url_monitoring,
        har_capture=data.har_capture,
        voice_mode=data.voice_mode,
        narration=data.narration,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


@router.put("/capture-sessions/{session_id}/start", response_model=CaptureSessionOut)
async def start_capture_session(session_id: str, db: AsyncSession = Depends(get_db)):
    session = await _get_session(session_id, db)
    session.status = "capturing"
    session.started_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(session)

    try:
        await emit_timeline_event(
            db,
            project_id=session.project_id,
            process_id=session.process_id,
            capture_session_id=session.id,
            event_type="capture_start",
            source_id=session.id,
            summary="Capture session started",
        )
        await db.commit()
    except Exception:
        pass

    return session


@router.put("/capture-sessions/{session_id}/stop", response_model=CaptureSessionOut)
async def stop_capture_session(session_id: str, db: AsyncSession = Depends(get_db)):
    session = await _get_session(session_id, db)
    session.status = "stopped"
    session.stopped_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(session)

    try:
        await emit_timeline_event(
            db,
            project_id=session.project_id,
            process_id=session.process_id,
            capture_session_id=session.id,
            event_type="capture_stop",
            source_id=session.id,
            summary="Capture session stopped",
        )
        await db.commit()
    except Exception:
        pass

    return session


@router.put("/capture-sessions/{session_id}/pause", response_model=CaptureSessionOut)
async def pause_capture_session(session_id: str, db: AsyncSession = Depends(get_db)):
    session = await _get_session(session_id, db)
    session.status = "paused"
    await db.commit()
    await db.refresh(session)
    return session


@router.patch("/capture-sessions/{session_id}/settings", response_model=CaptureSessionOut)
async def update_capture_session_settings(
    session_id: str,
    data: CaptureSessionUpdate,
    db: AsyncSession = Depends(get_db),
):
    session = await _get_session(session_id, db)
    if data.click_tracking is not None:
        session.click_tracking = data.click_tracking
    if data.url_monitoring is not None:
        session.url_monitoring = data.url_monitoring
    if data.har_capture is not None:
        session.har_capture = data.har_capture
    if data.voice_mode is not None:
        session.voice_mode = data.voice_mode
    if data.narration is not None:
        session.narration = data.narration
    await db.commit()
    await db.refresh(session)
    return session


@router.get("/processes/{process_id}/capture-sessions", response_model=list[CaptureSessionOut])
async def list_process_capture_sessions(process_id: str, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(CaptureSession)
        .where(CaptureSession.process_id == process_id)
        .order_by(CaptureSession.started_at.desc().nullslast())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/projects/{project_id}/capture-sessions", response_model=list[CaptureSessionOut])
async def list_project_capture_sessions(project_id: str, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(CaptureSession)
        .where(CaptureSession.project_id == project_id)
        .order_by(CaptureSession.started_at.desc().nullslast())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/capture-sessions/{session_id}", response_model=CaptureSessionOut)
async def get_capture_session(session_id: str, db: AsyncSession = Depends(get_db)):
    return await _get_session(session_id, db)


@router.get("/capture-sessions/{session_id}/har")
async def download_har(session_id: str, db: AsyncSession = Depends(get_db)):
    session = await _get_session(session_id, db)
    if not session.har_file_path:
        raise HTTPException(404, "No HAR file for this capture session")
    file_path = Path(session.har_file_path)
    if not file_path.exists():
        raise HTTPException(404, "HAR file not found on disk")
    return FileResponse(
        path=str(file_path),
        media_type="application/json",
        filename=f"capture-{session_id}.har",
    )


@router.post("/capture-sessions/{session_id}/har", response_model=CaptureSessionOut)
async def upload_har(session_id: str, file: UploadFile, db: AsyncSession = Depends(get_db)):
    session = await _get_session(session_id, db)

    har_dir = Path(settings.data_dir) / "har"
    har_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid.uuid4()}.json"
    file_path = har_dir / filename

    content = await file.read()
    file_path.write_bytes(content)

    session.har_file_path = str(file_path)
    await db.commit()
    await db.refresh(session)
    return session


@router.delete("/capture-sessions/{session_id}", status_code=204)
async def delete_capture_session(session_id: str, db: AsyncSession = Depends(get_db)):
    session = await _get_session(session_id, db)
    # Remove HAR file from disk
    if session.har_file_path:
        try:
            Path(session.har_file_path).unlink(missing_ok=True)
        except Exception:
            pass
    await db.delete(session)
    await db.commit()


async def _get_session(session_id: str, db: AsyncSession) -> CaptureSession:
    result = await db.execute(select(CaptureSession).where(CaptureSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(404, "Capture session not found")
    return session
