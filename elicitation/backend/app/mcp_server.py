"""MCP Server exposing browser context tools for Claude Code in VS Code.

Tools available:
- get_project_context: project + processes + recent messages
- get_har_recordings: HAR data for a process
- get_screenshots: screenshot metadata for a project
- get_conversation_history: recent messages
- get_api_spec_draft: current draft spec (placeholder)
- add_message: inject a message into the shared conversation

Run standalone: python -m app.mcp_server
Or mount as a sub-application within FastAPI.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.browser_bridge import execute_command as _browser_exec
from app.config import settings
from app.database import async_session
from app.models import CaptureSession, Message, Narration, Process, Project, Screenshot, TimelineEvent

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "elicitation-agent",
    instructions="Browser context tools for the Web API Spec Elicitation Agent. "
    "Use these tools to access project data, conversations, screenshots, and HAR recordings "
    "captured from the Chrome extension.",
)


async def _get_session() -> AsyncSession:
    """Get a database session."""
    return async_session()


@mcp.tool()
async def get_project_context(project_id: str) -> str:
    """Get full project context: project details, processes, and recent messages.

    Args:
        project_id: UUID of the project
    """
    async with async_session() as db:
        project = await db.get(Project, project_id)
        if not project:
            return json.dumps({"error": f"Project {project_id} not found"})

        # Get processes
        result = await db.execute(
            select(Process).where(Process.project_id == project_id)
        )
        processes = result.scalars().all()

        # Get recent messages
        result = await db.execute(
            select(Message)
            .where(Message.project_id == project_id)
            .order_by(Message.timestamp.desc())
            .limit(20)
        )
        messages = list(reversed(result.scalars().all()))

        return json.dumps({
            "project": {
                "id": project.id,
                "name": project.name,
                "description": project.description,
                "status": project.status,
            },
            "processes": [
                {
                    "id": p.id,
                    "name": p.name,
                    "description": p.description,
                    "status": p.status,
                }
                for p in processes
            ],
            "recent_messages": [
                {
                    "id": m.id,
                    "role": m.role,
                    "content": m.content,
                    "content_type": m.content_type,
                    "timestamp": m.timestamp.isoformat(),
                }
                for m in messages
            ],
        }, indent=2)


@mcp.tool()
async def get_capture_sessions(process_id: str) -> str:
    """Get capture sessions and HAR data for a specific process.

    Args:
        process_id: UUID of the process
    """
    async with async_session() as db:
        result = await db.execute(
            select(CaptureSession).where(CaptureSession.process_id == process_id)
        )
        sessions = result.scalars().all()

        session_data = []
        for s in sessions:
            entry = {
                "id": s.id,
                "status": s.status,
                "click_tracking": s.click_tracking,
                "url_monitoring": s.url_monitoring,
                "voice_mode": s.voice_mode,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "stopped_at": s.stopped_at.isoformat() if s.stopped_at else None,
                "har_file_path": s.har_file_path,
                "har_data": None,
            }
            if s.har_file_path:
                try:
                    with open(s.har_file_path, "r") as f:
                        entry["har_data"] = json.load(f)
                except (FileNotFoundError, json.JSONDecodeError) as e:
                    entry["har_data"] = f"Error reading HAR: {e}"
            session_data.append(entry)

        return json.dumps({"capture_sessions": session_data}, indent=2)


@mcp.tool()
async def get_screenshots(project_id: str) -> str:
    """Get screenshot metadata and file paths for a project.

    Args:
        project_id: UUID of the project
    """
    async with async_session() as db:
        result = await db.execute(
            select(Screenshot)
            .where(Screenshot.project_id == project_id)
            .order_by(Screenshot.timestamp.desc())
        )
        screenshots = result.scalars().all()

        return json.dumps({
            "screenshots": [
                {
                    "id": s.id,
                    "url": s.url,
                    "file_path": s.file_path,
                    "timestamp": s.timestamp.isoformat(),
                    "image_url": f"http://localhost:{settings.port}/screenshots/{s.id}/image",
                }
                for s in screenshots
            ],
        }, indent=2)


@mcp.tool()
async def get_conversation_history(project_id: str, limit: int = 50) -> str:
    """Get recent conversation messages for a project.

    Args:
        project_id: UUID of the project
        limit: Maximum number of messages to return (default 50)
    """
    async with async_session() as db:
        result = await db.execute(
            select(Message)
            .where(Message.project_id == project_id)
            .order_by(Message.timestamp.desc())
            .limit(limit)
        )
        messages = list(reversed(result.scalars().all()))

        return json.dumps({
            "messages": [
                {
                    "id": m.id,
                    "role": m.role,
                    "content": m.content,
                    "content_type": m.content_type,
                    "timestamp": m.timestamp.isoformat(),
                }
                for m in messages
            ],
        }, indent=2)


@mcp.tool()
async def get_api_spec_draft(project_id: str) -> str:
    """Get the current API specification draft for a project, if one exists.

    This searches for agent messages containing OpenAPI spec content.

    Args:
        project_id: UUID of the project
    """
    async with async_session() as db:
        # Look for agent messages that contain spec-like content
        result = await db.execute(
            select(Message)
            .where(
                Message.project_id == project_id,
                Message.role == "agent",
                Message.content.contains("openapi:"),
            )
            .order_by(Message.timestamp.desc())
            .limit(1)
        )
        spec_msg = result.scalar_one_or_none()

        if spec_msg:
            return json.dumps({
                "has_draft": True,
                "message_id": spec_msg.id,
                "content": spec_msg.content,
                "timestamp": spec_msg.timestamp.isoformat(),
            }, indent=2)

        return json.dumps({
            "has_draft": False,
            "message": "No API specification draft found. Start a conversation to generate one.",
        }, indent=2)


@mcp.tool()
async def add_message(project_id: str, content: str, role: str = "human") -> str:
    """Add a message to the shared conversation for a project.

    This allows Claude Code in VS Code to inject messages that the Chrome extension
    can see, maintaining a shared conversation history.

    Args:
        project_id: UUID of the project
        content: Message content
        role: Message role - "human" or "agent" (default "human")
    """
    if role not in ("human", "agent"):
        return json.dumps({"error": "role must be 'human' or 'agent'"})

    async with async_session() as db:
        project = await db.get(Project, project_id)
        if not project:
            return json.dumps({"error": f"Project {project_id} not found"})

        msg = Message(
            project_id=project_id,
            role=role,
            content_type="text",
            content=content,
        )
        db.add(msg)
        await db.commit()
        await db.refresh(msg)

        return json.dumps({
            "id": msg.id,
            "role": msg.role,
            "content": msg.content,
            "timestamp": msg.timestamp.isoformat(),
        }, indent=2)


@mcp.tool()
async def get_timeline(project_id: str, process_id: str | None = None, limit: int = 100) -> str:
    """Get the unified timeline of events for a project or process.

    Returns chronologically ordered events including messages, screenshots,
    clicks, voice recordings, and recording start/stop events.

    Args:
        project_id: UUID of the project
        process_id: Optional UUID of a specific process to filter by
        limit: Maximum number of events to return (default 100)
    """
    async with async_session() as db:
        stmt = (
            select(TimelineEvent)
            .where(TimelineEvent.project_id == project_id)
        )
        if process_id:
            stmt = stmt.where(TimelineEvent.process_id == process_id)
        stmt = stmt.order_by(TimelineEvent.timestamp.asc()).limit(limit)
        result = await db.execute(stmt)
        events = result.scalars().all()

        return json.dumps({
            "timeline": [
                {
                    "id": e.id,
                    "event_type": e.event_type,
                    "summary": e.summary,
                    "source_id": e.source_id,
                    "timestamp": e.timestamp.isoformat(),
                    "metadata": json.loads(e.metadata_json) if e.metadata_json else {},
                }
                for e in events
            ],
        }, indent=2)


# ── Browser control tools (via command queue → Chrome extension) ──────────


@mcp.tool()
async def browser_get_page_info() -> str:
    """Get the current active tab's URL, title, and basic page info.

    Returns JSON with url, title, and favicon URL of the active browser tab.
    Requires the Chrome extension to be running.
    """
    try:
        result = await _browser_exec("get_page_info", {})
        return json.dumps(result, indent=2)
    except TimeoutError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def browser_query_elements(
    selector: str,
    include_text: bool = True,
    max_results: int = 20,
) -> str:
    """Find elements in the active browser tab by CSS selector.

    Returns an array of matched elements with their tag, id, classes,
    text content, and key attributes. Useful for inspecting page structure
    before interacting with elements.

    Args:
        selector: CSS selector to query (e.g., "button.submit", "#login-form input")
        include_text: Whether to include text content of each element (default True)
        max_results: Maximum number of elements to return (default 20, max 100)
    """
    try:
        result = await _browser_exec("query_elements", {
            "selector": selector,
            "includeText": include_text,
            "maxResults": min(max_results, 100),
        })
        return json.dumps(result, indent=2)
    except TimeoutError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def browser_click_element(selector: str) -> str:
    """Click the first element matching a CSS selector in the active browser tab.

    Args:
        selector: CSS selector of the element to click (e.g., "button#submit", "a.nav-link")
    """
    try:
        result = await _browser_exec("click_element", {"selector": selector})
        return json.dumps(result, indent=2)
    except TimeoutError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def browser_type_text(selector: str, text: str, clear_first: bool = True) -> str:
    """Type text into an input or textarea matching a CSS selector.

    Args:
        selector: CSS selector of the input element
        text: Text to type into the element
        clear_first: Whether to clear the existing value first (default True)
    """
    try:
        result = await _browser_exec("type_text", {
            "selector": selector,
            "text": text,
            "clearFirst": clear_first,
        })
        return json.dumps(result, indent=2)
    except TimeoutError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def browser_navigate(url: str) -> str:
    """Navigate the active browser tab to a URL.

    Args:
        url: The URL to navigate to (e.g., "https://example.com/login")
    """
    try:
        result = await _browser_exec("navigate", {"url": url})
        return json.dumps(result, indent=2)
    except TimeoutError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def browser_eval_js(code: str) -> str:
    """Execute JavaScript code in the active browser tab's page context.

    The code runs in the page's main world. The return value is serialized
    to JSON. Use this for advanced interactions not covered by other browser tools.

    Args:
        code: JavaScript code to execute. The last expression's value is returned.
    """
    try:
        result = await _browser_exec("eval_js", {"code": code})
        return json.dumps(result, indent=2)
    except TimeoutError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def browser_take_screenshot() -> str:
    """Take a screenshot of the active browser tab.

    Returns the screenshot as a URL that can be used to view the image.
    The screenshot is saved to the backend and accessible via the screenshots API.
    """
    try:
        result = await _browser_exec("take_screenshot", {})
        return json.dumps(result, indent=2)
    except TimeoutError as e:
        return json.dumps({"error": str(e)})


# ── ABCD Export tools ────────────────────────────────────────────────────────

@mcp.tool()
async def generate_wdl_draft(process_id: str) -> str:
    """Generate a WDL (Workflow Definition Language) draft from captured data.

    Analyzes HAR traffic, click events, narrations, and timeline events
    for the given process to produce a multi-step WDL definition with
    parameterized variables and dependency detection.

    Args:
        process_id: UUID of the process with captured data.
    """
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"http://localhost:{settings.port}/abcd/processes/{process_id}/analysis",
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            return json.dumps({
                "wdl": data.get("wdl", {}),
                "auth": data.get("auth", {}),
                "api_groups": data.get("api_groups", []),
                "test_cases": data.get("test_cases", []),
                "stats": data.get("stats", {}),
            }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def detect_auth_patterns_tool(process_id: str) -> str:
    """Detect authentication patterns from captured HAR data.

    Analyzes HTTP request headers to identify auth schemes (Bearer, API key,
    Basic, Cookie, OAuth2) and returns ABCD-compatible security_params.

    Args:
        process_id: UUID of the process with HAR data.
    """
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"http://localhost:{settings.port}/abcd/processes/{process_id}/analysis",
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            return json.dumps(data.get("auth", {}), indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def generate_test_cases_tool(process_id: str) -> str:
    """Generate ABCD test cases from captured data.

    Creates test case definitions with prompts (from narrations),
    workflow_params (from form inputs), expected_output (from API responses),
    and validation rules.

    Args:
        process_id: UUID of the process with captured data.
    """
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"http://localhost:{settings.port}/abcd/processes/{process_id}/analysis",
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            return json.dumps(data.get("test_cases", []), indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def export_abcd_workspace(process_id: str) -> str:
    """Export an ABCD workspace bundle for a process.

    Generates a zip file containing the complete ABCD workspace structure:
    adopt_profile.json, widdle.json, requirements.md, description.txt,
    metadata.json, test_cases, and API manifest.

    The zip file is saved to the data directory and can be downloaded
    via the returned URL.

    Args:
        process_id: UUID of the process to export.
    """
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"http://localhost:{settings.port}/abcd/processes/{process_id}/export",
                timeout=60,
            )
            resp.raise_for_status()

            # Save to data dir
            from app.config import settings
            from pathlib import Path
            export_dir = Path(settings.data_dir) / "abcd_exports"
            export_dir.mkdir(parents=True, exist_ok=True)
            export_path = export_dir / f"abcd-{process_id}.zip"
            export_path.write_bytes(resp.content)

            return json.dumps({
                "status": "success",
                "file_path": str(export_path),
                "size_bytes": len(resp.content),
                "download_url": f"/abcd/processes/{process_id}/export",
            }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# Allow running as standalone MCP server
if __name__ == "__main__":
    mcp.run()
