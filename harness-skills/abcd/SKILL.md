---
name: abcd
description: "Run abcd, the Adopt Builder CLI, inside this sandbox: drive a client's Builder workspace (status, drift audits, evals, staging, test-staged, releases, explainers, training kits), build, publish or push a skill or agent to the org and test it in a session, guide builders with cards and forms, and capture a website into a skill (NoUI): record a login and a workflow through the signed-in user's cloud Tabby session via a VNC link, compile, generalize, replay, install. Use when an FDE asks to run abcd, a nightly or weekly Builder run, any `abcd ...` command, to build / publish / push a skill or agent to the org, or to test it in a session; or says \"I want to build a skill / an agent\"; or says \"create a skill / app template for <site>\", \"onboard <site> into Tabby\", \"record a login and a workflow\", \"turn this website into a tool\", \"noui capture/compile\". Installs abcd (NoUI included) from the bundled zip, clones the client's workspace repo when there is one, and checks the platform token."
---

# abcd (Agent Harness edition)

You are inside the Agent Harness sandbox. This skill installs abcd and runs it against one
client workspace, and also carries NoUI: recording a website into a skill (see "Capture a website
into a skill (NoUI)" below). Everything is done with `bash` and the same `abcd ...` commands an FDE
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
4. **NoUI runs here too, with its own rules.** Recording a website, replaying it and installing the
   skill follow "Capture a website into a skill (NoUI)" below; its rules and the broker checks
   apply to that part only. Everything else in abcd works without the broker variables.
5. Use `--json` on every abcd command and read the exit code: 0 done, 1 refused or a check
   failed, 2 usage error, 3 platform or network error. Report a refusal as it is; do not
   work around it.

## Guided building (start here when the user wants to build)

Use this section when you are the builder agent and the chat has just started, or when the user
says they want to build a skill or an agent. The user is an org admin or manager who may never
have seen a terminal: collect everything with cards and forms, run every `abcd` command yourself,
and never ask them to type one. Run Step 0 and `abcd cloud bootstrap --platform-only --json`
silently first (see "Build and publish a skill or agent" (a)); with a platform agent token
(`ADOPT_HARNESS_BEARER_TOKEN`) no PAT is needed, and `abcd cloud doctor --json` says
`identity: platform agent token`. Use `--env cloud` on every command below.

The card tools are `render_<component>` (for example `render_action_card`) and `render_builder`.
Each payload below is the exact shape of that tool's input. End your turn after every card that
waits for the user: their click or answer arrives as the next message.

**Rules for guided building**

- Agents are tested in the **Builder workstream** and published **disabled**. In this chat abcd
  enables an agent only while its one workstream is the Builder workstream (a test draft);
  `abcd agent push --publish` assigns it to the real workstream, and the platform then disables
  it. You never enable an agent for real use: say where a person does (Studio, Agents, switch it
  to Enabled).
- Publishing needs the user's own answer to the "Publish to the org?" card. A click or message from
  them is the approval; never write one for them, and never push before it.
- Never print or ask for a token, never compose a link: show only links a command printed.
- Name drafts for what they do. A test object is named `test-<name>` and never replaces a live one.

### 1. Start: three tiles

Send these three cards in one turn, then end the turn. The user's click sends the card's `value`
as a message.

```json render_action_card
{"type": "action-card", "eyebrow": "Build", "title": "Skill from a website (record it)", "description": "Record yourself doing the task once in a browser; I turn it into a skill.", "action": {"label": "Start", "kind": "message", "value": "Build a skill from a website by recording it"}}
```

```json render_action_card
{"type": "action-card", "eyebrow": "Build", "title": "Skill from an API or docs", "description": "Describe the API or paste the docs; I write and test the skill.", "action": {"label": "Start", "kind": "message", "value": "Build a skill from an API or docs"}}
```

```json render_action_card
{"type": "action-card", "eyebrow": "Build", "title": "Agent from a process description", "description": "Describe the steps, the checks and who approves; I draft the agent.", "action": {"label": "Start", "kind": "message", "value": "Build an agent from a process description"}}
```

If the page already gave a starter such as "I want to build a new skill", show the tiles anyway
unless it named an existing skill ("Improve the skill <name>"): then fetch it with
`abcd skill verify <name> --env cloud --json` and go to step 3 with `--replace` in mind.

- Tile 1 ("record it"): go to "Capture a website into a skill (NoUI)" below and follow it as
  written, literal script invocations included. When it has installed the skill, continue at step 4.
- Tiles 2 and 3: step 2.

### 2. Scope: one form

Send one `render_builder` form (`builder_kind: "form"`), then end the turn. Omit `step_id`: no
process step is bound to this chat. The `workstream` field is a picker over the org's workstreams
with the inline "Create new" form, so the user creates a workstream without leaving the card.
Leave `default_value` unset (it takes an id, never a name).

```json render_builder
{"title": "What are we building?", "builder_kind": "form", "submit_label": "Start drafting", "blocks": [{"id": "scope", "title": "Scope", "description": "A few lines are enough; I ask again if something is unclear.", "fields": [
  {"name": "name", "label": "Name", "field_type": "text", "required": true, "placeholder": "invoice-triage", "description": "Lowercase letters, digits and dashes."},
  {"name": "goal", "label": "What should it do?", "field_type": "textarea", "required": true},
  {"name": "inputs_outputs", "label": "What goes in and what comes out?", "field_type": "textarea", "required": true},
  {"name": "workstream", "label": "Workstream it will run in", "field_type": "entity_picker", "source": "workstreams", "allow_create": true, "create_fields": [{"name": "name", "label": "Workstream name", "field_type": "text", "required": true}], "required": true}
]}]}
```

For tile 3 (an agent) add these fields to the same block; fill `options` for `skills` from
`abcd skill list --env cloud --json` (every option needs `value` and `label`):

```json render_builder
{"title": "Describe the process", "builder_kind": "form", "submit_label": "Draft the agent", "blocks": [{"id": "process", "title": "Process", "fields": [
  {"name": "steps", "label": "Steps, in order", "field_type": "textarea", "required": true},
  {"name": "gates", "label": "Where must a person approve or answer?", "field_type": "textarea"},
  {"name": "skills", "label": "Skills it should use", "field_type": "multi_select", "options": [{"value": "invoice-triage", "label": "invoice-triage"}]}
]}]}
```

The submitted values arrive in the next message. A workstream the user created in the picker
exists already: read its name with `abcd workstream list --env cloud --json`.

### 3. Draft: write it, show it, no repo needed

Show progress with one `render_workflow_stepper` and re-send it with the same `id` after each
phase (update in place):

```json render_workflow_stepper
{"type": "workflow-stepper", "id": "build-progress", "title": "Building invoice-triage", "active_step": "draft", "steps": [
  {"id": "scope", "title": "Scope", "status": "done"},
  {"id": "draft", "title": "Draft", "status": "active"},
  {"id": "test", "title": "Test", "status": "pending"},
  {"id": "publish", "title": "Publish", "status": "pending"}
]}
```

**Skill.** Scaffold into the scratch workspace, then edit `SKILL.md` (quote every frontmatter
value that has a colon):

```bash
cd /workspace/abcd
abcd skill new <name> --description "<one line>" --env cloud --json   # workspaces/cloud/harness/skills/<name>/
abcd skill audit workspaces/cloud/harness/skills/<name>
abcd skill push workspaces/cloud/harness/skills/<name> --env cloud --dry-run --json
```

**Agent.** Scaffold the bundle with its skills and workstream, then refine `instructions.md`
(steps, gates and rules in general terms; never a case id or a name):

```bash
abcd agent new <name> --display-name "<Name>" --description "<one line>" \
    --skill <skill-name> --workstream "<workstream name>" \
    --instructions "<the steps and gates>" --env cloud --json
```

The bundle names the workstream the agent will finally run in (the one from the scope form).
That is where it goes at publish time; the test happens in the Builder workstream (step 4). An
agent can only use skills that are already in the org: push a new skill first (step 5).

Show the draft as a `render_key_value_list`: the text of `SKILL.md` or `instructions.md` goes in a
`code` item, the rest in plain items.

```json render_key_value_list
{"type": "key-value-list", "title": "Draft: invoice-triage", "groups": [{"label": "Overview", "items": [{"key": "Name", "value": "invoice-triage"}, {"key": "Runs in", "value": "Accounts payable"}, {"key": "Audit", "value": "0 findings"}]}, {"label": "Instructions", "items": [{"key": "SKILL.md", "value": "Sort each invoice into approve, hold or reject using the rules in references/rules.md.", "type": "code"}]}]}
```

`render_document_preview` is only for a PDF or Excel file the user gave you or you produced (a
sample output), never for the draft text:

```json render_document_preview
{"type": "document-preview", "title": "Sample output", "documentType": "pdf", "source": {"filename": "sample-output.pdf"}, "pages": [{"label": "Page 1", "content": "Summary of the triage result."}]}
```

### 4. Test in a session

A session needs an enabled agent assigned to its workstream. Drafts are tested in the **Builder
workstream**, the one marked as the Builder's own (abcd finds it; never create one). Use its id
from the push output (`workstream_id`): its name is not always "Builder".

```bash
# an agent draft: created disabled and assigned ONLY to the Builder workstream
abcd agent push <name> --yes --env cloud --json          # = --test-in-builder, the default here
abcd agent update <process_id> --enable --env cloud --json   # allowed: Builder alone
abcd session start --workstream <workstream_id from the push output> --process <name> \
    --message "<a short task>" --env cloud --json
abcd session logs <conversation_id> --env cloud --json
```

- Testing a skill: the skill is not in the org yet, so first say that a clearly named test copy
  goes in and wait for a yes. Then push it as `--name test-<name>` (`abcd skill push <dir> --name
  test-<name> --env cloud --json`), draft a test agent (`abcd agent new test-<name> --skill
  test-<name> --env cloud`), and run the commands above for `test-<name>`. Tell the user the test
  copy exists.
- Pending cards from the test agent are answered with `abcd hitl resolve` as usual.
- If `abcd agent update --enable` is refused, the agent is assigned somewhere other than the
  Builder workstream: do not work around it; re-run `abcd agent push <name> --yes --env cloud`.

Report the outcome as a `render_status_card`, then ask what next with a `render_decision_card`
(fix and retest, publish as it is, or stop):

```json render_status_card
{"type": "status-card", "title": "Test run: invoice-triage", "subtitle": "Session on Accounts payable", "status": "warning", "fields": [{"label": "Result", "value": "2 of 3 invoices sorted", "highlight": true}, {"label": "Problem", "value": "The third invoice had no total"}]}
```

```json render_decision_card
{"type": "decision-card", "id": "after-test", "decision_type": "Test", "title": "One invoice was not sorted", "question": "The skill stopped on an invoice with no total. Fix it and run the test again?", "options": [{"id": "fix", "label": "Fix and retest", "recommended": true}, {"id": "publish", "label": "Publish as it is"}, {"id": "stop", "label": "Stop here"}]}
```

On "Fix and retest" change the draft, run `abcd skill push <dir> --env cloud --dry-run --json`
again, and repeat this step.

### 5. Publish: ask, then push

Ask first. Name the object and whether it replaces something (`abcd skill list --search <name>
--env cloud --json`: an `org` hit means `--replace`; a `default` hit means pick another name).

```json render_decision_card
{"type": "decision-card", "id": "publish", "decision_type": "Publish", "title": "Publish to the org?", "question": "invoice-triage is a new skill. It becomes available to every agent in the org. An agent you publish is assigned to its workstream but stays disabled until a person enables it.", "options": [{"id": "publish", "label": "Publish", "recommended": true}, {"id": "revise", "label": "Keep editing"}, {"id": "cancel", "label": "Cancel"}]}
```

Only after the user answers "Publish" (their own message):

```bash
abcd skill push workspaces/cloud/harness/skills/<name> --env cloud [--replace] --json
abcd skill verify <name> --env cloud --json
# an agent, after its skills are published: assign it to the workstream chosen in the scope form
abcd agent push <name> --yes --publish --env cloud --json   # the platform disables it; prints how a person enables it
```

`agent push --publish` assigns the bundle's own workstreams and the platform disables the agent
until a person enables it. It refuses to touch an agent that is enabled outside the Builder
workstream (a live agent changes through `abcd release`). Finish with a `render_status_card` that says what was published, that an agent is **disabled**
(assigned to its workstream, waiting for a person) and where a person enables it (the `enable_hint` the command printed), and what to try next. Show
a `render_download_card` only for a file a command gave you a link to.

```json render_status_card
{"type": "status-card", "title": "Published: invoice-triage", "subtitle": "Skill in the org", "status": "success", "fields": [{"label": "Type", "value": "Skill"}, {"label": "Next step", "value": "Attach it to an agent, or ask me to draft one", "highlight": true}]}
```

---

## Step 0: install abcd (do this first, silently)

1. Call `read_skill_file(skill_name="abcd", path="abcd-bundle.zip")`. The zip is binary, so the
   harness stages it at `/workspace/skill_assets/abcd/abcd-bundle.zip` (path pattern
   `/workspace/skill_assets/<skill_name>/<path>`). It holds abcd and the NoUI toolkit
   (`cli/noui`); there is no separate noui bundle.
2. Then one `bash` call (the slim image has no `unzip`; use Python's `zipfile`). Install
   abcd as a package; if that fails, fall back to `PYTHONPATH` plus the core dependencies. Then
   expose the NoUI toolkit at `/workspace/noui` (the harness looks for NoUI replays there):
   ```bash
   set -e
   mkdir -p /workspace/abcd
   python -c "import zipfile; zipfile.ZipFile('/workspace/skill_assets/abcd/abcd-bundle.zip').extractall('/workspace/abcd')"
   if pip install /workspace/abcd; then
     :
   else
     pip install -q requests python-dotenv PyYAML httpx pydantic colorama
     export PYTHONPATH=/workspace/abcd   # also prefix every later abcd command with it
   fi
   ln -sfn /workspace/abcd/cli/noui /workspace/noui
   pip install -q -e /workspace/noui || echo "NoUI toolkit install failed: NoUI capture is unavailable this turn; the rest of abcd still works"
   abcd --version || python /workspace/abcd/cli/abcd.py --version
   # the slim image may lack git and ssh; bootstrap clones with them
   command -v git > /dev/null && command -v ssh > /dev/null \
     || (apt-get update -qq && apt-get install -y -qq git openssh-client)
   ```
   This is the core install only; the WDL extras are not installed. `/workspace/noui` is the
   NoUI toolkit; its workbench (`/workspace/noui/workbench/`) is where compiled skills land.

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
  not count); `abcd agent push` without `--staged` is, in this chat, a test draft in the Builder workstream
(or `--publish`: the real workstreams, disabled); it is refused for an agent enabled elsewhere.
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
another) from `ADOPT_HARNESS_BEARER_TOKEN` (the platform agent token, when the platform injects one) or
else `ABCD_PAT_CLIENT_ID` and `ABCD_PAT_SECRET`, plus `ABCD_WEBUI_ENDPOINT` (or
`ADOPT_APP_BASE_URL`), and checks the credential. Use `--env cloud` (or your name) on every later
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
    --instructions "<short>" --env <env> --json
abcd agent set-workstreams <process_id> <workstream_id> --env <env> --json
```

Without `--enable` the agent is created as a Draft. A process must be enabled and assigned to
the workstream before a session can use it. In this chat `--enable` is allowed only for an
agent assigned to the Builder workstream alone (see "Guided building", step 4); for real use a
person enables it in Studio. With a bundle in a repo workspace
(`harness/agents/<agent>/`), `abcd agent push <agent> --staged` is the staged path; plain
`abcd agent push` is a Builder-workstream test draft here (`--publish` assigns the real
workstreams, disabled); it is refused for an agent enabled elsewhere.

**(h) Test session.**

```bash
abcd session start --workstream abcd-test-<name> --process test-<name> \
    --message "<a short task>" --env <env> --json
abcd session logs <conversation_id> --env <env> --json
abcd hitl list --conversation <conversation_id> --env <env> --json
abcd hitl resolve <conversation_id> --builder-id <id> --value <field>=<value> --env <env> --json
```

`hitl resolve` answers the test agent's own cards. A release approval stays the FDE's, and so
does `abcd hitl policy` (org policies change what every member's agent may do): never create,
enable or disable one from this chat.

**(i) Commit.** When there is a repo workspace, commit the skill (and agent bundle) on
`cloud/$ADOPT_CONVERSATION_ID` and push it as described under "Code changes". With no repo,
say where the files are in `/workspace` so the FDE can keep them.

---

# Capture a website into a skill (NoUI) — record → compile → author

This part of the skill lets you onboard a website into
Tabby and produce a runnable Skill, end to end, on the **signed-in user's** identity:

1. **One recording** — the human signs in *and* drives the workflow in the same browser
   session (a single VNC link).
2. **One import** → NoUI splits that capture into a Tabby **App Template + ServiceProfile**
   (the reusable, per-user login) *and* a **harness Skill** (`call_web_api` operation cards).

You drive the scripts with `bash`; **the human drives the browser** through a VNC link
you surface to them. Never ask the user for a Tabby token, never write a `.env`.

## Critical rules for NoUI — this is the Harness, not a local CLI

The toolkit ships its own `README.md` (`/workspace/noui/README.md`) for *local CLI* use. **Ignore its setup/auth
instructions.** In the harness:

1. **Cloud Tabby via the broker.** `TABBY_API_URL` is already the control-plane broker
   (fronting cloud Tabby): never `localhost`, never change it, and **never build a
   user-facing URL from it** — it is an internal API, not a browsable site. Show only links
   a script printed verbatim; copy them, never compose them.
2. **No credentials.** Do not create a `.env` or set `TABBY_CLIENT_ID/SECRET/ADMIN_TOKEN`.
   The broker injects the user's federated bearer (`owner_user_id` = the real user) on
   every call. Profiles/templates you create are **owned by that user** automatically.
3. **Recording is human-in-the-loop, one link at a time.** `capture_record.py` prints a VNC
   URL you cannot open: show it as a markdown link, say what to do, and **end your turn**.
   Never put two links in one message — the user can't tell which to open. Recording spans
   multiple turns; that is expected.
4. **Harness execution mode for the skill.** Compile workflows with
   `--execution-mode harness` (never `tabby`/`http`).

## Step N0 — NoUI readiness check (do this first, silently)

Step 0 of this skill already staged the toolkit at `/workspace/noui` (a link to
`/workspace/abcd/cli/noui`, installed with `pip install -e`). Run every NoUI script as
`cd /workspace/noui && python scripts/<script>.py ...` exactly as written below (never
`abcd noui <script>` here): the harness recognises a replay by that form. One `bash` call:

```bash
set -e
test -f /workspace/noui/scripts/capture_record.py || { echo "run Step 0 first"; exit 1; }
python -c "import noui_core" || { echo "NoUI toolkit not installed (see Step 0 output)"; exit 1; }
cd /workspace/noui
python - <<'PY'
import os
assert os.environ.get("NOUI_TABBY_AUTH_MODE")=="broker", "harness broker not configured — tell the user the authoring path is unavailable"
assert os.environ.get("NOUI_BROKER_TOKEN"), "missing broker capability token"
print("noui ready; broker auth active; Tabby via", os.environ.get("TABBY_API_URL"))
PY
```

If the asserts fail, stop and tell the user the broker isn't configured for this turn. This
check belongs to NoUI only; the rest of abcd does not need the broker.

Ask the user up front for: the **site** (login URL + the workflow to capture) and a short
**skill/app name**.

## Agree on the goal first — one line, then record

A skill's goal is what the member asked for ("download the annual statement"), NOT one
badge per operation. Establish it **before** recording:

- **Clear ask** → state it in ONE line together with the recording link and go — no
  separate wait: *"Recording a skill to **download the annual statement**. In that window,
  do this…"* Don't over-ask; a clear ask needs a single confirming sentence.
- **Vague ask** ("onboard ExampleBank") → ask first, then record: *"What should this skill
  produce — a downloaded file, a value read off a page?"* Wait for the answer. Don't guess.
- **Multiple goals are fine** — they can name several ("download the statement **and** the
  last 3 months' transactions"); record to cover them all.

When you give the recording link, tell the member to **demonstrate reaching each goal** —
actually download the file, actually submit the form — so the recording carries evidence
the goal was met.

## Part A — RECORD: login + workflow in ONE session (the default, and the only recording)

```bash
cd /workspace/noui
python scripts/capture_record.py --url "<LOGIN_URL>" --name "<app-name>"
```

No `--mode` needed — with no `--profile`/`--from` it defaults to **combined**: the human
signs in *and* drives the workflow in the same browser, then clicks *Finish & export*
once. NoUI splits the capture at the login boundary afterwards.

It prints `session_id` and a short, redaction-safe **`login_url`** (`.../s/<id>`) that
opens the recording viewer (with the *Finish & export* toolbar). **Show the user that one
link as a clickable markdown link** and tell them, in this order:

1. Sign in to the site.
2. **Then keep going in the same window** and drive the exact workflow you want as a
   tool (run the search, open the report, browse the listings).
3. Click **Finish & export**.
4. Come back and say "done".

Then **end your turn**. Do not poll. Do not mention any other link yet.

> **Skip the login half** when the site already has a Tabby profile / App Template (the
> user will say so, e.g. *"the profile `adopt-bank` is ready"*): pass
> `--profile <profile_id>` and the mode defaults to workflow-only — the recorder starts
> already authenticated, so there is no login to record.
>
> **Apps with no login at all** — unauthenticated, or authenticated by a static API key —
> need `--mode workflow` **explicitly**, because the default would tell the user to sign in
> to nothing. For a static key, record only the workflow (no `--profile`/`--from`) and
> compile with `--auth-type api-key --api-key-header <header>` (default `Authorization`);
> the compile prints a `${SECRET:...}` name for an admin to register. **Otherwise leave
> `--auth-type` at its default** (`session`).

**Recording the halves separately is NOT the default and is NOT required for a fresh
profile.** Part A above is the whole recording. Part B is an import, not a second
recording: do not ask the user to record again after Part A.

A **fresh profile** (the user says "don't reuse an existing profile", or no App Template
exists yet) is already what the combined default does. `--name` checks for a matching
App Template first; add `--force` to record a new one anyway:

```bash
python scripts/capture_record.py --url "<LOGIN_URL>" --name "<app-name>" --force
```

Split the halves (`--mode login`, then `--mode workflow --from <login_session_id>`) only
when you have a specific reason, e.g. you must confirm the App Template registered before
spending the user's time on the workflow. It costs an extra link and an extra sign-in, so
it is not the default.

## Part B — IMPORT (no recording): App Template + Skill from that one capture

```bash
cd /workspace/noui
python scripts/capture_import.py <session_id> --as skill --execution-mode harness \
    --name "<app-name>"
# same-origin apps: add --post-login-url-pattern '<glob the logged-in URL matches>'
```

No `--combined` flag and no `--profile-slug`: `capture_record.py` recorded the mode in the
provision ledger, so the import splits the capture, registers the login App Template, and
compiles the workflow (session-auth) bound to that new profile in one step. It prints the
**profile slug** — note it.

Registration is **template-first**: a tenant-wide **App Template** only, never a direct
App/ServiceProfile. Tabby auto-provisions a private, per-user App+Profile (straight to
ACTIVE) on each member's first `call_web_api`, so there is no `--promote` step and the
credential model defaults to `manual:` (the member signs in via VNC; nothing is stored).
The drained bundle is saved under `workbench/bundles/`.

### Confirm the goal, and heed the coverage warning

`capture_import` prints the recording's **endpoint goal** and, when the recording produced
no download and no submit, a **coverage warning**. Act on both — all one-liners, never hard
gates:

- **Member never stated a goal** → confirm the inferred one in ONE line before installing:
  *"I read your goal from the recording as **download annual statement** — correct?"* Then
  proceed. An explicit ask always wins; this is only the floor.
- **Stated goal and the endpoint disagree** — they asked for the annual statement but the
  recording ended on the monthly one → **say so** and let them decide. The stated goal is
  the intent; the mismatch is worth a sentence.
- **Coverage warning** (nothing produced a result — a flaky server, a walkthrough that
  stopped short) → **relay it, don't block.** The skill still compiles; the member decides
  whether to re-record or proceed.

### The sign-in comes next — let it happen

The profile's runtime session needs one sign-in (see below). Go straight to B3: the first
`call_web_api` returns `login_required` and **the platform prompts the user itself**. Say what
it's for, wait, re-run. Don't pre-check the session (it sits in `STARTING` for minutes) and
don't build a card or link of your own — you have no sign-in URL (NoUI rule 1).

## Workflow-only capture (existing profile, or the split flow)

When the login half is already covered — the user has a profile, or you deliberately
recorded the login separately — record just the workflow:

```bash
cd /workspace/noui
# (a) Existing profile / App Template. Auth comes from the profile.
python scripts/capture_record.py --url "<START_URL>" --profile <profile_id>

# (b) You recorded the login separately in this same flow: seed cookies from it.
python scripts/capture_record.py --mode workflow --url "<START_URL>" --from <login_session_id>
```

Either way the mode resolves to workflow-only. There is **no `--from-profile`** flag.
Surface the printed `login_url` — *"open this and drive the exact workflow you want as a
tool, then click Finish & export and tell me when done"* — and **end your turn**. Import
with an explicit profile binding:

```bash
python scripts/capture_import.py <session_id> --as skill --execution-mode harness \
    --profile-slug <profile-slug> --name "<skill-name>"
```

The compiled skill lands under `workbench/skills/<app>/` as `call_web_api` cards +
`operations.json` (no transport code).

For a **static API-key app** (no profile at all), drop `--profile-slug` and add
`--auth-type api-key --api-key-header <header>` instead — the skill authenticates from a
`${SECRET:...}` an admin registers, not a session.

> If the profile already has a HEALTHY session, you may instead drive the workflow
> **agent-side** with `capture_autopilot.py <slug> --steps steps.json --as skill
> --execution-mode harness` (no human VNC) — see the toolkit reference. For a brand-new
> profile, the VNC recording above is the reliable path.

## Two sessions, two sign-ins (say this up front)

Two browser sessions exist: the **recording** (Part A) and the profile's **own runtime
session**, which Tabby provisions per user on first `call_web_api`. The second stores nothing
— reusing the recording's cookies would share one user's session with the whole org — so it
asks for one sign-in. Correct, not a bug, but tell the user it's coming or it won't look that
way. Recording the halves separately adds a third sign-in; that's why it isn't the default.

## Step B3 — generalize: prune the noise, then test until it works

The B2 compile is a **raw** mirror of the recording — it includes calls that aren't
part of the task (analytics / config / keepalive pings, typeahead/prefetch, third-party
hosts) and names lifted straight from the API. **Before installing**, clean it up. You
make the judgment calls; `scripts/generalize.py` applies them, so never hand-edit
`operations.json` or the `SKILL.md` cards:

1. **Draft the plan.** `python scripts/generalize.py draft workbench/skills/<app>` writes
   `generalize_plan.json`: every operation with its noise reasons (keepalive, telemetry,
   duplicate, third-party host flagged `REVIEW:`) and a suggested decision (keep/drop, a
   natural-language name, a description). Edit the decisions where you disagree.
2. **Test the survivors.** For each remaining operation, invoke the **`call_web_api`**
   tool with its recipe from `operations.json` (pass any `${SECRET:...}` verbatim). Run
   each **2–3 times** and confirm it returns the expected data — not just a non-error.
   You can do this now: `call_web_api` is a catalog tool, not part of the skill.
3. **Fix or drop.** `login_required` / empty creds / 401 → expected on the *first* call: the
   platform prompts the user itself — describe it, wait, re-run. Never build a card or URL
   (NoUI rule 1).
   429 → retry; wrong/empty body → recompile from the saved bundle
   (`compile_workflow.py <bundle.json> --as skill --execution-mode harness`). If an op
   can't be made to work and isn't essential, drop it.
4. **Make it reusable — through the plan.** Renames, descriptions and path-parameter
   renames go in the plan's decisions. Show the plan to the user
   (`python scripts/generalize.py show workbench/skills/<app>`), and only after they agree:
   `python scripts/generalize.py confirm workbench/skills/<app> --by "<their name>"` then
   `python scripts/generalize.py apply workbench/skills/<app>`. `apply` refuses an
   unconfirmed plan, and for a browser skill allows renames/descriptions only.
5. **Loop** until every remaining operation passes 2–3 clean runs. Only then install.

Full playbook: `references/generalize.md` (inside `/workspace/noui`).

## Verify / replay the generalized skill (member approval)

Before `install_skill`, a browser skill must be replayed live and approved by the member.
Run the replay with the literal form below (the harness shows the skill-replay card and pauses
the turn for the member after it):

```bash
cd /workspace/noui
python scripts/verify_replay.py workbench/skills/<app> --profile-slug <slug>
```

Then **wait**: the member approves on the replay card and the approval arrives as their own
message. Only that approval lets `install_skill` succeed. `python scripts/verify_approve.py
workbench/skills/<app>` (same `cd /workspace/noui` form) records a human decision: run it only
after the member's own message approving the replayed result, never to clear the gate yourself and
never on their behalf (see Step C). It refuses when the replay did not reach every goal; amend and
replay again instead of approving anyway. Use `--help` on either script for its flags.

## Step C — install the skill into the harness catalog

Once compiled **and generalized (Step B3)**, **install it** so it becomes a usable tool on future turns. Call the
**`install_skill`** harness tool (NOT a bash command — it's a tool in your catalog)
with the compiled directory:

```
install_skill(skill_dir="/workspace/noui/workbench/skills/<app>")   # the /workspace/noui form, not /workspace/abcd/cli/noui/...
```

**Claim "installed" ONLY when `install_skill` actually returned success — and call it once.**

- If the call came back an **error**, for any reason, the skill is **not** in the catalog.
  Do **not** write "installed", "fully built and installed", or any success summary; state
  what the error said and stop. Telling the member a skill is installed when it is not is
  the worst outcome — they believe they have a tool they do not.
- **Do not retry `install_skill` in a loop.** The same refusal repeats, and a wall of
  identical failures buries the one line the member needs. Call it once; if refused,
  surface the reason and hand back.
- **"not approved by a member" is not your cue to approve.** That decision is made by the
  member on the **replay card** and arrives as their own message. **Never run
  `verify_approve.py` yourself to clear it, and never narrate the approval on their
  behalf** — that is exactly what the gate refuses. The card is shown mechanically after
  the replay; wait for the member's approval, then install.

On success, it writes the skill to the org catalog (listable on a new turn, ~30s). A skill whose
auth is the signed-in user's **session** (the usual login-recording case — e.g. a
Tabby `profile_slug` was bound) needs **nothing more**: it is runnable as soon as it's
listed. Do **not** invent a secret-registration step for these.

`install_skill` returns a `${SECRET:...}` name list, but it is populated **only** when
the API needs an *extra static secret* (an API key sent on every request, on top of —
or instead of — the session). **Only if that list is non-empty**, relay it to the user:
an org admin must register each named secret in the org secret store before the skill
can run. **If it's empty (the common session-auth case), there is no secret step — say
nothing about secrets.**

Then report the deliverable: the profile slug, the compiled skill path, and the
installed skill name — plus the required secret(s) only if `install_skill` returned any.

## Diagnosing a surprising compile — `bundle_inspect.py`

If an import produces the wrong kind of asset, or operations you didn't expect, **don't
write a throwaway script** — inspect the bundle:

```bash
cd /workspace/noui
python scripts/bundle_inspect.py workbench/bundles/<name>.json
```

It prints the **mode block** (Tabby's `recording_mode` vs NoUI's classification vs the
mode the session was provisioned as, with a warning on any mismatch), the URL timeline
with the login boundary marked, credential interactions (roles + redaction only), and the
API endpoint table built with the compiler's own filter and naming — i.e. a preview of the
operation set, useful for the B3 prune.

**On modes:** Tabby's `recording_mode` is unreliable (a warm-pool session always reports
`login`), and NoUI ignores it. `capture_import.py` decides from `--mode` → the provision
ledger written by `capture_record.py` → the capture's content, and prints which won. If it
ever picks wrong, pass `--mode {login,workflow,combined}` explicitly.

## Troubleshooting

- **`login_required` on the first live `call_web_api`** — expected, not a failure: the
  profile's own session needs its one sign-in (see *Two sessions, two sign-ins* above). The
  platform prompts the user; nothing for you to run or build. Describe it, wait, re-run. If no
  prompt appears, say so. Only a problem if it repeats *after* they've signed in.
- **`login_required` / session not healthy on import** — the login wasn't completed; have
  the user redo the VNC sign-in and click *Finish & export*.
- **The compiled asset is the wrong kind** (a workflow recording registered as an App
  Template, or vice versa) — run `bundle_inspect.py` on the saved bundle and re-import
  with an explicit `--mode`.
- **403 from Tabby** — the profile isn't owned by / reachable for this user; re-record so
  it's created under the user's identity.
- **VNC link won't load** — it expires; re-run the `capture_record.py` step to get a fresh
  one.
