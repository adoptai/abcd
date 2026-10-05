# Pipeline WDL

The authoritative docs are online. Fetch `index.md`, then each operation file:
`https://adoptai.github.io/widdle_docs/operations/<FILE>`. This page is a map plus the
rules people most often get wrong. It does not replace those docs.

## Operations you will use most

| Operation | Doc file | Role |
|---|---|---|
| `READ_FROM_DB` | `READ_FROM_DB_OPERATION_DESCRIPTION.md` | Source: read rows via a connector (`connector_id`, `table_label` or `query`, `limit`) |
| `WRITE_TO_DB` | `WRITE_TO_DB_OPERATION_DESCRIPTION.md` | Sink: persist rows (`input`, `table_label`, `table_purpose`, `mode` append\|replace\|upsert) |
| `FAN_OUT` | `FAN_OUT_OPERATION_DESCRIPTION.md` | Scatter-gather: run `sub_steps` per row, then collect the results in order |
| `ESCALATE` | `ESCALATE_OPERATION_DESCRIPTION.md` | Human-in-the-loop / validation gate |
| `CONDITION`, `JUMP`, `END` | `CONDITION_…`, `JUMP_…`, `END_…` | Branching and termination |
| `S3_READ`, `SHAREPOINT_READ`, `GOOGLE_DRIVE_READ`, `OUTLOOK` | `<OP>_OPERATION_DESCRIPTION.md` | File and mail sources |
| `PARSE_DOCUMENT`, `DOCUMENT_EXTRACTOR`, `EMBEDDER` | same pattern | Document parsing, extraction, vectorising |
| `RUN_ACTION` | `RUN_ACTION_OPERATION_DESCRIPTION.md` | Run a published action as a sub-workflow |
| `REST`, `PAGINATION`, `REST_LOOP` | same pattern | External APIs |
| `JQ_FILTER`, `PROMPT`, `EDIT_VALUE`, `MERGE`, `FILTER`, ... | same pattern | Transforms, shared with actions |

Inside FAN_OUT `sub_steps`, pipeline runs (test or live) allow **only
pipeline-allowed operations**. Check the FAN_OUT doc before you nest something
unusual.

## Rules people get wrong

- **WRITE_TO_DB `table_label` is required and unique** across all WRITE_TO_DB steps in
  the WDL (max 64 chars). Never put `table_id` in the WDL, because the system
  allocates it. The physical name is `pipeline_<pipeline_id>_<table_id>`.
- **Same-pipeline reads** use the short `table_label`. **Cross-pipeline reads** set
  `table_label` to the source table's full physical name.
- **Default store:** `connector_id: "internal_data_store"`, or leave it out. Writing to
  the org's Postgres Data Store uses `"db": "org_internal"`: add `table_name` for a typed
  write to an existing table, or leave it out for an auto-created one. Read the
  WRITE_TO_DB doc for the decision table.
- **FAN_OUT input must be an array.** The first sub-step's `input` is the FAN_OUT
  `input` step ID (it receives the row). `"fan_out_row"` always refers to the
  unmodified current row. A PROMPT sub-step returns **only** its `fields`. To combine
  it with the row metadata, use a JQ_FILTER with `inputs: [rowStep, promptStep]`.
- **Large inputs:** set FAN_OUT `batch_size` and `write_step` (the WRITE_TO_DB step
  ID). The executor then reads, processes and writes in batches and never loads
  the whole table. Use it whenever READ_FROM_DB can return many rows.
- **Dict sources:** some sources (for example OUTLOOK) return a dict. Put a JQ_FILTER
  (for example `.emails`) in front of FAN_OUT.
- **Auth:** `test_pipeline.py` injects `{workflow_arguments.auth_token}` (a fresh
  bearer) into test runs. Reference it from REST steps that call the platform, and
  use `{security_params.X}` for third-party secrets.

## Skeleton: read, enrich each row, write

```json
[
  {"id": "read_invoices", "operation": "READ_FROM_DB",
   "connector_id": "internal_data_store", "table_label": "raw_invoices", "limit": 10000},
  {"id": "enrich", "operation": "FAN_OUT", "input": "read_invoices",
   "batch_size": 25, "write_step": "write_enriched",
   "sub_steps": [
     {"id": "row", "operation": "JQ_FILTER", "input": "read_invoices",
      "filter": "{invoice_id: .invoice_id, vendor: .vendor, memo: .memo}", "extract_all": false},
     {"id": "classify", "operation": "PROMPT", "input": "row",
      "instructions": "Classify the memo into one expense category.", "fields": ["category"]},
     {"id": "combine", "operation": "JQ_FILTER", "inputs": ["row", "classify"],
      "filter": "{invoice_id: .row.invoice_id, vendor: .row.vendor, category: .classify.category}",
      "extract_all": false}
   ],
   "notes": "Classify each invoice and keep its keys"},
  {"id": "write_enriched", "operation": "WRITE_TO_DB", "input": "enrich",
   "connector_id": "internal_data_store", "table_label": "enriched_invoices",
   "table_purpose": "Invoices with expense category", "mode": "upsert"}
]
```

Before you save, check each field against the current docs. Field names, such as
PROMPT `instructions` vs `prompt_template`, can differ between operation versions.

## Iterating

1. Edit `widdle.json`.
2. `python cli/test_pipeline.py <id> --local-wdl` runs the local WDL without a save.
   The pipeline must have been saved once so the remote exists.
3. When it's green, run `python cli/save_pipeline_draft.py <id> -d "..."`, then a
   normal `test_pipeline.py <id>` against the saved draft.
4. Publish.
