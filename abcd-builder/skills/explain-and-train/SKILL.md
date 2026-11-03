---
name: explain-and-train
description: "Generate the explainer and the training kit for an Adopt agent: an interactive explainer HTML (steps, skills, versions, dataset, criteria, checks, last eval, open items) and training material (quickstart, simple prompts, scenarios, FAQ, what is new) that ship on every release, with prompts proven on a dataset case. Use when the user says explain this agent, write the explainer, generate the training kit, quickstart for the agent, example prompts for users, FAQ, what is new in this release, or help a new FDE understand the agent."
---

# explain-and-train

Two generated artefacts per agent, refreshed on every release:

- `harness/agents/<agent>/docs/explainer.html` - for FDEs who need to understand the current
  implementation.
- `harness/agents/<agent>/training/` - quickstart, prompts, scenarios, FAQ, what's new - for the
  client's users.

Run from the root of an abcd checkout, in the client workspace.

## When to use

- A release is about to happen or just did (`release-manager` runs this for you).
- A new FDE joins an agent, or the client asks how to use it.

## Step 1 - explain

```bash
abcd explain <agent> --env <env> --json
```

Open the HTML and check it against the repo: steps match `instructions.md` and `process.yaml`,
versions match tags, criteria match the last report. Fix the source, not the HTML.

## Step 2 - train

```bash
abcd train <agent> --env <env> --dry-run --json
abcd train <agent> --env <env> --json
```

Prompts in the kit are run on a dataset case during `test-staged`; only the ones that work ship.
The FAQ comes from real questions in runs and HITL. A kit that fails to generate is flagged on
the release, but does not block it.

## Rules

- **Approvals:** `--yes` only after the human said so. Client-facing text is reviewed by the
  member before it is published to the status workstream.
- **Safety:** no client names, values, expected outputs or case ids in generated material that
  could leave the workspace, and none in the abcd repo; regenerate onto a
  `release/<agent>@v<n>` branch, never the default branch; never deploy-default for client work;
  BFF with the PAT only.

## Hand-offs

- Releasing: `release-manager`. Missing facts in the explainer: `context-and-kg`.
- Status files for the client: `abcd status publish` (see `session-debug` and `workspace-setup`
  for cloud runs).
