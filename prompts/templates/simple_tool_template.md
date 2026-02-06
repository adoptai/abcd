# Simple Tool Template

This template is for creating **simple API wrapper tools** that follow the REST → OUTPUT pattern.

## When to Use This Template

Use this template when:
- Wrapping a single REST API endpoint
- The tool performs one primary action
- Output is the API response (optionally transformed)
- No complex multi-step logic is needed

## Initial WDL Structure

Start with this placeholder and customize based on the API specification:

```json
[
  {
    "required_inputs": {
      "example_param": {
        "type": "string",
        "definition": "Description of parameter (mandatory)"
      }
    }
  },
  {
    "id": "call_api",
    "operation": "REST",
    "method": "GET",
    "canonical_api_endpoint": "/api/v1/endpoint",
    "url": "/api/v1/endpoint"
  },
  {
    "id": "output",
    "operation": "OUTPUT_TEXT",
    "raw": true,
    "inputs": {
      "content": "{call_api}"
    }
  }
]
```

## WDL Pattern for Simple Tools

### Pattern: REST → (Optional Transform) → OUTPUT

```
┌─────────────────┐
│ required_inputs │  ← Define all inputs with types and definitions
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│      REST       │  ← Call the API endpoint
│  id: "call_api" │     (id is used to reference results)
└────────┬────────┘
         │
         ▼ (optional)
┌─────────────────┐
│   JQ_FILTER /   │  ← Transform response if needed
│    EXTRACT /    │
│    PROJECT      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   OUTPUT_TEXT   │  ← Return result to user
│   raw: true     │
└─────────────────┘
```

## ⚠️ CRITICAL: How to Reference Operation Outputs

**The `id` field of each operation IS the output reference.** There is no separate "output_key" field.

To reference the output of a previous operation:
- Use `{operation_id}` to get the full output
- Use `{operation_id.field_name}` to access a specific field

Example:
```json
{"id": "get_data", "operation": "REST", ...}     // id = "get_data"
{"id": "output", "operation": "OUTPUT_TEXT", "inputs": {"content": "{get_data}"}}  // Reference by id
```

---

## Key Guidelines

### 1. Required Inputs Block (MUST be first)

```json
{
  "required_inputs": {
    "param_name": {
      "type": "string|number|boolean|array|object",
      "definition": "Clear description (mandatory|optional)",
      "default": "optional default value"
    }
  }
}
```

### 2. REST Operation

```json
{
  "id": "api_call",
  "operation": "REST",
  "method": "GET|POST|PUT|PATCH|DELETE",
  "canonical_api_endpoint": "/path/from/api/spec",
  "url": "/path/with/{workflow_arguments.param}",
  
  // For POST/PUT/PATCH:
  "payload": {
    "field": "{workflow_arguments.input_field}"
  },
  
  // Optional headers:
  "headers": {
    "X-Custom-Header": "value"
  },
  
  // Optional query params:
  "query_params": {
    "filter": "{workflow_arguments.filter_param}"
  }
}
```

**Note:** The `id` field is used to reference the operation's result in subsequent steps (e.g., `{api_call}` or `{api_call.field}`).

### 3. Data Transformation (Optional)

Use when you need to extract or filter the API response:

**JQ_FILTER** - Complex JSON transformations:
```json
{
  "id": "filtered_data",
  "operation": "JQ_FILTER",
  "filter": ".data | map({id, name})",
  "input": "call_api"
}
```

**EXTRACT** - Simple field extraction:
```json
{
  "id": "items",
  "operation": "EXTRACT",
  "input": "call_api",
  "field": "data.items"
}
```

**PROJECT** - Select specific fields:
```json
{
  "id": "projected_data",
  "operation": "PROJECTION",
  "input": "call_api",
  "fields": ["id", "name", "status"]
}
```

### 4. OUTPUT_TEXT Block (MUST be last)

```json
{
  "id": "output",
  "operation": "OUTPUT_TEXT",
  "raw": true,
  "inputs": {
    "content": "{call_api}"
  }
}
```

## Variable Reference

- `{workflow_arguments.param_name}` - Reference input parameters
- `{operation_id}` - Reference the full output from a previous operation by its `id`
- `{operation_id.field}` - Reference a specific field from a previous operation's output

## Common Patterns

### Pattern A: Direct API Response
```json
[
  {"required_inputs": {...}},
  {"id": "api_call", "operation": "REST", ...},
  {"id": "output", "operation": "OUTPUT_TEXT", "raw": true, "inputs": {"content": "{api_call}"}}
]
```

### Pattern B: Filtered Response
```json
[
  {"required_inputs": {...}},
  {"id": "api_call", "operation": "REST", ...},
  {"id": "filtered", "operation": "JQ_FILTER", "input": "api_call", "filter": "..."},
  {"id": "output", "operation": "OUTPUT_TEXT", "raw": true, "inputs": {"content": "{filtered}"}}
]
```

### Pattern C: POST with Body
```json
[
  {"required_inputs": {"data": {"type": "object", "definition": "Data to send"}}},
  {"id": "api_call", "operation": "REST", "method": "POST", "payload": "{workflow_arguments.data}", ...},
  {"id": "output", "operation": "OUTPUT_TEXT", "raw": true, "inputs": {"content": "{api_call}"}}
]
```

## Checklist Before Submitting

- [ ] `required_inputs` block is FIRST
- [ ] All API parameters are defined in `required_inputs`
- [ ] `canonical_api_endpoint` matches the API spec exactly
- [ ] `url` uses `{workflow_arguments.X}` for dynamic values
- [ ] Each operation has a unique `id` for referencing its output
- [ ] `OUTPUT_TEXT` with `raw: true` is LAST
- [ ] All variable references use correct syntax: `{operation_id}` or `{operation_id.field}`
