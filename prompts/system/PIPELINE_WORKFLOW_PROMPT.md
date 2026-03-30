# Pipeline Workflow System Prompt

**PURPOSE**: Comprehensive guide for creating, testing, and managing data pipelines in ABCD.

**LOAD THIS WHEN**: The user asks to create, edit, test, or manage a **pipeline** (data pipeline, ETL, data sync). Pipelines are DIFFERENT from actions -- they operate on data sources/destinations and use pipeline-specific WDL operations.

---

## Pipelines vs Actions

| Aspect | Actions | Pipelines |
|--------|---------|-----------|
| **Purpose** | User-triggered workflows (API wrappers, agents) | Data-driven ETL / sync jobs |
| **Trigger** | User prompt / orchestrator | Manual, scheduled (cron), or external |
| **Data model** | `db_org_action_library` | `db_org_pipeline` |
| **Test endpoint** | `/v1/actions/run-wdl` | `/v1/pipelines/workflows/test-run` |
| **Connectors** | Integrations (mail, drive, hubspot) | Connectors (S3, databases, REST APIs) |
| **Table registry** | N/A | Pipeline tables (`READ_FROM_DB`, `WRITE_TO_DB`) |
| **Workspace location** | `workspaces/{env}/actions/` or `agents/{agent}/actions/` | `workspaces/{env}/pipelines/` |
| **WDL format** | Same JSON array of operations | Same JSON array, but with pipeline-specific operations |

**Key insight**: Pipelines and actions share the WDL operation language but are completely separate systems with separate APIs, databases, and lifecycles.

---

## Pipeline-Specific WDL Operations

Pipelines can use all standard WDL operations (REST, EXTRACT, PROMPT, JQ_FILTER, etc.) PLUS these pipeline-specific operations:

### READ_FROM_DB
Read rows from a pipeline table.
```json
{
  "id": "readContacts",
  "operation": "READ_FROM_DB",
  "table_label": "contacts",
  "fields": ["name", "email", "company"],
  "filter": { "status": "active" }
}
```

### WRITE_TO_DB
Write/upsert rows to a pipeline table. Tables are auto-created on first write.
```json
{
  "id": "saveEnriched",
  "operation": "WRITE_TO_DB",
  "table_label": "enriched_contacts",
  "input": "enrichResult",
  "fields": ["name", "email", "company", "linkedin_url", "enriched_at"]
}
```

### FAN_OUT
Iterate over a collection with sub-steps. Used for batch processing rows.
```json
{
  "id": "processEach",
  "operation": "FAN_OUT",
  "input": "readContacts",
  "batch_size": 10,
  "sub_steps": [
    {
      "id": "enrichOne",
      "operation": "REST",
      "method": "GET",
      "url": "/api/enrich?email={item.email}"
    },
    {
      "id": "saveOne",
      "operation": "WRITE_TO_DB",
      "table_label": "enriched_contacts",
      "input": "enrichOne"
    }
  ]
}
```

**FAN_OUT restrictions** -- these operations are NOT allowed inside `sub_steps`:
- `JUMP`
- Nested `FAN_OUT`
- `ESCALATE`
- `ASK_USER_FOR_INPUT` / `CONVERSATIONAL_INPUT`
- `EMBEDDER`
- `READ_FROM_DB` / `WRITE_TO_DB`

### RUN_ACTION
Call a published action by its remote action_id.
```json
{
  "id": "runEnrichment",
  "operation": "RUN_ACTION",
  "action_id": "abc123-action-uuid",
  "input": "contactData"
}
```

### S3_READ
Read from an S3 connector source.

### OUTLOOK
Read emails via an Outlook connector.

### EMBEDDER
Generate embeddings for vector operations.

---

## Table Registry

Pipelines use a **table registry** for persistent data:

- `table_label`: A human-readable name, unique per pipeline (e.g., `"contacts"`, `"enriched_data"`, `"sync_log"`)
- WDL uses `table_label` in `READ_FROM_DB` and `WRITE_TO_DB` operations
- The platform resolves `table_label` to physical table names automatically
- Tables are **auto-created** on the first `WRITE_TO_DB`
- Consistent `table_label` values across operations connect the data flow

---

## Connectors (Source & Destination)

Connectors are pipeline-specific data sources and destinations (S3, databases, REST APIs). They are completely separate from integrations (which are for actions).

### Managing Connectors

```bash
# Browse available connector providers
python cli/workspace.py connector catalog
python cli/workspace.py connector catalog --mode source

# List org connectors
python cli/workspace.py connector list

# Create a connector
python cli/workspace.py connector create \
  --name "Production S3 Bucket" \
  --provider amazon_s3 \
  --mode source \
  --config '{"bucket": "my-data", "region": "us-east-1"}' \
  --credentials '{"access_key_id": "...", "secret_access_key": "..."}'

# Test connector
python cli/workspace.py connector test <connector-id>

# Show details
python cli/workspace.py connector show <connector-id>

# Update / delete
python cli/workspace.py connector update <id> --name "New Name"
python cli/workspace.py connector delete <id> --force
```

### Using Connectors in Pipeline WDL

Connectors are linked to pipelines as sources and destinations during pipeline creation. The pipeline WDL can then read from the source connector and write to destination connectors.

---

## Pipeline Lifecycle in ABCD

### Step 1: Set Up Connectors (if needed)

```bash
# List available provider types
python cli/workspace.py connector catalog

# Create source/destination connectors
python cli/workspace.py connector create --name "My S3" --provider amazon_s3 --mode source \
  --credentials-file s3_creds.json

# Test the connection
python cli/workspace.py connector test <connector-id>
```

### Step 2: Create Pipeline

```bash
# Create pipeline (creates both remote + local workspace)
python cli/workspace.py pipeline create \
  --name "Contact Enrichment Pipeline" \
  --description "Reads contacts from S3, enriches via API, writes to DB" \
  --source '{"integration_id": "<connector-id>"}' \
  --destinations '[{"type": "internal_data_store"}]'
```

This creates:
- A remote pipeline on the platform (with a dummy prompt for initial WDL generation)
- A local workspace at `workspaces/{env}/pipelines/{pipeline-id}/`

### Step 3: Edit Pipeline WDL

Edit `workspaces/{env}/pipelines/{pipeline-id}/widdle.json` directly.

**IMPORTANT**: Before editing pipeline WDL, fetch operation docs from the WDL documentation:
- Index: `https://adoptai.github.io/widdle_docs/operations/index.md`
- Individual ops: `https://adoptai.github.io/widdle_docs/operations/{OPERATION}_OPERATION_DESCRIPTION.md`

Pipeline WDL does NOT use `required_inputs` (pipelines are data-driven, not prompt-driven).

### Step 4: Test Pipeline

```bash
# Test pipeline WDL directly (test_mode=true, no draft needed)
python cli/test_pipeline.py my-pipeline

# With timeout
python cli/test_pipeline.py my-pipeline --timeout 600

# With explicit remote ID
python cli/test_pipeline.py --pipeline-id <remote-uuid> --wdl-file widdle.json
```

Testing sends the local WDL to `/v1/pipelines/workflows/test-run` with `test_mode=true`. This is a development test -- no concurrent-run checks, designed for rapid iteration.

**Known gap**: Test-run results are delivered via Pusher (WebSocket channel `conversation_<pipeline_id>`), not HTTP polling. ABCD dispatches the test run successfully but cannot yet poll for completion. Check results in the platform UI, then persist status via `POST /v1/pipelines/{id}/test-run-status`.

Test results (traces) are saved to `pipelines/{id}/traces/`.

### Step 5: Save Pipeline Draft

```bash
# Save to remote (create draft -> overwrite with local WDL -> publish)
python cli/save_pipeline_draft.py my-pipeline

# Save and activate (requires test to have passed)
python cli/save_pipeline_draft.py my-pipeline --activate

# Dry run
python cli/save_pipeline_draft.py my-pipeline --dry-run
```

The save workflow:
1. Creates a draft with an auto-generated prompt (required by API)
2. Waits for server-side WDL generation to complete
3. Overwrites with your local WDL (no LLM since no prompt change)
4. Publishes the draft
5. Optionally activates the pipeline (state -> running)

### Step 6: Iterate

```
Edit widdle.json -> test_pipeline.py -> fix issues -> test again -> save_pipeline_draft.py
```

---

## Pipeline Workspace Structure

```
workspaces/{env}/pipelines/{pipeline-id}/
├── widdle.json          # Pipeline WDL (your main edit target)
├── metadata.json        # Pipeline metadata (remote ID, state, etc.)
├── test_cases/          # Test cases for the pipeline
├── traces/              # Test run traces
└── versions/            # Saved WDL versions
```

### metadata.json Fields

```json
{
  "pipeline_id": "uuid-from-platform",
  "local_id": "my-pipeline",
  "name": "Contact Enrichment Pipeline",
  "description": "...",
  "type": "pipeline",
  "env_name": "staging",
  "state": "draft",
  "version_id": "...",
  "schedule_type": "manual",
  "source": { "integration_id": "...", "integration_type": "s3" },
  "destinations": [{ "type": "internal_data_store" }],
  "last_test_run_status": "passed"
}
```

---

## Pipeline CLI Commands Reference

### Pipeline Management

```bash
# Create pipeline (remote + local)
python cli/workspace.py pipeline create --name "Name" [--description "..."] [--source '...'] [--env env-id]

# List local pipelines
python cli/workspace.py pipeline list [--env env-id]

# List remote pipelines
python cli/workspace.py pipeline list --remote [--state draft] [--search "query"]

# Show pipeline details
python cli/workspace.py pipeline show <pipeline-id> [--env env-id]

# Download existing pipeline from platform
python cli/workspace.py pipeline checkout --remote-id <uuid> [--env env-id] [--id local-name]

# Delete pipeline
python cli/workspace.py pipeline delete <pipeline-id> [--remote] [--force]
```

### Pipeline Testing

```bash
# Test pipeline WDL
python cli/test_pipeline.py <pipeline-id> [--timeout 300] [--env env-id]
```

### Pipeline Saving

```bash
# Save pipeline WDL to remote
python cli/save_pipeline_draft.py <pipeline-id> [--activate] [--dry-run]
```

### Connector Management

```bash
# List connector providers
python cli/workspace.py connector catalog [--mode source|destination]

# List org connectors
python cli/workspace.py connector list [--mode source|destination] [--provider <id>]

# Show connector details
python cli/workspace.py connector show <connector-id>

# Create connector
python cli/workspace.py connector create --name "Name" --provider <provider-id> --mode source \
  [--config '{}'] [--credentials '{}'] [--config-file config.json] [--credentials-file creds.json]

# Update connector
python cli/workspace.py connector update <connector-id> [--name "..."] [--config '{}']

# Delete connector
python cli/workspace.py connector delete <connector-id> [--force]

# Test connector connection
python cli/workspace.py connector test <connector-id>
```

---

## Example: Complete Pipeline WDL

```json
[
  {
    "id": "readSource",
    "operation": "READ_FROM_DB",
    "table_label": "raw_contacts",
    "fields": ["name", "email", "company"]
  },
  {
    "id": "enrichBatch",
    "operation": "FAN_OUT",
    "input": "readSource",
    "batch_size": 5,
    "sub_steps": [
      {
        "id": "callEnrichAPI",
        "operation": "REST",
        "method": "GET",
        "url": "https://api.enrichment.com/v1/lookup?email={item.email}",
        "headers": { "Authorization": "Bearer {workflow_arguments.api_key}" }
      },
      {
        "id": "extractLinkedIn",
        "operation": "EXTRACT",
        "input": "callEnrichAPI",
        "field": "linkedin_url"
      }
    ]
  },
  {
    "id": "writeToDB",
    "operation": "WRITE_TO_DB",
    "table_label": "enriched_contacts",
    "input": "enrichBatch",
    "fields": ["name", "email", "company", "linkedin_url", "enriched_at"]
  },
  {
    "id": "summarize",
    "operation": "PROMPT",
    "system_prompt": "Summarize the enrichment results",
    "input": "writeToDB",
    "preferred_llm": "claude-sonnet-4-5"
  },
  {
    "id": "output",
    "operation": "OUTPUT_TEXT",
    "format_string": "Enrichment complete. {}",
    "values": ["summarize"],
    "raw": true
  }
]
```

---

## Key Differences from Action WDL

1. **No `required_inputs`**: Pipelines are data-driven, not prompt-driven
2. **Table operations**: `READ_FROM_DB` and `WRITE_TO_DB` with `table_label`
3. **`FAN_OUT`**: Batch iteration over rows with restricted sub_steps
4. **`RUN_ACTION`**: Can invoke published actions by UUID
5. **Connectors**: Pipeline-specific data sources/destinations (not integrations)
6. **Test endpoint**: Uses `/v1/pipelines/workflows/test-run` (always `test_mode=true`)
7. **Draft system**: POST requires prompt (workaround: auto-generated), PUT accepts raw WDL

---

## Checklist for Pipeline WDL

- [ ] Valid JSON array of operations
- [ ] Each operation has a unique `id`
- [ ] Consistent `table_label` values across `READ_FROM_DB` and `WRITE_TO_DB`
- [ ] No `required_inputs` block (pipelines are data-driven)
- [ ] `FAN_OUT.sub_steps` do not contain forbidden operations
- [ ] All `RUN_ACTION` references use valid published action UUIDs
- [ ] Save WDL to `widdle.json` in the pipeline workspace
