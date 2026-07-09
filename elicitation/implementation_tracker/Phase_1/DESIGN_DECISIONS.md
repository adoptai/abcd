# Phase 1: Export Alignment — Design Decisions

## Decision Log

### DD-001: Pure Utility Modules (No New Models Yet)

**Decision:** Phase 1 modules are stateless utility functions that operate on data fetched from existing models. No new SQLAlchemy models (Environment, ArtifactVersion, DetectedAPI, ABCDWorkspace) are added in Phase 1 — those belong to Phase 2 (Semantic Alignment).

**Rationale:** Keeps Phase 1 focused on the translation layer. The generators take capture data in, produce ABCD-ready output. They don't need persistence yet — the export endpoint assembles everything on-the-fly from existing DB data.

**Consequence:** Phase 1 export is ephemeral (generate-on-demand). Phase 2 adds persistence and versioning.

---

### DD-002: HAR Filtering Strategy

**Decision:** Filter HAR entries using a multi-signal classifier:
1. Response `Content-Type` contains `application/json` or `application/xml` → likely API call
2. Request URL matches REST patterns (e.g., `/api/`, `/v1/`, `/v2/`)
3. Exclude known asset patterns (`.js`, `.css`, `.png`, `.woff`, `.svg`, etc.)
4. Exclude known analytics/tracking domains (configurable list)
5. Exclude `OPTIONS` preflight requests

**Rationale:** No single signal is reliable. `Content-Type` misses APIs that return `text/plain`. URL patterns miss non-standard APIs. The combination covers most real-world cases.

**Configuration:** The filter should accept an optional `include_patterns` / `exclude_patterns` override for edge cases.

---

### DD-003: Auth Detection Approach

**Decision:** Detect auth from HAR request headers using pattern matching, not from response challenges (401s).

**Signals:**
- `Authorization: Bearer <token>` → bearer token
- `Authorization: Basic <base64>` → basic auth
- `X-API-Key` or similar headers → API key (header)
- Query params named `api_key`, `apikey`, `key`, `access_token` → API key (query)
- `Cookie` header with session-like values → cookie auth
- Redirect chains to OAuth providers (detected via 302 + `oauth` / `authorize` in Location) → OAuth2

**Output:** A structured `SecurityParams` dict compatible with ABCD's `adopt_profile.json` `security_params` field.

---

### DD-004: WDL Generation Strategy — HAR-First with Timeline Enrichment

**Decision:** Use a "HAR-first" strategy for WDL generation:
1. Start with filtered HAR entries as the backbone (each API call → one REST step)
2. Enrich with timeline events for step ordering, descriptions, and context
3. Use click events to extract form input values → parameterized workflow_params
4. Use narrations to generate step descriptions
5. Detect dependencies by finding response values that appear in subsequent request URLs/bodies

**Rationale:** HAR data is the most concrete and reliable signal. Timeline events provide ordering and context but can't produce REST steps on their own. Click events and narrations add human meaning.

**Alternative considered:** Timeline-first (sequence events, correlate with HAR). Rejected because timeline events are noisy (include asset loads, duplicate clicks, etc.) and HAR entries already carry the precise HTTP data needed.

---

### DD-005: Parameterization Heuristic

**Decision:** Identify parameters vs constants using these rules:
1. Form input values (from ClickEvent where `event_type='input'`) → always parameterize
2. URL path segments that look like IDs (UUIDs, numeric, hex) → parameterize
3. Values that appear in HAR request body AND were seen in a form input → parameterize with the field_name
4. Auth tokens/keys → parameterize as `{{access_token}}` or `{{api_key}}`
5. API version paths (`/v1/`, `/v2/`) → keep as constants
6. Domain/base URL → replace with `{{base_url}}`

**Consequence:** Generated WDL will have `{{variable_name}}` placeholders. These map to `workflow_params` in the adopt profile and to test case `workflow_params`.

---

### DD-006: Dependency Detection Between HAR Entries

**Decision:** Detect data dependencies by searching for response values in subsequent requests:
1. Parse each HAR response body as JSON
2. For each scalar value (strings, numbers) in the response, check if that exact value appears in any subsequent request URL, headers, or body
3. When found, create a JQ_FILTER step to extract the value and a variable reference in the dependent step

**Limitation:** This is a best-effort heuristic. It will miss indirect dependencies (e.g., a value is transformed before reuse) and may produce false positives (common values like "1", "true", "ok").

**Mitigation:** Only match values that are:
- Longer than 3 characters (skip trivial matches)
- Not common HTTP values ("application/json", "true", "false", "null", "ok")
- Present in a structurally meaningful position (URL path segment, JSON field value, not headers)

---

### DD-007: Export Format — Zip Bundle

**Decision:** The ABCD workspace export is a zip file containing the ABCD workspace directory structure:

```
workspace.zip
├── adopt_profile.json
├── requirements.md
├── description.txt
├── metadata.json
├── widdle.json          (WDL draft)
├── apis/
│   └── manifest.json
├── test_cases/
│   └── test_cases.json
├── traces/              (empty)
└── versions/            (empty)
```

**Rationale:** This matches ABCD's expected workspace structure. The zip can be extracted directly into an ABCD environment's `actions/<action_name>/` directory.

---

### DD-008: Requirements.md Structure

**Decision:** Generate requirements.md with this structure:

```markdown
# Requirements: <Process Name>

## Overview
<Process description or first narration>

## Workflow Steps
<Numbered list derived from timeline event sequence>

## Narrations
<Timestamped narration transcripts>

## Captured Parameters
<Table of form inputs with field_name, example_value, source>

## Open Questions
<Unanswered questions from the Questions model>

## Notes from Conversation
<Relevant human messages that express intent or constraints>
```

**Rationale:** Maps to what ABCD expects in `requirements.md` — a natural language description of what the action should do, with enough detail for WDL generation and test case authoring.

---

### DD-009: Module Dependency Order

**Decision:** Build modules in this order (each builds on the previous):

```
1. har_analyzer.py       (no dependencies — pure HAR parsing)
2. auth_detector.py      (uses har_analyzer output)
3. profile_generator.py  (uses auth_detector + har_analyzer)
4. wdl_generator.py      (uses har_analyzer + click events + narrations)
5. requirements_generator.py (uses narrations + messages + questions)
6. test_case_generator.py (uses click events + narrations + wdl_generator output)
7. routers/abcd_export.py (orchestrates all above into zip bundle)
8. mcp_server.py additions (wraps key functions as MCP tools)
```

**Rationale:** This allows incremental development and testing. Each module can be unit-tested independently before integration.
