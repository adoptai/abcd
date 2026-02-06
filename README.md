# ABCD - Agent-Based Action & Workflow Builder

A comprehensive CLI toolkit designed for AI agents (like Cursor) to build, test, and manage actions and workflows on the AdoptAI platform.

## Overview

ABCD is a comprehensive Agent powered by guiding prompts and a CLI toolkit for building, testing, and managing AI-powered actions and workflows on the AdoptAI platform. Designed for AI agents (Cursor, Claude, etc.) to invoke as part of automated workflows.

### Action Types

| Type | Description | Use Case |
|------|-------------|----------|
| **Simple Tools** | Single-API wrappers (REST → OUTPUT pattern) | Quick API integrations, data fetching |
| **Complex Workflows** | Multi-step WDL with REST, JQ_FILTER, PROMPT, CONDITION, etc. | Business logic, data transformations, AI integration |
| **Uber Agents** | Multi-action orchestrators using PROMPT_AND_TOOLS_AGENT | Composing atomic tools into intelligent workflows |

### Discovery & Search

- **Semantic Search**: FAISS embeddings for natural language queries (`"inventory management"`)
- **Fuzzy Search**: Text matching for specific names (`"get-orderpoints"`)
- **Hybrid Mode**: Combines both for best results
- **Requirements-based**: Auto-discover APIs/actions from `.md` requirements files

### Workspace Management

- **Hierarchical Structure**: Environments → Agents → Actions
- **Multi-Environment Support**: Separate staging/production, multiple clients
- **Config Inheritance**: Action inherits from Agent inherits from Environment
- **Profile Management**: Centralized `adopt_profile.json` for base URLs, security params

### Testing & Validation

- **Local Validation**: JSON syntax + WDL structure validation (`--local-only`)
- **Remote Execution**: Full workflow testing with trace capture
- **Test Cases**: JSON-based test cases with expected output validation
- **Parallel Testing**: Test multiple actions simultaneously (`--parallel N`)
- **Batch Testing**: Test all actions in workspace or agent (`--workspace`, `--agent`)
- **Via-Agent Testing**: Test sub-actions through parent orchestrator

### Version Management

- **Draft Support**: Save and test drafts without publishing
- **Version History**: List all versions with descriptions and timestamps
- **Checkout**: Restore any previous version (local-first, falls back to API)
- **Publish**: Make specific version live (requires explicit confirmation)

### Diagnostics & Fixes

- **Comprehensive Scanning**: Detect issues across all APIs and WDLs
- **Issue Detection**: Trailing slashes, missing parameters, invalid structures
- **Automated Fixes**: Apply fixes with optional post-fix testing
- **HTTP Log Analysis**: Fetch and analyze network logs for debugging
- **Rollback Support**: Undo applied changes when needed

### Deployment & Tool Mode

- **Tool Mode**: Enable actions as sub-tools for Uber Agents
- **Deployment Rules**: Manage visibility and execution settings
- **Cross-Environment Moves**: Move/copy actions between environments

## Prerequisites

- **Python 3.11 or higher**
- **Poetry** (Python dependency manager)

```bash
curl -sSL https://install.python-poetry.org | python3 -
```

## Installation

```bash
# Clone the repository
git clone <repository-url> abcd
cd abcd

# Install dependencies
poetry install

# Copy environment template
cp dev.env .env

# Configure your credentials in .env
```

## Environment Setup

**All operations require an environment.** The repo ships with a `default` environment that is automatically activated.

### 1. Configure the Default Environment

Edit `workspaces/default/.env` with your AdoptAI credentials:

```bash
# Required
ADOPT_API_KEY=your-api-key-here

# Optional (defaults shown)
ADOPT_API_ENDPOINT=https://connect.adopt.ai
ADOPT_ACTIONS_ENDPOINT=https://api.adopt.ai
```

### 2. Create Additional Environments (Optional)

For staging/production separation or multiple clients:

```bash
# Create production environment
python cli/workspace.py env create --id production --name "Production" --target production

# Switch to production
python cli/workspace.py env use production

# Configure production credentials
# Edit workspaces/production/.env
```

### 3. Check Active Environment

```bash
python cli/workspace.py env list
```

## Quick Decision Tree

```
User Request
    │
    ├─ "Create a simple API wrapper"
    │   → python cli/manage_wdl_action.py --create --template simple --use-api <id>
    │
    ├─ "Create a complex workflow"
    │   → python cli/manage_wdl_action.py --create --template workflow -r requirements.md
    │
    ├─ "Search for APIs/actions"
    │   → python cli/discover.py --apis "query"
    │   → python cli/discover.py --actions "query"
    │
    ├─ "Test a workflow"
    │   → python cli/test_runner.py <workflow_id>
    │
    ├─ "Diagnose and fix issues"
    │   → python cli/diagnose_and_fix.py
    │
    └─ "Version management"
        → python cli/list_wdl_versions.py / checkout_wdl_version.py
```

## CLI Commands Reference

### Discovery Commands

```bash
# Search for actions (semantic + fuzzy hybrid)
python cli/discover.py --actions "inventory management" [--json]

# Search for APIs
python cli/discover.py --apis "authentication endpoint" [--json]

# Semantic search only (FAISS embeddings)
python cli/discover.py --actions "natural language query" --mode semantic

# Fuzzy search only (text matching)
python cli/discover.py --actions "get-orderpoints" --mode fuzzy

# Discover from requirements file
python cli/discover.py --requirements requirements.md

# Tools only (filter by execution_type=TOOL)
python cli/discover.py --actions "fetch" --tools-only

# Specify environment for cache location
python cli/discover.py --actions "query" --env staging
```

### Workflow Creation Commands

```bash
# Create complex workflow in agent workspace
python cli/manage_wdl_action.py --create -r requirements.md -t "Title" --agent my-agent

# Create complex workflow standalone
python cli/manage_wdl_action.py --create -r requirements.md -t "Title" --standalone

# Create simple tool (single API wrapper)
python cli/manage_wdl_action.py --create --template simple --use-api api-id -t "My Tool"

# Create with tool/API context
python cli/manage_wdl_action.py --create -r requirements.md -t "Title" \
  --use-tool tool-id-1 --use-api api-id-1

# Create and register on Adopt immediately
python cli/manage_wdl_action.py --create -r requirements.md -t "Title" --create-remote
```

### Testing Commands

```bash
# Validate WDL structure only (no remote execution)
python cli/test_runner.py {workflow_id} --local-only

# Full remote test
python cli/test_runner.py {workflow_id}

# Test with specific test case
python cli/test_runner.py {workflow_id} --test test_2.json

# Run all test cases
python cli/test_runner.py {workflow_id} --all

# Verbose output (shows WDL operations, full traces)
python cli/test_runner.py {workflow_id} --verbose

# Test multiple actions in parallel
python cli/test_runner.py action1 action2 action3 --parallel 3

# Test all actions in workspace
python cli/test_runner.py --workspace my-env

# Test all sub-actions in an agent
python cli/test_runner.py --agent my-agent --all-subactions

# Test sub-action through parent agent
python cli/test_runner.py my-agent --via-agent --subaction get-data
```

### Version Management

```bash
# List all versions
python cli/list_wdl_versions.py {action_id} --workflow-id {workflow_id} --standalone

# Checkout specific version
python cli/checkout_wdl_version.py {action_id} --version 3 --workflow-id {workflow_id} --standalone

# Save current as draft
python cli/save_wdl_draft.py --workflow-id {workflow_id} --description "Fixed bug" --standalone

# Publish workflow (makes it live)
python cli/publish_wdl_action.py {action_id} --version 10 --description "Production release"
```

### Diagnostic Commands

```bash
# Run comprehensive scan of all tools and APIs
python cli/diagnose_and_fix.py --scan --format llm --output diagnostics/report.json

# Diagnose specific API issues
python cli/diagnose_apis.py --scan --output diagnostics/api_report.json

# Inspect a specific tool's WDL
python cli/inspect_tool_wdl.py <tool-id>

# Fix API paths
python cli/fix_api_path.py <api-id> --path "/api/v1/resource/{id}/"

# Apply fixes from a JSON file
python cli/diagnose_and_fix.py --apply-fixes fixes.json --test-after-fix

# Rollback changes
python cli/rollback_changes.py --file diagnostics/rollback_xxx.json
```

## Directory Structure

```
abcd/
├── cli/                          # All CLI scripts
│   ├── auth.py                   # Authentication
│   ├── discover.py               # Action/API discovery (FAISS + fuzzy)
│   ├── workspace.py              # Hierarchical workspace management
│   ├── manage_wdl_action.py      # Workflow creation & management
│   ├── test_runner.py            # Testing workflows (parallel/batch)
│   ├── save_wdl_draft.py         # Save drafts
│   ├── publish_wdl_action.py     # Publish workflows
│   ├── list_wdl_versions.py      # Version listing
│   ├── checkout_wdl_version.py   # Version checkout
│   ├── deployment_rules.py       # Tool mode management
│   ├── status.py                 # Workspace status
│   ├── validate.py               # Local WDL validation
│   ├── reconnect.py              # Reconnect workspace to action
│   ├── diagnose_and_fix.py       # Diagnostics
│   └── wdl_common/               # Shared utilities
│       ├── api_client.py         # AdoptAI API client
│       ├── workspace_manager.py  # Hierarchical workspace management
│       ├── discovery.py          # Action/API discovery with caching
│       ├── metadata_manager.py   # Action metadata management
│       ├── validator.py          # WDL validation
│       ├── trace_analyzer.py     # Test trace analysis
│       └── ...
├── prompts/                      # System prompts & templates
│   ├── system/                   # Agent system prompts
│   │   ├── CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md
│   │   └── DIAGNOSE_AND_FIX_SYSTEM_PROMPT.md
│   ├── templates/                # WDL templates
│   │   └── simple_tool_template.md
│   └── guidelines/               # Best practices
│       └── WDL_ISSUE_PATTERNS.md
├── .cursor/rules/                # Cursor agent rules
│   └── agents.mdc
├── agents.md                     # Agent instructions documentation
├── requirements/                 # Example requirements files
├── pyproject.toml               # Project dependencies
└── README.md                    # This file
```

## Agent Workflow

### For Simple Action Creation

1. **Discover** existing APIs: `python cli/discover.py --apis "inventory management"`
2. **Create** action: `python cli/manage_wdl_action.py --create --template simple --use-api <id>`
3. **Refine** WDL based on API specification
4. **Validate**: `python cli/test_runner.py <id> --local-only`
5. **Test**: `python cli/test_runner.py <id>`
6. **Save draft**: `python cli/save_wdl_draft.py --workflow-id <id>`
7. **Publish**: `python cli/publish_wdl_action.py <id>`

### For Complex WDL Workflow Creation

1. **Read** `prompts/system/CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md`
2. **Discover** tools/APIs: `python cli/discover.py --requirements requirements.md`
3. **Create** workspace: `python cli/manage_wdl_action.py --create -r requirements.md -t "Title"`
4. **Generate** WDL following roaming instructions
5. **Create 3 test cases** in `test_cases/` directory
6. **Test locally**: `python cli/test_runner.py <id> --local-only`
7. **Save draft**: `python cli/save_wdl_draft.py --workflow-id <id>`
8. **Test remotely**: `python cli/test_runner.py <id> --all`
9. **Iterate** until all tests pass
10. **Publish** when user confirms: `python cli/publish_wdl_action.py <id>`

## Key Concepts

### WDL (Workflow Definition Language)

WDL defines multi-step workflows with operations like:
- `REST` - HTTP API calls
- `JQ_FILTER` - JSON transformations
- `EXTRACT` / `PROJECT` - Data extraction
- `PROMPT` - LLM calls
- `CONDITION` - Conditional branching
- `OUTPUT_TEXT` / `OUTPUT_TABLE` - Output formatting

### Workspaces

Workflows are organized in workspaces:
- **Standalone**: Created in `actions/` directory
- **Agent-based**: Created in `tool_builder_agents/<agent-name>/workflows/`

### Version Management

- Every saved draft creates a version
- Versions can be checked out and tested
- All versions stored locally in `versions/` folder
- Publish makes a version live

## Documentation

- **Agent Instructions**: See `agents.md`
- **WDL Workflow Guide**: See `prompts/system/CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md`
- **Diagnostic Guide**: See `prompts/system/DIAGNOSE_AND_FIX_SYSTEM_PROMPT.md`
- **Issue Patterns**: See `prompts/guidelines/WDL_ISSUE_PATTERNS.md`
- **CLI Help**: `python cli/[script_name].py --help`

## License

See [LICENSE](LICENSE) file.
