---
name: context-gaps
description: "Close context gaps found by blind evals: classify each miss, open a gap, route it to the FDE or the customer SME, record the answer, encode it as a generic instruction (with the leak check), compile the instruction skill, verify with a blind re-run, or waive with a lead's reason; plus the build-phase exit check. Use when the user says why did the blind eval miss, open a gap, ask the SME, answer this gap, encode the answer, compile instructions, verify the gap, waive the gap, leak check, are we done with the build phase, or build exit."
---

# context-gaps

An agent is ready when it reproduces the golden dataset blind, on generic prompts, compiled
instructions and skills alone. Every case it cannot reproduce because context is missing is a
gap. Gap states: open, asked, answered, encoded, verified, waived.

Run from the root of an abcd checkout, in the client workspace.

## When to use

- A blind eval left misses (`eval-runner`).
- The SME answered something, or uploaded repro notes.
- Deciding whether the build phase can end.

## Step 1 - list and open

```bash
abcd gap list --env <env> --json
abcd gap open --from-run <eval_run_id> --env <env> --json
```

Classify each miss first. Only **missing context** opens a gap; skill bugs, rounding,
playbook differences and real errors become build plan items. The gap file holds the input
excerpt, observed versus golden output, the agent's reasoning and your hypothesis of what is
missing. It stays in the SME workstream, never in a blind run's view.

## Step 2 - route and answer

```bash
abcd gap ask <gap_id> --env <env> --json
abcd gap answer <gap_id> --file answer.md --by "<name>" --env <env> --json
```

The FDE can answer, or `gap ask` sends it to the customer SME. Record who answered (in a cloud
run `--by`, `--vetted-by` and `--lead` are refused: the actor is always the cloud identity). Case-specific
explanations are repro notes, not instructions. Ask the member before closing out a question
the SME has not seen.

## Step 3 - encode and compile

```bash
abcd gap encode <gap_id> --page rule.md --scope agent --vetted-by "<name>" --env <env> --json
abcd instructions compile <agent> --env <env> --json
abcd instructions sync <client> --agent <agent> --env <env> --json
```

Write the instruction as a generic rule in a markdown page (`--page`) and encode it with `gap encode`. The **leak check** refuses any instruction that names a
case id, quotes a value from an expected output, or only makes sense for one case; rewrite it
rather than working around the check. `compile` builds `harness/skills/<agent>-instructions/`
in the workspace; `sync` copies client-scope instructions into the client's workstreams.

## Step 4 - verify or waive

Re-run the affected cases blind, then the full set (`eval-runner`), then:

```bash
abcd gap verify <gap_id> --run <run_id> --env <env> --json
abcd gap waive <gap_id> --lead "<lead name>" --reason "..." --env <env> --yes --json
abcd build-exit <agent> --env <env> --json
```

A gap closes only when a blind run reproduces its case without regressing another. **Waiving
needs a lead's recorded reason**; an FDE alone does not waive. Build phase exit: every active
case reproduces or is waived, no gap is open, asked, answered or encoded, no instruction has a
flagged leak check, and the blind reproduction rate meets its criterion.

## Rules

- **Approvals:** `--yes` only after the human said so; waiving is a lead's approval; the SME or
  FDE vets instructions, never you.
- **Safety:** no client data in the abcd repo; answers are generic context and live in the SME
  Doc Store, never in tables as values; never commit to the default branch; never deploy-default
  for client work; BFF with the PAT only.

## Hand-offs

- Re-running blind: `eval-runner`. Instruction pages and decisions: `context-and-kg`.
- Instruction skill goes live with the agent: `release-manager`.
