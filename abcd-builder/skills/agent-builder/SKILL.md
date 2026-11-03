---
name: agent-builder
description: "Build an Adopt harness agent (a process) as a versioned bundle in the client workspace from an SOP or process document: process.yaml, instructions.md, flow.json for gated steps, release.yaml, metrics.yaml and prompts.yaml; then diff, push it to the staged shadow and pull live changes back. Use when the user says build the agent, create an agent from this SOP, turn this process doc into an agent, write the agent instructions, add a gated step, add a skill to the agent, push the agent, diff the agent against live, or pull the agent from the platform."
---

# agent-builder

A harness agent is a process: components (skills and plugins), instructions, optional compiled
flow, and workstream assignments. In the workspace it is a bundle at
`harness/agents/<agent>/` - this is not a WDL agent (those live in the workspace `agents/`).

Run from the root of an abcd checkout, in the client workspace. Only build items the member
approved in `build-plan`.

## When to use

- A plan item for an agent is approved.
- An SOP, call transcript or process doc has to become an agent.
- Someone changed an agent in the UI and the repo must catch up.

## Step 1 - scaffold and write the bundle

```bash
abcd agent new <agent> --env <env> --json
```

Edit the files in `workspaces/<env>/harness/agents/<agent>/`:

| File | Holds |
|---|---|
| `process.yaml` | name, display name, components (`type`, `source`, `ref`), `workstreams`, `write_workstreams` by name, `is_enabled` |
| `instructions.md` | the agent's instructions, written from the SOP |
| `flow.json` | compiled flow, only when there are gated steps |
| `release.yaml` | dataset, gate metrics, targets (set with `dataset-builder`) |
| `prompts.yaml` | generic prompts for blind evals (`blind:`); `agent new` scaffolds it, and `eval run --blind` refuses without it |
| `metrics.yaml` | metric definitions; not scaffolded, add it when you define metrics |

Instructions stay generic: describe the process, never a specific case, id or expected value.
Gated steps (HITL) stay gated; do not design them away.

## Step 2 - review the change

```bash
abcd agent diff <agent> --env <env> --json
abcd skill audit harness/skills/<skill> --env <env> --json
```

Audit every skill the agent uses. Show the member the diff in plain words.

## Step 3 - push to the staged shadow

Commit the bundle on a branch first (the pre-commit hook must pass), then:

```bash
abcd agent push <agent> --staged --dry-run --env <env> --json
abcd agent push <agent> --staged --env <env> --json
```

`--staged` builds the `<agent>-staged` shadow and assigns it only to staging and dataset
workstreams. It takes the agent's staged lock; if another FDE holds it, report the holder,
do not take it over. Plain `abcd agent push` without `--staged` writes live objects and is
reserved for `release-manager`.

## Step 4 - pull live back

```bash
abcd agent pull <agent> --env <env> --json
```

Pull writes to the workspace for review; never overwrite local edits without showing the
member the diff.

## Quick path without a bundle (tests and throwaway agents)

`agent new|push|pull|diff` need a bundle in an initialised workspace (`abcd ws init` first:
they stop with "builder.json not found" otherwise). For a throwaway test agent the direct
commands are enough:

```bash
abcd skill list --search <prefix> --env <env> --json          # the skills must exist first
abcd workstream ensure <workstream> --env <env> --json
abcd agent create <name> "<Display name>" --component skill:<skill> --component plugin:<slug> \
    --instructions "<short>" --enable --env <env> --json
abcd agent set-workstreams <process_id> <workstream_id> --env <env> --json
abcd agent get <process_id> --env <env> --json
abcd agent update <process_id> --disable --env <env> --json   # when the test is over
```

- Components are written as `{type, source: org, ref}`; `skill:<name>` admits a standalone org
  skill only, `plugin:<slug>` admits every skill bundled in that plugin. The BFF does not
  validate them, a wrong ref gives an empty skill menu, so check with `abcd agent get`.
- A process must be **enabled** and **assigned to the workstream** before a session or a
  pipeline can use it (`--enable`, `set-workstreams`); a turn on a disabled or unassigned
  process is refused with 403.
- `name` is immutable and there is no hard delete: disable it and leave it.
- `agent set-workstreams` takes workstream ids (`abcd workstream list`).

## Rules

- **Approvals:** `--yes` only after the human said so. A browser skill's replay approval is
  the member's alone: never approve it on their behalf (see `skill-builder`).
- **Safety:** no client data in the abcd repo; never commit to the default branch; never
  deploy-default for client work; BFF with the PAT only; no credentials in bundle files.
- If the agent is linked to a dataset, it cannot go live without a passing blind gate.

## Hand-offs

- Datasets, checks and criteria for the agent: `dataset-builder`.
- Running it: `eval-runner` or `session-debug`.
- Shared knowledge it needs: `context-and-kg`.
- Staging, gate and live release: `release-manager`.
