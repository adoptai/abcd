# State, durability and resuming

Pinned to `adoptai-workflows` origin/dev @ `63980995` (2026-09-10).

## Durability tiers — pick the right one

| Where | Survives | Notes |
|---|---|---|
| The agent's context | Nothing. Cleared at 60% of the window, compacted at 80%, gone at the turn boundary. | Anything that lives only here is lost. |
| `/workspace` (sandbox) | Across calls and across turns of the **same** conversation — until the sandbox is reaped. | 30-min lease, keepalive every 120s, ~10 min idle grace. A human thinking on an approval form for 13 minutes has already outlived it. |
| `/memories` | Across turns and conversations in the workstream. | 32 files, 2 MB, 256 KiB/file. Contents are **inlined into every turn's user message**. |
| `save_output` | This conversation's Outputs. Readable by later turns of **this** conversation via `ws_list`/`ws_read`. | **A different conversation cannot read it.** Not indexed for search. |
| `ws_add` (docstore) | Permanently, workstream-wide, cross-conversation. | The only route that makes state recoverable in a fresh conversation. PDFs get indexed. |
| Data store row | Permanently, queryable, audit-trailed. | Append-only; revise by inserting the new version and soft-deleting the old. |

The mistake this table exists to prevent: **`save_output` is not durability.** One skill's
own hard-won note puts it plainly — *"save_output chips are conversation-scoped and a FRESH
conversation cannot read them; only ws_add makes them cross-conversation recoverable."* And
because the sandbox reaps during a human wait, run state that isn't in the docstore leaves
the engagement unrecoverable in any later conversation.

Related: end-of-turn discovery collects deliverables from the artifact tree by extension,
and `.zip` **is** now in that set. So "a zip is never collected automatically" is stale — but
`save_output` is still the right instruction, because it fires immediately and controls the
destination path.

## Hand state across turns

A capped turn does not resume itself. A human sends another message, and the continuation
inherits a compaction **summary** — not the skill's contracts. One measured turn 2 opened
with **15 of its first 16 calls** back in discovery for exactly this reason. That is why
"send another message" does not recover a run.

What to look for in a skill:

- **Checkpoint as you go, not at the end.** A tick issued 97 `ws_add` calls and **zero**
  checkpoints; every landed path lived only in the agent's context. The promote failed, 90
  files were orphaned with the rows still at `attachments = 0`, and the next tick had nothing
  to resume from so it re-filed everything.
- **Open with a cheap "what's already done" read**, so a continuation resumes instead of
  rediscovering. A set-difference against what's already recorded is one query.
- **Persist derived values that later steps need.** One run resolved two externally-looked-up
  rates correctly, and the next turn reported it could not resolve them and escalated. The
  values were found and thrown away between turns, and the output shipped without them.

## `/memories` is not a queue

Note contents are inlined into **every** turn's user message. That makes a stale note
actively harmful.

An interrupted run left a note titled "resume here". Two consecutive later runs both resumed
that one stale item while **26 genuinely pending items went untouched**. Another run read a
note saying an item was complete, replied *"there is nothing new to action here"*, and did
nothing — eight times over. The note was accurate, for a different item.

The rules that fixed it, worth checking any memory-using skill against:

- Row/work selection **never** comes from memory. The claim query is always the first action.
- Key notes **per work item** (a stable id), never a global name, and never on an identifier
  that recurs.
- Open every note with a **NOT-AN-INSTRUCTION** header.
- Notes carry **facts only, never directives**.
- Both terminal paths **delete** the item's note, with opportunistic cleanup of one stale
  note per run — the 32-file cap is reached quickly otherwise.

## Identity across a turn boundary

When one turn writes an identifier and a later turn reads it, that hand-off is a load-bearing
interface and it fails in expensive ways.

- **Write it programmatically, and self-check it.** A skill documented an identifier with a
  literal truncated example (`mail_id=AAMkAD...`); the writing turn reproduced the
  truncation, the reading turn's query returned zero rows, and the work was applied to the
  wrong record.
- **A field serving two audiences will be overwritten.** One note field carried the machine
  identity; 340 events later a status update passed a human-readable summary as the same
  field and destroyed it. Nothing warned. Stamp identity **once**, then omit that field on
  every later update.
- **An identifier names one row; a characteristic describes many.** A turn resolved a record
  by a category field, an empty list, a document count and "the only match in my sample" — from
  a `LIMIT 5` result, against 46 live candidates. `LIMIT 5` returning 5 rows proves "at least
  5", never uniqueness.
- **Verification must use a value the candidate did not supply.** Re-querying by the id you
  just chose confirms only that it exists.
- **A count mismatch between saved state and the bound record ends the turn.** The saved
  extraction is this conversation's own output; a disagreement means the *record* is wrong.
- **Re-verify after any re-query.** A row that changes mid-turn is a stop, not a retry.

## Note on what a skill can actually enforce here

Run state reaches the sandbox only as text in the model's context — turn constants stage to
the message store, not the sandbox. So identity rules are ultimately mediated by the model.
A skill can make them much more reliable (copy from a tool result, refuse an abbreviated
value, forbid status-only queries) but cannot make them airtight. Say so when you review,
rather than implying a prose rule closes the hole.
