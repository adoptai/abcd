---
name: adopt-skill-review
description: Review a skill or plugin built for the Adopt agent harness and report what is costing it latency, LLM steps and reliability. Use when asked to review, audit, optimise, speed up or debug an Adopt harness skill or plugin; when a skill run is slow, hits the 100-LLM-step ceiling, stops with max_tool_iterations, or produces plausible-but-wrong output; or while writing a new harness skill and wanting it right in the first iteration. Covers batching, tool-call and step economics, script contracts, file and asset placement, process/HITL gates, GenUI, state and resume.
---

# Reviewing an Adopt harness skill

Your job is to make someone else's skill **faster and more reliable in the harness**, and
to say exactly what to change. Not to rewrite it, not to grade it. The output is a ranked
list of changes with the reason and the estimated saving for each.

## Two levels — establish which one you are doing, first

**Level 1 — the skill alone.** Everything recoverable statically: batching shape, script
contracts, file and asset placement, frontmatter traps, duplicated platform guidance,
missing stop conditions, prose guards nothing enforces. Tells you **what is wrong**. Works
before a run exists.

**Level 2 — the skill plus logs from a few real runs.** Adds what actually happened and,
crucially, **how much each thing cost**. Tells you **what to fix first**.

Level 2 is not just more findings. On a real triage the items ranged from 900 seconds down
to 40. Static review can tell you all of them are true; only the logs tell you which two
are worth 90% of the win. Without that an FDE fixes the cheap ones and the run still caps.

**Say which level you are working at in the report**, and if you are at level 1, say what
level 2 would add — ask for `temporal workflow show --workflow-id <agent-harness-…>
--output json`, one file per turn.

## Two cost axes, and they are not the same

| | What it is | What drives it |
|---|---|---|
| **Steps** | One assistant response = one step against the 100 | Round-trip count: staging one file at a time, chains without a wrapper, lone lifecycle calls |
| **Tokens** | Time spent generating output | Volume the model types: code the skill should have shipped, payloads that should travel as files, notes restating figures already on disk |

A skill can be step-efficient and still slow. On one measured run, **token generation was
59% of total agent time** — 200k output tokens, two-thirds of it Python the agent wrote
because the skill did not ship it. Cutting steps would not have touched that.

Level 1 can only reason about steps. Level 2 measures both, and the trace analyzer reports
the generation share explicitly. If it is above ~40%, look at what the model is *typing*
before you look at how many steps it took.

## Do this

**1. Find the target and run the audit — one command, before reading anything.**

```bash
python3 scripts/audit_skill.py <path-to-skill-or-plugin-dir> --format json
```

`<path>` is a directory holding `SKILL.md`, or a plugin directory holding
`.claude-plugin/plugin.json` and `skills/<slug>/SKILL.md`. Point it at the **plugin** when
there is one — several skills that hand off inside one turn share a single 100-step budget,
and that only shows up at plugin level.

The audit is deterministic and gives you every finding with file:line evidence, a step
estimate and an effort rating. Run it first so you never guess at something it already
measured.

**If this skill hands data to or from another skill, name it** — the shape-handoff check
only runs when you assert the chain exists:

```bash
python3 scripts/audit_skill.py <consumer-dir> --chain <producer-dir> [--chain <other>]
```

Why it is gated: most JSON a skill reads comes from *outside* the chain — an API, a
spreadsheet, or a file the agent hand-writes — so without your assertion "nothing writes
this key" is the normal case and the check is noise. Measured on six real skills ungated,
five produced false criticals. With `--chain` its hits are **candidates to verify against
the producing skill**, never conclusions.

**2. Read the SKILL.md yourself.** The audit finds mechanical faults; you judge whether the
*instructions* are any good. Read the full body of each skill — this is the one place worth
spending context, because badly-ordered or unenforceable instructions are the findings a
script cannot produce. Look for:

- Rules that appear *after* the step they govern. A batching rule read halfway through a
  turn has already been disobeyed. Anything that shapes the whole turn belongs at the top.
- Guards written as prose with nothing enforcing them. If violating a rule produces no
  error and the turn still reports success, it will be violated — this has recurred across
  independent skills. See `references/determinism.md`.
- Steps described but with no stated order dependency, so the agent can't tell what is
  safe to batch and what would be a correctness bug if collapsed.
- Instructions the platform already injects on every turn — those are dead weight. See
  `references/already-enforced.md` for the list and delete them.

**3. If they have real runs, measure instead of estimating — this is level 2.**

```bash
python3 scripts/analyze_run_trace.py turn1.json turn2.json turn3.json --format text
```

**Pass one file per turn.** A multi-turn job is only diagnosable across all of its turns:
the expensive turn is usually not the one that failed, and a capped turn's cause often sits
two turns earlier.

It reports steps and tool calls per turn, calls per step, wall clock, output tokens and the
generation share, plus: steps that carried nothing but bookkeeping (computed, not guessed),
bash calls bucketed into ran-a-script / read-a-script / reimplemented-inline, which file
was read for discovery most, large command bodies emitted more than once (the model
retyping code), progress-note volume, re-staged files, re-fetches, the context trajectory
against the 60%/80% clearing and compaction thresholds, and capped or `max_tokens` turns.

**4. Load only the references a finding points at.** Each finding carries a `reference`
field. Don't pre-read them all.

**5. Report.** Format below.

## The report

Rank by **steps saved per turn, then by blast radius** — not by severity label alone. An
FDE acts on the top three items and stops reading, so the top three must be the ones that
matter.

```
## <skill or plugin name> — review

**Now:** <what the audit measured: step floor, script count, contract coverage, and the
measured numbers if a trace was supplied>

**Do first** (high impact, low effort)
1. <change> — saves ~N steps. <why, in one or two sentences.> <where: file:line>
2. …

**Then**
…

**Correctness risks** (these do not cost steps; they ship wrong output)
…

**Already fine** — <name what the skill gets right, briefly.>
```

Rules for the report:

- **Every finding names a file and line.** "Batching could be better" is not actionable.
- **Give the fix, not the principle.** Show the corrected block where it is short enough.
- **Separate step cost from correctness.** A silent-wrong-output bug is not a latency
  finding and must not be buried under one.
- **Say what is already right.** A review that only lists faults gets argued with. It also
  stops someone "fixing" a deliberate decision — a skill with no `process:` block may be
  one step of an orchestrating Agent's process.
- **Mark estimates as estimates.** They overlap: fixing the staging batch and collapsing a
  chain can save the same step. Never present the sum as a forecast.
- **Do not invent platform behaviour.** Everything you assert about the harness must come
  from a reference here or from the code. If unsure, say so and point at the file.

## What to look for, in the order it usually pays

The audit checks all of this mechanically. This is the map, so you know what the findings
mean and can spot the ones only a human read will catch.

| Where the steps go | The fix | Reference |
|---|---|---|
| Staging one file per step instead of one batch | One named staging block, issued as one step, ≤5 files | `step-budget.md` |
| Agent rediscovering the skill's own scripts | An output contract per script: invocation, reads, writes, stdout shape, exit codes | `script-contracts.md` |
| A fixed chain run as N separate calls | A wrapper script that shells out to the same scripts | `step-budget.md` |
| Data routed through the model | `db_query save_to_file`, `render_ui file_ref`, chain by file not stdout | `step-budget.md` |
| A whole batch crammed into one call's arguments | Bound the width; a truncated call writes **nothing** | `step-budget.md` |
| No stated finish line | Name the call that ends the step and forbid re-deriving the deliverable | `step-budget.md` |
| Restating what the harness already injects | Delete it | `already-enforced.md` |
| Turn invariants in the body, costing a `fetch_skill` | Move them to `process.agent_instructions` — injected free every turn | `step-budget.md` |
| Files a script opens that the harness never puts on disk | Move them to `assets/` | `file-layout.md` |
| Work that dies at the turn boundary | Checkpoint durably; hand state forward | `state-and-resume.md` |
| Prose guards nothing enforces | Move enforcement into a script refusal | `determinism.md` |
| Skills in one plugin sharing one 100-step budget | Explicit allowance per skill; question the handoff | `step-budget.md` |

## Do not recommend these

They look like optimisations and are not. Recommending them wastes the FDE's time and
costs you credibility on the findings that matter.

- **Optimising script runtime.** Scripts finish in 1–2 seconds. On a 61-minute run that is
  nothing. The cost is round trips and generated tokens, not execution.
- **Asking for a higher step cap.** It buys time on the same defect. The run that capped
  did so because a loader mismatch sent it into 60 steps of discovery; a bigger budget
  funds more discovery.
- **Collapsing a real dependency to save a step.** If B reads what A wrote they stay in
  separate steps. That is a correctness bug dressed as a saving.
- **Deleting a domain rule because it looks like platform guidance.** Check
  `already-enforced.md` first; a skill legitimately narrows a general rule to its domain.

## Two things to get right

**Batching is only for genuinely independent work.** If B reads what A wrote, they must
stay in separate steps — collapsing a real dependency is a correctness bug, not a saving.
Recommend a batch only when you can point to why the calls don't depend on each other.

**Check the facts before restructuring a skill around one.** The pinned platform values are
stamped with a commit; workarounds outlive the bugs they were written for. One production
skill still spends a whole dedicated turn on a data-store write to dodge a gate expiry that
has since been fixed. Before telling someone to restructure:

```bash
python3 scripts/verify_platform_facts.py --repo <path-to-adoptai-workflows>
```

## References

Load on demand — a finding will tell you which.

- `references/platform-contract.md` — pinned limits, the 50-tool inventory and their
  per-turn gates, dispatch lanes, context and compaction math, feature flags, upload caps
- `references/already-enforced.md` — what the harness injects every turn, verbatim
- `references/step-budget.md` — the step economics and every batching rule, with evidence
- `references/script-contracts.md` — the contract block, output envelope, exit codes,
  refusals
- `references/file-layout.md` — the staging matrix: `scripts/` vs `assets/` vs `references/`
- `references/state-and-resume.md` — sandbox lifecycle, durability tiers, memory, identity
- `references/process-and-hitl.md` — `process:` blocks, gates, builders, escalations, GenUI
- `references/determinism.md` — turning prose guards into enforced ones
