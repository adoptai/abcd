---
name: discover-and-plan
description: "Turn a live website into agent-callable operations, inside an abcd workspace: record a login and workflow through a Tabby browser session (NoUI capture), compile the capture deterministically into a draft skill or MCP server, and generalize it (prune noise, rename, reword) through a plan the member confirms. Use when the user says record a workflow, record a login, capture this website, turn this site into a skill or tool, onboard a site into Tabby, there is no API for this, automate this web app without computer use, generalize or clean up the compiled operations, or recompile from a saved bundle. Hands off to skill-builder to verify, install and publish."
---

# discover-and-plan

Record what a website's browser already does, and turn it into a clean, named set of
operations. This is the front of the funnel; `skill-builder` verifies, installs and
publishes what this skill produces. For APIs the Adopt platform already knows, use
`action-builder` (WDL) instead.

Run everything from the root of an abcd checkout, through the **workspace bridge**:

```bash
python cli/noui_workspace.py [--env <env>] <noui-script> [args...]
```

The bridge runs the NoUI toolkit (`cli/noui/`) with the workspace's configuration and
writes everything under `workspaces/<env>/harness/`:

| Path (`ws:` prefix in args) | Holds |
|---|---|
| `ws:bundles/<name>-<session>.json` | raw capture bundles — the source of truth; never delete |
| `ws:sessions/<session_id>.json` | provision ledger (how a recording was opened) |
| `ws:skills/<skill_id>/` | compiled skills — exactly where `harness_skill.py push` reads |
| `ws:mcp_servers/<app>/<server>/` | compiled MCP servers |
| `ws:login_recordings/` | compiled login drafts |

Never hand-write `operations.json`, `manifest.json`, `recording_bundle.json` or
`replay_approval.json`. Record, compile, generalize through the plan, replay.

## Step 0 — workspace and Tabby

```bash
python cli/noui_workspace.py --env <env> doctor     # resolved config + Tabby reachability
```

`doctor` prints the auth mode the bridge pinned and why:

| Mode | When | Workspace `.env` needs |
|---|---|---|
| `platform_jwt` | cloud Tabby, per-user identity | `ADOPT_WEBUI_ENDPOINT`, `ADOPT_HARNESS_PAT_CLIENT_ID/SECRET`, `TABBY_API_URL` |
| `agent_token` | local / self-hosted Tabby | `TABBY_API_URL`, `TABBY_CLIENT_ID/SECRET` (+ `TABBY_ADMIN_TOKEN` to register templates) |
| `broker` | only inside the Agent Harness sandbox | nothing to configure here |

The WDL `ADOPT_CLIENT_ID/SECRET` are never passed to NoUI. Set `NOUI_TABBY_AUTH_MODE`
explicitly to override the inference.

No Tabby yet? `python cli/tabby_bootstrap.py up --env <env> --write-env` starts the API tier
from the pinned `tabby/` submodule and writes agent credentials into the workspace. The API
tier registers App Templates and profiles but **cannot record**: recording and `/execute`
need browser workers — a cloud Tabby (`platform_jwt`) or `python cli/tabby_bootstrap.py full`.

## Step 1 — agree on the goal (one line), then record

Ask for the site (login URL) and a short app name, and write the goal back in one line:
"download the annual statement for last year", "list open invoices". Describe the GOAL to
the human, never a click list — see [references/capture-rules.md](references/capture-rules.md).
It is the most common cause of a useless recording.

```bash
# Default: login + workflow in ONE session (one link, one sign-in).
python cli/noui_workspace.py --env <env> capture_record --url https://app.example.com/login --name example
```

- `--name` first checks for an existing App Template; reuse its profile instead of recording
  the login again (`--profile <slug>` records an authenticated workflow only; `--force` bypasses).
- Banks, brokerages, card issuers, lenders: always add `--residential-proxy`.
- The user said "browser based" / the app encrypts or signs requests in the page: add
  `--browser-driven`.
- Static API-key app (no login): `--mode workflow --auth-type api-key`.

Hand over the printed viewer link **verbatim** (never build one), and wait for the human to
finish the goal and click "Finish & export".

## Step 2 — import and confirm the KIND

```bash
python cli/noui_workspace.py --env <env> capture_import <session_id> --as skill --execution-mode harness --name example
```

The import splits a combined capture at the login boundary, registers the login as an App
Template, saves the bundle, and compiles the workflow. It prints one of:

```
KIND: browser-driven — ...
KIND: replay (call_web_api) — auto-detected: ...
```

When it is followed by CONFIRM THE KIND, tell the member which kind was chosen and why, and
ask before going on. Re-import with `--browser-driven` if they asked for a browser skill or the
app signs requests in-page (a replay skill would 403 later).

Use `--execution-mode harness` for the Agent Harness (`call_web_api` / `call_web_browser`);
`--as both --execution-mode tabby` for a local MCP server + skill.

Something surprising? Inspect the bundle before anything else:

```bash
python cli/noui_workspace.py --env <env> bundle_inspect ws:bundles/<name>-<session>.json
```

**Is the goal in there?** If the user asked for a download and no `download` operation was
emitted, the recording missed it — re-record. Never hand-write the operation.

## Step 3 — generalize through a confirmed plan

The compile is a raw mirror of the recording: keepalives, telemetry, duplicates, and names
lifted from URL paths. Clean it up — you decide, the script applies:

```bash
python cli/noui_workspace.py --env <env> generalize draft ws:skills/<skill_id>
#   edit ws:skills/<skill_id>/generalize_plan.json — per operation: decision.action keep|drop,
#   rename, description, params {<path param>: {rename?, description?}}
python cli/noui_workspace.py --env <env> generalize show ws:skills/<skill_id>   # show the member
python cli/noui_workspace.py --env <env> generalize confirm ws:skills/<skill_id> --by "<member>"
python cli/noui_workspace.py --env <env> generalize apply ws:skills/<skill_id>
```

- The draft flags keepalive/telemetry/duplicates for `drop` and third-party hosts as
  `REVIEW:` (kept — a workflow can span an auth host and an API host). Check each one.
- `apply` refuses an unconfirmed plan. Only `confirm` after the member agreed — the plan
  records who confirmed.
- **Replay skills**: drop, rename, reword, rename *path* parameters, describe any parameter.
  Query/body parameter names go on the wire and cannot be renamed.
- **Browser skills**: rename and reword only. Steps, parameters and which operations exist
  come from the recording; the installer refuses anything else. A rename invalidates an
  existing replay approval — replay again (skill-builder).

## Recompile without re-recording

The saved bundle regenerates the skill (HAR-replay skills):

```bash
python cli/noui_workspace.py --env <env> compile_workflow ws:bundles/<file>.json \
    --as skill --execution-mode harness --profile-slug <slug> --name <app>
```

A wrong `--profile-slug` is refused when Tabby is reachable (it lists the known profiles).

Autopilot (an agent drives an existing profile, no human VNC):
`capture_autopilot <profile-slug> --steps steps.json --as skill --execution-mode harness`.

## Hand-off

Once the plan is applied: `skill-builder` — replay/verify, install locally, push and run on
the Agent Harness. `python cli/noui_workspace.py --env <env> list` shows every bundle, session
and compiled skill in the workspace, with kind, provenance and replay/approval state.

## References

- [references/capture-rules.md](references/capture-rules.md) — what to tell the human, the two
  sessions, what not to hand-write
- `cli/noui/README.md` — the full toolkit reference (every script)
- `cli/noui/references/pillar-1-capture.md`, `pillar-2-compile.md`, `generalize.md`,
  `auth-modes.md`, `tabby-setup.md`
