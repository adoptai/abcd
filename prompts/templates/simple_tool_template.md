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
    "url": "/api/v1/endpoint",
    "output_key": "api_response"
  },
  {
    "id": "output",
    "operation": "OUTPUT_TEXT",
    "raw": true,
    "inputs": {
      "content": "{api_response}"
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
│  output_key:    │
│  "api_response" │
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
  "id": "unique_id",
  "operation": "REST",
  "method": "GET|POST|PUT|PATCH|DELETE",
  "canonical_api_endpoint": "/path/from/api/spec",
  "url": "/path/with/{workflow_arguments.param}",
  "output_key": "response_variable",
  
  // For POST/PUT/PATCH:
  "body": {
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

### 3. Data Transformation (Optional)

Use when you need to extract or filter the API response:

**JQ_FILTER** - Complex JSON transformations:
```json
{
  "id": "transform",
  "operation": "JQ_FILTER",
  "jq_query": ".data | map({id, name})",
  "inputs": {
    "data": "{api_response}"
  },
  "output_key": "filtered_data"
}
```

**EXTRACT** - Simple field extraction:
```json
{
  "id": "extract",
  "operation": "EXTRACT",
  "inputs": {
    "source": "{api_response}",
    "path": "data.items"
  },
  "output_key": "items"
}
```

**PROJECT** - Select specific fields:
```json
{
  "id": "project",
  "operation": "PROJECT",
  "inputs": {
    "source": "{api_response}",
    "fields": ["id", "name", "status"]
  },
  "output_key": "projected_data"
}
```

### 4. OUTPUT_TEXT Block (MUST be last)

```json
{
  "id": "output",
  "operation": "OUTPUT_TEXT",
  "raw": true,
  "inputs": {
    "content": "{api_response}"
  }
}
```

## Variable Reference

- `{workflow_arguments.param_name}` - Reference input parameters
- `{previous_output_key}` - Reference output from previous operation
- Use exact names from `required_inputs` and `output_key` fields

## Common Patterns

### Pattern A: Direct API Response
```json
[
  {"required_inputs": {...}},
  {"operation": "REST", "output_key": "response"},
  {"operation": "OUTPUT_TEXT", "inputs": {"content": "{response}"}}
]
```

### Pattern B: Filtered Response
```json
[
  {"required_inputs": {...}},
  {"operation": "REST", "output_key": "raw_response"},
  {"operation": "JQ_FILTER", "output_key": "filtered"},
  {"operation": "OUTPUT_TEXT", "inputs": {"content": "{filtered}"}}
]
```

### Pattern C: POST with Body
```json
[
  {"required_inputs": {"data": {"type": "object", "definition": "Data to send"}}},
  {"operation": "REST", "method": "POST", "body": "{workflow_arguments.data}"},
  {"operation": "OUTPUT_TEXT", ...}
]
```

## Checklist Before Submitting

- [ ] `required_inputs` block is FIRST
- [ ] All API parameters are defined in `required_inputs`
- [ ] `canonical_api_endpoint` matches the API spec exactly
- [ ] `url` uses `{workflow_arguments.X}` for dynamic values
- [ ] `output_key` is set on operations that produce data
- [ ] `OUTPUT_TEXT` with `raw: true` is LAST
- [ ] All variable references use correct syntax: `{variable_name}`








