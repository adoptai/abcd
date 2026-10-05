# Pipeline connectors

## What they are

A **pipeline connector** is a configured data source or destination that only
pipelines use: an S3 bucket, a database, a REST source, and so on. Each one has:

- a **provider** from the pipeline connector catalog (for example `amazon_s3`), and
- an **instance** (ID plus encrypted credentials), created by a user in the platform's
  pipeline connector UI.

They are **not** Actions-system integrations. Integrations such as mail, drive or CRM
are what action tools and `TOOL_EXECUTION` use. Pipelines and actions have separate
catalogs, separate credential storage and separate APIs. An integration ID will not
work as a pipeline connector ID, and the reverse is also true.

## How a pipeline references a connector

| Where | Field | Notes |
|---|---|---|
| Pipeline source (`pipeline.json`, set at create) | `manage_pipeline.py --source-type connector --source-connector-id ID --source-connector-type PROVIDER --source-connector-name NAME` | `--source-type internal` (the default) uses the internal data store. `salesforce` uses the built-in Salesforce source. |
| Pipeline destination | `--destination-connector-id ID [--destination-connector-type T] [--destination-label L]` | When you leave it out, the destination is the internal data store with label `results`. |
| WDL steps | `READ_FROM_DB` / `WRITE_TO_DB` → `connector_id` | `"internal_data_store"` (or leaving it out) means the default store. |

`workspace.py pipeline create` accepts the same `--source-*` flags but not the
destination ones.

To change the source or destination after creation, don't hand-edit `pipeline.json`.
Ask the user, or recreate the workspace with the right flags and copy the
`widdle.json` over.

## Credentials

- They are created and stored **only** on the platform, encrypted.
- abcd has **no CLI to create, edit or delete** connector instances or their
  credentials. Send the user to the platform UI.
- Never put connector credentials in `widdle.json`, `pipeline.json`, `.env` comments,
  or test fixtures.

## Finding a connector ID

Ask the user first, or have them copy it from the platform UI. If they want you to
look it up, the repo's pipeline client has **read-only** list helpers. It uses the
active env's credentials, which is the same path the CLI scripts use:

```bash
python -c "from cli.wdl_common.pipeline_client import get_pipeline_client as g; import json; print(json.dumps(g().list_connectors(), indent=2))"
python -c "from cli.wdl_common.pipeline_client import get_pipeline_client as g; import json; print(json.dumps(g().list_connector_catalog(), indent=2))"
```

`list_connectors(mode=None, provider_id=None, search=None)` lists instances
(`GET /v1/pipeline-connectors`). `list_connector_catalog(mode=None)` lists providers.
This is a lookup only. Don't script writes against the API.
