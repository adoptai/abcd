# Uber Agent (PROMPT_AND_TOOLS_AGENT) Guide

**PURPOSE**: This prompt explains how to create and manage Uber Agents that orchestrate multiple sub-actions.

**LOAD THIS WHEN**: Creating multi-action agents, working with PROMPT_AND_TOOLS_AGENT operation, or managing sub-actions.

---

## What is an Uber Agent?

An Uber Agent orchestrates multiple sub-actions using the `PROMPT_AND_TOOLS_AGENT` operation:

- Acts as an **intelligent router** between sub-actions
- Operates in **Tool Mode** (deterministic, no free-form LLM reasoning)
- Each sub-action is a **published, standalone action**
- Uses a **system prompt** to guide tool selection

---

## Uber Agent WDL Structure

```json
[
  {
    "id": "uberAgent",
    "operation": "PROMPT_AND_TOOLS_AGENT",
    "model_string": "claude-4-5-sonnet",
    "action_ids": [
      "{sub_action_1_remote_id}",
      "{sub_action_2_remote_id}"
    ],
    "system_prompt": "You are {name}.\n\nAvailable tools:\n1. {tool-1}: {description}\n2. {tool-2}: {description}\n\nDetermine which tool to call based on user request.\nAsk for missing required inputs.\nExecute deterministically."
  },
  {
    "id": "extractAgentMessage",
    "operation": "EXTRACT",
    "input": "uberAgent",
    "field": "message"
  },
  {
    "id": "outputAgentResponse",
    "operation": "OUTPUT_TEXT",
    "format_string": "{}",
    "values": ["extractAgentMessage"],
    "raw": true
  },
  {
    "required_inputs": []
  }
]
```

---

## Creating an Uber Agent

### Step 1: Create Environment and Agent

```bash
# Create/select environment
python cli/workspace.py env create --id staging --name "Staging" --use

# Create agent
python cli/workspace.py agent create \
  --id inventory-agent \
  --name "Inventory Agent" \
  --description "Manages inventory operations"
```

### Step 2: Create Sub-Actions

```bash
# Create sub-action within agent
python cli/workspace.py action create \
  --id get-orderpoints \
  --title "get-orderpoints" \
  --agent inventory-agent
```

### Step 3: Implement Sub-Action WDL

Edit `workspaces/{env}/agents/{agent}/actions/get-orderpoints/widdle.json`:

```json
[
  {
    "id": "apiCall",
    "operation": "REST",
    "method": "GET",
    "url": "{security_params.base_url}/api/orderpoints",
    "headers": {"Cookie": "{security_params.cookie}"}
  },
  {
    "id": "extractData",
    "operation": "EXTRACT",
    "input": "apiCall",
    "field": "data"
  },
  {
    "id": "output",
    "operation": "OUTPUT_TEXT",
    "format_string": "{}",
    "values": ["extractData"],
    "raw": true
  },
  {
    "required_inputs": [
      "{\"company_id\": {\"type\": \"integer\", \"definition\": \"Company ID\"}}"
    ]
  }
]
```

### Step 4: Publish Sub-Action with Tool Mode

```bash
# Test
python cli/test_runner.py get-orderpoints

# Save and publish
python cli/save_wdl_draft.py --workflow-id get-orderpoints
python cli/publish_wdl_action.py --workflow-id get-orderpoints

# CRITICAL: Enable tool mode
python cli/deployment_rules.py get-orderpoints --enable-tool-mode
```

### Step 5: Add Sub-Action to Agent

Get the remote action ID from publish output, then:

```bash
python cli/workspace.py agent add-subaction \
  --agent inventory-agent \
  --action get-orderpoints \
  --remote-id {REMOTE_ACTION_ID} \
  --title get-orderpoints \
  --description "Fetch orderpoints from inventory system"
```

### Step 6: Update Agent System Prompt

Edit `workspaces/{env}/agents/inventory-agent/widdle.json`:

```json
{
  "system_prompt": "You are an inventory management assistant.\n\nAvailable tools:\n1. get-orderpoints: Fetches reorder points from the inventory system\n2. create-po: Creates a new purchase order\n\nBased on the user's request:\n1. Determine which tool to call\n2. Ask for any missing required inputs\n3. Execute the tool\n4. Present results clearly"
}
```

### Step 7: Test (Three-Tier Progression)

**Tier 1 — Test each subaction individually:**
```bash
python cli/test_runner.py get-orderpoints --all
python cli/test_runner.py create-po --all
# Fix ALL failures before proceeding to Tier 2
```

**Tier 2 — Test uber agent with inline subactions (no platform dependency):**
```bash
python cli/test_runner.py inventory-agent --test test_1.json --inline
# Iterate on system_prompt and subaction logic together
```

**Tier 3 — Save, publish, and test with platform action_ids:**
```bash
# Save and publish all subactions + agent
python cli/save_wdl_draft.py --agent inventory-agent --force
python cli/publish_wdl_action.py --agent inventory-agent --yes

# Enable tool mode for all subactions
python cli/deployment_rules.py get-orderpoints --enable-tool-mode
python cli/deployment_rules.py create-po --enable-tool-mode

# Final validation with real platform action_ids
python cli/test_runner.py inventory-agent --test test_1.json
```

---

## Sub-Action Requirements

### 1. Must be PUBLISHED (for Tier 3 / production)
Draft actions cannot be used as platform-side tools.
During development, use `--inline` (Tier 2) to test without publishing.

### 2. Valid Title Format
Must match: `^[a-zA-Z0-9_-]{1,128}$`

✅ Good: `get-orderpoints`, `create_po`, `check-stock`
❌ Bad: `Get Orderpoints`, `Create PO (v2)`, `my action`

### 3. Tool Mode ENABLED

```bash
python cli/deployment_rules.py {action} --enable-tool-mode
```

### 4. Required Inputs Format

```json
{
  "required_inputs": [
    "{\"param_name\": {\"type\": \"string\", \"definition\": \"Description\"}}"
  ]
}
```

---

## Checkout Existing Agent

Download an existing agent with all sub-actions:

```bash
python cli/workspace.py agent checkout \
  --remote-id abc-123-def \
  --env staging \
  --include-subactions
```

---

## Sync Agent with Remote

```bash
# Check for changes
python cli/workspace.py agent sync inventory-agent

# Pull new sub-actions
python cli/workspace.py agent sync inventory-agent --pull
```

---

## Tool Mode Commands

```bash
# Show status
python cli/deployment_rules.py my-action --show

# Enable (required for sub-actions)
python cli/deployment_rules.py my-action --enable-tool-mode

# Disable
python cli/deployment_rules.py my-action --disable-tool-mode

# Visibility
python cli/deployment_rules.py my-action --visible
python cli/deployment_rules.py my-action --hidden
```

---

## Common Issues

### "Tool not found"
- Ensure sub-action is published (not draft)
- Check action_ids in agent WDL matches remote IDs

### "Invalid tool name"
- Title must match `^[a-zA-Z0-9_-]{1,128}$`
- No spaces, parentheses, or special characters

### "Tool mode not enabled"
- Run `python cli/deployment_rules.py {action} --enable-tool-mode`

---

## Related Prompts

- **WORKSPACE_HIERARCHY_PROMPT.md** - Workspace structure and management
- **TESTING_PROMPT.md** - Via-agent testing
- **CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md** - WDL operations reference

## Templates

- **prompts/templates/uber_agent_template.json** - Uber Agent WDL template


