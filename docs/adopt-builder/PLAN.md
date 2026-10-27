# Adopt Builder: plan

**Status:** draft for review, revised 2026-10-27 (first draft 2026-10-20)
**Companion:** [`adopt_builder_spec.html`](adopt_builder_spec.html) is an interactive explainer of this plan for FDEs. Open it in a browser.
**Base:** abcd `feat/noui-merge` (PR #25, `a25c228`): abcd and NoUI together. Everything this plan calls "today" or "exists" is on that branch.
**Checked against:** adoptai-workflows `29a3fe717` (sandbox secrets and skill tiers re-checked at `4e39b4570`), adoptwebui `88416ad0c`, abcd `a25c228`.

Every Adopt agent gets built through one headless Builder: abcd. FDEs drive it from their own coding agent (Claude Code, Codex, Cursor) on their own subscription, or from a harness chat in the cloud, where abcd runs inside the sandbox. Every skill and agent change becomes a versioned, staged release that has to pass its dataset gate, and each release carries its eval report.

**The workspace is the unit of everything.** abcd already scopes every command to a workspace (`workspaces/<env>/`, picked with `--env` or `python cli/workspace.py env use`). The Builder keeps that model: one workspace per client org, and each workspace is its own private git repo. Switching clients is switching workspaces. The repo is what keeps a client's skills, agents, datasets and checks in sync across FDEs, machines and the cloud, whatever state the platform is in.

**Goal number one: no new platform features.** The Builder consists of:
- abcd (this repo), with NoUI and Tabby already merged in,
- one private git repo per client workspace,
- a one-time setup of existing platform objects (workstreams, Doc Stores, Data Store tables, processes, cron pipelines, grants, vault secrets).

Where the platform can't do something, this plan says so and gives the workaround (section 4). Nothing in this plan waits on a platform release.

**WDL is legacy.** WDL actions, uber agents, pipelines and lambdas keep working exactly as they do today (`action-builder`, `pipeline-builder`, `save_wdl_draft.py`, `publish_wdl_action.py`). They live in the same workspace, so they get git history for free, but they get no staged shadows, datasets, gate or drift audit. Nothing below plans further for them.

---

## 1. Requirements

Each requirement maps to how it's met. Status values:
- **exists**: on the platform or in abcd today
- **setup**: existing platform objects, configured once
- **abcd**: new code in this repo only
- **workaround**: a platform limit, covered as described in section 4

| # | Requirement | How it is met | Status |
|---|---|---|---|
| R1 | No new platform features | Every call in this plan goes to an existing adoptwebui route. Gaps are covered in section 4 | exists |
| R2 | The builder side is entirely headless | abcd drives the existing end-user and studio routes with a Personal Access Token. No new UI | abcd |
| R3 | FDEs use their own coding agent, on subscription rather than API tokens | Authoring, local evals, review, perf analysis, judged-check scoring, explainers and training run in the FDE's coding agent. Platform tokens are spent only on harness turns and on cloud runs (R23) | abcd |
| R4 | Onboarding is "take a PAT, download an MD file" | Clone abcd, `abcd ws clone <repo>` (or `workspace.py env create` plus `abcd ws init` for a new client), put the PAT in `workspaces/<env>/.env`, then "follow `workspaces/<env>/ADOPT.md`" | abcd |
| R5 | Bring your own private git repo for agent versions | Each client workspace is its own private repo, nested inside abcd's git-ignored `workspaces/`. abcd uses `git` and `gh` locally; the platform never talks to GitHub (section 5) | setup |
| R6 | Version control for skills and agents, testing a staged version, then going live | The workspace repo is the version store. Shadow staged objects on the platform are built from it. Release copies staged onto live (section 7) | workaround |
| R7 | Native datasets: input/output work-product pairs, one workstream per pair | Datasets have a kind. For work-product pairs, each pair gets a workstream tagged `custom_properties.kind = dataset`. Expectations live in a separate store, indexed in tables (section 10) | setup |
| R7a | Flexible evals for agents with no work-product pairs (e.g. the CRM agent, email sorting) | Four dataset kinds share one case shape: `work_product`, `labeled` (known or synthetic, mappings known up front), `read_your_writes` (probe the target system after the write), and `custom` (a scorer script in the workspace) | abcd |
| R8 | Evals against datasets; a release is gated when the agent is linked to a dataset | `abcd eval run` runs each case the way its kind says (per case, batch, or sequential with a probe) and scores it. `abcd release` is the gate (section 10) | abcd |
| R9 | An eval report on every release | Written to `builder-ops`, copied to the client status workstream, committed to the workspace and attached to the workspace repo's GitHub release | abcd |
| R10 | The FDE defines metrics, ground truth, targets and release criteria per agent | Files in the agent bundle (`release.yaml`, `metrics.yaml`) and `harness/checks/`, mirrored into `bld_metrics`, `bld_checks`, `bld_release_criteria`; `abcd metric`, `check` and `criteria` commands | abcd |
| R11 | The FDE defines and approves the skills, plugins and agents to build | `bld_build_plan` with `abcd plan propose` and `approve`. Nothing is built until it's approved | abcd |
| R12 | A knowledge graph in S3, powered by a meta workstream, its Doc Store and its Data Store | The `builder-context` workstream. Pages go in its Doc Store (S3); nodes and edges go in two tables (section 12) | setup |
| R13 | Call context and process documentation as inputs | `context/calls`, `context/emails`, `context/process-docs` in `builder-context`. The Context curator extracts decisions and checks from them | setup |
| R14 | The loop: build, evals, abcd review, harness run and logs, perf review, evals again, iterate, staged, test staged, publish | Ten loop steps, each an abcd command, all scoped to the active workspace (section 8) | abcd |
| R15 | NoUI and Tabby scenarios handled by the loop | **Exists after the merge:** `tabby_bootstrap.py`, `noui_workspace.py` (capture, compile, generalize, replay, approve) and the `discover-and-plan` and `skill-builder` skills compile a site into `workspaces/<env>/harness/skills/`. abcd adds only the wiring: the skill joins an agent bundle, its replay approval joins the gate, and a KG page is written | exists + abcd |
| R16 | Admins see everything each FDE does every day | Every abcd command logs to `bld_dev_events`; Claude Code hooks add transcripts; a daily report goes out by Slack or email | abcd |
| R17 | A client stakeholder logs in and sees full metric status and what's pending on an agent's design | A Builder status workstream per client, with a read grant, in the end-user Agents cockpit, plus a Status agent and a Friday email (section 15) | setup |
| R18 | FDEs can always understand the current implementation | `abcd explain` generates an explainer HTML per agent on every release; this repo's spec HTML covers the Builder itself | abcd |
| R19 | Each agent ships training material: simple prompts and scenarios | `abcd train` generates quickstart, prompts, scenarios, FAQ and what's new on every release | abcd |
| R20 | The Builder works with minimal FDE guidance | The five inputs plus approval gates. The Builder asks only what the inputs don't settle, and every answer is saved as a decision | abcd |
| R21 | **During the build phase, the FDE and a customer-appointed SME keep adding missing context and repro explanations until blind evals show that generic prompts, the knowledge graph's instructions and the skills reproduce the golden dataset on their own.** Every agent linked to a dataset must meet this before going live | Context gaps opened automatically from blind-eval misses; an SME workstream and Context agent for answering; generic instruction pages with a leak check, compiled into an instruction skill; blind reproduction rate as a mandatory release criterion (section 11) | abcd |
| R22 | **Switching between clients is switching workspaces** | Every Builder command takes `--env` and defaults to the active workspace, exactly like the existing `harness_*`, `noui_workspace` and WDL commands. Nothing about one client lives outside its workspace (section 5) | exists + abcd |
| R23 | **abcd runs headless in the cloud, in the Agent Harness, as well as on an FDE's machine** | `harness-skills/abcd/` ships abcd as a skill bundle, the same way `harness-skills/noui/` ships NoUI. A harness turn clones the workspace repo into the sandbox and runs the same commands. Credentials come from `SANDBOX_*` vault secrets in an Adopt-owned Builder home org (section 6) | setup + abcd |
| R24 | Skills, agents and the Builder's own plugin stay one system | The `abcd-builder` plugin that ships today is extended, not replaced. NoUI's replay approval and the default skill tier are folded into the release rules (sections 7 and 13) | abcd |
| R25 | **One CLI.** Every Builder and harness command is reachable as `abcd <group> <command>`, with the same `--env`, `--json`, `--yes` and exit codes, installed with one `pip install` locally and in the cloud | Package mode with an `abcd` console script, a shared command layer, existing scripts moved behind it without breaking their old entry points, WDL passed through (section 17) | abcd |

## 2. What the FDE does

The FDE works in **one workspace per client** and switches with `python cli/workspace.py env use <client>` (or `--env <client>` on any command).

The FDE defines **five inputs**:
1. metrics,
2. ground truth: a dataset of the right kind (work-product pairs, labelled cases, read-your-writes cases, or a custom scorer) plus the client's checks,
3. the knowledge graph,
4. call context,
5. process documentation.

The FDE also owns four approvals:
1. **The build plan:** which skills, plugins and agents to build or change.
2. **Release criteria for each agent:** the dataset, the gate metrics and their targets for the next release.
3. **Context gaps, closed with the customer SME.** Every case a blind eval can't reproduce becomes a gap. The FDE answers it or routes it to the SME. The answer becomes a generic instruction, and the gap closes only when a blind eval reproduces the case.
4. **Staged to live,** once the release criteria pass.

A browser skill also needs its existing NoUI replay approval (section 7). Everything else in the loop, the Builder runs itself, locally or in the cloud.

## 3. Platform facts this plan relies on

All of these were verified in code. Paths are relative to `adoptwebui/backend/app` unless marked `W/` (adoptai-workflows `src/workflows`) or `A/` (abcd).

**Org skills**
- Upload: `POST /v1/end-user/agent-harness/skills`, Admin only (`resolve_org_admin`, `dependencies/harness_caller.py:184` at the pinned commit, `:225` on current dev).
- Name rule: `^[a-z0-9][a-z0-9-]*$`, so `<name>--staged` is valid. Pass `replace=true` to overwrite. Without `replace`, the upload also refuses a name that collides with a default skill (`W/agent_harness/skills_upload.py:235-250`); `replace=true` skips that guard.
- The upload route runs **no** replay, provenance or approval check (`W/agent_harness/skills_upload.py:187-280`). Those checks exist only inside the harness's own `install_skill` tool (see NoUI's replay gate below).
- Org skills are not versioned and have no content hash. `GET /skills/{name}/files` returns text files only, truncated; binary aux files can't be read back.
- A skill whose frontmatter has a `process:` block also shows up in the catalog as a process template (`has_process`).
- The catalog keys a skill by its **frontmatter name** (`W/agent_harness/skills.py:328`), so a staged copy has to rewrite `name:`. `harness_skill.py push --name` only changes the upload name, not the frontmatter, so it isn't enough on its own (`A/cli/harness_skill.py:266`).

**Skill tiers**
- There is an org tier (per org, uploaded by org admins) and a default ("Built-in") tier, visible to every org and gated to `@adopt.ai` (`A/cli/harness_skill.py deploy-default`).
- **Org skills override defaults on a name collision, and the override is total** (`W/agent_harness/skills.py:15`). An org skill named like a default skill hides the default for that whole org.
- Default skills carry a `bundle_version` and change on their own release schedule, outside any client's release.

**Processes (agents)**
- Routes: `/v1/org/processes`. Create, patch, delete, assign to workstreams, set write grants (`/write-workstreams`) and run are all Admin only (`routes/process.py:302-428`). abcd wraps most of them in `A/cli/harness_process.py`, which hard-codes component `source: "org"` and has no flag for `compiled_flow` or write grants.
- Patchable fields: `display_name, description, components, agent_instructions, is_enabled, compiled_flow`.
- `components` entries look like `{type, source, ref}`, and `ref` is matched against the catalog name.
- There is **no version history**.

**Turns**
- `POST /v1/end-user/agent-harness/turns` takes `workstream_id` and `process_id`.
- The process must be enabled and assigned to that workstream.
- Status, stream, transcript and outputs download routes exist. They are restricted to the conversation's owner.

**Workstreams and grants**
- Create, update and soft delete: `/v1/org/workstreams`, Admin only. There is no archive state.
- The property schema is set through `/v1/org/member-config/update`.
- Per-user read or write grants: `PUT /v1/org/console-access/{user_id}`, Admin only.

**Doc Store**
- Accepts any file type, and non-PDF files skip extraction.
- The harness copies every store linked to a workstream into the sandbox, up to 200 MB per session.
- There is no versioning, and an override mints a new document id.
- abcd already creates stores, links them to workstreams and uploads through `upload-urls` and `upload-confirm` (`A/cli/harness_common/api_client.py`, `A/cli/harness_workstream.py ensure|seed`).

**Data Store**
- Routes: `/v1/org-db/*`. Any JWT with `tenantId` and `sub` can call them.
- Inserts take at most 500 rows, aren't atomic, and have no upsert.
- No per-user row security, and read-only SQL covers the whole org.
- Agents get `db_query` and `db_insert_rows` when their skill sets `datastore_write: true`. They can't update rows.
- abcd has no client for these routes yet.

**The sandbox environment**
- An org marks a vault secret for sandbox injection by naming it `SANDBOX_<NAME>`. At sandbox creation the harness strips the prefix and injects it as env (`W/agent_harness/sandbox_secrets.py`). Names that could hijack execution (`PATH`, `LD_*`, `PYTHON*`, `GIT_*`, ...) are dropped. The module notes the feature is used with a GitHub PAT and `git`.
- **These secrets are org-wide:** every sandbox in that org gets them, whichever process or user the turn belongs to.
- The sandbox also gets `ADOPT_APP_BASE_URL` (the platform's API base, reachable from the container) and `ADOPT_CONVERSATION_ID` (`W/agent_harness/activities.py:4886-4899`).
- A sandbox is per conversation and is reaped after about 10 minutes idle. Nothing in it survives that except what was written out.
- One `bash` call is capped at 60 minutes (`W/agent_harness/activities.py:383`).
- A stripped secret name must be a valid env identifier (`^[A-Za-z_][A-Za-z0-9_]*$`), or it's silently dropped. A failed secret fetch also yields nothing, silently.
- The slim sandbox image has no `unzip`, and `gh` isn't confirmed to be present.

**NoUI in the harness (precedent for abcd in the cloud)**
- `A/harness-skills/noui/` ships the NoUI toolkit as a skill: `SKILL.md` plus a `noui-bundle.zip` aux file built from git-tracked files only. The agent unzips it into `/workspace/noui` and runs `pip install -e .`.
- Tabby auth comes from a control-plane broker the harness configures. That broker is NoUI-specific, and it's minted for the org and user of the conversation it runs in.

**NoUI capture bundles**
- A bundle is `{har, click_events, url_events, cookies}`. Browser-driven workflow recordings reduce the HAR to metadata, but **login recordings and non-browser workflow recordings keep the full HAR, headers included** (`A/cli/noui/references/pillar-1-capture.md`). `recording_bundle.json` beside a compiled skill is the same kind of file.
- In-pod redaction covers password and OTP fields. abcd's secret scan catches JWT- and Bearer-shaped values, not app cookies or session headers.

**NoUI's replay gate**
- A browser skill installs only after a human approved its replay. `verify_approve.py` writes `replay_approval.json` beside the skill, holding a fingerprint of the operations' names, kinds, steps and parameters (not descriptions, not the skill name). NoUI's local installer refuses when the fingerprint no longer matches (`A/cli/noui/noui_core/verify/gate.py`, which also reads `amendments.json`).
- **The file alone is forgeable:** the agent writes it. The harness's `install_skill` tool therefore also requires a member approval token, `<!--adopt:skill-approval v="1" fp="<fingerprint>"-->`, in one of the member's own `user_message` turns, and recomputes the digests from `operations.json`, `amendments.json` and `recording_bundle.json` (`W/agent_harness/activities.py:7922-7990`, `8001-8150`).
- abcd's `harness_skill.py push` has no browser gate today, and the upload route has none either, so a skill pushed by abcd skips both checks unless abcd adds them (section 7).
- NoUI also records each browser skill's goal (`A/cli/noui/noui_core/compile/goals.py`).

**Scheduling, integrations and runs**
- Scheduling: `POST /v1/pipelines` with `schedule_type: cron` and a `process_id` (webui `schemas/pipeline.py:35`). It runs the process across its assigned workstreams. abcd has no client for this: `A/cli/wdl_common/pipeline_client.py` speaks the WDL credentials and endpoint, and Builder pipelines are created once from a browser anyway (section 4).
- Notifications: agents can use connected Slack or Gmail through `list_integrations` and `run_integration_tool`.
- Run history: `/v1/runs` covers the whole org (`A/cli/harness_runs.py`).

**End-user access**
- An Operator-role user with a read grant can see that workstream's files, and chat with its processes, in the end-user Agents cockpit.
- That surface's entitlement (`studio.agents_enduser`) is set per org by Adopt in intranet.

**Driving the platform from an agent:** there is no harness tool for starting runs, patching processes or uploading skills. But a sandbox that holds a PAT can call the same adoptwebui routes over HTTP that abcd calls from a laptop. That is how abcd runs in the cloud (section 6).

## 4. Platform limits and workarounds

| Need | Platform today | Workaround | What remains |
|---|---|---|---|
| Version history | Skills and processes are overwritten in place | The workspace repo holds every version. `bld_versions` records content hashes and SHAs. Rollback re-pushes a tag | Nothing in practice |
| Staged versions | No staged pointer; a turn takes a `process_id` | Shadow objects: skill `<name>--staged` (frontmatter name rewritten) and process `<agent>-staged`. Promotion re-uploads under the live names and patches the live process | Promotion is a re-upload, not an atomic switch; abcd verifies afterwards |
| Hiding staged agents | An assigned process is visible to every user of that workstream | Assign staged processes only to staging and dataset workstreams, which no client user has a grant on | Admins still see them |
| Hiding staged skills | Skills are org-wide, so a `--staged` skill shows in every user's skill list; one with a `process:` block also adds a second process template | Staged copies drop the `process:` block and get a description starting "Staging copy, do not use" | Client users can still see the name |
| Reading live objects back | Org skills have no version or hash; the files route returns truncated text only | abcd records, at every push, the SHA-256 of `SKILL.md` and a manifest of aux paths and sizes in `bld_versions`. Drift compares the live `SKILL.md` and aux listing against that. `ws pull` imports `SKILL.md` and text files and marks truncated or binary files as partial | A changed binary aux file of the same size isn't detected |
| Browser-skill approval on abcd uploads | The upload route has no replay or approval check; `replay_approval.json` alone is agent-written | abcd's push applies the platform's full rule itself: the file and a member approval token in a `user_message` (section 7) | A local approval, made outside a harness chat, is only as trustworthy as the FDE's session |
| Per-agent skill pinning | Skills are org-wide by name | A change to a shared skill ships to every agent that uses it, and the gate runs every dependent agent's criteria | One release can hold up several agents |
| One staged slot per agent | Two FDEs pushing `--staged` overwrite each other | `push --staged` takes a lock in `bld_agents` (holder, SHA, since). Another FDE gets a refusal naming the holder; `--take` moves the lock and notifies the holder | Locks are advisory against direct UI edits; the drift audit covers those |
| A hard release gate | The platform can't refuse a write | `abcd release` is the only sanctioned path. `abcd audit drift` flags any live object that differs from its last release, on every abcd command and nightly | Detects within a day; doesn't prevent |
| Default skills changing under an agent | Default-tier skills update on Adopt's schedule; an org can't pin them | Each version records the `bundle_version` of every default skill it uses. The nightly cloud run re-runs the blind gate on live when one moves (section 6) | A regression shows up the next morning, not before |
| Org skill hiding a default skill | Org skills override defaults totally on a name collision. The platform's own guard refuses that collision only when `replace` is off | The first upload of any new name goes without `replace`, so the platform's guard fires. Later releases need `replace`, so `abcd release` checks the name against the default catalog itself and refuses unless a lead records a decision. `abcd audit drift` lists every org skill that shadows a default | None |
| Client view with per-user filtering | Data Store has no per-user row security | Clients get a read grant only on their Builder status workstream, which holds files; no table access | Scorecards are files, refreshed daily |
| PAT identity | Some routes record the token id (`sub`) instead of the user | abcd runs everything through `POST /turns`, which records the user. Cron pipelines are created once from a browser session | Pipeline setup is one manual step |
| FDE permissions | Skill, process, workstream and grant writes need Admin | FDEs use Admin PATs in the orgs they build for | FDEs can edit outside the Builder; the drift audit covers that |
| Credentials for cloud runs | Sandbox secrets are org-wide: every sandbox in the org gets every `SANDBOX_*` secret, including sandboxes that read untrusted content (transcripts, emails, SME uploads) | The cloud runner lives in an Adopt-owned **Builder home org, one per client**, with no client users and no client-facing agents. Its vault holds only that client's Admin PAT and a GitHub token scoped to that client's workspace repo. Nothing goes in a client org's vault (section 6) | A prompt injection in one client's material can reach that client's PAT, but never another client's |
| Sandboxes are short-lived | A sandbox is reaped after ~10 minutes idle, and one `bash` call is capped at 60 minutes | Every cloud run clones the workspace repo fresh, at a pinned ref. Results go to the Doc Store, the tables, or a pushed branch, never only to the sandbox. `abcd eval run` is resumable: it submits turns, records their ids in `bld_eval_runs`, and later calls poll and score | Each cloud run pays a clone and a dependency install |
| Entitlements | Can't be changed through adoptwebui | Adopt turns on `studio.agents_enduser` for client orgs in intranet | One request per client org |

Unrelated to the Builder but blocking: `/v1/end-user/docstore/*` (`routes/end_user_docstore.py`) authenticates only optionally (`optional_jwt`). **A request with no bearer token** takes `org_id` from the request and can upload and delete. This is a security fix, not a feature, and it should be raised with the platform team before Builder data lives in the Doc Store.

## 5. Workspaces: one client, one repo

### What exists today
abcd's workspace system (`cli/wdl_common/workspace_manager.py`, `cli/harness_common/workspace.py`, `cli/noui_workspace.py`):
- `workspaces/<env>/` holds `env.json` (name, target, client, domain), `.env` (credentials), `adopt_profile.json`, and the WDL folders `agents/`, `actions/`, `pipelines/` and `lambdas/`.
- `workspaces/<env>/harness/` holds harness work:
  - `skills/<skill>/`, which `harness_skill.py push` reads and NoUI compiles into;
  - the NoUI workbench: `bundles/`, `sessions/`, `mcp_servers/`, `login_recordings/`;
  - runtime data: `traces/`, `workstreams.json`, `.token_cache.json`.
- `workspaces/.active_env` is the active workspace. Every command takes `--env` and defaults to it.
- abcd's own `.gitignore` ignores `workspaces/*` except the tracked `default/` example, and always ignores `workspaces/*/harness/`.

### What the Builder adds
**A client workspace is its own private git repo.** It sits at `workspaces/<env>/`, inside abcd's ignored `workspaces/` folder, so abcd's git never sees it and it is never a submodule. The `default` workspace stays abcd's tracked example and is never made a repo.

- `abcd ws init [--env E] --remote <url>`: run `git init` in an existing workspace, write the Builder files below, make the first commit and push. It refuses on `default`.
- `abcd ws clone <url> [--env E]`: clone a client's repo into `workspaces/<env>/`. It checks that `env.json`'s `env_id` matches the folder name, then writes the same `.env` template `workspace_manager.create_env` writes, because `create_env` refuses an existing folder. Then `.env` is the only thing left to fill in.
- `abcd ws status`: branch, ahead and behind, uncommitted Builder files, and drift against the platform.

**Layout (Builder additions marked new):**

```
workspaces/<env>/                        one client org = one workspace = one private repo
  .gitignore                             new: written by abcd ws init (see below)
  ADOPT.md                               new: the manifest the coding agent follows for this client
  env.json                               existing: name, target, client, domain
  builder.json                           new: org id, webui endpoint, repo remote, default branch,
                                           Builder workstream, store and table ids
  adopt_profile.json                     existing (WDL)
  .env                                   existing: credentials, never committed
  agents/ actions/ pipelines/ lambdas/   existing WDL (legacy): versioned by the repo, nothing else
  harness/
    skills/<skill>/                      existing: hand-written or NoUI-compiled skills. SKILL.md,
                                           scripts/, references/; for NoUI skills also operations.json,
                                           manifest.json, amendments.json and replay_approval.json
                                           (recording_bundle.json is kept out of git, see below)
    plugins/<plugin>/                    new: plugin source for harness_skill.py push-plugin
    agents/<agent>/                      new: an agent bundle (a harness process, not a WDL agent)
      process.yaml                       name, display_name, description, components (org or
                                           default source), workstreams and write_workstreams
                                           (by name), is_enabled
      instructions.md                    agent_instructions
      flow.json                          compiled_flow, when the agent has gated steps
      release.yaml                       release criteria: dataset, gate metrics, targets
      metrics.yaml                       metric definitions
      prompts.yaml                       generic prompts for blind evals and the training kit
      docs/explainer.html
      training/                          quickstart.md, prompts.md, scenarios.md, faq.md, whats-new.md
    checks/<check>.yaml                  client checks as code
    datasets/<dataset>/
      manifest.json                      kind, layout, run mode, target env, probe, scorers
      cases/<case_id>.yaml               case shape; expected files stay in the ds- store
    evals/scorers/<name>.py              custom scorers
    evals/reports/<agent>/<version>.html
    mcp_servers/ login_recordings/       existing NoUI outputs (committed, secret-scanned)
    bundles.lock.json                    new: Doc Store path and SHA-256 of every capture bundle
                                           and recording_bundle.json
    bundles/ sessions/ traces/ workstreams.json .token_cache.json   existing (ignored)
  context/                               new: abcd context pull output (ignored)
  .builder/                              new: local Builder state, e.g. eval scratch (ignored)
```

**Committed versus ignored:**
- The workspace `.gitignore` covers:
  - credentials: `.env`, `.env.*`, `harness/.token_cache.json`, `*.pem`, `*.key`;
  - harness runtime: `harness/traces/`, `harness/sessions/`, `harness/workstreams.json`;
  - NoUI captures: `harness/bundles/`, `harness/skills/*/recording_bundle.json`, `*.har`;
  - WDL runtime: `.cache/`, `agents/*/traces/`, `actions/*/traces/`;
  - Builder local state: `context/`, `.builder/`.
- Everything else is source.
- **Capture bundles never go in git.** Login recordings and non-browser workflow recordings keep the full HAR and the app's cookies, which no scan reliably strips.
  - `abcd ws push-bundles` uploads them, and every `recording_bundle.json`, to a `noui-bundles` Doc Store linked only to `builder-ops`. It records their paths and SHA-256 in `harness/bundles.lock.json`.
  - `abcd ws pull-bundles` restores them on another machine, or in the cloud, before a recompile or a release that needs them.
  - Committing them later would need a scrubber for cookies, `Authorization`, `Set-Cookie` and session parameters, enforced by the hook below (section 20).
- A workspace pre-commit hook refuses a commit when any of these fail:
  - abcd's content secret scan (`cli/harness_common/secret_scan.py`) over every staged file;
  - NoUI's `count_sensitive_unredacted` over anything bundle-shaped;
  - a check that `adopt_profile.json` holds no `security_params` values (WDL keeps cookies there; they belong in `.env`).

**Why `harness/agents/`:** the WDL `agents/` folder at the workspace root already means uber agents. Harness agents are processes, which is what the harness CLI calls them, so they go under `harness/`, next to the skills they use. `ADOPT.md` spells out the difference for the coding agent.

**How the repo and the platform stay in sync**
- **The repo is the source of truth for definitions.** The platform only holds deployments: live objects, staged shadows, datasets and history. Nothing on the platform ever reads git.
- `abcd ws pull`: imports org skills, plugins and processes that the platform has and the repo doesn't.
  - It covers bootstrapping a client that was built before the Builder, and capturing an edit someone made in the UI.
  - It writes them into `harness/` on a `drift/<date>` branch for review.
  - The platform returns skill text only, truncated, so binary or truncated aux files are imported as a list and marked partial.
  - It extends the existing `harness_process.py get` and the skill and plugin GETs. WDL keeps its own `workspace.py agent checkout` and `sync`.
  - `abcd ws pull --check` only reports what it would import, and writes nothing.
  - **Baseline for a client built before the Builder:** `abcd ws pull --adopt` also records what's live as version 0. It writes `bld_versions` rows (state live, hashes as pulled), and once that branch is merged it tags `<agent>@v0`. Drift and `no_regression` then have something to compare against before the first gated release.
- `abcd audit drift`: the platform keeps no hashes, so abcd records its own at every push. Drift compares each live and staged object's `SKILL.md` hash and aux listing (paths and sizes), and each process's fields, with what `bld_versions` recorded for the last stage and release.
- **Rebuilding from git:** `abcd builder setup` followed by `abcd release --redeploy <agent>@v<n>` restores every definition. `--redeploy` re-uploads a tag that has already been released, with no new gate run. A lead must approve it, and it's recorded like a rollback. Eval history and the knowledge graph live in the platform's tables and Doc Store, so the repo alone can't restore them.

**Branches and teams**
- FDEs work on branches and push them. `abcd stage` works from any pushed commit.
- Changes reach the default branch only by pull request. Another FDE or a lead reviews and merges; nobody merges their own (the repo's branch protection enforces it).
- `abcd release` requires the staged commit to be on the workspace's default branch (`builder.json`), so a release has always been through a merge. A lead can release from another branch with a recorded decision.
- Release tags (`<agent>@v<n>`, `<agent>@v<n>-rc`) and GitHub releases belong to the workspace repo, not to abcd. A tag always points at the staged commit that passed the gate.
- **No command, local or cloud, commits to the default branch.** What a release generates (the report, explainer, training kit and `ADOPT.md` refresh) goes to a `release/<agent>@v<n>` branch with a pull request.

**What `ADOPT.md` holds:** the client and org, which agents are in flight and their phase, the commands for this workspace, the approval rules, and what not to touch (WDL legacy, the default tier). `abcd ws init` generates it. `abcd release` refreshes the agent table in it.

## 6. Where abcd runs: locally and in the cloud

The same abcd code, the same workspace repo, and the same commands run in both places.

| | Local | Cloud |
|---|---|---|
| Where | The FDE's machine, in Claude Code, Codex or Cursor | An Agent Harness turn in the client's Builder home org |
| abcd itself | A checkout of this repo, installed with `poetry install` (the `abcd` command) | `abcd-bundle.zip`, unzipped into `/workspace/abcd` with Python's `zipfile` and installed with `pip install /workspace/abcd` |
| Dependencies | `poetry install` (add `-E wdl` for WDL discovery) | The core install only. NoUI comes with it as a path dependency, and the WDL extras (faiss, sentence-transformers) aren't installed (section 17) |
| The workspace | `workspaces/<env>/`, cloned once | Cloned fresh into `/workspace/abcd/workspaces/<env>/` on every run, at a pinned ref |
| Platform credentials | The FDE's Admin PAT in `workspaces/<env>/.env` | `SANDBOX_ABCD_PAT_CLIENT_ID` and `SANDBOX_ABCD_PAT_SECRET` in the home org vault, written into the workspace `.env` at bootstrap |
| Git credentials | The FDE's own `git` and `gh` login | `SANDBOX_GITHUB_TOKEN`: a fine-grained token limited to this client's workspace repo, passed to `git` through a credential helper and never put in a remote URL |
| Platform endpoint | `ADOPT_WEBUI_ENDPOINT` in `.env` | `ADOPT_APP_BASE_URL`, unless `SANDBOX_ABCD_WEBUI_ENDPOINT` overrides it |
| Who pays for the model | The FDE's subscription | Platform tokens |
| NoUI capture and replay | Yes | No. The Tabby broker is minted for the home org's conversation, not the client org, so recording and replay stay local |
| Used for | Authoring, NoUI capture, local evals, review, gap work, releases | Scheduled work, and steps 2 to 10 of the loop from a chat when the FDE has no machine |

**The `abcd` harness skill** (`harness-skills/abcd/`) mirrors `harness-skills/noui/`:
- **`SKILL.md`, frontmatter `name: abcd`.** It teaches the agent four things:
  - how to stage and install the bundle;
  - how to run `abcd cloud bootstrap`, which clones the repo, writes `.env` from the injected secrets and checks the PAT. It fails loudly, naming every missing variable, because a secret fetch that fails or a badly named secret is dropped without an error;
  - to drive abcd only with `bash` and the same commands as locally;
  - to never print a secret.
- **`build_bundle.py`.** It builds `abcd-bundle.zip` from git-tracked files only, the same way NoUI's does. The zip holds `cli/` (NoUI included), `abcd-builder/`, `adopt-skill-review/` and `pyproject.toml`. It leaves out `workspaces/`, `tabby/` and every `.env`.
- **Deployment.** The skill is pushed with `harness_skill.py push --aux abcd-bundle.zip=...` to the **org tier of the Builder home org only**, never to the default tier. Default-tier skills are visible to every org, and this one is an FDE tool.
- **Checks before upload.** The same frontmatter lint and content secret scan that every push runs cover `SKILL.md` and every bundle member.

**The Builder home org, one per client**
- **Why it exists:** sandbox secrets are org-wide, so an Admin PAT stored in a client org's vault would reach every sandbox there, including the client's own agents. The home org holds the credentials instead. It is Adopt-owned, its users are FDEs and leads, and it has no client users and no client-facing agents.
- **Why one per client:** every sandbox in an org gets every `SANDBOX_*` secret, and cloud runs read untrusted content (transcripts, emails, SME uploads). A shared home org would put every client's Admin PAT in reach of a prompt injection in any one client's material. One home org per client limits that to the client's own PAT and repo. A shared home org is acceptable only if that risk is accepted in writing (section 20).
- **Workstream:** one, `abcd-<env>`, with a Doc Store holding one file, `abcd-workspace.json` (`env`, `repo`, `ref`). The harness copies it into the sandbox, so a run knows its workspace without being told.
- **Processes:**
  - `abcd-runner`: FDE-driven. It does what the chat asks. `abcd release` becomes a gated step, so the FDE's approval is a HITL answer in that chat.
  - `abcd-nightly`: a cron pipeline on the `abcd-<env>` workstream. Every night it runs:
    - `abcd ws pull --check`;
    - `abcd audit drift`;
    - the blind gate on live, when the agent's skills or a default skill it uses changed since the last run;
    - `abcd metric compute`;
    - `abcd status publish`, which writes the client's status files into the client org through the Doc Store routes;
    - the daily FDE report.
  - `abcd-weekly`: the Friday scorecard, sent through the home org's Slack or Gmail integration.
- **Pushing back:** a cloud run never changes the default branch. Code changes go to a pushed `cloud/<conversation>` branch. abcd opens pull requests and GitHub releases through the GitHub REST API, so it doesn't depend on `gh` being in the sandbox image. Reports and status go to the Doc Store and tables.
- **Approvals in the cloud:** the FDE's own chat turns are `user_message` events. So the browser-skill approval token (section 7) and the release approval are both the FDE's own messages, never the agent's.
- **Attribution:** a cloud run logs `bld_dev_events` as `cloud:<process>`, with `ADOPT_CONVERSATION_ID`, so the daily report can tell cloud runs from FDE runs.

**What stays in the client org:** the Builder workstreams, Doc Stores and tables (section 9), the staged and live objects, and the client-facing meta agents: the Status agent, the Context agent and the maker-checker step. None of these hold credentials, and none of them call abcd.

## 7. Versions, git and releases

**On the platform, every skill and agent has a live object and a staged shadow:**

| Object | Source in the workspace | Live | Staged shadow | Assigned to |
|---|---|---|---|---|
| Skill | `harness/skills/tb-reconcile/` | `tb-reconcile` | `tb-reconcile--staged`; frontmatter `name:`, `compiled_flow` and `agent_instructions` references rewritten | n/a |
| Plugin | `harness/plugins/<plugin>/` | `<plugin>` | `<plugin>--staged`, with its skills renamed the same way | n/a |
| Agent | `harness/agents/acme-1120pc-prep/` | `acme-1120pc-prep` | `acme-1120pc-prep-staged`, display name "Return prep (staged)"; components point at the `--staged` skills | Live: client workstream. Staged: staging and dataset workstreams only |

The staged copy is built in a temporary directory from the workspace, so the workspace never holds `--staged` names. What changes in the copy:
- the frontmatter `name:` and the references to other renamed objects;
- the frontmatter `process:` block is dropped, so a staged skill doesn't add a second process template;
- the description starts "Staging copy, do not use".

For a NoUI skill, `operations.json`, `manifest.json`, `amendments.json`, `replay_approval.json` and `recording_bundle.json` (restored from the `noui-bundles` store) are copied byte for byte, so every digest still matches.

**The browser-skill gate runs on every push of a browser skill (operation style `browser`), staged or live, not only at `stage`.** abcd applies the platform's full rule, because the upload route applies none:
1. `noui_core.verify.gate.check_installable` passes: `replay_approval.json` matches the fingerprint of the operations and amendments being pushed.
2. A member approval token with that fingerprint, `<!--adopt:skill-approval v="1" fp="..."-->`, is found in a `user_message` of the conversation recorded in the approval. abcd reads it through `/conversations/{id}/transcript`.

A replay approved outside any harness conversation (a local NoUI run) has no token. It's accepted only with `--local-approval`, which records the FDE as the approver in `bld_versions`. The eval report shows it as a local approval.

**States:**
- **draft:** a commit on a branch, pushed to the shadows.
- **staged:** hashes recorded, and the commit is tagged `<agent>@v<n>-rc`.
- **live:** uploaded under the live names, and the commit, which is on the default branch, is tagged `<agent>@v<n>`.
- **superseded:** its tag is the rollback target.
- **gate failed:** nothing changed under the live names.

**`abcd stage` refuses when:**
- the tree has uncommitted changes, or the commit isn't pushed;
- `harness_skill.py audit` exits non-zero (1 for medium or high findings, 2 for critical) on any skill in the bundle, or the secret scan fails. This is stricter than `push`, which blocks only on 2;
- a browser skill fails the gate above. A changed operation needs a new replay and approval;
- another FDE holds the agent's staged lock.

**`abcd release` does, in order:**
1. Re-checks that the shadows still match the staged hashes, and runs `abcd audit drift` on the live objects.
2. Checks that the staged commit is on the default branch, and that no skill name collides with a default-tier skill without a recorded decision. A name being released for the first time is uploaded without `replace`, so the platform's own collision guard also applies.
3. Checks every release criterion against the staged run on those exact hashes. This covers this agent and every agent that shares a changed skill. It refuses on any failure or any error.
4. Asks the FDE to approve. In the cloud, this is a HITL step in the `abcd-runner` chat.
5. Uploads each staged bundle under its live name with `replace=true`, with the names, `process:` block and description restored. Patches the live process, including its workstreams and write grants. Verifies what landed.
6. Writes the eval report to `releases/<agent>/<version>/` in `builder-ops`, copies it to the client's status workstream, and writes it to `harness/evals/reports/` on a `release/<agent>@v<n>` branch.
7. Inserts a `bld_releases` row with the previous tag as the rollback target. The released `bld_versions` rows already pin the `bundle_version` of every default-tier skill the agent uses.
8. Tags the staged commit `<agent>@v<n>` in the workspace repo, and attaches the report to a GitHub release on that repo (`gh` locally, the REST API in the cloud).
9. Regenerates the explainer and training kit, refreshes `ADOPT.md`, commits them to the same `release/<agent>@v<n>` branch, and opens a pull request.

**Rollback:** `abcd rollback <agent>` re-uploads the previous tag's bundles under the live names, patches the live process, verifies, and records the rollback.

**The default tier:**
- Client workspaces release to the org tier only. `abcd release` never calls `deploy-default`.
- The default tier holds abcd's own `harness-skills/*` (today, `noui`). They're released by abcd's CI (`.github/workflows/deploy-skill.yml`), outside the workspace flow.

**WDL objects:** none of the above applies to them. They keep `save_wdl_draft.py`, `publish_wdl_action.py` and `rollback_changes.py`. Their history is the workspace repo's history.

## 8. The builder loop

Every step runs in the active workspace (`--env` to pick another), locally or in the cloud.

| # | Step | abcd | Platform routes | Runs on |
|---|---|---|---|---|
| 1 | Build or refine from the context bundle; record and compile sites through NoUI | `abcd context pull`, `abcd plan show`, `noui_workspace.py` (capture, compile, generalize, replay, approve) | Doc Store downloads, org-db rows, Tabby | Subscription |
| 2 | Run evals locally | `abcd eval run --tier local` | Dataset input downloads | Subscription |
| 3 | Review with abcd | `abcd review` | none | Subscription |
| 4 | Run on the harness, get logs | `abcd push --staged`, `abcd run --staged` | skills upload, process patch, `POST /turns`, trace | Platform |
| 5 | Performance review and optimise | `abcd trace review` | `/v1/runs` trace | Subscription |
| 6 | Run blind evals on the platform (generic prompt, compiled instructions and skills only) | `abcd eval run --blind` | `POST /turns` per case or batch, outputs download, probe turns | Platform |
| 7 | Close context gaps with the FDE and SME until blind evals pass | `abcd gap list, ask, answer, encode, waive`, `abcd instructions compile` | Doc Store (SME workstream), org-db rows | Subscription |
| 8 | Freeze as staged | `abcd stage` | org-db rows | n/a |
| 9 | Test the staged agent, blind | `abcd test-staged` | skills read, `POST /turns` | Platform |
| 10 | Publish the new version | `abcd release` | skills upload, process patch, Doc Store upload | Platform |

In the cloud, "Subscription" steps run on platform tokens instead, and step 1's NoUI capture and replay stay local (section 6).

**`abcd <group> <command>` is the installed `abcd` CLI** (section 17). The existing scripts keep working as `python cli/<script>.py`, and WDL is reachable as `abcd wdl <script>`.

**What a step reuses from the combined branch:**

| abcd command | Reuses |
|---|---|
| `abcd review` | `harness_skill.py audit` (the vendored `adopt-skill-review` audit). `push` already runs it, and the content secret scan, first |
| `abcd push [--staged]` | `harness_skill.py push` and `push-plugin`, plus the staged rewrite, the browser-skill gate, and an agent-bundle push that calls the process routes in `harness_common/api_client.py` directly (the `harness_process.py` CLI can't express default-source components, `compiled_flow` or write grants) |
| `abcd run` | `harness_run.py start --workstream --process` |
| `abcd trace` | `harness_trace.py fetch` and `review` (`harness_common/trace_review.py`) |
| `abcd runs` | `harness_runs.py` |
| Test and dataset workstreams | `harness_workstream.py ensure` and `seed` (workstream plus linked store plus uploads) |
| `abcd ws pull` | `harness_process.py get`, and the skill and plugin GETs in `harness_common/api_client.py` |
| NoUI scenario | `tabby_bootstrap.py`, `noui_workspace.py`, the `discover-and-plan` and `skill-builder` skills |
| Browser-skill gate | `noui_core.verify.gate.check_installable`, plus the platform's approval-token rule, reimplemented in abcd |
| Cloud packaging | The `harness-skills/noui/build_bundle.py` pattern |

Cron pipelines have no abcd client. They're created once from a browser (section 4).

## 9. Platform workstreams, Doc Stores and tables

These live in the client org. Their ids are recorded in the workspace's `builder.json` by `abcd builder setup`, so every FDE and every cloud run finds them without listing.

**Workstreams, created by `abcd builder setup`:**

| Workstream | How many | Holds | Who has access |
|---|---|---|---|
| `builder-context` | One per org | Knowledge graph, call context, process docs, decisions, agent design pages, explainers, training | FDEs (write) |
| `builder-ops` | One per org | Versions, releases, eval runs and reports, metrics, FDE activity, dataset expected-output stores | FDEs (write) |
| `builder-sme-<client>` | One per client with an appointed SME | Gap questions with evidence (`gaps/<gap_id>.md`), answers, repro notes, uploads | Customer SME (write); FDEs; the Context agent |
| `builder-status-<client>` | One per client | Scorecard, agent status pages, eval reports, training kit, as files | `abcd status publish` (write, from the cloud run); client stakeholders (read) |
| `<client>-staging` | One per client | A copy of selected client files; staged agents run here | FDEs only |
| `ds:<dataset>[:<case>]` | One per case (work-product pairs) or one per dataset (labelled, read-your-writes) | Case inputs only; `custom_properties.kind = dataset`. For read-your-writes, its secrets point at the test system | FDEs only |

**Doc Store layouts:**

```
builder-context/
  kg/index.md  kg/schema.md  kg/log.md
  kg/clients/<client>.md  kg/systems/<system>.md  kg/apis/<system>/<api>.md
  kg/skills/<skill>.md  kg/agents/<agent>.md  kg/datasets/<dataset>.md  kg/incidents/<date>-<slug>.md
  kg/instructions/<agent>/<instruction>.md
  context/calls/<client>/<date>-<slug>.md
  context/emails/<client>/<date>-<slug>.md
  context/process-docs/<client>/<process>/sop.md
  decisions/<client>/<decision_id>.md
  agents/<agent>/design.md  agents/<agent>/explainer.html  agents/<agent>/training/*.md
  proposed/                                  pages and facts waiting for review

builder-ops/
  releases/<agent>/<version>/{manifest.json, eval_report.html, eval_report.json, diff.md, notes.md}
  evals/<eval_run_id>/{summary.json, cases/<case_id>/{checks.json, observed.json, trace.ndjson}}
  sessions/<fde>/<date>/<session_id>/{transcript.jsonl, summary.md}
  reports/daily/<date>/<fde>.md

ds-<dataset>/ (store linked to builder-ops only)
  manifests/v<n>.json  expected/<case_id>/...   expected files, labels or states

ds:<dataset>[:<case_id>]/ (dataset workstream)
  inputs/... or inbox/...  outputs/<eval_run_id>/...

builder-status-<client>/
  scorecard.md  scorecards/<date>.html
  agents/<agent>/status.md  agents/<agent>/releases/<version>/eval_report.html  agents/<agent>/training/
```

**What lives where:**
- **Definitions** go in the workspace repo: skills, agent bundles, checks, metrics, criteria, dataset manifests and case files, scorers, generic prompts and released reports.
- **Large or client-confidential inputs, NoUI capture bundles, and every expected output** go in the Doc Store.
- **Indexes and history** go in the tables.
- abcd mirrors definitions into their tables (`bld_checks`, `bld_metrics`, `bld_release_criteria`, `bld_dataset_cases`, `bld_generic_prompts`) whenever they change, so platform-side agents can query them. When the two disagree, the repo wins.
- **No answer ever sits in a table.** The Data Store has no row security, and any agent under test with `db_query` can read the whole org. So expected values, labels, states, case notes and expected-versus-observed diffs live only in the `ds-<dataset>` and `builder-sme` Doc Stores. Tables hold their paths and SHA-256 hashes, never their values.

**Doc Store rules:**
- Never overwrite: a versioned file always gets a new path. The exceptions are the pointer files that are rewritten on purpose: `scorecard.md`, each `status.md`, and `kg/log.md` (appended to). Each has a dated, frozen copy wherever history matters.
- Tables hold the paths, so nothing ever lists a folder.
- Use markdown for anything an agent reads, and HTML only for things people open.

**Tables.** All are Data Store custom tables, created through `/v1/org-db/tables` by a new `cli/builder_common/orgdb.py` client.
- Every table has `workstream_scoped = false`, because rows written through the external API get a null `workstream_id`.
- Each table is linked with write access to its Builder workstream (`POST /v1/org-db/tables/{id}/workstreams`). The platform-side writers need their own links:
  - every live agent's workstreams, for the maker-checker step's `bld_check_results`;
  - `builder-sme`, for the Context agent's `bld_context_gaps` and `bld_case_notes`;
  - `builder-context`, for the curator's `bld_decisions`, `bld_open_items` and the KG tables.
  
  No dataset workstream is ever linked to a table.
- Each has one unique key, so abcd retries are idempotent and a 409 means the row was already written.
- Enum values are checked by abcd, because the Data Store doesn't enforce them.

| Group | Table | Unique key | Purpose |
|---|---|---|---|
| Registry | `bld_agents` | agent_key | One row per agent: process ids (live and staged), client, owner, bundle path in the workspace, live and staged version, staged lock (holder, SHA, since), phase |
| Registry | `bld_build_plan` | item_key | Skills, plugins and agents to build or change; proposed, approved, building, done |
| Versions | `bld_versions` | version_id | Every version: kind, name, content hash, git SHA, state, pinned components (with default-tier `bundle_version`s), replay approval fingerprint for browser skills, reason |
| Versions | `bld_releases` | release_id | Every release: version, previous version, eval run, gate result, report path, approver, forced reason |
| Versions | `bld_release_criteria` | criteria_key | Per agent: dataset version, metric, comparator, target, decision reference |
| Datasets | `bld_datasets` | dataset_key | Dataset index: client, kind, layout, run mode, test environment and probe agent (read-your-writes), default scorers, current version |
| Datasets | `bld_dataset_versions` | dataset_version_key | One row per version: case count, change note, manifest path |
| Datasets | `bld_dataset_cases` | case_key | Case of any kind: workstream, inputs, setup, the path and hash of its expected files, labels or state (never the values), teardown, scorers, source (real, known, synthetic, production), tags, active versions |
| Quality | `bld_checks` | check_key | Client checks: deterministic rule or judged rubric, severity, source decision |
| Quality | `bld_metrics` | metric_key | Metric definitions: category, unit, formula, source, baseline, gate flag |
| Quality | `bld_eval_runs` | eval_run_id | One row per eval run: version, dataset version, tier, blind flag, where it ran (local or cloud), submitted turn ids (so a run can resume), cost, report path |
| Quality | `bld_eval_results` | result_key | Per case per run: path of the observed output and of the diff against expected (both in `builder-ops`), passed, score, failed check ids, miss cause, steps, tokens, duration, HITL count |
| Quality | `bld_check_results` | check_result_key | Per check per subject, in evals and in production (written by the maker-checker step) |
| Quality | `bld_metric_values` | value_key | Computed values per run, phase and week |
| Context | `bld_kg_nodes` | node_key | Knowledge graph nodes: type, name, page path, proposed or vetted |
| Context | `bld_kg_edges` | edge_key | Typed edges: uses, calls, owns, replaced_by, failed_on, tested_by |
| Context | `bld_decisions` | decision_key | Every answered question: answer, who, source, status |
| Context | `bld_open_items` | item_key | What's pending on an agent's design and who it waits on |
| Context | `bld_context_gaps` | gap_key | A case a blind eval couldn't reproduce because context was missing: hypothesis, question, evidence path, owner (FDE or SME), path of the answer in `builder-sme` (an answer is generic context, but it's stored by path like every other SME file), instruction, status (open, asked, answered, encoded, verified, waived), the verifying run |
| Context | `bld_case_notes` | note_key | Path and hash of the note, in `builder-sme`, on how the SME or FDE produced the golden output for one case. Never shown to a blind run |
| Context | `bld_instructions` | instruction_key | Generic instructions: scope (org, client, agent), page path, the gaps each closes, provenance, leak check, status, the instruction-skill version it was compiled into |
| Context | `bld_generic_prompts` | prompt_key | The generic prompts blind evals use, per agent (mirrored from `prompts.yaml`) |
| Context | `bld_training_assets` | asset_key | Explainer and training files per live version |
| Activity | `bld_dev_sessions` | session_id | Coding-agent sessions per FDE, and cloud conversations |
| Activity | `bld_dev_events` | event_id | Every abcd command and loop step, with workspace, result, cost, and whether it ran locally or in the cloud |

Column-level detail is in the HTML's Data tables page.

## 10. Datasets, metrics, checks and the gate

**Datasets**

A dataset is how an FDE says what "correct" means for an agent. Not every agent produces a document to compare against, so every dataset has a **kind**. All kinds share one case shape:
- `inputs`
- optional `setup`
- `expected`
- optional `teardown`
- `scorers`
- `source` (real, known, synthetic, production)
- `tags`

In the workspace, a dataset is `harness/datasets/<dataset>/manifest.json` plus one `cases/<case_id>.yaml` per case. Input and expected files are referenced by Doc Store path; they're never copied into git. Adding a new kind means a new runner and scorer in abcd. It never needs a new table or a platform change.

| Kind | Example agent | A case holds | Workstreams | How it runs | Scored by |
|---|---|---|---|---|---|
| `work_product` | Tax return prep (1120-PC) | Input files plus the expected output files | One per case | One turn per case | `field_match`, `rubric`, client checks |
| `labeled` | Email sorting into client workstreams | One input item (an email with attachments) plus its expected labels: client workstream, document types, page splits | One shared dataset workstream (inbox folder) | One batch turn, or one turn per case | `label_match` (confusion matrix per client), `boundary_match` |
| `read_your_writes` | CRM updates (Dynamics 365) | An instruction that should cause a write, a setup snapshot, the expected state and outcome code, and a teardown | One shared workstream whose secrets point at the test system | Sequential: probe snapshot, agent turn, probe read-back, teardown | `outcome_match`, `state_match`, `collateral` |
| `custom` | Anything else (SMB drops, API side effects, property checks) | Any inputs and expectation | Either | Any | `script:<name>`, i.e. `harness/evals/scorers/<name>.py` in the workspace |

**Browser skills from NoUI:** the goal NoUI recorded for a browser skill (`goals.py`) becomes a draft `goal_reached` check on every agent that uses the skill, proposed through `bld_build_plan`. The replay approval shows the operations worked once against the live app. The dataset shows the agent gets the right outcome. A release needs both.

**Labelled cases (email sorting)**
- Cases are **known** (historical emails whose routing a person confirmed) or **synthetic**. Synthetic cases are generated by `abcd dataset synth` in the FDE's coding agent from templates and the known client list, so every mapping is known up front.
- Known and synthetic cases are tagged separately, so release criteria can hold known cases to their own target.
- Boundary-detection HITLs stay blocking. The eval counts them; it doesn't tune them away.

**Read your writes (CRM)**
- The output that matters is what the CRM holds after the write.
- **The probe:** a small read-only agent, `acme-crm-probe`, kept as an agent bundle in the workspace like any other. It has one SQL skill using a read-only CRM login, and it runs through `POST /turns` on the same workstream. It runs there because the CRM is reachable only from the harness sandbox over the tunnel.
- **Each case runs in order:**
  1. The probe snapshots the target record.
  2. The agent turn performs the write.
  3. The probe reads the record back.
  4. The teardown restores the snapshot through the same WinRM executables. There are no direct database writes, ever.
- **Scoring:**
  - `outcome_match` checks the result code class: 0 updated, 1 updated with an overwrite warning, below 0 refused (for example -1 no tax return, -2 invalid year).
  - `state_match` checks the fields read back.
  - `collateral` checks that other rows in scope are unchanged.
- **Safety:** the dataset declares `target_env` (e.g. `CRM_TEST_DB`). Before the first case, abcd asks the probe which server and database it's connected to, and refuses if the answer isn't the declared one.

**Why expectations live in a separate store:** the harness copies every store linked to a workstream into the sandbox. If expected outputs or labels sat in the dataset workstream, the agent under test could read the answers. They live in `ds-<dataset>`, which is linked only to `builder-ops`. For the same reason they never go in the workspace repo, which a cloud run clones into a sandbox.

| Operation | abcd | Platform routes underneath | Effect on the dataset version |
|---|---|---|---|
| Create | `abcd dataset create --kind ... [--target-env --probe]` | Store resolve, workstream create (shared layouts), assign the staged agent and probe, rows | v1 |
| Add a case | `abcd dataset add-case` (files, `--label`, or `--expect-state`) | upload-urls + confirm, workstream create (per-case layout), rows | New version |
| Generate synthetic cases | `abcd dataset synth --from templates --count N` | upload-urls + confirm, rows | New version |
| List and search | `abcd dataset show --source --tag` | org-db rows and SQL | None |
| Correct an expectation | `abcd dataset update-case` | Upload to a new path, PATCH the row; needs a lead's approval if it loosens a gate | New version |
| Retire a case | `abcd dataset retire-case` | PATCH the row (`retired_in_version`); files kept | New version |
| Archive | `abcd dataset archive` | Refused while any criteria reference it; otherwise soft-delete the dataset workstreams | Frozen |

Every operation also writes the case files and manifest in the workspace, and commits them. Two releases are only ever compared on the same dataset version.

**Metrics**
- A metric definition is versioned and has: name, category, unit, formula, source, baseline, a target per phase, a gate flag, and a client sign-off decision. It lives in the agent's `metrics.yaml`.
- Sources are check results, HITL events, user actions by role, run timings and cost.
- `abcd metric compute` calculates values from eval results, check results and `/v1/runs`.

**Rules from the first pilot**
- Accuracy means the output passes all of the client's checks. When a check is added, past runs are re-scored.
- Every metric has to be able to fail. First-pass yield is the share of clients that pass every check on the first run with no human correction.
- Human time comes from platform logs, split by onshore and offshore role. Nobody watches screens.
- Misses are broken down by cause: rounding, missing context, playbook difference, real error.
- There is one scorecard, and the eval reports use the same numbers.

**Checks**
- A check is either deterministic (a rule, scored in Python by abcd) or judged (a rubric, scored by the FDE's coding agent locally, or by the harness agent in the cloud). It lives in `harness/checks/<check>.yaml`.
- Every check runs in evals. In production, it runs as a maker-checker step inside the live agent, which writes `bld_check_results` itself.

**The gate (`abcd release`)**
- Every criterion must pass on a staged run of the exact staged hashes.
- `no_regression` fails if any case that passed on live now fails without a recorded reason.
- The gate refuses on any error.
- A lead can force a release with a recorded decision. A forced release stays flagged until a passing run catches up.

**The eval report has six sections:**
1. Header
2. Release criteria against live and staged
3. Regressions
4. Misses by cause
5. Cost and performance
6. What changed and why

## 11. Context gaps and blind evals

**Requirement (R21):** an agent is ready when it reproduces the golden dataset **blind**. The FDE and a customer-appointed SME keep adding missing context during the build phase until that's true. It's mandatory for every agent linked to a dataset.

**What a blind run gets, and what it doesn't**

| Given | Withheld |
|---|---|
| A generic prompt from the agent's `prompts.yaml` (e.g. "Prepare the 1120-PC for this client from the files in this workstream") | Expected outputs, labels and states |
| The case inputs, exactly as a client would provide them | Case notes (`bld_case_notes`) |
| The compiled `<agent>-instructions` skill and the agent's other skills | Gap files and their evidence |
| Client-scope instructions synced into the case workstream | Any FDE message, hint or follow-up turn |
| | Answers to questions the agent asks: a question in a blind run is itself a context gap |

**The gap loop**
1. **Blind run:** `abcd eval run --blind` scores every case against the golden dataset.
2. **Classify misses:** the `context-gaps` skill sorts each miss into missing context, skill bug, rounding, playbook difference or real error. Only missing context opens a gap; the rest become build plan items.
3. **Open a gap:** a `bld_context_gaps` row, plus `gaps/<gap_id>.md` in `builder-sme-<client>`. The file holds the input excerpt, the agent's output against the golden output, the agent's reasoning, and the Builder's hypothesis of what's missing.
4. **Route it:** the FDE answers it, or runs `abcd gap ask` to send it to the SME. The SME sees it in their workstream and by email.
5. **Answer:** the SME works through the gap with the Context agent, which asks follow-ups until the answer is a reusable rule. Explanations specific to one case are saved as repro notes.
6. **Encode:** the `context-gaps` skill turns the answer into a generic page under `kg/instructions/<agent>/`, with its provenance. The FDE or SME vets it.
7. **Leak check:** abcd refuses any instruction that names a case id, quotes a value from any expected output, or only makes sense for one case.
8. **Compile and re-run:** `abcd instructions compile` builds the instruction skill into the workspace at `harness/skills/<agent>-instructions/` and commits it. Then the affected cases re-run blind, followed by the full set.
9. **Verify:** a gap closes only when a blind run reproduces its case without regressing any other case. Otherwise it reopens with the new evidence.

**Gap states:** open, asked, answered, encoded, verified, waived. A waived gap needs a lead's recorded reason and is counted on the scorecard.

**How instructions reach the agent, with no platform change**
- **Org and agent scope:** compiled into the `<agent>-instructions` skill (reference files plus an index in SKILL.md). It's a workspace skill like any other, so it's versioned, staged and released the same way.
- **Client scope:** synced by abcd into `_builder/instructions/` in the client workstream and its dataset workstreams, so the harness stages them alongside the client's files.

**Build phase exit criteria**
- Every active case reproduces blind, or its gap is waived with a lead's approval.
- No gaps are open, asked or encoded.
- No instruction has a flagged leak check.
- The blind reproduction rate meets its release criterion.

**Metrics this adds**
- blind reproduction rate (a gate metric)
- open gaps by owner (FDE or SME), shown on the client scorecard
- time to close a gap
- instructions per agent, and the gaps each one closed

**The gate:** every gated eval (`abcd test-staged`, and the run `abcd release` checks) is blind. Release criteria for a linked agent always include `blind_repro_rate` and `open_gaps <= 0`.

## 12. Knowledge graph and context

- **Pages** are markdown in `builder-context/kg/`, with `index.md`, `schema.md` and an append-only `log.md`.
- **The graph** is `bld_kg_nodes` and `bld_kg_edges`. It answers "what does this change affect?" before a push.
- **Trust:** agents propose and FDEs vet. Proposed pages live in `proposed/`.
- **Coming in:**
  - call transcripts, emails and SOPs uploaded to `context/`;
  - NoUI writing `kg/apis/` and `kg/skills/` when it compiles a site;
  - releases updating `kg/agents/`.
- **Context curator:** a process on `builder-context`, run by a nightly cron pipeline. It uses `ws_*` tools and `db_insert_rows`. It stays in the client org because it needs no credentials.
- **Going out:** `abcd context pull <agent>` downloads into `workspaces/<env>/context/`, which is ignored by the workspace repo:
  - the agent's page and its neighbours one or two edges away;
  - its decisions and open items;
  - its checks, metrics and criteria.

## 13. Meta skills and meta agents

**The `abcd-builder` plugin** (`abcd-builder/`, listed in abcd's `.claude-plugin/marketplace.json`) is the one plugin FDEs install. It ships four skills today. The Builder extends it to thirteen and bumps it to 0.2.0. `adopt-skill-review` stays a separate plugin in the same marketplace, and `abcd review` calls it.

| Skill | Status | Covers (the first draft's `builder-*` names) |
|---|---|---|
| `discover-and-plan` | exists | NoUI capture, compile and generalize (`builder-noui-scenario`, front half) |
| `skill-builder` | exists, extended | Replay, approve, install, push, run, trace, plugins (`builder-skill-author`, `builder-harness-run`, `builder-noui-scenario` back half); gains `--staged` |
| `action-builder` | exists, legacy | WDL actions. Unchanged |
| `pipeline-builder` | exists, legacy | WDL pipelines. Unchanged |
| `workspace-setup` | new | `abcd ws init`, `clone`, `status` and `pull`, `ADOPT.md`, `abcd builder setup`, switching clients (`builder-onboard`) |
| `build-plan` | new | `abcd plan propose` and `approve` (`builder-plan`) |
| `agent-builder` | new | Agent bundles in `harness/agents/` from an SOP (`builder-process-from-sop`) |
| `context-and-kg` | new | `abcd context pull`, KG pages, decisions (`builder-context`, `builder-kg`) |
| `dataset-builder` | new | Datasets, checks, metrics, criteria (`builder-dataset`, `builder-checks`, `builder-metrics`) |
| `eval-runner` | new | `abcd eval run`, local and blind; judged scoring; perf review (`builder-eval`, `builder-perf`) |
| `context-gaps` | new | Gaps, the SME loop, instructions and the leak check (`builder-gaps`, `builder-instructions`) |
| `release-manager` | new | `stage`, `test-staged`, `release`, `rollback`, `audit drift`, the default-tier rules (`builder-release`) |
| `explain-and-train` | new | `abcd explain`, `abcd train` (`builder-explain`, `builder-train`) |
| (`adopt-skill-review` plugin) | exists | `abcd review` (`builder-review`) |

The plugin is installed once per FDE machine, at onboarding (it's user-global, not per workspace), through abcd's marketplace (`claude plugin marketplace add <abcd checkout>` then `claude plugin install abcd-builder@abcd`, or `npx skills add <abcd checkout>` for other agents), as the README already documents. In the cloud, the plugin's skills ship inside `abcd-bundle.zip`, and the `abcd` harness skill points the agent at them.

**Meta agents:**

| Agent | Runs as | Where | Trigger | Writes |
|---|---|---|---|---|
| `abcd-runner` | Process using the `abcd` skill | Builder home org, one workstream per workspace | An FDE asks, in a harness chat | Whatever abcd writes for that command; code changes as a pushed branch and pull request |
| `abcd-nightly` | Process using the `abcd` skill | Builder home org | Nightly cron pipeline | Drift findings, blind-gate re-runs, metric values, client status files, the daily FDE report |
| `abcd-weekly` | Process using the `abcd` skill | Builder home org | Friday cron pipeline | The Friday scorecard, frozen in the status workstream and sent by Slack or Gmail |
| Context curator | Process on `builder-context` | Client org | Nightly cron pipeline | `proposed/`, `bld_decisions`, `bld_open_items` |
| Status agent | Process on `builder-status-<client>` | Client org | A client asks | Nothing (read only) |
| Context agent | Process on `builder-sme-<client>` | Client org | The SME opens a gap or uploads something | `bld_context_gaps` (answered), `bld_case_notes` |
| Maker-checker step | A step inside each live agent | Client org | Every production run | `bld_check_results` |
| Eval runner | `abcd eval run` | Local or cloud | On demand, or nightly | Eval tables and `evals/` |
| Explainer and trainer | `abcd explain`, `abcd train` | Local or cloud | `abcd release` | `harness/agents/<agent>/`, `bld_training_assets` |

The first draft's Status reporter, a client-org process writing into other workstreams, is replaced by `abcd status publish` in the nightly cloud run. It writes through the Doc Store routes with the workspace's PAT.

Cron pipelines are created once from a browser session, because a pipeline created with a PAT records the token id as its owner.

## 14. Roles and access

| Role | Platform setup | Can do |
|---|---|---|
| FDE | Admin user with a PAT in each client org they build for (every write route needs Admin); a user in that client's Builder home org; write access to the client's workspace repo, and can merge another FDE's pull request | Everything in the loop, locally or in the cloud. Lowering a target or editing an expected output needs a lead's recorded approval. Sees only their own activity in reports |
| FDE lead | Admin user; admin in the Builder home org; can merge to a workspace's default branch | Forces a release with a reason, approves loosened criteria, approves a default-tier name collision, gets every daily report |
| Cloud runner | The home org's `abcd-*` processes, using the per-workspace Admin PAT and GitHub token from the home org vault | What the nightly and weekly runs do, and what an FDE asks in an `abcd-runner` chat. Never merges or releases without an FDE's approval |
| Builder agents in the client org | Processes | Propose only: pages to `proposed/`, candidate checks and decisions |
| Client stakeholder | Operator user, read grant on `builder-status-<client>` only, `studio.agents_enduser` on for the org | Sees the scorecard, agent status, releases, eval reports and pending items; asks the Status agent; gets the Friday email |
| Customer SME, appointed by the customer | Operator user, write grant on `builder-sme-<client>`, read grant on `builder-status-<client>` | Answers context gaps with the Context agent, adds repro notes, sends checks, vets the generic instructions drawn from their answers |

## 15. Status for clients and leads

**What a client stakeholder sees in `builder-status-acme`, in the end-user Agents cockpit:**
- `scorecard.md`, in the format the client agreed. The nightly cloud run rewrites it, and the Friday run freezes it as `scorecards/<date>.html`.
- For each agent, `status.md`: live and staged version, criteria progress, and open items, each marked "waiting on the client" or "with Adopt" with a due date.
- Every release's eval report and training kit.

They can also ask the Status agent questions such as "what is pending on the return prep agent?" The Friday scorecard also arrives by email.

**What leads get:**
- The same files for every client.
- Every FDE's daily report: workspaces and agents touched, time per loop step, releases and their scores, platform spend (FDE turns and cloud runs, separately), and where they got stuck.

## 16. Explainers and training

On every release:
- `abcd explain` regenerates `harness/agents/<agent>/docs/explainer.html`: an interactive page for the agent, covering steps, skills, versions, dataset, criteria, checks, last eval, open items and scenarios.
- `abcd train` regenerates `harness/agents/<agent>/training/`: quickstart, simple prompts, scenarios, FAQ (from real questions in runs and HITL) and what's new.

Both run in the FDE's coding agent, or in the cloud runner when the release ran there. Prompts in the kit are run on a dataset case during `abcd test-staged`, and only the ones that work ship. A kit that fails to generate is flagged on the release; the release itself isn't blocked.

## 17. One CLI: `abcd`

**Today:** abcd's command surface is 46 separate scripts in `cli/`. Each has its own `argparse` and its own `sys.path.insert` so it can be run as `python cli/<script>.py`. Only 18 of them take `--env` and only 6 take `--json`. The `abcd-builder` skills call `python cli/...` 111 times, and the README and `agents.md` another 327 times. `pyproject.toml` has `package-mode = false`, so there's no `abcd` command and abcd can't be pip-installed. That's why cloud mode would otherwise have to run abcd in place with `PYTHONPATH` and a hand-picked dependency list.

**The Builder first turns abcd into one installable CLI.** Every Builder command is born inside it, and the existing scripts move behind it without breaking.

**Packaging**
- `pyproject.toml` switches to package mode. The `cli` package is installed and `[tool.poetry.scripts] abcd = "cli.abcd:main"` gives an `abcd` command. `poetry install` locally, or `pip install -e .` in a checkout or a sandbox, both provide it.
- NoUI stays its own package (`cli/noui`, `noui_core`) and becomes a path dependency of abcd, so one install brings both.
- **Extras:**
  - The core install covers the harness, NoUI and Builder commands: requests, httpx, python-dotenv, PyYAML, pydantic, colorama, plus NoUI.
  - `abcd[wdl]` adds faiss-cpu, sentence-transformers and numpy, which only WDL discovery uses.
  - `abcd[ce]` adds Playwright, replacing today's `ce` group.
- **Cloud:** `abcd-bundle.zip` carries the source, and the `abcd` harness skill runs `pip install /workspace/abcd` (core only). `PYTHONPATH` and the hand-written dependency list go away (section 6).

**The command tree:** `abcd <group> <command>`. One root parser builds it from a registry. Each module registers its own group.

| Group | Commands | Comes from |
|---|---|---|
| `ws` | `list`, `use`, `show`, `create`, `delete`, `init`, `clone`, `status`, `pull`, `push-bundles`, `pull-bundles` | `workspace.py env ...` (moved) plus new Builder commands |
| `skill` | `audit`, `push`, `verify`, `delete`, `deploy-default` | `harness_skill.py` (moved) |
| `plugin` | `push` | `harness_skill.py push-plugin` (moved) |
| `agent` | `list`, `get`, `create`, `update`, `delete`, `workstreams`, `run`, `push`, `pull` | `harness_process.py` (moved) plus bundle push and pull |
| `run`, `runs`, `trace`, `conversations` | as today | `harness_run.py`, `harness_runs.py`, `harness_trace.py`, `harness_conversations.py` (moved) |
| `workstream` | `ensure`, `seed` | `harness_workstream.py` (moved) |
| `noui` | `env`, `paths`, `list`, `doctor`, `scripts`, `run`, and each NoUI script | `noui_workspace.py` (moved) |
| `tabby` | `status`, `init`, `up`, `provision`, `down`, `full` | `tabby_bootstrap.py` (moved) |
| `builder` | `setup` | new |
| `plan`, `dataset`, `metric`, `check`, `criteria`, `eval`, `gap`, `instructions`, `context`, `status`, `report`, `explain`, `train`, `cloud` | as in sections 8 to 16 | new |
| `stage`, `test-staged`, `release`, `rollback`, `audit` | as in section 7 | new |
| `wdl` | `abcd wdl <script> [args]`, e.g. `abcd wdl save_wdl_draft ...` | Every WDL script, passed through unchanged (legacy) |

**One command layer** (`cli/core/`):
- **`context.py`:** resolves `--env` once for every command, with the active workspace as the default. It loads the workspace `.env` the way `harness_common/auth.py` does, and builds API clients lazily.
- **`output.py`:**
  - A command returns a result object. Without `--json` it's printed for people, the way the scripts print today. With `--json` it's printed as one JSON document on stdout, and logs and progress go to stderr.
  - Exit codes are the same everywhere: 0 done; 1 refused, or a check or gate failed; 2 usage error; 3 platform or network error.
- **`events.py`:** wraps every command once to write `bld_dev_events` (R16), instead of each script logging for itself.
- **Global flags on every command:** `--env`, `--json`, `--yes` (never prompt; every approval that matters is a recorded decision or a HITL step, not a terminal prompt), `--verbose`. Every command that writes also takes `--dry-run`.

**Migration, without breaking anything**
1. **Builder commands are written natively** as `cli/builder_*.py` modules that only register commands. No standalone script is added.
2. **The harness, NoUI and Tabby scripts move behind `abcd`.** Each keeps its file, splits into `register(subparsers)` plus handler functions, and keeps `if __name__ == "__main__": sys.exit(main())`. So `python cli/harness_skill.py push ...` and `abcd skill push ...` run the same code. The `sys.path.insert` hacks stay only in that `__main__` path, for a checkout without an install.
3. **WDL scripts aren't touched.** `abcd wdl <script>` runs the script's `main()` with the remaining arguments. Their docs stay as they are, with one note pointing at `abcd wdl`.
4. **Docs follow the code.** Each move updates the `abcd-builder` skills, the README and `agents.md` for that group in the same change. `skill-builder` and `discover-and-plan` switch to `abcd ...`; `action-builder` and `pipeline-builder` switch to `abcd wdl ...`.
5. **Tests guard it** (`tests/cli/`):
   - every registered command accepts `--env` and `--json`;
   - `--json` output parses and keeps stdout clean;
   - every `abcd ...` or `python cli/...` command written in a `SKILL.md`, the README or `agents.md` resolves to a real command (like the existing `tests/abcd/test_plugin_layout.py`);
   - an old script and its `abcd` command give the same result on the offline fixtures.

## 18. Where the new code goes in abcd

Everything follows the conventions already on the branch, plus the CLI rules in section 17:
- one `cli/<area>.py` module per command group, registered in the `abcd` CLI, with shared code in a `cli/<area>_common/` package;
- `--env`, `--json`, `--yes` and `--verbose` on every command, with `--env` defaulting to the active workspace;
- the workspace `.env` loaded the same way as `harness_common` loads it;
- the frontmatter lint, `audit` and secret scan before anything is uploaded;
- `ruff` over `cli/`, `tests/` and `harness-skills/`, and `mypy` over `cli/`, as CI runs them.

| Path | What |
|---|---|
| `pyproject.toml` | Package mode, the `abcd` console script, NoUI as a path dependency, the `wdl` and `ce` extras |
| `cli/abcd.py` | The `abcd` entry point: the root parser built from the command registry |
| `cli/core/` | `registry.py`, `context.py` (`--env`, `.env`, clients), `output.py` (`--json`, exit codes), `events.py` (`bld_dev_events`), `passthrough.py` (`abcd wdl`) |
| `cli/builder_common/workspace.py` | Builder paths on top of `harness_common/workspace.py`: `builder.json`, agent bundles, datasets, checks, reports; the workspace `.gitignore` and `ADOPT.md` templates |
| `cli/builder_common/repo.py` | `git` for workspace repos (init, clone, status, tags, the default-branch check, the pre-commit hook), and pull requests and releases through `gh` locally or the GitHub REST API in the cloud |
| `cli/builder_common/orgdb.py` | Data Store client (`/v1/org-db/*`), extending `HarnessAPIClient` |
| `cli/builder_common/staging.py` | The staged rewrite (frontmatter names, `process:` block, description, component refs, plugin skills) into a temp dir; byte-for-byte copies for NoUI files |
| `cli/builder_common/browser_gate.py` | The browser-skill gate: `check_installable` plus the approval token from the conversation transcript |
| `cli/builder_common/agent_bundle.py` | `process.yaml`, `instructions.md` and `flow.json` to and from `/v1/org/processes` (including `/write-workstreams`), calling `harness_common/api_client.py` directly |
| `cli/builder_common/versions.py`, `drift.py` | Content hashes, `bld_versions`, the staged lock, drift (including org skills that shadow default skills) |
| `cli/builder_common/evals/` | One runner per dataset kind, the scorers, blind mode, resumable runs, the report |
| `cli/builder_common/gaps.py`, `instructions.py` | Gap lifecycle, instruction compilation, the leak check |
| `cli/builder_common/events.py` | `bld_dev_events` logging around every command, tagged local or cloud |
| `cli/builder_ws.py` | `abcd ws init, clone, status, pull, push-bundles, pull-bundles` (the existing `env` commands move into `ws`) |
| `cli/builder_setup.py` | `abcd builder setup` |
| `cli/builder_plan.py` | `abcd plan propose, approve, show` |
| `cli/builder_agent.py` | Agent bundle push and pull |
| `cli/builder_release.py` | `abcd push --staged`, `stage`, `test-staged`, `release`, `rollback`, `audit drift` |
| `cli/builder_dataset.py` | `abcd dataset ...` |
| `cli/builder_quality.py` | `abcd metric`, `check`, `criteria` |
| `cli/builder_eval.py` | `abcd eval run` (resumable), scoring, the report |
| `cli/builder_gap.py` | `abcd gap ...`, `abcd instructions compile` |
| `cli/builder_context.py` | `abcd context pull`, KG pages |
| `cli/builder_status.py` | `abcd status <agent>` (read-only summary), `abcd status publish` (writes the client status files), `abcd report daily` and `abcd report scorecard` |
| `cli/builder_explain.py` | `abcd explain`, `abcd train` |
| `cli/builder_cloud.py` | `abcd cloud bootstrap` |
| `abcd-builder/hooks/` | Claude Code hooks that upload session transcripts to `builder-ops/sessions/` and log `bld_dev_sessions` (R16) |
| `abcd-builder/skills/<skill>/SKILL.md` | The nine new skills in section 13; `skill-builder` gains the staged flow |
| `harness-skills/abcd/` | `SKILL.md`, `build_bundle.py`, `README.md` for the cloud runner (section 6) |
| `tests/cli/` | The CLI guards in section 17 |
| `tests/builder/` | Offline tests against a fake API client, following `tests/abcd/test_funnel_offline.py`. They cover: the staged rewrite round trip; that NoUI files survive staging byte for byte; the browser gate refusing a missing or forged token; the leak check; the drift diff; the workspace `.gitignore` and pre-commit hook (bundles, cookies, `security_params`); that no table row ever holds an expected value; and that no command reads or writes outside `workspaces/<env>/` |

## 19. Build order

0. **Land the base.** This plan is merged into `feat/noui-merge` (PR #29). Merge abcd PR #25 (NoUI into abcd) and noui PR #150 into `dev`. Implementation happens on `feat/adopt-builder`, branched from `feat/noui-merge`.
1. **One CLI.**
   - Scope:
     - package mode, the `abcd` console script and the extras;
     - `cli/core/` (registry, context, output, events, passthrough);
     - the harness, NoUI and Tabby scripts moved behind `abcd` with their old entry points kept;
     - `abcd wdl` passthrough;
     - the `abcd-builder` skills, README and `agents.md` switched over;
     - `tests/cli/`.
   - Done when `pip install .` in a clean venv gives a working `abcd`, every non-WDL command takes `--env` and `--json`, every old `python cli/...` invocation still works, and the docs-command test passes.
2. **Workspace repos and agent bundles.**
   - Scope:
     - `abcd ws init`, `clone`, `status`, `pull`, `push-bundles` and `pull-bundles`, with the workspace `.gitignore`, pre-commit hook, `builder.json` and `ADOPT.md`;
     - agent bundles in `harness/agents/`, pushed and pulled through the process routes in `harness_common/api_client.py`;
     - the `workspace-setup` and `agent-builder` skills.
   - Done when a client workspace cloned on a second machine redeploys its skills and agents from the repo and a PAT alone, and `abcd ws pull` round-trips an existing client's live objects.
3. **Versions and releases.**
   - Scope:
     - `abcd builder setup` and the tables;
     - shadow staged objects, `push --staged` with the lock, `stage`, `release`, `rollback` and `audit drift`;
     - the browser-skill gate on every push, and the default-tier rules;
     - the `release-manager` skill.
   - Done when a skill change goes draft, staged, live and back, with tags on the workspace repo and version rows, and a browser skill with a stale approval or no approval token is refused.
4. **The cloud runner.**
   - Scope:
     - `harness-skills/abcd/` and `abcd cloud bootstrap`;
     - a Builder home org for the first client, with its vault secrets and `abcd-<env>` workstream;
     - the `abcd-runner` and `abcd-nightly` processes, and the nightly drift audit.
   - Done when an FDE runs steps 2 to 10 of the loop from a harness chat with no local machine, and the nightly run reports drift for a workspace with no FDE online.
5. **Datasets, metrics, blind evals and the gate.**
   - Scope:
     - dataset CRUD, checks, metrics and criteria;
     - the eval runner with blind mode;
     - context gaps, the SME workstream and Context agent, and instructions with the leak check;
     - the gate and the eval report;
     - the `dataset-builder`, `eval-runner` and `context-gaps` skills.
   - Done when abcd refuses to release a linked agent without a passing report, locally and in the cloud.
6. **Context, status and training.**
   - Scope:
     - the Context curator and the Status agent;
     - `abcd status publish` and `abcd-weekly`, and the client status workstreams;
     - `explain` and `train`;
     - wiring NoUI output into the KG and draft `goal_reached` checks.

The NoUI and Tabby scenario isn't a build step anymore. It ships with the base; step 1 puts it behind `abcd noui` and `abcd tabby`, and steps 2 and 3 wire it into agent bundles and the gate.

## 20. Open questions

1. **One workstream per case:** work-product pairs get a workstream each, while labelled and read-your-writes datasets share one. Is the `kind = dataset` tag enough at hundreds of workstreams?
2. **Test systems for read-your-writes:** which agents have a test tenant to write to? The CRM agent in the first pilot does. Agents without one can only be evaluated on refusals and dry runs.
3. **Synthetic realism:** how much may a release rely on synthetic cases? Should known cases always have their own target?
4. **FDEs as org admins:** this is needed for every write route. Is it acceptable, given the drift audit?
5. **Loosening criteria:** should every lowered target or edited expected output need a lead's recorded approval?
6. **Drift response:** revert automatically, or open an item for the owner? The plan currently opens a `drift/<date>` branch.
7. **Client logins:** Operator role plus `studio.agents_enduser` per client org, or only the Friday email at first?
8. **Call context source:** meeting-recorder import, uploaded transcripts, or both?
9. **Onshore and offshore role:** where is it recorded per client user, a custom property or a table?
10. **Eval cost:** should wording-only changes run a sample of cases?
11. **Shared skills:** ship to every dependent agent at once, or copy shared skills per agent?
12. **Blind reproduction target:** is 100% of active cases required before go-live, or a target such as 95% with each remaining miss waived and explained?
13. **Questions in blind runs:** count a question from the agent as a context gap (current plan), or let a scripted SME answer it and count it as a HITL?
14. **SME time:** how many gap questions a week can an SME answer? Should they be batched?
15. **Verify at runtime:** decode a real PAT JWT to confirm it carries `roles` (Admin) and `email`.
16. **The Builder home org:** the plan uses one Adopt-owned home org per client, so a prompt injection in one client's material can't reach another client's PAT. Is the per-org overhead (entitlements, vault, one cron pipeline per client) acceptable? Or should one shared home org be used, with the cross-client risk accepted in writing? Who administers the vaults?
17. **The PAT behind cloud runs:** the FDE's own PAT, or a per-client service Admin user (e.g. `builder@adopt.ai` in each client org) so cloud activity isn't attributed to one FDE?
18. **Sandbox egress and tools:** confirm the harness sandbox can reach `github.com`, `api.github.com` and the webui API from the home org, and that `git` is in the image (`gh` isn't assumed). The sandbox-secrets module says it's used with a GitHub PAT, but this plan hasn't run it.
19. **Local browser-skill approvals:** a replay approved in a local NoUI run has no platform approval token, so it needs `--local-approval`. Is that acceptable for go-live, or must every browser skill be re-approved in a harness chat (where the token exists) before its first release?
20. **NoUI bundles:** the plan keeps capture bundles out of git, in a `noui-bundles` Doc Store, because login and non-browser recordings carry full HARs and cookies. Is a scrubber worth building so they can be committed, and do any client data terms forbid even the Doc Store copy?
21. **Repo hosting:** one GitHub org for all client workspace repos, or the client's own GitHub when they ask for it? abcd only needs a remote URL and a token that can push.
22. **Default-tier dependencies:** should an agent be allowed to use a default-tier skill at all? Today the nightly blind gate catches changes after the fact, and the alternative is copying the skill into the workspace as an org skill.
23. **The installed package name:** abcd installs its code as a top-level `cli` package, which is a generic name that could clash with another package. Rename it (e.g. to `abcd_cli`) now, at the cost of touching every import, or later once the CLI has settled?
