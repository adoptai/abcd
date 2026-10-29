---
name: abcd
description: "Run abcd, the Adopt Builder CLI, inside this sandbox to drive a client's Builder workspace: status, drift audits, evals, staging, test-staged, releases, explainers and training kits. Use when an FDE asks to run abcd, a nightly or weekly Builder run, or any `abcd ...` command from a chat. Installs abcd from the bundled zip, clones the client's workspace repo, and checks the platform token. NoUI capture and replay are not available here."
---

# abcd (Agent Harness edition)

You are inside the Agent Harness sandbox. This skill installs abcd and runs it against one
client workspace. Everything is done with `bash` and the same `abcd ...` commands an FDE
runs locally.

## Rules

1. **Never print a secret.** Do not echo, log, cat or paste the PAT, a JWT, the GitHub
   token, the GitHub deploy key (`GITHUB_DEPLOY_KEY`, a private key) or the workspace `.env`. If a command output contains one, do not repeat it.
2. **Approvals are the FDE's own chat messages.** A release approval, and the
   browser-skill approval token (`<!--adopt:skill-approval ...-->`), must come from the
   FDE's own message. Never write one yourself, never infer one, never ask abcd to skip
   the gate. In this chat `abcd hitl approve-release` and `approve-skill` are refused: a human
   sends approvals from the chat UI, not the agent. Do not post a token with a raw API call
   either; abcd accepts only a token written by a user other than the one your PAT is.
3. **Never change the default branch.** Code changes go to a pushed `cloud/<conversation>`
   branch (`$ADOPT_CONVERSATION_ID`) and a pull request. Reports and status go to the Doc
   Store and tables through abcd.
4. **NoUI capture and replay are not available in the cloud.** If the FDE asks to record or
   replay a browser flow, tell them to do it locally.
5. Use `--json` on every abcd command and read the exit code: 0 done, 1 refused or a check
   failed, 2 usage error, 3 platform or network error. Report a refusal as it is; do not
   work around it.

## Step 0: install abcd (do this first, silently)

1. Call `read_skill_file(skill_name="abcd", path="abcd-bundle.zip")`. The zip is binary, so the
   harness stages it at `/workspace/skill_assets/abcd/abcd-bundle.zip` (path pattern
   `/workspace/skill_assets/<skill_name>/<path>`).
2. Then one `bash` call (the slim image has no `unzip`; use Python's `zipfile`). Install
   abcd as a package; if that fails, fall back to `PYTHONPATH` plus the core dependencies:
   ```bash
   set -e
   mkdir -p /workspace/abcd
   python -c "import zipfile; zipfile.ZipFile('/workspace/skill_assets/abcd/abcd-bundle.zip').extractall('/workspace/abcd')"
   if pip install /workspace/abcd; then
     :
   else
     pip install -q requests python-dotenv PyYAML httpx pydantic colorama
     pip install -q -e /workspace/abcd/cli/noui
     export PYTHONPATH=/workspace/abcd   # also prefix every later abcd command with it
   fi
   abcd --version || python /workspace/abcd/cli/abcd.py --version
   # the slim image may lack git and ssh; bootstrap clones with them
   command -v git > /dev/null && command -v ssh > /dev/null \
     || (apt-get update -qq && apt-get install -y -qq git openssh-client)
   ```
   This is the core install only; the WDL extras are not installed.

## Step 1: find the workspace

The harness copies the Doc Store files of this workstream into the sandbox. Find the one
that names the workspace:

```bash
find /workspace -name abcd-workspace.json -not -path '/workspace/abcd/*' 2>/dev/null
```

It holds `env`, `repo` and `ref`. Read it to learn which client workspace this run is for.
If there is no such file, stop and tell the user the workstream has no `abcd-workspace.json`.

## Step 2: bootstrap

```bash
cd /workspace/abcd
abcd cloud bootstrap --workspace-file <path from step 1> --json
```

This clones the repo at `ref` into `workspaces/<env>`, writes the workspace `.env` from the
injected secrets and checks the token. If it fails with "missing required environment
variable(s)", tell the user exactly which names it listed, and that each comes from a vault
secret `SANDBOX_<NAME>` in this home org (`SANDBOX_ABCD_PAT_CLIENT_ID`,
`SANDBOX_ABCD_PAT_SECRET`, `SANDBOX_GITHUB_TOKEN` or `SANDBOX_GITHUB_DEPLOY_KEY`, optionally
`SANDBOX_ABCD_WEBUI_ENDPOINT`). Then stop.

**Git access.** Bootstrap needs `GITHUB_TOKEN` (HTTPS) or `GITHUB_DEPLOY_KEY` (SSH, a repo-scoped
deploy key; the token wins when both are set). With a deploy key it writes the key to a 0600 file
under the sandbox home (`~/.abcd/github_deploy_key`), sets the `origin` URL to
`git@github.com:<owner>/<repo>.git` and `core.sshCommand` in the workspace, so plain `git fetch`
and `git push` in `workspaces/<env>` work. **A deploy key cannot call the GitHub API**: with no
`GITHUB_TOKEN`, pull requests and GitHub releases are flagged (the bootstrap output says
`github_api: flagged`), never failed. Push the branch, then tell the user a pull request must be
opened by a person (give the branch name); do not try another way.

## Step 3: drive abcd

Run what the FDE asks with `abcd <group> <command> --env <env> --json` from
`/workspace/abcd`. The commands are the same as local: `abcd status`, `abcd audit drift`,
`abcd eval run`, `abcd stage`, `abcd test-staged`, `abcd release`, `abcd explain`,
`abcd train`, and so on. Use `abcd --help` and `abcd <group> --help` to discover flags.
The builder skills under `/workspace/abcd/abcd-builder/skills/` describe each step.
Always pass `--env`, and set `ABCD_REQUIRE_ENV=1` so a forgotten `--env` refuses (exit 2) instead of
acting on the active workspace, which may belong to another client.

- **Look before you write:** `abcd skill list`, `abcd plugin list`, `abcd agent list`,
  `abcd workstream list`, `abcd pipeline list`, `abcd conversations list` and
  `abcd session models` are read-only and show what the org already has.
- **Reading a session result:** `abcd session start|send --json` returns that turn's own
  `text`, `tool_calls`, `pending` cards and `status` (`completed`, `needs_input`, `cancelled`,
  `failed`). `status` may say `RUNNING saving_outputs terminal=True` for a few minutes after the
  final answer: the answer is final. Pending cards are resolved with `abcd hitl resolve|answer`,
  and approvals stay a human's.
- **Release:** run `abcd release` only after the FDE's own message approves that release.
  In this chat `--yes` is not an approval: `abcd release` refuses unless a `user_message`
  of this conversation carries `<!--adopt:release-approval v="1" action="release" release="<agent>@v<n>"
  sha="<staged sha, 12 hex>"-->` (the conversation must be a workstream-shared run). The FDE types it in this chat (`abcd hitl approve-release` is
  refused here); never write or send it yourself. `--force-reason`, `--lead`, `--accept-regression` and `--local-approval` are
  refused in a cloud run (forced releases, accepted regressions and local approvals are
  made by a human locally), and so are `--approved-by`, `--by` and `--vetted-by` on the
  other commands: the actor is always the cloud identity. `abcd rollback` needs its own
  human token (`action="rollback"`, naming the target version and its sha; a release token
  does not count); `abcd agent push` without `--staged` is refused.
- **Code changes:** create and push `cloud/$ADOPT_CONVERSATION_ID` from `workspaces/<env>`
  (`git checkout -b`, `git add`, `git commit`, `git push -u origin <branch>`; bootstrap sets a
  local committer identity), check that `git log origin/<default>..HEAD` shows your commit before you
  say it was pushed (a failed commit still lets an empty branch push), then open a pull
  request when a GitHub token is available. With only a deploy key the pull request is flagged:
  report the pushed branch and say a person opens the PR. Do not merge it and never push the
  default branch.
- Keep long steps inside one `bash` call (up to 60 minutes) and report results briefly, with
  paths to any report files rather than their whole contents.
