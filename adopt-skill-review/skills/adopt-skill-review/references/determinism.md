# Prose guards get skipped. Move enforcement into code.

The most-repeated lesson across every postmortem on this platform, stated in one of them
almost exactly:

> Every guard that failed here was advisory prose. Violating it produced no error and the
> turn still reported success. The replacements all either require the agent to emit
> something only compliance can produce, or block the output on a count. **Prose that cannot
> be checked has now been skipped twice.**

## The shape of the failure

A skill states a rule. The rule is correct, unambiguous, and in the body. The agent doesn't
do it. Nothing errors. The turn reports success.

Real instances:

- A skill's step 2 mandated an eight-phase plan including a field-determination phase. A run
  emitted a five-phase plan going straight from raw text to promote, wrote nine fields off
  the row, never called the extraction script at all, promoted 20 rows with nine fields
  null, and **reported success**. 56 LLM steps of 100, 252 tool calls, no failures. The
  mandate had been added *because of* an identical earlier run.
- A mandatory read-back check — "read the row back after the insert and confirm" — never
  ran. All four of that turn's queries were before the insert.
- A rule said to use a specific script for re-reading; the run wrote *"since we don't have
  that script…"* and promoted anyway. The script had been in the sandbox the whole time.
- Two reference files were skipped, so the run invented its own lookup table containing four
  codes that do not exist.

None of these was ambiguous. The gap is that nothing *checked*.

## What to recommend instead

For each load-bearing guard, ask: **if the agent ignores this, what fails?** If the answer
is "nothing", it is not a guard.

Three mechanisms that hold, in order of preference:

**1. The script refuses to write.** The strongest form. Move the invariant into the script
that produces the output, and have it exit non-zero with an `error`/`why` payload rather
than emit something it cannot stand behind.

> A field the downstream consumer required was optional in the skill's own schema, absent
> from its required-field list, and defaulted by the script. It shipped empty on a filed
> output. The fix made the script **write no output at all** when the value cannot be
> resolved — *"the script check is what makes the other three unnecessary to get right."*

**2. Block the output on a count.** Where the invariant is about completeness, assert it.
A pre-insert null test on the payload. An "output count must equal input count" check. A
per-item count check inside the extractor. These catch the class where every individual
value is well-formed but the *set* is wrong.

**3. Require the agent to emit something only compliance can produce.** Where code genuinely
can't see it, make the evidence mandatory: a `reflect` after `set_plan` that maps each phase
to a step; a value copied verbatim from a tool result rather than recalled. This is weaker —
it is still mediated by the model — but it converts a silent omission into a visible one.

Keep the prose. It explains *why*. It just stops being the mechanism.

## Two traps specific to this

**A guard that fires for the wrong reason still looks like a working guard.** One
duplicate-detection refusal was catching a double-emission bug it was never written to
catch. Because it failed loudly, the real defect stayed invisible for weeks. When a guard
trips, it is worth asking whether it tripped for the reason it was written.

**A whitelist that drops what it does not recognise converts a typo into missing data, and
missing data into a model's guess.** One compaction step silently dropped seven correctly-
valued keys because they were spelled differently from its whitelist; a later turn then
*guessed* the mapping and filed four outputs against the wrong record. The fix: repair known
aliases, then **hard-error on an unknown key**. Never silently drop.

**A documentation example containing `...` will be reproduced as data.** A skill documented
an identifier with a literal truncated example; the writing turn reproduced the truncation,
and the identifier became unusable. Every identifier a later turn depends on must be written
by code, and every reader of one must reject an obviously abbreviated value rather than work
around it.

## How to phrase the finding

Name the specific guard and the specific mechanism:

> §2.5 requires a value to be collected for every record, and §2.8 requires a read-back
> after the insert. Both are prose; a run that skips them reports success. Move the first
> into the extraction script as a per-record count assertion that exits 3 when a record
> yields none, and make the write script refuse when a required column is null. Keep both
> prose sections as the explanation.

Don't recommend converting *every* imperative — a skill body legitimately carries a lot of
domain explanation. Target the ones where silent violation ships wrong output.
