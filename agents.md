# ABCD - Agent CLI Instructions

This document provides comprehensive guidance for AI agents (like Cursor) working with the ABCD repository.

---

## ⛔ CRITICAL: ALWAYS USE CLI SCRIPTS

**NEVER bypass or circumvent the CLI tools.** All operations MUST be performed using the provided CLI scripts.

### ❌ FORBIDDEN Actions

- **DO NOT** directly call AdoptAI APIs bypassing the CLI scripts
- **DO NOT** manually create workspace directories without `workspace.py`
- **DO NOT** manually edit `metadata.json` files
- **DO NOT** guess API endpoints or parameters - use `discover.py`
- **DO NOT** skip testing before saving/publishing
- **DO NOT** circumvent the workspace manager or context system

### ✅ REQUIRED Workflow

1. **Discovery**: Use `cli/discover.py` to find APIs/actions
2. **Creation**: Use `cli/manage_wdl_action.py --create` or `cli/workspace.py`
3. **📚 DOCUMENTATION FIRST**: Before editing ANY operation:
   - Fetch operation docs from `https://adoptai.github.io/widdle_docs/operations/{OPERATION}_OPERATION_DESCRIPTION.md`
   - Read the FULL documentation for each operation you plan to use
   - Note default parameter values (especially `extract_all` for JQ_FILTER!)
4. **Edit WDL**: Directly edit `widdle.json` files based on documentation
5. **Compile**: Run `cli/test_runner.py --compile` after edits (MANDATORY)
6. **Testing**: Use `cli/test_runner.py` to test directly (executes local WDL via /run-wdl, no save needed)

   **For Uber Agents — Three-Tier Testing Progression:**

   | Tier | What | Command | When |
   |------|------|---------|------|
   | **Tier 1** | Test each subaction individually | `python cli/test_runner.py <subaction-id>` | After editing any subaction WDL |
   | **Tier 2** | Test uber agent with inline subactions | `python cli/test_runner.py <agent-id> --inline` | After all subactions pass Tier 1 |
   | **Tier 3** | Test uber agent with platform subactions | `python cli/test_runner.py <agent-id>` | After save+publish, final validation |

   **Always progress bottom-up**: fix subaction failures before testing the uber agent.
   Never skip to Tier 2/3 if any subaction is failing at Tier 1.

7. **Saving**: Use `cli/save_wdl_draft.py` ONLY after all tests pass
8. **Publishing**: Use `cli/publish_wdl_action.py` when approved
9. **CE Testing** (production validation via Chrome Extension):
   - One-time setup per agent: `python cli/ce_test.py configure <agent-name>`
   - Generate test cases: `python cli/ce_test.py generate <agent-name>`
   - Start Chrome (separate terminal): `python cli/ce_test.py start <agent-name>`
   - Run tests: `python cli/ce_test.py run <agent-name>`
   - Review results and iterate on WDL if issues found

### 📝 Editing WDL Files

**You SHOULD directly edit `widdle.json` files.** This is your primary task.

When editing WDL:
- **Read the documentation first**: Load `prompts/system/CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md`
- **Use roaming RAG**: Fetch operation docs from `https://adoptai.github.io/widdle_docs/operations/`
- **Follow templates**: Use patterns from `prompts/templates/`
- **Check API specs**: Read files in `apis/` folder for endpoint details
- **Compile**: Run `cli/test_runner.py --compile` after edits

### Why This Matters

The CLI scripts:
- **Load correct credentials** from the active environment's `.env`
- **Resolve configuration inheritance** (action → agent → environment)
- **Track metadata** (versions, action_id, sync status)
- **Validate WDL structure** before upload
- **Handle caching** for discovery and embeddings per-environment
- **Ensure consistency** across the workspace hierarchy

**If a CLI tool doesn't exist for what you need, ask the user first.**

---

## 🚨 FIRST STEP: Create a TODO List

**Before building any action or workflow, ALWAYS create a TODO list first.**

When the user requests an action/workflow, immediately create todos with:
1. Environment validation (check if request matches active env)
2. Requirements analysis
3. API/action discovery
4. WDL creation
5. Testing
6. Publication (if requested)

This ensures systematic progress and helps track complex multi-step tasks.

---

## 🏢 CREATING A NEW CLIENT ENVIRONMENT

When the user mentions a **new client** (e.g., "work on ClientX actions", "edit ClientY workflows") or asks to **create a new environment**:

### Step 1: Gather Information from User

Ask for:
```
I'll create a new environment for [CLIENT]. I need:

1. **Target**: `staging` or `production`?
2. **Client ID**: ADOPT_CLIENT_ID value
3. **Client Secret**: ADOPT_CLIENT_SECRET value
4. **Description** (optional): What this environment is for
```

### Step 2: Create Environment

```bash
python cli/workspace.py env create \
  --id <client-name>-<staging|prod> \
  --name "<Client Name> - <Target>" \
  --description "<description>" \
  --target <staging|production> \
  --client <client-name> \
  --use
```

### Step 3: Configure Credentials

Edit `workspaces/<env-id>/.env`:
```bash
ADOPT_CLIENT_ID=<user-provided>
ADOPT_CLIENT_SECRET=<user-provided>

# Production endpoints:
ADOPT_API_ENDPOINT=https://connect.adopt.ai
ADOPT_ACTIONS_ENDPOINT=https://api.adopt.ai

# Staging endpoints (if staging):
# ADOPT_API_ENDPOINT=https://connect.staging.adopt.ai
# ADOPT_ACTIONS_ENDPOINT=https://api.staging.adopt.ai
```

**📖 Full details: `prompts/system/REMOTE_ACTION_WORKFLOW_PROMPT.md`**

---

## 🔀 WORKFLOW DECISION: New Action vs Edit Existing

### User wants to CREATE NEW action/workflow:
→ Load **`CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md`**
```bash
python cli/discover.py --requirements requirements.md
python cli/manage_wdl_action.py --create -r requirements.md -t "Title"
```

### User wants to EDIT EXISTING remote action:
→ Load **`REMOTE_ACTION_WORKFLOW_PROMPT.md`**
```bash
python cli/discover.py --list-all
python cli/checkout_wdl_version.py --workflow-id <action-id>
# Edit, test, push
python cli/save_wdl_draft.py --workflow-id <action-id>
python cli/publish_wdl_action.py --workflow-id <action-id>
```

---

## 🔍 ENVIRONMENT VALIDATION (Required Before Creating Actions)

**Before creating any action**, validate that the request matches the active environment:

### Step 1: Check Active Environment

```bash
python cli/workspace.py env list
```

This shows all environments with their descriptions. The active one is marked with ✓.

### Step 2: Compare Request vs Environment Description

Read the active environment's `env.json` description and compare against the user's request:
- **Match**: Proceed with action creation
- **Mismatch**: Politely ask user to confirm or suggest switching environments

### Example Mismatch Scenarios

| User Request | Active Env Description | Action |
|--------------|------------------------|--------|
| "Create inventory control action" | "Marketing automation for Client X" | ⚠️ Ask user to confirm or switch env |
| "Build email campaign workflow" | "Marketing automation for Client X" | ✅ Proceed |
| "Create data sync action" | "General-purpose development" | ✅ Proceed (default env allows anything) |

### How to Respond to Mismatch

```
I notice you want to create an [inventory control] action, but the active 
environment is configured for [Marketing automation for Client X].

Current environment: **marketing-clientx** - "Marketing automation for Client X"

Would you like me to:
1. **Proceed anyway** in the current environment
2. **Switch to a different environment** (list available with `python cli/workspace.py env list`)
3. **Create a new environment** for this domain

To switch environments: `python cli/workspace.py env use <env-id>`
```

---

## 📚 Detailed Prompts (Load as Needed)

For in-depth information, load the appropriate prompt from `prompts/system/`:

| Prompt | When to Load |
|--------|--------------|
| **CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md** | Creating NEW WDL workflows from scratch |
| **REMOTE_ACTION_WORKFLOW_PROMPT.md** | Loading, editing, and pushing EXISTING remote actions |
| **WORKSPACE_HIERARCHY_PROMPT.md** | Managing workspaces, environments, config inheritance |
| **UBER_AGENT_PROMPT.md** | Creating Uber Agents with sub-actions |
| **TESTING_PROMPT.md** | Testing strategies, parallel tests, via-agent tests |
| **DIAGNOSE_AND_FIX_SYSTEM_PROMPT.md** | Debugging failures, analyzing traces |

Templates: `prompts/templates/` (uber_agent, complex_workflow, simple_tool)

---

## Table of Contents

- [Transparent CLI Behavior](#transparent-cli-behavior)
- [Quick Command Reference](#quick-command-reference)
- [Workspace Management (NEW)](#workspace-management-new)
- [Testing Commands](#testing-commands)
- [Overview](#overview)
- [Quick Decision Tree](#quick-decision-tree)
- [Functionality Categories](#functionality-categories)
- [PROMPT_AND_TOOLS_AGENT Requirements](#prompt_and_tools_agent-requirements)

---

## Transparent CLI Behavior

**Core Principle**: CLI commands are transparent - they auto-detect version, draft status, agent/standalone mode, and action_id from metadata. **Only use flags when automatic behavior doesn't work.**

### Primary Commands

| Command | Purpose |
|---------|---------|
| `python cli/test_runner.py <workflow-id>` | Test workflow (always uses allow_draft=True) |
| `python cli/save_wdl_draft.py --workflow-id <id>` | Save draft (auto-creates action if needed) |
| `python cli/publish_wdl_action.py --workflow-id <id>` | Publish workflow (requires confirmation) |
| `python cli/list_wdl_versions.py --workflow-id <id>` | List all versions |
| `python cli/checkout_wdl_version.py --workflow-id <id> -v N` | Checkout specific version |
| `python cli/status.py <workflow-id>` | Show comprehensive workspace status |
| `python cli/validate.py <workflow-id>` | Validate WDL locally |
| `python cli/reconnect.py <workflow-id> --search` | Reconnect workspace to action |

### Workflow Example

```bash
# 1. Create workflow workspace
python cli/manage_wdl_action.py --create -r requirements.md -t "My Workflow"

# 2. Check status
python cli/status.py my-workflow

# 3. Compile WDL (MANDATORY before testing)
python cli/test_runner.py my-workflow --compile

# 4. Test directly (executes local widdle.json via /run-wdl — no save needed!)
python cli/test_runner.py my-workflow

# 5. Iterate: edit widdle.json → compile → test until all tests pass

# 6. Save draft ONLY after tests pass (auto-creates action if needed)
python cli/save_wdl_draft.py --workflow-id my-workflow --description "Fixed pagination"

# 7. View versions
python cli/list_wdl_versions.py --workflow-id my-workflow

# 8. Publish when ready
python cli/publish_wdl_action.py --workflow-id my-workflow

# 9. Enable tool mode (for sub-actions)
python cli/deployment_rules.py my-workflow --enable-tool-mode
```

---

## Workspace Management

**📖 Full details: `prompts/system/WORKSPACE_HIERARCHY_PROMPT.md`**

**IMPORTANT**: All operations require an environment. The repo ships with a `default` environment that is auto-activated. Create additional environments for staging/production or different clients.

### Workspace Structure

```
workspaces/
├── .active_env                 # Points to active environment
├── default/                    # Default environment (auto-created)
│   ├── .env                    # API credentials
│   ├── adopt_profile.json      # Base URL, workflow params
│   ├── env.json                # Environment metadata
│   ├── agents/{agent}/         # Uber Agents
│   │   └── actions/{action}/   # Sub-actions
│   └── actions/{action}/       # Standalone actions
└── {other_env}/                # Additional environments
    └── ...
```

### Key Commands

```bash
# Check active environment
python cli/workspace.py env list

# Create additional environment
python cli/workspace.py env create --id production --name "Production" --target production

# Switch environment
python cli/workspace.py env use production

# Create agent in active environment
python cli/workspace.py agent create --id my-agent --name "My Agent"

# Create action in active environment
python cli/workspace.py action create --id my-action --title "My Action"

# Profile (config inheritance)
python cli/workspace.py profile show --action my-action
```

### Move Actions/Agents Between Workspaces

**⚠️ Requires explicit user confirmation - cannot be auto-run by AI agents.**

Use `move_action.py` to move actions or agents between LOCAL workspace environments.
Environment names are workspace directory names (e.g., `clienta-prod`, `clientb-staging`).

```bash
# List available workspace environments
python cli/move_action.py --list-envs

# List actions/agents in a workspace
python cli/move_action.py --list-actions --env clienta-prod
python cli/move_action.py --list-agents --env clientb-staging

# Move action within same client (staging → prod)
python cli/move_action.py my-action --from clienta-staging --to clienta-prod

# Move action between different clients
python cli/move_action.py my-action --from clienta-prod --to clientb-prod

# Copy action (keep original)
python cli/move_action.py my-action --from clienta-staging --to clienta-prod --copy

# Move entire agent with all sub-actions
python cli/move_action.py my-agent --agent --from clienta-staging --to clienta-prod
```

**After moving an agent**, sub-actions need re-registration in the new environment.
See `prompts/system/WORKSPACE_HIERARCHY_PROMPT.md` for full workflow.

### Config Inheritance

```
Action adopt_profile.json → Agent → Environment
```

---

## Testing Commands

**📖 Full details: `prompts/system/TESTING_PROMPT.md`**

### Test Runner Features

| Feature | Description |
|---------|-------------|
| **Direct WDL execution** | Default mode: executes local widdle.json via /run-wdl (no save needed) |
| **Inline agent testing** | `--inline` flag: sends subaction WDLs inline for uber agent testing (no platform dependency) |
| **Remote action testing** | `--remote` flag: tests saved remote action (requires `save_wdl_draft` first) |
| **Parallel testing** | `--parallel N` for multiple actions |
| **Batch testing** | `--workspace`, `--agent --all-subactions` |
| **Via-agent testing** | `--via-agent --subaction` |
| **Verbose output** | `--verbose` for WDL operations and traces |
| **Compilation** | `--compile` for remote WDL compilation check |

### Single Action

```bash
# Compile WDL (MANDATORY before testing)
python cli/test_runner.py my-action --compile

# Test single action (direct WDL execution — no save/draft needed)
python cli/test_runner.py my-action

# Test saved remote action (legacy mode, requires save_wdl_draft first)
python cli/test_runner.py my-action --remote

# Run all test cases
python cli/test_runner.py my-action --all

# Verbose output with traces
python cli/test_runner.py my-action --verbose
```

### Parallel Testing

```bash
python cli/test_runner.py action1 action2 action3 --parallel 3
```

### Batch Testing

```bash
# All actions in environment
python cli/test_runner.py --workspace production-env

# All sub-actions in agent
python cli/test_runner.py --agent my-agent --all-subactions
```

### Via-Agent Testing

Test sub-action through its parent agent:

```bash
python cli/test_runner.py my-agent --via-agent --subaction get-data
```

### Parallel Save Draft

```bash
# Multiple actions
python cli/save_wdl_draft.py action1 action2 action3 --parallel 3

# Agent + all changed subactions
python cli/save_wdl_draft.py --agent my-agent

# Agent + ALL subactions (even unchanged)
python cli/save_wdl_draft.py --agent my-agent --force
```

### Parallel Publish

```bash
# Multiple actions
python cli/publish_wdl_action.py action1 action2 action3 --parallel 3 --yes

# Agent + all draft subactions (publishes subactions first, then agent)
python cli/publish_wdl_action.py --agent my-agent --yes
```

---

## Security Headers & Token Management

**Remote playground profiles must NEVER contain hardcoded secrets.** All security header values must reference published token configs by name. The CLI enforces this — `playground-profile create` and `playground-profile update` will reject hardcoded values.

### How It Works

1. **Token configs** define HOW to extract a secret from a browser session (custom JS scripts, cookies, localStorage, etc.)
2. **Playground profiles** reference token configs by name in their `security_headers`
3. At runtime, the CE token manager resolves the names to fresh values extracted from the user's browser

### Workflow: Setting Up Security Headers

```bash
# 1. Create token configs (one per secret)
python cli/workspace.py token-config create \
  --name "my_api_token" \
  --domain-suffix "example.com" \
  --storage-type customScript \
  --custom-script "return document.querySelector('meta[name=csrf]').content;"

# 2. Reference token configs by name in the profile
python cli/workspace.py playground-profile update <profile-id> \
  --security-headers '{"my_header": "my_api_token"}'
```

### What Gets Rejected

```bash
# ❌ This will fail — hardcoded secret value
python cli/workspace.py playground-profile update <id> \
  --security-headers '{"api_key": "sk-abc123-real-secret"}'

# ✅ This works — references a published token config by name
python cli/workspace.py playground-profile update <id> \
  --security-headers '{"api_key": "my_api_token"}'
```

### Local vs Remote

| Context | Where secrets live | How they're managed |
|---------|-------------------|---------------------|
| **Local (CLI tests)** | `adopt_profile.json` → `security_params` | Hardcoded values OK (never leaves your machine) |
| **Remote (CE)** | Playground profile → `security_headers` | Must reference token configs (managed by token manager) |

### Token Config Storage Types

| Type | Use case | Key flag |
|------|----------|----------|
| `customScript` | Extract from page JS globals | `--custom-script "..."` |
| `cookie` | Extract from browser cookies | `--cookie-key "SESSION_ID"` |
| `localStorage` | Extract from localStorage | `--storage-key "auth_token"` |
| `sessionStorage` | Extract from sessionStorage | `--storage-key "csrf"` |
| `domElement` | Extract from DOM element | `--dom-selector "//meta[@name='token']"` |

---

## Tool Mode / Deployment Rules

**Required for sub-actions used by Uber Agents.**

```bash
python cli/deployment_rules.py my-action --show              # Show status
python cli/deployment_rules.py my-action --enable-tool-mode  # Enable
python cli/deployment_rules.py my-action --disable-tool-mode # Disable
```

### Automatic Features

1. **Auto-version detection**: Commands auto-detect which version to use from metadata
2. **Auto-draft flag**: Commands auto-determine if allow_draft is needed
3. **Auto-action creation**: `save_wdl_draft.py` creates remote action if not linked
4. **Auto-recovery**: If action_id is lost, CLI searches by title to recover
5. **Auto-agent detection**: Commands auto-detect agent vs standalone from workspace location
6. **Auto-validation**: `save_wdl_draft.py` validates WDL before upload

### When to Use Flags

**Only use explicit flags when automatic behavior fails:**

```bash
# Force standalone mode (when agent detection fails)
python cli/save_wdl_draft.py --workflow-id my-workflow --standalone

# Force specific action_id (when recovery by title fails)
python cli/publish_wdl_action.py abc123-action-id --version 5
```

---

## Overview

ABCD provides a comprehensive CLI toolkit for building and managing actions, agents, and automations on the AdoptAI platform. It supports three main types of operations:

1. **Simple Actions**: Single-API wrappers (REST → OUTPUT pattern)
2. **Complex Workflows**: Multi-step WDL workflows with multiple operations, AI integration, and data transformations
3. **Uber Agents**: Multi-action orchestrators using PROMPT_AND_TOOLS_AGENT

### Key Files

- **`cli/`**: All CLI scripts organized by functionality
- **`prompts/system/CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md`**: Comprehensive guide for creating complex WDL workflows
- **`README.md`**: User-facing documentation

---

## Quick Decision Tree

```
User Request
    │
    ├─ "Create NEW action/workflow"
    │   → Read: CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md
    │   → python cli/manage_wdl_action.py --create -r requirements.md -t "Title"
    │
    ├─ "Edit EXISTING remote action" / "Work on [client] actions"
    │   → Read: REMOTE_ACTION_WORKFLOW_PROMPT.md
    │   → python cli/discover.py --list-all
    │   → python cli/checkout_wdl_version.py --workflow-id <id>
    │
    ├─ "Create new environment" / "Work on [new client]"
    │   → Read: REMOTE_ACTION_WORKFLOW_PROMPT.md (Environment section)
    │   → python cli/workspace.py env create --id <client>-prod --target production --use
    │   → Edit workspaces/<env>/.env with credentials
    │
    ├─ "Search for APIs/actions"
    │   → python cli/discover.py --apis "query"
    │   → python cli/discover.py --actions "query"
    │   → python cli/discover.py --list-all
    │
    ├─ "Test a tool/action"
    │   → python cli/test_runner.py <workflow_id>
    │
    ├─ "Diagnose and fix issues"
    │   → Read: DIAGNOSE_AND_FIX_SYSTEM_PROMPT.md
    │
    └─ "Version management"
        → python cli/list_wdl_versions.py --workflow-id <id>
        → python cli/checkout_wdl_version.py --workflow-id <id>
```

---

## Functionality Categories

### Agent/Workspace Management

**Purpose**: Organize actions and workflows into hierarchical workspaces (environments, agents, actions)

**Tools**:
- `cli/workspace.py`: Manage environments, agents, and actions
- Used by all other tools for workspace organization

**When to Use**:
- User wants to organize actions by project/team/feature
- Need to set up multi-environment workspaces (staging, production)
- Creating Uber Agents with sub-actions

**CLI Access**:
- `python cli/workspace.py env list` - List environments
- `python cli/workspace.py agent create --id my-agent` - Create agent
- See `prompts/system/WORKSPACE_HIERARCHY_PROMPT.md` for full details

---

### Simple Actions (API Wrappers)

**Purpose**: Create single-API wrapper actions that follow REST → OUTPUT pattern

**Template**: See `prompts/templates/simple_tool_template.md` for the complete guide.

**Characteristics**:
- One REST API call
- Simple data transformation (optional)
- Single output operation
- Fast to create and test

**How to Create Simple Actions**:

1. **Create workspace**: `python cli/manage_wdl_action.py --create --template simple -t "My Action"`
2. **Edit `widdle.json`** using the template pattern (REST → EXTRACT → OUTPUT_TEXT)
3. **Test**: `python cli/test_runner.py my-action`
4. **Save**: `python cli/save_wdl_draft.py --workflow-id my-action`
5. **Publish**: `python cli/publish_wdl_action.py --workflow-id my-action`

**Template Pattern**:
```json
[
  {"required_inputs": {"param": {"type": "string", "definition": "Description"}}},
  {"id": "apiCall", "operation": "REST", "method": "GET", "url": "/api/endpoint"},
  {"id": "extractData", "operation": "EXTRACT", "input": "apiCall", "field": "data"},
  {"id": "output", "operation": "OUTPUT_TEXT", "format_string": "{}", "values": ["extractData"], "raw": true}
]
```

**API Discovery** (use `cli/discover.py`):

```bash
# Semantic search (for requirements-based discovery)
python cli/discover.py --apis "inventory management"

# Fuzzy search (for specific names)
python cli/discover.py --apis "orderpoints" --mode fuzzy

# From requirements file
python cli/discover.py --requirements requirements.md
```

**When to Use Simple Actions**:
- User needs a quick API wrapper
- Single API endpoint call
- Simple data transformation
- No complex logic or multiple steps required

---

### Complex Workflows (WDL)

**Purpose**: Create multi-step workflows with complex operations, AI integration, and data transformations

**Characteristics**:
- Multiple operations (REST, JQ_FILTER, EXTRACT, PROJECT, PROMPT, etc.)
- Complex data flow between steps
- AI operations (LLM calls, agents)
- Conditional logic
- Pagination handling
- Multiple outputs

**⚠️ IMPORTANT**: For creating complex WDL workflows, refer to **[`prompts/system/CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md`](CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md)** for comprehensive instructions.

**Tools**:

#### 1. **Discovery** (`cli/discover.py`)
- **Purpose**: Find relevant actions and APIs for new workflows
- **Features**:
  - FAISS semantic search (for requirements-based discovery)
  - Fuzzy text matching (for specific name search)
  - Hybrid mode (combines both)
  - Smart caching at environment level
  - Incremental cache updates (only embeds new items)
- **Key Options**:
  ```bash
  # Semantic search (default for requirements discovery)
  python cli/discover.py --actions "inventory management"
  python cli/discover.py --apis "authentication endpoint"
  
  # Fuzzy search (for specific names)
  python cli/discover.py --actions "get-orderpoints" --mode fuzzy
  
  # Hybrid (both semantic + fuzzy)
  python cli/discover.py --apis "orderpoints" --mode hybrid
  
  # From requirements file (uses semantic search)
  python cli/discover.py --requirements requirements.md
  
  # Tools only (filter by execution_type=TOOL)
  python cli/discover.py --actions "fetch" --tools-only
  
  # Full details and JSON output
  python cli/discover.py --apis "user auth" --details --json
  ```
- **Cache Location**: `workspaces/{env}/.cache/actions_cache.json`, `apis_cache.json`
- **Search Modes**:
  - `semantic`: FAISS vector similarity (best for requirements)
  - `fuzzy`: Text matching with ratio score (best for names)
  - `hybrid`: Both combined (default)

#### 2. **Manage WDL Workflow** (`cli/manage_wdl_action.py`)
- **Purpose**: Create and update WDL workflow workspaces
- **Features**:
  - Workspace creation with context (`--create`)
  - Update existing workspaces (`--update`)
  - Add APIs/tools to existing workflows
  - Cursor instruction generation
- **Note**: Discovery moved to `cli/discover.py`
- **Key Options**:
  ```bash
  # Create new workflow
  --create              # Create new workflow workspace
  --requirements, -r    # Path to requirements .md file
  --title, -t           # Workflow title
  --use-tool ID         # Include existing tool as building block
  --use-api ID          # Include API specification
  --standalone          # Create standalone workspace (no agent)
  --agent NAME          # Create in agent workspace
  
  # Update existing workflow
  --update              # Update existing workflow
  --workflow-id, -w     # Workflow ID to update
  
  # Add APIs/tools independently
  --workflow-id, -w     # Workflow ID
  --use-api ID          # Add API to existing workflow
  --use-tool ID         # Add tool to existing workflow
  ```

#### 2. **Test WDL Workflow** (`cli/test_runner.py`)
- **Purpose**: Compile and test WDL workflows
- **Features**:
  - **Step 1**: JSON syntax validation (catches parsing errors)
  - **Step 2**: Remote WDL compilation (`--compile`) — MANDATORY before testing
  - **Direct WDL execution** (default): Sends local `widdle.json` directly to /run-wdl for testing — **no save/draft needed**
  - **Remote action testing** (`--remote`): Tests saved remote action (requires `save_wdl_draft` first)
  - **Test case management**: Run specific test case or all test cases
  - **Output validation**: Validates outputs against `expected_output` in test cases
  - Fix instruction generation on failure
- **Menu Option**: 9
- **Key Options**:
  ```bash
  --compile             # Compile WDL via remote compiler (MANDATORY before testing)
  --remote              # Test saved remote action (requires save_wdl_draft first)
  --test FILE           # Use specific test case (filename only, not path)
                        # Examples: --test test_1.json, --test test_2.json
                        # NOT: --test test_cases/test_1.json (wrong!)
  --all                  # Run all test cases in test_cases/ directory
  --agent NAME          # Specify agent name
  ```
- **Default mode**: Executes local `widdle.json` directly via /run-wdl — no remote action or draft needed
- **Save draft only after tests pass**: Use `save_wdl_draft.py` only when testing is complete
- **Test Cases**: JSON files in `test_cases/` directory with `prompt`, `workflow_params`, and `expected_output`
  - Validation types: `similarity` (most common, LLM-judged), `exact` (code-based), `contains` (code-based)
  - For `similarity`: Agent (LLM) reviews actual vs expected output and judges if they're similar enough with valid data

#### 3. **Save WDL Draft** (`cli/save_wdl_draft.py`)
- **Purpose**: Save workflow as draft without publishing
- **Features**:
  - **Automatically creates remote action** if it doesn't exist
  - **Automatically publishes WDL** to remote if not already published
  - Saves as draft (creates version)
  - Does NOT approve - action stays in draft state
  - **Version descriptions**: Use `--description` parameter (defaults to "WDL workflow update")
  - **No interactive prompts**: All parameters via command line
  - **Local WDL storage**: Automatically saves WDL to `versions/v{version_number}_widdle.json`
  - Updates `metadata.json` with version info in versions map
  - **Multiple actions**: Pass multiple IDs for parallel save
  - **Agent support**: `--agent` saves agent + all changed subactions
- **Usage**: Persist changes without making workflow live
- **Menu Option**: 10
- **Examples**:
  ```bash
  # Single action
  python cli/save_wdl_draft.py my-action
  python cli/save_wdl_draft.py --workflow-id my-action --description "Fixed bug"
  
  # Multiple actions in parallel
  python cli/save_wdl_draft.py action1 action2 action3 --parallel 3
  
  # Agent + all changed subactions
  python cli/save_wdl_draft.py --agent my-agent
  
  # Agent + ALL subactions (even unchanged)
  python cli/save_wdl_draft.py --agent my-agent --force
  ```
- **Local Storage**: Each saved draft stores WDL in `versions/v{version_number}_widdle.json` for future checkout

#### 4. **Publish WDL Workflow** (`cli/publish_wdl_action.py`)
- **Purpose**: Approve and publish workflow (makes it live)
- **Features**:
  - **Version descriptions**: Supports `--description` parameter
  - **Local WDL storage**: Saves WDL to `versions/v{version_number}_widdle.json` when publishing
  - Updates version status to published in metadata.json
  - Shows existing description when available
  - **Multiple actions**: Pass multiple IDs for parallel publish
  - **Agent support**: `--agent` publishes subactions first, then agent
- **⚠️ Requires explicit user confirmation** (use `--yes` to skip)
- **Menu Option**: 11
- **Examples**:
  ```bash
  # Single action
  python cli/publish_wdl_action.py my-action
  python cli/publish_wdl_action.py --workflow-id my-action --description "Production release"
  
  # Multiple actions in parallel
  python cli/publish_wdl_action.py action1 action2 action3 --parallel 3 --yes
  
  # Agent + all draft subactions (publishes subactions first, then agent)
  python cli/publish_wdl_action.py --agent my-agent --yes
  ```
- **Local Storage**: Tries to save WDL locally (from draft copy or workspace) for future checkout

#### 5. **List WDL Versions** (`cli/list_wdl_versions.py`)
- **Purpose**: View version history for a workflow
- **Features**: 
  - Shows all versions, status, timestamps
  - **Displays descriptions prominently**
  - **Syncs all versions from API to metadata.json**
  - Shows which version is current and checked-out
- **Menu Option**: 12
- **Example**:
  ```bash
  python cli/list_wdl_versions.py {action_id} --workflow-id {workflow_id} --standalone
  ```

#### 6. **Checkout WDL Version** (`cli/checkout_wdl_version.py`)
- **Purpose**: Checkout specific version for iteration
- **Features**:
  - **Local-first approach**: Checks `versions/v{version_number}_widdle.json` first (no API call if available)
  - **Saves WDL locally**: Stores checked-out WDL to `versions/v{version_number}_widdle.json` for future use
  - **Tracks publish status** (`is_published` flag)
  - **Retrieves and stores version descriptions** from API
  - Updates both `current_version.txt` and `metadata.json` versions map
  - Displays version status and description in output
- **Usage**: Work on historical versions, compare changes
- **Menu Option**: 13
- **Example**:
  ```bash
  python cli/checkout_wdl_version.py {action_id} --version 4 --workflow-id {workflow_id} --standalone
  ```
- **Local Storage**: All checked-out versions saved to `versions/` folder for easy comparison and reuse

**When to Use WDL Workflows**:
- User needs multi-step operations
- Multiple API calls required
- Complex data transformations
- AI/LLM integration needed
- Conditional logic or branching
- User explicitly requests "complex workflow" or "multi-step"

**👉 For detailed WDL workflow creation instructions, see: [`prompts/system/CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md`](CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md)**

---

## CLI Tools Reference

### Main Entry Point

**CLI scripts**: All commands are in the `cli/` directory
```bash
python cli/<script>.py --help
```

**Menu Structure**:
```
--- Agent Management ---
1. Create agent
2. Delete agent

--- Simple Actions (API Wrappers) ---
3. List all actions
4. Search tools
5. Create tools
6. Change APIs/tools of an agent
7. Test tools (WDL structure)

--- Complex Workflows (WDL) ---
8. Create WDL workflow
9. Test WDL workflow
10. Save WDL draft
11. Publish WDL workflow
12. List WDL versions
13. Checkout WDL version

--- Diagnostics & Fixes ---
15. Diagnose APIs & Tools (scan for issues)
16. Fix & Test Pipeline
17. Fetch HTTP network logs
18. Rollback changes

--- Other ---
19. Publish agent to upstream
20. Exit
```

### Direct CLI Scripts

All scripts can be run directly without the menu:

#### Agent Management
```bash
python tool_agents.py                    # Interactive agent management
```

#### Discovery Commands (`cli/discover.py`)
```bash
# List all actions/tools/workflows
python cli/discover.py --list-tools       # List tools (execution_type=TOOL)
python cli/discover.py --list-all         # List all actions (no filter, includes hidden)
python cli/discover.py --list-workflows   # List workflows (execution_type=WORKFLOW)
python cli/discover.py --list-apis        # List available APIs
python cli/discover.py --list-uber-agents # List Uber Agents (PROMPT_AND_TOOLS_AGENT)

# Semantic search
python cli/discover.py --actions "inventory management"  # Search actions
python cli/discover.py --apis "user auth"                # Search APIs

# Fuzzy search (for specific names)
python cli/discover.py --actions "get-orderpoints" --mode fuzzy

# From requirements file
python cli/discover.py --requirements requirements.md

# Verbose debugging
python cli/discover.py --list-tools --verbose

# JSON output
python cli/discover.py --list-tools --json
```

#### Bulk Checkout Commands (`cli/workspace.py action checkout-all`)
```bash
# Checkout all actions from remote
python cli/workspace.py action checkout-all --env my-env

# Limit to first N actions
python cli/workspace.py action checkout-all --env my-env --limit 10

# Checkout only Uber Agents with sub-actions
python cli/workspace.py action checkout-all --env my-env --uber-agents-only --include-subactions

# Force overwrite existing
python cli/workspace.py action checkout-all --env my-env --force
```

#### Simple Actions
```bash
python cli/manage_wdl_action.py --create --template simple -t "Title"  # Create action
python cli/test_runner.py my-action        # Test action
python cli/save_wdl_draft.py --workflow-id my-action   # Save draft
python cli/publish_wdl_action.py --workflow-id my-action  # Publish
```

#### Complex Workflows (WDL)
```bash
python cli/manage_wdl_action.py [OPTIONS]    # Create/update WDL workflow
python cli/test_runner.py [OPTIONS]           # Test WDL workflow
python cli/save_wdl_draft.py [OPTIONS]        # Save draft
python cli/publish_wdl_action.py [OPTIONS]    # Publish workflow
python cli/list_wdl_versions.py [OPTIONS]    # List versions
python cli/checkout_wdl_version.py [OPTIONS] # Checkout version
```

#### Diagnostics & Fixes
```bash
python cli/diagnose_and_fix.py [OPTIONS]     # Main diagnostic workflow
python cli/fetch_http_logs.py [OPTIONS]      # Fetch network logs
python cli/diagnose_apis.py [OPTIONS]        # Diagnose API issues
python cli/inspect_tool_wdl.py [OPTIONS]     # Inspect tool WDL issues
python cli/fix_api_path.py [OPTIONS]         # Fix API paths
python cli/patch_tool_wdl.py [OPTIONS]       # Patch tool WDLs
python cli/fix_and_test.py [OPTIONS]         # Fix → test → eval pipeline
python cli/rollback_changes.py [OPTIONS]     # Rollback changes
python cli/generate_test_cases.py [OPTIONS]  # Generate test cases
```

**For help on any script**:
```bash
python cli/[script_name].py --help
```

---

## Diagnostics & Fixes Toolkit

The Diagnostic Toolkit provides AI agents with tools to diagnose and fix issues in APIs and tool WDLs.

**🔒 Environment Integration**: All diagnostic scripts are integrated with the hierarchical workspace manager:
- Scripts automatically use the **active environment's** credentials
- Cache files are stored **per-environment** in `workspaces/{env}/.cache/`
- Each script displays the active environment at startup

This ensures cached data from one client environment doesn't contaminate diagnostics for another.

### Core Diagnostic Workflow

```bash
# 1. Verify you're in the correct environment
python cli/workspace.py env list

# 2. Run comprehensive scan (uses active environment)
python cli/diagnose_and_fix.py --scan --format llm --output diagnostics/report.json
# Output: "📁 Environment: my-client-staging"
# Output: "📂 Cache: workspaces/my-client-staging/.cache"

# 3. Review issues and create fixes
# (AI agent analyzes report and generates fixes.json)

# 4. Apply fixes with testing
python cli/diagnose_and_fix.py --apply-fixes fixes.json --test-after-fix

# 5. Rollback if needed
python cli/rollback_changes.py --file diagnostics/rollback_xxx.json
```

### Issue Types Detected

| Issue Type | Description | Severity |
|------------|-------------|----------|
| `TRAILING_SLASH_MISMATCH` | API path differs from HTTP log URL in trailing slash | HIGH |
| `MISSING_WORKFLOW_ARGUMENTS` | URL uses `{param}` instead of `{workflow_arguments.param}` | HIGH |
| `MISSING_REQUIRED_INPUTS` | Parameter referenced but not declared in required_inputs | HIGH |
| `MISSING_QUERY_PARAMETERS` | HTTP logs show query params not in WDL | MEDIUM |
| `INVALID_WDL_STRUCTURE` | WDL has structural issues (missing id, operation, etc.) | HIGH |
| `METHOD_MISMATCH` | HTTP method differs between WDL and logs | CRITICAL |

### Diagnostic Scripts

All scripts automatically use the active environment's credentials and per-environment cache.

| Script | Purpose | Per-Env Cache |
|--------|---------|---------------|
| `diagnose_and_fix.py` | Main workflow - scan, diagnose, fix | ✅ |
| `fetch_http_logs.py` | Fetch and cache network logs | ✅ |
| `diagnose_apis.py` | Scan APIs for issues | ✅ |
| `inspect_tool_wdl.py` | Inspect tool WDLs for issues | ✅ |
| `fix_api_path.py` | Fix API paths (trailing slashes) | ✅ |
| `patch_tool_wdl.py` | Patch tool WDLs | - |
| `fix_and_test.py` | Apply fix and run tests | - |
| `rollback_changes.py` | Rollback applied changes | - |
| `generate_test_cases.py` | Generate test cases for tools | - |

### Fix File Format

When generating fixes, use this structure:

```json
{
  "fixes": [
    {
      "issue_id": "issue-abc123",
      "tool_id": "tool-xyz",
      "tool_title": "Get Organization Details",
      "action": "update_wdl",
      "changes_summary": "Fixed trailing slash and missing workflow_arguments prefix",
      "new_wdl": [
        {
          "id": "metadata",
          "required_inputs": {
            "orgId": {
              "type": "string",
              "definition": "The organization ID"
            }
          }
        },
        {
          "id": "get_org",
          "operation": "REST",
          "method": "GET",
          "url": "/api/v1/organizations/{workflow_arguments.orgId}/",
          "canonical_api_endpoint": "/api/v1/organizations/{orgId}/"
        }
      ],
      "test_prompts": [
        "Get details for organization acme-corp"
      ]
    }
  ]
}
```

### Agent Prompts for Diagnostics

See these files for AI agent guidance:
- `prompts/system/DIAGNOSE_AND_FIX_SYSTEM_PROMPT.md` - System prompt for diagnostic agents
- `prompts/guidelines/WDL_ISSUE_PATTERNS.md` - Common issue patterns and fixes

---

## When to Use Each Tool

### Scenario-Based Guide

#### "I need to wrap a single API endpoint"
→ **Use Simple Actions** (template-based approach)
- See `prompts/templates/simple_tool_template.md`
- Quick setup
- Single REST operation
- Simple output

#### "I need a complex workflow with multiple steps"
→ **Use WDL Workflows** (`cli/manage_wdl_action.py`)
- **👉 See [`prompts/system/CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md`](CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md)**
- Multiple operations
- Complex data flow
- AI integration

#### "I want to find existing actions/APIs"
→ **Use Discovery** (`cli/discover.py --actions` or `--apis`)
- Semantic search
- Natural language queries
- Tool discovery

#### "I need to test my workflow"
→ **Use Test** (`cli/test_runner.py`)
- Compile first (`--compile`) — MANDATORY before testing
- Direct WDL execution (default, no save needed) or remote execution (`--remote`)
- Trace capture
- Run all test cases (`--all`) or specific test (`--test test_N.json`)
  - **IMPORTANT**: Use filename only (e.g., `test_1.json`), not path (`test_cases/test_1.json`)
- Output validation against `expected_output` in test cases

**When to use which Testing approach**:
| Scenario | Use Test | Use Evaluation |
|----------|----------|----------------|
| Quick validation during dev | ✅ | |
| Single prompt check | ✅ | |
| Before publishing major version | | ✅ |
| Regression testing | | ✅ |
| Validating fixes across multiple cases | | ✅ |
| Need detailed metrics | | ✅ |

#### "I want to iterate on a previous version"
→ **Use Version Management** (`list_wdl_versions.py` + `checkout_wdl_version.py`)
- View history with descriptions
- Checkout specific version (tracks publish status)
- Test draft versions directly
- Iterate safely with version descriptions

#### "I need to organize tools by project"
→ **Use Agent Management** (`tool_agents.py`)
- Create agents
- Group related tools
- Workspace organization

---

## Integration Points

### Shared Infrastructure

All tools share common infrastructure:

1. **Authentication** (`get_bearer.py`)
   - Bearer token management
   - Session caching
   - Used by all API calls

2. **API Client** (`cli/wdl_common/api_client.py`)
   - Consolidated AdoptAI API client
   - CRUD operations
   - Testing and versioning
   - Used by WDL workflow tools

3. **Discovery** (`cli/wdl_common/discovery.py` / `cli/discover.py`)
   - Unified action and API discovery
   - FAISS semantic search + fuzzy text matching
   - Smart caching at environment workspace level
   - Used for finding relevant actions/APIs for new workflows

4. **Workspace Management** (`cli/wdl_common/workspace_manager.py`)
   - Agent integration
   - Standalone mode support
   - Used by WDL workflow tools

5. **WDL Documentation** (`cli/wdl_common/wdl_documentation.py`)
   - Roaming RAG support
   - Documentation path provider
   - Used for WDL generation

### Workspace Structure

```
workspaces/{env}/agents/
└── {agent_name}/
    ├── actions/            # Simple actions
    │   └── {action_id}.json
    ├── workflows/          # Complex workflows
    │   └── {workflow_id}/
    │       ├── widdle.json
    │       ├── requirements.md
    │       ├── apis/        # API specifications
    │       │   ├── manifest.json
    │       │   └── {api_id}.json
    │       ├── tools/       # Tool definitions (for reference)
    │       │   ├── manifest.json
    │       │   └── {tool_id}.json
    │       ├── tool_context.md  # Markdown-formatted tool WDLs
    │       ├── test_cases/
    │       └── traces/
    └── agent.json          # Agent metadata
```

---

## Key Differences: Simple Actions vs WDL Workflows

| Feature | Simple Actions | WDL Workflows |
|---------|-------------|---------------|
| **Complexity** | Single API call | Multiple operations |
| **Operations** | REST → OUTPUT | REST, JQ_FILTER, EXTRACT, PROJECT, PROMPT, CONDITION, etc. |
| **Data Flow** | Simple | Complex with transformations |
| **AI Integration** | No | Yes (PROMPT, PROMPT_AND_TOOLS_AGENT) |
| **Control Flow** | Linear | Conditional branching |
| **Creation Time** | Minutes | Hours (with iteration) |
| **Use Case** | API wrappers | Complex business logic, agents |

---

## Agent Workflow Recommendations

### For Simple Action Creation

1. **Search** for existing APIs: `discover.py --apis "query"`
2. **Create** action workspace: `manage_wdl_action.py --create --template simple`
3. **Edit** `widdle.json` using simple template pattern
4. **Test**: `test_runner.py my-action`
5. **Save/Publish**: `save_wdl_draft.py` then `publish_wdl_action.py`

### For Uber Agent Development

1. **Read [`prompts/system/UBER_AGENT_PROMPT.md`](UBER_AGENT_PROMPT.md)**
2. **Create agent and subactions**: `python cli/workspace.py agent create ...`
3. **Implement subaction WDLs**: Edit each `actions/{subaction}/widdle.json`
4. **Tier 1 — Test subactions individually**:
   ```bash
   python cli/test_runner.py <subaction> --all
   ```
   Fix ALL failures before proceeding.
5. **Implement uber agent WDL**: Edit agent-level `widdle.json` with system prompt and `action_ids`
6. **Tier 2 — Test uber agent inline**:
   ```bash
   python cli/test_runner.py <agent> --test test_1.json --inline
   ```
   Iterate on system prompt and subaction logic.
7. **Save and publish**: `python cli/save_wdl_draft.py --agent <agent> --force`
8. **Tier 3 — Test uber agent platform-side**:
   ```bash
   python cli/test_runner.py <agent> --test test_1.json
   ```
9. **Publish**: `python cli/publish_wdl_action.py --agent <agent> --yes`

### For Complex WDL Workflow Creation

1. **👉 Read [`prompts/system/CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md`](CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md)**
2. **Discover** tools/APIs (`cli/discover.py --requirements requirements.md`)
3. **Create** workspace (`cli/manage_wdl_action.py --create`)
4. **Update** workspace with APIs/tools (`cli/manage_wdl_action.py --update --workflow-id <id>` or use `--workflow-id` with `--use-api`/`--use-tool`)
5. **Generate** WDL (Cursor-led, following roaming instructions)
6. **Generate 3 test cases** BEFORE testing:
   - Create `test_1.json`, `test_2.json`, `test_3.json` in `test_cases/` directory
   - Test 1: Basic/common use case
   - Test 2: Different input scenario or parameter combination
   - Test 3: Edge case or alternative scenario
   - Each should include `prompt`, `workflow_params`, and `expected_output` with `similarity` validation
7. **Compile** (`cli/test_runner.py workflow-id --compile`) — **MANDATORY**
   - **Step 1**: JSON syntax validation (catches parsing errors early)
   - **Step 2**: Remote WDL compilation (catches structural and logical errors)
8. **Test directly** (`cli/test_runner.py workflow-id --all`) - Executes local WDL via /run-wdl, no save needed
   - Or test specific case: `python cli/test_runner.py workflow-id --test test_2.json`
   - **Note**: Use filename only (e.g., `test_2.json`), not path (`test_cases/test_2.json`)
9. **Iterate** based on test results (edit widdle.json → compile → test)
10. **Save** draft ONLY after all tests pass (`cli/save_wdl_draft.py`) - Auto-creates remote action if needed
11. **Publish** when user confirms (`cli/publish_wdl_action.py`)

---

## Important Notes for Agents

1. **Always ask for confirmation before publishing** - Publishing makes workflows live
2. **Compile first** - Always run `--compile` before testing
   - **Step 1**: JSON syntax validation catches parsing errors (invalid JSON, malformed strings, etc.)
   - **Step 2**: Remote WDL compilation catches structural and logical errors (missing fields, invalid references, etc.)
   - Fix compilation errors before proceeding to testing
3. **Test directly** - Default test mode executes local `widdle.json` via /run-wdl — no save/draft needed
4. **Save draft only after tests pass** - Use `save_wdl_draft.py` only when all tests pass and output is verified
5. **Automatic remote action creation** - `save_wdl_draft.py` automatically creates remote action and publishes WDL if needed
6. **Remote testing** - Use `--remote` flag to test the saved remote action (requires `save_wdl_draft` first)
7. **Version management** - Every draft creates a version with descriptions, users can checkout and test any version
8. **Version descriptions** - All versions can have descriptions stored locally and synced from API
9. **Roaming RAG** - For WDL generation, fetch the index from `https://adoptai.github.io/widdle_docs/operations/index.md`, then fetch specific operation docs as needed
10. **Tool discovery** - Use semantic search to find relevant building blocks
11. **Agent organization** - Use agents to group related tools/workflows

---

## Quick Reference: Common Commands

```bash
# Simple action creation (template-based)
python cli/manage_wdl_action.py --create --template simple -t "My Action"

# Search for APIs/actions (use discover.py)
python cli/discover.py --apis "query"
python cli/discover.py --actions "query"

# Create WDL workflow
python cli/manage_wdl_action.py --create -r requirements.md -t "Title" --agent my-agent

# Update existing workflow (add APIs/tools)
python cli/manage_wdl_action.py --update --workflow-id abc123 --use-api api-1 --use-api api-2

# Add APIs/tools independently (no --create/--update needed)
python cli/manage_wdl_action.py --workflow-id abc123 --use-api api-1 --use-tool tool-1

# Test WDL workflow
python cli/test_runner.py workflow-id --compile         # Compile WDL (MANDATORY before testing)
python cli/test_runner.py workflow-id                   # Direct WDL execution (no save needed)
python cli/test_runner.py workflow-id --remote          # Test saved remote action (requires save_wdl_draft)
python cli/test_runner.py action1 action2 --parallel 2  # Parallel testing
python cli/test_runner.py workflow-id --all             # Run all test cases
python cli/test_runner.py workflow-id --test test_2.json  # Run specific test case
python cli/test_runner.py workflow-id --verbose         # Verbose with traces
python cli/test_runner.py agent-id --inline             # Uber agent with inline subactions
python cli/test_runner.py agent-id --inline sub1,sub2   # Inline specific subactions only
python cli/test_runner.py agent-id --multi-turn test_1.json test_2.json --inline  # Compose tests into multi-turn conversation
# Note: Use --test test_2.json, NOT --test test_cases/test_2.json

# Version management
python cli/list_wdl_versions.py {action_id} --workflow-id workflow-id --standalone
python cli/checkout_wdl_version.py {action_id} --version 3 --workflow-id workflow-id --standalone

# Save draft ONLY after tests pass (auto-creates remote action if needed)
python cli/save_wdl_draft.py --workflow-id workflow-id --description "Fixed bug" --standalone

# Test saved remote action (legacy mode)
python cli/test_runner.py workflow-id --remote

# Publish (requires confirmation)
python cli/publish_wdl_action.py workflow-id
```

---

## Getting Help

- **User Documentation**: See `README.md`
- **WDL Workflow Guide**: See [`prompts/system/CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md`](CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md)
- **CLI Help**: `python cli/[script_name].py --help`

---

## Summary

- **Simple Actions**: Use for single-API wrappers → See `prompts/templates/simple_tool_template.md`
- **Complex Workflows**: Use for multi-step operations → **See [`prompts/system/CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md`](CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md)**
- **Uber Agents**: Use for multi-action orchestrators → **See [`prompts/system/UBER_AGENT_PROMPT.md`](UBER_AGENT_PROMPT.md)**
- **Discovery**: Use `--search-apis` / `--search` / `--auto-discover` options
- **Testing**: Always compile first (`--compile`), then test directly (`--all`) — no save needed
- **Saving**: Save draft only after all tests pass
- **Publishing**: Only when user explicitly confirms

**Workflow Order**:
1. Create/Generate WDL
2. Compile (`test_runner.py --compile`) — **MANDATORY**
3. Test directly (`test_runner.py --all`) — executes local WDL via /run-wdl, no save needed
4. Iterate (edit → compile → test) until all tests pass
5. Save draft (`save_wdl_draft.py`) — only after tests pass
6. Publish (`publish_wdl_action.py`)

For detailed WDL workflow creation instructions, **always refer to [`prompts/system/CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md`](CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md)**.

---

## 🔧 Implementation Guide (For Developers)

This section is for developers creating new CLI scripts or features.

### Standard Pattern: Using Context Module

All CLI scripts should use the context module for accessing workspace resources:

```python
from cli.wdl_common.context import (
    get_context,      # Get action context (loads env, resolves profile)
    get_client,       # Get API client (uses active env credentials)
    get_discovery,    # Get discovery (uses active env cache)
    get_manager,      # Get workspace manager singleton
    ensure_env,       # Ensure env is loaded, get its name
)
```

### Pattern 1: Working with an Action

```python
from cli.wdl_common.context import get_context, get_client

def process_action(action_id: str) -> int:
    # Get context (loads env, resolves profile automatically)
    ctx = get_context(action_id)
    if not ctx:
        print(f"❌ Action not found in active environment: {action_id}")
        return 1
    
    print(f"📁 Environment: {ctx.env_name}")
    print(f"📁 Workspace: {ctx.path}")
    
    # Access resolved profile
    base_url = ctx.resolved_profile.get("base_url")
    security_params = ctx.resolved_profile.get("security_params", {})
    
    # Get API client (credentials already loaded)
    client = get_client()
    
    # Do work...
    return 0
```

### Pattern 2: Script That Just Needs API Access

```python
from cli.wdl_common.context import get_client, ensure_env

def list_remote_actions() -> int:
    # Ensure env is loaded first
    env = ensure_env()
    print(f"📁 Using environment: {env}")
    
    # Get client
    client = get_client()
    
    success, actions, msg = client.list_tools()
    # ...
    return 0
```

### Pattern 3: Script Using Discovery

```python
from cli.wdl_common.context import get_discovery, ensure_env

def search_apis(query: str) -> int:
    env = ensure_env()
    print(f"📁 Searching in environment: {env}")
    
    # Discovery uses per-environment cache
    discovery = get_discovery()
    
    results = discovery.search_apis(query)
    # ...
    return 0
```

### ❌ DO NOT Do This

```python
# ❌ WRONG - Direct instantiation (no env credentials!)
from cli.wdl_common.discovery import Discovery
discovery = Discovery()

# ❌ WRONG - Direct instantiation (no env credentials!)
from cli.wdl_common.api_client import AdoptAPIClient
client = AdoptAPIClient()

# ❌ WRONG - Manual env loading (use context functions)
load_dotenv("workspaces/staging/.env")

# ❌ WRONG - Adding --env parameter to scripts
# Scripts should use active environment, not accept --env
parser.add_argument("--env", help="Environment")  # DON'T DO THIS
```

### ✅ DO This Instead

```python
# ✅ CORRECT - Use factory functions from context
from cli.wdl_common.context import get_discovery, get_client

discovery = get_discovery()  # Uses active env automatically
client = get_client()        # Uses active env automatically

# ✅ CORRECT - Switch env before running script
# python cli/workspace.py env use staging
# python cli/discover.py  # Now uses staging
```

### When --env IS Appropriate

Only these cross-environment operations should accept --env:
- `workspace.py env use` - Switches active environment
- `workspace.py env create` - Creates new environment
- `move_action.py` - Moves between environments (`--from`, `--to`)
- `workspace.py action checkout-all` - Bulk checkout to specific env

### Environment Files Structure

```
workspaces/
├── .active_env              # Contains: "staging"
├── staging/
│   ├── .env                 # ADOPT_CLIENT_ID, ADOPT_CLIENT_SECRET
│   ├── adopt_profile.json   # base_url, security_params
│   ├── env.json             # Environment metadata
│   ├── .cache/              # Per-environment embedding cache
│   │   ├── actions_cache.json
│   │   └── apis_cache.json
│   ├── agents/
│   └── actions/
└── production/
    └── ...
```

### ActionContext Properties

The `ActionContext` dataclass provides convenient properties:

```python
ctx = get_context("my-action")
ctx.action_id       # "my-action"
ctx.path            # Path to action directory
ctx.env_name        # "staging"
ctx.agent_name      # "my-agent" or None
ctx.metadata        # Dict from metadata.json
ctx.resolved_profile  # Merged adopt_profile.json

# Convenience paths
ctx.wdl_path        # path / "widdle.json"
ctx.metadata_path   # path / "metadata.json"
ctx.apis_dir        # path / "apis"
ctx.tools_dir       # path / "tools"
ctx.test_cases_dir  # path / "test_cases"
ctx.traces_dir      # path / "traces"
```

---

## PROMPT_AND_TOOLS_AGENT Requirements

When using actions as sub-tools in a `PROMPT_AND_TOOLS_AGENT` orchestrator, follow these requirements:

### Testing Uber Agents (Three-Tier Progression)

Uber agents must be tested bottom-up, in three tiers:

#### Tier 1: Individual Subaction Testing (Direct WDL)
Test each subaction independently via `/run-wdl`. This validates the subaction's
WDL logic, API calls, JQ filters, and output formatting in isolation.

```bash
python cli/test_runner.py search-products --test test_1.json
python cli/test_runner.py add-product-to-quote --test test_1.json
python cli/test_runner.py get-bundle-options --test test_1.json
```

Only proceed to Tier 2 when ALL subactions pass.

#### Tier 2: Inline Uber Agent Testing
Test the uber agent with subaction WDLs sent inline in the payload. This validates
the agent's system prompt, tool selection logic, and multi-tool orchestration —
without needing anything saved to the platform.

```bash
python cli/test_runner.py my-agent --test test_1.json --inline

python cli/test_runner.py my-agent --test test_1.json --inline search-products,get-bundle-options
```

This lets you iterate rapidly on the system prompt and subaction logic together.
Fix any orchestration issues before proceeding to Tier 3.

#### Tier 3: Platform-Side Uber Agent Testing
After all subactions are saved, published, and have tool mode enabled — test the
uber agent with real platform `action_ids`. This is the final validation that
the full production setup works.

```bash
python cli/save_wdl_draft.py --agent my-agent --force
python cli/publish_wdl_action.py --agent my-agent --yes

python cli/test_runner.py my-agent --test test_1.json
```

### Sub-Action Requirements

1. **Must be PUBLISHED (for Tier 3 / production)** - Draft actions cannot be used as platform-side sub-tools. During development, use `--inline` (Tier 2) to test without publishing.
2. **Title format** - Must match pattern: `^[a-zA-Z0-9_-]{1,128}$`
   - ✅ Good: `get-orderpoints`, `inventory_check`, `create-po`
   - ❌ Bad: `Get Orderpoints`, `Check (Inventory)`, `create po`
3. **required_inputs format** - Must be a list of JSON strings:
   ```json
   "required_inputs": [
     "{\"company_id\": {\"type\": \"integer\", \"description\": \"Company ID\"}}"
   ]
   ```
   NOT a dict like `"required_inputs": {"company_id": {...}}`

### Architecture Pattern

Build atomic tools first, then compose with orchestrator:

```
Individual Tools (publish each separately):
├── get-orderpoints           # Simple REST wrapper
├── create-po                 # Simple REST wrapper
├── check-exceptions          # Simple REST wrapper
└── ...

Orchestrator (uses tools above):
└── inventory-orchestrator    # PROMPT_AND_TOOLS_AGENT
    └── Uses: get-orderpoints, create-po, ...
```

⚠️ **Anti-pattern**: Don't build a single 30+ operation monolithic WDL.
   Instead, create small focused tools and compose them with an orchestrator.

### Validation

Use the validator to check for orchestrator compatibility:

```bash
python cli/validate.py my-workflow --orchestrator
python cli/validate.py my-workflow --orchestrator --auto-fix
```

---

## Test-First Workflow

### Before Testing

1. **Generate 3 test cases** in `test_cases/`:
   - `test_1.json` - Basic/common use case
   - `test_2.json` - Different input scenario
   - `test_3.json` - Edge case

2. Each test case must include either **single-turn** or **multi-turn** format:

   **Single-turn format** (one prompt, one response):
   ```json
   {
     "prompt": "User input",
     "workflow_params": {},
     "expected_output": {
       "description": "What the output should contain",
       "validation": "similarity",
       "key_fields": ["field1", "field2"]
     }
   }
   ```

   **Multi-turn format** (conversation with multiple exchanges):
   ```json
   {
     "turns": [
       {
         "prompt": "I want to fly from SP to RJ",
         "expected_output": {
           "description": "Agent should ask for departure date",
           "validation": "similarity"
         }
       },
       {
         "prompt": "April 8, 2026",
         "expected_output": {
           "description": "Returns flight results with prices",
           "validation": "similarity",
           "key_fields": ["airline", "price"]
         }
       }
     ],
     "workflow_params": {}
   }
   ```

   Multi-turn tests use server-managed conversation state (`trace_id`) — each turn sends only
   its prompt, and the server maintains conversation history across turns.

### Simplified Testing Commands

```bash
# Compile WDL (MANDATORY before testing)
python cli/test_runner.py my-workflow --compile

# Test directly (executes local widdle.json via /run-wdl — no save needed)
python cli/test_runner.py my-workflow

# Test all cases
python cli/test_runner.py my-workflow --all

# Test saved remote action (after save_wdl_draft)
python cli/test_runner.py my-workflow --remote

# Multi-turn: compose individual test files into one conversation
python cli/test_runner.py my-agent --multi-turn test_1.json test_2.json --inline

# Auto-fix validation issues
python cli/validate.py my-workflow --auto-fix
```

Note: Default test mode executes local `widdle.json` directly via /run-wdl.
No need to save a draft before testing. Save draft only after tests pass.

---

## Common Error Patterns and Solutions

| Error | Cause | Fix |
|-------|-------|-----|
| `required_inputs should be a list` | required_inputs is a dict | Run `python cli/validate.py workflow-id --auto-fix` |
| `tool names have invalid characters` | Title has spaces/special chars | Run `python cli/validate.py workflow-id --auto-fix --orchestrator` |
| `Action not found` | action_id is lost/wrong | Run `python cli/reconnect.py workflow-id --search` |
| `Session expired` | API session/cookie expired | Update `security_params.cookie` in `adopt_profile.json` |
| `Empty WDL field` | Save failed partially | Run `python cli/save.py workflow-id` again |

---

## Quick Reference

```bash
# === TRANSPARENT COMMANDS (auto-detect everything) ===

# Check status
python cli/status.py my-workflow

# Validate structure
python cli/validate.py my-workflow
python cli/validate.py my-workflow --auto-fix
python cli/validate.py my-workflow --orchestrator  # For sub-tool use

# Compile and test
python cli/test_runner.py my-workflow --compile    # Compile WDL (MANDATORY first)
python cli/test_runner.py my-workflow              # Direct WDL test (no save needed)
python cli/test_runner.py my-workflow --all        # All test cases
python cli/test_runner.py my-workflow --remote     # Test saved remote action

# Save draft ONLY after tests pass (auto-creates action if needed)
python cli/save_wdl_draft.py --workflow-id my-workflow
python cli/save_wdl_draft.py --workflow-id my-workflow --description "Fixed bug"

# Publish (requires confirmation)
python cli/publish_wdl_action.py --workflow-id my-workflow

# Version management
python cli/list_wdl_versions.py --workflow-id my-workflow
python cli/checkout_wdl_version.py --workflow-id my-workflow --version 3

# Reconnect workspace to action
python cli/reconnect.py my-workflow --search  # Search by title
python cli/reconnect.py my-workflow abc-123-action-id

# === USE THESE FLAGS ONLY WHEN AUTO-DETECTION FAILS ===
--version N       # Force specific version
--allow-draft     # Force allow draft flag
--standalone      # Force standalone mode (no agent)
--agent NAME      # Force specific agent
--action-id ID    # Force specific action_id (legacy)
```

