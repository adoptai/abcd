"""Routes for click events captured by the content script."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ClickEvent
from app.schemas import ClickEventCreate, ClickEventOut
from app.timeline_utils import emit_timeline_event

router = APIRouter(prefix="/clicks", tags=["clicks"])


@router.post("", response_model=ClickEventOut, status_code=201)
async def create_click(data: ClickEventCreate, db: AsyncSession = Depends(get_db)):
    click = ClickEvent(
        project_id=data.project_id,
        process_id=data.process_id,
        capture_session_id=data.capture_session_id,
        event_type=data.event_type,
        url=data.url,
        tag_name=data.tag_name,
        element_id=data.element_id,
        class_name=data.class_name,
        text_content=data.text_content,
        href=data.href,
        selector=data.selector,
        x=data.x,
        y=data.y,
        input_type=data.input_type,
        value=data.value,
        field_name=data.field_name,
    )
    if data.timestamp:
        click.timestamp = data.timestamp
    db.add(click)
    await db.commit()
    await db.refresh(click)

    # Emit timeline event with event-type-aware summary
    if data.project_id:
        try:
            label = data.field_name or data.selector or data.tag_name or "element"
            val_preview = f' "{data.value[:40]}"' if data.value else ""
            et = data.event_type or "click"
            if et == "input":
                summary = f"Typed in {label}{val_preview}"
                tl_type = "input"
            elif et == "change":
                summary = f"Changed {label} to{val_preview}"
                tl_type = "change"
            elif et == "submit":
                summary = f"Submitted form {label}"
                tl_type = "submit"
            else:
                text_preview = f' "{data.text_content[:30]}"' if data.text_content else ""
                summary = f"Clicked {label}{text_preview}"
                tl_type = "click"
            await emit_timeline_event(
                db,
                project_id=data.project_id,
                process_id=data.process_id,
                capture_session_id=data.capture_session_id,
                event_type=tl_type,
                source_id=click.id,
                summary=summary,
                metadata={"url": data.url, "selector": data.selector, "event_type": et},
            )
            await db.commit()
        except Exception:
            pass  # Don't fail click storage for timeline

    return click


@router.get("", response_model=list[ClickEventOut])
async def list_clicks(
    project_id: str | None = Query(None),
    process_id: str | None = Query(None),
    capture_session_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ClickEvent)
    if project_id:
        stmt = stmt.where(ClickEvent.project_id == project_id)
    if process_id:
        stmt = stmt.where(ClickEvent.process_id == process_id)
    if capture_session_id:
        stmt = stmt.where(ClickEvent.capture_session_id == capture_session_id)
    stmt = stmt.order_by(ClickEvent.timestamp.desc()).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{click_id}", response_model=ClickEventOut)
async def get_click(click_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ClickEvent).where(ClickEvent.id == click_id))
    click = result.scalar_one_or_none()
    if not click:
        from fastapi import HTTPException
        raise HTTPException(404, "Click event not found")
    return click
