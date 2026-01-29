# ABCD - Agent-Based Action & Workflow Builder

A comprehensive CLI toolkit designed for AI agents (like Cursor) to build, test, and manage actions and workflows on the AdoptAI platform.

## Overview

This repository provides agent-driven scripts for:

1. **Simple Tools**: Single-API wrapper tools (REST → OUTPUT pattern)
2. **Complex Workflows**: Multi-step WDL workflows with multiple operations, AI integration, and data transformations
3. **Diagnostics & Fixes**: Tools to diagnose and fix issues in APIs and WDLs

> **Note**: This is an agent-driven toolkit, not a traditional interactive CLI. The scripts are designed to be invoked by AI agents (Cursor, Claude, etc.) as part of automated workflows.

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

Edit `.env` with your AdoptAI credentials:

```bash
# Required
ADOPT_CLIENT_ID=your_client_id_here
ADOPT_CLIENT_SECRET=your_client_secret_here

# Optional (defaults shown)
ADOPT_API_ENDPOINT=https://connect.adopt.ai
ADOPT_ACTIONS_ENDPOINT=https://api.adopt.ai

# Optional - for LLM features
OPENAI_API_KEY=your_openai_api_key_here

# Optional - for bulk evaluation
MAXIM_API_KEY=your_maxim_api_key_here
MAXIM_WORKSPACE_ID=your_maxim_workspace_id_here
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
    ├─ "Search for APIs/tools"
    │   → python cli/manage_wdl_action.py --search-apis "query"
    │   → python cli/manage_wdl_action.py --search "query"
    │
    ├─ "Test a workflow"
    │   → python cli/test_wdl_action.py <workflow_id>
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
# List all available tools
python cli/manage_wdl_action.py --list-tools [--json]

# List all available APIs
python cli/manage_wdl_action.py --list-apis [--json]

# Semantic search for tools
python cli/manage_wdl_action.py --search "natural language query" [--json] [--top-k 10]

# Semantic search for APIs
python cli/manage_wdl_action.py --search-apis "natural language query" [--json] [--top-k 10]

# Auto-discover tools AND APIs based on requirements
python cli/manage_wdl_action.py --auto-discover -r requirements.md [--top-k 5]
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
python cli/test_wdl_action.py {workflow_id} --local-only

# Full remote test
python cli/test_wdl_action.py {workflow_id}

# Test with specific test case
python cli/test_wdl_action.py {workflow_id} --test test_2.json

# Run all test cases
python cli/test_wdl_action.py {workflow_id} --all

# Test with agent
python cli/test_wdl_action.py {workflow_id} --agent my-agent
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

### Bulk Evaluation Commands

```bash
# Run bulk evaluation using workspace test cases
python cli/eval_wdl_action.py {workflow_id}

# Evaluate with custom CSV test data
python cli/eval_wdl_action.py {workflow_id} --csv-file tests.csv

# Evaluate with field exclusion
python cli/eval_wdl_action.py {workflow_id} --exclude-fields header_message,footer_message
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
│   ├── agents.py                 # Agent/workspace management
│   ├── auth.py                   # Authentication
│   ├── manage_wdl_action.py      # Workflow creation & management
│   ├── test_wdl_action.py        # Testing workflows
│   ├── save_wdl_draft.py         # Save drafts
│   ├── publish_wdl_action.py     # Publish workflows
│   ├── list_wdl_versions.py      # Version listing
│   ├── checkout_wdl_version.py   # Version checkout
│   ├── diagnose_and_fix.py       # Diagnostics
│   ├── search_tools.py           # Tool search
│   ├── search_apis.py            # API search
│   └── wdl_common/               # Shared utilities
│       ├── api_client.py         # AdoptAI API client
│       ├── workspace_manager.py  # Workspace management
│       ├── tool_discovery.py     # Tool/API discovery
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

### For Simple Tool Creation

1. **Search** for existing APIs: `python cli/manage_wdl_action.py --search-apis "query"`
2. **Create** tool: `python cli/manage_wdl_action.py --create --template simple --use-api <id>`
3. **Refine** WDL based on API specification
4. **Test**: `python cli/test_wdl_action.py <id> --local-only`
5. **Save draft**: `python cli/save_wdl_draft.py --workflow-id <id>`
6. **Publish**: `python cli/publish_wdl_action.py <id>`

### For Complex WDL Workflow Creation

1. **Read** `prompts/system/CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md`
2. **Discover** tools/APIs: `python cli/manage_wdl_action.py --auto-discover -r requirements.md`
3. **Create** workspace: `python cli/manage_wdl_action.py --create -r requirements.md -t "Title"`
4. **Generate** WDL following roaming instructions
5. **Create 3 test cases** in `test_cases/` directory
6. **Test locally**: `python cli/test_wdl_action.py <id> --local-only`
7. **Save draft**: `python cli/save_wdl_draft.py --workflow-id <id>`
8. **Test remotely**: `python cli/test_wdl_action.py <id> --all`
9. **Iterate** until all tests pass
10. **Evaluate** (optional): `python cli/eval_wdl_action.py <id>`
11. **Publish** when user confirms: `python cli/publish_wdl_action.py <id>`

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
