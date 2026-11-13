---
name: abcd
description: "Run abcd, the Adopt Builder CLI, inside this sandbox: drive a client's Builder workspace (status, drift audits, evals, staging, test-staged, releases, explainers, training kits) and build, publish or push a skill or agent to the org and test it in a session. Use when an FDE asks to run abcd, a nightly or weekly Builder run, any `abcd ...` command, to build / publish / push a skill or agent to the org, or to test it in a session. Installs abcd from the bundled zip, clones the client's workspace repo when there is one, and checks the platform token. NoUI capture and replay are not available here."
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

(No workstream, so no `abcd-workspace.json`? Skip to "Build and publish a skill or agent": run
`abcd cloud bootstrap --platform-only`. Repo work needs the pointer file; skill and agent pushes
do not.)

The harness copies the Doc Store files of this workstream into the sandbox. Find the one
that names the workspace:

```bash
find /workspace -name abcd-workspace.json -not -path '/workspace/abcd/*' 2>/dev/null
```

It holds `env`, `repo` and `ref`. Read it to learn which client workspace this run is for.
If there is no such file, this is an unscoped chat: either tell the user to open the
`abcd-<env>` workstream for repo work, or, for a skill or agent push only, use
`abcd cloud bootstrap --platform-only` (see "Build and publish a skill or agent").

## Step 2: bootstrap

To check what this sandbox has, run `abcd cloud doctor --json` (read-only): it names the
variables that are present or missing (never their values), says whether a pointer file and a
bootstrapped workspace exist, and shows the PAT's tenant and roles. Use it instead of listing
environment variables. With no pointer file use `abcd cloud bootstrap --platform-only` (below)
instead of the command here.

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
- **Release:** run `abcd release` only after the FDE's own approval of that release. In this
  chat `--yes` is not an approval: `abcd release` refuses unless this conversation holds one.
  Use the `abcd-release-gate` skill: run `abcd release gate-card <agent> --action release --json`
  (`--action rollback` for a rollback), open the HITL gate card on the `approve` step with exactly
  the fields it prints (`release`, `action`, `sha`, and `decision` for the FDE), wait for the FDE
  to submit `decision=approve`, then run `abcd release <agent> --yes`. The FDE may instead type
  the token `<!--adopt:release-approval v="1" action="release" release="<agent>@v<n>"
  sha="<staged sha, 12 hex>"-->` (`abcd hitl approve-release` is refused here); never write or
  send either yourself. The conversation must be a workstream-shared run and the approval must
  not be authored by the PAT's own user. `--force-reason`, `--lead`, `--accept-regression` and
  `--local-approval` are refused in a cloud run (forced releases, accepted regressions and local
  approvals are made by a human locally), and so are `--approved-by`, `--by` and `--vetted-by` on
  the other commands: the actor is always the cloud identity. `abcd rollback` needs its own
  approval (`action="rollback"`, naming the target version and its sha; a release approval does
  not count); `abcd agent push` without `--staged` is refused.
- **Testing only, until a service-account PAT exists:** `ABCD_ALLOW_SAME_IDENTITY_APPROVAL=1`
  (arrives from vault secret `SANDBOX_ABCD_ALLOW_SAME_IDENTITY_APPROVAL`) accepts an approval
  authored by the runner's own identity and a private, non-shared conversation. It still needs
  the right release, action and sha, and never replaces a missing approval. abcd warns on stderr
  and records `approval_mode=same_identity_override`. Never set it in production.
- **Code changes:** create and push `cloud/$ADOPT_CONVERSATION_ID` from `workspaces/<env>`
  (`git checkout -b`, `git add`, `git commit`, `git push -u origin <branch>`; bootstrap sets a
  local committer identity), check that `git log origin/<default>..HEAD` shows your commit before you
  say it was pushed (a failed commit still lets an empty branch push), then open a pull
  request when a GitHub token is available. With only a deploy key the pull request is flagged:
  report the pushed branch and say a person opens the PR. Do not merge it and never push the
  default branch.
- Keep long steps inside one `bash` call (up to 60 minutes) and report results briefly, with
  paths to any report files rather than their whole contents.

## Build and publish a skill or agent

Use this when the FDE asks to build, publish or push a skill or agent to the org, or to test
one. Everything is `abcd ... --env <env> --json`.

**Rules for this section**

- Pushing to the org tier (`abcd skill push`, `abcd plugin push`) and creating or enabling an
  agent (`abcd agent create`, `--enable`) need the FDE's explicit request in this chat. Before a
  real push, state the skill name and whether it replaces an existing one, and wait for a yes.
- `abcd skill deploy-default` (the Built-in tier, visible to every org) is never run from the
  cloud; abcd refuses it. Do not look for another way. abcd itself ships system-wide from CI on
  releases, and `abcd cloud setup --home` uses that default copy; an org copy of `abcd`
  (`--org-copy`) is for testing a branch only, because it hides the default for the org.
- Name new test objects clearly (for example `test-<name>`, display name "TEST <name>") so they
  are not mistaken for production ones. A process has no hard delete: disable it when done.

**(a) Bootstrap.** With a workstream and pointer file, do Step 1 and Step 2 as above. With no
pointer file (an unscoped chat) run:

```bash
cd /workspace/abcd
abcd cloud bootstrap --platform-only --json      # writes workspaces/cloud/ , no git, no repo
abcd cloud doctor --json                         # variables by name, PAT tenant and roles
```

`--platform-only` builds `workspaces/cloud/` (default `--env cloud`; pass `--env <name>` to pick
another) from `ABCD_PAT_CLIENT_ID`, `ABCD_PAT_SECRET` and `ABCD_WEBUI_ENDPOINT` (or
`ADOPT_APP_BASE_URL`), and checks the PAT. Use `--env cloud` (or your name) on every later
command. If it names missing variables, tell the FDE which vault secrets (`SANDBOX_<NAME>`) to
add, and stop. Never `cat` the `.env`.

**(b) Author.** With a repo: `workspaces/<env>/harness/skills/<name>/SKILL.md` (plus aux files).
Without one, any directory, for example `/workspace/skills/<name>/`. The directory holds
`SKILL.md` with YAML frontmatter `name` and `description`. The `name` is a slug matching
`^[a-z0-9][a-z0-9-]*$`. Quote every frontmatter value that contains a colon, or the platform
silently drops the rest of the frontmatter (a `process:` block included).

**(c) Check the name.**

```bash
abcd skill list --search <name> --env <env> --json
```

A hit in the `org` tier means the push needs `--replace` (confirm that with the FDE). A hit in
the `default` tier means the name is taken by a Built-in skill: pick another name.

**(d) Audit** (free, local):

```bash
abcd skill audit <skill_dir>
```

**(e) Dry run, then push.**

```bash
abcd skill push <skill_dir> --env <env> --dry-run --json     # local checks only; lists the upload
abcd skill push <skill_dir> --env <env> [--replace] --json   # only after the FDE's yes
```

The dry run fails (non-zero) on a missing directory or `SKILL.md`, bad frontmatter or a bad name
slug, runs the audit, and lists the name, aux files and new or replace. It makes no platform
call. `abcd plugin push <plugin_dir> --dry-run` does the same for a plugin.

**(f) Verify.**

```bash
abcd skill verify <name> --env <env> --json
abcd skill list --search <name> --env <env> --json   # a gated-step skill must show has_process
```

**(g) Make it an agent** (only when the FDE asked for one). Check `abcd agent create --help`:

```bash
abcd workstream ensure abcd-test-<name> --env <env> --json    # or the workstream the FDE names
abcd agent create test-<name> "TEST <name>" --component skill:<name> \
    --instructions "<short>" --enable --env <env> --json
abcd agent set-workstreams <process_id> <workstream_id> --env <env> --json
```

Without `--enable` the agent is created as a Draft. A process must be enabled and assigned to
the workstream before a session can use it. With a bundle in a repo workspace
(`harness/agents/<agent>/`), `abcd agent push <agent> --staged` is the staged path; plain
`abcd agent push` is refused in the cloud.

**(h) Test session.**

```bash
abcd session start --workstream abcd-test-<name> --process test-<name> \
    --message "<a short task>" --env <env> --json
abcd session logs <conversation_id> --env <env> --json
abcd hitl list --conversation <conversation_id> --env <env> --json
abcd hitl resolve <conversation_id> --builder-id <id> --value <field>=<value> --env <env> --json
```

`hitl resolve` answers the test agent's own cards. A release approval stays the FDE's.

**(i) Commit.** When there is a repo workspace, commit the skill (and agent bundle) on
`cloud/$ADOPT_CONVERSATION_ID` and push it as described under "Code changes". With no repo,
say where the files are in `/workspace` so the FDE can keep them.
