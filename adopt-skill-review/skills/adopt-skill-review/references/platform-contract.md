# The platform contract — pinned facts

**Verified against `adoptai-workflows` origin/dev @ `63980995`, 2026-09-10.**
Re-check before relying on any number: `scripts/verify_platform_facts.py --repo <path>`.

Local checkouts go stale fast. When this was written, the local `adoptai-workflows` was 332
commits behind `origin/dev`, `adoptwebui` 457, `tabby` 100. Always read from the ref, not
the working tree.

## Limits

| Fact | Value | Where |
|---|---|---|
| LLM steps per turn | **100** | `MAX_TOOL_ITERATIONS` |
| Context clearing fires at | 60% of the model window | `CLEAR_TRIGGER_PCT` |
| In-turn compaction fires at | 80% | `COMPACT_TRIGGER_PCT` |
| Cross-turn compaction at turn start | 70% | `CROSS_TURN_COMPACT_PCT` |
| Recent tool results kept verbatim | 4 | `KEEP_RECENT_TOOL_USES` |
| Recent rounds kept verbatim on compaction | 3 | `KEEP_RECENT_ROUNDS` |
| Default context window | 200,000 tokens | `DEFAULT_CONTEXT_TOKEN_LIMIT` |
| `bash` stdout returned to the model | 100,000 chars, truncated **in the middle** | `BASH_OUTPUT_MAX_CHARS` |
| `bash` command timeout | 20 min | activity config |
| Aux file **read** cap | 256 KiB (truncated; a script over it is **refused**) | `_MAX_SKILL_FILE_BYTES` |
| Skill **upload** cap | 50 MB combined; 100 aux files | upload route |
| Skills per plugin | 50 | upload route |
| Sandbox file-transfer concurrency | **5** | `_GETMANY_CONCURRENCY` |
| Process steps | **1–50**, each needs `id` + `title` | `process.py` Field |
| `db_insert_rows` | 500 rows inline | tool schema |
| `db_insert_rows_from_file` | 5000 rows from a sandbox JSON file | tool schema |
| `/memories` | 32 files, 2 MB total, 256 KiB/file, 20k-char view | `memory_store.py` |
| Workstream staging session budget | 200 MB, shared between eager staging and `ws_read` | `workstream_staging.py` |
| Sandbox lease | 30 min initial, keepalive every 120s, ~10 min idle grace | sandbox config |
| `render_ui` schema union | all 65 schemas, every turn | `GENUI_FULL_UNION = True` |

Some larger models register a 1,000,000-token window (`claude-sonnet-5`,
`claude-sonnet-4-6`). The clearing and compaction thresholds are **percentages**, so on a 1M
model they fire five times later in absolute tokens — worth checking which model an org runs
before diagnosing a compaction problem.

## Tool inventory and per-turn gates

50 tools, but a skill body must never assume a tool is present. Tools come and go per turn.

**Always on:** `fetch_skill`, `read_skill_file`, `bash`, `ws_list`, `ws_read`, `ws_read_pdf`,
`process_read`, `ws_grep`, `save_output`, `ws_add`, `ws_update_pdf_field`, `render_builder`,
`list_integrations`, `run_integration_tool`, the narrative tools (`set_plan`, `start_phase`,
`end_phase`, `start_chip`, `reflect`, `suggest`).

**Gated:**

| Tool(s) | Gate |
|---|---|
| `search_skills` | `AGENT_HARNESS_SKILL_SEARCH_ENABLED` (default off) |
| `start_process`, `update_process_step`, `start_inline_process` | `AGENT_HARNESS_PROCESS_ENABLED` (default **off**) |
| `db_list_tables`, `db_query` | `ORGDB_ENABLED` (default on) |
| `db_insert_rows`, `db_insert_rows_from_file` | org writes enabled **+** an active `datastore_write: true` skill **+** a real (non-general) workstream **+** the table write-linked |
| `db_delete_rows` | same, via `datastore_delete: true` |
| `smb_write_file`, `dynamics_crm_execute_operation` | an active write-capable skill (v1 reuses the datastore-write gate) |
| `call_web_api`, `call_web_browser`, `install_skill` | the Tabby/web-API gate, org+session level |
| `list_pipelines`, `run_pipeline`, `list_pipeline_*`, `get_pipeline_*` | `AGENT_HARNESS_PIPELINES_ENABLED` (default off) |
| `memory` | `AGENT_HARNESS_MEMORY_ENABLED` (default on; per-org kill switches exist) |
| portfolio tools | general/unscoped turns only |
| `finalize_result` | exactly one structured-output skill bound this turn |
| `render_ui` | at least one schema resolves |

**`extract_document` is not agent-invocable.** The schema exists but is never added to a
turn's tool list — PDF field extraction runs automatically on workstream upload. Read
documents with `ws_read` / `ws_read_pdf` / `process_read`.

**`render_ui` scoping is inert.** `GENUI_FULL_UNION = True` means the full union ships every
turn. `genui_schemas` and `process.genui` are documentation, not access control, and will not
put a component in reach that isn't already there.

**The capability gate survives earlier turns.** `_fetched_skills_from_history` seeds the
active-skill set from `fetch_skill` calls in prior turns, so a follow-up turn like "delete
it" still gets the tool. Any skill that structures itself around "the write gate is live
only within one response" is working around a bug that has been fixed — that pattern costs a
whole turn for nothing.

## Frontmatter

Read by `skills._meta_to_summary`: `name` (required), `description`, `keywords` (list),
`featured`, `process` (dict → `has_process`), `genui_schemas`, `output_type` +
`output_schema`, `datastore_write` + `write_tables`, `datastore_delete` + `delete_tables`.
Read live, not from the index: `auth`, `api_hosts` / `api_host`.

### Frontmatter is strict YAML or nothing

`_parse_skill_md` tries `yaml.safe_load`; **on failure it falls back to a regex that
recovers only `name` and `description`.** The entire `process:` block, `keywords`,
`featured`, `genui_schemas` and every capability flag are silently dropped — and upload still
returns 200. The usual trigger is an unquoted colon in a *nested* value, such as a step
`sub:` or `title:`. Quote every value containing a colon.

**Upload success never means the frontmatter parsed.** Verify with `GET /skills/{name}`.

### Capability flags

`datastore_write` and `datastore_delete` are read as `meta.get(key) is True` — an identity
check against the Python bool. Fail-closed:

| Written as | Result |
|---|---|
| `datastore_write: true` | works |
| `datastore_write: "true"` | **silently no-ops** — the write tool is never offered |
| `datastore_write: 1` | **silently no-ops** |

And `featured` is read as `bool(meta.get("featured", False))`, so `featured: "false"` is
**truthy** and pins the skill in the inline menu.

`write_tables` is ignored entirely unless the flag is truly enabled. Omitting it while the
flag *is* set means **unrestricted**, not "none" — and the effective per-turn allowlist is
the UNION across all active write skills, so one unrestricted skill makes the whole turn
unrestricted.

There is **no UPDATE**: revise by appending the new row and soft-deleting the old.

### Output contract

`output_type: structured` + a valid `output_schema` (`{type: object, properties: {...}}`)
makes the skill pipeline-mappable and offers `finalize_result` with that schema verbatim. An
unknown `output_type`, or a schema that isn't an object with properties, **degrades silently
to `text`** with no upload error. Convention: include a required `summary` string.

### Silent no-ops

`tagline` (always derived from the first 8 words of `description`), `version`, `license`,
`allowed-tools`, `model`, `tools`, `argument-hint`, and `author` on a skill. `name`/`skill`
inside a `process:` block are overwritten with the skill's own name.

## Dispatch lanes

Within one assistant response: narrative tools, process tools and any `render_builder`
carrying a `step_id` dispatch **sequentially in order**; all other work tools **fan out in
parallel**; `render_ui` runs last, sequentially.

On `stop_reason="max_tokens"` the harness **does not dispatch the half-formed call** — it
runs a continuation step. An over-wide tool call therefore spends a step and writes nothing.

## Plugins

A plugin is a zip whose first directory holds `.claude-plugin/plugin.json` and
`skills/<slug>/…`. The plugin slug is `plugin.json::name`, not the zip's top directory, and
is slug-validated (`^[a-z0-9][a-z0-9-]*$`).

`commands/`, `agents/`, `hooks.json`, `mcp.json`, `config/`, `docs/` and extra `plugin.json`
fields are **silently dropped** at upload — only `plugin.json`, top-level `README.md` and
`skills/**` survive. Org plugins shadow default plugins of the same name. A skill inside a
plugin can carry a `process:` block exactly like a standalone one.

## Deploy

1. Upload via the API (`POST /api/v1/agent-harness/skills` or `/plugins`, `replace: true` to
   overwrite) — the route busts the catalog index for you. Only raw storage writes need a
   manual bust.
2. **Restart the worker.** Temporal workers do not hot-reload.
3. Set `AGENT_HARNESS_PROCESS_ENABLED=true` if the skill has a `process:` block and you need
   real gate enforcement.
4. Verify with `GET /skills/{name}` — the only way to know the frontmatter parsed.
