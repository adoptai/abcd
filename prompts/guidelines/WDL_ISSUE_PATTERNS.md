# WDL Issue Patterns Guide

This document catalogs common WDL issues, their detection patterns, and fix strategies. Use this as a reference when diagnosing and fixing tool WDL problems.

## ⚠️ CRITICAL RULE: Keep API and WDL in Sync

**When fixing any issue, you MUST follow this order:**

### Mandatory Fix Order

1. **UPDATE THE API ON REMOTE FIRST**
   ```bash
   # Fix API path (e.g., trailing slash)
   python cli/fix_api_path.py <api-id> --trailing-slash add
   ```
   - API changes must be applied to the remote AdoptAI platform FIRST
   - Verify the API is updated before proceeding to WDL

2. **THEN UPDATE THE WDL FOR THE SPECIFIC ACTION**
   - Update `widdle.json` to match the corrected API
   - Ensure `canonical_api_endpoint` matches the updated API path exactly
   - Ensure `url` uses the correct path with `{workflow_arguments.X}` substitution

3. **SAVE DRAFT AND TEST**
   ```bash
   python cli/save_wdl_draft.py --workflow-id <id> --standalone
   python cli/test_wdl_action.py <id>
   ```

### Why This Matters

| Scenario | Result |
|----------|--------|
| Update WDL only, not API | Tool references wrong API definition, may break |
| Update API only, not WDL | Tool uses stale WDL with old paths, will fail |
| Update both in sync | ✅ Tool works correctly |

### Sync Verification Checklist

After EVERY fix, verify:
- [ ] API `canonical_api_endpoint` on remote matches actual endpoint (with correct trailing slash)
- [ ] WDL `canonical_api_endpoint` matches API definition exactly
- [ ] WDL `url` matches canonical path (with `{workflow_arguments.X}` for parameters)
- [ ] Remote test passes

---

## Issue Categories

### Category 1: URL and Path Issues

#### 1.1 Trailing Slash Mismatch

**Pattern**: API canonical path differs from actual HTTP log URL in trailing slash

**Detection Regex**:
```python
# Check if paths differ only in trailing slash
def has_trailing_slash_mismatch(api_path, log_path):
    normalized_api = api_path.rstrip('/')
    normalized_log = log_path.rstrip('/')
    return normalized_api == normalized_log and api_path != log_path
```

**Example**:
```
API Path:  /api/v1/organizations/{orgId}
Log Path:  /api/v1/organizations/{orgId}/
Status:    MISMATCH - API missing trailing slash
```

**Fix Strategy** (in this EXACT order):
1. Check network logs for the correct trailing slash behavior
2. **Update API on remote FIRST** (choose one):
   ```bash
   # Option A: Set path directly (PREFERRED - no cache needed)
   python cli/fix_api_path.py <api-id> --path "/api/v1/resource/{id}/"
   
   # Option B: Add/remove trailing slash (needs cache or fetches from remote)
   python cli/fix_api_path.py <api-id> --trailing-slash add|remove
   ```
3. **Then update local API spec file**: Update `apis/{api_id}.json` to match
4. **Then update WDL**: Update `canonical_api_endpoint` and `url` in `widdle.json` to match
5. Save draft and test to verify sync

**⚠️ NEVER update WDL without updating API on remote first - they must stay in sync!**

**Severity**: HIGH - Can cause 404 errors

---

#### 1.2 Missing Path Segment

**Pattern**: WDL URL missing path segments present in network logs

**Detection**:
```python
def detect_missing_segments(wdl_url, log_url):
    wdl_segments = wdl_url.strip('/').split('/')
    log_segments = log_url.strip('/').split('/')
    missing = [s for s in log_segments if s not in wdl_segments]
    return missing
```

**Example**:
```
WDL URL:  /api/v1/users/{userId}
Log URL:  /api/v1/organizations/{orgId}/users/{userId}
Missing:  ['organizations', '{orgId}']
```

**Fix Strategy**:
1. Add missing segments to URL
2. Add corresponding required_inputs if dynamic

**Severity**: CRITICAL - Requests will fail

---

#### 1.3 Hardcoded Path Values

**Pattern**: WDL has hardcoded values that should be parameterized

**Detection Regex**:
```regex
/api/v\d+/[^{]+/[a-f0-9-]{36}/
```

**Example**:
```json
{
  "url": "/api/v1/organizations/550e8400-e29b-41d4-a716-446655440000/users"
}
```

**Fix Strategy**:
1. Replace hardcoded ID with `{workflow_arguments.paramName}`
2. Add parameter to required_inputs

**Severity**: HIGH - Tool only works for one specific resource

---

### Category 2: Parameter Issues

#### 2.1 Missing workflow_arguments Prefix

**Pattern**: URL uses `{param}` instead of `{workflow_arguments.param}`

**Detection Regex**:
```regex
\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!.*workflow_arguments)
```

**Example**:
```json
// WRONG
{ "url": "/api/users/{userId}" }

// CORRECT
{ "url": "/api/users/{workflow_arguments.userId}" }
```

**Fix Strategy**:
1. Add `workflow_arguments.` prefix to all parameter references
2. Verify parameter exists in required_inputs

**Severity**: HIGH - Parameters won't be substituted

---

#### 2.2 Missing required_inputs Entry

**Pattern**: Parameter referenced in WDL but not declared in required_inputs

**Detection**:
```python
def detect_missing_inputs(wdl):
    referenced = set()
    declared = set()
    
    for block in wdl:
        # Find all {workflow_arguments.X} references
        if isinstance(block, dict):
            for value in str(block.values()):
                matches = re.findall(r'\{workflow_arguments\.(\w+)\}', value)
                referenced.update(matches)
        
        if 'required_inputs' in block:
            declared.update(block['required_inputs'].keys())
    
    return referenced - declared
```

**Example**:
```json
{
  "url": "/api/users/{workflow_arguments.userId}",
  "required_inputs": {}  // Missing userId!
}
```

**Fix Strategy**:
1. Add missing parameter to required_inputs
2. Include type and definition

**Severity**: HIGH - Tool will fail at runtime

---

#### 2.3 Mismatched Parameter Types

**Pattern**: Parameter type doesn't match API expectation

**Detection**:
```python
def detect_type_mismatch(wdl, network_logs):
    # Compare parameter values in logs to declared types
    pass
```

**Example**:
```json
{
  "required_inputs": {
    "limit": { "type": "string" }  // Should be integer
  }
}
```

**Fix Strategy**:
1. Change type to match API requirements
2. Common types: string, integer, boolean, array, object

**Severity**: MEDIUM - May cause validation errors

---

### Category 3: Query Parameter Issues

#### 3.1 Missing Query Parameters

**Pattern**: Network logs show query params not present in WDL

**Detection**:
```python
from urllib.parse import urlparse, parse_qs

def detect_missing_query_params(wdl_block, log_url):
    log_params = parse_qs(urlparse(log_url).query)
    wdl_params = wdl_block.get('query_parameters', {})
    
    missing = {}
    for param, values in log_params.items():
        if param not in wdl_params:
            missing[param] = values[0]
    
    return missing
```

**Example**:
```
Log URL: /api/users?include=profile&page_size=50
WDL query_parameters: {}
Missing: {'include': 'profile', 'page_size': '50'}
```

**Fix Strategy**:
1. Add missing params to query_parameters
2. Use `{workflow_arguments.X}` for dynamic values
3. Use literal values for static params

**Severity**: MEDIUM - API may return incomplete data

---

#### 3.2 Incorrect Query Parameter Format

**Pattern**: Query parameter value formatted incorrectly

**Detection**:
```python
def detect_incorrect_format(wdl_params, log_params):
    issues = []
    for param, wdl_value in wdl_params.items():
        if param in log_params:
            log_value = log_params[param][0]
            # Check array encoding, boolean format, etc.
    return issues
```

**Example**:
```json
// WRONG - array as single value
{ "ids": "{workflow_arguments.ids}" }

// CORRECT - array encoding
{ "ids[]": "{workflow_arguments.ids}" }
```

**Fix Strategy**:
1. Match the format expected by the API
2. Common formats: array[], comma-separated, repeated keys

**Severity**: MEDIUM - Query may not work correctly

---

### Category 4: Method Issues

#### 4.1 HTTP Method Mismatch

**Pattern**: WDL uses different HTTP method than network logs

**Detection**:
```python
def detect_method_mismatch(wdl_method, log_method):
    return wdl_method.upper() != log_method.upper()
```

**Example**:
```
WDL Method: GET
Log Method: POST
Status: MISMATCH
```

**Fix Strategy**:
1. Update WDL method to match network logs
2. May need to add request body for POST/PUT/PATCH

**Severity**: CRITICAL - Request will fail

---

### Category 5: Structure Issues

#### 5.1 Missing Block ID

**Pattern**: WDL block missing required `id` field

**Detection**:
```python
def detect_missing_id(block, index):
    if 'id' not in block:
        return f"Block at index {index} missing 'id'"
    return None
```

**Example**:
```json
{
  "operation": "REST",
  "url": "/api/users"
  // Missing: "id": "get_users"
}
```

**Fix Strategy**:
1. Add unique `id` field
2. Use descriptive name (snake_case)

**Severity**: HIGH - WDL won't validate

---

#### 5.2 Invalid Block Order

**Pattern**: Blocks not in logical execution order

**Detection**:
```python
def detect_order_issues(wdl):
    # METADATA should be first
    # Dependencies should come before dependents
    pass
```

**Example**:
```json
[
  { "operation": "TRANSFORM", "input": "{step_1_result}" },  // Uses step_1
  { "operation": "REST", "id": "step_1" }  // Defined after use!
]
```

**Fix Strategy**:
1. Move METADATA block to first position
2. Order blocks by dependency

**Severity**: HIGH - May cause undefined reference errors

---

#### 5.3 Orphaned API Reference

**Pattern**: WDL references an API ID that doesn't exist

**Detection**:
```python
def detect_orphaned_api(wdl_api_id, all_apis):
    return wdl_api_id not in [a['id'] for a in all_apis]
```

**Example**:
```json
{
  "api_id": "api_12345",  // API was deleted
  "canonical_api_endpoint": "/api/v1/users"
}
```

**Fix Strategy**:
1. Find the correct API ID
2. Or remove api_id if not needed

**Severity**: MEDIUM - May affect tracing/logging

---

## Detection Priority Matrix

| Issue Type | Severity | Detection Difficulty | Fix Complexity |
|------------|----------|---------------------|----------------|
| Trailing Slash | HIGH | Easy | Easy |
| Missing Segments | CRITICAL | Medium | Medium |
| Missing workflow_arguments | HIGH | Easy | Easy |
| Missing required_inputs | HIGH | Easy | Easy |
| Missing Query Params | MEDIUM | Medium | Easy |
| Method Mismatch | CRITICAL | Easy | Easy |
| Missing Block ID | HIGH | Easy | Easy |
| Orphaned API Reference | MEDIUM | Easy | Medium |

## Automated Detection Script

Use this command to detect all issue types:

```bash
python cli/inspect_tool_wdl.py <tool-id> --verbose
```

Or for multiple tools:

```bash
python cli/diagnose_and_fix.py --scan --format llm
```

## Fix File Template

When creating fixes, use this template:

```json
{
  "tool_id": "<tool-id>",
  "tool_title": "Tool Name",
  "changes_summary": "Description of all fixes applied",
  "issues_fixed": [
    "TRAILING_SLASH_MISMATCH",
    "MISSING_WORKFLOW_ARGUMENTS"
  ],
  "new_wdl": [
    {
      "id": "metadata",
      "required_inputs": {
        "paramName": {
          "type": "string",
          "definition": "Description of the parameter"
        }
      }
    },
    {
      "id": "api_call",
      "operation": "REST",
      "method": "GET",
      "url": "/api/v1/resource/{workflow_arguments.paramName}/",
      "canonical_api_endpoint": "/api/v1/resource/{resourceId}/",
      "query_params": {}
    }
  ],
  "test_prompts": [
    "Test prompt 1",
    "Test prompt 2"
  ]
}
```

## Propagating Fixes to Multiple Tools

When you fix an API issue, **all tools using that API must be updated**:

```bash
# 1. Find all tools using an API
python cli/diagnose_and_fix.py --scan --format llm | grep "api_id: <api-id>"

# 2. For each affected tool, update the WDL
# After fixing API on remote, update each tool's widdle.json

# 3. Save draft and test each tool
python cli/save_wdl_draft.py --workflow-id <tool-1> --standalone
python cli/test_wdl_action.py <tool-1>
# Repeat for all affected tools
```

**IMPORTANT**: If you fix an API and don't update all tools using it, those tools will break!

---

### Category 6: profiles_map Issues

#### 6.1 Missing Application Profile

**Pattern**: REST block uses `application` property but no matching entry in `profiles_map`

**Detection**:
```python
def detect_missing_profile(wdl, adopt_profile):
    profiles_map = adopt_profile.get('profiles_map', {})
    for block in wdl:
        if block.get('operation') == 'REST' and 'application' in block:
            app_name = block['application']
            if app_name not in profiles_map:
                return f"Missing profile for application: {app_name}"
    return None
```

**Example**:
```json
// WDL
{ "operation": "REST", "application": "ShippingAPI", "url": "/v2/shipments" }

// adopt_profile.json - Missing ShippingAPI profile!
{ "base_url": "https://api.example.com", "profiles_map": {} }
```

**Fix Strategy**:
1. Add the missing profile to `adopt_profile.json`:
   ```json
   {
     "profiles_map": {
       "ShippingAPI": {
         "base_url": "https://api.shipping-provider.com",
         "security_params": {
           "API-Key": "your-api-key"
         }
       }
     }
   }
   ```

**Severity**: CRITICAL - REST call will use wrong base URL or fail authentication

---

#### 6.2 Wrong Security Parameter Name

**Pattern**: Using `security_headers` instead of `security_params` in `adopt_profile.json`

**Detection**:
```python
def detect_wrong_security_name(adopt_profile):
    profiles_map = adopt_profile.get('profiles_map', {})
    for app_name, profile in profiles_map.items():
        if 'security_headers' in profile:
            return f"Use 'security_params' not 'security_headers' in profiles_map.{app_name}"
    return None
```

**Example**:
```json
// WRONG - Uses security_headers
{
  "profiles_map": {
    "ShippingAPI": {
      "base_url": "https://api.shipping-provider.com",
      "security_headers": { "API-Key": "key" }  // WRONG
    }
  }
}

// CORRECT - Uses security_params
{
  "profiles_map": {
    "ShippingAPI": {
      "base_url": "https://api.shipping-provider.com",
      "security_params": { "API-Key": "key" }  // CORRECT
    }
  }
}
```

**Note**: The CLI automatically converts `security_params` to `security_headers` when sending to the backend.

**Severity**: HIGH - Authentication will fail

---

#### 6.3 Application Name Mismatch

**Pattern**: Application name in WDL doesn't match key in `profiles_map` (case-sensitive)

**Detection**:
```python
def detect_app_name_mismatch(wdl, adopt_profile):
    profiles_map = adopt_profile.get('profiles_map', {})
    for block in wdl:
        if block.get('operation') == 'REST' and 'application' in block:
            app_name = block['application']
            # Check for case mismatch
            for profile_key in profiles_map.keys():
                if app_name.lower() == profile_key.lower() and app_name != profile_key:
                    return f"Case mismatch: WDL uses '{app_name}' but profile has '{profile_key}'"
    return None
```

**Example**:
```json
// WDL uses "shippingapi" (lowercase)
{ "operation": "REST", "application": "shippingapi" }

// adopt_profile.json has "ShippingAPI" (different case)
{ "profiles_map": { "ShippingAPI": { ... } } }
```

**Fix Strategy**:
1. Ensure exact case match between WDL `application` and `profiles_map` key

**Severity**: CRITICAL - Profile won't be found, falls back to default

---

---

## ⚠️ MANDATORY: Data Flow Tracing Protocol

When debugging WDL execution failures, follow this systematic 4-step protocol:

### Step 1: Identify the Failing Operation

From the error message or trace, identify:
- **Which operation failed** (by `id`)
- **What error type** occurred (KeyError, TypeError, JQ error, etc.)
- **What input was expected** vs what was received

### Step 2: Trace Data Backwards

Starting from the failing operation, trace the data flow backwards:

```
failing_operation.input → previous_operation.output → ... → source
```

For each step:
1. **Check the output type** - Is it an array, object, string?
2. **Check for wrappers** - Is the data wrapped in `result: [...]` or similar?
3. **Check for nesting** - Is it `[[data]]` instead of `[data]`?

### Step 3: Verify Operation Parameters

For the operation that produced the problematic output:
- **JQ_FILTER**: Check `extract_all` parameter (default: `true` = wraps in array!)
- **EXTRACT**: Check if input is actually an object (not array)
- **FIRST_ELEMENT**: Check if you need multiple unwraps for nested arrays

### Step 4: Fix and Verify

After identifying the issue:
1. **Fix the operation** (add FIRST_ELEMENT, set extract_all=false, etc.)
2. **Save draft** using `python cli/save_wdl_draft.py`
3. **Test** using `python cli/test_runner.py` (simpler, always allow_draft=True)
4. **Verify trace** - Check that data types match expectations at each step

### Common Data Flow Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Cannot index array with string` | JQ_FILTER wrapped result in array | Add FIRST_ELEMENT or set `extract_all: false` |
| `Input is not a JSON object, it is a list` | Double-nested array `[[...]]` | Add second FIRST_ELEMENT |
| `KeyError: 'field'` | Input is array, not object | Add FIRST_ELEMENT before EXTRACT |
| `result: []` wrapper | JQ_FILTER default behavior | Access via `.result` or set `extract_all: false` |

### Example Debug Session

```
Error: "Cannot index array with string 'Items'"
↓
Trace backwards: extractItems.input = "{decodeResponse}"
↓
Check decodeResponse output: { "result": [{ "Items": [...] }] }
↓
Problem: JQ_FILTER wrapped output in array!
↓
Fix: Add FIRST_ELEMENT between JQ_FILTER and EXTRACT
     OR: Modify JQ filter to access .Items directly
```

---

## Related Documentation

- [WDL Schema Reference](../../docs/wdl_schema.md)
- [API Configuration Guide](../../docs/api_configuration.md)
- [Diagnostic Toolkit CLI](../../docs/diagnostic_toolkit.md)
- `prompts/system/DIAGNOSE_AND_FIX_SYSTEM_PROMPT.md` - Full diagnostic system prompt
- `prompts/system/WORKSPACE_HIERARCHY_PROMPT.md` - profiles_map configuration details



