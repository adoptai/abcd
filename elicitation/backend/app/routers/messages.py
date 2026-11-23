"""Routes for messages."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Message
from app.schemas import MessageCreate, MessageOut

router = APIRouter(prefix="/messages", tags=["messages"])


@router.post("", response_model=MessageOut, status_code=201)
async def create_message(body: MessageCreate, db: AsyncSession = Depends(get_db)):
    if not body.project_id and not body.process_id:
        raise HTTPException(400, "At least one of project_id or process_id is required")
    message = Message(
        project_id=body.project_id,
        process_id=body.process_id,
        role=body.role,
        content_type=body.content_type,
        content=body.content,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return message


@router.get("", response_model=list[MessageOut])
async def list_messages(
    project_id: str | None = Query(None),
    process_id: str | None = Query(None),
    chat_session_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    if not project_id and not process_id and not chat_session_id:
        raise HTTPException(400, "Provide project_id, process_id, or chat_session_id query parameter")
    stmt = select(Message)
    if chat_session_id:
        stmt = stmt.where(Message.chat_session_id == chat_session_id)
    if project_id:
        stmt = stmt.where(Message.project_id == project_id)
    if process_id:
        stmt = stmt.where(Message.process_id == process_id)
    stmt = stmt.order_by(Message.timestamp.asc())
    result = await db.execute(stmt)
    return result.scalars().all()
