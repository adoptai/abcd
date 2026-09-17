# Processes, gates, escalations and GenUI

Pinned to `adoptai-workflows` origin/dev @ `63980995` (2026-09-10).

## The process block

A skill becomes a tracked process **iff** its frontmatter carries a `process:` block. There
is no `TEMPLATE.json` and no template store: the catalog is "skills with `has_process`", and
the template is derived live at bind time. The run is **ephemeral** — editing the skill later
does not mutate a live bound run.

```yaml
process:
  title: Batch → Reviewed Workbook
  framework: <optional standard or badge>
  period_format: MMMM YYYY              # optional (parsed but never applied — see below)
  phrase_hints:                         # what the model matches to call start_process
    - process this batch
  steps:                                # 1-50, each needs id + title
    - { id: ingest, title: Document ingestion, sub: Source files received }
    - id: hitl-review
      title: HITL review
      assignee_role: human              # agent | human (default agent)
      assignee_hint: Reviewer           # role label until a human is assigned
      gate: { type: approval, role: Reviewer }
    - { id: generate, title: Generate workbook, sub: "Workbook · summary" }
  artifacts:
    - { slot: source-docs, title: Source documents, accepts: [pdf, csv, xlsx] }
  genui: [data-table, key-value-list]   # documentation only — see GenUI scoping
  agent_instructions: |                 # injected EVERY turn, free of step cost
    ...
```

Two step fields the example omits: `integrations: [names]` pins an integration chip to a
step, and `executor` hands the step to something other than the agent (`kind: pipeline |
eval`, with `inputs` substituting `{step_id.field}` / `{artifacts.slot}` and a
`result_schema` shaping the compact summary handed back on resume).

Notes that matter when reviewing:

- **Quote anything containing a colon.** A `sub:` or `title:` with an unquoted colon kills
  the whole YAML parse, and the fallback keeps only `name` and `description` — the entire
  block vanishes silently.
- **`phrase_hints` falls back to `keywords`.** With neither, the process is in the catalog
  but effectively untriggerable by phrasing.
- **`period_format` is parsed and never applied.** `run.period` is just the agent-supplied
  `period` argument to `start_process`, so it is not a reliable identifier.
- **Steps are 1–50.** Older docs say 2–12; that is stale.
- **`name`/`skill` inside the block are overwritten** with the skill's own name. Don't author
  them.

## Gates are the only real HITL

A step with `gate: {type: approval, role: …}` **cannot** be set to `done` by the agent. It
must pass through `review`, and the harness completes it when the human submits — enforced
server-side in `apply_step_update`. The agent physically cannot skip the human.

Everything else is advisory. A skill that says "wait for the reviewer" without a gate is
relying on prose, and prose gets skipped.

The mechanics to check:

- The turn must **END** with a `render_builder` carrying that step's `step_id`. That render
  *is* the review: it moves the step to `review` and the harness auto-completes it on
  submission. The agent must not mark it done afterwards.
- A step-bound `render_builder` dispatches in the **sequential** lane (it read-modify-writes
  the process state via `bind_step_builder`), so it must not be racing other process calls
  in the same response.
- `bind_step_builder` sets `builder_id`, `builder_kind`, `updated_at` and flips a gated step
  to `review`. It **never touches `note`** — the platform neither writes nor protects that
  field.
- Never narrate "ready to review" and continue. Never summarise-and-stop instead of
  rendering.

A skill legitimately has **no** `process:` block when it is one step of an orchestrating
Agent's process. Say so in the body — otherwise the next reviewer "fixes" it.

## Artifacts drive file intake

When the process declares `artifacts`, the bound guidance steers the agent: before working a
step that needs source documents, if they are not attached, its **first** action is
`render_builder` (`builder_kind: 'escalation'`) with **one `file_upload` field per slot** —
never a prose "please upload X", never fabricated or demo data. `accepts` sets the
extensions.

Without slots, that steer doesn't exist and the agent is more likely to ask in prose.

## Builders — the shapes and the hard rules

| `builder_kind` | Blocks | Use for |
|---|---|---|
| `escalation` | **1** | 1–3 quick clarifications, or a file-drop |
| `form` | **1** | structured intake, >3 fields |
| `stepper` | N | a wizard, or a queue reviewed one item per block |

**HARD RULE: more than one block ⇒ `stepper`.** `form` and `escalation` render only the
FIRST block and silently drop every later one. This is a real and silent data-loss bug.

Field types: `text`, `textarea`, `number`, `select`, `multi_select`, `date`, `checkbox`,
`file_upload`, `entity_picker`, `file_map`. `select`/`multi_select` always need
`options: [{value, label}]`.

Other mechanics worth checking:

- **Block ids must be unique and stable** — for a per-item queue, make it the item's id.
  Answers are namespaced per block, so **reuse** the same field `name` across blocks
  (`decision` on every step); values never collide.
- **`context`** on a block embeds a read-only GenUI composite above the fields, and it is
  validated against that component's own schema exactly like `render_ui` — it must be
  COMPLETE, including `type` and every required nested field. A partial context is rejected
  with `is_error`.
- **`document_target`** on a block highlights one extracted field's bounding box in the file
  preview. Copy `store_id`/`doc_id`/`relative_path` **verbatim** from `process_read`.
- **After `render_builder`, end the turn.** The submission arrives as the next user message
  wrapped in `<builder_data>`.
- Submitted state is re-derived from the transcript and rendered as a locked recap, so don't
  rely on client state for "was this submitted", and don't re-render a resolved gate as if
  it were open.

## Two different "escalations"

| | `render_builder` `builder_kind: 'escalation'` | `render_ui` `escalation-card` |
|---|---|---|
| Does | **Collects** input inline | **Links out** to a review portal |
| Use when | The user acts here and now | You are handing off to another surface |

Picking the wrong one is a real defect: an `escalation-card` where a builder was needed
collects nothing.

## GenUI

65 component schemas ship. Two — `tabby-auth` and `skill-replay` — are **withheld from the
model's union on purpose**: the harness emits them itself with authoritative data (a real
session id, a real approval fingerprint). A skill must never name them.

**Scoping is inert.** `GENUI_FULL_UNION = True`, so the full union ships every turn and
`process.genui` / `genui_schemas` are documentation. They will not put a component in reach
that isn't already there, and they are not access control.

Validation is server-side and strict:

- Unknown `type` → error listing valid types, nothing renders.
- Full JSON-Schema validation after the type check.
- **Any non-`type` array field that is empty is rejected.**
- A JSON-encoded string where an array/object belongs is auto-coerced once; a malformed one
  fails with a hint. Author native JSON.
- Component guards: `document-preview.source.url` must not be a sandbox path;
  `income-statement.periods` 1–8; `amortization-table.payments` ≤372;
  `workflow-stepper.steps` 2–12; `decision-card.options` 1–5; `entity-card-grid.columns`
  ∈ {2,3,4}.
- `document-field-extraction` is **blocked inside a bound process turn** — use a
  `render_builder` stepper with `document_target` per block instead.
- Shape traps: `data-table` needs `columns` as `{key,label}` objects and `rows` keyed by each
  column's `key` — plain-string columns fail. `stat-grid` uses `items`, not `stats`.

Large payloads go by `file_ref`, never inline — see `step-budget.md`.

Never render a `download-card` for a sandbox path: it has no real URL and renders as a dead
link. Deliverables surface automatically.
