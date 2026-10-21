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
| R7 | Native datasets: input/output work-product pairs, one workstream per pair | Datasets have a kind. For work-product pairs, each pair gets a workstream tagged `custom_properties.kind = dataset`. Expectations live in a separate store, indexed in tables (section 8) | setup |
| R7a | Flexible evals for agents with no work-product pairs (e.g. the CRM agent, email sorting) | Four dataset kinds share one case shape: `work_product`, `labeled` (known or synthetic, mappings known up front), `read_your_writes` (probe the target system after the write), and `custom` (a scorer script in the repo) | abcd |
| R8 | Evals against datasets; a release is gated when the agent is linked to a dataset | `abcd eval run` runs each case the way its kind says (per case, batch, or sequential with a probe) and scores it. `abcd release` is the gate (section 8) | abcd |
| R9 | An eval report on every release | Written to Builder: operations, copied to the client status workstream, attached to the GitHub release | abcd |
| R10 | The FDE defines metrics, ground truth, targets and release criteria per agent | `bld_metrics`, `bld_checks`, `bld_release_criteria` tables, plus `abcd metric`, `check` and `criteria` commands | abcd |
| R11 | The FDE defines and approves the skills, plugins and agents to build | `bld_build_plan` with `abcd plan propose` and `approve`. Nothing is built until it's approved | abcd |
| R12 | A knowledge graph in S3, powered by a meta workstream, its Doc Store and its Data Store | The Builder: context workstream. Pages go in its Doc Store (S3); nodes and edges go in two tables (section 10) | setup |
| R13 | Call context and process documentation as inputs | `context/calls`, `context/emails`, `context/process-docs` in Builder: context. The Context curator extracts decisions and checks from them | setup |
| R14 | The loop: build, evals, abcd review, harness run and logs, perf review, evals again, iterate, staged, test staged, publish | Ten loop steps, each an abcd command (section 6) | abcd |
| R15 | NoUI and Tabby scenarios handled by the loop | `abcd scenario noui <app>`: Tabby session, recording, NoUI extraction, draft skill, wiring the agent | abcd |
| R16 | Admins see everything each FDE does every day | Every abcd command logs to `bld_dev_events`; Claude Code hooks add transcripts; a daily report goes out by Slack or email | abcd |
| R17 | Someone like Nicklas logs in and sees full metric status and what's pending on an agent's design | A Builder status workstream per client, with a read grant, in the end-user Agents cockpit, plus a Status agent and a Friday email (section 13) | setup |
| R18 | FDEs can always understand the current implementation | `abcd explain` generates an explainer HTML per agent on every release; this repo's spec HTML covers the Builder itself | abcd |
| R19 | Each agent ships training material: simple prompts and scenarios | `abcd train` generates quickstart, prompts, scenarios, FAQ and what's new on every release | abcd |
| R20 | The Builder works with minimal FDE guidance | The five inputs plus approval gates. The Builder asks only what the inputs don't settle, and every answer is saved as a decision | abcd |
| R21 | **During the build phase, the FDE and a customer-appointed SME keep adding missing context and repro explanations until blind evals show that generic prompts, the knowledge graph's instructions and the skills reproduce the golden dataset on their own.** Every agent linked to a dataset must meet this before going live | Context gaps opened automatically from blind-eval misses; an SME workstream and Context agent for answering; generic instruction pages with a leak check, compiled into an instruction skill; blind reproduction rate as a mandatory release criterion (section 9) | abcd |

## 2. What the FDE does

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
| 6 | Run blind evals on the platform (generic prompt, compiled instructions and skills only) | `abcd eval run --blind` | `POST /turns` per case or batch, outputs download, probe turns | Platform |
| 7 | Close context gaps with the FDE and SME until blind evals pass | `abcd gap list, ask, answer, encode, waive`, `abcd instructions compile` | Doc Store (SME workstream), org-db rows | Subscription |
| 8 | Freeze as staged | `abcd stage` | org-db rows | n/a |
| 9 | Test the staged agent, blind | `abcd test-staged` | skills read, `POST /turns` | Platform |
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
| `builder-sme-<client>` | One per client with an appointed SME | Gap questions with evidence (`gaps/<gap_id>.md`), answers, repro notes, uploads | Customer SME (write); FDEs; the Context agent |
| `builder-status-<client>` | One per client | Scorecard, agent status pages, eval reports, training kit, as files | Status reporter (write); client stakeholders (read) |
| `<client>-staging` | One per client | A copy of selected client files; staged agents run here | FDEs only |
| `ds:<dataset>[:<case>]` | One per case (work-product pairs) or one per dataset (labelled, read-your-writes) | Case inputs only; `custom_properties.kind = dataset`. For read-your-writes, its secrets point at the test system | FDEs only |

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
| Datasets | `bld_datasets` | dataset_key | Dataset index: client, kind, layout, run mode, test environment and probe agent (read-your-writes), default scorers, current version |
| Datasets | `bld_dataset_versions` | dataset_version_key | One row per version: case count, change note, manifest path |
| Datasets | `bld_dataset_cases` | case_key | Case of any kind: workstream, inputs, setup, expected (files, labels or state), teardown, scorers, source (real, known, synthetic, production), tags, active versions |
| Quality | `bld_checks` | check_key | Client checks: deterministic rule or judged rubric, severity, source decision |
| Quality | `bld_metrics` | metric_key | Metric definitions: category, unit, formula, source, baseline, gate flag |
| Quality | `bld_eval_runs` | eval_run_id | One row per eval run: version, dataset version, tier, blind flag, cost, report path |
| Quality | `bld_eval_results` | result_key | Per case per run: observed (outputs, labels or rows read back), passed, score, failed checks, miss cause, steps, tokens, duration, HITL count |
| Quality | `bld_check_results` | check_result_key | Per check per subject, in evals and in production (written by the maker-checker step) |
| Quality | `bld_metric_values` | value_key | Computed values per run, phase and week |
| Context | `bld_kg_nodes` | node_key | Knowledge graph nodes: type, name, page path, proposed or vetted |
| Context | `bld_kg_edges` | edge_key | Typed edges: uses, calls, owns, replaced_by, failed_on, tested_by |
| Context | `bld_decisions` | decision_key | Every answered question: answer, who, source, status |
| Context | `bld_open_items` | item_key | What's pending on an agent's design and who it waits on |
| Context | `bld_context_gaps` | gap_key | A case a blind eval couldn't reproduce because context was missing: hypothesis, question, evidence path, owner (FDE or SME), answer, instruction, status (open, asked, answered, encoded, verified, waived), the verifying run |
| Context | `bld_case_notes` | note_key | How the SME or FDE produced the golden output for one case. Never shown to a blind run |
| Context | `bld_instructions` | instruction_key | Generic instructions: scope (org, client, agent), page path, the gaps each closes, provenance, leak check, status, the instruction-skill version it was compiled into |
| Context | `bld_generic_prompts` | prompt_key | The generic prompts blind evals use, per agent |
| Context | `bld_training_assets` | asset_key | Explainer and training files per live version |
| Activity | `bld_dev_sessions` | session_id | Coding-agent sessions per FDE |
| Activity | `bld_dev_events` | event_id | Every abcd command and loop step, with result and cost |

Column-level detail is in the HTML's Data tables page.

## 8. Datasets, metrics, checks and the gate

**Datasets**

A dataset is how an FDE says what "correct" means for an agent. Not every agent produces a document to compare against, so every dataset has a **kind**. All kinds share one case shape:
- `inputs`
- optional `setup`
- `expected`
- optional `teardown`
- `scorers`
- `source` (real, known, synthetic, production)
- `tags`

Adding a new kind means a new runner and scorer in abcd. It never needs a new table or a platform change.

| Kind | Example agent | A case holds | Workstreams | How it runs | Scored by |
|---|---|---|---|---|---|
| `work_product` | GPW return prep (1120-PC) | Input files plus the expected output files | One per case | One turn per case | `field_match`, `rubric`, client checks |
| `labeled` | GPWA email sorting | One input item (an email with attachments) plus its expected labels: client workstream, document types, page splits | One shared dataset workstream (inbox folder) | One batch turn, or one turn per case | `label_match` (confusion matrix per client), `boundary_match` |
| `read_your_writes` | GPWA CRM updates (Dynamics 365) | An instruction that should cause a write, a setup snapshot, the expected state and outcome code, and a teardown | One shared workstream whose secrets point at the test system | Sequential: probe snapshot, agent turn, probe read-back, teardown | `outcome_match`, `state_match`, `collateral` |
| `custom` | Anything else (SMB drops, API side effects, property checks) | Any inputs and expectation | Either | Any | `script:<name>`, i.e. `evals/scorers/<name>.py` |

**Labelled cases (email sorting)**
- Cases are **known** (historical emails whose routing a person confirmed) or **synthetic**. Synthetic cases are generated by `abcd dataset synth` in the FDE's coding agent from templates and the known client list, so every mapping is known up front.
- Known and synthetic cases are tagged separately, so release criteria can hold known cases to their own target.
- Boundary-detection HITLs stay blocking. The eval counts them; it doesn't tune them away.

**Read your writes (CRM)**
- The output that matters is what the CRM holds after the write.
- **The probe:** a small read-only agent, `gpwa-crm-probe`. It has one SQL skill using the `CRMReaderRole` login, and it runs through `POST /turns` on the same workstream. It runs there because the CRM is reachable only from the harness sandbox over the tunnel.
- **Each case runs in order:**
  1. The probe snapshots the target record.
  2. The agent turn performs the write.
  3. The probe reads the record back.
  4. The teardown restores the snapshot through the same WinRM executables. There are no direct database writes, ever.
- **Scoring:**
  - `outcome_match` checks the result code class: 0 updated, 1 updated with an overwrite warning, below 0 refused (for example -1 no tax return, -2 invalid year).
  - `state_match` checks the fields read back.
  - `collateral` checks that other rows in scope are unchanged.
- **Safety:** the dataset declares `target_env` (`D365TEST_MSCRM`). Before the first case, abcd asks the probe which server and database it's connected to, and refuses if the answer isn't the declared one.

**Why expectations live in a separate store:** the harness copies every store linked to a workstream into the sandbox. If expected outputs or labels sat in the dataset workstream, the agent under test could read the answers. They live in `ds-<dataset>`, which is linked only to `builder-ops`.

| Operation | abcd | Platform routes underneath | Effect on the dataset version |
|---|---|---|---|
| Create | `abcd dataset create --kind ... [--target-env --probe]` | Store resolve, workstream create (shared layouts), assign the staged agent and probe, rows | v1 |
| Add a case | `abcd dataset add-case` (files, `--label`, or `--expect-state`) | upload-urls + confirm, workstream create (per-case layout), rows | New version |
| Generate synthetic cases | `abcd dataset synth --from templates --count N` | upload-urls + confirm, rows | New version |
| List and search | `abcd dataset show --source --tag` | org-db rows and SQL | None |
| Correct an expectation | `abcd dataset update-case` | Upload to a new path, PATCH the row; needs a lead's approval if it loosens a gate | New version |
| Retire a case | `abcd dataset retire-case` | PATCH the row (`retired_in_version`); files kept | New version |
| Archive | `abcd dataset archive` | Refused while any criteria reference it; otherwise soft-delete the dataset workstreams | Frozen |

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

## 9. Context gaps and blind evals

**Requirement (R21):** an agent is ready when it reproduces the golden dataset **blind**. The FDE and a customer-appointed SME keep adding missing context during the build phase until that's true. It's mandatory for every agent linked to a dataset.

**What a blind run gets, and what it doesn't**

| Given | Withheld |
|---|---|
| A generic prompt from `bld_generic_prompts` (e.g. "Prepare the 1120-PC for this client from the files in this workstream") | Expected outputs, labels and states |
| The case inputs, exactly as a client would provide them | Case notes (`bld_case_notes`) |
| The compiled `<agent>-instructions` skill and the agent's other skills | Gap files and their evidence |
| Client-scope instructions synced into the case workstream | Any FDE message, hint or follow-up turn |
| | Answers to questions the agent asks: a question in a blind run is itself a context gap |

**The gap loop**
1. **Blind run:** `abcd eval run --blind` scores every case against the golden dataset.
2. **Classify misses:** `builder-gaps` sorts each miss into missing context, skill bug, rounding, playbook difference or real error. Only missing context opens a gap; the rest become build plan items.
3. **Open a gap:** a `bld_context_gaps` row, plus `gaps/<gap_id>.md` in `builder-sme-<client>`. The file holds the input excerpt, the agent's output against the golden output, the agent's reasoning, and the Builder's hypothesis of what's missing.
4. **Route it:** the FDE answers it, or runs `abcd gap ask` to send it to the SME. The SME sees it in their workstream and by email.
5. **Answer:** the SME works through the gap with the Context agent, which asks follow-ups until the answer is a reusable rule. Explanations specific to one case are saved as repro notes.
6. **Encode:** `builder-instructions` turns the answer into a generic page under `kg/instructions/<agent>/`, with its provenance. The FDE or SME vets it.
7. **Leak check:** abcd refuses any instruction that names a case id, quotes a value from any expected output, or only makes sense for one case.
8. **Compile and re-run:** `abcd instructions compile` builds the instruction skill. Then the affected cases re-run blind, followed by the full set.
9. **Verify:** a gap closes only when a blind run reproduces its case without regressing any other case. Otherwise it reopens with the new evidence.

**Gap states:** open, asked, answered, encoded, verified, waived. A waived gap needs a lead's recorded reason and is counted on the scorecard.

**How instructions reach the agent, with no platform change**
- **Org and agent scope:** compiled into the `<agent>-instructions` skill (reference files plus an index in SKILL.md). It's versioned, staged and released like any other skill.
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

## 10. Knowledge graph and context

- **Pages** are markdown in `builder-context/kg/`, with `index.md`, `schema.md` and an append-only `log.md`.
- **The graph** is `bld_kg_nodes` and `bld_kg_edges`. It answers "what does this change affect?" before a push.
- **Trust:** agents propose and FDEs vet. Proposed pages live in `proposed/`.
- **Coming in:** call transcripts, emails and SOPs uploaded to `context/`, NoUI extraction writing `kg/apis/`, releases updating `kg/agents/`.
- **Context curator:** a process on `builder-context`, run by a nightly cron pipeline. It uses `ws_*` tools and `db_insert_rows`.
- **Going out:** `abcd context pull <agent>` downloads the agent's page, its neighbours one or two edges away, its decisions, open items, checks, metrics and criteria into `./context`, which is git-ignored.

## 11. Meta skills and meta agents

**The adopt-builder plugin (this repo)** has 19 meta skills, installed into the FDE's coding agent by `abcd init`:
`builder-onboard`, `builder-context`, `builder-plan`, `builder-process-from-sop`, `builder-skill-author`, `builder-checks`, `builder-dataset`, `builder-metrics`, `builder-eval`, `builder-review`, `builder-harness-run`, `builder-perf`, `builder-release`, `builder-noui-scenario`, `builder-kg`, `builder-gaps`, `builder-instructions`, `builder-explain`, `builder-train`.

**Meta agents:**

| Agent | Runs as | Trigger | Writes |
|---|---|---|---|
| Context curator | Process on `builder-context` | Nightly cron pipeline | `proposed/`, `bld_decisions`, `bld_open_items` |
| Status reporter | Process on `builder-ops`, with write access to every `builder-status-<client>` | Daily and Friday cron pipelines | Status files; sends the daily FDE report and Friday scorecard by Slack or Gmail |
| Status agent | Process on `builder-status-<client>` | A client asks | Nothing (read only) |
| Context agent | Process on `builder-sme-<client>` | The SME opens a gap or uploads something | `bld_context_gaps` (answered), `bld_case_notes` |
| Maker-checker step | A step inside each live agent | Every production run | `bld_check_results` |
| Eval runner | `abcd eval run` | On demand | Eval tables and `evals/` |
| Explainer and trainer | `abcd explain`, `abcd train` | `abcd release` | `agents/<agent>/`, `bld_training_assets` |

Cron pipelines are created once from a browser session, because a pipeline created with a PAT records the token id as its owner.

## 12. Roles and access

| Role | Platform setup | Can do |
|---|---|---|
| FDE | Admin user with a PAT (every write route needs Admin) | Everything in the loop. Lowering a target or editing an expected output needs a lead's recorded approval. Sees only their own activity in reports |
| FDE lead | Admin user | Forces a release with a reason, approves loosened criteria, gets every daily report |
| Builder agents | Processes | Propose only: pages to `proposed/`, candidate checks and decisions |
| Client stakeholder (e.g. Nicklas) | Operator user, read grant on `builder-status-<client>` only, `studio.agents_enduser` on for the org | Sees the scorecard, agent status, releases, eval reports and pending items; asks the Status agent; gets the Friday email |
| Customer SME (e.g. Amanda), appointed by the customer | Operator user, write grant on `builder-sme-<client>`, read grant on `builder-status-<client>` | Answers context gaps with the Context agent, adds repro notes, sends checks, vets the generic instructions drawn from their answers |

## 13. Status for clients and leads

**What Nicklas sees in `builder-status-gpw`, in the end-user Agents cockpit:**
- `scorecard.md`, in the format the client agreed. It's rewritten daily and frozen every Friday as `scorecards/<date>.html`.
- For each agent, `status.md`: live and staged version, criteria progress, and open items, each marked "waiting on GPW" or "with Adopt" with a due date.
- Every release's eval report and training kit.

He can also ask the Status agent questions such as "what is pending on the return prep agent?" The Friday scorecard also arrives by email.

**What leads get:**
- The same files for every client.
- Every FDE's daily report: agents touched, time per loop step, releases and their scores, platform spend, and where they got stuck.

## 14. Explainers and training

On every release:
- `abcd explain` regenerates `agents/<agent>/explainer.html`: an interactive page for the agent, covering steps, skills, versions, dataset, criteria, checks, last eval, open items and scenarios.
- `abcd train` regenerates `training/`: quickstart, simple prompts, scenarios, FAQ (from real questions in runs and HITL) and what's new.

Both run in the FDE's coding agent. Prompts in the kit are run on a dataset case during `abcd test-staged`, and only the ones that work ship. A kit that fails to generate is flagged on the release; the release itself isn't blocked.

## 15. Build order (all in abcd)

1. **Setup and versions.**
   - Scope: `abcd builder setup`, the tables, shadow staged objects, `push --staged`, `stage`, `release`, `rollback`, `audit drift`.
   - Done when a skill change goes draft, staged, live and back, with git tags and version rows.
2. **Headless loop.**
   - Scope: the ADOPT.md template, `abcd init`, the adopt-builder plugin, dev-event logging.
   - Done when a new FDE goes from a PAT and ADOPT.md to a live skill without opening the Adopt UI.
3. **Datasets, metrics, blind evals and the gate.**
   - Scope: dataset CRUD, checks, metrics, criteria, the eval runner with blind mode, context gaps, the SME workstream and Context agent, instructions with the leak check, the gate and the eval report.
   - Done when abcd refuses to release a linked agent without a passing report.
4. **Context, status and training.**
   - Scope: the Context curator, Status reporter and Status agent on cron pipelines, client status workstreams, `explain` and `train`, and the NoUI and Tabby scenario.

## 16. Open questions

1. **One workstream per case:** work-product pairs get a workstream each, while labelled and read-your-writes datasets share one. Is the `kind = dataset` tag enough at hundreds of workstreams?
1. **Test systems for read-your-writes:** which agents have a test tenant to write to? The GPWA CRM does (`D365TEST_MSCRM`). Agents without one can only be evaluated on refusals and dry runs.
1. **Synthetic realism:** how much may a release rely on synthetic cases? Should known cases always have their own target?
2. **FDEs as org admins:** this is needed for every write route. Is it acceptable, given the drift audit?
3. **Loosening criteria:** should every lowered target or edited expected output need a lead's recorded approval?
4. **Drift response:** revert automatically, or open an item for the owner?
5. **Client logins:** Operator role plus `studio.agents_enduser` per client org, or only the Friday email at first?
6. **Call context source:** meeting-recorder import, uploaded transcripts, or both?
7. **Onshore and offshore role:** where is it recorded per client user, a custom property or a table?
8. **Eval cost:** should wording-only changes run a sample of cases?
9. **Shared skills:** ship to every dependent agent at once, or copy shared skills per agent?
10. **Blind reproduction target:** is 100% of active cases required before go-live, or a target such as 95% with each remaining miss waived and explained?
11. **Questions in blind runs:** count a question from the agent as a context gap (current plan), or let a scripted SME answer it and count it as a HITL?
12. **SME time:** how many gap questions a week can an SME answer? Should they be batched?
13. **Verify at runtime:** decode a real PAT JWT to confirm it carries `roles` (Admin) and `email`.
