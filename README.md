# ABCD - (Adopt | Agent | Action | Automation) Builder in  (Cursor | Copilot | Claude) for (Devs | Dreamers)

A comprehensive CLI toolkit designed for AI agents (like Cursor) to build, test, and manage actions, pipelines, and workflows on the AdoptAI platform.

## Overview

ABCD is a comprehensive Agent powered by guiding prompts and a CLI toolkit for building, testing, and managing AI-powered actions, data pipelines, and workflows on the AdoptAI platform. Designed for AI agents (Cursor, Claude, etc.) to invoke as part of automated workflows.

### Action Types

| Type | Description | Use Case |
|------|-------------|----------|
| **Simple Tools** | Single-API wrappers (REST → OUTPUT pattern) | Quick API integrations, data fetching |
| **Complex Workflows** | Multi-step WDL with REST, JQ_FILTER, PROMPT, CONDITION, etc. | Business logic, data transformations, AI integration |
| **Uber Agents** | Multi-action orchestrators using PROMPT_AND_TOOLS_AGENT | Composing atomic tools into intelligent workflows |
| **Data Pipelines** | ETL / data sync workflows with connectors and table registry | Batch processing, data enrichment, scheduled data flows |

### Discovery & Search

- **Semantic Search**: FAISS embeddings for natural language queries (`"inventory management"`)
- **Fuzzy Search**: Text matching for specific names (`"get-orderpoints"`)
- **Hybrid Mode**: Combines both for best results
- **Requirements-based**: Auto-discover APIs/actions from `.md` requirements files

### Workspace Management

- **Hierarchical Structure**: Environments → Agents → Actions / Pipelines
- **Multi-Environment Support**: Separate staging/production, multiple clients
- **Config Inheritance**: Action inherits from Agent inherits from Environment
- **Profile Management**: Centralized `adopt_profile.json` for base URLs, security params
- **Pipeline Workspaces**: Dedicated `pipelines/` directory within each environment

### Pipeline & Connector Management

- **Data Pipelines**: Create, test, and deploy ETL / data sync workflows
- **Pipeline-Specific Operations**: `READ_FROM_DB`, `WRITE_TO_DB`, `FAN_OUT`, `RUN_ACTION`
- **Connectors**: Manage pipeline data sources/destinations (S3, databases, REST APIs)
- **Connector Catalog**: Browse available providers, create instances, test connections
- **Test Mode**: Rapid iteration with `test_mode=true` (no concurrent-run checks)

### Testing & Validation

- **Compilation**: JSON syntax + WDL structure compilation (`--compile`) — MANDATORY before testing
- **Direct WDL Execution**: Test local `widdle.json` via /run-wdl without saving (default mode)
- **Remote Action Testing**: Test saved remote action with `--remote` flag
- **Test Cases**: JSON-based test cases with expected output validation
- **Parallel Testing**: Test multiple actions simultaneously (`--parallel N`)
- **Batch Testing**: Test all actions in workspace or agent (`--workspace`, `--agent`)
- **Via-Agent Testing**: Test sub-actions through parent orchestrator

### Version Management

- **Draft Support**: Save drafts after testing, publish when ready
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
git clone https://github.com/adoptai/abcd.git abcd
cd abcd

# Install dependencies
poetry install --no-root

# Copy environment template to default workspace
cp dev.env workspaces/default/.env

# Configure your credentials in workspaces/default/.env
```

## Development Setup

The `dev` dependency group contains linting, type-checking, and git-hook tools. It is **not installed by default** — you must opt in explicitly.

### Install dev dependencies

There are two optional groups:

| Group | Install command | Contains |
|-------|----------------|---------|
| `lint` | `poetry install --no-root --only lint` | `ruff`, `mypy`, `types-requests` — minimal, used by CI |
| `dev` | `poetry install --no-root --with dev` | Everything in `lint` + `pre-commit` for git hooks |

For local development (git hooks + checks):

```bash
poetry install --no-root --with dev
```

### Enable pre-commit hooks

After installing dev dependencies, install the git hooks once:

```bash
poetry run pre-commit install
```

From that point on, every `git commit` will automatically run `ruff` (lint + format check) and `mypy` against staged files. To run the hooks manually across the whole codebase:

```bash
poetry run pre-commit run --all-files
```

### Run checks manually

Use the `local_checks.sh` script (mirrors CI behaviour):

```bash
# Run ruff linter only
bash local_checks.sh --run-ruff-check

# Run ruff formatter check only
bash local_checks.sh --run-ruff-format

# Run mypy type checks only
bash local_checks.sh --run-mypy

# Run everything at once
bash local_checks.sh --run-all
```

Or invoke the tools directly through Poetry:

```bash
# Lint (and auto-fix)
poetry run ruff check --fix cli/

# Format
poetry run ruff format cli/

# Type check
poetry run mypy cli/
```

The same checks run automatically on every pull request via the GitHub Actions workflow at `.github/workflows/lint.yml`.

---

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
    ├─ "Create a data pipeline / ETL"
    │   → python cli/workspace.py pipeline create --name "Name"
    │   → Edit widdle.json → python cli/test_pipeline.py <id>
    │
    ├─ "Set up connectors (S3, database, etc.)"
    │   → python cli/workspace.py connector catalog
    │   → python cli/workspace.py connector create --name "..." --provider <id> --mode source
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
# Compile WDL (MANDATORY before testing)
python cli/test_runner.py {workflow_id} --compile

# Direct WDL test (no save needed — default mode)
python cli/test_runner.py {workflow_id}

# Test saved remote action (requires save_wdl_draft first)
python cli/test_runner.py {workflow_id} --remote

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

### Chrome Extension (CE) Testing

Production validation by testing agents as end users experience them — through the Adopt Chrome Extension copilot.

**First-time setup:**

```bash
# Check prerequisites (Chrome, Playwright)
python cli/ce_test.py setup
```

**One-time agent setup:**

```bash
# Pick playground profile and target URL (saved to ce_test_cases/ce_config.json)
python cli/ce_test.py configure my-agent

# Generate test cases from agent metadata
python cli/ce_test.py generate my-agent
```

**Testing workflow (every run):**

```bash
# Terminal 1: Launch Chrome with extension (blocking — Ctrl+C to stop)
python cli/ce_test.py start my-agent

# Terminal 2: Run tests (profile auto-selected)
python cli/ce_test.py run my-agent

# Run specific test(s)
python cli/ce_test.py run my-agent --test 1,3

# Send ad-hoc query
python cli/ce_test.py send my-agent "What can you do?"
```

Config is stored in `workspaces/{env}/agents/{agent}/ce_test_cases/ce_config.json`.
Test cases are in `ce_test_cases/ce_test_suite.json`.
Results are saved to `workspaces/{env}/agents/{agent}/traces/ce_results/`.

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

### Pipeline Commands

```bash
# Create pipeline (remote + local workspace)
python cli/workspace.py pipeline create --name "My Pipeline" --description "..."

# List / show pipelines
python cli/workspace.py pipeline list
python cli/workspace.py pipeline show <pipeline-id>

# Download existing pipeline from platform
python cli/workspace.py pipeline checkout --remote-id <uuid>

# Test pipeline WDL (always test_mode=true)
python cli/test_pipeline.py <pipeline-id>

# Save pipeline draft to remote
python cli/save_pipeline_draft.py <pipeline-id> [--activate]
```

### Connector Commands

```bash
# Browse available connector providers
python cli/workspace.py connector catalog [--mode source|destination]

# Create / manage connectors
python cli/workspace.py connector create --name "S3 Source" --provider amazon_s3 --mode source
python cli/workspace.py connector list
python cli/workspace.py connector test <connector-id>
python cli/workspace.py connector delete <connector-id> --force
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
│   ├── ce_test.py                # Chrome Extension agent testing
│   ├── test_pipeline.py          # Pipeline WDL testing
│   ├── save_pipeline_draft.py    # Pipeline draft save/publish
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
│   │   ├── PIPELINE_WORKFLOW_PROMPT.md
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
4. **Compile** (MANDATORY): `python cli/test_runner.py <id> --compile`
5. **Test**: `python cli/test_runner.py <id>`
6. **Save draft**: `python cli/save_wdl_draft.py --workflow-id <id>`
7. **Publish**: `python cli/publish_wdl_action.py <id>`

### For Complex WDL Workflow Creation

1. **Read** `prompts/system/CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md`
2. **Discover** tools/APIs: `python cli/discover.py --requirements requirements.md`
3. **Create** workspace: `python cli/manage_wdl_action.py --create -r requirements.md -t "Title"`
4. **Generate** WDL following roaming instructions
5. **Create 3 test cases** in `test_cases/` directory
6. **Compile** (MANDATORY): `python cli/test_runner.py <id> --compile`
7. **Save draft**: `python cli/save_wdl_draft.py --workflow-id <id>`
8. **Test remotely**: `python cli/test_runner.py <id> --all`
9. **Iterate** until all tests pass
10. **Publish** when user confirms: `python cli/publish_wdl_action.py <id>`
11. **CE Test** (optional): `python cli/ce_test.py start <agent>` + `run <agent>` — validate in Chrome Extension

### For Data Pipeline Creation

1. **Read** `prompts/system/PIPELINE_WORKFLOW_PROMPT.md`
2. **Set up connectors** (if needed): `python cli/workspace.py connector catalog` → `connector create`
3. **Create** pipeline: `python cli/workspace.py pipeline create --name "Name"`
4. **Edit** `widdle.json` with pipeline operations (`READ_FROM_DB`, `WRITE_TO_DB`, `FAN_OUT`)
5. **Test**: `python cli/test_pipeline.py <id>`
6. **Iterate** until tests pass
7. **Save**: `python cli/save_pipeline_draft.py <id>` → optionally `--activate`

## Key Concepts

### WDL (Workflow Definition Language)

WDL defines multi-step workflows with operations like:
- `REST` - HTTP API calls
- `JQ_FILTER` - JSON transformations
- `EXTRACT` / `PROJECT` - Data extraction
- `PROMPT` - LLM calls
- `CONDITION` - Conditional branching
- `OUTPUT_TEXT` / `OUTPUT_TABLE` - Output formatting
- `READ_FROM_DB` / `WRITE_TO_DB` - Pipeline table operations
- `FAN_OUT` - Batch iteration over rows (pipelines)
- `RUN_ACTION` - Invoke published actions (pipelines)

### Workspaces

Workflows are organized in workspaces:
- **Standalone**: Created in `workspaces/{env}/actions/` directory
- **Agent-based**: Created in `workspaces/{env}/agents/{agent-name}/actions/`
- **Pipelines**: Created in `workspaces/{env}/pipelines/` directory

### Version Management

- Every saved draft creates a version
- Versions can be checked out and tested
- All versions stored locally in `versions/` folder
- Publish makes a version live

## Documentation

- **Agent Instructions**: See `agents.md`
- **WDL Workflow Guide**: See `prompts/system/CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md`
- **Pipeline Guide**: See `prompts/system/PIPELINE_WORKFLOW_PROMPT.md`
- **Diagnostic Guide**: See `prompts/system/DIAGNOSE_AND_FIX_SYSTEM_PROMPT.md`
- **Issue Patterns**: See `prompts/guidelines/WDL_ISSUE_PATTERNS.md`
- **CLI Help**: `python cli/[script_name].py --help`

## License

See [LICENSE](LICENSE) file.
