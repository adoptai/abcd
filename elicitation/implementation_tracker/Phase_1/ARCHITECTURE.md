# Phase 1: Export Alignment — Architecture

## Module Overview

```
backend/app/
├── har_analyzer.py          # HAR filtering, annotation, multi-API detection
├── auth_detector.py         # Auth pattern inference from HAR headers
├── wdl_generator.py         # WDL draft generation from captures
├── profile_generator.py     # adopt_profile.json generation
├── requirements_generator.py # requirements.md generation
├── test_case_generator.py   # ABCD test case generation
├── routers/
│   └── abcd_export.py       # Export endpoint + workspace bundle assembly
└── mcp_server.py            # (extended) New Phase 1 MCP tools
```

## Data Flow

```
                    ┌──────────────────────────────┐
                    │     Existing DB Models        │
                    │  (CaptureSession, ClickEvent, │
                    │   Narration, Message, etc.)   │
                    └──────────┬───────────────────┘
                               │
                    ┌──────────▼───────────────────┐
                    │      har_analyzer.py          │
                    │  filter_har_entries()         │
                    │  annotate_har()               │
                    │  detect_api_groups()          │
                    └──┬───────┬──────────┬────────┘
                       │       │          │
          ┌────────────▼──┐   │   ┌──────▼──────────┐
          │ auth_detector │   │   │ wdl_generator    │
          │  .py          │   │   │  .py             │
          │ detect_auth() │   │   │ generate_wdl()   │
          └──────┬────────┘   │   │ detect_deps()    │
                 │            │   │ parameterize()   │
                 │            │   └──────┬───────────┘
          ┌──────▼────────┐   │          │
          │ profile_gen   │   │   ┌──────▼───────────┐
          │  .py          │   │   │ test_case_gen    │
          │ generate()    │   │   │  .py             │
          └──────┬────────┘   │   │ generate()       │
                 │            │   └──────┬───────────┘
                 │            │          │
          ┌──────▼────────────▼──────────▼───────────┐
          │         requirements_generator.py         │
          │  generate_requirements_md()               │
          └──────────────────┬────────────────────────┘
                             │
                    ┌────────▼────────────────────────┐
                    │   routers/abcd_export.py         │
                    │   POST /processes/{id}/export/abcd│
                    │   Assembles zip bundle:           │
                    │   - adopt_profile.json            │
                    │   - widdle.json                   │
                    │   - requirements.md               │
                    │   - description.txt               │
                    │   - metadata.json                 │
                    │   - test_cases/test_cases.json    │
                    │   - apis/manifest.json            │
                    └─────────────────────────────────┘
```

## Module APIs

### har_analyzer.py

```python
def filter_har_entries(
    har_log: dict,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> list[dict]:
    """Filter HAR entries to API calls only.

    Returns filtered entries with original indices preserved in _metadata.
    """

def annotate_har_entries(
    entries: list[dict],
    timeline_events: list[dict],
    click_events: list[dict],
) -> list[dict]:
    """Add _annotations to filtered HAR entries.

    Annotations include: step_number, related_click_event_ids,
    related_narration, is_primary, detected_params.
    """

def detect_api_groups(entries: list[dict]) -> list[dict]:
    """Group HAR entries by base URL to identify distinct APIs.

    Returns: [{"name": "auto-detected", "base_url": "...",
               "entry_count": N, "endpoints": [...]}]
    """
```

### auth_detector.py

```python
def detect_auth_patterns(har_entries: list[dict]) -> dict:
    """Analyze HAR entries to detect authentication patterns.

    Returns: {
        "primary_auth": "bearer" | "api_key" | "basic" | "cookie" | "oauth2" | "none",
        "security_params": {<ABCD-compatible security_params>},
        "details": {<detection details and confidence>},
    }
    """
```

### wdl_generator.py

```python
def generate_wdl(
    har_entries: list[dict],       # Filtered + annotated
    click_events: list[dict],      # For parameterization
    narrations: list[dict],        # For step descriptions
    timeline_events: list[dict],   # For ordering
    base_url: str = "",
) -> dict:
    """Generate a WDL draft from captured data.

    Returns: {
        "steps": [...],
        "detected_params": {...},
        "detected_dependencies": [...],
        "source_stats": {...},
    }
    """

def detect_dependencies(har_entries: list[dict]) -> list[dict]:
    """Find data dependencies between sequential HAR entries.

    Returns: [{"from_entry": 0, "to_entry": 2, "field": "id",
               "jq_expression": ".id", "variable": "customer_id"}]
    """
```

### profile_generator.py

```python
def generate_adopt_profile(
    base_url: str,
    security_params: dict,         # From auth_detector
    click_events: list[dict],      # For workflow_params
    api_groups: list[dict],        # For profiles_map
) -> dict:
    """Generate an adopt_profile.json compatible with ABCD.

    Returns: {
        "base_url": "...",
        "security_params": {...},
        "workflow_params": {...},
        "profiles_map": {...},
    }
    """
```

### requirements_generator.py

```python
def generate_requirements_md(
    process_name: str,
    process_description: str,
    narrations: list[dict],
    messages: list[dict],
    questions: list[dict],
    click_events: list[dict],
    timeline_events: list[dict],
) -> str:
    """Generate a requirements.md document from captured data.

    Returns: Markdown string.
    """
```

### test_case_generator.py

```python
def generate_test_cases(
    process_name: str,
    click_events: list[dict],
    narrations: list[dict],
    har_entries: list[dict],       # Filtered
    wdl_params: dict,              # From wdl_generator detected_params
) -> list[dict]:
    """Generate ABCD test cases from capture data.

    Returns: [{
        "test_case_name": "...",
        "prompt": "...",
        "workflow_params": {...},
        "expected_output": "...",
        "validation": {"type": "...", "value": "..."},
    }]
    """
```

## MCP Tool Additions (Phase 1)

| Tool | Wraps | Input | Output |
|------|-------|-------|--------|
| `generate_wdl_draft` | `wdl_generator.generate_wdl()` | `process_id` | WDL JSON + stats |
| `detect_auth_patterns` | `auth_detector.detect_auth_patterns()` | `process_id` | Auth analysis |
| `generate_test_cases` | `test_case_generator.generate_test_cases()` | `process_id` | Test case array |
| `export_abcd_workspace` | `abcd_export.export_workspace()` | `process_id` | Zip file path |

## Integration Points with Existing Code

### Reading Capture Data

All modules read from existing models via the DB session. Key queries:

```python
# HAR data — from capture_sessions router pattern
session = await db.get(CaptureSession, session_id)
har_data = json.loads(session.har_data) if session.har_data else None

# Click events
clicks = await db.execute(
    select(ClickEvent).where(ClickEvent.process_id == process_id)
    .order_by(ClickEvent.timestamp)
)

# Narrations
narrations = await db.execute(
    select(Narration).where(Narration.process_id == process_id)
    .order_by(Narration.timestamp)
)

# Timeline events
events = await db.execute(
    select(TimelineEvent).where(TimelineEvent.process_id == process_id)
    .order_by(TimelineEvent.timestamp)
)

# Messages
messages = await db.execute(
    select(Message).where(Message.process_id == process_id)
    .order_by(Message.timestamp)
)

# Questions
questions = await db.execute(
    select(Question).where(Question.process_id == process_id)
)
```

### Router Registration

```python
# In main.py, add:
from app.routers import abcd_export
app.include_router(abcd_export.router)
```

### MCP Tool Registration

```python
# In mcp_server.py, after browser tools:
# ── ABCD Export tools ──────────────────────────────
@mcp.tool()
async def generate_wdl_draft(process_id: str) -> str:
    ...
```
