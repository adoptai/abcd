---
name: action-builder
description: "Build, test, save and publish WDL actions on the Adopt platform from an abcd checkout: simple API-wrapper tools, multi-step workflows, uber agents (PROMPT_AND_TOOLS_AGENT) with sub-actions, and lambdas. Drives the abcd CLI lifecycle: discover, create, edit widdle.json, compile, test (direct, remote, inline), save drafts (incl. the uber-agent double save), publish, Chrome-extension e2e, and diagnose, fix or roll back. Use when the user says create an action, build a tool for this API, wrap this endpoint, fix this widdle.json, build an uber agent, add a sub-action, test my action, save the draft, publish the action, check out an existing action, agent not visible in the Chrome extension, invalid tool name, 504 on run-wdl, diagnose and fix tools, or create a lambda."
---

# action-builder

Author WDL **actions** (chat-triggered tools and agents) with the abcd CLI. This skill
runs from the root of an abcd checkout. Run every command as `python cli/<script>.py ...`
(or `poetry run python cli/<script>.py ...` when the venv is not active).

Scope: actions and uber agents. For data-sync/ETL **pipelines**, use the
`pipeline-builder` skill. To turn a live website's traffic into tools, use the
`discover-and-plan` skill (NoUI capture through Tabby). Do not hand-author WDL from
HAR files or network captures.

## Hard rules (from `agents.md`)

1. **Always use the CLI scripts.** Never call AdoptAI APIs directly, never create
   workspace dirs by hand, never hand-edit `metadata.json`, never guess endpoints
   (use `cli/discover.py`). If no CLI covers what you need, ask the user first.
2. **`widdle.json` is yours to edit.** It is the only file you write by hand.
3. **Read the docs before you edit an operation.** Fetch
   `https://adoptai.github.io/widdle_docs/operations/index.md`, then each
   `{OPERATION}_OPERATION_DESCRIPTION.md` you plan to use. Pay attention to defaults
   (for example JQ_FILTER `extract_all`).
4. **Compile after every edit** with `python cli/test_runner.py <id> --compile`. This
   is mandatory.
5. **Test before you save.** Save only after the tests pass. Publish only after the
   user confirms.
6. **No secrets in WDL.** Use `{security_params.NAME}` for credentials and
   `{workflow_arguments.NAME}` for caller inputs. See
   `prompts/guidelines/SANDBOX_LAMBDA_EXECUTION_GUIDE.md` §2.
7. **Non-interactive.** Pass `--yes` to publish commands once the user has approved.
   Pass `--dry-run` when you want a preview.
8. **Start with a TODO list.** Include env validation, requirements, discovery, WDL,
   tests, and publish if requested.

## Step 0: Workspace and environment

Every command uses the **active environment** (`workspaces/.active_env`). Its
credentials are in `workspaces/<env>/.env` and its base URL and security params are
in `adopt_profile.json`.

```bash
python cli/workspace.py env list            # active env marked with a check
python cli/workspace.py env show <env-id>   # read its description
python cli/workspace.py env use <env-id>    # switch
```

- Compare the request with the active env's `env.json` description. If they don't
  match (for example an inventory tool in a marketing env), tell the user and offer
  three options: proceed, switch, or create a new env.
- New env: `python cli/workspace.py env create --id acme-staging --name "Acme - Staging" --target staging --client acme --use`,
  then ask the user to fill in `ADOPT_CLIENT_ID` / `ADOPT_CLIENT_SECRET` in
  `workspaces/acme-staging/.env`. Full procedure:
  `prompts/system/REMOTE_ACTION_WORKFLOW_PROMPT.md`.
- Config inheritance: action `adopt_profile.json` → agent → environment
  (`python cli/workspace.py profile show --action <id>`). Details:
  `prompts/system/WORKSPACE_HIERARCHY_PROMPT.md`.
- `test_runner.py`, `save_wdl_draft.py` and `publish_wdl_action.py` accept `--env`
  to target another env for one run. Most other scripts only use the active env.

Layout:

```
workspaces/<env>/
├── actions/<action>/            standalone actions
├── agents/<agent>/widdle.json   uber agent (PROMPT_AND_TOOLS_AGENT)
│   └── actions/<sub-action>/    its sub-actions
└── lambdas/<name>/              shared lambdas (not under an agent)
<action>/ = widdle.json, metadata.json, apis/, tools/, test_cases/, traces/, versions/
```

## Decide what you're building

| Request | Path |
|---|---|
| Wrap one endpoint (REST → optional transform → OUTPUT_TEXT) | Simple action: `--template simple`, guide `prompts/templates/simple_tool_template.md` |
| Several steps, LLM steps, branching, pagination | WDL workflow: `prompts/system/CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md` |
| Chat agent that picks among tools | Uber agent: [references/uber-agents.md](references/uber-agents.md) |
| Edit an action that already exists remotely | Checkout flow below, plus `prompts/system/REMOTE_ACTION_WORKFLOW_PROMPT.md` |
| Custom Python code or container execution | [references/lambdas-and-sandbox.md](references/lambdas-and-sandbox.md) |
| Scheduled or triggered data sync that writes tables | Use the `pipeline-builder` skill instead |

Don't build one 30+ operation WDL. Build small atomic tools and compose them with an
uber agent.

## Lifecycle

### 1. Discover

```bash
python cli/discover.py --requirements requirements.md      # semantic, from a requirements file
python cli/discover.py --apis "invoice list"               # search APIs
python cli/discover.py --actions "get-invoices" --mode fuzzy
python cli/discover.py --list-all                          # every action, incl. hidden sub-actions
python cli/discover.py --list-uber-agents
```

Modes are `semantic`, `fuzzy` and `hybrid` (the default). Add `--tools-only`,
`--details`, `--json` or `--refresh` (rebuild the per-env cache) as needed.

### 2. Create the workspace (local only)

```bash
python cli/manage_wdl_action.py --create --template simple -t "get-invoice" --use-api <api-id>
python cli/manage_wdl_action.py --create -r requirements.md -t "Invoice Review" --agent acme-agent
python cli/manage_wdl_action.py --create -r requirements.md -t "Invoice Review" --standalone
# add context to an existing workspace
python cli/manage_wdl_action.py --workflow-id invoice-review --use-api <api-id> --use-tool <tool-id>
```

`--use-api` and `--use-tool` copy specs into `apis/` and `tools/`. **`apis/` is the
only source of truth for endpoint schemas.** No remote action is created until
the first save, unless you pass `--create-remote`.

**Existing remote action:** run `python cli/discover.py --list-all` to find the ID,
then check it out:
- uber agent with sub-actions: `python cli/workspace.py agent checkout --remote-id <id> --env <env> --include-subactions`
- bulk: `python cli/workspace.py action checkout-all --env <env> [--uber-agents-only --include-subactions]`
- single action or version: `python cli/checkout_wdl_version.py --workflow-id <id> --version N`.
  This script has a known defect on this branch. See
  [references/cli-reference.md](references/cli-reference.md#known-cli-defects).

### 3. Edit `widdle.json`

- A JSON array of steps. Each step has a unique `id` and an `operation`. Steps
  reference earlier outputs as `{stepId}` / `{stepId.field}` and inputs as
  `{workflow_arguments.x}`.
- Put a `required_inputs` block first for simple tools. For a tool that an uber agent
  will call, `required_inputs` must be a **list of JSON strings**, not a dict.
- Look at the templates in `prompts/templates/`: `simple_tool_template.json`,
  `complex_workflow_template.json`, `uber_agent_template.json`.
- Known failure shapes and their fixes: `prompts/guidelines/WDL_ISSUE_PATTERNS.md`.

Minimal simple tool:

```json
[
  {"required_inputs": {"invoiceId": {"type": "string", "definition": "Invoice ID"}}},
  {"id": "getInvoice", "operation": "REST", "method": "GET",
   "url": "/api/v1/invoices/{workflow_arguments.invoiceId}"},
  {"id": "out", "operation": "OUTPUT_TEXT", "format_string": "{}", "values": ["getInvoice"], "raw": true}
]
```

### 4. Write test cases (before the first run)

Write three files in `test_cases/`: `test_1.json` (common case), `test_2.json`
(different inputs) and `test_3.json` (edge case). Each has a `prompt`,
`workflow_params` and `expected_output` (`validation`: `similarity` (judged by you,
the LLM), `contains` or `exact`; plus `key_fields`). Multi-turn tests use `turns: [...]`.
Format: [references/testing.md](references/testing.md).

### 5. Compile and test

```bash
python cli/test_runner.py <id> --compile          # MANDATORY: JSON syntax + remote compiler
python cli/test_runner.py <id>                    # run local widdle.json via /run-wdl (no save needed)
python cli/test_runner.py <id> --test test_2.json # filename only, NOT test_cases/test_2.json
python cli/test_runner.py <id> --all --verbose    # all cases, with WDL ops + traces
python cli/validate.py <id> [--auto-fix] [--orchestrator]
```

Loop: edit, compile, test, until every case passes. Traces are saved under `traces/`.
`--remote` tests the **saved** remote action and needs a prior save. `--inline` tests
an uber agent with its local sub-actions. See
[references/uber-agents.md](references/uber-agents.md).

**CloudFront ~120 s limit.** Every API call goes through a hard ~120 s gateway
timeout, and a 504 means the turn was too long. It can't be fixed from the client.
Budget about **3 tool calls per turn via `--remote`** and **about 2 via the default
`/run-wdl`**. Four or more always 504. Split tests into smaller turns or `--multi-turn`.
Details are in [references/testing.md](references/testing.md#cloudfront-120-s-gateway-limit).

### 6. Save the draft (only after the tests pass)

```bash
python cli/save_wdl_draft.py <id> --description "Add pagination"
python cli/save_wdl_draft.py a b c --parallel 3
python cli/save_wdl_draft.py --agent acme-agent           # agent + changed sub-actions
python cli/save_wdl_draft.py --agent acme-agent --force   # agent + ALL sub-actions
```

The first save creates the remote action and links it in `metadata.json`. If the link
is lost, the script recovers it by title. Each save writes `versions/v<N>_widdle.json`
and pushes the statement (selection criteria) from `metadata.json` or the WDL.

**Uber-agent double save.** For a `PROMPT_AND_TOOLS_AGENT`, run `save_wdl_draft.py`
**twice** before publishing. The first save creates a `draft` version. The second
moves it to `pending_approval`. `publish_wdl_action.py` only publishes a
`pending_approval` version and reports "No pending_approval version to publish" if
it can't find one. A single save is enough for regular actions.

### 7. Publish (only after the user confirms)

```bash
python cli/publish_wdl_action.py <id> --yes [--description "v1"] [--version N]
python cli/publish_wdl_action.py --agent acme-agent --yes   # sub-actions first, then agent
python cli/deployment_rules.py <sub-action> --enable-tool-mode   # required for uber-agent sub-tools
python cli/list_wdl_versions.py --workflow-id <id>
```

**Chrome-extension visibility.** The extension reads the platform's entity store,
which is synced at approve time. That sync rejects an action whose metadata fields
are empty, which is common for actions created from the CLI. If a published action
or agent does not appear in the extension, push its metadata with
`python cli/patch_action_metadata.py <id> --title ... --description ... --statement ...`.
With no flags, it syncs the values already in `metadata.json`. Use `--agent <agent>`
for every sub-action. The script updates the local metadata too, so never hand-edit
`metadata.json`. Then save and publish again. For other empty fields (example prompts, tags, summary),
this branch has no CLI. Ask the user rather than calling the API.

### 8. End-to-end through the Chrome extension

```bash
python cli/ce_test.py setup                     # prerequisites (Chrome, extension)
python cli/ce_test.py configure <agent> --profile-id <playground-profile-id>   # one-time
python cli/ce_test.py generate <agent>          # CE test cases from agent metadata
python cli/ce_test.py start <agent>             # Chrome session, run in a SEPARATE terminal (background)
python cli/ce_test.py run <agent> [--test 1,3]
python cli/ce_test.py send <agent> "list my open invoices"   # ad-hoc query
python cli/ce_test.py status
```

`cli/ce_browser.py` is the Python library behind `ce_test.py` (Chrome launch, CDP,
extension boot). It is not a CLI. Remote playground profiles must reference token
configs by name and never contain hardcoded secrets. See
[references/cli-reference.md](references/cli-reference.md#security-headers-and-token-configs).

### 9. Diagnose, fix and roll back

```bash
python cli/diagnose_and_fix.py --scan --format llm --output diagnostics/report.json
python cli/diagnose_and_fix.py --apply-fixes fixes.json --test-after-fix [--dry-run]
python cli/fix_and_test.py <tool-id> --fix-file fix.json --test [--generate-instruction]
python cli/fetch_http_logs.py --search "/invoices"        # compare WDL with real traffic logs
python cli/rollback_changes.py --list
python cli/rollback_changes.py --file diagnostics/rollback_<ts>.json [--only-apis|--only-tools] -y
```

Fix order is always API spec first, then the WDLs that use it. The issue catalogue,
the `fixes.json` format and the extra tools are in
[references/diagnose-and-fix.md](references/diagnose-and-fix.md). Deep reference:
`prompts/system/DIAGNOSE_AND_FIX_SYSTEM_PROMPT.md`.

## Tool-name constraints (uber agents)

- The model API requires tool names that match `^[a-zA-Z0-9_-]{1,128}$`.
- The platform derives tool names from **both** the action `title` and its
  `statement`. Both must be valid and **unique across every sub-action of the agent**.
- Fixes: `python cli/validate.py <id> --orchestrator --auto-fix` locally, and
  `python cli/patch_action_metadata.py <id> --title "get-invoice" --statement "..."` remotely.
- Inline prefix: inlined sub-action IDs must pass the same regex, so the prefix
  has to be `inline__`. Colons are invalid in tool names. On this branch,
  `test_runner.py` still emits `inline::<name>`. If `--inline` fails with a tool-name
  error, report that mismatch. See
  [references/uber-agents.md](references/uber-agents.md#inline-prefix).

## Utilities

| Need | Command |
|---|---|
| Workspace status, sync state | `python cli/status.py <id> [--no-sync]` |
| Re-link a workspace to its remote action | `python cli/reconnect.py <id> --search` or `python cli/reconnect.py <id> <action-id>` |
| Move or copy between envs (ask the user first) | `python cli/move_action.py <id> --from a --to b [--copy] [--agent]` |
| Show or toggle tool mode / visibility | `python cli/deployment_rules.py <id> --show / --visible / --hidden` |

Every verified flag and the known defects: [references/cli-reference.md](references/cli-reference.md).

## Deep-detail prompts (load when needed)

| File | When |
|---|---|
| `prompts/system/CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md` | Writing a new WDL workflow (pre-edit doc checklist, phases) |
| `prompts/system/REMOTE_ACTION_WORKFLOW_PROMPT.md` | Loading and editing existing remote actions, new client env |
| `prompts/system/UBER_AGENT_PROMPT.md` | Uber agent structure, sub-action registration |
| `prompts/system/TESTING_PROMPT.md` | Test strategies, via-agent tests, result format |
| `prompts/system/WORKSPACE_HIERARCHY_PROMPT.md` | Envs, profiles, `profiles_map` |
| `prompts/system/DIAGNOSE_AND_FIX_SYSTEM_PROMPT.md` | Debugging failures |
| `prompts/system/adopt_agent_configuration.md` | Agent execution model |
| `prompts/guidelines/WDL_ISSUE_PATTERNS.md` | Error to fix catalogue, data-flow tracing |
| `prompts/guidelines/SANDBOX_LAMBDA_EXECUTION_GUIDE.md` | EXECUTE_LAMBDA vs SANDBOX, secrets, warm pools |

## Done means

- `--compile` is clean, and all of `test_cases/` pass (every case judged for
  `similarity`).
- The draft is saved with a meaningful `--description`. Uber agents were saved twice.
- Publish and tool mode happened only with the user's approval, and CE e2e was run
  when the user asked for production validation.
- Report the action ID, the version number, and the test evidence (trace paths under
  `traces/`).
