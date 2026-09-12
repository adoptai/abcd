# The step budget, and how batching actually works

Pinned to `adoptai-workflows` origin/dev @ `63980995` (2026-09-10). Re-check with
`scripts/verify_platform_facts.py`.

## The economics, in four facts

1. **A turn gets 100 LLM steps.** `MAX_TOOL_ITERATIONS = 100`. Hitting it returns
   `stop_reason="max_tool_iterations"`.
2. **One assistant response is one step, whatever it carries.** A response with one tool
   call and a response with thirty cost the same. This is the whole game.
3. **Work-tool calls in one response dispatch in parallel.** The workflow splits the
   response's tool calls into lanes and `asyncio.gather`s the work lane. So batching
   independent calls is *free*, and splitting them never saves anything.
4. **Nothing resumes a capped turn.** A human has to send another message, and the
   continuation inherits a compaction *summary* rather than the skill's contracts. One
   measured turn 2 opened with 15 of its first 16 calls back in discovery for that reason.
   The step budget is not a cost concern, it is a completion concern.

Measured, from production Temporal histories: `llm_step_activity` was 439 of 605 minutes
(73%) across 20 conversations, at 5.8s median and 27s p90. `bash` was 44 minutes across
1,524 calls. **The step count is the clock.** Also: the same skill run locally took 210
tool calls; on the platform, 1,032.

## The dispatch lanes — what parallelises and what does not

Within a single response the workflow routes tool calls into three lanes
(`workflow.py::_is_sequential_tool`):

| Lane | Tools | Behaviour |
|---|---|---|
| Sequential | `set_plan`, `start_phase`, `end_phase`, `start_chip`, `reflect`, `suggest`, `start_process`, `update_process_step`, `start_inline_process`, and any `render_builder` carrying a `step_id` | Dispatched one at a time, in the order the model wrote them |
| Parallel | `bash`, `read_skill_file`, `ws_*`, `db_*`, `fetch_skill`, everything else | `asyncio.gather` — true fan-out |
| Last | `render_ui` | Sequential, after the work lane |

Two consequences worth knowing when you review:

- Lifecycle calls **serialize**, but they still cost **zero extra steps** when they ride in
  a response that also carries work. They are cheap to include and expensive to isolate.
- The process tools serialize because each one read-modify-writes the conversation's
  process state. A step-bound `render_builder` joins them for the same reason — racing it
  against `update_process_step` loses updates and reorders the state snapshots.

## Calls batch. Payloads do not.

This distinction is the one that bites hardest, because the two failures look nothing alike.

**Batching independent CALLS into one response: free.** Five `read_skill_file` calls in one
response cost one step instead of five.

**Batching many items into ONE call's arguments: not free, and it can lose the work.** A
tool call's arguments are output tokens. When the response is truncated the harness reports
`stop_reason="max_tokens"` and deliberately **runs a continuation step instead of
dispatching the half-formed call** — forcing a dispatch there would send invalid arguments.
So an over-wide call spends a step and writes nothing.

That is not theoretical. A real run put seven rows and all their documents into a single
`db_insert_rows`: the call was truncated mid-string, the turn errored after 4.5 minutes of
thinking, and **nothing was written** — the rows stayed at `attachments = 0` with the
watermark unadvanced. Seven separate inserts would have cost six more steps and finished
cleanly.

So a good skill bounds **width** as well as step count:

- State a per-call item ceiling (one insert per row; ≤25 `ws_add` per step).
- Say plainly: **a truncated tool call means send less — never send it again.**
- Prefer the file form for bulk a script produced: `db_insert_rows_from_file` reads up to
  5000 rows from a JSON file in the sandbox, so the rows never pass through the model at
  all. `db_insert_rows` accepts 500 inline — that is a *limit*, not a target.

## Stage once, in one step

Every `read_skill_file` a turn needs should go out in **one** response. They are pure reads
with no dependency on each other.

The common defect is not disagreement with this rule — it is writing the staging
instructions in the sections where each file happens to be needed. The agent then stages
each file where it reads about it, one step each. Put **one** staging block near the top of
the body listing every file the turn will need.

### The five-slot ceiling

Keep a single batch to **five files or fewer**. Confirmed against a real activity log: 8
files batched in one step all dispatched correctly, scheduled within a millisecond of each
other — batching itself works — but 5 completed in well under a second while the other 3
each sat for 4.5+ seconds. The sandbox's file-transfer helper runs
`_GETMANY_CONCURRENCY = 5` concurrent operations; a bigger batch does not fail, the excess
queues behind the same five slots.

So size the batch to the limit: one step of 5 plus one step of the remainder beats one step
of 8. **≤5 is a ceiling, not a target** — fill each step to it before opening another.

### A ceiling needs a prohibition

A batching instruction stated as a number alone gets violated. One run spent **98 of 100
steps** issuing `ws_add` and `bash` calls one at a time and never reached a single claim or
export — despite the platform's own system prompt and the skill's own wording already
stating the batched form correctly.

Models pattern-match into busywork under uncertainty unless the wrong default is explicitly
**forbidden**, not merely bounded. Every batching instruction should pair its ceiling with
"never issue these one at a time across separate steps."

## Never spend a step on bookkeeping alone

`start_chip` and `reflect` are free riding along with real work and cost a full step alone.
Of one skill's ~83 calls, **29 were `start_chip`/`reflect` issued alone**, each in its own
step — a third of the budget. Another measured turn lost 18 of 53 steps this way, and
another 13 of 56 (24%).

**This is now enforced platform-side.** The tool descriptions themselves say it:
`start_chip` carries *"NEVER spend a message on start_chip alone; that wastes a full model
round-trip"*, and `start_phase`, `end_phase`, `reflect` and `update_process_step` all now
carry batching text. A skill does not need to restate it. If you see a skill spending
paragraphs on this, that is a deletion.

## Collapse fixed chains

Batching is for independent work. A chain where B reads what A wrote **cannot** be batched
— but it can be *collapsed*. A wrapper script that shells out to the same scripts, in the
same order, with the same arguments turns N round trips into one without changing any
logic. Keep the underlying scripts independently runnable so you can re-run just one.

Two real examples, both pure wrappers rather than rewrites: one runs an extractor then an
Excel builder in fixed order; another runs two scripts *concurrently* as subprocesses
because both only read the same input and each writes its own output, so there was never a
race between them.

**The dependency rule is the one thing not to over-optimise.** If B reads what A wrote,
they stay in separate steps. Collapsing a real dependency is a correctness bug, not a
saving. When in doubt, ask whether B reads what A wrote — if yes, separate; if no, same
step.

## Route pointers, not payloads

The single biggest latency mistake a skill can make is routing **data** through the
**model**. Every row the model reads inline it must retype into the next call, and it
physically cannot re-emit anything large.

- **Query results a script consumes → `db_query {sql, save_to_file: true}`.** The result
  lands at `/workspace/db/query_<id>.csv` at any size and the response gives the path.
  Without the flag, a small-enough result comes back inline and the model retypes it —
  about a minute and ~8k output tokens per 50 rows. Skip the flag only when the *model*
  must reason over the rows.
- **Component payloads → the script writes the JSON, then `render_ui {type, file_ref}`.**
  Only a ~15-token stub enters context. On a validation error the agent fixes the **file**
  and re-renders with the same `file_ref` — never falls back to a partial inline payload.
- **Chain by file, not by piping stdout.** Piping one script's JSON into the next puts whole
  documents through context on the way.
- **Print a summary, write the payload.** `bash` stdout returned to the model is capped at
  100,000 chars and truncated in the **middle**, so a large result arrives cut and the agent
  then probes the output file one `python3 -c` per fact.

One skill went from **720s and 38k output tokens with truncated data** to **119s and 2.2k
tokens with complete data** on these changes alone.

The output ceiling is real in the other direction too: a run stopped at `max_tokens` (49,100
output tokens) mid-handoff on a 13-item batch, leaving the work claimed, no gate rendered
and nothing to reclaim it. Real traffic on that stream reaches 40 items, roughly 3x the
output — so this is a hard ceiling, not a tuning preference.

## State the finish line

Documenting what a script returns removes the *reason* to go digging. It says nothing about
when to **stop**.

A real turn had a valid deliverable at **step 38 of 100**, six minutes into a twenty-minute
turn. Every script had been invoked correctly. It then spent the remaining **62 steps** —
62% of the whole budget — checking its own work: unzipping the output, reading every CSV
back, re-summing totals, re-deriving figures, and re-running the export twice into `/tmp` to
diff against the real one. It hit `max_tool_iterations` and died. Nothing after step 38
changed the deliverable.

The fix is a stop condition, not another prohibition. Forbidding `grep` would have blocked
the symptom while leaving the agent no signal that it was already finished. State the finish
line: **a green result from a script that refuses rather than emit bad output is
trustworthy, and reproducing it cannot add information.** Name the one call that ends the
step, and forbid re-deriving the deliverable.

## Never re-fetch the skill

If the turn pulls `SKILL.md` more than once, that is steps and context both. Context
clearing fires at 60% of the model window and compaction at 80%; one measured pair of turns
cleared context 7 times and the agent re-fetched `SKILL.md` after each, 4 times in turn 2
alone.

## `agent_instructions` is free context

For a **bound process run**, `process.agent_instructions` is injected into the system prompt
on every turn (under "Template instructions:"). No tool call, no step.

The same text in the body is only in context after a `fetch_skill`, and is the first thing
lost to compaction. So:

- **Turn-level invariants** — what to do first, how to resume, what must never happen —
  belong in `agent_instructions`.
- **The detailed domain procedure** belongs in the body.

The trade: `agent_instructions` costs context tokens on *every* turn, so keep it to
invariants, not the whole procedure. This is the cheapest step saving available to a process
skill and it is almost never used.

## One turn, one budget

The 100 is per **turn**, not per skill. Skills that hand off inside one turn spend one
budget between them. Two measured runs had the first skill alone consume **82–84 of the
100** on a single email, and both turns ended degraded before the deliverable was produced.

When reviewing a plugin: give each skill an explicit allowance in its own body, say where in
the turn it runs and how much is likely left, put the shared mechanism in **one** reference
file rather than restating it per skill — and then ask whether the handoff needs to be in
one turn at all.

## What the platform fixed, so you don't optimise for it

Several older workarounds are now unnecessary. Startup staging batched: the workspace gate
went from ~455s to 1.4s. Skill-file co-staging batched: 227s → 28s in a controlled A/B, and
now flat in asset count rather than ~4 round trips per file. Single-flight staging with a
memo: eight parallel reads went 242s → 51s and a full turn 368s → 231s, with model round
trips down from 24 to 16.

Still open, and worth knowing when you set expectations: the platform is ~2.7× slower than
running the same skill locally, and the cause is round-trip count. The 100-step ceiling has
no auto-continue. A paused approval can outlive the sandbox's ~10-minute idle lease and lose
staged files.

## The second axis: output volume

Everything above is about **step count**. There is a second cost that step count does not
capture, and on at least one measured run it was the larger one.

That run: 61 minutes, 281 model calls, 415 tool calls. The breakdown:

| Component | Share | Detail |
|---|---|---|
| Token generation | **59%** | 200,887 output tokens; two-thirds were Python parsers and pipeline scripts the agent typed; 32k in ten progress notes |
| Fixed per-call overhead | 17% | 281 calls × ~2.2s; the 47 calls under 150 tokens still averaged 3.6s |
| Stalled streams | 12% | platform-side |
| Tool execution | 11% | every script itself finished in 1–2s |

Fitted on that run: **step duration ≈ 2.23s + 10.7ms per output token.**

Two things follow. First, **optimising script runtime is pointless** — it is 11% of the
time and each script already finishes in about a second. Second, a skill can be
step-efficient and still slow, because the model is *typing*.

What actually drives output volume, in the order it paid on that run:

- **Code the skill should have shipped.** The same pipeline script was generated three
  times at 5.4–5.8k tokens each, ~40–45s of typing per emission, because nothing persisted
  it. Anything the agent authors should be saved (`save_output`, and `ws_add` if a later
  conversation needs it) and re-used. Anything it authors *twice* should ship as a script
  in the skill.
- **Progress notes that restate figures.** Ten memory writes of 3–10k characters, mostly
  repeating numbers already in files on disk. A note should say what is done, which file
  holds the state, and the next command — and name the file that has the figures rather
  than reciting them. One note per phase, a few dozen lines.
- **Payloads travelling as text.** Covered above: `db_query save_to_file`, `render_ui
  file_ref`, chain by file. Every row that enters context gets retyped on the way out.
- **A body that is mostly rationale.** A 614-line `SKILL.md` where half is incident history
  and exit-code prose costs ~15k tokens on every fetch, and buries the operative facts. Put
  the commands, contracts and staging block first; move the history to `references/` and
  point at it from the check that fails.

`analyze_run_trace.py` reports the generation share directly. Above ~40%, look at what the
model is typing before you look at how many steps it took.
