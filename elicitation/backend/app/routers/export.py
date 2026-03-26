"""Project export/import endpoints — JSON bundle."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    Attachment,
    CaptureSession,
    ClickEvent,
    Document,
    Message,
    Narration,
    Process,
    Project,
    Question,
    Screenshot,
    TimelineEvent,
)
from app.schemas import (
    AttachmentOut,
    CaptureSessionOut,
    ClickEventOut,
    DocumentOut,
    MessageOut,
    NarrationOut,
    ProcessOut,
    ProjectOut,
    QuestionOut,
    ScreenshotOut,
    TimelineEventOut,
)

router = APIRouter(tags=["export"])


@router.get("/projects/{project_id}/export")
async def export_project(project_id: str, db: AsyncSession = Depends(get_db)):
    """Export a project and all associated data as a JSON bundle."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(404, "Project not found")

    # Fetch all related data
    processes = (await db.execute(select(Process).where(Process.project_id == project_id))).scalars().all()
    messages = (await db.execute(select(Message).where(Message.project_id == project_id))).scalars().all()
    narrations = (await db.execute(select(Narration).where(Narration.project_id == project_id))).scalars().all()
    timeline_events = (await db.execute(select(TimelineEvent).where(TimelineEvent.project_id == project_id))).scalars().all()
    click_events = (await db.execute(select(ClickEvent).where(ClickEvent.project_id == project_id))).scalars().all()
    capture_sessions = (await db.execute(select(CaptureSession).where(CaptureSession.project_id == project_id))).scalars().all()
    screenshots = (await db.execute(select(Screenshot).where(Screenshot.project_id == project_id))).scalars().all()
    documents = (await db.execute(select(Document).where(Document.project_id == project_id))).scalars().all()
    attachments = (await db.execute(select(Attachment).where(Attachment.project_id == project_id))).scalars().all()
    questions = (await db.execute(select(Question).where(Question.project_id == project_id))).scalars().all()

    return {
        "export_version": "1.0",
        "project": ProjectOut.model_validate(project).model_dump(mode="json"),
        "processes": [ProcessOut.model_validate(p).model_dump(mode="json") for p in processes],
        "messages": [MessageOut.model_validate(m).model_dump(mode="json") for m in messages],
        "narrations": [NarrationOut.model_validate(n).model_dump(mode="json") for n in narrations],
        "timeline_events": [TimelineEventOut.model_validate(te).model_dump(mode="json") for te in timeline_events],
        "click_events": [ClickEventOut.model_validate(ce).model_dump(mode="json") for ce in click_events],
        "capture_sessions": [CaptureSessionOut.model_validate(cs).model_dump(mode="json") for cs in capture_sessions],
        "screenshots": [ScreenshotOut.model_validate(s).model_dump(mode="json") for s in screenshots],
        "documents": [DocumentOut.model_validate(d).model_dump(mode="json") for d in documents],
        "attachments": [AttachmentOut.model_validate(a).model_dump(mode="json") for a in attachments],
        "questions": [QuestionOut.model_validate(q).model_dump(mode="json") for q in questions],
    }


# ── Import ──────────────────────────────────────────────────────────────────


class ImportBundle(BaseModel):
    """Accepts the export JSON bundle and recreates the project."""
    export_version: str = "1.0"
    project: dict
    processes: list[dict] = []
    messages: list[dict] = []
    narrations: list[dict] = []
    timeline_events: list[dict] = []
    click_events: list[dict] = []
    capture_sessions: list[dict] = []
    screenshots: list[dict] = []
    documents: list[dict] = []
    attachments: list[dict] = []
    questions: list[dict] = []


def _ts(val):
    """Parse an ISO timestamp string to datetime, or return current UTC time."""
    if not val:
        return datetime.now(timezone.utc)
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(val)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


@router.post("/projects/import", response_model=ProjectOut, status_code=201)
async def import_project(bundle: ImportBundle, db: AsyncSession = Depends(get_db)):
    """Import a project from an export bundle. Generates new IDs for all records."""
    p = bundle.project

    # ID mapping: old_id -> new_id (so FK references stay consistent)
    id_map = {}

    def new_id(old_id: str | None) -> str:
        if not old_id:
            return str(uuid.uuid4())
        if old_id not in id_map:
            id_map[old_id] = str(uuid.uuid4())
        return id_map[old_id]

    # 1. Create project
    project_id = new_id(p.get("id"))
    project = Project(
        id=project_id,
        name=p.get("name", "Imported Project"),
        description=p.get("description", ""),
        base_url=p.get("base_url", ""),
        status=p.get("status", "active"),
    )
    db.add(project)

    # 2. Processes
    for proc in bundle.processes:
        db.add(Process(
            id=new_id(proc.get("id")),
            project_id=project_id,
            name=proc.get("name", ""),
            description=proc.get("description", ""),
            base_url=proc.get("base_url", ""),
            status=proc.get("status", "active"),
        ))

    # 3. Messages
    for m in bundle.messages:
        db.add(Message(
            id=new_id(m.get("id")),
            project_id=project_id,
            process_id=id_map.get(m.get("process_id")),
            role=m.get("role", "human"),
            content_type=m.get("content_type", "text"),
            content=m.get("content", ""),
            url=m.get("url", ""),
            timestamp=_ts(m.get("timestamp")),
        ))

    # 4. Capture sessions
    for cs in bundle.capture_sessions:
        db.add(CaptureSession(
            id=new_id(cs.get("id")),
            process_id=id_map.get(cs.get("process_id"), project_id),
            project_id=project_id,
            status=cs.get("status", "stopped"),
            click_tracking=cs.get("click_tracking", True),
            url_monitoring=cs.get("url_monitoring", True),
            har_capture=cs.get("har_capture", True),
            voice_mode=cs.get("voice_mode", "narrate"),
            started_at=_ts(cs.get("started_at")),
            stopped_at=_ts(cs.get("stopped_at")),
        ))

    # 5. Narrations
    for n in bundle.narrations:
        db.add(Narration(
            id=new_id(n.get("id")),
            project_id=project_id,
            process_id=id_map.get(n.get("process_id")),
            capture_session_id=id_map.get(n.get("capture_session_id")),
            content=n.get("content", ""),
            url=n.get("url", ""),
            timestamp=_ts(n.get("timestamp")),
        ))

    # 6. Click events
    for ce in bundle.click_events:
        db.add(ClickEvent(
            id=new_id(ce.get("id")),
            project_id=project_id,
            process_id=id_map.get(ce.get("process_id")),
            capture_session_id=id_map.get(ce.get("capture_session_id")),
            event_type=ce.get("event_type", "click"),
            url=ce.get("url", ""),
            tag_name=ce.get("tag_name", ""),
            element_id=ce.get("element_id"),
            class_name=ce.get("class_name"),
            text_content=ce.get("text_content"),
            href=ce.get("href"),
            selector=ce.get("selector", ""),
            x=ce.get("x", 0),
            y=ce.get("y", 0),
            input_type=ce.get("input_type"),
            value=ce.get("value"),
            field_name=ce.get("field_name"),
            timestamp=_ts(ce.get("timestamp")),
        ))

    # 7. Timeline events
    for te in bundle.timeline_events:
        db.add(TimelineEvent(
            id=new_id(te.get("id")),
            project_id=project_id,
            process_id=id_map.get(te.get("process_id")),
            capture_session_id=id_map.get(te.get("capture_session_id")),
            event_type=te.get("event_type", ""),
            source_id=id_map.get(te.get("source_id")),
            summary=te.get("summary", ""),
            metadata_json=te.get("metadata_json", "{}"),
            timestamp=_ts(te.get("timestamp")),
        ))

    # 8. Documents (note: file references won't work but metadata is preserved)
    for d in bundle.documents:
        db.add(Document(
            id=new_id(d.get("id")),
            project_id=project_id,
            process_id=id_map.get(d.get("process_id")),
            title=d.get("title", ""),
            content=d.get("content", ""),
            doc_type=d.get("doc_type", "user"),
            is_canonical=d.get("is_canonical", False),
        ))

    # 9. Screenshots (metadata only — file paths from original won't exist)
    for s in bundle.screenshots:
        db.add(Screenshot(
            id=new_id(s.get("id")),
            project_id=project_id,
            process_id=id_map.get(s.get("process_id")),
            file_path=s.get("file_path", ""),
            url=s.get("url", ""),
            timestamp=_ts(s.get("timestamp")),
        ))

    # 10. Attachments (metadata only)
    for a in bundle.attachments:
        db.add(Attachment(
            id=new_id(a.get("id")),
            project_id=project_id,
            process_id=id_map.get(a.get("process_id")),
            filename=a.get("filename", ""),
            file_path=a.get("file_path", ""),
            content_type=a.get("content_type", ""),
            file_size=a.get("file_size", 0),
        ))

    # 11. Questions
    for q in bundle.questions:
        db.add(Question(
            id=new_id(q.get("id")),
            project_id=project_id,
            process_id=id_map.get(q.get("process_id")),
            content=q.get("content", ""),
            answer=q.get("answer", ""),
            status=q.get("status", "open"),
            context_url=q.get("context_url", ""),
        ))

    await db.commit()
    await db.refresh(project)
    return project
