# Local Runbook — Web API Spec Elicitation Agent

> For any Claude instance or developer picking this up fresh.

## Project layout

```
web-api-spec-elicitation-agent/
├── .env                          # secrets (gitignored) — ANTHROPIC_API_KEY etc.
├── .venv/                        # Python 3.11 virtualenv managed by uv
├── backend/                      # FastAPI backend (Python)
│   ├── app/                      # application code
│   │   ├── main.py               # FastAPI app, router registration, MCP mount
│   │   ├── config.py             # Settings dataclass, loads .env via python-dotenv
│   │   ├── database.py           # async SQLAlchemy engine + session
│   │   ├── models.py             # ORM: Project, Process, Message, Screenshot, Recording
│   │   ├── schemas.py            # Pydantic request/response models
│   │   ├── claude_client.py      # Anthropic SDK wrapper, per-project locks, streaming
│   │   ├── mcp_server.py         # MCP tool definitions (browser context for Claude Code)
│   │   └── routers/
│   │       ├── sessions.py       # POST /sessions/ping
│   │       ├── projects.py       # CRUD /projects
│   │       ├── processes.py      # CRUD /projects/{id}/processes
│   │       ├── messages.py       # POST/GET /messages
│   │       ├── screenshots.py    # POST/GET /screenshots
│   │       ├── recordings.py     # Recording lifecycle /recordings
│   │       └── chat.py           # POST /chat (SSE streaming), GET /chat/history
│   ├── data/                     # SQLite DB + uploaded files (gitignored)
│   ├── tests/                    # pytest async tests
│   ├── requirements.txt
│   └── pyproject.toml
├── extension/                    # Chrome Manifest V3 extension (no build step)
│   ├── manifest.json
│   ├── background/service-worker.js
│   ├── popup/                    # UI components (Preact + HTM, vanilla JS modules)
│   │   ├── popup.html
│   │   ├── popup.js              # main app, views, state persistence
│   │   ├── components.js         # MessageList, MessageInput, PlumbingTestView, etc.
│   │   ├── api.js                # backend API client + SSE chat streaming
│   │   ├── voice.js              # Web Speech API (STT)
│   │   └── tts.js                # SpeechSynthesis (TTS)
│   ├── sidepanel/panel.html      # side panel shell (reuses popup components)
│   ├── lib/htm-preact.mjs        # vendored Preact + HTM (no npm needed)
│   └── icons/
└── scripts/
    ├── dev.sh                    # start backend with auto-reload
    └── smoke-test.sh
```

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (Python package/venv manager)
- Chrome or Chromium (for the extension)

## First-time setup

```bash
cd /home/juancho/projects/specification_agent/web-api-spec-elicitation-agent

# 1. Create virtualenv (if .venv doesn't exist)
uv venv .venv --python 3.11

# 2. Install backend + dev dependencies
VIRTUAL_ENV=.venv uv pip install -e "backend[dev]"

# 3. Verify .env exists and has ANTHROPIC_API_KEY set
cat .env
# Should contain: ANTHROPIC_API_KEY=sk-ant-...
# If missing, create it:
#   cp .env.example .env  (or create manually — see "Environment variables" below)
```

## Environment variables

All in `.env` at project root (auto-loaded by backend via python-dotenv):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Yes (for AI chat) | `""` | Claude API key from console.anthropic.com |
| `ELIC_PORT` | No | `8000` | Backend listen port |
| `ELIC_DEBUG` | No | `true` | SQLAlchemy echo + debug mode |
| `CLAUDE_MODEL` | No | `claude-sonnet-4-20250514` | Anthropic model ID |

## Running the backend

```bash
# Option A: convenience script
./scripts/dev.sh

# Option B: manual
source .venv/bin/activate
cd backend
uvicorn app.main:app --reload --port 8000
```

Backend serves at **http://localhost:8000**. Key endpoints:
- `GET /health` — health check
- `GET /docs` — auto-generated OpenAPI docs (Swagger UI)
- `POST /chat` — SSE streaming chat with Claude
- `GET /chat/history?project_id=X` — conversation history
- `/mcp/sse` — MCP server endpoint for Claude Code

## Running tests

```bash
cd backend
../.venv/bin/python -m pytest tests/ -v
```

All 28 tests should pass. Tests use an in-memory SQLite database (no backend server needed).

## Loading the Chrome extension

1. Open `chrome://extensions/`
2. Enable **Developer mode** (toggle top-right)
3. Click **Load unpacked**
4. Select the `extension/` directory
5. The extension icon appears in the toolbar
6. Click it — opens as a **side panel** (docked right)

If it opens as a popup instead of side panel, right-click the icon and choose "Open side panel", or restart Chrome.

## Key workflows

### Chat with Claude (from extension)
1. Create a project in the side panel
2. Go to Chat tab
3. Type a message — response streams token-by-token via SSE
4. Messages are stored in SQLite and visible from both extension and MCP

### MCP integration (from VS Code Claude Code)
With backend running, add to project `.mcp.json`:
```json
{
  "mcpServers": {
    "elicitation-agent": {
      "url": "http://localhost:8000/mcp/sse"
    }
  }
}
```

Available MCP tools:
- `get_project_context(project_id)` — project + processes + recent messages
- `get_conversation_history(project_id, limit)` — message history
- `get_screenshots(project_id)` — screenshot metadata + image URLs
- `get_har_recordings(process_id)` — HAR data
- `get_api_spec_draft(project_id)` — finds spec-like agent messages
- `add_message(project_id, content, role)` — inject into shared conversation

### Plumbing Test View
In the extension side panel: project list → click **Test** button. Runs isolated tests for connection, voice STT, TTS, screenshot capture, HAR stubs, message storage, and full voice→store→retrieve→TTS loop.

## Database

SQLite at `backend/data/elicitation.db`. Auto-created on first startup.

To reset: stop the backend, delete `backend/data/elicitation.db`, restart.

Tables: `projects`, `processes`, `messages`, `screenshots`, `recordings`.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `ModuleNotFoundError` | Run `VIRTUAL_ENV=.venv uv pip install -e "backend[dev]"` |
| Chat returns "API key not configured" | Check `.env` has a valid `ANTHROPIC_API_KEY` |
| Extension shows "Disconnected" | Make sure backend is running on port 8000 |
| Side panel doesn't open | Restart Chrome after first extension load |
| Tests fail with event loop errors | Ignore SSE-specific test warnings — known sse_starlette/httpx incompatibility in test env |
| `uv: command not found` | Install: `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
