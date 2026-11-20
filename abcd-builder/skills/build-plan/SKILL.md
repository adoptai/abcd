---
name: build-plan
description: "Define and approve what the Adopt Builder will build for a client: propose the skills, plugins and agents to build or change from the five inputs (metrics, ground truth, knowledge graph, call context, process docs), show the plan to the member, record approval, and mark items done. Nothing is built until the plan is approved. Use when the user says what should we build, plan the build, propose a build plan, approve the plan, which skills and agents do we need, add this to the plan, what is left to build, or mark the plan item done."
---

# build-plan

The build plan (`bld_build_plan`) is the first of the FDE's four approvals. Items move through
proposed, approved, building and done. **Nothing is built until its item is approved.**

Run from the root of an abcd checkout, in the client's workspace (`--env <env>`).

## When to use

- At the start of a client engagement, and whenever scope changes.
- When a context gap, a trace review or a NoUI compile adds work (new skill, changed agent).
- Before `agent-builder`, `dataset-builder` or `skill-builder` start building anything.

## Step 1 - gather the inputs

```bash
abcd context pull <agent> --env <env> --json
abcd plan show --env <env> --json
```

Read the five inputs: metrics, ground truth, the knowledge graph, call context, process docs.
Ask the member only what these do not settle, and record each answer as a decision (see
`context-and-kg`).

## Step 2 - propose

```bash
abcd plan propose <item_key> --kind skill --title "<what it does>" --agent <agent> --env <env> --dry-run --json
abcd plan propose <item_key> --kind skill --title "<what it does>" --agent <agent> --env <env> --json
```

Run it once per item (`--kind` is `skill`, `plugin` or `agent`; `--action` is `new`, `change` or `retire`;
`--reason-ref` records the decision, run or ticket). Each item names a skill, plugin or agent, why it is needed, which dataset will prove it, and
what it depends on. A NoUI browser skill also proposes a draft `goal_reached` check.
Show the member the plan in plain words, grouped by agent.

## Step 3 - approve (human only)

Ask plainly: "Do you approve these items?" Only after a clear yes:

```bash
abcd plan approve <item_key> --by "<member name>" --env <env> --yes --json
```

In a cloud run `plan approve` is refused (`--by` is a self-asserted name): the member approves
locally. Approve per item, not "all", unless the member said all. Never approve on the member's
behalf, and never treat silence as consent.

## Step 4 - track and close

```bash
abcd plan show --env <env> --json
abcd plan done <item_key> --env <env> --json
```

Mark an item done only when its artifact is released or explicitly dropped.

## Rules

- **Approvals:** `--yes` only after the human said so. Loosening a target or editing an
  expected output needs a lead's recorded approval, not just the FDE's.
- **Safety:** no client data in the abcd repo; plan changes are committed to a workspace
  branch, never the default branch; never deploy-default for client work; BFF with the PAT
  only.

## Hand-offs

- Approved agent items: `agent-builder`.
- Approved dataset, check or metric items: `dataset-builder`.
- Approved site-to-skill items: `discover-and-plan`, then `skill-builder`.
- Questions the plan could not settle: `context-gaps` or `context-and-kg`.
