# Remote Action Workflow: Load, Edit, Test, Push

This prompt guides the agent through loading existing remote actions, editing them locally, testing, and pushing new versions.

---

## 🚨 FIRST STEPS (Always Do This First)

### 1. CREATE A TODO LIST

Before starting any remote action workflow, create todos:
- [ ] Environment setup (create or verify active env)
- [ ] List and select action to edit
- [ ] Download action to workspace
- [ ] Understand requirements (from user or ask)
- [ ] Edit WDL
- [ ] Test locally then remotely
- [ ] Push new version

### 2. VERIFY OR CREATE ENVIRONMENT

Check if the correct environment is active:

```bash
python cli/workspace.py env list
```

If the user mentions a **new client** (e.g., "work on ClientX actions", "edit ClientY workflow"), you need to create a new environment for that client.

---

## 🏢 CREATING A NEW CLIENT ENVIRONMENT

When the user mentions a new client or asks to create a new environment:

### Step 1: Gather Required Information

Ask the user for:
1. **Client name** (e.g., "clientx", "clienty", "clientz")
2. **Target environment**: `staging` or `production`
3. **ADOPT_CLIENT_ID** (API client ID)
4. **ADOPT_CLIENT_SECRET** (API secret)
5. **Environment description** (what types of actions this env is for)

Example prompt to user:
```
I'll create a new environment for [CLIENT]. I need a few details:

1. **Target**: Is this for `staging` or `production`?
2. **Client ID**: What's the ADOPT_CLIENT_ID?
3. **Client Secret**: What's the ADOPT_CLIENT_SECRET?
4. **Description** (optional): Brief description of this environment
   (e.g., "Inventory management actions for ClientX")
```

### Step 2: Create the Environment

```bash
# Create the environment workspace
python cli/workspace.py env create \
  --id <client-name> \
  --name "<Client Name> - <Target>" \
  --description "<description of what actions this env handles>" \
  --target <staging|production> \
  --client <client-name> \
  --use
```

**Example:**
```bash
python cli/workspace.py env create \
  --id clientx-prod \
  --name "ClientX Production" \
  --description "Production actions for ClientX inventory and order management" \
  --target production \
  --client clientx \
  --use
```

### Step 3: Configure the `.env` File

Edit `workspaces/<env-id>/.env` with the user-provided credentials:

```bash
# File: workspaces/<env-id>/.env

# Environment: <Client Name>
# Target: <staging|production>

ADOPT_CLIENT_ID=<user-provided-client-id>
ADOPT_CLIENT_SECRET=<user-provided-secret>

# API Endpoints (based on target)
# Production:
ADOPT_API_ENDPOINT=https://connect.adopt.ai
ADOPT_ACTIONS_ENDPOINT=https://api.adopt.ai

# Staging:
# ADOPT_API_ENDPOINT=https://connect.staging.adopt.ai
# ADOPT_ACTIONS_ENDPOINT=https://api.staging.adopt.ai
```

### Step 4: Configure `adopt_profile.json` (if needed)

Edit `workspaces/<env-id>/adopt_profile.json` with client-specific settings:

```json
{
  "base_url": "",
  "application_base_url": "",
  "workflow_params": {},
  "security_params": {}
}
```

### Step 5: Verify Environment

```bash
# Verify environment was created and is active
python cli/workspace.py env list
python cli/workspace.py env show <env-id>
```

---

## 📥 LOADING REMOTE ACTIONS

### Step 1: Ensure Correct Environment is Active

```bash
# Check active environment
python cli/workspace.py env list

# Switch if needed
python cli/workspace.py env use <env-id>
```

### Step 2: List All Remote Actions

```bash
# List ALL actions including hidden sub-actions
# This fetches visible actions AND hidden sub-actions from Uber Agents
python cli/discover.py --list-all

# List by type (only visible actions)
python cli/discover.py --list-tools
python cli/discover.py --list-workflows
python cli/discover.py --list-uber-agents  # List Uber Agents (PROMPT_AND_TOOLS_AGENT)

# Search for specific action
python cli/discover.py --actions "action name or description" --mode fuzzy

# JSON output for programmatic use
python cli/discover.py --list-all --json
```

**Understanding the output:**
- 🤖 `UBER (N sub-actions)` - This is an Uber Agent with N sub-actions
- 🔗 `Sub-action of <Agent>` - This action belongs to an Uber Agent
- 👁️ `Hidden` - This action is hidden from the default list (is_visible_in_list=false)

### Step 3: Select Action to Edit

Present the list to the user and let them choose. Note the **action ID** (UUID).

### Step 4: Download Action to Workspace

Use the checkout command to download the action:

```bash
# Checkout action to current environment
python cli/checkout_wdl_version.py --workflow-id <action-id>

# Or specify environment explicitly
python cli/checkout_wdl_version.py --workflow-id <action-id> --env <env-id>
```

**If the workspace already exists**, it will be replaced with the remote version:
- The old local workspace is removed
- Fresh copy from remote is downloaded
- Latest draft (or published version if no draft) is used

### Step 5: Verify Download

```bash
# Check workspace status
python cli/status.py <action-id>

# Workspace location:
# workspaces/<env-id>/actions/<action-id>/
# or
# workspaces/<env-id>/agents/<agent-id>/actions/<action-id>/
```

---

## 🤖 LOADING UBER AGENTS

Uber Agents are actions that contain `PROMPT_AND_TOOLS_AGENT` operations with sub-actions.

### Identify Uber Agents

```bash
# List all Uber Agents in the environment
python cli/discover.py --list-uber-agents
```

### Checkout an Uber Agent with Sub-Actions

```bash
# Checkout agent with all its sub-actions
python cli/workspace.py agent checkout \
  --remote-id <uber-agent-id> \
  --env <env-id> \
  --include-subactions
```

This creates the agent workspace with all sub-actions:
```
workspaces/<env>/agents/<agent-name>/
├── widdle.json           # Agent WDL (contains PROMPT_AND_TOOLS_AGENT)
├── agent.json            # Agent metadata
├── actions/              # Sub-actions
│   ├── sub-action-1/
│   │   ├── widdle.json
│   │   └── metadata.json
│   ├── sub-action-2/
│   │   └── ...
│   └── ...
└── test_cases/
```

---

## 📦 BULK CHECKOUT: Load ALL Actions from Client

If the user requests to load **all actions** from a client:

```bash
# Checkout all actions from remote
python cli/workspace.py action checkout-all --env <env-id>

# Limit to first N actions
python cli/workspace.py action checkout-all --env <env-id> --limit 10

# Checkout only Uber Agents with their sub-actions
python cli/workspace.py action checkout-all --env <env-id> --uber-agents-only --include-subactions

# Force overwrite existing local actions
python cli/workspace.py action checkout-all --env <env-id> --force
```

**Options:**
- `--limit N`: Only checkout first N actions
- `--force`: Overwrite existing local workspaces
- `--tools-only`: Only checkout tool-type actions
- `--workflows-only`: Only checkout workflow-type actions
- `--uber-agents-only`: Only checkout Uber Agents
- `--include-subactions`: Download sub-actions for Uber Agents

---

## 🔄 MOVING ACTIONS TO AGENTS

If you need to add an existing standalone action to an Uber Agent (make it a sub-action):

```bash
# Move standalone action to become part of an agent
python cli/workspace.py agent move-action \
  --action <action-id> \
  --agent <agent-id> \
  --env <env-id>
```

**What this does:**
1. Moves the action folder from `workspaces/<env>/actions/<action-id>` to `workspaces/<env>/agents/<agent-id>/actions/<action-id>`
2. Updates the action's metadata to reference the parent agent
3. Updates the agent's `agent.json` to include the action in `sub_actions`
4. Updates the agent's `widdle.json` to add the action ID to `PROMPT_AND_TOOLS_AGENT.action_ids`

**Important requirements for sub-actions:**
1. The action must be **published** (not just a draft)
2. The action must have **tool mode enabled**: `python cli/deployment_rules.py <action-id> --enable-tool-mode`
3. The action title must match format: `^[a-zA-Z0-9_-]{1,128}$` (no spaces)

**After moving, push the updated agent:**
```bash
python cli/save_wdl_draft.py --workflow-id <agent-id>
python cli/publish_wdl_action.py --workflow-id <agent-id>
```

---

## ✏️ EDITING THE ACTION

### Step 1: Understand Requirements

If the user hasn't provided specific requirements, ask:
```
What changes do you need to make to this action?

1. **Bug fix**: What's the issue you're seeing?
2. **New feature**: What functionality needs to be added?
3. **Modification**: What behavior needs to change?

Please describe the expected outcome.
```

### Step 2: Review Current WDL

Read the current WDL to understand the action:

```bash
# View the widdle.json
cat workspaces/<env-id>/actions/<action-id>/widdle.json

# Or check status which shows path
python cli/status.py <action-id>
```

### Step 3: Edit the WDL

Make the required changes to `widdle.json`:
- Follow WDL patterns from `prompts/system/CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md`
- Check `prompts/guidelines/WDL_ISSUE_PATTERNS.md` for common issues to avoid

### Step 4: Validate Locally

```bash
# JSON syntax + WDL structure validation
python cli/test_runner.py <action-id> --local-only
```

---

## 🧪 TESTING

### Step 1: Create/Update Test Cases

Ensure test cases exist in `test_cases/` directory:

```json
// test_cases/test_1.json
{
  "prompt": "User prompt for the action",
  "workflow_params": {
    "param1": "value1"
  },
  "expected_output": {
    "contains": ["expected", "keywords"],
    "similarity": 0.7
  }
}
```

### Step 2: Test Locally First

```bash
# Validate structure
python cli/test_runner.py <action-id> --local-only
```

### Step 3: Test Remotely

```bash
# Test single case
python cli/test_runner.py <action-id> --test test_1.json

# Test all cases
python cli/test_runner.py <action-id> --all
```

### Step 4: Debug Failures

If tests fail:
1. Check traces in `traces/` directory
2. Read `prompts/system/DIAGNOSE_AND_FIX_SYSTEM_PROMPT.md`
3. Review `prompts/guidelines/WDL_ISSUE_PATTERNS.md`

### Step 5: Iterate

Repeat edit → test cycle until all tests pass.

---

## 📤 PUSHING NEW VERSION

### Step 1: Save as Draft

```bash
# Save current WDL as new draft version
python cli/save_wdl_draft.py --workflow-id <action-id> --description "Description of changes"
```

### Step 2: Verify Draft

```bash
# List versions to confirm draft was created
python cli/list_wdl_versions.py --workflow-id <action-id>
```

### Step 3: Final Remote Test

```bash
# Test the draft version remotely
python cli/test_runner.py <action-id> --all
```

### Step 4: Publish (When User Confirms)

**IMPORTANT**: Only publish when the user explicitly confirms!

```bash
# Publish the draft to make it live
python cli/publish_wdl_action.py --workflow-id <action-id>
```

### Step 5: Verify Publication

```bash
# Confirm new version is published
python cli/list_wdl_versions.py --workflow-id <action-id>
python cli/status.py <action-id>
```

---

## 📋 COMPLETE WORKFLOW CHECKLIST

```
Remote Action Workflow
│
├─ 1. Environment Setup
│   ├─ Check active environment: `workspace.py env list`
│   ├─ Create new if needed: `workspace.py env create ...`
│   ├─ Configure .env with credentials
│   └─ Switch to correct env: `workspace.py env use <env-id>`
│
├─ 2. Action Discovery
│   ├─ List actions: `discover.py --list-all`
│   ├─ Search if needed: `discover.py --actions "query"`
│   └─ User selects action to edit
│
├─ 3. Download Action
│   ├─ Checkout: `checkout_wdl_version.py --workflow-id <id>`
│   └─ Verify: `status.py <id>`
│
├─ 4. Edit
│   ├─ Get requirements from user
│   ├─ Review current WDL
│   ├─ Make changes
│   └─ Validate locally: `test_runner.py <id> --local-only`
│
├─ 5. Test
│   ├─ Create/update test cases
│   ├─ Test remotely: `test_runner.py <id> --all`
│   ├─ Debug failures if any
│   └─ Iterate until passing
│
└─ 6. Push
    ├─ Save draft: `save_wdl_draft.py --workflow-id <id>`
    ├─ Final test
    ├─ Publish (on user confirmation): `publish_wdl_action.py --workflow-id <id>`
    └─ Verify publication
```

---

## 🔄 SWITCHING BETWEEN CLIENTS

When the user wants to work on a different client:

```bash
# List all environments
python cli/workspace.py env list

# Switch to different environment
python cli/workspace.py env use <client-env-id>

# Verify switch
python cli/workspace.py env list
```

All subsequent commands will use the new environment's:
- Credentials (from `.env`)
- Profile settings (from `adopt_profile.json`)
- Local workspaces

---

## ⚠️ IMPORTANT NOTES

1. **Always verify active environment** before any remote operations
2. **Never publish without user confirmation**
3. **If workspace exists**, checkout will replace it with remote version
4. **Test locally before testing remotely** to catch issues early
5. **Environment credentials** are stored in `workspaces/<env>/.env`
6. **Each client should have its own environment** for isolation

---

## 🔗 Related Prompts

- **CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md** - Creating new workflows
- **TESTING_PROMPT.md** - Testing strategies
- **DIAGNOSE_AND_FIX_SYSTEM_PROMPT.md** - Debugging failures
- **WORKSPACE_HIERARCHY_PROMPT.md** - Workspace management

