---
name: skill-builder
description: "Verify, install and publish a skill built from a NoUI capture (or hand-written) for the Adopt Agent Harness, from an abcd workspace: replay a browser skill against the live app and record the member's approval, install it locally into Claude Code, Codex, Cline or OpenCode, then audit, push, run and trace it on the harness, bundle skills into a plugin, or publish to the platform default tier. Use when the user says replay the skill, approve the replay, install the skill, push the skill to the harness, test it on the harness, debug a harness run, review the trace, bind it to a process, build a plugin, or publish the noui skill."
---

# skill-builder

The back half of the skill funnel: take a compiled skill from
`workspaces/<env>/harness/skills/<skill_id>/` (produced by `discover-and-plan`) — or any
hand-written skill directory — and get it verified, installed and running on the Agent Harness.
For WDL actions use `action-builder`.

Run from the root of an abcd checkout. NoUI scripts go through the workspace bridge
(`abcd noui <script> --env <env> ...`, `ws:` = the workspace harness root);
harness commands are `abcd skill ...`, `abcd run ...`, `abcd trace ...` and `abcd workstream ...`, each with `--env <env>`.

Order matters: **verify locally → install locally → push → run on the harness**. The local
half is free and deterministic; the harness run should be confirming parity, not doing
first-draft debugging.

## Step 1 — verify before anything is installed

Check the kind first: `abcd noui list --env <env>` shows each skill's kind,
provenance and replay/approval state.

**Browser skills — the replay gate (mandatory).** The installer refuses a browser skill with no
approved replay.

```bash
abcd noui verify_replay --env <env> ws:skills/<skill_id> --profile-slug <slug>
#   exit 2 = no signed-in session: show the runtime sign-in card, wait for the member, re-run.
```

Show the member, per operation: whether it **reached its goal** (not just how many steps ran),
which steps were blocked, which were improvised, and anything held for approval. Ask plainly if
it looks right. Only after they say yes:

```bash
abcd noui verify_approve --env <env> ws:skills/<skill_id>
```

One replay. Re-run only if the member asked for one or changed something, or the replay failed
and you changed something since. Never approve on the member's behalf. Details:
[references/replay-gate.md](references/replay-gate.md).

**Replay (`call_web_api`) skills.** Run each surviving operation 2–3 times against the live
site and confirm it returns the expected data. On the harness: the `call_web_api` tool with the
recipe from `operations.json`. For a local MCP server:
`abcd noui activate_verify --env <env> ws:mcp_servers/<app>/<server_id>`.
Fix or drop failures through the generalize plan (`discover-and-plan` step 3), never by
editing `operations.json` by hand.

## Step 2 — install locally (optional)

```bash
abcd noui activate_install --env <env> ws:skills/<skill_id> claude-code [--project]
#   agents: claude-code | codex | cline | opencode | agents;  --uninstall to remove
```

## Step 3 — push to the harness (org tier)

```bash
abcd skill audit workspaces/<env>/harness/skills/<skill_id>   # free, local
abcd skill push workspaces/<env>/harness/skills/<skill_id> --env <env> --dry-run   # local checks
abcd skill push workspaces/<env>/harness/skills/<skill_id> --env <env> [--replace]
```

`--dry-run` is fully local: it checks the directory, `SKILL.md`, the frontmatter lint, the name
slug, the secret scan and the audit (unless `--skip-audit`), then lists what would be uploaded
(name, aux files, new or `--replace`). It exits non-zero on a bad directory and never calls the
platform. `abcd plugin push --dry-run` does the same for a plugin.

In a harness sandbox with no workstream repo, run `abcd cloud bootstrap --platform-only` first
(then pass `--env cloud`; `abcd cloud doctor` shows what is missing). The `abcd` harness skill
has the full cloud path under "Build and publish a skill or agent".

`push` lints the frontmatter with the platform's strictness and refuses to stage dev-only
files by name. It also **content-scans** SKILL.md and every aux file, including members of
zipped aux files, for credentials, and refuses on a finding. It then runs the adopt-skill-review
audit (critical findings block unless `--force`), uploads, and re-fetches to confirm the live
skill parsed as intended. All of that happens before the PAT is exchanged.

- Needs `ADOPT_WEBUI_ENDPOINT` + `ADOPT_HARNESS_PAT_CLIENT_ID/SECRET` (an org-admin PAT) in the
  workspace `.env`. A 409 means the name exists: `--replace`.
- The catalog slug is the SKILL.md `name`. Prefix skills you are iterating on (for example
  `acme-…`) so they don't collide with production skills in a shared org.
- `abcd skill verify <name> --env <env>` re-checks a live skill (the fetched body has the
  frontmatter stripped, so check the process block through `has_process`);
  `delete <name> --yes` removes one.
- Look before you push: `abcd skill list [--source org|default] [--search <text>]` shows the
  catalog with the tier and `has_process`, `abcd plugin list` the plugins. A name listed once as
  `org` can be hiding a default skill of that name (org wins). Names are slugs
  (`^[a-z0-9][a-z0-9-]*$`).
- A **gated step** (a human approves) is frontmatter, not body. Quote any value that contains
  a colon, or the whole `process:` block is dropped silently and the skill degrades to plain text:

  ```yaml
  process:
    title: "Gate check"
    steps:
      - { id: prepare, title: "Prepare note" }
      - id: approve
        title: "Human approval"
        assignee_role: human
        gate: { type: approval, role: Reviewer }
  ```

  After the push, `abcd skill list --search <name>` must show `process`. In the body tell the
  agent to render a `render_builder` card bound to the gate's `step_id` and to end the turn; the
  agent may set that step to `review`, never `done` (`session-debug` resolves it).

## Step 4 — run it on the harness and read the trace

```bash
abcd workstream ensure <workstream> --env <env>      # reuses the default "Workstream Files" store
abcd session models --env <env>                      # pick the cheapest model for a test run
abcd run start --env <env> --workstream <workstream> --message "<what a member would ask>"
abcd trace review <run_id> --env <env>
abcd run continue <run_id> --env <env> --message "..."
```

`abcd run start` runs **one turn and stops**; `--model` defaults to the org's own default and
`--process` takes a name or an id. For `--json`, the final text, tool calls and pending cards,
use `abcd session start|send` instead (`session-debug`). If it looks wrong, fix the local skill and
`push --replace` before continuing the conversation. Traces persist under
`workspaces/<env>/harness/traces/<run_id>/`. Org-wide history: `harness_runs.py`,
`harness_conversations.py`. Bind a skill to an Agents-tab process for deterministic selection:
`harness_process.py create|update ... --component skill:<name>`. More:
[references/harness-loop.md](references/harness-loop.md).

## Step 4b — wire it into an agent

A skill does nothing for an agent until it is a component of that agent's bundle. After the
replay is approved (browser skills) and the skill runs on the harness:

```bash
abcd wire skill <skill_id> --agent <agent> --env <env>        # process.yaml component, idempotent
abcd wire kg <skill_id> --agent <agent> --env <env>           # KG pages + uses/calls edges (proposed/)
abcd wire goal-check <skill_id> --agent <agent> --env <env>   # draft goal_reached check + plan item
abcd wire all <skill_id> --agent <agent> --env <env>          # the three, in order
```

`wire skill` refuses a browser skill with no approved replay and names the replay and approval
steps. The KG pages hold operation names, kinds, the goal and provenance digests, never values
from the recording. The goal check stays a draft until an FDE approves its build-plan item
(`abcd plan approve`); then `abcd wire goal-check <skill_id> --agent <agent> --activate` turns it
on. Push the agent with `abcd agent push <agent>`.

## Step 5 — plugins and the default tier

Several skills that belong together ship as one plugin:

```
<plugin>/
  .claude-plugin/plugin.json      {"name": "<slug>", "version": "...", "description": "..."}
  skills/<skill-slug>/SKILL.md    (name == directory) + aux files
```

```bash
abcd plugin push <plugin_dir> --env <env> [--replace]
abcd plugin list --env <env>
```

A plugin's skills are admitted to an agent through a `plugin:<slug>` component, not through
`skill:<name>` components. The plugin's version, description and `skill_count` come back from
the push.

The platform **default ("Built-in") tier** is visible to every org and gated to `@adopt.ai`
principals. It is how the NoUI harness skill itself ships (`harness-skills/noui/`, CI
`.github/workflows/deploy-skill.yml`):

```bash
python harness-skills/noui/build_bundle.py
abcd skill deploy-default harness-skills/noui --env <env> \
    --aux noui-bundle.zip=harness-skills/noui/noui-bundle.zip --bundle-version <sha> [--dry-run]
```

`deploy-default` is refused in a cloud (harness sandbox) run, even with `--dry-run`: run it from a
local machine, and from a cloud chat push to the org tier with `abcd skill push` instead.

## Rules

- Never hand-write `operations.json`, `manifest.json`, `recording_bundle.json` or
  `replay_approval.json`.
- No credentials in skill files — use `${SECRET:name}` placeholders and the harness secret store.
- A `login_required` needs a runtime sign-in (the platform's card), never a new recording.
- Publishing to the noui marketplace repository is not wired up yet. Ship through the harness
  tiers above.
