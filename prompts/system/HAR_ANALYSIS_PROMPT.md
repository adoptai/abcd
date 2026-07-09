# HAR Analysis & Action Generalization

This prompt guides the agent through analyzing HAR files (or Chrome extension recordings) to:
1. Understand the API patterns used by a target application
2. Extract security parameters for `adopt_profile.json`
3. Generalize recorded requests into dynamic WDL actions
4. Set up the token manager and playground profile for CE testing

**LOAD THIS WHEN**:
- You have a HAR file and need to build actions from it
- You imported an elicitation bundle and need to generalize hardcoded payloads
- You need to set up `adopt_profile.json` from scratch for a new client
- You need to configure token manager + playground profile before CE testing

---

## 📂 HAR File Structure Overview

A HAR (HTTP Archive) file is a JSON document. The relevant section is `log.entries`, an array of HTTP request/response pairs:

```json
{
  "log": {
    "entries": [
      {
        "startedDateTime": "2024-01-15T10:23:45.123Z",
        "request": {
          "method": "POST",
          "url": "https://app.example.com/api/v2/orders",
          "headers": [...],
          "queryString": [...],
          "postData": { "mimeType": "application/json", "text": "{...}" }
        },
        "response": {
          "status": 200,
          "headers": [...],
          "content": { "mimeType": "application/json", "text": "{...}" }
        }
      }
    ]
  }
}
```

---

## 🔍 Step 1: Initial HAR Scan

Before deep analysis, get the lay of the land:

1. **List all unique domains** — identify if this is a single-API or multi-API app
2. **List all unique URL paths** — group by resource type (e.g., `/api/v2/orders`, `/api/v2/products`)
3. **List all HTTP methods used** — GET, POST, PUT, DELETE, PATCH
4. **Note response status codes** — filter out 4xx/5xx, focus on successful calls
5. **Filter out noise**: Ignore static assets (`.js`, `.css`, `.png`, `.svg`, telemetry endpoints, analytics pings)

Focus on the subset of calls that match the user's described workflow.

---

## 🔐 Step 2: Security Parameter Extraction

This is the most critical step for `adopt_profile.json` and the CE token manager.

### 2a. Find Auth Headers

Look in each request's `headers` array for:

| Header Name | Pattern | Type |
|-------------|---------|------|
| `Authorization` | `Bearer eyJ...` (JWT) or `Bearer sk-...` (API key) | JWT = dynamic, API key = static |
| `Authorization` | `Basic dXNlcjpwYXNz...` (base64) | Usually static (service account) |
| `X-Api-Key` | Any value | Usually static |
| `X-Auth-Token` | Any value | Usually dynamic |
| `X-CSRF-Token` | Short random string | Dynamic (changes per session) |
| `X-Request-Token` | Any value | Dynamic |
| Custom headers | Any `X-*` header not matching standard HTTP | Inspect value to determine |

### 2b. Find Session Cookies

In the `Cookie` header or `Set-Cookie` response header, look for:
- Session IDs (`JSESSIONID`, `session_id`, `sessionToken`)
- Auth cookies (`auth`, `access_token`, `refresh_token`)
- CSRF cookies (often match the CSRF header value)

### 2c. Classify Each Security Parameter

For each auth header/cookie found, classify it as **static** or **dynamic**:

| Static (API Key) | Dynamic (Session Token) |
|-----------------|------------------------|
| Same value across ALL entries in the HAR | Changes between sessions or users |
| Looks like `sk-abc123...`, `apikey-xyz...` | Looks like a JWT (`eyJ...`) or random string |
| Often labeled "API Key" or "Service Account" | Tied to browser session |
| Used in service-to-service calls | Used in user-facing browser sessions |

**Static params** → go directly into `adopt_profile.json` `security_params`
**Dynamic params** → need a token config in the CE token manager

### 2d. Determine Token Extraction Method

For each **dynamic** security parameter, identify where the browser stores it:

```
Is it in a Cookie? → storage-type: cookie, --cookie-key "<cookie-name>"
Is it in localStorage? → check by looking at JS console patterns → --storage-key "<key>"
Is it in sessionStorage? → similar to localStorage
Is it in a <meta> tag? → customScript: return document.querySelector('meta[name="csrf-token"]').content
Is it injected into JS globals? → customScript: return window.__INITIAL_STATE__.auth.token
Is it in a response header that the browser stores? → customScript
```

---

## 🌐 Step 3: Base URL Extraction

1. Find the **common prefix** across all API calls (e.g., `https://app.example.com`)
2. If multiple different domains are called (e.g., `api.example.com` AND `auth.example.com`), plan for `profiles_map`

```json
// Single API — root base_url is enough
{
  "base_url": "https://app.example.com",
  "security_params": { "Authorization": "Bearer <static-key>" }
}

// Multiple APIs — use profiles_map
{
  "base_url": "https://app.example.com",
  "security_params": {},
  "profiles_map": {
    "AuthAPI": {
      "base_url": "https://auth.example.com",
      "security_params": { "X-Api-Key": "<api-key>" }
    }
  }
}
```

---

## ⚙️ Step 4: Payload Generalization

This step converts hardcoded recorded requests into dynamic WDL actions.

### 4a. Identify Dynamic Fields

For each request body and URL, classify each value:

| Value Type | Example | WDL Treatment |
|-----------|---------|---------------|
| **User input** | `"query": "search term"` | REQUIRED_INPUT or workflow_params |
| **Entity ID from previous call** | `"orderId": "12345"` after GET /orders | JQ_FILTER from previous response |
| **Static config** | `"format": "json"`, `"version": "v2"` | Hardcode in WDL |
| **Pagination** | `"page": 1, "limit": 20` | REQUIRED_INPUT or default values |
| **Timestamps** | `"from": "2024-01-01"` | REQUIRED_INPUT |
| **Hardcoded IDs** | `"companyId": 42` | Likely workflow_params (per-client config) |

### 4b. URL Path Parameters

Compare multiple entries calling the same endpoint with different IDs:
```
GET /api/orders/12345   → "12345" is a dynamic ID → use {order_id} path param
GET /api/orders/67890
```
This means the WDL URL should be: `/api/orders/{order_id}` with `order_id` from REQUIRED_INPUT or a previous step.

### 4c. Query Parameters

```
GET /api/products?search=widget&page=1&limit=20
```
- `search` → REQUIRED_INPUT
- `page` + `limit` → often default values, may be REQUIRED_INPUT if user controls them

### 4d. Data Flow Between Calls

Look for values in a request body that match values from a PREVIOUS response. This indicates a multi-step workflow:

```
Step 1: POST /api/sessions → response: { "sessionId": "abc123" }
Step 2: GET /api/data?sessionId=abc123 ← "abc123" came from Step 1
```

In WDL, this becomes:
1. REST call to `/api/sessions`
2. JQ_FILTER to extract `sessionId` from Step 1 response
3. REST call to `/api/data` using the extracted `sessionId`

### 4e. Working with Chrome Extension Dumps

When the user provides a Chrome extension recording dump (not a raw HAR), it contains:
- **HTTP call log**: Same as HAR entries (method, URL, headers, body, response)
- **Timing info**: When each call was made relative to user actions
- **Voice narration**: User's description of what each step does
- **Click events**: UI interactions that triggered the calls

**Preferred approach**: Use the dump as rich context to build a well-informed `widdle.json` from scratch — do NOT import it hardcoded and try to generalize afterward. The narration tells you what each call is supposed to do; the HTTP log tells you how.

Map the narration to WDL steps:
```
"I clicked search and typed the order number" → REQUIRED_INPUT for order_number
"The list of results appeared" → REST GET /api/orders?query={order_number}
"I clicked on the first result" → JQ_FILTER to extract first result ID
"The details page loaded" → REST GET /api/orders/{id}
```

---

## 📝 Step 5: Populate `adopt_profile.json`

After analysis, create or update `workspaces/<env-id>/adopt_profile.json`:

```json
{
  "base_url": "<extracted-base-url>",
  "application_base_url": "<extracted-app-url-if-different>",
  "workflow_params": {
    "<param>": "<default-value-from-analysis>"
  },
  "security_params": {
    "<static-header-name>": "<static-value>"
  }
}
```

**Only put static (non-secret, non-session) values in `security_params` here.**
Dynamic session tokens go in the CE token manager (next step), NOT here.

---

## 🔑 Step 6: Set Up CE Token Manager

For each **dynamic** security parameter identified in Step 2:

```bash
# Cookie-based token
python cli/workspace.py token-config create \
  --name "<descriptive-name>" \
  --domain-suffix "<app-domain.com>" \
  --storage-type cookie \
  --cookie-key "<cookie-name>"

# localStorage-based token
python cli/workspace.py token-config create \
  --name "<descriptive-name>" \
  --domain-suffix "<app-domain.com>" \
  --storage-type localStorage \
  --storage-key "<localStorage-key>"

# Custom JS extraction (for DOM, JS globals, or complex cases)
python cli/workspace.py token-config create \
  --name "<descriptive-name>" \
  --domain-suffix "<app-domain.com>" \
  --storage-type customScript \
  --custom-script "return <JS expression to extract the token>;"
```

**Naming convention**: Use lowercase_underscore names that describe the token's purpose:
- `csrf_token`, `session_token`, `bearer_token`, `api_auth_header`

Verify the configs were published:
```bash
python cli/workspace.py token-config list
```

---

## 🎮 Step 7: Create Playground Profile

The playground profile is required for CE testing. It maps security headers to token config names:

```bash
# Create the profile (if it doesn't exist yet)
python cli/workspace.py playground-profile create \
  --name "<Client Name> Profile" \
  --base-url "<app-base-url>"

# Add security headers referencing token config names (NOT the actual values)
python cli/workspace.py playground-profile update <profile-id> \
  --security-headers '{"<header-name>": "<token-config-name>"}'
```

**⚠️ CRITICAL**: The value in `security-headers` must be the TOKEN CONFIG NAME (e.g., `"csrf_token"`), NOT the actual token value. The CE token manager resolves the name to the actual value at runtime.

Verify the profile:
```bash
python cli/workspace.py playground-profile list
python cli/workspace.py playground-profile show <profile-id>
```

---

## ✅ Complete Setup Checklist

```
HAR / Extension Recording Analysis
│
├─ 1. Scan entries (filter noise, list endpoints)
├─ 2. Security parameters
│   ├─ Identify all auth headers and cookies
│   ├─ Classify as static vs dynamic
│   └─ Determine extraction method for dynamic ones
├─ 3. Base URL extraction → adopt_profile.json base_url
├─ 4. Payload generalization
│   ├─ Classify each field (user input / previous response / static / config)
│   ├─ Identify URL path parameters
│   ├─ Map data flow between calls
│   └─ Map narration (if extension dump) to WDL steps
├─ 5. Populate adopt_profile.json (static params only)
├─ 6. Create token configs (dynamic params → CLI token-config create)
└─ 7. Create/update playground profile (reference token configs by name)
```

After completing this checklist, CE testing prerequisites are met:
→ Proceed to `prompts/system/TESTING_PROMPT.md` for CE test setup.

---

## 🔗 Related Prompts

- **CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md** — Building the WDL from the analyzed patterns
- **WORKSPACE_HIERARCHY_PROMPT.md** — `adopt_profile.json` inheritance and profiles_map
- **TESTING_PROMPT.md** — Running CE tests after setup
- **REMOTE_ACTION_WORKFLOW_PROMPT.md** — Creating and configuring client environments
