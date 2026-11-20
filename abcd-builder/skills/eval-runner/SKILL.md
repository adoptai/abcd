---
name: eval-runner
description: "Run and judge evals for an Adopt agent against its dataset: local runs, blind runs on the platform, scoring, judged-check packets scored in the coding agent, the eval report, the release gate check, and performance review of traces. Use when the user says run the evals, run a blind eval, score the run, judge these cases, did the agent pass, check the gate, eval report, why did this case fail, review the trace, make it faster, regression, or how does it do blind."
---

# eval-runner

Evals run each case the way its dataset kind says and score it. A **blind** run gives the agent
only a generic prompt, the case inputs and the compiled instructions and skills; expected
outputs, case notes, gap files and any follow-up hint stay withheld. Every gated eval is blind.

Run from the root of an abcd checkout, in the client workspace.

## When to use

- After building or changing an agent, skill or instruction.
- To see where an agent stands against its release criteria.
- To score judged checks, or to find why a run was slow or expensive.

## Step 1 - run

```bash
abcd eval run <agent> --tier local --env <env> --json
abcd eval run <agent> --blind --env <env> --json
```

Gate on the staged agent with `--staged`. `--model <id>` picks the model (`GET /models`; the org
default is usually the priciest, so use a cheap one for test runs). Submit and poll separately with
`--submit-only`, then `--resume <run_id>`. `eval report` writes its copies under
`.builder/evals/<run>/` (git-ignored); the committed report comes from `release`.

Platform runs are resumable: the command records turn ids, and calling it again polls and
scores. Report the run id and cost. If a run errors, say so; an error is never a pass.
Anything the member should not see (expected values) stays out of what you paste back.

## Step 2 - judged checks

Judged checks are scored by you, locally:

```bash
abcd eval judge-packet <run_id> --env <env> --json
abcd eval judge-apply <run_id> --scores scores.json --env <env> --json
```

Read each packet, score honestly against the rubric, and never see or use the expected values
beyond what the packet shows.

## Step 3 - report and gate

```bash
abcd eval report <run_id> --env <env> --json
abcd eval gate <agent> --run <run_id> --env <env> --json
```

The gate lists every criterion against the staged run. Exit code 1 means a criterion failed;
tell the member which, never reword a failure as a pass.

## Performance review

```bash
abcd trace fetch <turn_id> --conversation-id <conversation_id> --env <env> --json
abcd trace review <run_id> --env <env> --json
```

Look at step budget, batching, latency and cost, then change the skill, not the dataset.

## Misses

Classify every miss: missing context, skill bug, rounding, playbook difference, real error.
Only missing context opens a gap (`context-gaps`); the others go back to `build-plan` items.

## Rules

- **Approvals:** `--yes` only after the human said so. Never rerun until it passes and
  quietly drop failures; never edit expectations to turn a miss into a pass.
- **Safety:** no client data or expected values in the abcd repo or in chat logs that leave the
  workspace; never commit to the default branch; never deploy-default for client work; BFF with
  the PAT only.

## Hand-offs

- Missing context: `context-gaps`.
- Dataset wrong or incomplete: `dataset-builder`.
- Passing and ready to freeze: `release-manager`.
- Debugging one live conversation: `session-debug`.
