# What the harness already injects — delete it from the skill

Pinned to `adoptai-workflows` origin/dev @ `63980995` (2026-09-10).

Every turn, the harness assembles system guidance from
`genui.py::build_narrative_system_guidance`, `build_skills_system_guidance`,
`build_genui_system_guidance`, `build_datastore_system_guidance`,
`build_memory_system_guidance`, `workstream_staging.py::build_workstream_system_guidance`
and, for a bound process, `templates.py::build_process_system_guidance`. Plus the tool
descriptions themselves.

A skill that restates any of this pays context tokens for it on **every step** and can only
weaken it by paraphrase. The body should carry **domain logic only**.

Two caveats before you tell someone to delete something:

- A skill may legitimately **narrow** a platform rule to its own domain — "stage exactly
  these four files in one step" is domain-specific and belongs in the skill. "Batch
  independent calls" does not.
- A skill may legitimately **contradict** a general rule where its domain demands it, and
  that must stay. Example: the general guidance says review queues go in one stepper; a
  skill whose gate is genuinely one item at a time needs to say so.

## Already covered, with the platform's own wording

**Batching and the iteration budget** — *"Each assistant response (regardless of how many
tool_use blocks it contains) counts as ONE iteration against the workflow's tool-loop cap.
Splitting narrative + work into separate responses DOUBLES the iteration cost and can starve
the final render_ui call."* Ships with worked BAD/GOOD examples.

**Independent operations in parallel** — *"Whenever you need multiple INDEPENDENT
operations, invoke all the relevant tools simultaneously in ONE response rather than one per
response… Only batch calls that are independent of each other; if one depends on another's
output, sequence those across responses."*

**One script beats many exploratory calls** — *"Each tool call is a full LLM round-trip, and
every prior result is re-sent on the next step… write ONE script that extracts everything
you need and prints a COMPACT summary; do NOT call bash repeatedly to poke one sheet / table
/ section at a time, and do NOT reopen the same file… If a skill ships scripts, run them in
order rather than re-implementing their logic inline."*

**Keep bash commands small** — don't paste multi-KB scripts into `bash`; use
`read_skill_file` then run the staged path; stdout is capped ~100KB, print compact JSON.

**Never read a skill's `.py` source to understand it** — *"Do not `read_skill_file` on
`scripts/*.py` (or any implementation file) to 'see how it works', to learn its
inputs/outputs, or to pick a skill — that is all in `SKILL.md`. Reading implementation is
wasted budget."* Also: read a `references/*.md` file **only** when `SKILL.md` points at it,
and open a script **only** at the moment you run it.

> This one is worth reading twice when you review. The platform tells the agent the contract
> is in `SKILL.md`. If the contract is *not* in `SKILL.md`, the agent has been told to trust
> a document that doesn't answer the question — and it goes and reads the source anyway.
> That is why a missing output contract is so expensive: it puts the skill in direct
> conflict with the platform's own instruction.

**Never spend a step on a lone lifecycle call** — now in the tool descriptions themselves.
`start_chip`: *"Emit it IN THE SAME assistant message as the bash/render_ui/skill tool calls
that carry it out… NEVER spend a message on start_chip alone; that wastes a full model
round-trip."* `start_phase`, `end_phase`, `reflect` and `update_process_step` all carry
equivalent batching text. **A skill no longer needs to say any of this.**

**Never fabricate** — *"NEVER fabricate, synthesize, mock, guess, or invent data, files,
numbers, or source documents."* Missing or unreadable input is a `render_builder` escalation
with a `file_upload` field, re-upload only, no demo-data option unless the user explicitly
asked for a demo this conversation.

**The whole builder ruleset** — never ask for input in prose; the affordance ladder
(`question` → `escalation` → `form` → `stepper`); **more than one block ⇒ `stepper`**
(`form` and `escalation` silently drop every later block); after `render_builder`, END THE
TURN; a review queue is the human's decision and must not be auto-accepted; never render a
hand-typed choice menu in prose; the `context` slot is validated exactly like `render_ui` and
a partial payload is rejected.

**`file_ref` for large payloads** — *"When a component's payload is large (roughly >5 KB) or
already exists as a file in the sandbox, do NOT emit the payload inline and do NOT print/cat
it back to inspect it… write the complete payload JSON to a file under /workspace and call
`render_ui` with ONLY `{type, file_ref}`."* Including: on validation failure, fix the **file**
and re-render with the same `file_ref`.

**No ASCII / box-art trackers** — use `flow-canvas` (a Mermaid flowchart) with a stable `id`
re-rendered with updated `node_states`, and for a bound process the pinned stepper **is** the
tracker.

**No hand-rolled charts** — no matplotlib / plotly / seaborn / SVG / ASCII charts; compute
with bash, render with the matching `render_ui` component. Prefer a purpose-built component
over `data-table` (variance-analysis, aging-report, amortization-table…).

**Voice rules** — first action is a tool call, never a sentence; chip titles 3–7 words,
verb-first; no throat-clearing; `reflect()` is the only prose channel during work phases;
never name internal components, sandbox paths or `/workspace` in user-facing text; the answer
phase interleaves prose with components.

**`suggest` instead of prose option lists** at the end of a turn.

**The whole process protocol**, for a bound run — the `active`/`review`/`done`/`blocked`
lifecycle; mark every completed non-gated step done including the last; a gated step is
human-resolved, the turn must END with a `render_builder` carrying its `step_id`, and the
agent must not mark it done; always pass `step_id`; don't render the stepper via `render_ui`;
refer to steps by title in prose and `step_id` in tool calls; write deliverables under
`process/<artifact-slot>/`; don't call `start_process` again. Plus, when the run has
artifact slots, the file-intake escalation rule; and for a pipeline-executor step, dispatch
then **end the turn** rather than polling.

**Data-store idiom** — read-only by default; append-only writes; no UPDATE, so revise by
appending the new row and soft-deleting the old; confirm columns with `db_list_tables` first.

## How to phrase the finding

Don't just say "duplicated". Say what to delete and what to keep:

> Lines 31–58 restate the platform's batching, parallel-calls and lone-`start_chip` rules,
> all of which are injected every turn and the last of which is now in the tool descriptions
> themselves. Delete them. Keep line 44 — "stage `common.py` alongside the three scripts
> that import it" — that is domain-specific and the platform cannot know it.
