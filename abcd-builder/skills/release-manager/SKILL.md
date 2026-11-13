---
name: release-manager
description: "Take an Adopt agent from draft to staged to live safely: freeze a staged version, test the staged agent blind, run the release gate and promote to live, roll back, redeploy from a tag, audit drift, and follow the default-tier rules. Use when the user says stage the agent, freeze this version, test staged, release the agent, publish the new version, go live, roll back, redeploy, audit drift, did someone edit live, why is the release blocked, force a release, or default skill collision."
---

# release-manager

Every skill and agent has a live object and a staged shadow (`<name>--staged`, `<agent>-staged`).
`abcd release` is the only sanctioned path to live; it runs the gate on the exact staged hashes.
Releases go to the org tier only and never call `deploy-default`.

Run from the root of an abcd checkout, in the client workspace. The commit must be pushed and
the tree clean.

## When to use

- The eval gate looks ready and the member wants to freeze and ship.
- A release or rollback is requested, or the member suspects live was edited in the UI.

## Step 1 - push to the shadows and stage

```bash
abcd push --staged --dry-run --env <env> --json
abcd push --staged --env <env> --json            # everything staged in the workspace
abcd push --staged <agent> <skill> --env <env> --json   # only these (other agents' shadows untouched)
abcd stage <agent> --env <env> --json
```

`stage` refuses on uncommitted changes, an unpushed commit, a failed audit (stricter than push),
a secret-scan hit, a browser skill without a fresh replay approval, or another FDE's staged lock.
Report the holder; do not take a lock over without the member's say.

## Step 2 - test the staged agent

```bash
abcd test-staged <agent> --env <env> --json
abcd audit drift --env <env> --json
```

`test-staged` is blind. Show the member the criteria table; it must pass on these exact hashes.

## Step 3 - release (human approves)

```bash
abcd release <agent> --dry-run --env <env> --json
```

Show the dry run, then ask plainly: "Do you approve releasing this to live?" Only after a clear yes:

```bash
abcd release <agent> --yes --env <env> --json
# no GitHub remote or `gh`: the tag and release branch still land and the GitHub release/PR are
# listed under "flags", not as a failure
```

In a cloud run (a chat in the Builder home org) `--yes` alone is refused: the member's own
message in that chat must carry the release approval token. The human types it in the chat UI
(`abcd hitl approve-release` is refused in the cloud: a human sends approvals from the chat UI,
not the agent); `sha12` is in the dry run and in `abcd stage`'s output. abcd accepts the token
only from a user other than the one the PAT authenticates as, in the conversation the sandbox
was started for. `abcd rollback` needs the same token naming the target version; `agent push`
without `--staged` and `--redeploy` are refused in the cloud; `--force-reason`, `--lead`,
`--accept-regression` and `--local-approval` are refused there, a human does those locally.

Release deploys exactly what `stage` froze: if a newer `push --staged` exists it refuses with
"a newer staged push exists; run `abcd stage <agent>` again". It cuts `release/<agent>@v<n>`
from `origin/<default branch>`, never from your working branch, and a re-staged commit gets
the tag `<agent>@v<n>-rc.<k>`.

The release verifies the shadows and drift, requires the staged commit on the default branch
(already merged by pull request), checks the criteria, uploads under live names, patches the
process, writes the eval report, and tags `<agent>@v<n>`. Generated files go to a
`release/<agent>@v<n>` branch with a pull request; never push to the default branch.

## Rollback and redeploy

```bash
abcd rollback <agent> --yes --env <env> --json
abcd release <agent> --redeploy <agent>@v<n> --yes --env <env> --json
```

Both need a lead's approval and are recorded. Rollback re-uploads the previous tag and verifies.

## When the gate refuses

- A failed criterion or an error: fix the cause (`eval-runner`, `context-gaps`). Do not retry
  until it passes by luck.
- **Forcing a release, a staged commit off the default branch, or a name that collides with a
  default-tier skill all need a lead's recorded decision.** You cannot approve those; say so and
  ask the lead. A forced release stays flagged until a passing run catches up.
- Drift: a live object that differs from its last release. Open an item for the owner; do not
  overwrite live to hide it.

## Rules

- **Approvals:** `--yes` only after the human said so, per step. Never approve a browser skill
  on the member's behalf: its replay approval and approval token are the member's own. A replay
  approved locally goes in only with `--local-approval`, and the report shows it.
- **Safety:** no client data in the abcd repo; never the default branch directly; never
  `deploy-default` for client work; BFF with the PAT only; WDL objects keep their own scripts
  (`action-builder`, `pipeline-builder`).

## Hand-offs

- Gate failing on blind misses: `eval-runner`, `context-gaps`. Browser approval: `skill-builder`.
- After a release: `explain-and-train`. A broken live conversation: `session-debug`.
