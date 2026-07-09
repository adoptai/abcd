"""Generate canonical markdown documents from project/process data."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CaptureSession,
    ClickEvent,
    Message,
    Narration,
    Process,
    Project,
    Screenshot,
    TimelineEvent,
)


def _fmt_time(dt: datetime | None) -> str:
    if not dt:
        return "N/A"
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _sanitize_cell(text: str) -> str:
    """Sanitize text for use in a markdown table cell — strip newlines and escape pipes."""
    if not text:
        return ""
    return text.replace("\n", " ").replace("\r", " ").replace("|", "\\|").strip()


def _fmt_date(dt: datetime | None) -> str:
    if not dt:
        return "N/A"
    return dt.strftime("%Y-%m-%d")


async def generate_project_markdown(db: AsyncSession, project_id: str) -> str:
    """Build a canonical markdown document for a project."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise ValueError(f"Project {project_id} not found")

    lines = []
    lines.append(f"# {project.name}")
    lines.append("")

    meta = []
    if project.base_url:
        meta.append(f"**Base URL:** {project.base_url}")
    meta.append(f"**Status:** {project.status}")
    meta.append(f"**Created:** {_fmt_date(project.created_at)}")
    lines.append(" | ".join(meta))
    lines.append("")

    # Description
    if project.description:
        lines.append("## Description")
        lines.append("")
        lines.append(project.description)
        lines.append("")

    # Processes
    result = await db.execute(
        select(Process).where(Process.project_id == project_id).order_by(Process.created_at.asc())
    )
    processes = result.scalars().all()
    if processes:
        lines.append("## Processes")
        lines.append("")
        lines.append("| Name | Base URL | Status | Created |")
        lines.append("|------|----------|--------|---------|")
        for proc in processes:
            lines.append(f"| {proc.name} | {proc.base_url or '-'} | {proc.status} | {_fmt_date(proc.created_at)} |")
        lines.append("")

    # Conversation
    result = await db.execute(
        select(Message).where(Message.project_id == project_id, Message.process_id.is_(None))
        .order_by(Message.timestamp.asc())
    )
    messages = result.scalars().all()
    if messages:
        lines.append("## Conversation History")
        lines.append("")
        for msg in messages:
            role = "User" if msg.role == "human" else "Agent"
            lines.append(f"### {_fmt_time(msg.timestamp)} — {role}")
            lines.append("")
            lines.append(msg.content)
            lines.append("")

    # Screenshots
    result = await db.execute(
        select(Screenshot).where(Screenshot.project_id == project_id)
        .order_by(Screenshot.timestamp.asc())
    )
    screenshots = result.scalars().all()
    if screenshots:
        lines.append("## Screenshots")
        lines.append("")
        for ss in screenshots:
            url_part = f" ({ss.url})" if ss.url else ""
            lines.append(f"- **{_fmt_time(ss.timestamp)}**{url_part} — `{ss.file_path}`")
        lines.append("")

    # Timeline
    result = await db.execute(
        select(TimelineEvent).where(TimelineEvent.project_id == project_id)
        .order_by(TimelineEvent.timestamp.asc())
    )
    events = result.scalars().all()
    if events:
        lines.append("## Timeline")
        lines.append("")
        lines.append("| Time | Type | Summary |")
        lines.append("|------|------|---------|")
        for ev in events:
            lines.append(f"| {_fmt_time(ev.timestamp)} | {ev.event_type} | {_sanitize_cell(ev.summary)} |")
        lines.append("")

    return "\n".join(lines)


async def generate_process_markdown(db: AsyncSession, process_id: str) -> str:
    """Build a canonical markdown document for a process."""
    result = await db.execute(select(Process).where(Process.id == process_id))
    process = result.scalar_one_or_none()
    if not process:
        raise ValueError(f"Process {process_id} not found")

    result = await db.execute(select(Project).where(Project.id == process.project_id))
    project = result.scalar_one_or_none()

    lines = []
    lines.append(f"# Process: {process.name}")
    lines.append("")

    meta = []
    if project:
        meta.append(f"**Project:** {project.name}")
    if process.base_url:
        meta.append(f"**Base URL:** {process.base_url}")
    meta.append(f"**Status:** {process.status}")
    meta.append(f"**Created:** {_fmt_date(process.created_at)}")
    lines.append(" | ".join(meta))
    lines.append("")

    # Description
    if process.description:
        lines.append("## Description")
        lines.append("")
        lines.append(process.description)
        lines.append("")

    # Capture Sessions
    result = await db.execute(
        select(CaptureSession).where(CaptureSession.process_id == process_id)
        .order_by(CaptureSession.started_at.asc().nulls_last())
    )
    sessions = result.scalars().all()
    if sessions:
        lines.append("## Capture Sessions")
        lines.append("")
        for sess in sessions:
            start = _fmt_time(sess.started_at)
            end = _fmt_time(sess.stopped_at) if sess.stopped_at else "ongoing"
            lines.append(f"### Session: {start} → {end}")
            lines.append("")

            # Narrations for this session
            result = await db.execute(
                select(Narration).where(Narration.capture_session_id == sess.id)
                .order_by(Narration.timestamp.asc())
            )
            narrations = result.scalars().all()
            if narrations:
                lines.append("#### Narrations")
                lines.append("")
                for nar in narrations:
                    lines.append(f"- **{_fmt_time(nar.timestamp)}**: {_sanitize_cell(nar.content)}")
                lines.append("")

            # Clicks for this session
            result = await db.execute(
                select(ClickEvent).where(ClickEvent.capture_session_id == sess.id)
                .order_by(ClickEvent.timestamp.asc())
            )
            clicks = result.scalars().all()
            if clicks:
                lines.append("#### Clicks")
                lines.append("")
                lines.append("| Time | Type | Details |")
                lines.append("|------|------|---------|")
                for click in clicks:
                    desc = _sanitize_cell(click.selector or click.tag_name)
                    if click.text_content:
                        desc += f' "{_sanitize_cell(click.text_content)}"'
                    et = click.event_type if hasattr(click, "event_type") and click.event_type else "click"
                    lines.append(f"| {_fmt_time(click.timestamp)} | {et} | {desc} |")
                lines.append("")

            # URL navigations for this session
            result = await db.execute(
                select(TimelineEvent).where(
                    TimelineEvent.capture_session_id == sess.id,
                    TimelineEvent.event_type == "url_change",
                ).order_by(TimelineEvent.timestamp.asc())
            )
            url_events = result.scalars().all()
            if url_events:
                lines.append("#### URL Navigation")
                lines.append("")
                for ue in url_events:
                    lines.append(f"- **{_fmt_time(ue.timestamp)}**: {_sanitize_cell(ue.summary)}")
                lines.append("")

    # Conversation
    result = await db.execute(
        select(Message).where(Message.process_id == process_id)
        .order_by(Message.timestamp.asc())
    )
    messages = result.scalars().all()
    if messages:
        lines.append("## Conversation")
        lines.append("")
        for msg in messages:
            role = "User" if msg.role == "human" else "Agent"
            lines.append(f"### {_fmt_time(msg.timestamp)} — {role}")
            lines.append("")
            lines.append(msg.content)
            lines.append("")

    # Screenshots
    result = await db.execute(
        select(Screenshot).where(Screenshot.process_id == process_id)
        .order_by(Screenshot.timestamp.asc())
    )
    screenshots = result.scalars().all()
    if screenshots:
        lines.append("## Screenshots")
        lines.append("")
        for ss in screenshots:
            url_part = f" ({ss.url})" if ss.url else ""
            lines.append(f"- **{_fmt_time(ss.timestamp)}**{url_part} — `{ss.file_path}`")
        lines.append("")

    # Full Timeline
    result = await db.execute(
        select(TimelineEvent).where(TimelineEvent.process_id == process_id)
        .order_by(TimelineEvent.timestamp.asc())
    )
    events = result.scalars().all()
    if events:
        lines.append("## Full Timeline")
        lines.append("")
        lines.append("| Time | Type | Summary |")
        lines.append("|------|------|---------|")
        for ev in events:
            lines.append(f"| {_fmt_time(ev.timestamp)} | {ev.event_type} | {_sanitize_cell(ev.summary)} |")
        lines.append("")

    return "\n".join(lines)
