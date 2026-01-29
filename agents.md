# Tool Builder - Agent Instructions

This document provides comprehensive guidance for AI agents (like Cursor) working with the Tool Builder repository. It explains all available functionalities, CLI tools, and when to use each.

## Table of Contents

- [Overview](#overview)
- [Quick Decision Tree](#quick-decision-tree)
- [Functionality Categories](#functionality-categories)
  - [Agent Management](#agent-management)
  - [Simple Tools (API Wrappers)](#simple-tools-api-wrappers)
  - [Complex Workflows (WDL)](#complex-workflows-wdl)
- [CLI Tools Reference](#cli-tools-reference)
- [When to Use Each Tool](#when-to-use-each-tool)
- [Integration Points](#integration-points)

---

## Overview

The Tool Builder repository provides a comprehensive CLI toolkit for managing tools and workflows on the AdoptAI platform. It supports two main types of operations:

1. **Simple Tools**: Single-API wrapper tools (REST → OUTPUT pattern)
2. **Complex Workflows**: Multi-step WDL workflows with multiple operations, AI integration, and data transformations

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
    ├─ "Create a simple API wrapper" → python cli/manage_wdl_action.py --create --template simple --use-api <id>
    │
    ├─ "Create a complex workflow" → python cli/manage_wdl_action.py --create --template workflow -r requirements.md
    │
    ├─ "Search for APIs/tools" → python cli/manage_wdl_action.py --search-apis "query"
    │                          → python cli/manage_wdl_action.py --search "query"
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

### Agent Management

**Purpose**: Organize tools and workflows into logical groups (agents/workspaces)

**Tools**:
- `cli/agents.py`: Create, delete, and select agents
- Used by all other tools for workspace organization

**When to Use**:
- User wants to organize tools by project/team/feature
- Need to group related tools and workflows together
- Setting up a new project workspace

**CLI Access**:
- Interactive menu: Options 1-2 in `tool_builder.py`
- Direct: `python cli/agents.py` (if standalone)

---

### Simple Tools (API Wrappers)

**Purpose**: Create single-API wrapper tools that follow REST → OUTPUT pattern

**Characteristics**:
- One REST API call
- Simple data transformation (optional)
- Single output operation
- Fast to create and test

**Unified CLI**: `cli/manage_wdl_action.py --template simple`

**Tools**:

#### 1. **Create Simple Tool** (unified CLI)
- **Purpose**: Create new simple tools from a single API
- **Usage**: `python cli/manage_wdl_action.py --create --template simple --use-api <api-id> -t "Title"`
- **Features**:
  - Auto-discovers API details
  - Creates workspace with placeholder WDL
  - Agent refines WDL based on template
  - Optionally creates remote action on Adopt
- **Menu Option**: 5

#### 2. **List APIs** (`cli/manage_wdl_action.py --list-apis`)
- **Purpose**: Browse all available APIs in AdoptAI instance
- **Features**: Paginated view, JSON output
- **Usage**: `python cli/manage_wdl_action.py --list-apis [--json]`

#### 3. **Search APIs** (`cli/manage_wdl_action.py --search-apis`)
- **Purpose**: Semantic search for APIs using natural language
- **Features**: FAISS indexing, sentence-transformers embeddings
- **Menu Option**: 4

#### 4. **Checkout Tools** (`checkout_tools.py`)
- **Purpose**: Download tool definitions from AdoptAI to local workspace
- **Usage**: Get existing tools for modification or reference
- **Menu Option**: 6

#### 5. **Test Tools** (`test_adopt_zaction.py` / `patch_wdls.py`)
- **Purpose**: Test simple tools and generate WDL test structures
- **Features**: Validates WDL structure, tests execution
- **Menu Option**: 7

#### 6. **Update Tools** (`update_adopt_zaction.py`)
- **Purpose**: Update existing simple tools
- **Usage**: Modify tool definitions, WDL, or metadata

**When to Use Simple Tools**:
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

#### 1. **Manage WDL Workflow** (`cli/manage_wdl_action.py`)
- **Purpose**: Create, update, and manage WDL workflow workspaces
- **Features**:
  - Tool/API discovery (`--search`, `--auto-discover`)
  - Workspace creation with context (`--create`)
  - Update existing workspaces (`--update`)
  - Add APIs/tools to existing workflows independently
  - Cursor instruction generation
- **Menu Option**: 8
- **Key Options**:
  ```bash
  # Discovery (works independently)
  --list-tools          # List all available tools
  --list-apis           # List all available APIs
  --search "query"      # Semantic search for tools
  --search-apis "query" # Semantic search for APIs
  --auto-discover       # Auto-discover based on requirements
  
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
  
  # Add APIs/tools independently (no --create/--update needed)
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

#### 7. **Evaluate WDL Workflow** (`cli/eval_wdl_action.py`)
- **Purpose**: Run comprehensive bulk evaluation using AdoptXchange/Maxim platform
- **Features**:
  - Integrates with **Maxim** evaluation platform via AdoptXchange
  - Uses **BulkEvaluator** for parallel test execution
  - **Schema validation**: Verifies output structure matches expected
  - **Tracing validation**: Compares execution steps (APIs, operations)
  - **Semantic similarity**: Maxim's "Ragas Answer Semantic Similarity" evaluator
  - **Bias detection**: Maxim's "Bias" evaluator
  - Saves results to CSV and JSON summary
- **Menu Option**: 14 (new)
- **Key Options**:
  ```bash
  # Evaluate with workspace test cases
  python cli/eval_wdl_action.py {workflow_id}
  
  # Evaluate with custom CSV test data
  python cli/eval_wdl_action.py {workflow_id} --csv-file tests.csv
  
  # Evaluate with field exclusion
  python cli/eval_wdl_action.py {workflow_id} --exclude-fields header_message
  
  # Evaluate in specific agent
  python cli/eval_wdl_action.py {workflow_id} --agent my-agent
  
  # Use workspace test cases directory
  python cli/eval_wdl_action.py {workflow_id} --use-workspace-tests
  ```
- **Requirements**:
  ```bash
  # In .env file
  MAXIM_API_KEY=your-maxim-api-key
  MAXIM_WORKSPACE_ID=your-maxim-workspace-id
  ```
- **Output Files**:
  - `evals/eval_summary_{timestamp}.json` - Evaluation summary
  - `evals/evaluation_results_{timestamp}.csv` - Detailed per-test results
  - Maxim dashboard link for visual review

**When to Use Evaluation**:
- Before publishing a major version
- When validating fixes across multiple test cases
- For regression testing after WDL changes
- For comprehensive quality assessment
- When quick tests pass but need deeper validation

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

--- Simple Tools (API Wrappers) ---
3. List all tools
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
14. Evaluate WDL workflow (AdoptXchange/Maxim)

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

#### Simple Tools
```bash
python list_tools.py                     # List all tools
python search_tools.py                   # Search tools semantically
python create_tools.py                   # Create new tools
python checkout_tools.py                 # Checkout existing tools
python cli/test_adopt_zaction.py        # Test simple tools
python cli/update_adopt_zaction.py      # Update simple tools
```

#### Complex Workflows (WDL)
```bash
python cli/manage_wdl_action.py [OPTIONS]    # Create/update WDL workflow
python cli/test_wdl_action.py [OPTIONS]       # Test WDL workflow
python cli/save_wdl_draft.py [OPTIONS]        # Save draft
python cli/publish_wdl_action.py [OPTIONS]    # Publish workflow
python cli/list_wdl_versions.py [OPTIONS]    # List versions
python cli/checkout_wdl_version.py [OPTIONS] # Checkout version
python cli/eval_wdl_action.py [OPTIONS]      # Bulk evaluation with Maxim
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
→ **Use Simple Tools** (`create_tools.py`)
- Quick setup
- Single REST operation
- Simple output

#### "I need a complex workflow with multiple steps"
→ **Use WDL Workflows** (`cli/manage_wdl_action.py`)
- **👉 See [`prompts/system/CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md`](CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md)**
- Multiple operations
- Complex data flow
- AI integration

#### "I want to find existing tools"
→ **Use Search** (`search_tools.py` or `cli/manage_wdl_action.py --search`)
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

#### "I need comprehensive evaluation before publishing"
→ **Use Evaluation** (`cli/eval_wdl_action.py`)
- Bulk test execution with AdoptXchange/Maxim
- Schema validation (output structure)
- Tracing validation (API calls, operations)
- Semantic similarity scoring
- Bias detection
- CSV results for detailed analysis

**When to use Test vs Evaluation**:
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

3. **Tool Discovery** (`cli/wdl_common/tool_discovery.py`)
   - Wraps `search_tools.py`, `list_tools.py`, `create_tools.py`
   - Semantic search integration
   - Used by WDL workflow creation

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
    ├── tools/              # Simple tools
    │   └── {tool_id}.json
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

## Key Differences: Simple Tools vs WDL Workflows

| Feature | Simple Tools | WDL Workflows |
|---------|-------------|---------------|
| **Complexity** | Single API call | Multiple operations |
| **Operations** | REST → OUTPUT | REST, JQ_FILTER, EXTRACT, PROJECT, PROMPT, CONDITION, etc. |
| **Data Flow** | Simple | Complex with transformations |
| **AI Integration** | No | Yes (PROMPT, PROMPT_AND_TOOLS_AGENT) |
| **Control Flow** | Linear | Conditional branching |
| **Creation Time** | Minutes | Hours (with iteration) |
| **Use Case** | API wrappers | Complex business logic |

---

## Agent Workflow Recommendations

### For Simple Tool Creation

1. **Search** for existing tools/APIs (`search_tools.py`)
2. **Create** tool (`create_tools.py`)
3. **Test** tool (`test_adopt_zaction.py`)
4. **Update** if needed (`update_adopt_zaction.py`)

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
11. **Evaluate** comprehensively (`cli/eval_wdl_action.py workflow-id`) - Before publishing:
    - Run bulk evaluation with AdoptXchange/Maxim
    - Check schema validation results
    - Check tracing validation results  
    - Review semantic similarity and bias scores
    - Fix any issues found before publishing
12. **Publish** when user confirms (`cli/publish_wdl_action.py`)

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
# Simple tool creation
python create_tools.py

# Search for tools
python search_tools.py
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

# Run comprehensive evaluation (before publishing)
python cli/eval_wdl_action.py workflow-id
python cli/eval_wdl_action.py workflow-id --csv-file tests.csv   # With custom test data

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

- **Simple Tools**: Use for single-API wrappers → `create_tools.py`
- **Complex Workflows**: Use for multi-step operations → **See [`prompts/system/CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md`](CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md)**
- **Discovery**: Use `search_tools.py` or `--search` / `--auto-discover` options
- **Testing**: Always validate locally first (`--local-only`), then test remotely (`--all`)
- **Evaluation**: Run comprehensive evaluation before publishing → `cli/eval_wdl_action.py`
  - Schema validation, tracing validation, semantic similarity, bias detection
  - Uses AdoptXchange/Maxim platform
- **Publishing**: Only when user explicitly confirms, after evaluation passes

**Workflow Order**:
1. Create/Generate WDL
2. Local validation (`test_wdl_action.py --local-only`)
3. Remote testing (`test_wdl_action.py --all`)
4. Save draft (`save_wdl_draft.py`)
5. **Comprehensive evaluation** (`eval_wdl_action.py`) ← Before publishing
6. Publish (`publish_wdl_action.py`)

For detailed WDL workflow creation instructions, **always refer to [`prompts/system/CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md`](CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md)**.

