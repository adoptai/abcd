"""Helper for emitting timeline events from routers."""

import json
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TimelineEvent

logger = logging.getLogger(__name__)


async def emit_timeline_event(
    db: AsyncSession,
    *,
    project_id: str,
    event_type: str,
    summary: str,
    process_id: str | None = None,
    capture_session_id: str | None = None,
    source_id: str | None = None,
    metadata: dict | None = None,
    timestamp: datetime | None = None,
) -> TimelineEvent:
    """Create a timeline event in the database."""
    event = TimelineEvent(
        project_id=project_id,
        process_id=process_id,
        capture_session_id=capture_session_id,
        event_type=event_type,
        source_id=source_id,
        summary=summary,
        metadata_json=json.dumps(metadata or {}),
    )
    if timestamp:
        event.timestamp = timestamp
    db.add(event)
    await db.flush()  # Use flush instead of commit so caller can batch
    return event
