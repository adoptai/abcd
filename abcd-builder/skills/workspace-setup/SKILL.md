---
name: workspace-setup
description: "Set up and switch client workspaces for the Adopt Builder: create or clone a client's private workspace repo, write builder.json and ADOPT.md, install the pre-commit hook, run the one-time platform setup (builder workstreams, Doc Stores, Data Store tables), check status and drift, import what is already live, and move capture bundles between machines. Use when the user says onboard a client, set up a workspace, clone the client repo, init the workspace, switch client, switch workspace, builder setup, is my workspace in sync, pull what is live, adopt an existing client, push or pull bundles, or follow ADOPT.md."
---

# workspace-setup

One client org = one workspace = one private git repo, at `workspaces/<env>/`. Every Builder
command takes `--env <env>` and defaults to the active workspace, so switching clients is
switching workspaces. This skill gets a workspace ready; `build-plan` is next.

**Agents: always pass `--env <env>`, and export `ABCD_REQUIRE_ENV=1`.** The active workspace may be
another client's; with that variable a command without `--env` refuses (exit 2) instead of
falling back to it. Without it, a platform command that used the active workspace prints a
notice naming it on stderr.

Run from the root of an abcd checkout. Add `--json` when you parse the output.

## When to use

- A new client, a new machine, or a new FDE joining a client.
- A client that was built on the platform before the Builder existed.
- "Is my workspace in sync with the platform and the repo?"
- Capture bundles must move to another machine (they never go in git).

## Step 1 - pick or create the workspace

```bash
abcd ws list --json
abcd ws use <env>
```

Existing client repo (the common case, including every second machine):

```bash
abcd ws clone <repo-url> --env <env> --json
```

Brand-new client: create the workspace folder first, then make it a repo.

```bash
abcd ws create --id <env> --name "<Client> - <Env>" --target staging --client <client> --json
abcd ws init --env <env> --remote <repo-url> --json
```

`ws init` writes `builder.json`, `.gitignore`, `ADOPT.md`, makes the first commit and pushes it.
It refuses on `default`, which is abcd's tracked example and is never a repo.

Re-running it with a different `--remote` repoints `origin` (for example to a GitHub repo that
already holds the history); it never pushes the default branch on a re-run. It leaves
`builder.json` (`repo_remote`) modified: commit it on a feature branch and open a pull request.

Fill in `workspaces/<env>/.env` yourself with the member: the Admin PAT
(`ADOPT_WEBUI_ENDPOINT`, `ADOPT_HARNESS_PAT_CLIENT_ID`, `ADOPT_HARNESS_PAT_SECRET`). Never print,
echo or commit a credential.

## Step 2 - install the pre-commit hook

```bash
abcd ws hook install --env <env> --json
```

The hook refuses commits with secrets, bundle-shaped files, or `security_params` values in
`adopt_profile.json`. Never bypass it with `--no-verify`.

## Step 3 - one-time platform setup

```bash
abcd builder setup --env <env> --dry-run --json
abcd builder setup --env <env> --json
```

Show the member the dry run first (workstreams, Doc Stores, tables it would create). Setup is
idempotent and records ids in `builder.json`, so rerunning is safe. Use `--yes` only if a step
asks for it and the human said go.

## Step 4 - check status, then read ADOPT.md

```bash
abcd ws status --env <env> --json
```

`ws status` shows branch, ahead/behind, uncommitted Builder files and drift. Then follow
`workspaces/<env>/ADOPT.md`: it lists the in-flight agents and this client's rules.

## Importing what is already live

```bash
abcd ws pull --check --env <env> --json
abcd ws pull --env <env> --json
abcd ws pull --adopt --env <env> --json
```

- `--check` writes nothing; show the member what would be imported.
- A plain pull writes to a `drift/<date>` branch for review; never merge it yourself.
- `--adopt` also records what is live as version 0, the baseline for a pre-Builder client.
  Run it once per client, after the member agrees.
- Truncated or binary aux files are imported as a list marked partial; do not "fix" them.

## Capture bundles

Bundles hold full HARs and cookies and never go in git.

```bash
abcd ws push-bundles --env <env> --json
abcd ws pull-bundles --env <env> --json
```

Run `pull-bundles` before a recompile or release on a new machine.

## Status, reports and the cloud runner

```bash
abcd status <agent> --env <env> --json
abcd status publish --env <env> --dry-run --json
abcd report daily --env <env> --json
abcd report scorecard --env <env> --json
abcd events flush --env <env> --json
abcd cloud bootstrap --env <env> --json
abcd cloud bundle --json
abcd cloud nightly --env <env> --json
```

- `status` is read-only. `status publish` writes the client's status files into the client org;
  show the member the dry run first, because stakeholders read them.
- `events flush` sends locally queued `bld_dev_events`. The Claude Code hooks in this plugin
  already log sessions; do not disable them.
- `cloud bootstrap` runs inside the Agent Harness sandbox: it clones the workspace repo and writes
  `.env` from injected `SANDBOX_*` secrets, failing loudly on any missing name. `cloud bundle` and
  `cloud nightly` are for the Builder home org's runner; a cloud run never touches the default
  branch.

## Meta agents and the cloud home org

```bash
abcd meta list --env <env> --json
abcd meta install status-agent context-agent context-curator --env <env> --json
abcd meta maker-checker <agent> --env <env> --json
abcd cloud setup --home --env <home-env> --workspace-env <env> --dry-run --json
abcd cloud setup --home --env <home-env> --workspace-env <env> --json
```

- `meta install` copies the shipped templates into `harness/agents/<name>/` (and
  `harness/skills/<skill>/`), fills in the client and workstream names from `builder.json`, and
  commits on a branch, never the default branch. It pushes nothing to the platform: use
  `abcd agent push <name>` for the client agents, and open a pull request for the branch.
  Client org: `status-agent`, `context-agent`, `context-curator`. Home org: `abcd-runner`,
  `abcd-nightly`, `abcd-weekly`, which `cloud setup --home` creates.
- `meta maker-checker <agent>` generates `harness/skills/<agent>-maker-checker/` from the agent's
  `harness/checks/*.yaml` and `release.yaml` and adds it to the agent's `process.yaml`. It ships
  with the agent's next release. It needs at least one check.
- `cloud setup --home` is for the client's Builder HOME org: `--env` is a workspace whose `.env`
  PAT belongs to that org and which is already a Builder repo (`abcd ws init --env <home-env>`
  first; it needs no remote and calls nothing on the platform), and `--workspace-env` is the client workspace the cloud runs work on.
  It makes the `abcd-<env>` workstream and its Doc Store, writes the `abcd-workspace.json` pointer
  (`env`, `repo`, `ref`), pushes the `abcd` skill with its bundle at the org tier (never the
  default tier), and creates or patches the three processes. Always show the `--dry-run` plan
  first. It refuses a workspace whose `builder.json` says `role: client` (`builder setup` sets
  that; `cloud setup --home` sets `role: home`). The command is idempotent: a second run creates
  and patches nothing. New processes come up enabled, so disable the ones you do not want
  running (`abcd agent update <id> --disable`); a re-run keeps an existing process's enabled
  state.
- Then do by hand what the command prints: the `SANDBOX_ABCD_PAT_CLIENT_ID`,
  `SANDBOX_ABCD_PAT_SECRET` and `SANDBOX_GITHUB_TOKEN` (or, for a repo-scoped deploy key instead,
  `SANDBOX_GITHUB_DEPLOY_KEY`: git works over SSH, pull requests and releases are flagged) vault
  secrets (values are write-only), and
  the two cron pipelines (`abcd-nightly`, `abcd-weekly`) created from a browser session.
  `abcd pipeline create` is for tests only: a pipeline made with a PAT records the token as owner.

## Rules

- **Approvals:** pass `--yes` only after the human said so for that exact step. Adopting a
  baseline and merging a drift branch are human decisions.
- **Safety:** no client names, URLs, tokens or data in the abcd repo itself; client material
  lives only in the workspace repo. Never commit or push to the default branch: work on a
  branch and open a pull request. Never `deploy-default` for client work. Platform calls go
  through the BFF with the PAT only.
- A workspace repo whose `env.json` does not match its folder name is refused; do not rename
  around it.

## Hand-offs

- Workspace ready, nothing planned yet: `build-plan`.
- NoUI recording needed first: `discover-and-plan`.
- Drift or a release question: `release-manager`.
