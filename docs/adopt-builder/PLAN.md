# Adopt Builder: plan

**Status:** draft for review, 2026-10-20
**Companion:** [`adopt_builder_spec.html`](adopt_builder_spec.html) is an interactive explainer of this plan for FDEs. Open it in a browser.
**Checked against:** adoptai-workflows `29a3fe717`, adoptwebui `88416ad0c`, abcd `b41dd25`.

Every Adopt agent gets built through one headless Builder. FDEs drive it from their own coding agent (Claude Code, Codex, Cursor) on their own subscription. Every skill and agent change becomes a versioned, staged release that has to pass its dataset gate, and each release carries its eval report.

**Goal number one: no new platform features.** The Builder consists of:
- abcd (this repo),
- a one-time setup of existing platform objects (workstreams, Doc Stores, Data Store tables, processes, cron pipelines, grants),
- a private git repo.

Where the platform can't do something, this plan says so and gives the workaround (section 4). Nothing in this plan waits on a platform release.

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
| R3 | FDEs use their own coding agent, on subscription rather than API tokens | Authoring, local evals, review, perf analysis, judged-check scoring, explainers and training run in the FDE's coding agent. Platform tokens are spent only on harness turns | abcd |
| R4 | Onboarding is "take a PAT, download an MD file" | Mint a PAT in the UI (exists), clone the agents repo, then "follow ADOPT.md". `abcd init` does the rest | abcd |
| R5 | Bring your own private git repo for agent versions | One private GitHub repo per org or client. abcd uses `git` and `gh` locally; the platform never talks to GitHub | setup |
| R6 | Version control for skills and agents, testing a staged version, then going live | Git is the version store. Shadow staged objects on the platform. Release copies staged onto live (section 5) | workaround |
| R7 | Native datasets: input/output work-product pairs, one workstream per pair | Pair workstreams tagged `custom_properties.kind = dataset`. Expected outputs live in a separate store. Indexed in tables (section 7) | setup |
| R8 | Evals against datasets; a release is gated when the agent is linked to a dataset | `abcd eval run` (one turn per pair, scored by abcd). `abcd release` is the gate (section 8) | abcd |
| R9 | An eval report on every release | Written to Builder: operations, copied to the client status workstream, attached to the GitHub release | abcd |
| R10 | The FDE defines metrics, ground truth, targets and release criteria per agent | `bld_metrics`, `bld_checks`, `bld_release_criteria` tables, plus `abcd metric`, `check` and `criteria` commands | abcd |
| R11 | The FDE defines and approves the skills, plugins and agents to build | `bld_build_plan` with `abcd plan propose` and `approve`. Nothing is built until it's approved | abcd |
| R12 | A knowledge graph in S3, powered by a meta workstream, its Doc Store and its Data Store | The Builder: context workstream. Pages go in its Doc Store (S3); nodes and edges go in two tables (section 9) | setup |
| R13 | Call context and process documentation as inputs | `context/calls`, `context/emails`, `context/process-docs` in Builder: context. The Context curator extracts decisions and checks from them | setup |
| R14 | The loop: build, evals, abcd review, harness run and logs, perf review, evals again, iterate, staged, test staged, publish | Ten loop steps, each an abcd command (section 6) | abcd |
| R15 | NoUI and Tabby scenarios handled by the loop | `abcd scenario noui <app>`: Tabby session, recording, NoUI extraction, draft skill, wiring the agent | abcd |
| R16 | Admins see everything each FDE does every day | Every abcd command logs to `bld_dev_events`; Claude Code hooks add transcripts; a daily report goes out by Slack or email | abcd |
| R17 | Someone like Nicklas logs in and sees full metric status and what's pending on an agent's design | A Builder status workstream per client, with a read grant, in the end-user Agents cockpit, plus a Status agent and a Friday email (section 11) | setup |
| R18 | FDEs can always understand the current implementation | `abcd explain` generates an explainer HTML per agent on every release; this repo's spec HTML covers the Builder itself | abcd |
| R19 | Each agent ships training material: simple prompts and scenarios | `abcd train` generates quickstart, prompts, scenarios, FAQ and what's new on every release | abcd |
| R20 | The Builder works with minimal FDE guidance | The five inputs plus approval gates. The Builder asks only what the inputs don't settle, and every answer is saved as a decision | abcd |

## 2. What the FDE does

The FDE defines **five inputs**:
1. metrics,
2. ground truth (work-product pairs plus the client's checks),
3. the knowledge graph,
4. call context,
5. process documentation.

The FDE also owns four approvals:
1. **The build plan:** which skills, plugins and agents to build or change.
2. **Release criteria for each agent:** the dataset, the gate metrics and their targets for the next release.
3. **Answers** to questions the inputs don't settle. Each answer becomes a decision.
4. **Staged to live,** once the release criteria pass.

Everything else in the loop, the Builder runs itself.

## 3. Platform facts this plan relies on

All of these were verified in code. Paths are relative to `adoptwebui/backend/app` unless marked `W/` (adoptai-workflows `src/workflows`).

**Org skills**
- Upload: `POST /v1/end-user/agent-harness/skills`, Admin only (`resolve_org_admin`, `dependencies/harness_caller.py:184`).
- Name rule: `^[a-z0-9][a-z0-9-]*$`, so `<name>--staged` is valid. Pass `replace=true` to overwrite.
- The catalog keys a skill by its **frontmatter name** (`W/agent_harness/skills.py:328`), so a staged copy has to rewrite `name:`.

**Processes (agents)**
- Routes: `/v1/org/processes`. Create, patch, delete, assign to workstreams and run are all Admin only (`routes/process.py:302-428`).
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

**Data Store**
- Routes: `/v1/org-db/*`. Any JWT with `tenantId` and `sub` can call them.
- Inserts take at most 500 rows, aren't atomic, and have no upsert.
- No per-user row security, and read-only SQL covers the whole org.
- Agents get `db_query` and `db_insert_rows` when their skill sets `datastore_write: true`. They can't update rows.

**Scheduling, integrations and runs**
- Scheduling: `POST /v1/pipelines` with `schedule_type: cron` and a `process_id`. It runs the process across its assigned workstreams.
- Notifications: agents can use connected Slack or Gmail through `list_integrations` and `run_integration_tool`.
- Run history: `/v1/runs` covers the whole org.

**End-user access**
- An Operator-role user with a read grant can see that workstream's files, and chat with its processes, in the end-user Agents cockpit.
- That surface's entitlement (`studio.agents_enduser`) is set per org by Adopt in intranet.

**Agents can't drive the platform:** an agent can't start other harness runs, patch processes or upload skills. That work is done by abcd.

## 4. Platform limits and workarounds

| Need | Platform today | Workaround | What remains |
|---|---|---|---|
| Version history | Skills and processes are overwritten in place | Git holds every version. `bld_versions` records content hashes and SHAs. Rollback re-pushes a tag | Nothing in practice |
| Staged versions | No staged pointer; a turn takes a `process_id` | Shadow objects: skill `<name>--staged` (frontmatter name rewritten) and process `<agent>-staged`. Promotion re-uploads under the live names and patches the live process | Promotion is a re-upload, not an atomic switch; abcd verifies afterwards |
| Hiding staged agents | An assigned process is visible to every user of that workstream | Assign staged processes only to staging and dataset workstreams, which no client user has a grant on | Admins still see them |
| Per-agent skill pinning | Skills are org-wide by name | A change to a shared skill ships to every agent that uses it, and the gate runs every dependent agent's criteria | One release can hold up several agents |
| A hard release gate | The platform can't refuse a write | `abcd release` is the only sanctioned path. `abcd audit drift` flags any live object that differs from its last release, on every abcd command and nightly | Detects within a day; doesn't prevent |
| Client view with per-user filtering | Data Store has no per-user row security | Clients get a read grant only on their Builder status workstream, which holds files; no table access | Scorecards are files, refreshed daily |
| PAT identity | Some routes record the token id (`sub`) instead of the user | abcd runs everything through `POST /turns`, which records the user. Cron pipelines are created once from a browser session | Pipeline setup is one manual step |
| FDE permissions | Skill, process, workstream and grant writes need Admin | FDEs use Admin PATs in the orgs they build for | FDEs can edit outside the Builder; the drift audit covers that |
| Entitlements | Can't be changed through adoptwebui | Adopt turns on `studio.agents_enduser` for client orgs in intranet | One request per client org |

Unrelated to the Builder but blocking: `/v1/end-user/docstore/*` (`routes/end_user_docstore.py`) has **no authentication**. It takes `org_id` from the request and allows uploads and deletes. This is a security fix, not a feature, and it should be raised with the platform team before Builder data lives in the Doc Store.

## 5. Versions, git and releases

**On the platform, every skill and agent has a live object and a staged shadow:**

| Object | Live | Staged shadow | Assigned to |
|---|---|---|---|
| Skill | `tb-reconcile` | `tb-reconcile--staged`; frontmatter `name:`, `compiled_flow` and `agent_instructions` references rewritten | n/a |
| Agent | `gpw-1120pc-prep` | `gpw-1120pc-prep-staged`, display name "Return prep (staged)"; components point at the `--staged` skills | Live: client workstream. Staged: staging and dataset workstreams only |

**States:**
- **draft:** a commit on your branch, pushed to the shadows.
- **staged:** hashes recorded, and the commit is tagged `<agent>@v<n>-rc`.
- **live:** uploaded under the live names, and the commit is tagged `<agent>@v<n>`.
- **superseded:** its tag is the rollback target.
- **gate failed:** nothing changed under the live names.

**`abcd release` does, in order:**
1. Re-checks that the shadows still match the staged hashes, and runs `abcd audit drift` on the live objects.
2. Checks every release criterion against the staged run on those exact hashes. This covers this agent and every agent that shares a changed skill. It refuses on any failure or any error.
3. Asks the FDE to approve.
4. Uploads each staged bundle under its live name with `replace=true`, with the names rewritten back. Patches the live process. Verifies what landed.
5. Writes the eval report to `releases/<agent>/<version>/` in Builder: operations and copies it to the client's status workstream.
6. Inserts a `bld_releases` row with the previous tag as the rollback target.
7. Runs `git tag <agent>@v<n>` and attaches the report to a GitHub release with `gh release create`.
8. Regenerates the explainer and training kit and commits them.

**Rollback:** `abcd rollback <agent>` re-uploads the previous tag's bundles under the live names, patches the live process, verifies, and records the rollback.

**Git repo layout (private, one per org or client):**

```
ADOPT.md                          manifest the coding agent follows
.adopt/project.json               org, environment, agents
agents/<agent>/agent.yaml         process: steps, HITL gates, components, instructions
agents/<agent>/release.yaml       release criteria: dataset, gate metrics, targets
agents/<agent>/metrics.yaml       metric definitions
agents/<agent>/docs/explainer.html
agents/<agent>/training/          quickstart.md, prompts.md, scenarios.md, faq.md, whats-new.md
skills/<skill>/                   SKILL.md + scripts + references
plugins/<plugin>/
checks/<client>/<check>.yaml      client checks as code
datasets/<dataset>/manifest.json  manifest only; files stay in the Doc Store
evals/reports/<agent>/<version>.html
```

## 6. The builder loop

| # | Step | abcd | Platform routes | Runs on |
|---|---|---|---|---|
| 1 | Build or refine from the context bundle | `abcd context pull`, `abcd plan show` | Doc Store downloads, org-db rows | Subscription |
| 2 | Run evals locally | `abcd eval run --tier local` | Dataset input downloads | Subscription |
| 3 | Review with abcd | `abcd review` (adopt-skill-review) | none | Subscription |
| 4 | Run on the harness, get logs | `abcd push --staged`, `abcd run --staged` | skills upload, process patch, `POST /turns`, trace | Platform |
| 5 | Performance review and optimise | `abcd trace review` | `/v1/runs` trace | Subscription |
| 6 | Run evals again on the platform | `abcd eval run --tier platform` | `POST /turns` per pair, outputs download | Platform |
| 7 | Iterate until they pass | `abcd status`, `abcd decide` | org-db rows | Subscription |
| 8 | Freeze as staged | `abcd stage` | org-db rows | n/a |
| 9 | Test the staged agent | `abcd test-staged` | skills read, `POST /turns` | Platform |
| 10 | Publish the new version | `abcd release` | skills upload, process patch, Doc Store upload | Platform |

**What a step reuses from today:**
- `abcd review` is `harness_skill.py audit`.
- `abcd push` extends `harness_skill.py push` and `verify`.
- `abcd run` is `harness_run.py`, which already takes `--process`.
- `abcd trace` is `harness_trace.py`.
- Agent CRUD is `harness_process.py`.
- Run history is `harness_runs.py`.
- Test workstreams are `harness_workstream.py`.

## 7. Workstreams, Doc Stores and tables

**Workstreams, created by `abcd builder setup`:**

| Workstream | How many | Holds | Who has access |
|---|---|---|---|
| `builder-context` | One per org | Knowledge graph, call context, process docs, decisions, agent design pages, explainers, training | FDEs (write) |
| `builder-ops` | One per org | Versions, releases, eval runs and reports, metrics, FDE activity, dataset expected-output stores | FDEs (write); the Status reporter process |
| `builder-status-<client>` | One per client | Scorecard, agent status pages, eval reports, training kit, as files | Status reporter (write); client stakeholders (read) |
| `<client>-staging` | One per client | A copy of selected client files; staged agents run here | FDEs only |
| `ds:<dataset>:<pair>` | One per pair | That pair's inputs only; `custom_properties.kind = dataset` | FDEs only |

**Doc Store layouts:**

```
builder-context/
  kg/index.md  kg/schema.md  kg/log.md
  kg/clients/<client>.md  kg/systems/<system>.md  kg/apis/<system>/<api>.md
  kg/skills/<skill>.md  kg/agents/<agent>.md  kg/datasets/<dataset>.md  kg/incidents/<date>-<slug>.md
  context/calls/<client>/<date>-<slug>.md
  context/emails/<client>/<date>-<slug>.md
  context/process-docs/<client>/<process>/sop.md
  decisions/<client>/<decision_id>.md
  agents/<agent>/design.md  agents/<agent>/explainer.html  agents/<agent>/training/*.md
  proposed/                                  pages and facts waiting for review

builder-ops/
  releases/<agent>/<version>/{manifest.json, eval_report.html, eval_report.json, diff.md, notes.md}
  evals/<eval_run_id>/{summary.json, pairs/<pair_id>/{checks.json, trace.ndjson}}
  sessions/<fde>/<date>/<session_id>/{transcript.jsonl, summary.md}
  reports/daily/<date>/<fde>.md

ds-<dataset>/ (store linked to builder-ops only)
  manifests/v<n>.json  expected/<pair_id>/...

ds:<dataset>:<pair_id>/ (pair workstream)
  inputs/...  outputs/<eval_run_id>/...

builder-status-<client>/
  scorecard.md  scorecards/<date>.html
  agents/<agent>/status.md  agents/<agent>/releases/<version>/eval_report.html  agents/<agent>/training/
```

**Doc Store rules:**
- Never overwrite: a versioned file always gets a new path.
- Tables hold the paths, so nothing ever lists a folder.
- Use markdown for anything an agent reads, and HTML only for things people open.

**Tables.** All are Data Store custom tables, created through `/v1/org-db/tables`.
- Every table has `workstream_scoped = false`, because rows written through the external API get a null `workstream_id`.
- Each table is linked with write access to its Builder workstream.
- Each has one unique key, so abcd retries are idempotent and a 409 means the row was already written.
- Enum values are checked by abcd, because the Data Store doesn't enforce them.

| Group | Table | Unique key | Purpose |
|---|---|---|---|
| Registry | `bld_agents` | agent_key | One row per agent: process ids (live and staged), client, owner, repo path, live and staged version, phase |
| Registry | `bld_build_plan` | item_key | Skills, plugins and agents to build or change; proposed, approved, building, done |
| Versions | `bld_versions` | version_id | Every version: kind, name, content hash, git SHA, state, pinned components, reason |
| Versions | `bld_releases` | release_id | Every release: version, previous version, eval run, gate result, report path, approver, forced reason |
| Versions | `bld_release_criteria` | criteria_key | Per agent: dataset version, metric, comparator, target, decision reference |
| Datasets | `bld_datasets` | dataset_key | Dataset index: client, current version, expected store, default scorer |
| Datasets | `bld_dataset_versions` | dataset_version_key | One row per version: pair count, change note, manifest path |
| Datasets | `bld_dataset_pairs` | pair_key | Pair: workstream, input and expected paths, scorer, tags, active versions |
| Quality | `bld_checks` | check_key | Client checks: deterministic rule or judged rubric, severity, source decision |
| Quality | `bld_metrics` | metric_key | Metric definitions: category, unit, formula, source, baseline, gate flag |
| Quality | `bld_eval_runs` | eval_run_id | One row per eval run: version, dataset version, tier, cost, report path |
| Quality | `bld_eval_results` | result_key | Per pair per run: passed, score, failed checks, miss cause, steps, tokens, duration, HITL count |
| Quality | `bld_check_results` | check_result_key | Per check per subject, in evals and in production (written by the maker-checker step) |
| Quality | `bld_metric_values` | value_key | Computed values per run, phase and week |
| Context | `bld_kg_nodes` | node_key | Knowledge graph nodes: type, name, page path, proposed or vetted |
| Context | `bld_kg_edges` | edge_key | Typed edges: uses, calls, owns, replaced_by, failed_on, tested_by |
| Context | `bld_decisions` | decision_key | Every answered question: answer, who, source, status |
| Context | `bld_open_items` | item_key | What's pending on an agent's design and who it waits on |
| Context | `bld_training_assets` | asset_key | Explainer and training files per live version |
| Activity | `bld_dev_sessions` | session_id | Coding-agent sessions per FDE |
| Activity | `bld_dev_events` | event_id | Every abcd command and loop step, with result and cost |

Column-level detail is in the HTML's Data tables page.

## 8. Datasets, metrics, checks and the gate

**Datasets**

**Why expected outputs live in a separate store:** the harness copies every store linked to a workstream into the sandbox. If the expected outputs sat in the pair's own store, the agent under test could read the answers. They live in `ds-<dataset>`, which is linked only to `builder-ops`.

| Operation | abcd | Platform routes underneath | Effect on the dataset version |
|---|---|---|---|
| Create | `abcd dataset create` | store resolve, org-db rows | v1 |
| Add a pair | `abcd dataset add-pair` | workstream create (kind = dataset), upload-urls + confirm, assign staged process, rows | New version |
| List and search | `abcd dataset show --tag` | org-db rows and SQL | None |
| Correct an expected output | `abcd dataset update-pair` | upload to a new path, PATCH the row; needs a lead's approval if it loosens a gate | New version |
| Retire a pair | `abcd dataset retire-pair` | PATCH the row (`retired_in_version`); files kept | New version |
| Archive | `abcd dataset archive` | Refused while any criteria reference it; otherwise soft-delete the pair workstreams | Frozen |

Two releases are only ever compared on the same dataset version.

**Metrics**
- A metric definition is versioned and has: name, category, unit, formula, source, baseline, a target per phase, a gate flag, and a client sign-off decision.
- Sources are check results, HITL events, user actions by role, run timings and cost.
- `abcd metric compute` calculates values from eval results, check results and `/v1/runs`.

**Rules from the GPW pilot**
- Accuracy means the output passes all of the client's checks. When a check is added, past runs are re-scored.
- Every metric has to be able to fail. First-pass yield is the share of clients that pass every check on the first run with no human correction.
- Human time comes from platform logs, split by onshore and offshore role. Nobody watches screens.
- Misses are broken down by cause: rounding, missing context, playbook difference, real error.
- There is one scorecard, and the eval reports use the same numbers.

**Checks**
- A check is either deterministic (a rule, scored in Python by abcd) or judged (a rubric, scored by the FDE's coding agent).
- Every check runs in evals. In production, it runs as a maker-checker step inside the live agent, which writes `bld_check_results` itself.

**The gate (`abcd release`)**
- Every criterion must pass on a staged run of the exact staged hashes.
- `no_regression` fails if any pair that passed on live now fails without a recorded reason.
- The gate refuses on any error.
- A lead can force a release with a recorded decision. A forced release stays flagged until a passing run catches up.

**The eval report has six sections:**
1. Header
2. Release criteria against live and staged
3. Regressions
4. Misses by cause
5. Cost and performance
6. What changed and why

## 9. Knowledge graph and context

- **Pages** are markdown in `builder-context/kg/`, with `index.md`, `schema.md` and an append-only `log.md`.
- **The graph** is `bld_kg_nodes` and `bld_kg_edges`. It answers "what does this change affect?" before a push.
- **Trust:** agents propose and FDEs vet. Proposed pages live in `proposed/`.
- **Coming in:** call transcripts, emails and SOPs uploaded to `context/`, NoUI extraction writing `kg/apis/`, releases updating `kg/agents/`.
- **Context curator:** a process on `builder-context`, run by a nightly cron pipeline. It uses `ws_*` tools and `db_insert_rows`.
- **Going out:** `abcd context pull <agent>` downloads the agent's page, its neighbours one or two edges away, its decisions, open items, checks, metrics and criteria into `./context`, which is git-ignored.

## 10. Meta skills and meta agents

**The adopt-builder plugin (this repo)** has 17 meta skills, installed into the FDE's coding agent by `abcd init`:
`builder-onboard`, `builder-context`, `builder-plan`, `builder-process-from-sop`, `builder-skill-author`, `builder-checks`, `builder-dataset`, `builder-metrics`, `builder-eval`, `builder-review`, `builder-harness-run`, `builder-perf`, `builder-release`, `builder-noui-scenario`, `builder-kg`, `builder-explain`, `builder-train`.

**Meta agents:**

| Agent | Runs as | Trigger | Writes |
|---|---|---|---|
| Context curator | Process on `builder-context` | Nightly cron pipeline | `proposed/`, `bld_decisions`, `bld_open_items` |
| Status reporter | Process on `builder-ops`, with write access to every `builder-status-<client>` | Daily and Friday cron pipelines | Status files; sends the daily FDE report and Friday scorecard by Slack or Gmail |
| Status agent | Process on `builder-status-<client>` | A client asks | Nothing (read only) |
| Maker-checker step | A step inside each live agent | Every production run | `bld_check_results` |
| Eval runner | `abcd eval run` | On demand | Eval tables and `evals/` |
| Explainer and trainer | `abcd explain`, `abcd train` | `abcd release` | `agents/<agent>/`, `bld_training_assets` |

Cron pipelines are created once from a browser session, because a pipeline created with a PAT records the token id as its owner.

## 11. Roles and access

| Role | Platform setup | Can do |
|---|---|---|
| FDE | Admin user with a PAT (every write route needs Admin) | Everything in the loop. Lowering a target or editing an expected output needs a lead's recorded approval. Sees only their own activity in reports |
| FDE lead | Admin user | Forces a release with a reason, approves loosened criteria, gets every daily report |
| Builder agents | Processes | Propose only: pages to `proposed/`, candidate checks and decisions |
| Client stakeholder (e.g. Nicklas) | Operator user, read grant on `builder-status-<client>` only, `studio.agents_enduser` on for the org | Sees the scorecard, agent status, releases, eval reports and pending items; asks the Status agent; gets the Friday email |
| Client checker (e.g. Amanda) | Same as a stakeholder | Sends checks by email or upload. They land as proposed until an FDE turns them into scorers |

## 12. Status for clients and leads

**What Nicklas sees in `builder-status-gpw`, in the end-user Agents cockpit:**
- `scorecard.md`, in the format the client agreed. It's rewritten daily and frozen every Friday as `scorecards/<date>.html`.
- For each agent, `status.md`: live and staged version, criteria progress, and open items, each marked "waiting on GPW" or "with Adopt" with a due date.
- Every release's eval report and training kit.

He can also ask the Status agent questions such as "what is pending on the return prep agent?" The Friday scorecard also arrives by email.

**What leads get:**
- The same files for every client.
- Every FDE's daily report: agents touched, time per loop step, releases and their scores, platform spend, and where they got stuck.

## 13. Explainers and training

On every release:
- `abcd explain` regenerates `agents/<agent>/explainer.html`: an interactive page for the agent, covering steps, skills, versions, dataset, criteria, checks, last eval, open items and scenarios.
- `abcd train` regenerates `training/`: quickstart, simple prompts, scenarios, FAQ (from real questions in runs and HITL) and what's new.

Both run in the FDE's coding agent. Prompts in the kit are run on a dataset pair during `abcd test-staged`, and only the ones that work ship. A kit that fails to generate is flagged on the release; the release itself isn't blocked.

## 14. Build order (all in abcd)

1. **Setup and versions.**
   - Scope: `abcd builder setup`, the tables, shadow staged objects, `push --staged`, `stage`, `release`, `rollback`, `audit drift`.
   - Done when a skill change goes draft, staged, live and back, with git tags and version rows.
2. **Headless loop.**
   - Scope: the ADOPT.md template, `abcd init`, the adopt-builder plugin, dev-event logging.
   - Done when a new FDE goes from a PAT and ADOPT.md to a live skill without opening the Adopt UI.
3. **Datasets, metrics and the gate.**
   - Scope: dataset CRUD, checks, metrics, criteria, the eval runner, the gate and the eval report.
   - Done when abcd refuses to release a linked agent without a passing report.
4. **Context, status and training.**
   - Scope: the Context curator, Status reporter and Status agent on cron pipelines, client status workstreams, `explain` and `train`, and the NoUI and Tabby scenario.

## 15. Open questions

1. **One workstream per pair:** is the `kind = dataset` tag enough at hundreds of pairs, or should pairs share workstreams by folder?
2. **FDEs as org admins:** this is needed for every write route. Is it acceptable, given the drift audit?
3. **Loosening criteria:** should every lowered target or edited expected output need a lead's recorded approval?
4. **Drift response:** revert automatically, or open an item for the owner?
5. **Client logins:** Operator role plus `studio.agents_enduser` per client org, or only the Friday email at first?
6. **Call context source:** meeting-recorder import, uploaded transcripts, or both?
7. **Onshore and offshore role:** where is it recorded per client user, a custom property or a table?
8. **Eval cost:** should wording-only changes run a sample of pairs?
9. **Shared skills:** ship to every dependent agent at once, or copy shared skills per agent?
10. **Verify at runtime:** decode a real PAT JWT to confirm it carries `roles` (Admin) and `email`.
