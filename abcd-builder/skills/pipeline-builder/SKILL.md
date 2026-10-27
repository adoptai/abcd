---
name: pipeline-builder
description: "Build, test and publish WDL pipelines (scheduled or manual data-sync and ETL workflows) on the Adopt platform from an abcd checkout. Creates the pipeline workspace, guides widdle.json edits with pipeline operations (READ_FROM_DB, WRITE_TO_DB, FAN_OUT, ESCALATE, S3_READ, RUN_ACTION), pushes drafts that auto-create the remote, runs safe test_mode runs streamed live as NDJSON, publishes and activates, lists pipelines, and wires pipeline connectors (pipeline-only sources and destinations, distinct from Actions integrations). Use when the user says create a pipeline, build a data sync, ETL workflow, write results to a table, fan out over rows, add a HITL escalation, test or stream the pipeline run, publish or activate the pipeline, list or check out pipelines, or use an S3 or database connector as a source."
---

# pipeline-builder

Author WDL **pipelines** with the abcd CLI. A pipeline is a scheduled or triggered
data flow that writes to tables. An action is a chat response, and those belong to
the `action-builder` skill. Run everything from the root of an abcd checkout as
`python cli/<script>.py ...` (or `poetry run python cli/<script>.py ...`).

## Hard rules

1. **Always use the CLI scripts.** Don't call the pipeline API directly, don't
   create workspace dirs by hand, and don't hand-edit `pipeline.json`. Edit only
   `widdle.json`. If no CLI covers a need, ask the user.
2. **Read the operation docs before editing**:
   `https://adoptai.github.io/widdle_docs/operations/index.md`, then each
   `{OP}_OPERATION_DESCRIPTION.md` you'll use. For pipelines that usually means
   READ_FROM_DB, WRITE_TO_DB and FAN_OUT.
3. **Test before publish.** `test_pipeline.py` defaults to `test_mode=true`, which is
   safe. Use `--production` only when the user explicitly wants a real run.
4. **Publish and activate only after the user confirms.** Then pass `--yes`.
5. **No secrets in WDL.** Use connector IDs, `{security_params.X}` and
   `{workflow_arguments.X}`. Never put literal credentials in the WDL.
6. **Never build pipeline WDL from HAR files or network captures.** To turn a
   website's traffic into tools, use the `discover-and-plan` skill.

## Pipeline vs action

| | Pipeline | Action |
|---|---|---|
| Purpose | Data sync / ETL | Chat or agent response |
| Trigger | Schedule (`cron`) or manual run | User message |
| Output | Rows in tables | Text to the user |
| Typical ops | READ_FROM_DB, WRITE_TO_DB, FAN_OUT, ESCALATE, CONDITION, S3_READ, RUN_ACTION | REST, PROMPT, OUTPUT_TEXT, PROMPT_AND_TOOLS_AGENT |
| Sources and credentials | **Pipeline connectors** (own catalog and encrypted creds) | Actions-system integrations |
| Workspace | `workspaces/<env>/pipelines/<id>/` | `workspaces/<env>/actions/` or `agents/` |
| CLI | `manage_pipeline.py`, `save_pipeline_draft.py`, `test_pipeline.py`, `publish_pipeline.py` | `manage_wdl_action.py`, `test_runner.py`, ... |

Pipelines share WDL operations with actions, but they use separate DB tables, API
endpoints, workspace dirs and connector management.

## Step 0: Environment

The pipeline scripts use the **active** env (`workspaces/.active_env`) and have no
`--env` flag. Check it and switch before you start:

```bash
python cli/workspace.py env list
python cli/workspace.py env use <env-id>
```

If the request doesn't fit the env's `env.json` description, ask the user before
going on.

## Lifecycle

```
manage_pipeline --create → edit widdle.json → save_pipeline_draft → test_pipeline (streams) → publish_pipeline [--activate]
```

### 1. Create the workspace (local only)

```bash
python cli/manage_pipeline.py --create -t "Sync Vendor Invoices" \
    -d "Pull vendor invoices nightly and store them" \
    --prompt "Fetch all vendor invoices and store them in the data store"
# with a connector source and a cron schedule
python cli/manage_pipeline.py --create -t "S3 Invoice Ingest" \
    --source-type connector --source-connector-id <connector-id> \
    --source-connector-type amazon_s3 --source-connector-name "Invoices bucket" \
    --destination-label results --schedule-type cron
python cli/manage_pipeline.py --show sync-vendor-invoices [--json]
```

`--id` is derived from the title when you leave it out. `--source-type` is
`internal` (the default), `salesforce` or `connector`. `python cli/workspace.py pipeline create ...`
is equivalent but has no destination or schedule flags.

This creates:

```
workspaces/<env>/pipelines/<id>/
├── pipeline.json   name, prompt, source, destinations, schedule_type, remote_pipeline_id, version_id, state
├── widdle.json     starts as [], and is the file you edit
└── versions/       v<N>_widdle.json snapshots written by save_pipeline_draft.py
```

To work on pipelines that already exist remotely:
`python cli/workspace.py pipeline checkout-all [--state running] [--search "invoice"] [--limit N] [--force] [--dry-run]`.

### 2. Edit `widdle.json`

Typical shape: source (READ_FROM_DB / S3_READ / REST) → transform (JQ_FILTER /
PROMPT) → FAN_OUT per row → WRITE_TO_DB. Operation details, the FAN_OUT batch mode,
WRITE_TO_DB destinations, and an end-to-end skeleton are in
[references/pipeline-wdl.md](references/pipeline-wdl.md).

### 3. Save the draft (auto-creates the remote)

```bash
python cli/save_pipeline_draft.py <id> --description "Add fan-out enrichment"
python cli/save_pipeline_draft.py a b --parallel 2
python cli/save_pipeline_draft.py <id> --dry-run
```

The first push creates the remote pipeline and stores `remote_pipeline_id` and
`version_id` in `pipeline.json`. It sends the WDL straight to the draft endpoint (no
prompt, no LLM) and snapshots `versions/v<N>_widdle.json`. `--legacy-prompt-flow`
forces the old flow (dummy prompt, poll, overwrite). Use it only if the backend
rejects prompt-less drafts.

### 4. Test (streams by default)

```bash
python cli/test_pipeline.py <id>                    # test_mode=true, remote draft, live NDJSON stream
python cli/test_pipeline.py <id> --local-wdl        # send local widdle.json instead of the saved draft
python cli/test_pipeline.py <id> --timeout 900      # wait longer for the final event (default 300 s)
python cli/test_pipeline.py <id> --no-stream        # fire-and-forget: trigger only, print run id
python cli/test_pipeline.py <id> --status           # remote state, last test status, 5 recent runs
python cli/test_pipeline.py <id> --mark-passed      # or --mark-failed, with no new run
python cli/test_pipeline.py <id> --production       # test_mode=false; 409 if a run is active (explicit ask only)
```

In stream mode, the script subscribes to the BFF `GET /stream/<remote_pipeline_id>`
(`application/x-ndjson`) and triggers the run on a background thread so the first
event isn't missed. It prints each step as `Step i/N: <label> [status]` with errors,
then the final result. **It auto-marks the test passed or failed on the platform from
the final status.** Exit code is 0 on pass and 1 on fail, on a trigger error, or when
the stream ends with no final result. Event names, retries, and what to do on a
timeout are in [references/test-and-publish.md](references/test-and-publish.md).

`--local-wdl` lets you iterate without saving. You still need a save first, because a
linked remote pipeline must exist. The script injects a fresh bearer as
`{workflow_arguments.auth_token}`, because `/test-run` doesn't substitute workflow
arguments on the server.

### 5. Publish and activate (after the user confirms)

```bash
python cli/publish_pipeline.py <id> --yes                # mark test passed + publish draft
python cli/publish_pipeline.py <id> --activate --yes     # publish + state=running
python cli/publish_pipeline.py a b --parallel 2 --yes
python cli/publish_pipeline.py <id> --skip-test-mark --yes   # test already marked
python cli/publish_pipeline.py <id> --dry-run
```

Publish marks the test as passed, because the platform requires it, so **you** must
have actually seen a passing run first. Publishing makes the pipeline live.

### 6. List and inspect

```bash
python cli/list_pipelines.py                         # local workspaces
python cli/list_pipelines.py --remote                # merge platform pipelines
python cli/list_pipelines.py --state running --search invoice --json
python cli/harness_runs.py list --source pipeline --range 24h [--status failed] [--json]   # org-wide runs
python cli/harness_runs.py detail --run-id pr:<id>
```

`harness_runs.py` needs the workspace's platform PAT (the agent-harness credentials).
It shows pipeline runs across the whole org, including runs other people started.

## Connectors (pipeline data sources and destinations)

Pipeline connectors are **separate from Actions-system integrations** (mail, drive,
CRM tools used by actions). They have their own catalog (S3, databases, REST, and
more) and their own encrypted credential storage. Rules:

- Connector instances and their credentials are created on the platform, in the
  pipeline connector UI. **abcd has no CLI to create or edit them**, and credentials
  never go into WDL or `pipeline.json`.
- Reference a connector by **ID**: `--source-connector-id` / `--destination-connector-id`
  at create time, and `connector_id` inside READ_FROM_DB / WRITE_TO_DB steps.
  `"internal_data_store"` (or leaving it out) means the default internal store.
- Get the connector ID from the user or from the platform UI.

More detail, including the read-only lookup helper, is in
[references/connectors.md](references/connectors.md).

## Lambdas and sandbox steps

Pipelines can run custom code with `EXECUTE_LAMBDA`, or with `SANDBOX` (cloud only).
Lambdas are authored with `manage_lambda.py`, `save_lambda.py`, `test_lambda.py` and
`lambda_logs.py`. Full workflow:
[../action-builder/references/lambdas-and-sandbox.md](../action-builder/references/lambdas-and-sandbox.md).
Policy, secrets and warm pools: `prompts/guidelines/SANDBOX_LAMBDA_EXECUTION_GUIDE.md`.

## Verified CLI flags

All flags are listed in [references/test-and-publish.md](references/test-and-publish.md#cli-flags).
`workspace.py pipeline` offers `create`, `list`, `show` and `checkout-all`.

## Done means

- `widdle.json` uses only documented operations. Every WRITE_TO_DB `table_label` is
  unique. There are no secrets.
- The draft is saved, and a `test_pipeline.py` run showed a passing final status
  (auto-marked passed).
- Publish (and `--activate`, if asked) ran only after the user confirmed.
- Report the remote pipeline ID, the version, the run ID, and the final streamed
  status.
