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
(`python cli/noui_workspace.py --env <env> <script> ...`, `ws:` = the workspace harness root);
harness commands are `python cli/harness_*.py ... --env <env>`.

Order matters: **verify locally → install locally → push → run on the harness**. The local
half is free and deterministic; the harness run should be confirming parity, not doing
first-draft debugging.

## Step 1 — verify before anything is installed

Check the kind first: `python cli/noui_workspace.py --env <env> list` shows each skill's kind,
provenance and replay/approval state.

**Browser skills — the replay gate (mandatory).** The installer refuses a browser skill with no
approved replay.

```bash
python cli/noui_workspace.py --env <env> verify_replay ws:skills/<skill_id> --profile-slug <slug>
#   exit 2 = no signed-in session: show the runtime sign-in card, wait for the member, re-run.
```

Show the member, per operation: whether it **reached its goal** (not just how many steps ran),
which steps were blocked, which were improvised, and anything held for approval. Ask plainly if
it looks right. Only after they say yes:

```bash
python cli/noui_workspace.py --env <env> verify_approve ws:skills/<skill_id>
```

One replay. Re-run only if the member asked for one or changed something, or the replay failed
and you changed something since. Never approve on the member's behalf. Details:
[references/replay-gate.md](references/replay-gate.md).

**Replay (`call_web_api`) skills.** Run each surviving operation 2–3 times against the live
site and confirm it returns the expected data. On the harness: the `call_web_api` tool with the
recipe from `operations.json`. For a local MCP server:
`python cli/noui_workspace.py --env <env> activate_verify ws:mcp_servers/<app>/<server_id>`.
Fix or drop failures through the generalize plan (`discover-and-plan` step 3), never by
editing `operations.json` by hand.

## Step 2 — install locally (optional)

```bash
python cli/noui_workspace.py --env <env> activate_install ws:skills/<skill_id> claude-code [--project]
#   agents: claude-code | codex | cline | opencode | agents;  --uninstall to remove
```

## Step 3 — push to the harness (org tier)

```bash
python cli/harness_skill.py audit workspaces/<env>/harness/skills/<skill_id>   # free, local
python cli/harness_skill.py push workspaces/<env>/harness/skills/<skill_id> --env <env> [--replace]
```

`push` lints the frontmatter with the platform's strictness and refuses to stage dev-only
files by name. It also **content-scans** SKILL.md and every aux file, including members of
zipped aux files, for credentials, and refuses on a finding. It then runs the adopt-skill-review
audit (critical findings block unless `--force`), uploads, and re-fetches to confirm the live
skill parsed as intended. All of that happens before the PAT is exchanged.

- Needs `ADOPT_WEBUI_ENDPOINT` + `ADOPT_HARNESS_PAT_CLIENT_ID/SECRET` (an org-admin PAT) in the
  workspace `.env`. A 409 means the name exists: `--replace`.
- The catalog slug is the SKILL.md `name`. Prefix skills you are iterating on (for example
  `acme-…`) so they don't collide with production skills in a shared org.
- `python cli/harness_skill.py verify <name> --env <env>` re-checks a live skill;
  `delete <name> --yes` removes one.

## Step 4 — run it on the harness and read the trace

```bash
python cli/harness_workstream.py ensure <workstream> --env <env>
python cli/harness_run.py start --env <env> --workstream <workstream> --message "<what a member would ask>"
python cli/harness_trace.py review <run_id> --env <env>
python cli/harness_run.py continue <run_id> --env <env> --message "..."
```

`harness_run` runs **one turn and stops**. If it looks wrong, fix the local skill and
`push --replace` before continuing the conversation. Traces persist under
`workspaces/<env>/harness/traces/<run_id>/`. Org-wide history: `harness_runs.py`,
`harness_conversations.py`. Bind a skill to an Agents-tab process for deterministic selection:
`harness_process.py create|update ... --component skill:<name>`. More:
[references/harness-loop.md](references/harness-loop.md).

## Step 5 — plugins and the default tier

Several skills that belong together ship as one plugin:

```
<plugin>/
  .claude-plugin/plugin.json      {"name": "<slug>", "version": "...", "description": "..."}
  skills/<skill-slug>/SKILL.md    (name == directory) + aux files
```

```bash
python cli/harness_skill.py push-plugin <plugin_dir> --env <env> [--replace]
```

The platform **default ("Built-in") tier** is visible to every org and gated to `@adopt.ai`
principals. It is how the NoUI harness skill itself ships (`harness-skills/noui/`, CI
`.github/workflows/deploy-skill.yml`):

```bash
python harness-skills/noui/build_bundle.py
python cli/harness_skill.py deploy-default harness-skills/noui --env <env> \
    --aux noui-bundle.zip=harness-skills/noui/noui-bundle.zip --bundle-version <sha> [--dry-run]
```

## Rules

- Never hand-write `operations.json`, `manifest.json`, `recording_bundle.json` or
  `replay_approval.json`.
- No credentials in skill files — use `${SECRET:name}` placeholders and the harness secret store.
- A `login_required` needs a runtime sign-in (the platform's card), never a new recording.
- Publishing to the noui marketplace repository is not wired up yet. Ship through the harness
  tiers above.
