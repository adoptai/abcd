# Testing Strategy and Commands

**PURPOSE**: Comprehensive testing guide for WDL workflows and Uber Agents.

**LOAD THIS WHEN**: Testing workflows, setting up test cases, or debugging failures.

---

## Test Types

| Type | Command | Description |
|------|---------|-------------|
| **Single** | `test_runner.py <id>` | Test one action (direct WDL execution) |
| **Inline Agent** | `test_runner.py <agent> --inline` | Test uber agent with inline subactions |
| **Remote** | `test_runner.py <id> --remote` | Test saved remote action (requires draft) |
| **Parallel** | `test_runner.py a1 a2 a3` | Test multiple in parallel |
| **Batch** | `test_runner.py --workspace ENV` | Test all in environment |
| **Agent Batch** | `test_runner.py --agent A --all-subactions` | All sub-actions |
| **Via-Agent** | `test_runner.py A --via-agent --subaction S` | Through agent |
| **Compile** | `test_runner.py <id> --compile` | Compile WDL (MANDATORY before testing) |

---

## Single Action Testing

### Basic Test (Direct WDL Execution)

```bash
python cli/test_runner.py my-action
```

**What happens:**
1. Validates WDL structure
2. Loads `test_cases/test_1.json`
3. Sends local `widdle.json` directly to /run-wdl endpoint
4. Captures execution trace
5. Validates output against expected

**No save/draft needed** — the local WDL is executed directly.

### Test Saved Remote Action

```bash
python cli/test_runner.py my-action --remote
```

Tests the saved remote action (requires `save_wdl_draft` first). Use this to verify the saved version matches expectations.

### Compile WDL (MANDATORY Before Testing)

```bash
python cli/test_runner.py my-action --compile
```

Compiles WDL via the remote compiler — validates JSON syntax and WDL structure. No execution.

### Specific Test Case

```bash
python cli/test_runner.py my-action --test test_2.json
```

### All Test Cases

```bash
python cli/test_runner.py my-action --all
```

### Verbose Output

```bash
python cli/test_runner.py my-action --verbose
```

Shows WDL operations and full traces for debugging.

---

## Parallel Testing

### Multiple Actions

```bash
python cli/test_runner.py action1 action2 action3
```

Default: 5 parallel workers.

### Control Parallelism

```bash
python cli/test_runner.py action1 action2 action3 --parallel 3
```

### Compile-Only Parallel

```bash
python cli/test_runner.py action1 action2 action3 --compile
```

---

## Batch Testing

### Test Entire Environment

```bash
python cli/test_runner.py --workspace production-client-a
```

Tests ALL actions (standalone + sub-actions) in the environment.

### Test Agent Sub-Actions

```bash
python cli/test_runner.py --agent inventory-agent --all-subactions
```

### Specify Environment

```bash
python cli/test_runner.py --agent my-agent --all-subactions --env staging
```

---

## Via-Agent Testing

Tests that a sub-action works correctly when called **through** its parent agent.

### Usage

```bash
python cli/test_runner.py inventory-agent --via-agent --subaction get-orderpoints
```

### How It Works

1. Sends prompt to agent designed to trigger sub-action
2. Agent processes and routes to sub-action
3. Verifies expected tool was called
4. Compares output

### Via-Agent Test Case

Create in `test_cases/subaction_tests/test_{action}_via_agent.json`:

```json
{
  "test_type": "via_agent",
  "target_subaction": "get-orderpoints",
  "prompt": "Can you get me the orderpoints for company 1?",
  "workflow_params": {},
  "expected_behavior": {
    "description": "Agent should call get-orderpoints",
    "expected_tool_calls": ["get-orderpoints"],
    "tool_input_contains": {"company_id": 1}
  },
  "expected_output": {
    "validation": "similarity",
    "description": "Returns orderpoints data"
  }
}
```

---

## Uber Agent Testing (Three-Tier Progression)

Uber agents with `PROMPT_AND_TOOLS_AGENT` must be tested bottom-up:

### Tier 1: Individual Subaction Testing

Test each subaction independently. This validates WDL logic in isolation.

```bash
python cli/test_runner.py <subaction> --test test_1.json
```

**Do not proceed to Tier 2 until ALL subactions pass Tier 1.**

### Tier 2: Inline Uber Agent Testing

Test the uber agent with subaction WDLs sent inline — no platform dependency.

```bash
# All subactions inline
python cli/test_runner.py <agent> --test test_1.json --inline

# Specific subactions inline (mixed mode)
python cli/test_runner.py <agent> --test test_1.json --inline search-products,add-product
```

The `--inline` flag:
- Scans the uber agent WDL for `PROMPT_AND_TOOLS_AGENT` steps
- Resolves `action_ids` to local subaction `widdle.json` files under `actions/`
- Sends them as `inline_actions` in the `/run-wdl` payload
- Falls back to platform DB lookup for non-inline action_ids

When `--inline` is used without specifying subaction names, ALL `action_ids` that match
local subaction directory names are automatically resolved from local files.

### Tier 3: Platform-Side Testing

After save+publish, test with real platform `action_ids` — final production validation.

```bash
python cli/test_runner.py <agent> --test test_1.json
```

---

## Test Case Format

Location: `{action}/test_cases/test_N.json`

### Basic Structure

```json
{
  "prompt": "Get orderpoints for company 1",
  "workflow_params": {
    "company_id": 1
  },
  "expected_output": {
    "description": "Should return list of orderpoints",
    "validation": "similarity",
    "key_fields": ["orderpoints", "count"]
  }
}
```

### Validation Types

#### similarity (Recommended)
LLM judges if actual output is semantically similar to expected.

```json
{
  "expected_output": {
    "validation": "similarity",
    "description": "Returns formatted orderpoints data"
  }
}
```

#### contains
Checks if key fields exist in output.

```json
{
  "expected_output": {
    "validation": "contains",
    "key_fields": ["orderpoints", "total", "status"]
  }
}
```

#### exact
Exact string match (rarely used).

```json
{
  "expected_output": {
    "validation": "exact",
    "sample_output": {"status": "success"}
  }
}
```

---

## Test Results Format

```
================================================================================
📊 TEST RESULTS SUMMARY
================================================================================
Total: 5 | Passed: 4 ✅ | Failed: 1 ❌
Time: 12.34s
--------------------------------------------------------------------------------

✅ PASSED:
   get-orderpoints (1234ms) - Test passed
   create-po (2345ms) - Test passed

❌ FAILED:
   delete-order (3456ms) - API returned 404
      Error: Resource not found

================================================================================
```

### Exit Codes

- `0` - All tests passed
- `1` - One or more tests failed

---

## Execution Traces

Location: `{action}/traces/trace_{test}_{timestamp}.json`

Contains step-by-step execution data for debugging.

---

## Creating Test Cases

### Best Practice: 3 Test Cases

1. **test_1.json** - Basic/common use case
2. **test_2.json** - Different input scenario
3. **test_3.json** - Edge case or error scenario

### Template

```json
{
  "prompt": "Natural language request",
  "workflow_params": {
    "param1": "value1",
    "param2": 123
  },
  "expected_output": {
    "description": "What the output should contain",
    "validation": "similarity"
  }
}
```

---

## Profile Resolution

Tests use hierarchical profile resolution:

1. Action `adopt_profile.json`
2. Agent `adopt_profile.json`
3. Environment `adopt_profile.json`
4. Root `adopt_profile.json`

Override with `--env`:

```bash
python cli/test_runner.py my-action --env staging
```

### Multi-API Actions with profiles_map

For actions that call multiple external APIs, configure `profiles_map` in `adopt_profile.json`:

```json
{
  "base_url": "https://default-api.example.com",
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

The REST block uses the `application` property to select the profile:
```json
{
  "id": "get_shipments",
  "operation": "REST",
  "application": "ShippingAPI",
  "url": "/v2/shipments/types"
}
```

**Note**: Use `security_params` in `adopt_profile.json` (user-facing). The CLI automatically maps this to `security_headers` when sending to the backend.

---

## Troubleshooting

### "Not linked to remote" (only with --remote flag)

Action not linked to remote. This only affects `--remote` mode. Save first:

```bash
python cli/save_wdl_draft.py --workflow-id my-action
```

Without `--remote`, tests execute the local `widdle.json` directly via /run-wdl and don't need a remote action.

### "Tool not called" in via-agent

1. Check agent's system prompt mentions the tool
2. Verify prompt is designed to trigger the tool
3. Ensure sub-action is published and in `action_ids`
4. Confirm tool mode is enabled

### Timeout

Increase timeout or simplify WDL operations.

### Auth Errors

Check profile resolution:

```bash
python cli/workspace.py profile show --action my-action
```

---

## Related Prompts

- **WORKSPACE_HIERARCHY_PROMPT.md** - Profile inheritance
- **UBER_AGENT_PROMPT.md** - Agent and sub-action setup
- **CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md** - WDL debugging



