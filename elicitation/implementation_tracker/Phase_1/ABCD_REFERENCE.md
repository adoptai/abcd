# Phase 1: ABCD Quick Reference

Extracted from `docs/ABCD_ALIGNMENT_DEEP_DIVE.md` — the key ABCD formats and concepts needed for Phase 1 implementation.

## WDL Operations Reference

| Operation | Description | Key Config Fields |
|-----------|-------------|-------------------|
| `REST` | HTTP API call | method, url, headers, body |
| `JQ_FILTER` | Transform JSON with jq | expression, output_var |
| `EXTRACT` | Extract specific fields | fields, source |
| `PROJECT` | Reshape data structure | mapping |
| `PROMPT` | LLM processing | prompt_template, model |
| `CONDITION` | Conditional branching | condition, if_true, if_false |
| `FOR_EACH` | Iterate over array | source_array, step |
| `PAGINATE` | Handle paginated responses | next_page_expression, max_pages |
| `OUTPUT_TEXT` | Format final output | template |
| `PROMPT_AND_TOOLS_AGENT` | Uber agent with sub-tools | prompt, tools |

**Phase 1 focuses on:** `REST`, `JQ_FILTER`, `CONDITION` (basic error handling), `OUTPUT_TEXT`

## adopt_profile.json Schema

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
    }
  },
  "profiles_map": {
    "crm": {
      "base_url": "https://crm.example.com/api",
      "security_params": { "type": "bearer" }
    }
  }
}
```

## Test Case Schema

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

Validation types: `contains`, `exact`, `similarity`

## WDL (widdle.json) Schema

```json
{
  "steps": [
    {
      "operation": "REST",
      "config": {
        "method": "POST",
        "url": "{{base_url}}/api/customers",
        "headers": {"Authorization": "Bearer {{access_token}}"},
        "body": {"name": "{{customer_name}}"}
      },
      "description": "Create a new customer"
    },
    {
      "operation": "JQ_FILTER",
      "config": {"expression": ".id"},
      "output": "customer_id",
      "description": "Extract customer ID"
    },
    {
      "operation": "CONDITION",
      "config": {
        "condition": ".status_code >= 400",
        "if_true": {"operation": "OUTPUT_TEXT", "config": {"template": "Error: {{.message}}"}},
        "if_false": null
      },
      "description": "Handle error responses"
    },
    {
      "operation": "OUTPUT_TEXT",
      "config": {
        "template": "Created customer {{customer_name}} (ID: {{customer_id}})"
      }
    }
  ]
}
```

## Workspace Directory Structure

```
actions/<action_name>/
├── adopt_profile.json
├── widdle.json
├── metadata.json
├── requirements.md
├── description.txt
├── apis/
│   └── manifest.json
├── test_cases/
│   └── test_cases.json
├── versions/
└── traces/
```

## metadata.json Schema

```json
{
  "action_id": "create-customer-order",
  "name": "Create Customer Order",
  "description": "Creates a new customer and retrieves their order history",
  "created_at": "2026-02-23T00:00:00Z",
  "source": "elicitation-agent",
  "source_process_id": "proc-uuid",
  "source_project_id": "proj-uuid",
  "version": "draft",
  "tool_mode": false
}
```

## HAR Filtering Signals

**Include (likely API call):**
- Response Content-Type: `application/json`, `application/xml`, `text/xml`, `application/hal+json`
- URL contains: `/api/`, `/v1/`, `/v2/`, `/graphql`
- Request method: POST, PUT, PATCH, DELETE (non-navigation)

**Exclude (definitely not API):**
- URL ends with: `.js`, `.css`, `.png`, `.jpg`, `.gif`, `.svg`, `.woff`, `.woff2`, `.ico`, `.map`
- URL contains: `analytics`, `tracking`, `telemetry`, `beacon`, `pixel`
- Response Content-Type: `text/html` (page loads), `text/css`, `application/javascript`, `image/*`, `font/*`
- Method: OPTIONS (CORS preflight)
- Status: 301/302 with Location to same domain (redirects, not API responses)

## Auth Detection Patterns

| Pattern | Header/Signal | security_params Output |
|---------|---------------|----------------------|
| Bearer token | `Authorization: Bearer <token>` | `{"type": "bearer", "token_env_var": "API_ACCESS_TOKEN"}` |
| Basic auth | `Authorization: Basic <base64>` | `{"type": "basic", "username_env_var": "API_USERNAME", "password_env_var": "API_PASSWORD"}` |
| API key (header) | `X-API-Key: <key>` or custom | `{"type": "api_key", "location": "header", "name": "<header_name>", "key_env_var": "API_KEY"}` |
| API key (query) | `?api_key=<key>` | `{"type": "api_key", "location": "query", "name": "<param_name>", "key_env_var": "API_KEY"}` |
| Cookie | `Cookie: session=<value>` | `{"type": "cookie", "note": "session-based auth detected"}` |
| OAuth2 | 302 → authorize + token exchange | `{"type": "oauth2", "note": "OAuth2 flow detected"}` |
