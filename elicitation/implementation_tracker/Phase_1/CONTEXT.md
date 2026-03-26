# Phase 1: Export Alignment — Context & Background

## Why This Phase Exists

The Elicitation Agent (this project) captures rich, multi-modal data when a user demonstrates a workflow in the browser: HTTP traffic (HAR), click events, form inputs, voice narrations, screenshots, URL navigation. The ABCD library (downstream) creates automated workflows from structured API specifications. Today these two systems are connected by a thin handoff — a raw HAR file. Phase 1 builds the translation layer that makes our output directly consumable by ABCD.

## The V0.1 Baseline

At v0.1 (`9233d3e` on `main`), the Elicitation Agent has:

### Backend (FastAPI + SQLite)
- **Models:** Project, Process, CaptureSession, Message, ChatSession, Screenshot, Narration, ClickEvent, TimelineEvent, Document, Attachment, Question
- **Routers:** projects, processes, messages, screenshots, chat, timeline, capture_sessions, export, narrations, clicks, documents, attachments, questions, example_site, browser_commands
- **MCP Server:** 14 data tools + 7 browser control tools (21 total)
- **Claude Client:** Streaming chat with tool use (chat agent can call MCP tools)
- **Key files:**
  - `backend/app/models.py` — All ORM models
  - `backend/app/schemas.py` — Pydantic schemas
  - `backend/app/mcp_server.py` — MCP tool definitions
  - `backend/app/claude_client.py` — Anthropic API wrapper with tool loop
  - `backend/app/browser_bridge.py` — In-memory command queue for browser control
  - `backend/app/main.py` — App entry, router registration, MCP mount, migrations
  - `backend/app/config.py` — Settings (port 8000, CORS, DB path)

### Chrome Extension (MV3)
- **Capture:** HAR via debugger API, clicks via content script, narrations via Web Speech API, screenshots via tab capture
- **UI:** Popup with project/process management, chat with Claude, timeline view, documents, questions, capture controls, options (text size)
- **Backend polling:** Service worker polls `/browser-commands/pending` for MCP browser commands
- **Key files:**
  - `extension/background/service-worker.js` — HAR capture, command polling, backend communication
  - `extension/popup/popup.js` — Main app component
  - `extension/popup/components.js` — UI components (ChatSessionBar, OptionsView, etc.)
  - `extension/popup/api.js` — Backend API client
  - `extension/content/click-tracker.js` — DOM event capture

### Reference Document
- `docs/ABCD_ALIGNMENT_DEEP_DIVE.md` — 2,284-line strategic alignment analysis (Sections 1-12 + 3 appendices)

## What ABCD Expects

ABCD's workspace structure for an action:

```
workspaces/<environment>/actions/<action_name>/
├── adopt_profile.json    # base_url, security_params, workflow_params, profiles_map
├── widdle.json           # WDL definition (multi-step workflow)
├── metadata.json         # action metadata
├── requirements.md       # natural language requirements
├── description.txt       # short description for discovery
├── apis/
│   └── manifest.json     # API specs listing
├── test_cases/
│   └── test_cases.json   # test case definitions
├── versions/             # immutable version snapshots
└── traces/               # execution traces
```

Key ABCD concepts Phase 1 must produce output for:
- **adopt_profile.json** — from HAR auth headers + form input values
- **widdle.json** — WDL draft from HAR entries + timeline sequence
- **requirements.md** — from narrations + messages + questions
- **description.txt** — from process description
- **test_cases.json** — from capture session data (form values + API responses)
- **Filtered HAR** — API calls only, annotated with step numbers

## Semantic Mapping (What Translates to What)

| Our Data | ABCD Target | Translation |
|----------|-------------|-------------|
| HAR entries (filtered) | WDL REST steps | Method, URL, headers, body → REST operation config |
| Click events (input type) | workflow_params | Field name + value → parameterized variable |
| Click events (sequence) | WDL step ordering | Timestamp ordering → step sequence |
| Narrations | requirements.md + step descriptions | Transcripts → natural language requirements |
| Messages (human) | requirements.md (intent) | User messages expressing what they want → requirements |
| Messages (agent) | API spec drafts | Claude's analysis → OpenAPI fragments |
| Screenshots | (not consumed by ABCD) | Retained for visual verification in Phase 4 |
| Questions (answered) | requirements.md (edge cases) | Q&A → assumptions and edge case documentation |
| Process.base_url | adopt_profile.base_url | Direct mapping |
| Process.name | action directory name | Slugify |
| Process.description | description.txt | Direct mapping |
| HAR auth headers | security_params | Pattern detection → auth config |
| Multiple HAR base URLs | profiles_map | Group by domain → named API profiles |
| Response value → next request | JQ_FILTER + variable | Dependency chain detection |

## Bugs Fixed Before This Branch

These were committed in v0.1 on `main`:

1. **Chat API 400 error** — `claude_client.py:243` used `block.model_dump()` which serialized SDK-internal `parsed_output` field. Fixed with `_serialize_block()` that only includes API-permitted fields.

2. **Chat session rename** — Added `PATCH /chat/sessions/{id}` endpoint, `ChatSessionUpdate` schema, `updateChatSession()` API call, pencil button with inline edit in `ChatSessionBar`.

3. **Service worker storage guard** — `chrome.storage.session.get()` callback could receive `undefined` result on cold start. Added `if (!result) return;` guard.
