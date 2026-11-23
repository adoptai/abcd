from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from sqlalchemy import text

from app.database import Base, engine
from app.mcp_server import mcp
from app.routers import abcd_export, attachments, browser_commands, capture_sessions, chat, clicks, documents, example_site, export, messages, narrations, processes, projects, questions, screenshots, sessions, timeline, url_events


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create data directories
    data_dir = Path(settings.data_dir)
    (data_dir / "screenshots").mkdir(parents=True, exist_ok=True)
    (data_dir / "har").mkdir(parents=True, exist_ok=True)
    (data_dir / "attachments").mkdir(parents=True, exist_ok=True)

    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Migrate existing tables — add new columns (SQLite no-op if they exist)
    async with engine.begin() as conn:
        for col_def in [
            "ALTER TABLE click_events ADD COLUMN event_type VARCHAR(20) DEFAULT 'click'",
            "ALTER TABLE click_events ADD COLUMN input_type VARCHAR(50)",
            "ALTER TABLE click_events ADD COLUMN value VARCHAR(500)",
            "ALTER TABLE click_events ADD COLUMN field_name VARCHAR(255)",
            "ALTER TABLE capture_sessions ADD COLUMN har_capture BOOLEAN DEFAULT 1",
        ]:
            try:
                await conn.execute(text(col_def))
            except Exception:
                pass  # Column already exists

    yield

    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    description="Backend service for the Web API Spec Elicitation Agent",
    lifespan=lifespan,
)

# CORS — Chrome extensions send requests from chrome-extension:// origins
# For dev, we also allow all origins so curl/httpx tests work
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sessions.router)
app.include_router(projects.router)
app.include_router(processes.router)
app.include_router(messages.router)
app.include_router(screenshots.router)
app.include_router(capture_sessions.router)
app.include_router(chat.router)
app.include_router(clicks.router)
app.include_router(narrations.router)
app.include_router(timeline.router)
app.include_router(url_events.router)
app.include_router(documents.router)
app.include_router(attachments.router)
app.include_router(export.router)
app.include_router(questions.router)
app.include_router(example_site.router)
app.include_router(browser_commands.router)
app.include_router(abcd_export.router)

# Mount MCP server at /mcp for Claude Code integration
app.mount("/mcp", mcp.sse_app())


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": "0.2.0",
    }
