# ABCD Alignment Deep Dive

## Aligning the Web API Spec Elicitation Agent with the ABCD Library

**Document Version:** 1.0
**Date:** 2026-02-22
**Status:** Strategic Planning
**Audience:** Engineering, Product, Architecture

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current State: Where We Are](#2-current-state-where-we-are)
3. [The Closed Loop Vision](#3-the-closed-loop-vision)
4. [Semantic Alignment Analysis](#4-semantic-alignment-analysis)
5. [Missing Capabilities: What the Elicitation Agent Should Add](#5-missing-capabilities-what-the-elicitation-agent-should-add)
6. [Missing Capabilities: What ABCD Could Leverage](#6-missing-capabilities-what-abcd-could-leverage)
7. [Export Format Alignment](#7-export-format-alignment)
8. [Agentic Workflow: Claude as Orchestrator](#8-agentic-workflow-claude-as-orchestrator)
9. [Technical Integration Points](#9-technical-integration-points)
10. [Proposed Roadmap](#10-proposed-roadmap)
11. [Data Model Changes](#11-data-model-changes)
12. [New MCP Tools for ABCD Integration](#12-new-mcp-tools-for-abcd-integration)

---

## 1. Executive Summary

### What This Alignment Means

The Web API Spec Elicitation Agent and the ABCD library (Adopt | Agent | Action | Automation Builder) represent two halves of a **complete agentic workflow creation loop**. The Elicitation Agent is the **observation and capture** system -- it watches humans interact with web applications, records every click, network request, voice narration, and screenshot. The ABCD library is the **creation and deployment** system -- it takes API specifications and transforms them into tested, versioned, deployable automated workflows.

Today, these two systems are connected only by a thin handoff: the Elicitation Agent exports HAR files, and ABCD can consume HAR data as input for workflow creation. This document analyzes the full surface area of alignment opportunities and proposes a phased roadmap to close the gap between "I watched you do it" and "I can now do it automatically, tested and deployed."

### Why This Matters

The strategic value of alignment is threefold:

1. **Reduced time-to-automation.** A human demonstrates a workflow once in the browser. The system captures it, understands it, generates the WDL, validates it, tests it, and publishes it -- with Claude orchestrating every step. What currently takes hours of manual API specification writing, WDL authoring, and iterative testing becomes a conversational process measured in minutes.

2. **Closed-loop quality.** The Elicitation Agent's MCP browser control tools allow Claude to *verify* the automations it creates by replaying them in a real browser. This closes the loop between creation and validation in a way that neither system can achieve alone.

3. **Semantic continuity.** When concepts flow naturally between the two systems -- when a "Project" cleanly maps to an "Environment," when a "Process" becomes an "Action" -- the cognitive overhead for both humans and AI agents drops dramatically. The user thinks in one mental model, and the systems translate seamlessly.

### The Big Picture

```
+---------------------------------------------------------------------+
|                    CLOSED-LOOP CREATION PROCESS                     |
|                                                                     |
|  [1] HUMAN DEMONSTRATES          [2] SYSTEM CAPTURES                |
|  +----------------------+        +-------------------------+        |
|  | User clicks through  |  --->  | Chrome Extension        |        |
|  | a web application    |        | - Clicks & form inputs  |        |
|  | while narrating      |        | - HAR network traffic   |        |
|  +----------------------+        | - Voice narrations      |        |
|                                  | - Screenshots           |        |
|                                  | - URL navigation        |        |
|                                  +-------------------------+        |
|                                          |                          |
|                                          v                          |
|  [7] VERIFY & ITERATE           [3] ELICITATION AGENT BACKEND       |
|  +----------------------+        +-------------------------+        |
|  | Claude uses browser  |        | FastAPI + SQLite        |        |
|  | MCP tools to replay  |        | - Timeline events       |        |
|  | and verify the       |  <-->  | - Documents & Q&A       |        |
|  | automation works     |        | - Claude chat agent     |        |
|  +----------------------+        | - MCP server (14 tools) |        |
|         ^                        +-------------------------+        |
|         |                                |                          |
|         |                                v                          |
|  [6] ABCD TEST & PUBLISH        [4] EXPORT & TRANSFORM              |
|  +----------------------+        +-------------------------+        |
|  | - Validate WDL       |        | - HAR files             |        |
|  | - Run test cases     |  <---  | - API spec drafts       |        |
|  | - Save draft         |        | - Requirements docs     |        |
|  | - Create version     |        | - Adopt profiles        |        |
|  | - Publish            |        | - WDL-ready data        |        |
|  +----------------------+        +-------------------------+        |
|         ^                                |                          |
|         |                                v                          |
|         |                        [5] ABCD WORKSPACE CREATION        |
|         +-----<------------------+-------------------------+        |
|                                  | - Create environment    |        |
|                                  | - Discover existing APIs|        |
|                                  | - Generate WDL          |        |
|                                  | - Create test cases     |        |
|                                  +-------------------------+        |
|                                                                     |
|  ORCHESTRATOR: Claude Code via MCP (drives all steps)               |
+---------------------------------------------------------------------+
```

---

## 2. Current State: Where We Are

### 2.1 What the Elicitation Agent Currently Outputs

The Elicitation Agent backend (FastAPI, v0.2.0) produces the following artifacts:

| Output Type | Format | Source | Endpoint/Location |
|---|---|---|---|
| HAR files | JSON (HAR 1.2 spec) | Chrome DevTools Protocol via debugger API | `GET /capture-sessions/{id}/har` |
| Project export bundle | JSON (proprietary) | Full project data aggregation | `GET /projects/{id}/export` |
| Timeline download | JSON (proprietary) | Timeline events + clicks + HAR | `GET /timeline/download` |
| API spec drafts | OpenAPI 3.x YAML (embedded in messages) | Claude agent analysis | `get_api_spec_draft` MCP tool |
| Canonical markdown documents | Markdown | `markdown_generator.py` | `GET /projects/{id}/documents` |
| Screenshots | PNG files | Chrome tab capture | `GET /screenshots/{id}/image` |
| Conversation history | JSON messages | Human + agent messages | `GET /chat/history` |
| Narrations | Text (voice transcripts) | Web Speech API recognition | `GET /narrations` |
| Click/interaction events | JSON | Content script DOM tracking | `GET /clicks` |

**Key file references:**
- Export router: `backend/app/routers/export.py`
- Timeline router: `backend/app/routers/timeline.py`
- MCP server: `backend/app/mcp_server.py`
- Models: `backend/app/models.py`
- Capture sessions: `backend/app/routers/capture_sessions.py`

### 2.2 What ABCD Currently Expects as Input

ABCD's creation pipeline expects these inputs:

| Input Type | Format | Purpose | ABCD Entry Point |
|---|---|---|---|
| API specifications | OpenAPI/Swagger JSON or URLs | Defines endpoints, params, auth | `fetch_api_spec.py` |
| HTTP logs (HAR) | HAR JSON | Source material for action creation | Diagnostics analysis |
| Environment config | `.env` file | Credentials (ADOPT_CLIENT_ID, ADOPT_CLIENT_SECRET) | Workspace `.env` |
| Adopt profile | `adopt_profile.json` | base_url, security_params, workflow_params | Action-level config |
| Requirements | `requirements.md` | Natural language description of what the action does | Action workspace |
| Description | `description.txt` | Short action description for discovery | Action workspace |
| Test cases | JSON array | prompt, workflow_params, expected_output, validation | `test_cases/` directory |
| WDL definitions | `widdle.json` | Multi-step workflow definition | Action workspace |
| Discovery index | FAISS + JSON | Existing actions/APIs for reuse | `search_existing_actions.py` |

### 2.3 The Gap

The Elicitation Agent produces rich, multi-modal capture data. ABCD expects structured, API-centric input. The gap is a **translation layer** that converts observed human behavior into the precise structures ABCD needs:

```
ELICITATION AGENT OUTPUT          GAP                    ABCD INPUT
--------------------------    ----------------    ----------------------
HAR files (raw HTTP)     -->  [Filter & Map]  --> API spec (OpenAPI)
Click events             -->  [Sequence]      --> WDL steps
Voice narrations         -->  [Summarize]     --> requirements.md
Project config           -->  [Transform]     --> adopt_profile.json
Screenshots              -->  [Not consumed]  --> (no equivalent)
Chat conversations       -->  [Extract]       --> description.txt
Timeline events          -->  [Correlate]     --> WDL operation order
Questions/answers        -->  [Resolve]       --> Param documentation
```

---

## 3. The Closed Loop Vision

### 3.1 The Three Pillars

The closed loop rests on three pillars, each corresponding to a system role:

```
     ELICITATION AGENT              CLAUDE CODE (MCP)              ABCD LIBRARY
     ==================             =================              ============
     Captures reality               Orchestrates flow              Builds automation
     - Watches users                - Analyzes captures            - Creates workspaces
     - Records network              - Generates specs              - Writes WDL
     - Transcribes speech           - Bridges systems              - Validates & tests
     - Tracks interactions          - Controls browser             - Publishes versions
```

### 3.2 Elicitation Agent Captures, ABCD Creates

The fundamental flow is: **demonstrate once, automate forever.**

A business user opens their browser, starts a capture session in the Elicitation Agent extension, and walks through a workflow -- say, creating a customer in a CRM, then fetching their order history from an ERP. The extension records:

- Every HTTP request/response (HAR) including auth headers, payloads, and timing
- Every click, form input, and dropdown selection (ClickEvents)
- The user's voice narration explaining what they are doing and why (Narrations)
- Screenshots at key moments (Screenshots)
- URL navigation sequence (TimelineEvents)

This raw material is everything ABCD needs to reverse-engineer the API calls and create an automated workflow. The HAR data reveals endpoints, methods, headers, request bodies, and response shapes. The click events and URL changes reveal the *sequence* of operations. The narrations provide *intent* -- why each step matters, what the expected outcome is.

### 3.3 Claude Code (MCP) Orchestrates Both

Claude Code, running in VS Code, connects to both systems via MCP:

**Current MCP tools for the Elicitation Agent (14 tools):**

| Category | Tool | Purpose |
|---|---|---|
| Data | `get_project_context` | Project + processes + recent messages |
| Data | `get_capture_sessions` | HAR data for a process |
| Data | `get_screenshots` | Screenshot metadata and paths |
| Data | `get_conversation_history` | Recent messages |
| Data | `get_api_spec_draft` | Current draft spec from messages |
| Data | `add_message` | Inject message into shared conversation |
| Data | `get_timeline` | Chronological event feed |
| Browser | `browser_get_page_info` | Active tab URL and title |
| Browser | `browser_query_elements` | CSS selector element query |
| Browser | `browser_click_element` | Click element by selector |
| Browser | `browser_type_text` | Type into input by selector |
| Browser | `browser_navigate` | Navigate to URL |
| Browser | `browser_eval_js` | Execute JavaScript in page |
| Browser | `browser_take_screenshot` | Capture screenshot of active tab |

**Proposed MCP tools for ABCD (see Section 12 for full details):**

| Tool | Purpose |
|---|---|
| `abcd_create_workspace` | Initialize ABCD environment + action structure |
| `abcd_discover_apis` | Search FAISS + fuzzy for existing APIs/actions |
| `abcd_generate_wdl` | Generate WDL from captured data |
| `abcd_validate` | Run JSON + WDL structure validation |
| `abcd_test` | Execute test cases against platform |
| `abcd_save_draft` | Persist as unpublished draft version |
| `abcd_publish` | Approve and publish live version |

With both sets of MCP tools, Claude can orchestrate the entire pipeline without human intervention (beyond the initial demonstration).

### 3.4 Browser Control Closes the Loop

The browser MCP tools are the critical differentiator. They allow Claude to:

1. **Verify captured workflows** -- replay the exact sequence of clicks and inputs to confirm the capture is complete
2. **Test generated automations** -- after creating an ABCD action, use the browser to test the API endpoints directly
3. **Debug failures** -- when a test fails, navigate to the API documentation, inspect response codes, and adjust the WDL
4. **Iterate on specifications** -- if the generated OpenAPI spec is missing an endpoint, Claude can navigate to the API docs in the browser and extract the missing information

This transforms the workflow from a one-shot "export and hope" to an iterative, self-correcting loop:

```
Capture --> Analyze --> Generate WDL --> Test (via browser) --> Fix --> Re-test --> Publish
                ^                                                  |
                +--------------------------------------------------+
                              (Claude iterates until passing)
```

---

## 4. Semantic Alignment Analysis

### 4.1 Concept Mapping Table

| Elicitation Agent Concept | ABCD Concept | Alignment Quality | Notes |
|---|---|---|---|
| **Project** | **Environment** | PARTIAL | Both are top-level containers. Project has name, description, base_url. Environment has .env credentials and adopt_profile.json. Project is broader (multiple APIs), Environment is more deployment-focused (staging/prod). |
| **Process** | **Action** | STRONG | Both represent a discrete unit of work. Process captures one workflow; Action automates one workflow. Process.name maps to action description. Process.base_url maps to adopt_profile.base_url. |
| **CaptureSession** | (no equivalent) | MISSING | CaptureSession represents a recording session within a process. ABCD has no concept of "how was this created." This is provenance data that ABCD ignores. |
| **TimelineEvent** | **HTTP Network Logs / WDL Steps** | PARTIAL | Timeline events of type `network_request` map to HTTP logs ABCD can diagnose. The sequence of events maps to WDL step ordering. But most timeline event types (click, narration, url_change) have no ABCD equivalent. |
| **ClickEvent** | **WDL REST/JQ_FILTER Steps** | WEAK | Clicks and form inputs represent UI interactions. ABCD works at the API level, not the UI level. However, click sequences *imply* API call sequences, and form input values *imply* request parameters. |
| **Document** | **requirements.md / description.txt** | STRONG | Documents map directly to ABCD's requirements.md (detailed) and description.txt (short). The `is_canonical` flag aligns with the "official" requirements concept. |
| **Message (human)** | **requirements.md (intent sections)** | MODERATE | Human messages often express intent ("I want to automate the customer lookup flow"). These can seed requirements.md. |
| **Message (agent)** | **API spec drafts** | MODERATE | Agent messages containing OpenAPI YAML map to ABCD's API spec inputs. The `get_api_spec_draft` MCP tool already extracts these. |
| **Narration** | **requirements.md (narration sections)** | STRONG | Voice narrations are natural language descriptions of what the user is doing and why. These are the richest source for requirements.md content. |
| **Screenshot** | (no equivalent) | MISSING | ABCD has no visual context concept. Screenshots could inform API documentation or be used for visual regression testing, but ABCD does not consume them. |
| **Question** | (no equivalent) | MISSING | Questions about unclear aspects of the workflow have no ABCD counterpart. They could feed into requirements.md as "open questions" or "assumptions." |
| **ChatSession** | (no equivalent) | MISSING | Chat sessions are conversational context. ABCD does not model conversation history as part of its workspace. |
| **HAR file** | **API specs + HTTP logs** | STRONG | HAR is the primary bridge artifact. ABCD's diagnostics can analyze HAR-format HTTP logs. HAR entries can be transformed into OpenAPI spec fragments. |
| **base_url (Project/Process)** | **adopt_profile.base_url** | DIRECT | Both store the target API base URL. Direct field mapping. |
| (no equivalent) | **Agent (Uber Agent)** | MISSING | The Elicitation Agent has no concept of multi-action orchestration. |
| (no equivalent) | **WDL (Workflow Definition Language)** | MISSING | No WDL awareness. The agent captures *what* happened but does not generate *how to reproduce it*. |
| (no equivalent) | **Versions / Drafts** | MISSING | No versioning or draft lifecycle for captured workflows. |
| (no equivalent) | **Discovery (FAISS + fuzzy)** | MISSING | No ability to search existing ABCD actions or APIs. |
| (no equivalent) | **Test Cases** | MISSING | No test case generation from captured data. |
| (no equivalent) | **security_params** | MISSING | No modeling of authentication schemes (API keys, OAuth, etc.). |
| (no equivalent) | **profiles_map** | MISSING | No multi-API profile support. |
| (no equivalent) | **Tool Mode** | MISSING | No concept of deploying a workflow as a sub-tool. |

### 4.2 Alignment Depth Analysis

```
CONCEPT ALIGNMENT SPECTRUM

DIRECT (1:1 mapping, minimal transformation):
  base_url <==> adopt_profile.base_url
  HAR file <==> HTTP logs / API spec input

STRONG (clear mapping, some transformation needed):
  Process  <==> Action
  Document <==> requirements.md / description.txt
  Narration <==> requirements.md narration sections

PARTIAL (conceptual overlap, significant transformation):
  Project  <==> Environment
  Timeline <==> WDL step ordering + HTTP logs
  Messages <==> requirements.md + API spec drafts

WEAK (exists in one, implied in the other):
  ClickEvent <==> WDL steps (UI-level vs API-level)
  Question   <==> Assumptions in requirements

MISSING (exists only in one system):
  CaptureSession, Screenshot, ChatSession  (Elicitation only)
  WDL, Versions, Drafts, Discovery, Agents, Tool Mode,
  Test Cases, security_params, profiles_map  (ABCD only)
```

### 4.3 Critical Divergences

**1. Level of abstraction.** The Elicitation Agent operates at the **UI interaction level** (clicks, form inputs, page navigations). ABCD operates at the **API call level** (REST endpoints, request/response schemas, JQ transformations). The translation from UI-level to API-level is the core intellectual challenge. A single "click on Submit" might trigger 3 API calls; a single API call might require understanding 5 form field values that were entered across 30 seconds of UI interaction.

**2. Lifecycle orientation.** The Elicitation Agent is **session-oriented** -- it captures a moment in time (a capture session with a start and stop). ABCD is **version-oriented** -- it manages an evolving series of drafts and published versions. There is no concept of "this capture session produced v1, this later session produced v2."

**3. Multi-API awareness.** The Elicitation Agent treats each process as a single workflow with one base_url. ABCD's `profiles_map` allows a single action to orchestrate calls across multiple APIs with different base URLs and authentication. The Elicitation Agent does not model this.

**4. Deployment context.** The Elicitation Agent has no concept of staging vs. production environments, authentication credential management, or deployment state. ABCD's Environment model with `.env` files and `adopt_profile.json` is fundamentally about deployment context.

---

## 5. Missing Capabilities: What the Elicitation Agent Should Add

### 5.1 Environment Management (Staging/Production Targets)

**ABCD concept:** Environments are top-level workspace containers with isolated credentials and configurations. A `workspaces/staging/` environment might point to `https://api-staging.example.com` while `workspaces/production/` points to `https://api.example.com`.

**What the Elicitation Agent needs:**

The current `Project` model has a single `base_url` field. It needs:

- An **environment** dimension (staging, production, development) per project
- Per-environment credential storage (or references to credential stores)
- The ability to associate capture sessions with a specific environment ("this was captured against staging")
- Export that produces environment-specific `adopt_profile.json` files

**Implementation sketch:**

```python
class Environment(Base):
    __tablename__ = "environments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(100))  # "staging", "production"
    base_url: Mapped[str] = mapped_column(String(2000))
    credentials_json: Mapped[str] = mapped_column(Text, default="{}")  # encrypted
    adopt_profile_json: Mapped[str] = mapped_column(Text, default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
```

### 5.2 Draft/Version Lifecycle

**ABCD concept:** Actions evolve through a lifecycle: creation, draft, versioned release, publication. Drafts are unpublished working copies. Versions (v1, v2, ...) are immutable snapshots stored in `versions/`. Publication makes a version live on the platform.

**What the Elicitation Agent needs:**

Currently, capture sessions and documents have no versioning. The agent needs:

- Versioned snapshots of generated artifacts (API specs, WDL drafts, requirements)
- Draft status tracking ("this WDL is a draft, not yet validated")
- Version history showing the evolution of a workflow definition
- The ability to roll back to a previous version

**Implementation sketch:**

```python
class ArtifactVersion(Base):
    __tablename__ = "artifact_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id"))
    artifact_type: Mapped[str]  # "wdl", "api_spec", "requirements", "adopt_profile"
    version_number: Mapped[int]
    status: Mapped[str]  # "draft", "validated", "published"
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime]
    created_by: Mapped[str]  # "user", "claude", "auto"
```

### 5.3 API Discovery Integration

**ABCD concept:** Before creating a new action, ABCD searches for existing APIs and actions using dual-mode discovery (FAISS semantic vectors + fuzzy string matching). This avoids duplicating work and enables reuse.

**What the Elicitation Agent needs:**

- When a user demonstrates a workflow, the agent should check ABCD's discovery index: "Does an action for this API already exist?"
- Surface existing actions in the chat interface: "I found an existing action `CRM_CreateCustomer_v3` that matches this workflow. Should I extend it or create a new one?"
- Feed captured API endpoints into the discovery index so future searches can find them

**Integration point:** This requires the Elicitation Agent to call ABCD CLI commands (`search_existing_actions.py`, `search_existing_apis.py`) or expose them as MCP tools.

### 5.4 WDL Awareness

**ABCD concept:** WDL (Workflow Definition Language) is a JSON-based definition format with operations: REST, JQ_FILTER, EXTRACT, PROJECT, PROMPT, CONDITION, FOR_EACH, PAGINATE, OUTPUT_TEXT, PROMPT_AND_TOOLS_AGENT.

**What the Elicitation Agent needs:**

- A WDL preview/editor in the extension sidebar or a dedicated document type
- The ability for the Claude chat agent to generate WDL from timeline data
- WDL validation feedback in the capture workflow
- Bidirectional mapping: timeline events to WDL steps, and WDL steps back to original capture events

**Example mapping (timeline to WDL):**

```
Timeline Events:                     Generated WDL Steps:
-------------------                  --------------------
1. Navigate to /api/customers        1. REST: GET /api/customers
2. Click "New Customer" button          (setup step)
3. Fill in "name" field: "Acme"      2. REST: POST /api/customers
4. Fill in "email" field: "a@b.com"     body: {name: "{{name}}", email: "{{email}}"}
5. Click "Submit" button             3. JQ_FILTER: .id
6. See success message                  (extract customer_id from response)
7. Navigate to /api/orders?cid=123   4. REST: GET /api/orders?customer_id={{customer_id}}
8. See order list                    5. OUTPUT_TEXT: "Found {{.length}} orders"
```

### 5.5 Test Case Generation from Captured Data

**ABCD concept:** Test cases are JSON definitions with: `prompt` (natural language instruction), `workflow_params` (input parameters), `expected_output` (what the action should produce), and `validation` (similarity, exact, contains).

**What the Elicitation Agent needs:**

- Automatic test case generation from capture sessions: each capture session represents one "happy path" test case
- The click events and form inputs provide `workflow_params`
- The API responses provide `expected_output` (or patterns thereof)
- Voice narrations provide the `prompt` (what the user said they wanted to do)

**Example generation:**

```json
{
  "test_case_name": "Create customer and fetch orders",
  "prompt": "Create a new customer named Acme Corp and retrieve their order history",
  "workflow_params": {
    "customer_name": "Acme Corp",
    "customer_email": "contact@acme.com"
  },
  "expected_output": "Found 3 orders for customer Acme Corp",
  "validation": {
    "type": "contains",
    "value": "orders for customer"
  },
  "source": {
    "capture_session_id": "abc-123",
    "generated_from": "narration + form_inputs + api_responses"
  }
}
```

### 5.6 Adopt Profile / Security Params Management

**ABCD concept:** `adopt_profile.json` contains `base_url`, `security_params` (authentication configuration), `workflow_params` (runtime parameters), and `profiles_map` (multi-API routing).

**What the Elicitation Agent needs:**

- Detection of authentication patterns from HAR data (Bearer tokens, API keys, OAuth flows, cookies)
- A security configuration UI or automatic inference: "I see all requests include an `Authorization: Bearer <token>` header -- this API uses Bearer token authentication"
- Storage of detected auth patterns per-project, per-environment
- Export of `adopt_profile.json` with security_params populated

**Detection heuristic from HAR:**

```python
def detect_auth_from_har(har_entries):
    """Analyze HAR entries to detect authentication patterns."""
    patterns = {
        "bearer_token": False,
        "api_key_header": None,
        "api_key_query": None,
        "basic_auth": False,
        "cookie_auth": False,
        "oauth2": False,
    }
    for entry in har_entries:
        headers = {h["name"].lower(): h["value"] for h in entry["request"]["headers"]}
        if "authorization" in headers:
            auth_value = headers["authorization"]
            if auth_value.startswith("Bearer "):
                patterns["bearer_token"] = True
            elif auth_value.startswith("Basic "):
                patterns["basic_auth"] = True
        if "x-api-key" in headers:
            patterns["api_key_header"] = "X-API-Key"
        # Check query params for api_key patterns
        for param in entry["request"].get("queryString", []):
            if param["name"].lower() in ("api_key", "apikey", "key", "access_token"):
                patterns["api_key_query"] = param["name"]
    return patterns
```

### 5.7 Multi-API Profile Support (profiles_map)

**ABCD concept:** `profiles_map` allows a single action to make calls to multiple APIs, each with its own base_url and security parameters. For example, an action that reads from a CRM and writes to an ERP.

**What the Elicitation Agent needs:**

- Detection of multiple API base URLs within a single capture session
- Automatic grouping of network requests by base URL
- UI to name and configure each detected API
- Export that generates `profiles_map` entries

Currently, `Process.base_url` is a single string. A capture session that hits both `https://crm.example.com/api` and `https://erp.example.com/api` would need to generate:

```json
{
  "profiles_map": {
    "crm": {
      "base_url": "https://crm.example.com/api",
      "security_params": { "type": "bearer" }
    },
    "erp": {
      "base_url": "https://erp.example.com/api",
      "security_params": { "type": "api_key", "header": "X-ERP-Key" }
    }
  }
}
```

### 5.8 Tool Mode / Deployment Rules

**ABCD concept:** An action can be flagged as a "tool" (`tool_mode: true`), making it available as a sub-tool for Uber Agents. This determines how the action appears in the platform's tool registry.

**What the Elicitation Agent needs:**

- A metadata field on Process indicating whether this workflow should be a standalone action or a tool for composition
- UI toggle in the extension: "This workflow should be reusable as a building block"
- Export that includes the `tool_mode` flag

### 5.9 Diagnostic Integration

**ABCD concept:** ABCD's diagnostics module analyzes HTTP logs to detect issues: trailing slash mismatches, missing required parameters, HTTP method mismatches, unexpected response codes.

**What the Elicitation Agent needs:**

- Feed captured HTTP logs (from HAR) into ABCD's diagnostic engine during the capture session
- Surface diagnostic warnings in real-time: "Warning: Your request to `/api/customers` returned 301 -- the API expects a trailing slash"
- Store diagnostic results as timeline events for later review

---

## 6. Missing Capabilities: What ABCD Could Leverage

### 6.1 Rich Interaction Data (Clicks, Inputs, Form Submissions)

**What the Elicitation Agent provides:**

The `ClickEvent` model captures:
- `event_type`: click, input, change, submit
- `tag_name`, `element_id`, `class_name`, `selector`: DOM context
- `text_content`: visible text of the clicked element
- `input_type`, `value`, `field_name`: form field data
- `x`, `y`: click coordinates
- `url`: page URL when the event occurred

**What ABCD could do with this:**

1. **Parameter extraction.** Form field values (name, email, quantity) directly map to `workflow_params` in WDL. When a user fills in a "Customer Name" field with "Acme Corp," ABCD can parameterize this as `{{customer_name}}` in the generated WDL.

2. **Step ordering.** The sequence of clicks reveals the intended order of operations. A click on "Search" followed by a click on "Export CSV" implies a two-step workflow.

3. **Conditional logic detection.** If the user clicks different buttons based on what they see on screen (e.g., "Approve" vs "Reject"), this implies CONDITION operations in WDL.

4. **Page context for API correlation.** A click on a specific page helps correlate which API calls are triggered by which user action.

### 6.2 Voice Narration of Workflows

**What the Elicitation Agent provides:**

The `Narration` model captures timestamped voice transcripts via the Web Speech API. The user says things like: "Now I'm going to look up the customer by their email address" or "This step is important because the order won't process without the tax ID."

**What ABCD could do with this:**

1. **Natural language requirements.** Narrations are the most natural source for `requirements.md`. They capture intent, context, and edge cases that are never visible in HTTP traffic.

2. **Test case prompts.** The narration "Look up the customer by their email address" is almost exactly the `prompt` field in an ABCD test case.

3. **Step descriptions in WDL.** Each WDL step can have a description. Narrations provide human-readable descriptions for each step: "This step fetches the customer record using their email."

4. **Disambiguation.** When HAR data shows 5 API calls triggered by a single button click, the narration helps identify which call is the "main" one vs. analytics/logging side effects.

### 6.3 Real-Time Timeline Events

**What the Elicitation Agent provides:**

The `TimelineEvent` model provides a unified, chronologically ordered stream of events:
- `event_type`: message, screenshot, click, narration, url_change, capture_start, capture_stop, har_start, har_stop, network_request, input, change
- `summary`: human-readable description
- `metadata_json`: structured data (URL, method, status code, element details)
- `timestamp`: precise ordering

**What ABCD could do with this:**

1. **Workflow sequence reconstruction.** The timeline provides the exact order of operations, including timing gaps that might indicate waiting for asynchronous operations.

2. **Correlation analysis.** By aligning timeline events with HAR entries by timestamp, ABCD can determine: "The user clicked 'Submit' at 14:32:05, and 200ms later a POST to `/api/orders` was made. The click *caused* the API call."

3. **Error detection.** Timeline events can reveal: "The user clicked 'Submit' twice in rapid succession -- the first produced a 500 error, the second succeeded. The WDL should include retry logic."

4. **Parallel operation detection.** Multiple API calls happening simultaneously (same timestamp) suggest the WDL could use parallel execution rather than sequential steps.

### 6.4 Screenshot-Based Visual Context

**What the Elicitation Agent provides:**

The `Screenshot` model stores PNG captures of the browser tab at specific moments, linked to URLs and timestamps.

**What ABCD could do with this:**

1. **Visual validation.** After generating and running a WDL, Claude could take a screenshot via browser MCP tools and compare it to the original capture screenshot to verify the automation produced the same visual result.

2. **Documentation.** Screenshots can be embedded in `requirements.md` as visual references: "The workflow starts from this dashboard view [screenshot]."

3. **UI element mapping.** Combined with click coordinates, screenshots allow visual mapping of where the user clicked, helping disambiguate complex UIs.

### 6.5 Network Events with Timing and Metadata

**What the Elicitation Agent provides:**

Timeline events of type `network_request` include metadata:
```json
{
  "url": "https://api.example.com/customers",
  "method": "POST",
  "status": 201,
  "request_body": "{\"name\": \"Acme\"}",
  "response_body": "{\"id\": 123, \"name\": \"Acme\"}",
  "duration_ms": 245,
  "headers": {...}
}
```

**What ABCD could do with this:**

1. **Direct WDL REST step generation.** Each network request maps almost 1:1 to a WDL REST operation with method, URL, headers, and body.

2. **Response schema inference.** Response bodies provide concrete examples for expected output shapes, enabling JQ_FILTER step generation.

3. **Performance baselines.** Timing data can set expectations for test case timeouts.

4. **Dependency chain detection.** When a response body value (e.g., `id: 123`) appears in a subsequent request URL (e.g., `/customers/123/orders`), ABCD can detect the data dependency and create appropriate JQ_FILTER + variable passing in WDL.

### 6.6 Structured Questions About the Workflow

**What the Elicitation Agent provides:**

The `Question` model tracks open questions with `content`, `answer`, `status` (open, answered, resolved), and `context_url`.

**What ABCD could do with this:**

1. **Requirements completeness.** Unanswered questions indicate gaps in the workflow specification. ABCD could refuse to generate WDL until critical questions are resolved.

2. **Edge case documentation.** Resolved questions often capture edge cases: "Q: What happens if the customer already exists? A: The API returns 409 Conflict." This feeds directly into CONDITION operations in WDL.

3. **Parameter documentation.** Questions like "What format should the date be in?" with answer "ISO 8601" provide parameter validation rules for WDL.

---

## 7. Export Format Alignment

### 7.1 Current HAR Export vs. What ABCD Expects

**Current HAR export:**

The Elicitation Agent stores raw HAR files captured via Chrome's debugger API. These are standard HAR 1.2 format with full request/response data. Accessed via:

```
GET /capture-sessions/{session_id}/har
```

Returns: Standard HAR JSON with `log.entries[]` containing request URLs, methods, headers, postData, response status, content, timing.

**What ABCD expects:**

ABCD's diagnostics module processes HTTP logs in HAR format, so the raw format is compatible. However, ABCD also needs:

1. **Filtered HAR.** ABCD wants API calls, not asset loads. The raw HAR includes requests for CSS, JS, images, fonts, analytics, etc. ABCD needs a filtered view containing only API calls (typically identified by `Content-Type: application/json` responses or URL patterns).

2. **Annotated HAR.** ABCD benefits from annotations: which requests are "primary" workflow steps vs. "side effect" calls (analytics, logging), which requests are authentication flows vs. business logic.

3. **Parameterized HAR.** ABCD needs to know which values in the HAR are constants vs. variables. The customer name "Acme" should be marked as a parameter; the API version `/v1/` should be marked as a constant.

**Recommended new export: Filtered Annotated HAR**

```json
{
  "har_version": "1.2",
  "metadata": {
    "source": "elicitation-agent",
    "capture_session_id": "abc-123",
    "process_id": "def-456",
    "filter_applied": "api_calls_only",
    "total_entries_before_filter": 147,
    "total_entries_after_filter": 12
  },
  "log": {
    "entries": [
      {
        "startedDateTime": "2026-02-22T14:32:05.000Z",
        "request": { "...standard HAR..." },
        "response": { "...standard HAR..." },
        "_annotations": {
          "step_number": 1,
          "is_primary": true,
          "related_click_event_id": "click-789",
          "related_narration": "Looking up the customer",
          "detected_params": [
            { "path": "$.request.postData.text.name", "suggested_var": "customer_name" }
          ]
        }
      }
    ]
  }
}
```

### 7.2 WDL-Ready Export Format

Beyond filtered HAR, the Elicitation Agent should offer a **WDL-ready** export that is one step closer to what ABCD needs:

```json
{
  "wdl_draft": {
    "version": "1.0",
    "source": "elicitation-agent-auto",
    "steps": [
      {
        "operation": "REST",
        "description": "Create new customer",
        "method": "POST",
        "url": "{{base_url}}/api/customers",
        "headers": {
          "Authorization": "Bearer {{access_token}}",
          "Content-Type": "application/json"
        },
        "body": {
          "name": "{{customer_name}}",
          "email": "{{customer_email}}"
        },
        "expected_status": 201,
        "source_har_entry_index": 0,
        "source_timeline_events": ["event-1", "event-2", "event-3"]
      },
      {
        "operation": "JQ_FILTER",
        "description": "Extract customer ID from response",
        "expression": ".id",
        "output_var": "customer_id",
        "inferred_from": "value 123 appears in next request URL"
      },
      {
        "operation": "REST",
        "description": "Fetch customer orders",
        "method": "GET",
        "url": "{{base_url}}/api/orders?customer_id={{customer_id}}",
        "headers": {
          "Authorization": "Bearer {{access_token}}"
        },
        "expected_status": 200,
        "source_har_entry_index": 1,
        "source_timeline_events": ["event-5"]
      }
    ],
    "detected_params": {
      "base_url": "https://api.example.com",
      "customer_name": { "example": "Acme Corp", "source": "form_input" },
      "customer_email": { "example": "contact@acme.com", "source": "form_input" },
      "access_token": { "source": "auth_header", "note": "Bearer token detected" }
    }
  }
}
```

### 7.3 API Spec Discovery Format Alignment

**Current state:** The Elicitation Agent's Claude chat agent can generate OpenAPI YAML snippets embedded in messages. These are informal and unstructured.

**ABCD expects:** API specifications accessible as JSON files at known paths, or URLs that can be fetched via `fetch_api_spec.py`.

**Alignment needed:**

| Current | Target | Change |
|---|---|---|
| OpenAPI YAML in chat messages | Structured JSON files in `apis/` directory format | Generate and store OpenAPI JSON as Documents with `doc_type: "api_spec"` |
| Single spec per project | Multiple specs per process (one per API) | Support multiple API spec documents per process |
| No manifest | `manifest.json` listing available specs | Generate manifest file alongside specs |
| No API discovery metadata | FAISS-indexed descriptions | Include description embeddings for discovery |

**Target export structure (matching ABCD's `apis/` directory):**

```json
{
  "apis": {
    "manifest": {
      "apis": [
        {
          "id": "crm-api",
          "name": "CRM Customer API",
          "description": "CRUD operations for customer management",
          "spec_format": "openapi-3.0",
          "base_url": "https://crm.example.com/api/v1"
        }
      ]
    },
    "specs": {
      "crm-api": {
        "openapi": "3.0.3",
        "info": { "title": "CRM Customer API", "version": "1.0" },
        "paths": { "..." }
      }
    }
  }
}
```

### 7.4 Test Case Format Alignment

**Current state:** The Elicitation Agent has no test case concept.

**ABCD test case format:**

```json
[
  {
    "test_case_name": "happy_path_create_customer",
    "prompt": "Create a new customer named {{customer_name}}",
    "workflow_params": {
      "customer_name": "Test Customer",
      "customer_email": "test@example.com"
    },
    "expected_output": "Customer created successfully with ID",
    "validation": {
      "type": "contains",
      "value": "Customer created successfully"
    }
  }
]
```

**Generation from captured data:**

| ABCD Test Case Field | Elicitation Agent Source |
|---|---|
| `test_case_name` | Process name + "happy_path" |
| `prompt` | First narration text or human message content |
| `workflow_params` | Extracted from ClickEvent values where `event_type` is "input" |
| `expected_output` | Last API response body summary or final narration |
| `validation.type` | Default "contains"; "exact" if response is simple |
| `validation.value` | Key phrase from response or narration |

### 7.5 Adopt Profile Generation from Captured Data

**Target `adopt_profile.json` generation:**

```json
{
  "base_url": "https://api.example.com",
  "security_params": {
    "type": "bearer",
    "token_env_var": "API_ACCESS_TOKEN"
  },
  "workflow_params": {
    "customer_name": {
      "type": "string",
      "description": "Name of the customer to create",
      "required": true,
      "example": "Acme Corp"
    },
    "customer_email": {
      "type": "string",
      "description": "Email address of the customer",
      "required": true,
      "example": "contact@acme.com"
    }
  },
  "profiles_map": {}
}
```

**Sources for each field:**

| Field | Source in Elicitation Agent |
|---|---|
| `base_url` | `Process.base_url` or most common base URL in HAR entries |
| `security_params` | Inferred from HAR request headers (see Section 5.6) |
| `workflow_params` | Extracted from ClickEvent input values + narration context |
| `profiles_map` | Generated when multiple distinct API base URLs are detected in HAR |

---

## 8. Agentic Workflow: Claude as Orchestrator

### 8.1 The Complete Orchestration Sequence

The following describes the full agentic loop, step by step, with Claude Code as the orchestrating intelligence via MCP tools.

```
STEP  ACTOR              ACTION                           MCP TOOL(S) USED
----  -----------------  -------------------------------  ---------------------------
 1    Human              Demonstrates workflow in browser  (manual browser interaction)
 2    Chrome Extension   Captures clicks, HAR, voice       (extension content scripts)
 3    Elicitation Backend Stores all data, emits timeline  (FastAPI endpoints)
 4    Claude Code        Reads project context              get_project_context
 5    Claude Code        Reads capture session data          get_capture_sessions
 6    Claude Code        Reads timeline events               get_timeline
 7    Claude Code        Analyzes conversation                get_conversation_history
 8    Claude Code        Checks for existing ABCD actions    abcd_discover_apis
 9    Claude Code        Generates API spec from HAR          (internal analysis)
10    Claude Code        Generates WDL from timeline+HAR      abcd_generate_wdl
11    Claude Code        Validates WDL structure               abcd_validate
12    Claude Code        Creates ABCD workspace                abcd_create_workspace
13    Claude Code        Generates test cases                  (from capture data)
14    Claude Code        Runs test cases                       abcd_test
15    Claude Code        Opens browser to verify               browser_navigate
16    Claude Code        Replays workflow in browser            browser_click_element,
                                                               browser_type_text
17    Claude Code        Compares results to expected           browser_get_page_info,
                                                               browser_take_screenshot
18    Claude Code        Fixes WDL if tests fail               abcd_generate_wdl (iterate)
19    Claude Code        Re-validates and re-tests              abcd_validate, abcd_test
20    Claude Code        Saves as draft                         abcd_save_draft
21    Claude Code        Publishes version                      abcd_publish
22    Claude Code        Reports results to user                add_message
```

### 8.2 Detailed Phase Walkthrough

#### Phase A: Capture and Analysis (Steps 1-7)

```python
# Claude Code's internal reasoning:
#
# 1. User has completed a capture session for process "Create Customer Order"
# 2. I need to understand what was captured

# Step 4: Get project context
project = await mcp.call("get_project_context", {"project_id": "proj-123"})
# Returns: project name, description, processes list, recent messages

# Step 5: Get capture data
captures = await mcp.call("get_capture_sessions", {"process_id": "proc-456"})
# Returns: session status, HAR data, click tracking config

# Step 6: Get timeline
timeline = await mcp.call("get_timeline", {
    "project_id": "proj-123",
    "process_id": "proc-456",
    "limit": 200
})
# Returns: chronological events with metadata

# Step 7: Get conversation for context
history = await mcp.call("get_conversation_history", {
    "project_id": "proj-123",
    "limit": 50
})
# Returns: human + agent messages with timestamps
```

#### Phase B: Discovery and Generation (Steps 8-13)

```python
# Step 8: Check for existing ABCD actions
existing = await mcp.call("abcd_discover_apis", {
    "query": "create customer order CRM",
    "search_mode": "semantic"
})
# Returns: matching actions with similarity scores

# Step 9-10: Generate WDL from captured data
wdl = await mcp.call("abcd_generate_wdl", {
    "process_id": "proc-456",
    "har_data": captures["har_data"],
    "timeline_events": timeline["timeline"],
    "narrations": narrations,
    "base_url": "https://api.example.com"
})
# Returns: generated widdle.json content

# Step 11: Validate WDL
validation = await mcp.call("abcd_validate", {
    "wdl_content": wdl,
    "action_id": "create-customer-order"
})
# Returns: {valid: true/false, errors: [...], warnings: [...]}

# Step 12: Create workspace
workspace = await mcp.call("abcd_create_workspace", {
    "environment": "staging",
    "action_id": "create-customer-order",
    "wdl": wdl,
    "requirements": requirements_md,
    "adopt_profile": profile_json,
    "test_cases": test_cases
})
# Returns: workspace path and structure

# Step 13: Generate test cases from capture data
test_cases = generate_test_cases_from_capture(
    click_events=clicks,
    narrations=narrations,
    har_responses=har_data
)
```

#### Phase C: Testing and Verification (Steps 14-19)

```python
# Step 14: Run ABCD test cases
test_results = await mcp.call("abcd_test", {
    "action_id": "create-customer-order",
    "environment": "staging"
})
# Returns: {passed: 3, failed: 1, results: [...]}

# Step 15-17: Browser verification
await mcp.call("browser_navigate", {"url": "https://crm.example.com/customers"})
page_info = await mcp.call("browser_get_page_info")
# Navigate to the CRM and verify the customer was created

await mcp.call("browser_query_elements", {"selector": "table.customers tbody tr"})
# Check that the customer appears in the customer list

screenshot = await mcp.call("browser_take_screenshot")
# Visual verification

# Step 18-19: Iterate if needed
if test_results["failed"] > 0:
    # Analyze failure, adjust WDL, re-validate, re-test
    fixed_wdl = adjust_wdl(wdl, test_results["results"])
    await mcp.call("abcd_validate", {"wdl_content": fixed_wdl})
    await mcp.call("abcd_test", {"action_id": "create-customer-order"})
```

#### Phase D: Publication (Steps 20-22)

```python
# Step 20: Save as draft
draft = await mcp.call("abcd_save_draft", {
    "action_id": "create-customer-order",
    "environment": "staging"
})
# Returns: {draft_version: 1, path: "..."}

# Step 21: Publish (after human approval or auto-approval)
published = await mcp.call("abcd_publish", {
    "action_id": "create-customer-order",
    "version": 1,
    "environment": "staging"
})
# Returns: {published: true, version: 1, platform_id: "..."}

# Step 22: Report to user
await mcp.call("add_message", {
    "project_id": "proj-123",
    "content": "Workflow 'Create Customer Order' has been published as v1. "
               "3/3 test cases passed. The action is now live on staging.",
    "role": "agent"
})
```

### 8.3 Error Handling and Recovery

The orchestration must handle failures at every step:

| Failure Point | Recovery Strategy |
|---|---|
| Empty capture session (no HAR) | Prompt user to re-record with HAR capture enabled |
| Discovery finds existing action | Ask user: extend existing or create new? |
| WDL validation fails | Claude analyzes errors, regenerates WDL, retries (up to 3 times) |
| Test cases fail | Claude inspects HAR vs. actual response, adjusts expected values |
| Browser verification fails | Claude takes screenshot, compares to original, identifies divergence |
| Publication fails | Claude checks credentials, environment config, retries with fixes |
| Network timeout | Retry with exponential backoff |

---

## 9. Technical Integration Points

### 9.1 Backend API Endpoints That Could Bridge to ABCD

**Existing endpoints to extend:**

| Endpoint | Current Purpose | Bridge Extension |
|---|---|---|
| `GET /projects/{id}/export` | Export full project JSON | Add `?format=abcd` query param to export ABCD workspace structure |
| `GET /capture-sessions/{id}/har` | Download raw HAR | Add `?filtered=true&annotated=true` for ABCD-optimized HAR |
| `GET /timeline/download` | Download timeline bundle | Add `?format=wdl_draft` to generate WDL-ready structure |
| `POST /chat` | Chat with Claude agent | Extend Claude's system prompt with ABCD awareness |
| `GET /chat/history` | Get conversation | No change needed; already usable as requirements source |

**New endpoints needed:**

| Endpoint | Purpose | Request/Response |
|---|---|---|
| `POST /projects/{id}/export/abcd` | Generate full ABCD workspace bundle | Returns zip with workspace structure |
| `POST /processes/{id}/generate-wdl` | Generate WDL from process captures | Returns WDL JSON draft |
| `POST /processes/{id}/generate-test-cases` | Generate test cases from captures | Returns ABCD test case array |
| `POST /processes/{id}/generate-adopt-profile` | Generate adopt_profile.json | Returns profile JSON |
| `POST /processes/{id}/generate-api-spec` | Generate OpenAPI spec from HAR | Returns OpenAPI JSON |
| `GET /processes/{id}/detect-auth` | Detect auth patterns from HAR | Returns detected patterns |
| `GET /processes/{id}/detect-apis` | Detect distinct APIs from HAR | Returns API groupings |
| `POST /abcd/discover` | Search ABCD discovery index | Returns matching actions/APIs |
| `POST /abcd/validate` | Validate WDL content | Returns validation result |
| `POST /abcd/workspace` | Create ABCD workspace on disk | Returns workspace path |

### 9.2 MCP Tools That Could Invoke ABCD CLI Commands

The Elicitation Agent's MCP server currently has 14 tools. Adding ABCD integration tools would bring it to approximately 21-24 tools. These new tools would invoke ABCD CLI scripts as subprocesses:

```python
# Example: MCP tool that wraps ABCD CLI
@mcp.tool()
async def abcd_validate_wdl(wdl_json: str, action_id: str) -> str:
    """Validate a WDL definition using ABCD's validation engine.

    Args:
        wdl_json: The WDL JSON content to validate
        action_id: The action identifier for context
    """
    import subprocess
    import tempfile

    # Write WDL to temp file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write(wdl_json)
        wdl_path = f.name

    # Invoke ABCD CLI
    result = subprocess.run(
        ["python", "-m", "abcd.cli.validate_wdl", "--input", wdl_path],
        capture_output=True, text=True, timeout=30
    )

    return json.dumps({
        "valid": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
    })
```

### 9.3 Data Models That Need New Fields/Relationships

**Summary of model changes (detailed in Section 11):**

| Model | Change Type | Fields/Relations |
|---|---|---|
| `Project` | Add fields | `environment_type`, `abcd_workspace_path` |
| `Process` | Add fields | `abcd_action_id`, `tool_mode`, `wdl_status` |
| `CaptureSession` | Add fields | `environment_id` |
| `TimelineEvent` | Add event types | `api_call` (filtered network), `wdl_step` |
| `Document` | Add doc types | `api_spec`, `wdl_draft`, `adopt_profile`, `test_cases`, `requirements` |
| NEW: `Environment` | New model | `name`, `base_url`, `credentials`, `adopt_profile` |
| NEW: `ArtifactVersion` | New model | `artifact_type`, `version_number`, `status`, `content` |
| NEW: `DetectedAPI` | New model | `base_url`, `name`, `auth_type`, `endpoints[]` |
| NEW: `ABCDWorkspace` | New model | `environment_id`, `workspace_path`, `status` |

### 9.4 Extension Features That Could Feed ABCD Discovery

**Content script enhancements:**

1. **API documentation detector.** When the user visits a page that contains Swagger UI, Redoc, or OpenAPI spec links, the extension could auto-detect and extract the spec URL. This feeds directly into ABCD's `fetch_api_spec.py`.

2. **Form field analyzer.** Enhanced click tracking that captures not just the value entered but the field label, placeholder text, validation rules (from HTML attributes), and surrounding context. This provides richer `workflow_params` documentation.

3. **Network request classifier.** The extension could classify network requests in real-time as "API call" vs "asset load" vs "analytics" using heuristics (JSON content type, REST-like URL patterns, non-browser User-Agent). This pre-filters HAR data for ABCD consumption.

4. **Auth flow detector.** When the extension detects an OAuth redirect flow, login form submission, or token refresh, it could flag these as authentication events and extract the auth pattern for `security_params` generation.

---

## 10. Proposed Roadmap

### Phase 1: Export Alignment (Weeks 1-4)

**Goal:** Make the Elicitation Agent's output directly consumable by ABCD without manual transformation.

| Task | Priority | Effort | Dependencies |
|---|---|---|---|
| Filtered HAR export (API calls only) | P0 | 3 days | None |
| Annotated HAR export (step numbers, related events) | P0 | 5 days | Filtered HAR |
| `requirements.md` generation from narrations + messages | P0 | 3 days | None |
| `description.txt` generation from process description | P1 | 1 day | None |
| `adopt_profile.json` generation from HAR analysis | P0 | 5 days | Auth detection |
| Auth pattern detection from HAR headers | P0 | 3 days | None |
| Multi-API base URL detection | P1 | 3 days | None |
| OpenAPI spec generation from HAR (basic) | P1 | 5 days | Filtered HAR |
| Test case generation from capture data | P1 | 5 days | None |
| ABCD workspace bundle export (`/export/abcd`) | P0 | 5 days | All above |

**Deliverable:** `POST /processes/{id}/export/abcd` returns a zip file that can be unpacked directly into an ABCD workspace directory.

### Phase 2: Semantic Alignment (Weeks 5-8)

**Goal:** Align data models so concepts flow naturally between systems.

| Task | Priority | Effort | Dependencies |
|---|---|---|---|
| `Environment` model + CRUD endpoints | P0 | 3 days | None |
| `ArtifactVersion` model + versioning logic | P1 | 5 days | None |
| `DetectedAPI` model + auto-detection | P1 | 3 days | Phase 1 auth detection |
| `Document.doc_type` extensions (api_spec, wdl_draft, etc.) | P0 | 2 days | None |
| `Process.abcd_action_id` linking | P1 | 2 days | None |
| `Process.tool_mode` flag | P2 | 1 day | None |
| Draft/publish status tracking on Process | P1 | 3 days | ArtifactVersion |
| Environment-aware capture sessions | P1 | 2 days | Environment model |
| `profiles_map` generation for multi-API processes | P2 | 3 days | DetectedAPI |
| WDL status tracking (draft, validated, published) | P1 | 2 days | ArtifactVersion |

**Deliverable:** The Elicitation Agent's data model mirrors ABCD's workspace hierarchy, enabling bidirectional mapping.

### Phase 3: ABCD Integration (Weeks 9-14)

**Goal:** Enable Claude Code to drive ABCD directly from the Elicitation Agent's MCP server.

| Task | Priority | Effort | Dependencies |
|---|---|---|---|
| `abcd_create_workspace` MCP tool | P0 | 3 days | Phase 2 |
| `abcd_discover_apis` MCP tool | P0 | 3 days | ABCD CLI access |
| `abcd_generate_wdl` MCP tool | P0 | 5 days | Phase 1 exports |
| `abcd_validate` MCP tool | P0 | 3 days | ABCD CLI access |
| `abcd_test` MCP tool | P0 | 3 days | ABCD CLI access |
| `abcd_save_draft` MCP tool | P1 | 2 days | ABCD CLI access |
| `abcd_publish` MCP tool | P1 | 2 days | ABCD CLI access |
| ABCD CLI subprocess management | P0 | 3 days | ABCD installation |
| Error handling and retry logic | P1 | 3 days | All tools |
| Integration tests (MCP tools + ABCD CLI) | P0 | 5 days | All tools |

**Deliverable:** Claude Code can invoke all ABCD lifecycle operations via MCP tools exposed by the Elicitation Agent backend.

### Phase 4: Closed-Loop Automation (Weeks 15-20)

**Goal:** Claude orchestrates the complete capture-to-publish loop with browser verification.

| Task | Priority | Effort | Dependencies |
|---|---|---|---|
| Claude system prompt: ABCD-aware orchestration | P0 | 3 days | Phase 3 |
| Workflow replay via browser MCP tools | P0 | 5 days | Phase 3 |
| Visual verification (screenshot comparison) | P1 | 5 days | Screenshot tooling |
| Auto-iteration on test failures | P0 | 5 days | Phase 3 test tool |
| Multi-step WDL generation with dependency detection | P0 | 5 days | Phase 1 HAR analysis |
| Conditional logic generation from branching captures | P2 | 5 days | Multiple capture sessions |
| FOR_EACH / PAGINATE detection from repeated patterns | P2 | 3 days | HAR pattern analysis |
| End-to-end smoke test: capture to publish | P0 | 5 days | All above |
| Documentation and runbook | P1 | 3 days | All above |
| Performance optimization (parallel tool calls) | P2 | 3 days | All above |

**Deliverable:** A demonstrated end-to-end flow where a user demonstrates a workflow, and Claude autonomously creates, tests, and publishes an ABCD action.

### Roadmap Visualization

```
Week  1  2  3  4  5  6  7  8  9  10 11 12 13 14 15 16 17 18 19 20
      |-----Phase 1-----|  |-----Phase 2-----|  |---Phase 3----|  |---Phase 4----|
      Export Alignment      Semantic Alignment   ABCD Integration  Closed Loop

P1:   [=Filtered HAR==]
      [=====Annotated HAR=====]
      [==Auth Detect==]
      [=Requirements.md=]
      [=====Adopt Profile=====]
      [=====OpenAPI Gen=======]
      [====Test Case Gen======]
      [========ABCD Bundle Export========]

P2:                         [=Environment Model=]
                            [====ArtifactVersion====]
                            [=DetectedAPI=]
                            [=Doc Types=]
                            [=Process Links=]
                            [===Draft/Publish Tracking===]
                            [==profiles_map==]

P3:                                              [=Workspace Tool=]
                                                 [=Discover Tool=]
                                                 [====WDL Gen Tool====]
                                                 [=Validate Tool=]
                                                 [=Test Tool=]
                                                 [=Draft/Publish=]
                                                 [====Integration Tests====]

P4:                                                                [=System Prompt=]
                                                                   [====Replay====]
                                                                   [==Visual Verify==]
                                                                   [====Auto-Iterate====]
                                                                   [=Dependency Detect=]
                                                                   [====E2E Smoke Test====]
```

---

## 11. Data Model Changes

### 11.1 New Models

#### Environment

```python
class Environment(Base):
    """Deployment target context (staging, production, etc.)."""
    __tablename__ = "environments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))  # "staging", "production", "development"
    base_url: Mapped[str] = mapped_column(String(2000), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)

    # ABCD-aligned configuration
    credentials_json: Mapped[str] = mapped_column(Text, default="{}")
    # Encrypted JSON: {"ADOPT_CLIENT_ID": "...", "ADOPT_CLIENT_SECRET": "..."}

    adopt_profile_json: Mapped[str] = mapped_column(Text, default="{}")
    # JSON matching adopt_profile.json schema:
    # {"base_url": "...", "security_params": {...}, "workflow_params": {...}, "profiles_map": {...}}

    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)

    project: Mapped["Project"] = relationship(back_populates="environments")
    capture_sessions: Mapped[list["CaptureSession"]] = relationship(back_populates="environment")
```

#### ArtifactVersion

```python
class ArtifactVersion(Base):
    """Versioned artifact produced during the capture-to-publish lifecycle."""
    __tablename__ = "artifact_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"))
    artifact_type: Mapped[str] = mapped_column(String(50))
    # Artifact types: "wdl", "api_spec", "requirements", "adopt_profile",
    #                 "test_cases", "description", "filtered_har"

    version_number: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    # Status values: "draft", "validated", "tested", "published", "archived"

    content: Mapped[str] = mapped_column(Text, default="")
    # JSON or text content of the artifact

    content_hash: Mapped[str] = mapped_column(String(64), default="")
    # SHA-256 hash for change detection

    created_by: Mapped[str] = mapped_column(String(50), default="user")
    # "user", "claude", "auto-generate", "abcd-cli"

    source_capture_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("capture_sessions.id", ondelete="SET NULL"), nullable=True
    )
    # Links this artifact version to the capture session that produced it

    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    process: Mapped["Process"] = relationship(back_populates="artifact_versions")
```

#### DetectedAPI

```python
class DetectedAPI(Base):
    """An API detected from HAR traffic analysis."""
    __tablename__ = "detected_apis"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"))
    capture_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("capture_sessions.id", ondelete="SET NULL"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(255), default="")
    # Auto-generated or user-provided name: "CRM API", "Payment Gateway"

    base_url: Mapped[str] = mapped_column(String(2000))
    # Detected base URL: "https://api.crm.example.com/v1"

    auth_type: Mapped[str] = mapped_column(String(50), default="none")
    # Detected auth: "bearer", "api_key_header", "api_key_query", "basic", "cookie", "oauth2", "none"

    auth_details_json: Mapped[str] = mapped_column(Text, default="{}")
    # {"header_name": "Authorization", "prefix": "Bearer"}

    endpoints_json: Mapped[str] = mapped_column(Text, default="[]")
    # [{"method": "POST", "path": "/customers", "status_codes": [201, 400]}]

    request_count: Mapped[int] = mapped_column(Integer, default=0)
    # Number of HAR entries matching this API

    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    process: Mapped["Process"] = relationship(back_populates="detected_apis")
```

#### ABCDWorkspace

```python
class ABCDWorkspace(Base):
    """Tracks ABCD workspace creation and state."""
    __tablename__ = "abcd_workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"))
    environment_id: Mapped[str | None] = mapped_column(
        ForeignKey("environments.id", ondelete="SET NULL"), nullable=True
    )

    workspace_path: Mapped[str] = mapped_column(String(1000), default="")
    # Absolute path to the ABCD workspace directory on disk

    action_id: Mapped[str] = mapped_column(String(255), default="")
    # ABCD action identifier

    agent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # ABCD uber agent identifier (if part of an agent)

    status: Mapped[str] = mapped_column(String(30), default="created")
    # "created", "wdl_generated", "validated", "tested", "draft_saved", "published"

    current_version: Mapped[int] = mapped_column(Integer, default=0)
    published_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    tool_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    # Whether this action is deployable as a sub-tool

    last_test_result_json: Mapped[str] = mapped_column(Text, default="{}")
    # {"passed": 3, "failed": 0, "timestamp": "..."}

    platform_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # ID on the Adopt.AI platform after publication

    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)

    process: Mapped["Process"] = relationship(back_populates="abcd_workspace")
    environment: Mapped["Environment | None"] = relationship()
```

### 11.2 Modified Existing Models

#### Project (additions)

```python
# Add to Project model:
environment_type: Mapped[str] = mapped_column(String(50), default="development")
# "development", "staging", "production" -- default environment type

abcd_workspace_root: Mapped[str] = mapped_column(String(1000), default="")
# Root path for ABCD workspaces associated with this project

# Add relationship:
environments: Mapped[list["Environment"]] = relationship(
    back_populates="project", cascade="all, delete-orphan"
)
```

#### Process (additions)

```python
# Add to Process model:
abcd_action_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
# Links this process to its ABCD action identifier

tool_mode: Mapped[bool] = mapped_column(Boolean, default=False)
# Whether this process should be published as a tool for uber agents

wdl_status: Mapped[str] = mapped_column(String(30), default="none")
# "none", "draft", "validated", "tested", "published"

# Add relationships:
artifact_versions: Mapped[list["ArtifactVersion"]] = relationship(
    back_populates="process", cascade="all, delete-orphan"
)
detected_apis: Mapped[list["DetectedAPI"]] = relationship(
    back_populates="process", cascade="all, delete-orphan"
)
abcd_workspace: Mapped["ABCDWorkspace | None"] = relationship(
    back_populates="process", uselist=False, cascade="all, delete-orphan"
)
```

#### CaptureSession (additions)

```python
# Add to CaptureSession model:
environment_id: Mapped[str | None] = mapped_column(
    ForeignKey("environments.id", ondelete="SET NULL"), nullable=True
)
# Which environment was this session captured against?

# Add relationship:
environment: Mapped["Environment | None"] = relationship(back_populates="capture_sessions")
```

#### Document (doc_type extensions)

```python
# Extend doc_type values from current {"canonical", "user"} to include:
# "user"           - User-created document
# "canonical"      - Auto-generated canonical project/process summary
# "api_spec"       - OpenAPI specification (JSON)
# "wdl_draft"      - WDL definition (JSON)
# "adopt_profile"  - adopt_profile.json content
# "test_cases"     - ABCD test cases (JSON array)
# "requirements"   - requirements.md content
# "description"    - description.txt content
# "filtered_har"   - Filtered and annotated HAR (JSON)
```

### 11.3 Schema Changes Summary

```sql
-- New tables
CREATE TABLE environments (
    id VARCHAR(36) PRIMARY KEY,
    project_id VARCHAR(36) REFERENCES projects(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    base_url VARCHAR(2000) DEFAULT '',
    is_active BOOLEAN DEFAULT FALSE,
    credentials_json TEXT DEFAULT '{}',
    adopt_profile_json TEXT DEFAULT '{}',
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);

CREATE TABLE artifact_versions (
    id VARCHAR(36) PRIMARY KEY,
    process_id VARCHAR(36) REFERENCES processes(id) ON DELETE CASCADE,
    artifact_type VARCHAR(50) NOT NULL,
    version_number INTEGER DEFAULT 1,
    status VARCHAR(20) DEFAULT 'draft',
    content TEXT DEFAULT '',
    content_hash VARCHAR(64) DEFAULT '',
    created_by VARCHAR(50) DEFAULT 'user',
    source_capture_session_id VARCHAR(36) REFERENCES capture_sessions(id) ON DELETE SET NULL,
    created_at TIMESTAMP
);

CREATE TABLE detected_apis (
    id VARCHAR(36) PRIMARY KEY,
    process_id VARCHAR(36) REFERENCES processes(id) ON DELETE CASCADE,
    capture_session_id VARCHAR(36) REFERENCES capture_sessions(id) ON DELETE SET NULL,
    name VARCHAR(255) DEFAULT '',
    base_url VARCHAR(2000) NOT NULL,
    auth_type VARCHAR(50) DEFAULT 'none',
    auth_details_json TEXT DEFAULT '{}',
    endpoints_json TEXT DEFAULT '[]',
    request_count INTEGER DEFAULT 0,
    created_at TIMESTAMP
);

CREATE TABLE abcd_workspaces (
    id VARCHAR(36) PRIMARY KEY,
    process_id VARCHAR(36) REFERENCES processes(id) ON DELETE CASCADE,
    environment_id VARCHAR(36) REFERENCES environments(id) ON DELETE SET NULL,
    workspace_path VARCHAR(1000) DEFAULT '',
    action_id VARCHAR(255) DEFAULT '',
    agent_id VARCHAR(255),
    status VARCHAR(30) DEFAULT 'created',
    current_version INTEGER DEFAULT 0,
    published_version INTEGER,
    tool_mode BOOLEAN DEFAULT FALSE,
    last_test_result_json TEXT DEFAULT '{}',
    platform_id VARCHAR(255),
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);

-- Alterations to existing tables
ALTER TABLE projects ADD COLUMN environment_type VARCHAR(50) DEFAULT 'development';
ALTER TABLE projects ADD COLUMN abcd_workspace_root VARCHAR(1000) DEFAULT '';

ALTER TABLE processes ADD COLUMN abcd_action_id VARCHAR(255);
ALTER TABLE processes ADD COLUMN tool_mode BOOLEAN DEFAULT FALSE;
ALTER TABLE processes ADD COLUMN wdl_status VARCHAR(30) DEFAULT 'none';

ALTER TABLE capture_sessions ADD COLUMN environment_id VARCHAR(36) REFERENCES environments(id) ON DELETE SET NULL;
```

### 11.4 Migration Strategy

Since the backend uses SQLite with `CREATE TABLE IF NOT EXISTS` and inline `ALTER TABLE` migrations (see `main.py` lifespan), the new tables and columns should be added using the same pattern:

```python
# In main.py lifespan, after existing migrations:
for table_sql in [
    """CREATE TABLE IF NOT EXISTS environments (
        id VARCHAR(36) PRIMARY KEY, ...)""",
    """CREATE TABLE IF NOT EXISTS artifact_versions (
        id VARCHAR(36) PRIMARY KEY, ...)""",
    """CREATE TABLE IF NOT EXISTS detected_apis (
        id VARCHAR(36) PRIMARY KEY, ...)""",
    """CREATE TABLE IF NOT EXISTS abcd_workspaces (
        id VARCHAR(36) PRIMARY KEY, ...)""",
]:
    try:
        await conn.execute(text(table_sql))
    except Exception:
        pass

for col_def in [
    "ALTER TABLE projects ADD COLUMN environment_type VARCHAR(50) DEFAULT 'development'",
    "ALTER TABLE projects ADD COLUMN abcd_workspace_root VARCHAR(1000) DEFAULT ''",
    "ALTER TABLE processes ADD COLUMN abcd_action_id VARCHAR(255)",
    "ALTER TABLE processes ADD COLUMN tool_mode BOOLEAN DEFAULT 0",
    "ALTER TABLE processes ADD COLUMN wdl_status VARCHAR(30) DEFAULT 'none'",
    "ALTER TABLE capture_sessions ADD COLUMN environment_id VARCHAR(36)",
]:
    try:
        await conn.execute(text(col_def))
    except Exception:
        pass  # Column already exists
```

---

## 12. New MCP Tools for ABCD Integration

### 12.1 Tool Overview

| Tool Name | Category | ABCD CLI Script(s) | Purpose |
|---|---|---|---|
| `abcd_create_workspace` | Workspace | `create_action.py`, `setup_environment.py` | Initialize ABCD workspace structure |
| `abcd_discover_apis` | Discovery | `search_existing_apis.py`, `search_existing_actions.py` | Search for existing APIs/actions |
| `abcd_fetch_api_spec` | Discovery | `fetch_api_spec.py` | Retrieve and cache API specifications |
| `abcd_generate_wdl` | Generation | `generate_wdl.py`, custom analysis | Generate WDL from captured data |
| `abcd_validate` | Validation | `validate_wdl.py` | Validate WDL JSON structure |
| `abcd_test` | Testing | `run_tests.py`, `test_action.py` | Execute test cases against platform |
| `abcd_save_draft` | Persistence | `save_draft.py` | Save current state as draft version |
| `abcd_create_version` | Persistence | `create_version.py` | Create numbered version snapshot |
| `abcd_publish` | Publication | `publish_action.py` | Publish version to live platform |
| `abcd_run_diagnostics` | Diagnostics | `run_diagnostics.py` | Analyze HTTP logs for issues |

### 12.2 Detailed Tool Specifications

#### abcd_create_workspace

```python
@mcp.tool()
async def abcd_create_workspace(
    process_id: str,
    environment_name: str = "default",
    action_name: str | None = None,
    agent_id: str | None = None,
) -> str:
    """Create an ABCD workspace structure for a process.

    Generates the full workspace directory hierarchy including:
    - Environment directory with .env and adopt_profile.json
    - Action directory with widdle.json, metadata.json
    - APIs, tools, test_cases, traces, versions subdirectories
    - requirements.md and description.txt from captured data

    Args:
        process_id: UUID of the Elicitation Agent process
        environment_name: Environment name (default, staging, production)
        action_name: Optional action name override (defaults to process name)
        agent_id: Optional uber agent ID if this action belongs to an agent
    """
    # Implementation:
    # 1. Fetch process data from DB
    # 2. Generate adopt_profile.json from captured HAR/environment
    # 3. Generate requirements.md from narrations/messages
    # 4. Generate description.txt from process description
    # 5. Create directory structure
    # 6. Return workspace path and structure summary
```

**Expected response:**

```json
{
  "workspace_path": "/path/to/workspaces/default/actions/create-customer-order",
  "environment": "default",
  "action_id": "create-customer-order",
  "structure": {
    "adopt_profile.json": "generated",
    "widdle.json": "empty (pending generation)",
    "metadata.json": "generated",
    "requirements.md": "generated from 5 narrations + 12 messages",
    "description.txt": "generated",
    "apis/manifest.json": "generated with 2 detected APIs",
    "test_cases/": "empty (pending generation)",
    "versions/": "empty",
    "traces/": "empty"
  }
}
```

#### abcd_discover_apis

```python
@mcp.tool()
async def abcd_discover_apis(
    query: str,
    search_mode: str = "hybrid",
    limit: int = 10,
) -> str:
    """Search ABCD's discovery index for existing APIs and actions.

    Uses dual-mode search: FAISS semantic similarity + fuzzy string matching.
    Returns ranked results with similarity scores.

    Args:
        query: Natural language search query (e.g., "create customer CRM")
        search_mode: Search mode - "semantic", "fuzzy", or "hybrid" (default)
        limit: Maximum number of results to return (default 10)
    """
    # Implementation:
    # 1. Call ABCD CLI: search_existing_apis.py --query "..." --mode hybrid
    # 2. Call ABCD CLI: search_existing_actions.py --query "..." --mode hybrid
    # 3. Merge and rank results
    # 4. Return formatted results
```

**Expected response:**

```json
{
  "results": [
    {
      "type": "action",
      "id": "crm-create-customer-v3",
      "name": "CRM Create Customer",
      "description": "Creates a new customer record in the CRM system",
      "similarity_score": 0.89,
      "version": 3,
      "status": "published",
      "base_url": "https://api.crm.example.com/v1"
    },
    {
      "type": "api",
      "id": "crm-api",
      "name": "CRM REST API v1",
      "description": "Customer relationship management API",
      "similarity_score": 0.82,
      "spec_url": "https://api.crm.example.com/v1/openapi.json"
    }
  ],
  "total_results": 2,
  "search_mode": "hybrid"
}
```

#### abcd_fetch_api_spec

```python
@mcp.tool()
async def abcd_fetch_api_spec(
    spec_url: str,
    api_name: str | None = None,
    process_id: str | None = None,
) -> str:
    """Fetch an API specification from a URL and cache it locally.

    Downloads an OpenAPI/Swagger specification and stores it in the
    ABCD workspace's apis/ directory. If process_id is provided, also
    stores as a Document in the Elicitation Agent database.

    Args:
        spec_url: URL of the API specification (OpenAPI JSON/YAML)
        api_name: Optional name for the API (auto-detected from spec if omitted)
        process_id: Optional process ID to associate the spec with
    """
    # Implementation:
    # 1. Call ABCD CLI: fetch_api_spec.py --url "..." --output apis/
    # 2. Parse and validate the spec
    # 3. If process_id, store as Document with doc_type="api_spec"
    # 4. Return spec summary (paths, methods, schemas count)
```

#### abcd_generate_wdl

```python
@mcp.tool()
async def abcd_generate_wdl(
    process_id: str,
    strategy: str = "from_har",
    include_error_handling: bool = True,
    parameterize: bool = True,
) -> str:
    """Generate a WDL (Workflow Definition Language) definition from captured data.

    Analyzes HAR data, timeline events, click interactions, and narrations to
    produce a multi-step WDL definition. Supports different generation strategies.

    Args:
        process_id: UUID of the process with captured data
        strategy: Generation strategy - "from_har" (HTTP-centric), "from_timeline"
                  (event-centric), or "hybrid" (combines both). Default "from_har".
        include_error_handling: Add CONDITION steps for error responses (default True)
        parameterize: Replace concrete values with {{variables}} (default True)
    """
    # Implementation:
    # 1. Fetch HAR data, timeline events, click events, narrations from DB
    # 2. Filter HAR to API calls only
    # 3. Detect dependencies between requests (response value -> next request)
    # 4. Map timeline click events to HAR entries by timestamp
    # 5. Generate WDL steps:
    #    - REST steps from HAR entries
    #    - JQ_FILTER steps for data extraction
    #    - CONDITION steps for error handling
    #    - OUTPUT_TEXT for final result formatting
    # 6. Parameterize concrete values using click event input values
    # 7. Add step descriptions from narrations
    # 8. Store as ArtifactVersion with type="wdl", status="draft"
    # 9. Return WDL JSON content
```

**Expected response:**

```json
{
  "wdl": {
    "steps": [
      {
        "operation": "REST",
        "config": {
          "method": "POST",
          "url": "{{base_url}}/api/customers",
          "headers": {"Authorization": "Bearer {{access_token}}"},
          "body": {"name": "{{customer_name}}", "email": "{{customer_email}}"}
        },
        "description": "Create a new customer record"
      },
      {
        "operation": "JQ_FILTER",
        "config": {"expression": ".id"},
        "output": "customer_id",
        "description": "Extract customer ID from creation response"
      },
      {
        "operation": "REST",
        "config": {
          "method": "GET",
          "url": "{{base_url}}/api/orders?customer_id={{customer_id}}"
        },
        "description": "Fetch orders for the newly created customer"
      },
      {
        "operation": "OUTPUT_TEXT",
        "config": {
          "template": "Created customer {{customer_name}} (ID: {{customer_id}}) with {{orders | length}} orders"
        }
      }
    ]
  },
  "artifact_version_id": "ver-789",
  "version_number": 1,
  "status": "draft",
  "detected_params": ["customer_name", "customer_email", "access_token"],
  "detected_dependencies": [
    {"from_step": 0, "to_step": 2, "via": "customer_id"}
  ],
  "source_har_entries": 3,
  "source_click_events": 8,
  "source_narrations": 3
}
```

#### abcd_validate

```python
@mcp.tool()
async def abcd_validate(
    process_id: str | None = None,
    wdl_content: str | None = None,
    action_id: str | None = None,
) -> str:
    """Validate a WDL definition for correctness.

    Performs JSON syntax validation, WDL structure validation, operation
    type checking, and reference integrity verification. Provide either
    process_id (to validate the latest draft) or wdl_content (raw JSON).

    Args:
        process_id: UUID of the process (validates latest WDL draft)
        wdl_content: Raw WDL JSON string to validate (alternative to process_id)
        action_id: ABCD action ID for workspace-context validation
    """
    # Implementation:
    # 1. Get WDL content from process draft or wdl_content parameter
    # 2. JSON syntax validation
    # 3. WDL schema validation (valid operations, required fields)
    # 4. Reference integrity (variables referenced before definition)
    # 5. API endpoint validation against stored specs
    # 6. Return validation result with errors and warnings
```

**Expected response:**

```json
{
  "valid": false,
  "errors": [
    {
      "step": 2,
      "operation": "REST",
      "error": "Variable {{customer_id}} referenced but not defined in prior steps",
      "suggestion": "Add a JQ_FILTER step after step 0 to extract customer_id"
    }
  ],
  "warnings": [
    {
      "step": 0,
      "operation": "REST",
      "warning": "No error handling for non-2xx responses",
      "suggestion": "Add a CONDITION step to handle 400/500 responses"
    }
  ],
  "stats": {
    "total_steps": 4,
    "operations_used": ["REST", "JQ_FILTER", "OUTPUT_TEXT"],
    "variables_defined": ["customer_name", "customer_email"],
    "variables_used": ["customer_name", "customer_email", "customer_id"],
    "undefined_variables": ["customer_id"]
  }
}
```

#### abcd_test

```python
@mcp.tool()
async def abcd_test(
    process_id: str,
    test_mode: str = "local",
    specific_test: str | None = None,
) -> str:
    """Run test cases for an ABCD action.

    Executes test cases defined in the workspace's test_cases/ directory.
    Supports local validation (schema + logic) and remote execution
    (actually runs against the platform API).

    Args:
        process_id: UUID of the process with ABCD workspace
        test_mode: "local" (validation only) or "remote" (platform execution)
        specific_test: Optional specific test case name to run
    """
    # Implementation:
    # 1. Find ABCD workspace for this process
    # 2. Load test cases from test_cases/ directory
    # 3. If local: validate inputs, simulate WDL execution, check outputs
    # 4. If remote: call ABCD CLI run_tests.py against platform
    # 5. Return results with pass/fail per test case
```

**Expected response:**

```json
{
  "test_mode": "local",
  "total": 3,
  "passed": 2,
  "failed": 1,
  "results": [
    {
      "test_case": "happy_path_create_customer",
      "status": "passed",
      "duration_ms": 45,
      "validation": {"type": "contains", "expected": "Customer created", "actual": "Customer created successfully"}
    },
    {
      "test_case": "happy_path_fetch_orders",
      "status": "passed",
      "duration_ms": 38
    },
    {
      "test_case": "error_duplicate_customer",
      "status": "failed",
      "duration_ms": 52,
      "error": "Expected status 409 but WDL has no CONDITION step for error responses",
      "suggestion": "Add error handling in WDL for HTTP 409 Conflict"
    }
  ]
}
```

#### abcd_save_draft

```python
@mcp.tool()
async def abcd_save_draft(
    process_id: str,
    notes: str = "",
) -> str:
    """Save the current ABCD workspace state as an unpublished draft.

    Persists the current WDL, test cases, and configuration as a draft
    that can be reviewed and tested without affecting published versions.

    Args:
        process_id: UUID of the process with ABCD workspace
        notes: Optional notes about this draft version
    """
    # Implementation:
    # 1. Find ABCD workspace for this process
    # 2. Call ABCD CLI: save_draft.py --workspace "..."
    # 3. Update ABCDWorkspace status to "draft_saved"
    # 4. Create ArtifactVersion snapshot
    # 5. Return draft details
```

#### abcd_create_version

```python
@mcp.tool()
async def abcd_create_version(
    process_id: str,
    version_notes: str = "",
) -> str:
    """Create a numbered version from the current draft.

    Snapshots the current workspace state as an immutable version
    (v1, v2, ...) stored in the versions/ directory. Versions are
    prerequisites for publication.

    Args:
        process_id: UUID of the process with ABCD workspace
        version_notes: Description of what changed in this version
    """
    # Implementation:
    # 1. Find ABCD workspace
    # 2. Determine next version number
    # 3. Call ABCD CLI: create_version.py --workspace "..." --version N
    # 4. Copy workspace files to versions/vN/
    # 5. Update ABCDWorkspace.current_version
    # 6. Return version details
```

#### abcd_publish

```python
@mcp.tool()
async def abcd_publish(
    process_id: str,
    version: int | None = None,
) -> str:
    """Publish a version of the action to the live platform.

    Makes a specific version (or the latest) live on the Adopt.AI platform.
    Requires all test cases to pass and the version to be validated.

    Args:
        process_id: UUID of the process with ABCD workspace
        version: Specific version number to publish (default: latest)
    """
    # Implementation:
    # 1. Find ABCD workspace
    # 2. Verify version exists and tests pass
    # 3. Call ABCD CLI: publish_action.py --workspace "..." --version N
    # 4. Update ABCDWorkspace.published_version and .status
    # 5. Store platform_id from publication response
    # 6. Return publication confirmation
```

#### abcd_run_diagnostics

```python
@mcp.tool()
async def abcd_run_diagnostics(
    process_id: str,
    har_source: str = "latest",
) -> str:
    """Run ABCD diagnostics on captured HTTP logs.

    Analyzes HAR data to detect common API integration issues:
    trailing slash mismatches, missing required parameters, HTTP method
    mismatches, unexpected response codes, and more.

    Args:
        process_id: UUID of the process with HAR data
        har_source: "latest" (most recent capture session) or a capture session ID
    """
    # Implementation:
    # 1. Load HAR data from capture session
    # 2. Call ABCD CLI: run_diagnostics.py --input har_file
    # 3. Parse diagnostic results
    # 4. Store as timeline events for visibility
    # 5. Return diagnostic report
```

**Expected response:**

```json
{
  "diagnostics": {
    "total_issues": 3,
    "critical": 1,
    "warnings": 2,
    "issues": [
      {
        "severity": "critical",
        "type": "trailing_slash_mismatch",
        "details": "Request to /api/customers returned 301 redirect to /api/customers/",
        "affected_har_entry": 2,
        "fix": "Use /api/customers/ (with trailing slash) in WDL"
      },
      {
        "severity": "warning",
        "type": "missing_content_type",
        "details": "POST request missing Content-Type header",
        "affected_har_entry": 0,
        "fix": "Add Content-Type: application/json header to REST step"
      },
      {
        "severity": "warning",
        "type": "deprecated_endpoint",
        "details": "Response includes Deprecation header for /api/v1/orders",
        "affected_har_entry": 4,
        "fix": "Consider migrating to /api/v2/orders"
      }
    ]
  },
  "har_entries_analyzed": 12,
  "capture_session_id": "sess-abc"
}
```

### 12.3 MCP Tool Registration Pattern

All new tools should follow the existing pattern in `backend/app/mcp_server.py`:

```python
# In mcp_server.py, after existing tool definitions:

# ── ABCD Integration tools ──────────────────────────────────────────

@mcp.tool()
async def abcd_create_workspace(...) -> str:
    """..."""
    # Implementation

@mcp.tool()
async def abcd_discover_apis(...) -> str:
    """..."""
    # Implementation

# ... etc.
```

The MCP server is mounted at `/mcp` on the FastAPI application (`app.mount("/mcp", mcp.sse_app())`), so all new tools are automatically available to Claude Code via the same SSE transport.

### 12.4 ABCD CLI Invocation Layer

Since the MCP tools need to invoke ABCD CLI scripts, a shared utility layer should manage subprocess execution:

```python
# backend/app/abcd_bridge.py

import asyncio
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

ABCD_CLI_TIMEOUT = 60  # seconds

async def run_abcd_cli(
    script: str,
    args: list[str],
    workspace_path: str | None = None,
    env_overrides: dict | None = None,
) -> dict:
    """Run an ABCD CLI script as a subprocess.

    Args:
        script: Script name (e.g., "validate_wdl", "run_tests")
        args: Command-line arguments
        workspace_path: Optional workspace directory for --workspace arg
        env_overrides: Optional environment variable overrides

    Returns:
        {"returncode": int, "stdout": str, "stderr": str, "parsed": dict|None}
    """
    cmd = ["python", "-m", f"abcd.cli.{script}"] + args
    if workspace_path:
        cmd.extend(["--workspace", workspace_path])

    env = dict(os.environ)
    if env_overrides:
        env.update(env_overrides)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=ABCD_CLI_TIMEOUT
        )

        result = {
            "returncode": proc.returncode,
            "stdout": stdout.decode(),
            "stderr": stderr.decode(),
            "parsed": None,
        }

        # Try to parse stdout as JSON
        try:
            result["parsed"] = json.loads(stdout.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        return result

    except asyncio.TimeoutError:
        logger.error("ABCD CLI script '%s' timed out after %ds", script, ABCD_CLI_TIMEOUT)
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"Timeout after {ABCD_CLI_TIMEOUT}s",
            "parsed": None,
        }
    except FileNotFoundError:
        logger.error("ABCD CLI script '%s' not found", script)
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"Script 'abcd.cli.{script}' not found. Is ABCD installed?",
            "parsed": None,
        }
```

---

## Appendix A: Glossary

| Term | System | Definition |
|---|---|---|
| **Action** | ABCD | The smallest functional unit of automation. Can be a single API call or a multi-step WDL workflow. |
| **Adopt Profile** | ABCD | Configuration file (`adopt_profile.json`) containing base_url, security_params, workflow_params, and profiles_map. |
| **Agent (Uber Agent)** | ABCD | An orchestrator that combines multiple sub-actions using PROMPT_AND_TOOLS_AGENT operation. |
| **Capture Session** | Elicitation | A recording session within a process, tracking clicks, HAR, voice, and other events with a start/stop lifecycle. |
| **Discovery** | ABCD | Dual-mode search (FAISS semantic + fuzzy matching) for finding existing APIs and actions. |
| **Draft** | ABCD | An unpublished version of an action that can be tested without affecting the live platform. |
| **Environment** | ABCD | A top-level workspace container with isolated credentials and configuration (e.g., staging vs. production). |
| **HAR** | Both | HTTP Archive format (v1.2). Standard JSON format for recording HTTP request/response data. |
| **MCP** | Both | Model Context Protocol. Allows Claude Code to invoke tools exposed by the Elicitation Agent backend. |
| **Process** | Elicitation | A discrete workflow being captured within a project. Contains capture sessions, messages, documents, etc. |
| **profiles_map** | ABCD | Configuration for multi-API actions, mapping logical API names to their base_url and security_params. |
| **Project** | Elicitation | Top-level organizational container. Contains processes, messages, screenshots, documents, etc. |
| **Test Case** | ABCD | JSON definition with prompt, workflow_params, expected_output, and validation type for testing an action. |
| **Timeline Event** | Elicitation | A single event in the chronological timeline (click, network request, narration, URL change, etc.). |
| **Tool Mode** | ABCD | Deployment flag that makes an action available as a sub-tool for uber agents. |
| **Version** | ABCD | A numbered, immutable snapshot of an action (v1, v2, ...) stored in the versions/ directory. |
| **WDL** | ABCD | Workflow Definition Language. JSON-based multi-step definition with operations like REST, JQ_FILTER, CONDITION, etc. |

---

## Appendix B: ABCD WDL Operations Reference

| Operation | Description | Key Config Fields | Elicitation Agent Source |
|---|---|---|---|
| `REST` | Make an HTTP API call | method, url, headers, body | HAR entries (direct mapping) |
| `JQ_FILTER` | Transform JSON data with jq expressions | expression, output_var | Inferred from response-to-request data flow |
| `EXTRACT` | Extract specific fields from data | fields, source | Inferred from which response fields are used later |
| `PROJECT` | Reshape/project data structure | mapping | Inferred from response transformation patterns |
| `PROMPT` | Use LLM to process data | prompt_template, model | Narrations describing complex logic |
| `CONDITION` | Conditional branching | condition, if_true, if_false | Click events showing different paths |
| `FOR_EACH` | Iterate over array | source_array, step | Repeated HAR patterns with varying parameters |
| `PAGINATE` | Handle paginated API responses | next_page_expression, max_pages | HAR entries with page/offset/cursor params |
| `OUTPUT_TEXT` | Format final output text | template | Final narration describing expected result |
| `PROMPT_AND_TOOLS_AGENT` | Uber agent with sub-tools | prompt, tools | Multiple processes in same project |

---

## Appendix C: File Reference

**Elicitation Agent files referenced in this document:**

| File | Purpose |
|---|---|
| `backend/app/models.py` | SQLAlchemy ORM models (Project, Process, CaptureSession, etc.) |
| `backend/app/schemas.py` | Pydantic request/response schemas |
| `backend/app/mcp_server.py` | MCP tool definitions (14 tools) |
| `backend/app/main.py` | FastAPI application entry point, router registration, MCP mount |
| `backend/app/config.py` | Application settings and environment configuration |
| `backend/app/claude_client.py` | Claude API wrapper with conversation context and tool use |
| `backend/app/browser_bridge.py` | Browser command queue for Chrome extension communication |
| `backend/app/markdown_generator.py` | Canonical markdown document generation |
| `backend/app/routers/export.py` | Project export/import endpoints |
| `backend/app/routers/chat.py` | Chat router with SSE streaming and Claude tool execution |
| `backend/app/routers/timeline.py` | Timeline event CRUD and download |
| `backend/app/routers/capture_sessions.py` | Capture session lifecycle management |
| `extension/manifest.json` | Chrome extension manifest (MV3) |
