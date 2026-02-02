# ABCD - Agent CLI Instructions

This document provides comprehensive guidance for AI agents (like Cursor) working with the ABCD repository.

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
| **CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md** | Creating WDL workflows, understanding operations |
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
| `python cli/test_wdl_action.py <workflow-id>` | Test workflow (auto-detects version/draft) |
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

# 3. Validate locally before testing
python cli/test_wdl_action.py my-workflow --local-only

# 4. Test remotely (auto-detects version/draft)
python cli/test_wdl_action.py my-workflow

# 5. Save draft (auto-creates action if needed)
python cli/save_wdl_draft.py --workflow-id my-workflow --description "Fixed pagination"

# 6. View versions
python cli/list_wdl_versions.py --workflow-id my-workflow

# 7. Publish when ready
python cli/publish_wdl_action.py --workflow-id my-workflow

# 8. Enable tool mode (for sub-actions)
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

### Config Inheritance

```
Action adopt_profile.json → Agent → Environment
```

---

## Testing Commands

**📖 Full details: `prompts/system/TESTING_PROMPT.md`**

### Single Action

```bash
python cli/test_wdl_action.py my-action
python cli/test_wdl_action.py my-action --local-only    # Validate only
python cli/test_wdl_action.py my-action --all           # All test cases
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
# Force specific version (when auto-detection picks wrong version)
python cli/test_wdl_action.py my-workflow --version 3 --allow-draft

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

- **`tool_builder.py`**: Main interactive CLI menu (entry point)
- **`cli/`**: All CLI scripts organized by functionality
- **`prompts/system/CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md`**: Comprehensive guide for creating complex WDL workflows
- **`README.md`**: User-facing documentation

---

## Quick Decision Tree

```
User Request
    │
    ├─ "Create a simple action" → python cli/manage_wdl_action.py --create --template simple --use-api <id>
    │
    ├─ "Create a complex workflow" → python cli/manage_wdl_action.py --create --template workflow -r requirements.md
    │
    ├─ "Search for APIs/actions" → python cli/discover.py --apis "query"
    │                            → python cli/discover.py --actions "query"
    │
    ├─ "Test a tool" → python cli/test_wdl_action.py <workflow_id>
    │
    ├─ "Diagnose and fix issues" → python cli/diagnose_and_fix.py
    │
    ├─ "Manage agents/workspaces" → python tool_agents.py
    │
    └─ "Version management" → python cli/list_wdl_versions.py / checkout_wdl_version.py
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
3. **Test**: `python cli/test_wdl_action.py my-action`
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
  
  # Specify environment for cache
  python cli/discover.py --actions "query" --env staging
  
  # Full details and JSON output
  python cli/discover.py --apis "user auth" --details --json
  ```
- **Cache Location**: `workspaces/{env}/.cache/actions_cache.json`, `apis_cache.json`
- **Search Modes**:
  - `semantic`: FAISS vector similarity (best for requirements)
  - `fuzzy`: Text matching with ratio score (best for names)
  - `hybrid`: Both combined (default)

#### 2. **Manage WDL Workflow** (`cli/manage_wdl_action.py`)
- **Purpose**: Create, update, and manage WDL workflow workspaces
- **Features**:
  - Workspace creation with context (`--create`)
  - Update existing workspaces (`--update`)
  - Add APIs/tools to existing workflows
  - Cursor instruction generation
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

#### 2. **Test WDL Workflow** (`cli/test_wdl_action.py`)
- **Purpose**: Test WDL workflows locally and remotely
- **Features**:
  - **Step 1**: JSON syntax validation (catches parsing errors)
  - **Step 2**: Local WDL structure validation (`--local-only`)
  - Remote execution with trace capture
  - **Draft testing**: Automatically detects and tests draft versions
  - **Test case management**: Run specific test case or all test cases
  - **Output validation**: Validates outputs against `expected_output` in test cases
  - Fix instruction generation on failure
- **Menu Option**: 9
- **Key Options**:
  ```bash
  --local-only          # Validate JSON syntax and WDL structure only
  --test FILE           # Use specific test case (filename only, not path)
                        # Examples: --test test_1.json, --test test_2.json
                        # NOT: --test test_cases/test_1.json (wrong!)
  --all                  # Run all test cases in test_cases/ directory
  --agent NAME          # Specify agent name
  ```
- **⚠️ Auto-save is DISABLED**: After testing, save drafts separately with `save_wdl_draft.py`
- **Draft Testing**: Tests draft versions directly without requiring publication
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
- **Usage**: Persist changes without making workflow live
- **Menu Option**: 10
- **Example**:
  ```bash
  python cli/save_wdl_draft.py --workflow-id {id} --description "Fixed bug" --standalone
  ```
- **Local Storage**: Each saved draft stores WDL in `versions/v{version_number}_widdle.json` for future checkout

#### 4. **Publish WDL Workflow** (`cli/publish_wdl_action.py`)
- **Purpose**: Approve and publish workflow (makes it live)
- **Features**:
  - **Version descriptions**: Supports `--description` parameter
  - **Local WDL storage**: Saves WDL to `versions/v{version_number}_widdle.json` when publishing
  - Updates version status to published in metadata.json
  - Shows existing description when available
- **⚠️ Requires explicit user confirmation**
- **Menu Option**: 11
- **Example**:
  ```bash
  python cli/publish_wdl_action.py {action_id} --version 10 --description "Production release"
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

**`tool_builder.py`**: Interactive menu-driven CLI
```bash
python tool_builder.py
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

#### Simple Actions
```bash
python cli/manage_wdl_action.py --list-apis    # List available APIs
python cli/manage_wdl_action.py --search-apis  # Search APIs semantically
python cli/manage_wdl_action.py --create --template simple -t "Title"  # Create action
python cli/test_wdl_action.py my-action        # Test action
python cli/save_wdl_draft.py --workflow-id my-action   # Save draft
python cli/publish_wdl_action.py --workflow-id my-action  # Publish
```

#### Complex Workflows (WDL)
```bash
python cli/manage_wdl_action.py [OPTIONS]    # Create/update WDL workflow
python cli/test_wdl_action.py [OPTIONS]       # Test WDL workflow
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

### Core Diagnostic Workflow

```bash
# 1. Run comprehensive scan
python cli/diagnose_and_fix.py --scan --format llm --output diagnostics/report.json

# 2. Review issues and create fixes
# (AI agent analyzes report and generates fixes.json)

# 3. Apply fixes with testing
python cli/diagnose_and_fix.py --apply-fixes fixes.json --test-after-fix

# 4. Rollback if needed
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

| Script | Purpose |
|--------|---------|
| `diagnose_and_fix.py` | Main workflow - scan, diagnose, fix |
| `fetch_http_logs.py` | Fetch and cache network logs |
| `diagnose_apis.py` | Scan APIs for issues |
| `inspect_tool_wdl.py` | Inspect tool WDLs for issues |
| `fix_api_path.py` | Fix API paths (trailing slashes) |
| `patch_tool_wdl.py` | Patch tool WDLs |
| `fix_and_test.py` | Apply fix and run tests |
| `rollback_changes.py` | Rollback applied changes |
| `generate_test_cases.py` | Generate test cases for tools |

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
→ **Use Search** (`cli/manage_wdl_action.py --search` or `--search-apis`)
- Semantic search
- Natural language queries
- Tool discovery

#### "I need to test my workflow"
→ **Use Test** (`cli/test_wdl_action.py`)
- Local validation first (`--local-only`)
- Remote execution
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
tool_builder_agents/
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

1. **Search** for existing APIs: `manage_wdl_action.py --search-apis`
2. **Create** action workspace: `manage_wdl_action.py --create --template simple`
3. **Edit** `widdle.json` using simple template pattern
4. **Test**: `test_wdl_action.py my-action`
5. **Save/Publish**: `save_wdl_draft.py` then `publish_wdl_action.py`

### For Complex WDL Workflow Creation

1. **👉 Read [`prompts/system/CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md`](CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md)**
2. **Discover** tools/APIs (`cli/manage_wdl_action.py --auto-discover`)
3. **Create** workspace (`cli/manage_wdl_action.py --create`)
4. **Update** workspace with APIs/tools (`cli/manage_wdl_action.py --update --workflow-id <id>` or use `--workflow-id` with `--use-api`/`--use-tool`)
5. **Generate** WDL (Cursor-led, following roaming instructions)
6. **Generate 3 test cases** BEFORE testing:
   - Create `test_1.json`, `test_2.json`, `test_3.json` in `test_cases/` directory
   - Test 1: Basic/common use case
   - Test 2: Different input scenario or parameter combination
   - Test 3: Edge case or alternative scenario
   - Each should include `prompt`, `workflow_params`, and `expected_output` with `similarity` validation
7. **Test** locally (`cli/test_wdl_action.py workflow-id --local-only`)
   - **Step 1**: JSON syntax validation (catches parsing errors early)
   - **Step 2**: WDL structure validation (catches logical errors)
8. **Test** remotely (`cli/test_wdl_action.py workflow-id --all`) - Runs all 3 test cases
   - Or test specific case: `python cli/test_wdl_action.py workflow-id --test test_2.json`
   - **Note**: Use filename only (e.g., `test_2.json`), not path (`test_cases/test_2.json`)
9. **Iterate** based on test results
10. **Save** draft (`cli/save_wdl_draft.py`) - Automatically creates remote action and publishes WDL if needed
11. **Publish** when user confirms (`cli/publish_wdl_action.py`)

---

## Important Notes for Agents

1. **Always ask for confirmation before publishing** - Publishing makes workflows live
2. **Use local validation first** - Test with `--local-only` before remote execution
   - **Step 1**: JSON syntax validation catches parsing errors (invalid JSON, malformed strings, etc.)
   - **Step 2**: WDL structure validation catches logical errors (missing fields, invalid references, etc.)
   - Fix JSON errors first before proceeding to structure validation
3. **Auto-save drafts** - Tests passing auto-save drafts (safe)
4. **Automatic remote action creation** - `save_wdl_draft.py` automatically creates remote action and publishes WDL if needed
5. **Draft testing** - Draft actions can be tested directly without publishing
6. **Version management** - Every draft creates a version with descriptions, users can checkout and test any version
7. **Version descriptions** - All versions can have descriptions stored locally and synced from API
5. **Roaming RAG** - For WDL generation, fetch the index from `https://adoptai.github.io/widdle_docs/operations/index.md`, then fetch specific operation docs as needed
6. **Tool discovery** - Use semantic search to find relevant building blocks
7. **Agent organization** - Use agents to group related tools/workflows

---

## Quick Reference: Common Commands

```bash
# Simple action creation (template-based)
python cli/manage_wdl_action.py --create --template simple -t "My Action"

# Search for APIs
python cli/manage_wdl_action.py --search-apis "query"
# OR
python cli/manage_wdl_action.py --search "query"

# Create WDL workflow
python cli/manage_wdl_action.py --create -r requirements.md -t "Title" --agent my-agent

# Update existing workflow (add APIs/tools)
python cli/manage_wdl_action.py --update --workflow-id abc123 --use-api api-1 --use-api api-2

# Add APIs/tools independently (no --create/--update needed)
python cli/manage_wdl_action.py --workflow-id abc123 --use-api api-1 --use-tool tool-1

# Test WDL workflow
python cli/test_wdl_action.py workflow-id --local-only  # Validate JSON syntax + structure
python cli/test_wdl_action.py workflow-id               # Execute remotely (default test case)
python cli/test_wdl_action.py workflow-id --all         # Run all test cases
python cli/test_wdl_action.py workflow-id --test test_2.json  # Run specific test case (filename only!)
# Note: Use --test test_2.json, NOT --test test_cases/test_2.json

# Version management
python cli/list_wdl_versions.py {action_id} --workflow-id workflow-id --standalone
python cli/checkout_wdl_version.py {action_id} --version 3 --workflow-id workflow-id --standalone

# Save draft with description (auto-creates remote action and publishes WDL if needed)
python cli/save_wdl_draft.py --workflow-id workflow-id --description "Fixed bug" --standalone

# Test draft version directly (no publish needed!)
python cli/test_wdl_action.py workflow-id

# Publish (requires confirmation)
python cli/publish_wdl_action.py workflow-id
```

---

## Getting Help

- **User Documentation**: See `README.md`
- **WDL Workflow Guide**: See [`prompts/system/CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md`](CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md)
- **CLI Help**: `python cli/[script_name].py --help`
- **Interactive Menu**: `python tool_builder.py`

---

## Summary

- **Simple Actions**: Use for single-API wrappers → See `prompts/templates/simple_tool_template.md`
- **Complex Workflows**: Use for multi-step operations → **See [`prompts/system/CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md`](CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md)**
- **Uber Agents**: Use for multi-action orchestrators → **See [`prompts/system/UBER_AGENT_PROMPT.md`](UBER_AGENT_PROMPT.md)**
- **Discovery**: Use `--search-apis` / `--search` / `--auto-discover` options
- **Testing**: Always validate locally first (`--local-only`), then test remotely (`--all`)
- **Publishing**: Only when user explicitly confirms

**Workflow Order**:
1. Create/Generate WDL
2. Local validation (`test_wdl_action.py --local-only`)
3. Remote testing (`test_wdl_action.py --all`)
4. Save draft (`save_wdl_draft.py`)
5. Publish (`publish_wdl_action.py`)

For detailed WDL workflow creation instructions, **always refer to [`prompts/system/CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md`](CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md)**.

---

## PROMPT_AND_TOOLS_AGENT Requirements

When using actions as sub-tools in a `PROMPT_AND_TOOLS_AGENT` orchestrator, follow these requirements:

### Sub-Action Requirements

1. **Must be PUBLISHED** - Draft actions cannot be used as sub-tools
2. **Title format** - Must match pattern: `^[a-zA-Z0-9_-]{1,128}$`
   - ✅ Good: `odoo-get-orderpoints`, `inventory_check`, `create-po`
   - ❌ Bad: `Odoo Get Orderpoints`, `Check (Inventory)`, `create po`
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
├── odoo-get-orderpoints      # Simple REST wrapper
├── odoo-create-po            # Simple REST wrapper
├── odoo-exception-check      # Simple REST wrapper
└── ...

Orchestrator (uses tools above):
└── inventory-orchestrator    # PROMPT_AND_TOOLS_AGENT
    └── Uses: odoo-get-orderpoints, odoo-create-po, ...
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

2. Each test case must include:
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

### Simplified Testing Commands

```bash
# Validate locally (no remote execution)
python cli/test.py my-workflow --local-only

# Test remotely (auto-detects version)
python cli/test.py my-workflow

# Test all cases
python cli/test.py my-workflow --all

# Auto-fix validation issues
python cli/test.py my-workflow --auto-fix
```

Note: The CLI automatically determines which version to test based on your
working state. No need to specify `--version` or `--allow-draft`.

---

## Common Error Patterns and Solutions

| Error | Cause | Fix |
|-------|-------|-----|
| `required_inputs should be a list` | required_inputs is a dict | Run `python cli/validate.py workflow-id --auto-fix` |
| `tool names have invalid characters` | Title has spaces/special chars | Run `python cli/validate.py workflow-id --auto-fix --orchestrator` |
| `Action not found` | action_id is lost/wrong | Run `python cli/reconnect.py workflow-id --search` |
| `Session expired` | Odoo cookie expired | Update `security_params.cookie` in `adopt_profile.json` |
| `Empty WDL field` | Save failed partially | Run `python cli/save.py workflow-id` again |

---

## Quick Reference

```bash
# === TRANSPARENT COMMANDS (auto-detect everything) ===

# Check status
python cli/status.py my-workflow

# Validate locally
python cli/validate.py my-workflow
python cli/validate.py my-workflow --auto-fix
python cli/validate.py my-workflow --orchestrator  # For sub-tool use

# Test (auto-detects version/draft from metadata)
python cli/test_wdl_action.py my-workflow --local-only  # Validate only
python cli/test_wdl_action.py my-workflow               # Remote test
python cli/test_wdl_action.py my-workflow --all         # All test cases

# Save draft (auto-creates action if needed)
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

