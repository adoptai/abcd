# Testing Strategy and Commands

**PURPOSE**: Comprehensive testing guide for WDL workflows and Uber Agents.

**LOAD THIS WHEN**: Testing workflows, setting up test cases, or debugging failures.

---

## Test Types

| Type | Command | Description |
|------|---------|-------------|
| **Single** | `test_wdl_action.py <id>` | Test one action |
| **Parallel** | `test_runner.py a1 a2 a3` | Test multiple in parallel |
| **Batch** | `test_runner.py --workspace ENV` | Test all in environment |
| **Agent Batch** | `test_runner.py --agent A --all-subactions` | All sub-actions |
| **Via-Agent** | `test_runner.py A --via-agent --subaction S` | Through agent |
| **Local** | `test_wdl_action.py <id> --local-only` | Validate only |

---

## Single Action Testing

### Basic Test

```bash
python cli/test_wdl_action.py my-action
```

**What happens:**
1. Validates WDL structure
2. Loads `test_cases/test_1.json`
3. Runs action remotely
4. Captures execution trace
5. Validates output against expected

### Local Validation Only

```bash
python cli/test_wdl_action.py my-action --local-only
```

No remote execution - just validates JSON and WDL structure.

### Specific Test Case

```bash
python cli/test_wdl_action.py my-action --test test_2.json
```

### All Test Cases

```bash
python cli/test_wdl_action.py my-action --all
```

### Auto-Fix Issues

```bash
python cli/test_wdl_action.py my-action --local-only --auto-fix
```

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

### Local-Only Parallel

```bash
python cli/test_runner.py action1 action2 action3 --local-only
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

---

## Troubleshooting

### "No action_id found"

Action not linked to remote. Save first:

```bash
python cli/save_wdl_draft.py --workflow-id my-action
```

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



