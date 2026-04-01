"""Routes for login-recording sessions."""

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import CaptureSession, ClickEvent, LoginDraft, Process, Project, TimelineEvent
from app.schemas import LoginSessionCreate, LoginSessionOut
from app.timeline_utils import emit_timeline_event
from app import login_profile_generator

router = APIRouter(prefix="/login-sessions", tags=["login-sessions"])


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

async def _get_login_session(session_id: str, db: AsyncSession) -> CaptureSession:
    result = await db.execute(
        select(CaptureSession).where(
            CaptureSession.id == session_id,
            CaptureSession.capture_purpose == "login_recording",
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(404, "Login session not found")
    return session


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@router.post("", response_model=LoginSessionOut, status_code=201)
async def create_login_session(data: LoginSessionCreate, db: AsyncSession = Depends(get_db)):
    """
    Create a login-recording session.

    Optionally accepts an existing project_id/process_id.
    If not provided, creates a new project and process automatically.
    """
    process_id = data.process_id
    project_id = data.project_id

    if not process_id:
        # Create ephemeral project + process for this recording
        proj = Project(
            name=f"{data.app_name} Login Recording",
            description=f"Auto-created for login recording of {data.app_name}",
            base_url=data.login_url,
        )
        db.add(proj)
        await db.flush()  # get proj.id without committing

        proc = Process(
            project_id=proj.id,
            name="Login Flow",
            description=f"Login flow recording for {data.app_name}",
            base_url=data.login_url,
        )
        db.add(proc)
        await db.flush()

        project_id = proj.id
        process_id = proc.id

    session = CaptureSession(
        process_id=process_id,
        project_id=project_id,
        capture_purpose="login_recording",
        login_url=data.login_url,
        app_name=data.app_name,
        click_tracking=True,
        url_monitoring=True,
        har_capture=True,
        voice_mode="narrate",
        narration=False,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    await emit_timeline_event(
        db,
        project_id=project_id,
        process_id=process_id,
        capture_session_id=session.id,
        event_type="login_recording_created",
        source_id=session.id,
        summary=f"Login recording session created for {data.app_name}",
    )
    await db.commit()

    return session


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

@router.get("", response_model=list[LoginSessionOut])
async def list_login_sessions(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(CaptureSession)
        .where(CaptureSession.capture_purpose == "login_recording")
        .order_by(CaptureSession.started_at.desc().nullslast())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------

@router.get("/{session_id}", response_model=LoginSessionOut)
async def get_login_session(session_id: str, db: AsyncSession = Depends(get_db)):
    return await _get_login_session(session_id, db)


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------

@router.put("/{session_id}/start", response_model=LoginSessionOut)
async def start_login_session(session_id: str, db: AsyncSession = Depends(get_db)):
    from datetime import datetime, timezone
    session = await _get_login_session(session_id, db)
    session.status = "capturing"
    session.started_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(session)
    return session


# ---------------------------------------------------------------------------
# Complete (stop)
# ---------------------------------------------------------------------------

@router.put("/{session_id}/complete", response_model=LoginSessionOut)
async def complete_login_session(session_id: str, db: AsyncSession = Depends(get_db)):
    from datetime import datetime, timezone
    session = await _get_login_session(session_id, db)
    session.status = "stopped"
    session.stopped_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(session)

    await emit_timeline_event(
        db,
        project_id=session.project_id,
        process_id=session.process_id,
        capture_session_id=session.id,
        event_type="login_recording_complete",
        source_id=session.id,
        summary=f"Login recording completed for {session.app_name or 'app'}",
    )
    await db.commit()
    await db.refresh(session)
    return session


# ---------------------------------------------------------------------------
# Analyze (generate bundle)
# ---------------------------------------------------------------------------

@router.post("/{session_id}/analyze")
async def analyze_login_session(
    session_id: str,
    source_capture_session_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Run the login profile generator on this session's recorded events.
    Stores the result in LoginDraft and returns the bundle.

    If the login session itself has no click events (e.g. the user recorded
    via a regular capture session in the same process), the endpoint falls
    back to the most recent capture session in the same process that has
    events on the login page.  Pass source_capture_session_id to override.
    """
    session = await _get_login_session(session_id, db)

    # Resolve which capture session actually holds the events
    source_id = source_capture_session_id or session_id

    # Load click events (chronological)
    click_result = await db.execute(
        select(ClickEvent)
        .where(ClickEvent.capture_session_id == source_id)
        .order_by(ClickEvent.timestamp.asc())
    )
    events_check = click_result.scalars().all()

    # Fallback: no events on the login session → look at sibling capture sessions
    if not events_check and source_id == session_id:
        sibling_result = await db.execute(
            select(ClickEvent)
            .where(
                ClickEvent.process_id == session.process_id,
                ClickEvent.url == session.login_url,
            )
            .order_by(ClickEvent.timestamp.asc())
        )
        events_check = sibling_result.scalars().all()

    click_events = [
        {c: getattr(ev, c) for c in [
            "id", "event_type", "url", "tag_name", "element_id", "class_name",
            "text_content", "href", "selector", "x", "y", "input_type",
            "value", "field_name", "field_role", "is_redacted", "autocomplete",
            "placeholder", "aria_label", "role_attr", "data_attrs_json",
            "timestamp",
        ]}
        for ev in events_check
    ]

    # Load URL transition events (chronological) — check login session and siblings
    url_result = await db.execute(
        select(TimelineEvent)
        .where(
            TimelineEvent.process_id == session.process_id,
            TimelineEvent.event_type == "url_change",
        )
        .order_by(TimelineEvent.timestamp.asc())
    )
    url_events = [
        {c: getattr(ev, c) for c in [
            "id", "event_type", "summary", "metadata_json", "timestamp"
        ]}
        for ev in url_result.scalars().all()
    ]

    # Load HAR if available
    har: dict | None = None
    if session.har_file_path:
        har_path = Path(session.har_file_path)
        if har_path.exists():
            try:
                har = json.loads(har_path.read_text())
            except Exception:
                pass

    # Build session dict
    session_dict = {
        "id": session.id,
        "app_name": session.app_name,
        "login_url": session.login_url,
        "status": session.status,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "stopped_at": session.stopped_at.isoformat() if session.stopped_at else None,
    }

    # Run generator
    bundle = login_profile_generator.generate(
        session=session_dict,
        click_events=click_events,
        url_events=url_events,
        har=har,
    )

    # Store or update LoginDraft
    existing = await db.execute(
        select(LoginDraft).where(LoginDraft.capture_session_id == session_id)
    )
    draft = existing.scalar_one_or_none()
    if draft:
        draft.bundle_json = json.dumps(bundle)
        draft.updated_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    else:
        draft = LoginDraft(
            capture_session_id=session_id,
            bundle_json=json.dumps(bundle),
        )
        db.add(draft)

    await db.commit()

    return bundle


# ---------------------------------------------------------------------------
# Get bundle
# ---------------------------------------------------------------------------

@router.get("/{session_id}/bundle")
async def get_login_bundle(session_id: str, db: AsyncSession = Depends(get_db)):
    """Return the previously generated bundle for this session."""
    await _get_login_session(session_id, db)  # ensure session exists

    result = await db.execute(
        select(LoginDraft).where(LoginDraft.capture_session_id == session_id)
    )
    draft = result.scalar_one_or_none()
    if not draft:
        raise HTTPException(
            404,
            "No bundle generated yet — call POST /login-sessions/{id}/analyze first",
        )

    try:
        return json.loads(draft.bundle_json)
    except Exception:
        raise HTTPException(500, "Bundle JSON is malformed")
