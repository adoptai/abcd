# Offline end-to-end tests for the Adopt Builder

**Live staging was NOT run (NOT_RUN).** None of these tests talk to the Adopt platform. They drive
the real `abcd` CLI against `fake_bff.py`, an in-process model of the adoptwebui BFF built from the
route contracts in `BFF_ROUTES.md` (request and response shapes, status codes, auth rules). A green
run proves the CLI and the documented contract agree. It does not prove the real platform behaves
like the document. Every claim about live behaviour stays NOT_RUN until a staging run is done.

## What is here

| File | What it is |
|---|---|
| `fake_bff.py` | Threaded `http.server` on 127.0.0.1 (random port) with in-memory state (`FakeBFFState`) and 101 routes |
| `fake_agent.py` | The scriptable agent behind `POST /turns` (event helpers, scripts, HITL rules) |
| `conftest.py` | Fixtures: `bff`, `acme` workspace, `cli` runner, `fixture_skill`, `fixture_plugin`, `fake_github` |
| `helpers.py` | `require_command("hitl resolve")`, git helpers, NDJSON readers |
| `test_e2e_workspace.py` | (a) workspace flow: `ws create/list/show/use/delete`, `ws init --remote`, `ws status`, branch commit and push, `ws clone` into a second dir |
| `test_e2e_skills.py` | (b) `skill audit/push/verify/delete/deploy-default`, `plugin push`, 409 and replace, default-tier shadowing, secret scan, 415 and truncation, admin checks |
| `test_e2e_agent_run.py` | (c) agent with a skill component, workstream assignment, `run start/continue` streaming, `runs list/detail/trace`, `trace fetch --save`, HITL gate by `<builder_data>`, skill-approval tokens, stop |
| `test_e2e_auth.py` | (d) PAT exchange cached per env, expiry, bad and locked-out PATs, no secrets in output, logs or events |
| `test_e2e_platform_routes.py` | The wave-1 clients (Data Store, Doc Store, write grants, pipelines, vault, escalations) against the fake |
| `test_e2e_wave2_sessions.py` | `session`, `hitl` and `pipeline` commands |
| `test_e2e_wave2_builder.py` | `builder setup`, `dataset`, `criteria`, `push --staged`, `stage`, `test-staged`, `release`, `audit drift`, `eval run --tier local` |
| `test_fake_bff_contract.py`, `test_fake_bff_routes_sweep.py` | The fake itself, over plain HTTP, so it cannot drift from `BFF_ROUTES.md` unnoticed |

All of them carry `@pytest.mark.integration` (registered in `tests/conftest.py`). They are offline
and run by default. Deselect with `-m "not integration"`.

## How the tests run the CLI

- In process: `cli.abcd.main([...])` with `ABCD_WORKSPACES_DIR=<tmp>`. In a subprocess:
  `python -m cli.abcd ...` with a minimal environment (workspace tests and one skill push).
- The `acme` workspace is created by `abcd ws create`; its `.env` holds
  `ADOPT_WEBUI_ENDPOINT=<fake url>` and a fake PAT.
- Git is local only. `fake_github` makes git rewrite `https://github.test/acme/acme.git` to a local
  bare repo (`url.<path>.insteadOf` through `GIT_CONFIG_*`) and puts a fake `gh` on PATH, so
  release's tag, GitHub release and pull-request steps run offline.

## BFF-only proof

The fake logs every request in `state.requests`: method, path, query, status, whether an
`Authorization` header was present, the top-level JSON key names, and whether `X-Workflows-Secret`
was sent. Values are never logged. Anything outside `/v1/` is a 404 that is still logged, so
`bff.forbidden_calls()` lists any workflows route or `X-Workflows-Secret` header the CLI used.
Tests assert it is empty. Presigned storage URLs live under `/_presigned/` and are logged apart
(`state.storage_requests`), like the object store they stand in for.

## The scriptable agent

```python
from tests.integration import fake_agent as fa

# The builder card puts step "approve" in review. `process` is an id or a name; the other
# matchers are message=, workstream=, once=, hold= and failure=.
bff.state.script(
    [
        fa.text("Reading. "),
        fa.tool_use("read_skill_file"),
        fa.artifact("outputs/a.csv", "x\n"),
        fa.builder("bld-1", step_id="approve", title="Approve"),
    ],
    process="triage",
)
```

Events: `text`, `tool_use`, `tool_result`, `artifact` (served by `/outputs/download`), `builder`
(`builder_id`, `step_id`), `question`, `process_state`; the fake then adds `harness_turn_complete`
(or `harness_error` with `failure=`). `hold=True` keeps the turn RUNNING until `/stop`. A
`<builder_data>` user message moves a `review` step to `done` only when its `builder_id` matches;
the agent itself can never set a gated step to `done`. `policy-` builders open an escalation that
`/escalations/{id}/decide` resolves (and answers 409 the second time). Skill approval tokens are
recorded from `user_message` text only.

## Wave-2 scenarios and `require_command`

Scenarios for commands other wave-2 agents build in parallel start with
`require_command("hitl resolve", ...)`. It looks the command up in the real registry
(`cli.abcd.build_registry().resolve(...)`) and skips until it exists. They were written against
`WAVE2_CONTRACT.md` and the PLAN, and checked against the work-in-progress modules in the sibling
clones. Once everything is merged they run with no change.

## Route coverage

101 routes are implemented. Every one is requested at least once by the tests in this directory
(check: `FAKE_BFF_COVERAGE=out.json pytest tests/integration` writes the served routes).
`test_readme_lists_every_implemented_route` keeps this table in step with the code.

### Auth, identity, limits

| Route | Auth |
|---|---|
| `POST /v1/users/api-token` | none |
| `POST /v1/users/validate-token` | none |
| `GET /v1/end-user/agent-harness/me` | jwt |
| `GET /v1/users/organization` | jwt |
| `GET /v1/end-user/agent-harness/limits` | jwt |
| `GET /v1/end-user/agent-harness/models` | jwt |

### Skills and default skills

| Route | Auth |
|---|---|
| `POST /v1/end-user/agent-harness/skills` | admin |
| `GET /v1/end-user/agent-harness/skills` | jwt |
| `GET /v1/end-user/agent-harness/skills/{name}` | jwt |
| `GET /v1/end-user/agent-harness/skills/{name}/files` | jwt |
| `DELETE /v1/end-user/agent-harness/skills/{name}` | admin |
| `POST /v1/end-user/agent-harness/default-skills` | adopt |
| `GET /v1/end-user/agent-harness/default-skills/{name}` | adopt |
| `GET /v1/end-user/agent-harness/plugins/{name}/skills/{slug}/files` | jwt |

### Plugins

| Route | Auth |
|---|---|
| `POST /v1/end-user/agent-harness/plugins` | admin |
| `GET /v1/end-user/agent-harness/plugins` | jwt |
| `GET /v1/end-user/agent-harness/plugins/{name}` | jwt |
| `DELETE /v1/end-user/agent-harness/plugins/{name}` | admin |

### Processes

| Route | Auth |
|---|---|
| `GET /v1/org/processes` | jwt |
| `POST /v1/org/processes` | admin |
| `GET /v1/org/processes/{pid}` | admin |
| `PATCH /v1/org/processes/{pid}` | admin |
| `DELETE /v1/org/processes/{pid}` | admin |
| `GET /v1/org/processes/{pid}/workstreams` | jwt |
| `PUT /v1/org/processes/{pid}/workstreams` | admin |
| `GET /v1/org/processes/{pid}/write-workstreams` | admin |
| `PUT /v1/org/processes/{pid}/write-workstreams` | admin |
| `POST /v1/org/processes/{pid}/run` | admin |
| `POST /v1/processes/{pid}/run` | jwt |
| `GET /v1/processes` | jwt |

### Workstreams

| Route | Auth |
|---|---|
| `GET /v1/org/workstreams` | jwt |
| `POST /v1/org/workstreams` | admin |
| `GET /v1/org/workstreams/{wid}` | jwt |
| `POST /v1/org/workstreams/{wid}/update` | admin |
| `POST /v1/org/workstreams/{wid}/delete` | admin |
| `GET /v1/end-user/agent-harness/workstreams` | jwt |

### Doc Store

| Route | Auth |
|---|---|
| `POST /v1/docstore/stores` | jwt |
| `POST /v1/docstore/stores/resolve` | jwt |
| `GET /v1/docstore/stores` | jwt |
| `GET /v1/docstore/stores/{sid}` | jwt |
| `PUT /v1/docstore/stores/{sid}` | jwt |
| `DELETE /v1/docstore/stores/{sid}` | jwt |
| `POST /v1/docstore/stores/{sid}/workstreams` | jwt |
| `DELETE /v1/docstore/stores/{sid}/workstreams/{wid}` | jwt |
| `POST /v1/docstore/stores/{sid}/upload-urls` | jwt |
| `POST /v1/docstore/stores/{sid}/upload-confirm` | jwt |
| `GET /v1/docstore/stores/{sid}/documents` | jwt |
| `GET /v1/docstore/stores/{sid}/documents/{did}/preview-url` | jwt |
| `GET /v1/end-user/agent-harness/docstore/stores/{sid}/document-download-url` | jwt |
| `GET /v1/end-user/agent-harness/docstore/stores` | jwt |
| `GET /v1/end-user/agent-harness/docstore/stores/{sid}/documents` | jwt |
| `DELETE /v1/docstore/stores/{sid}/documents/{did}` | jwt |
| `POST /v1/docstore/stores/{sid}/folders` | jwt |

### Data Store (org-db)

| Route | Auth |
|---|---|
| `POST /v1/org-db/provision` | jwt |
| `GET /v1/org-db/tables` | jwt |
| `GET /v1/org-db/tables/{tid}` | jwt |
| `POST /v1/org-db/tables` | jwt |
| `PATCH /v1/org-db/tables/{tid}` | jwt |
| `PATCH /v1/org-db/tables/{tid}/schema` | jwt |
| `DELETE /v1/org-db/tables/{tid}` | jwt |
| `POST /v1/org-db/tables/{tid}/workstreams` | jwt |
| `DELETE /v1/org-db/tables/{tid}/workstreams/{wid}` | jwt |
| `POST /v1/org-db/tables/{tid}/assign-workstream` | admin |
| `POST /v1/org-db/tables/{tid}/rows` | jwt |
| `GET /v1/org-db/tables/{tid}/rows` | jwt |
| `PATCH /v1/org-db/tables/{tid}/rows/{rid}` | jwt |
| `DELETE /v1/org-db/tables/{tid}/rows/{rid}` | jwt |
| `POST /v1/org-db/sql` | jwt |

### Turns, conversations, transcript, outputs, trace

| Route | Auth |
|---|---|
| `POST /v1/end-user/agent-harness/turns` | jwt |
| `POST /v1/end-user/agent-harness/turns/{turn_id}/stop` | jwt |
| `GET /v1/end-user/agent-harness/turns/{turn_id}/status` | jwt |
| `GET /v1/end-user/agent-harness/turns/{turn_id}/stream-checkpoint` | jwt |
| `GET /v1/end-user/agent-harness/turns/{turn_id}/temporal-history` | jwt |
| `GET /v1/end-user/agent-harness/turns/{turn_id}/stream` | jwt |
| `GET /v1/end-user/agent-harness/conversations` | jwt |
| `GET /v1/end-user/agent-harness/conversations/{cid}/transcript` | jwt |
| `GET /v1/end-user/agent-harness/conversations/{cid}/outputs/download` | jwt |
| `GET /v1/end-user/agent-harness/trace/{turn_id}` | jwt |
| `GET /v1/end-user/agent-harness/conversations/{cid}/trace` | jwt |

### Escalations

| Route | Auth |
|---|---|
| `GET /v1/end-user/agent-harness/escalations/{bid}/card` | jwt |
| `POST /v1/end-user/agent-harness/escalations/{bid}/decide` | jwt |
| `GET /v1/end-user/agent-harness/builders/{bid}/capabilities` | jwt |
| `GET /v1/end-user/agent-escalations` | jwt |

### Runs

| Route | Auth |
|---|---|
| `GET /v1/runs` | jwt |
| `GET /v1/runs/detail` | jwt |
| `GET /v1/runs/conversation-trace` | jwt |
| `GET /v1/runs/turn-trace` | jwt |

### Pipelines

| Route | Auth |
|---|---|
| `POST /v1/pipelines` | jwt |
| `GET /v1/pipelines` | jwt |
| `GET /v1/pipelines/{pid}` | jwt |
| `POST /v1/pipelines/{pid}/update` | jwt |
| `DELETE /v1/pipelines/{pid}` | jwt |
| `GET /v1/pipelines/{pid}/runs` | jwt |
| `GET /v1/pipelines/{pid}/runs/{rid}/detail` | jwt |
| `POST /v1/pipelines/{pid}/runs/{rid}/stop` | jwt |
| `POST /v1/pipelines/{pid}/runs/{rid}/resolve` | jwt |
| `POST /v1/pipelines/workflows/test-run` | jwt |

### Vault

| Route | Auth |
|---|---|
| `POST /v1/settings/secrets` | admin |
| `GET /v1/settings/secrets` | admin |
| `PUT /v1/settings/secrets/{key}` | admin |
| `DELETE /v1/settings/secrets/{key}` | admin |

### Not implemented (in `BFF_ROUTES.md`, not needed by any abcd command)

`/v1/personal-tokens`, `/v1/org/console-access*`, `/v1/org/member-config*`, workstream member
routes, `POST /v1/org/processes/compile` and `/starters`, `.../write-audit`,
`POST /default-skills/rollback` and `/cleanup`, `/uploads/presign|confirm|abort`,
`.../outputs/download-url`, `/artifacts/*`, `/entities/*`, the Books and rating routes,
`/v1/mcp-integrations/*`, `/v1/pipelines/state-counts`, `/dashboard/stats`, `/all-tables`,
`/{id}/data`, document extraction routes, and `/v1/org-db/overview|activity|ai-*|writers|readers`.
Rate limits (240 reads and 60 writes a minute) are not modelled.

## Where the fake is an assumption

- Response envelopes that `BFF_ROUTES.md` leaves open are guesses: `GET /v1/settings/secrets`
  answers `{items, total}`, `GET .../conversations` answers `{conversations: [...]}`, and
  `POST /v1/pipelines/workflows/test-run` answers `{run_id, status}` (the document says a
  RUN_PROCESS pipeline is not verified through it).
- The Data Store `/sql` route runs the guarded statement on SQLite, not Postgres.
- JWTs are HS256-shaped and signed with the fake's own key; the real platform returns Frontegg
  RS256 tokens. Whether a PAT-minted JWT carries `email` and `roles` is unproven (see
  `BFF_ROUTES.md` section 0); the fake issues both.
- The real harness (Redis streams, Temporal) is not modelled: a turn's events exist when
  `POST /turns` returns.

## Known defects these tests expose

- `test_e2e_auth.py`: two strict `xfail` tests. A failed PAT exchange exits 1, not 3.
- `test_e2e_wave2_builder.py`: none. The cross-module defects it exposed (release.yaml schema,
  workstream names sent as ids, unrecorded live workstream, unscored datasets) are fixed at source.
