> **Repurposed for Wave-2.** This file was originally drafted as the body for an abcd PR — turned out abcd is direct-publish (`workspaces/*` gitignored, `save_*_draft.py` is the deploy surface) so no abcd PR ever opens. Per Iain `REPLY_TO_ADRYANN_HANDOFF_ACK_20260525.md` §4: *"Keep it. Rename to `WAVE2_PR_BODY_DRAFT.md` — it's the right shape for the separate Wave-2 ticket PR description when that opens (Wed/Thu)."*
>
> **When Wave-2 opens** (post-Wave-1 stable on prod, Tuesday PM earliest), this body needs three swap-ins before use:
> 1. **URLs:** flip from `echo-summit.westus3.cloudapp.azure.com` (staging) to `swifty-panda...` (prod) in all sandbox script env blocks
> 2. **Version numbers:** v12 → v13 (action) + v52 → v53 (pipeline) — Wave-2 creates fresh drafts off the staging-proven v12/v52 widdle, repointed at prod
> 3. **Section titles:** s/"Change 1"/"Wave-2 Surface 1 prod cutover"/ and s/"Change 2"/"Wave-2 Surface 2 prod cutover"/; drop the "test status: 4/4 green against staging" lines (Wave-2 verifies via the live prod chain after Wave-1 soaks clean)
>
> All test evidence, deltas, bug counts, and v1.1 ticket queue stay relevant.
>
> ---
>
> **Callouts for v53 / future devs** (per Iain `REPLY_TO_ADRYANN_POL_GREEN_20260525.md` bonus ask):
>
> - **`/preparations/{prep_id}/deliverables` returns a DICT envelope, not a list.** Shape is `{"preparation_id": "...", "deliverables": [...]}` — NOT bare `[...]`. Adopt-side WDL chains and probe scripts that use `isinstance(list)` will return 0 hits across all preps. Always unwrap with `payload.get("deliverables", [])` first. (Discovered during POL Stages 5-7 sweep against staging — `exec_pol.py` bug, fixed in commit `3b3b38d`.)
> - **`/api/v1/query` and `/api/v1/search` are POST, not GET.** Body shape: `{"query": "...", "client_id": <int>, "tax_year": <int>}` for `/query`; `{"query": "...", "top_k": N, "client_id": <int>?}` for `/search`. Per `clients_uhy/demo/api/main.py:3880,3889`.
> - **`/hitl-summary` is per-client (`/clients/{id}/hitl-summary`), not global.** There is no `/hitl-summary?client_name=...` or `/hitl/tasks` endpoint. Resolution is `POST /clients/{id}/duplicate-suspects/{event_id}/resolve` or `PATCH /employees/{id}` — also per-client/per-event.
> - **`client_id` is an INTEGER, not a UUID string.** On staging Complete Automation = 173; on prod Complete Automation = 2. Don't hardcode; always resolve via `/api/v1/clients` lookup.
> - **Deliverable signed URL has NO `expires_at` JSON key.** The `download_url` field is the entire signed URL with `token` and `expires` already in the query string. Just follow the URL with no Authorization header (HMAC is the auth) — `expires_at` is decodable from the URL params if you need it.
> - **`/preparations/{id}` does NOT return a `hitl_state` field.** Equivalent state is composed from `tasks`, `blockers`, `preflight`, and `runs[*]`. Don't write WDL/agent code that reads `.hitl_state` directly.

---

## Summary

Migrates **two Adopt.ai surfaces** to the convergence VM v0.4+ contract per Iain's directives (`UHY_WORKFLOWS_20260524.md` + `REPLY_TO_ADRYANN_ARCHITECTURE_D_GREEN_LIGHT_20260524.md` + `REPLY_TO_ADRYANN_F7_F8_SANDBOX_GREENLIGHT_20260525.md` + `REPLY_TO_ADRYANN_DELTAS_ACK_CHANGE2_GO_20260525.md` + `REPLY_TO_ADRYANN_HANDOFF_ACK_20260525.md`).

Architecture **D** ("idempotency lookup with 'ask me again' UX"). No worker.py touch, no signal emit, no receiver pipeline. The event-triggered-actions-in-originating-chat infra is captured as a v1.1 follow-up ticket.

Opened as **DRAFT** per Iain's §4: *"open the PR today as a draft once Change 2 is at least stubbed."* Change 2 is more than stubbed — full implementation, v51 saved on platform.

---

## Change 1 — `generate-workbook` action SANDBOX rewrite (4/4 green)

**Platform:** action `96a83403-3b0f-4205-9e2b-676510c87766` **v12 DRAFT** (v11 published, points at prod swifty-panda — DO NOT publish v12 per Iain §2: "Wave-2 prod cutover is a separate ticket").

**WDL shape:** 28 steps, 4 branches (per `PROBE_FINDINGS_ARCH_D_REALITY_CHECK_FOR_IAIN.md` §13):
- **Case A** — existing run, status=completed → format deliverables + download URL
- **Case A-fold** — existing run, status=engine_silent_zero → format $0/$0 + empty download
- **Case C** — existing run, status=failed → format retry prompt
- **Case D** — no run exists → POST `/from-harness-async` + emit "preparing, ask me again"
- **Case D-Error** — POST returns 4xx/5xx → graceful `formatCaseDError`

**Test evidence:** `test_runner --all` against echo-summit staging, 4/4 green in 50s total. Per Iain §2: *"4/4 green IS the smoke. No joint smoke ceremony needed."*

| Case | Test | Result | Time |
|------|------|--------|------|
| A | `test_1.json` (Complete Automation TY 2025) | ✅ $264,063 credit + working download URL | 8.4s |
| A-fold | `test_2.json` (Bridge Organics TY 2025) | ✅ $0/$0/empty download (engine_silent_zero) | 8.5s |
| C | `test_4.json` (Akervall TY 2025, failed run) | ✅ retry prompt | 10.0s |
| D-Error | `test_3.json` (ZZZ Nonexistent TY 2099) | ✅ graceful "no corpus dir" message | 23.2s |

**3 deltas vs Iain's `REPLY_TO_ADRYANN_F7_F8_SANDBOX_GREENLIGHT_20260525.md` sketch — all ACKed by Iain in `REPLY_TO_ADRYANN_DELTAS_ACK_CHANGE2_GO_20260525.md` §1:**

| Delta | Iain's sketch | Shipped shape | Iain's ACK |
|-------|---------------|---------------|------------|
| 1 | `triggerNew` SANDBOX `sys.exit(1)` on 4xx | Exit 0 + `{ok: false, http_status, detail}` + new `checkTriggerOk` CONDITION → `formatCaseDError` | ✅ "Correct. My sketch was wrong. Your shape is better than mine — keep it as canonical, ship it." |
| 2 | `is_last_step: true` halts execution | Explicit `JUMP target=endOfFlow` after every outputA/B/C/D + a no-op `endOfFlow` JQ trailer | ✅ "Correct. Explicit JUMPs are belt-and-braces. File as platform bug post-PR." |
| 3 | "Remove `adopt_profile.json` override after SANDBOX rewrite" | Override kept — only POST tripped F8; `fetchPrepDetail` + `getDeliverables` are still REST GETs and need the staging bearer | ✅ "Correct, and I was wrong in my earlier §7. Keep the override." |

---

## Change 2 — `p1-copy-from-inbox` SANDBOX swap (built, real exec test Tue)

**Platform:** pipeline `2622de32e0f64bae` **v51 DRAFT** (v50 published, runs the legacy `eval_and_commit.py` against deprecated `/upload-and-evaluate` + `/commit`).

**Per Iain `REPLY_TO_ADRYANN_DELTAS_ACK_CHANGE2_GO_20260525.md` §3:** GO, start now, real test Tuesday post-Wave-1. Built per spec.

**Endpoint chain (convergence VM v0.4+):**
1. `POST /api/v1/clients/upload-zip` → `ingest_job_id` (handles 200+idempotent / 202 / 409+force_replace)
2. Poll `GET /api/v1/ingest/{id}/poll` → `harness_job_id` when `status=completed`
3. Poll `GET /api/v1/workbook/from-harness-async/{id}` → `preparations[0]{id, federal_credit, total_qre}` when `status=succeeded`

**Poll cadence:** 5s for first 30s, then 15s, **max 45 min wall-clock** (pipeline-side polling is safe — no 3-min chat-agent timeout).

**Surgical scope:** only the middle SANDBOX chain (`sbx_evalcommit_*`) — 3 of 19 widdle steps mutated, 16 byte-identical to v50:
- `sbx_evalcommit_init.upload_files[0]`: `eval_and_commit.py` → `upload_and_poll.py` (434 lines)
- `sbx_evalcommit_init.env` += `FORCE_REPLACE = "{workflow_arguments.force_replace}"`
- `sbx_evalcommit_init.timeout_minutes`: 10 → 50 (45min poll cap + headroom)
- `sbx_evalcommit_run.command`: filename change

**Output-shape contract preserved:** the new script emits the exact same JSON field-set the old emitted (`triage_status` / `go_no_go` / `folder_name` / `client_id` / `evaluation_id` / `engine_status` / `total_qre` / `federal_credit` / `s174_sre` (null) / `prep_run_id` / `elapsed_seconds` / `blockers`), so `vmEvalResult` JQ, `triageFormatted` JQ, `routeOnTriage` CONDITION, `triageHitl` ESCALATE, `sbx_trigger_*` Pipeline B trigger, and the final `output` OUTPUT_TEXT continue to work without modification.

**Where reviewers find the script:**
- Git-visible mirror: `uhy solution/change-2-upload-and-poll/upload_and_poll.py` + `README.md`
- Source of truth: embedded in `widdle.json` on the platform draft (`GET /v1/pipelines/2622de32e0f64bae`)
- (Why a mirror? `workspaces/` is `.gitignore`d.)

**Test status:**

| Tier | Status | Notes |
|------|--------|-------|
| `ast.parse` | ✅ | 434 lines, clean |
| `save_pipeline_draft --dry-run` | ✅ | 19 steps accepted |
| `save_pipeline_draft` (real) | ✅ | v51 on platform |
| Real execution against Bridge Organics zip | ⏳ | Tuesday post-Wave-1 per Iain §3 — I don't have the zip locally; can run from Iain's side via `adryann_smoke.sh staging Bridge` |

---

## Out of scope (per Iain)

- **No `worker.py` touch.** Architecture A (signal emit + receiver pipeline) was probed and found infeasible — see `PROBE_FINDINGS_ARCHITECTURE_RISK_FOR_IAIN.md`. Iain picked Arch D in `REPLY_TO_ADRYANN_ARCHITECTURE_D_GREEN_LIGHT_20260524.md`.
- **No publish.** Both Change 1 (v12) and Change 2 (v51) stay drafts. Production points at prod swifty-panda for the action and at the legacy `eval_and_commit.py` chain for the pipeline. Per Iain §2: *"Wave-2 (prod cutover) is a separate ticket post-Wave-1 stable."*
- **No deliverables step (Iain spec §3 step 4).** Iain: *"Skip if scope-creep concern; can land in a follow-up PR."* Skipped. v1.1 ticket queued.

---

## Platform bugs surfaced (file post-PR, running list = 5)

| # | Bug | Repo | Severity |
|---|-----|------|----------|
| 1 | `GET /api/v1/preparations?client_name=&tax_year=` silently ignores filter params (returns full list) | `adoptai/clients_uhy` | L |
| 2 | `widdle_docs` missing action-level `adopt_profile.json` override docs | `adoptai/adopt-docs` | L |
| 3 | F7: `{workflow_arguments.X}` doesn't substitute in `JQ_FILTER` bodies | adopt platform | M |
| 4 | F8: `WiddleExecutor` mis-classifies non-200-shaped success responses (e.g. `{status:"queued"}`) as 400 | adopt platform | M |
| 5 | `is_last_step: true` doesn't halt action execution; explicit `JUMP` required | adopt platform | M |

---

## v1.1 follow-up tickets (file post-PR)

- `T-Event-Triggered`: event-triggered actions infrastructure for HITL-in-originating-chat (per Iain `REPLY_TO_ADRYANN_ARCHITECTURE_D_GREEN_LIGHT_20260524.md` §11)
- `T-Case-E`: reintroduce Case E for `engine_silent_zero` with proper "no qualifying R&D activity" wording (currently folded into Case A returning `$0 credit / $0 QRE / empty download`)
- `T-S174`: surface `s174_sre` in the new `/workbook/from-harness-async` response (currently `null` in Change 2 output; legacy `eval_and_commit.py` got it from `engine.s174_sre`)
- `T-FR-AutoRetry`: auto-retry inside the same pipeline run when operator says `force_replace` on a 409 (currently requires manual re-trigger with `FORCE_REPLACE=true` workflow_arg)
- `T-Deliverables-Step`: add the optional `GET /preparations/{prep_id}/deliverables` step (Iain spec §3 step 4, skipped for v1.0)

---

## Wave-2 — prod cutover (separate ticket)

Per Iain §7: *"Stop thinking about prod/swifty-panda in this PR's scope. Wave-2 is its own ticket, opens post-Wave-1 stable on prod (Tuesday afternoon earliest). Don't pre-build v13 or repoint URLs — Wave-2 will create the prod-pointed action as a copy from v12."*

---

## Side effects during diagnostics

During F8 diagnosis (the `is_last_step: true` no-halt issue), the action accidentally fired POST `/workbook/from-harness-async` for `Complete Automation TY 2025` — created **staging job_id 32** (and also job_id 31 from earlier F7 diagnosis). Both succeeded (`status=succeeded`, `preparations: []`) — no prod impact, no data corruption. Mentioning here per Iain's request so reviewers querying the staging DB don't get confused. Idempotency lookup catches subsequent runs.

---

## Test plan / how to review

### Change 1 (action `96a83403` v12)

```bash
# Pull the draft WDL
gh api repos/adoptai/clients_uhy_core_pipeline/actions/96a83403-3b0f-4205-9e2b-676510c87766/versions/12

# OR re-run the 4-case suite from the workspace
cd abcd && source venv/bin/activate
python cli/test_runner.py generate-workbook --all
```

Expected: 4/4 green in ~50s total. Traces written to `workspaces/uhy-prod/actions/generate-workbook/traces/`.

### Change 2 (pipeline `2622de32e0f64bae` v51)

```bash
# Pull the draft WDL
gh api repos/adoptai/clients_uhy_core_pipeline/pipelines/2622de32e0f64bae/versions/51

# OR read the script mirror in this PR
cat "uhy solution/change-2-upload-and-poll/upload_and_poll.py"
cat "uhy solution/change-2-upload-and-poll/README.md"
```

Tuesday post-Wave-1, real exec test:
```bash
cd clients_uhy/uhy_client_onboarding
./adryann_smoke.sh staging Bridge      # silent_zero shape
./adryann_smoke.sh staging BioPro      # folder_eq_legal_multiyear
./adryann_smoke.sh staging Reinhart    # folder_neq_legal_multiyear (don't disturb soak)
```

### Spec docs in this PR

- `uhy solution/PR_PLAN_SURFACE1_HANDSHAKE_FOR_IAIN_REVIEW.md` — Arch D plan + decisions locked
- `uhy solution/PROBE_FINDINGS_ARCH_D_REALITY_CHECK_FOR_IAIN.md` — 5 spec deltas + §13 (Change 1 cases) + §14 (Findings #7/#8 SANDBOX-wrap) + §15 (Change 2 build summary)
- `uhy solution/change-2-upload-and-poll/` — Change 2 script mirror + README

---

## Commit log on this branch

```
99132e6 feat(uhy-arch-d): Change 2 — p1-copy-from-inbox SANDBOX swap to upload_and_poll.py
6db9864 docs(uhy-arch-d): §14 — Findings #7/#8 RESOLVED via SANDBOX-wrap, 4/4 tests green
9031b2f docs(uhy-arch-d): test evidence + 2 platform findings post VM back-up
256fde4 docs(uhy-arch-d): pin PR plan, probe findings, and echo-summit infra blocker
```
