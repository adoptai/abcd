"""Chat router with SSE streaming for Claude integration."""

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from functools import partial

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.database import get_db
from app.models import ChatSession, Message, Process, Project, Screenshot, TimelineEvent
from app.schemas import ChatSessionCreate, ChatSessionOut, ChatSessionUpdate
from app import claude_client
from app.timeline_utils import emit_timeline_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    project_id: str
    process_id: str | None = None
    chat_session_id: str | None = None
    content: str
    content_type: str = "text"
    url: str = ""


class ChatMessageOut(BaseModel):
    id: str
    role: str
    content: str
    content_type: str
    timestamp: datetime

    model_config = {"from_attributes": True}


async def _get_conversation_history(
    db: AsyncSession,
    project_id: str,
    process_id: str | None = None,
    chat_session_id: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Fetch recent messages for the conversation context."""
    query = select(Message).where(Message.project_id == project_id)
    if chat_session_id:
        query = query.where(Message.chat_session_id == chat_session_id)
    elif process_id:
        query = query.where(Message.process_id == process_id)
    query = query.order_by(Message.timestamp.desc()).limit(limit)
    result = await db.execute(query)
    messages = result.scalars().all()
    # Reverse to chronological order
    messages = list(reversed(messages))
    return [
        {
            "role": m.role,
            "content": m.content,
            "content_type": m.content_type,
        }
        for m in messages
    ]


async def _get_context_block(db: AsyncSession, project_id: str) -> str:
    """Build context block from project data."""
    # Get project
    project = await db.get(Project, project_id)
    if not project:
        return ""

    project_dict = {"name": project.name, "description": project.description}

    # Get processes
    result = await db.execute(
        select(Process).where(Process.project_id == project_id)
    )
    processes = [{"name": p.name} for p in result.scalars().all()]

    # Get screenshot count
    result = await db.execute(
        select(Screenshot).where(Screenshot.project_id == project_id)
    )
    screenshots = [{"id": s.id} for s in result.scalars().all()]

    return claude_client.build_context_block(
        project=project_dict,
        processes=processes,
        screenshots=screenshots,
    )


async def _execute_tool(project_id: str, tool_name: str, tool_input: dict) -> str:
    """Execute a Claude tool call using a fresh DB session."""
    from app.database import async_session

    async with async_session() as db:
        if tool_name == "query_timeline":
            return await _tool_query_timeline(db, project_id, tool_input)
        elif tool_name == "get_capture_summary":
            return await _tool_get_capture_summary(db, project_id, tool_input)
        else:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})


async def _tool_query_timeline(
    db: AsyncSession, project_id: str, params: dict
) -> str:
    """Execute the query_timeline tool."""
    stmt = select(TimelineEvent).where(TimelineEvent.project_id == project_id)

    event_type = params.get("event_type")
    if event_type:
        stmt = stmt.where(TimelineEvent.event_type == event_type)

    search_text = params.get("search_text")
    if search_text:
        pattern = f"%{search_text}%"
        stmt = stmt.where(
            or_(
                TimelineEvent.summary.ilike(pattern),
                TimelineEvent.metadata_json.ilike(pattern),
            )
        )

    limit = min(params.get("limit", 50), 200)
    stmt = stmt.order_by(TimelineEvent.timestamp.asc()).limit(limit)

    result = await db.execute(stmt)
    events = result.scalars().all()

    return json.dumps({
        "total_returned": len(events),
        "events": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "summary": e.summary,
                "timestamp": e.timestamp.isoformat(),
                "process_id": e.process_id,
                "metadata": json.loads(e.metadata_json) if e.metadata_json else {},
            }
            for e in events
        ],
    })


async def _tool_get_capture_summary(
    db: AsyncSession, project_id: str, params: dict
) -> str:
    """Execute the get_capture_summary tool."""
    stmt = select(TimelineEvent).where(TimelineEvent.project_id == project_id)

    process_id = params.get("process_id")
    if process_id:
        stmt = stmt.where(TimelineEvent.process_id == process_id)

    result = await db.execute(stmt)
    events = result.scalars().all()

    if not events:
        return json.dumps({
            "message": "No timeline data captured yet for this project.",
            "total_events": 0,
            "event_counts": {},
            "unique_urls": [],
            "api_endpoints": [],
            "time_range": None,
        })

    type_counts = Counter(e.event_type for e in events)

    unique_urls = set()
    api_endpoints = set()

    for e in events:
        try:
            meta = json.loads(e.metadata_json) if e.metadata_json else {}
        except (json.JSONDecodeError, TypeError):
            meta = {}

        url = meta.get("url", "")
        if url:
            unique_urls.add(url)

        if e.event_type == "network_request":
            method = meta.get("method", "")
            req_url = meta.get("url", "")
            if method and req_url:
                api_endpoints.add(f"{method} {req_url}")

    timestamps = [e.timestamp for e in events]

    return json.dumps({
        "total_events": len(events),
        "event_counts": dict(type_counts),
        "unique_urls": sorted(unique_urls)[:50],
        "api_endpoints": sorted(api_endpoints)[:50],
        "time_range": {
            "earliest": min(timestamps).isoformat(),
            "latest": max(timestamps).isoformat(),
        },
    })


@router.post("")
async def chat(req: ChatRequest, db: AsyncSession = Depends(get_db)):
    """Send a message and get a streaming Claude response via SSE.

    1. Stores the human message
    2. Fetches conversation history + project context
    3. Streams Claude's response as SSE events
    4. Stores the complete agent response when done
    """
    # Validate project exists
    project = await db.get(Project, req.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Store the human message
    human_msg = Message(
        project_id=req.project_id,
        process_id=req.process_id,
        chat_session_id=req.chat_session_id,
        role="human",
        content_type=req.content_type,
        content=req.content,
        url=req.url,
    )
    db.add(human_msg)
    await db.commit()
    await db.refresh(human_msg)

    # Auto-title: set session title from first user message
    if req.chat_session_id:
        chat_sess = await db.get(ChatSession, req.chat_session_id)
        if chat_sess and chat_sess.title == "New Chat":
            chat_sess.title = req.content[:60] + ("..." if len(req.content) > 60 else "")
            await db.commit()

    # Emit timeline event for human message
    try:
        await emit_timeline_event(
            db,
            project_id=req.project_id,
            process_id=req.process_id,
            event_type="message",
            source_id=human_msg.id,
            summary=f"User: {req.content[:80]}{'...' if len(req.content) > 80 else ''}",
            metadata={"role": "human", "content_type": req.content_type, "url": req.url},
        )
        await db.commit()
    except Exception as e:
        logger.warning("Failed to emit timeline event: %s", e)

    # Get conversation context
    history = await _get_conversation_history(
        db, req.project_id, req.process_id, req.chat_session_id
    )
    context_block = await _get_context_block(db, req.project_id)

    async def event_generator():
        full_response = []

        # Send the stored human message event
        yield {
            "event": "human_message",
            "data": json.dumps({
                "id": human_msg.id,
                "role": "human",
                "content": human_msg.content,
                "content_type": human_msg.content_type,
                "url": human_msg.url,
                "timestamp": human_msg.timestamp.isoformat(),
            }),
        }

        # Stream Claude response with tool use support
        tool_executor = partial(_execute_tool, req.project_id)
        async for delta in claude_client.stream_response_with_tools(
            project_id=req.project_id,
            conversation_history=history,
            context_block=context_block,
            tools=claude_client.TOOL_DEFINITIONS,
            tool_executor=tool_executor,
        ):
            full_response.append(delta)
            yield {
                "event": "delta",
                "data": json.dumps({"text": delta}),
            }

        # Store the complete agent response
        agent_content = "".join(full_response)
        if agent_content:
            # Need a new session since the generator runs outside the original request
            from app.database import async_session
            async with async_session() as session:
                agent_msg = Message(
                    project_id=req.project_id,
                    process_id=req.process_id,
                    chat_session_id=req.chat_session_id,
                    role="agent",
                    content_type="text",
                    content=agent_content,
                )
                session.add(agent_msg)
                await session.commit()
                await session.refresh(agent_msg)

                # Emit timeline event for agent message
                try:
                    await emit_timeline_event(
                        session,
                        project_id=req.project_id,
                        process_id=req.process_id,
                        event_type="message",
                        source_id=agent_msg.id,
                        summary=f"Agent: {agent_content[:80]}{'...' if len(agent_content) > 80 else ''}",
                        metadata={"role": "agent"},
                    )
                    await session.commit()
                except Exception as te:
                    logger.warning("Failed to emit agent timeline event: %s", te)

                yield {
                    "event": "agent_message",
                    "data": json.dumps({
                        "id": agent_msg.id,
                        "role": "agent",
                        "content": agent_msg.content,
                        "content_type": "text",
                        "timestamp": agent_msg.timestamp.isoformat(),
                    }),
                }

        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(event_generator())


@router.get("/history")
async def get_chat_history(
    project_id: str = Query(...),
    process_id: str | None = Query(None),
    chat_session_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[ChatMessageOut]:
    """Get conversation history for a project/process/session."""
    query = select(Message).where(Message.project_id == project_id)
    if chat_session_id:
        query = query.where(Message.chat_session_id == chat_session_id)
    elif process_id:
        query = query.where(Message.process_id == process_id)
    query = query.order_by(Message.timestamp.asc()).limit(limit)
    result = await db.execute(query)
    return [ChatMessageOut.model_validate(m) for m in result.scalars().all()]


# ── Chat Session CRUD ──────────────────────────────────────────────────────

@router.post("/sessions", response_model=ChatSessionOut, status_code=201)
async def create_chat_session(
    data: ChatSessionCreate, db: AsyncSession = Depends(get_db)
):
    session = ChatSession(
        project_id=data.project_id,
        process_id=data.process_id,
        title=data.title,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


@router.patch("/sessions/{session_id}", response_model=ChatSessionOut)
async def update_chat_session(
    session_id: str, data: ChatSessionUpdate, db: AsyncSession = Depends(get_db)
):
    session = await db.get(ChatSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    session.title = data.title
    await db.commit()
    await db.refresh(session)
    return session


@router.get("/sessions", response_model=list[ChatSessionOut])
async def list_chat_sessions(
    project_id: str = Query(...),
    process_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ChatSession).where(ChatSession.project_id == project_id)
    if process_id:
        stmt = stmt.where(ChatSession.process_id == process_id)
    stmt = stmt.order_by(ChatSession.updated_at.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_chat_session(
    session_id: str, db: AsyncSession = Depends(get_db)
):
    session = await db.get(ChatSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    await db.delete(session)
    await db.commit()
