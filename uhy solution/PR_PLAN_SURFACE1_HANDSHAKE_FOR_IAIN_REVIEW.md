# PR plan — Surface 1 / Surface 2 handshake with convergence VM

> **STATUS (updated 2026-05-24 night): ACK'd by Iain via `REPLY_TO_ADRYANN_PR_PLAN_20260524.md`.** Scope confirmed; 7 of 8 open Qs answered; only Q4 (chat-agent timeout, platform-side) remains. See §0 below for the resolved decisions before reading §1+.
>
> Original sources: `UHY_WORKFLOWS_20260524.md` (Iain conceptual map) + `ADRYANN_DISPATCH_20260525.md` (Iain's Monday playbook). Iain's reply is the third source.

---

## §0 — Decisions locked after Iain's reply (read this first)

| # | Open Q from §6 | Iain's answer / our probe result | Action this implies |
|---|---|---|---|
| 1 | 3 local `generate-workbook` copies — same or different action_id? | **Resolved by local probe** (Iain endorsed running it). ONE platform action (`96a83403-3b0f-4205-9e2b-676510c87766` v11). Workpaper-agent's local copy points at GHOST id `63860b1f-...` which returns "Action not found" on the platform. Uber-agent's local copy has correct id but stale URL (says echo-summit; platform says swifty-panda). | **Edit ONE platform action**. Delete the two stale local checkouts under `agents/*/actions/generate-workbook/` after the canonical edit is in place. Mention deletions in PR description. |
| 2 | SANDBOX script vs native WDL for new ingest step? | **SANDBOX script.** Consistency with existing `eval_and_commit.py`. Better `try/except` + observability via structured prints. Native WDL only if SANDBOX hits a wall. | Build the new ingest as `upload_and_poll.py` SANDBOX script (or fold into a renamed `eval_and_commit.py` body). |
| 3 | `/workbook/from-harness-async/{id}` terminal-response shape? | **Confirmed by Iain's code read.** Use `preparations[0].preparation_id` + `preparations[0].federal_credit` + `workbook_jobs[0].preparation_deliverable_id`. Construct download URL as `/api/v1/deliverables/{preparation_deliverable_id}/download/{filename}` (server URL-encodes — don't double-encode). | Belt-and-braces: also call `GET /api/v1/preparations/{preparation_id}/deliverables` as fallback because `workbook_deliverables[]` JOIN has P1.1 bug (B-040 clients drop). |
| 4 | Adopt.ai chat-agent max-response-time? | **RESOLVED: 3 minutes** (Adryann confirmed with platform team). **Note: we don't fight this** — action returns immediately (<1 sec) with a "preparing…" message; 3-min timeout never bites. The real implication is UX-shape: we can't return the URL inline regardless of timeout (every client takes minutes), so we either need a receiver pipeline (Architecture A) or accept manual re-asking (Architecture D). | **Scope expansion request sent to Iain**: see `SCOPE_EXPANSION_REQUEST_Q4_FORCES_WEBHOOK_FOR_IAIN.md`. Proposes Architecture A (worker emits `workbook.ready` signal + new receiver pipeline opens HITL with download URL in originating chat). Action body becomes SIMPLER than current (just idempotency GET + async POST + immediate OUTPUT_TEXT). ~30 LoC worker.py touch + new pipeline + 2 extra steps in the action. Awaiting re-ACK before branching. |
| 5 | Branch + PR strategy? | **Branch off `dev`, PR back to `dev`.** Name like `feat/adopt-surface1-handshake` or `feat/upload-zip-action-migration`. Don't branch from any integration branch. | Confirmed I'm already on `dev` (switched + fast-forwarded earlier). Branch name TBD on user OK. |
| 6 | UX wording for deferred / hybrid case? | Verbatim string given (only matters if Q4 → hybrid): *"Your RTC workbook for **{client_name} TY {tax_year}** is being prepared (~{eta_minutes} min remaining). I'll fetch it when ready — ask me again in a few minutes, or watch for a notification."* `preparation_id` structured but not surfaced as UUID text. | Reserved for hybrid branch only. |
| 7 | VM version target? | **v0.4.3** (staging today). Iain will redeploy if v0.4.4+ ships before joint smoke. | Test against staging echo-summit's v0.4.3. |
| 8 | Joint smoke timing? | **Monday 10:00 ET (07:00 PT / 15:00 UTC).** Iain triggers via `adryann_smoke.sh --client biopro --target staging`. We extend to Bridge + Reinhart if BioPro passes. **No Complete Automation in joint smoke** — defer to async verification afterward. | Calendar slot locked. |

### Iain's scope refinement to fold into §2

> "Please keep the action's input contract identical (same `client_name`, `tax_year` fields) — the only change should be how it talks to the API + how it returns. That way any other agent flow that already calls the action keeps working."

Already implicit in §2 proposed WDL — making explicit.

### Iain's two non-blocking pushback items (worth folding in, drop if it bloats the PR)

1. **Polling cadence**: 5s for first 30s, then 15s (not 2s like current `eval_and_commit.py`). Less log noise on minutes-long tasks.
2. **Action-side idempotency**: before calling `/upload-zip`, look up the existing `preparation` — if one exists with a `queued`/`doing` run, return its `poll_url` instead of enqueueing a duplicate. "Cheaper to dedupe in the action than in the API." Iain has F-004 idempotent re-projection but not idempotent ingest.

### New finding from the Q1 probe (added to scope)

The live published `generate-workbook` v11 currently points at **prod (`swifty-panda`)**, NOT staging (`echo-summit`). For the migration to make sense in `uhy-prod` env on staging:

- Switch URL from `https://swifty-panda.westus3.cloudapp.azure.com` → `https://echo-summit.westus3.cloudapp.azure.com`
- AND switch contract from sync `/workbook/generate-sync` → async `/workbook/from-harness-async` chain
- (Per Iain §"What I'm NOT asking you to do": *"Don't reconcile the action with prod/swifty-panda in this PR. Staging-first; prod cutover is a separate ticket after we burn down the soak."* — so prod stays on its current path; only the staging action body changes.)

### What's NOT changing from the original plan

- §2 changes 1 + 2 (action + pipeline migration) stand
- §3 out-of-scope stands; ADD: "delete two stale local `generate-workbook` checkouts under `agents/uhy-uber-agent/actions/` and `agents/uhy-workpaper-agent/actions/` (ghost action_id + stale URL respectively)"
- §4 test plan stands; ADD Iain's Monday-10ET joint-smoke shape
- §5 risk/rollback stands; Iain's one-paragraph SOP for the PR description: *"Revert the action body to the prior `/workbook/generate-sync` call + the pipeline body to `/upload-and-evaluate` + `/commit`; legacy endpoints are still live, no API rollback required."*

### Current gating status

| Gate | State |
|---|---|
| Iain ACK on original scope | ✅ |
| 7 of 8 Qs answered by Iain | ✅ |
| Q4 (platform timeout) | ✅ RESOLVED = 3 min |
| Iain re-ACK on Arch A (subject to 2 probes) via `REPLY_TO_ADRYANN_ARCHITECTURE_A_ACK_20260524.md` | ✅ (then superseded — see below) |
| Probe #1 (worker.py emit pattern) | ⚠️ Mostly green; revised LoC up to ~60–80 (was ~15); task-tail emit precedent is net-new code, not a copy. Now MOOT — Arch D has zero worker touch. |
| Probe #2 (receiver pipeline pattern) | ❌ RED — pattern doesn't exist in platform. See `PROBE_FINDINGS_ARCHITECTURE_RISK_FOR_IAIN.md`. |
| Iain's fallback choice via `REPLY_TO_ADRYANN_ARCHITECTURE_D_GREEN_LIGHT_20260524.md` | ✅ **Architecture D approved.** A' and B rejected (both blocked on missing "post HITL to existing workstream" API). Probe #2 status: RESOLVED — no receiver pipeline needed, no signal emit needed, idempotency lookup is the future-proof building block A'/B will need later. |
| Branch name | ✅ `feat/adopt-surface1-surface2-architecture-d` off `dev` in `abcd/` (per Iain) |
| Branch created | ❌ Branching tomorrow (Iain: "Sleep well. Branch tomorrow.") |
| Code changes started | ❌ Nothing touched yet |
| Preparation-lookup endpoint shape probe (Iain Q1 of "tomorrow asks") | ⚠️ **YELLOW** — ran, found 5 spec deltas vs Iain's sketch (none architectural). See `PROBE_FINDINGS_ARCH_D_REALITY_CHECK_FOR_IAIN.md`. Headlines: (1) server ignores `?client_name=&tax_year=`, must filter client-side; (2) status vocab on staging is `pending/running/completed/failed/engine_silent_zero/superseded/stub_carry` — NOT Iain's `queued/doing/succeeded/failed/cancelled`; (3) need 2-step REST (list → detail) because `runs[]` is detail-only; (4) `federal_credit` lives on `runs[i]`, not deliverable; (5) Bridge Organics TY25 is `engine_silent_zero` → new Case E. Action body now has 6 branches not 3, but same architecture. Iain ACK on Case E wording pending before branching. |
| v1.1 follow-up ticket (event-triggered actions infra) | ⏳ TBD post-PR — see §11 below |

---

---

## TL;DR

Two adopt.ai surfaces talk to endpoints that the convergence VM has either replaced or deprecated:

| Surface | adopt.ai artifact | What it does today | Why it breaks under convergence v0.4.3 |
|---|---|---|---|
| 1 — Upstream | `p1-copy-from-inbox` pipeline | Calls legacy `/upload-and-evaluate` + `/commit` on echo-summit via SANDBOX script `eval_and_commit.py` | The new contract per Iain §4/§5 is `/upload-zip` + poll `/ingest/{id}/poll` + poll `/workbook/from-harness-async/{harness_job_id}` + fetch `/deliverables/*`. Legacy endpoints status unknown. |
| 2 — Agent | `generate-workbook` action (3 local copies) | Sync POST to `/api/v1/workbook/generate-sync`, returns `/api/v1/workbook/download/{job_id}/...` URLs to chat | Per Iain §4: old download shape is **INACTIVE for convergence-generated deliverables** (`workbook_jobs` table has 0 new rows from May 24). New endpoints: `GET /deliverables/{id}`, `GET /deliverables/{id}/download/{filename}?token=...&expires=...`, `GET /preparations/{prep_id}/deliverables`. |

**Proposed PR scope (one PR, three changes)**:

1. **`generate-workbook` action** → switch from sync to async-poll, then emit the new deliverable download URLs.
2. **`p1-copy-from-inbox` pipeline** → swap the embedded `eval_and_commit.py` SANDBOX script's `/upload-and-evaluate` + `/commit` calls for the new `/upload-zip` + polling chain.
3. **No new pipeline. No worker code changes. No webhook plumbing.** The prior "generate-workbook async + webhook + receiver pipeline" plan is **dropped** — the convergence VM already does the wait + signal internally; we just poll it.

Nothing else changes. The 14 other actions + Surface-3 pipelines (`uhy-prep-review`, `uhy-hitl-task-review`) keep working as-is — they already speak the new contract.

**Risk profile**: low-medium. All changes are WDL-level edits in `abcd/`; no platform service code, no DB migrations. Backout = re-publish prior version (every action has full `versions/` history; pipeline has `versions/live_20260524_widdle.json` baseline post-snapshot).

---

## §1 — Baseline (what's live right now)

Per `TEST_FLOWS_CHILD_AND_PIPELINE_A.md` §6b (snapshot captured 2026-05-24 22:50 UTC-3):

| Surface | Slug | Action / Remote ID | Current published version | Endpoint(s) it calls |
|---|---|---|---|---|
| 1 | `p1-copy-from-inbox` | `2622de32e0f64bae` | v50 + hard-writes | SANDBOX `eval_and_commit.py` → `POST /api/v1/zip-jobs/upload-and-evaluate` + `POST /api/v1/zip-jobs/{evaluation_id}/commit` (with idempotent retry logic for 409 / 500 / `force_replace`) on `echo-summit` |
| 2 | `generate-workbook` (standalone) | `96a83403-3b0f-4205-9e2b-676510c87766` | v11 | `POST https://swifty-panda.westus3.cloudapp.azure.com/api/v1/workbook/generate-sync`; outputs `.downloads.workbook` + `.downloads.audit_trail` (legacy job-id shape) |
| 2 | `uhy-workpaper-agent` (consumer) | `1215c0d1-e18b-46e6-8e32-7ed3d5ebfcb2` | v3 | Wraps `generate-workbook` |
| 2 | `uhy-uber-agent` (consumer) | `a307de2b-93ed-43d5-9d80-354d40d99d45` | v42 | Has `generate-workbook` among its 20 sub-actions |

**Already synced (in §6b drift-flags)**: child pipeline `uhy-suralink-ingest-per-zip` live patch to `unifyHitlOverrides` JQ (smarter `force_create` logic when `workstream_id` is already provided). No platform change made — only local widdle.json updated from live; needs proper `save_pipeline_draft.py` cycle at some point to advance the canonical version number (separate concern, not in this PR).

---

## §2 — Proposed change set

### Change 1 — `generate-workbook` action: sync → **Architecture D idempotency-lookup** (per Iain's REPLY_TO_ADRYANN_ARCHITECTURE_D_GREEN_LIGHT_20260524.md)

**File**: ONE platform action (`96a83403-...`) at `workspaces/uhy-prod/actions/generate-workbook/widdle.json`. The two agent-folder copies are stale local checkouts (uber-agent: same action_id, drifted URL; workpaper-agent: ghost action_id `63860b1f-...` that returns "Action not found" on platform) — **DELETE both local copies** as part of this PR; no platform action exists at the ghost id (verified via probe).

**Today's WDL** (v11 live, abbreviated):
```
[
  required_inputs: client_name, tax_year,
  generateWorkbook: REST POST https://swifty-panda.../api/v1/workbook/generate-sync
                    { client_name, tax_year } → returns summary + .downloads.{workbook,audit_trail},
  formatOutput: JQ → markdown "Workbook generated... [Populated Workbook](...) [Audit Trail](...)",
  output: OUTPUT_TEXT
]
```

**Proposed WDL** (Architecture D — idempotency lookup, **5 branches** A/B/C/D/E per probe findings. Lifetime: <2 sec for all cases. Never polls. Never approaches 3-min platform timeout):

> ⚠️ **Pre-branch probe findings** in `PROBE_FINDINGS_ARCH_D_REALITY_CHECK_FOR_IAIN.md` corrected Iain's sketch in 5 places. Body below is the canonical spec.

```
required_inputs: client_name (string), tax_year (integer)

Step 1 — listPreps:
  GET {VM_BASE_STAGING}/api/v1/preparations
  (server ignores ?client_name=&tax_year=, must filter client-side — confirmed by probe)

Step 2 — matchPrep JQ (case-insensitive substring on client_name + exact tax_year):
  [ .[] | select(
      (.client_name | ascii_downcase) | contains($q | ascii_downcase)
    ) | select(.tax_year == ($ty | tonumber))
  ] | sort_by(.created_at) | last
  → null if no match, else { id, client_name, tax_year, ... }

Step 3 — CONDITION on matchPrep:
  null → JUMP to step 9 (Case D)
  else → continue

Step 4 — fetchPrepDetail:
  GET {VM_BASE_STAGING}/api/v1/preparations/{matchPrep.id}
  → returns { runs: [...], deliverable_count, status, ... }

Step 5 — pickLatestRealRun JQ (filter out non-terminal-noise statuses):
  .runs
  | map(select(.status as $s | $s != "superseded" and $s != "stub_carry"))
  | sort_by(.run_number)
  | last
  → null if all runs were superseded/stub_carry (treat as Case B in-flight)

Step 6 — CONDITION on pickLatestRealRun.status:
  null OR status in ('pending', 'running')  → Case B
  status == 'completed'                     → Case A
  status == 'failed'                        → Case C
  status == 'engine_silent_zero'            → Case E
  default unrecognized                       → Case B (conservative)

Step 7 — Case A path (terminal-good with deliverable):
  Step 7a — getDeliverables: GET /api/v1/preparations/{matchPrep.id}/deliverables
  Step 7b — pickNewestDeliverable JQ: .deliverables | sort_by(.created_at) | last
  Step 7c — caseA_format JQ:
    "Workbook ready for {client_name} TY {tax_year}.
     **Federal credit:** ${federal_credit:,.2f}
     **Total QRE:** ${total_qre:,.2f}
     **Download:** [{filename}]({download_url}) (expires 7d)"

Step 8 — Case B/C/E formatters (JQ each):
  Case B: "Workbook generation is still in progress for {client} TY {year}.
           Started at {run.started_at}. Typical runtime:
             • Small (e.g. Bridge Organics): ~5 min
             • Medium (e.g. Reinhart, BioPro): ~10–15 min
             • Large (e.g. Complete Automation): ~30–40 min
           Ask me again when you think it should be ready."
  Case C: "Last workbook generation attempt for {client} TY {year} failed:
             {run.error or 'unknown error'}.
           Want me to retry?
           (Reply 'yes' to start a new attempt, or work with a CPA to resolve the failure.)"
  Case E (NEW per probe): "Workbook ready for {client} TY {year}, but the engine
           found **no qualifying R&D activity** (federal credit = $0).
           This usually means either:
             • No R&D documents were ingested for this client/year, OR
             • The activity genuinely doesn't qualify under §41 rules.
           Want me to check what documents the preparation has, or work with a CPA?"
  [Iain ACK pending on Case E wording before branching.]

Step 9 — Case D path (no matching prep — trigger new):
  Step 9a — triggerNew: POST /api/v1/workbook/from-harness-async {client_name, tax_year}
                        → 202 + { job_id, poll_url }
  Step 9b — caseD_format JQ:
    "Started generating workbook for {client} TY {year}.
     Typical runtime: ~5 min (small) → ~35 min (large with many BCs).
     Ask me again in a few minutes to check status."

Step 10 — output: OUTPUT_TEXT (markdown, single message)
```

**Design notes:**
1. Action keeps `client_name` + `tax_year` input contract IDENTICAL — no signature change for any caller (uber-agent, workpaper-agent, future direct callers). (Iain's earlier ask.)
2. The 5-branch shape is the exact building block A'/B will need later when the receiver infra exists — future-proof for v1.1 upgrade.
3. URL switches `swifty-panda` (prod) → `echo-summit` (staging) per §6b finding that live v11 points at prod.

**Status vocab → Case mapping** (corrected via probe — Iain's Q2 vocab `queued/doing/succeeded/failed/cancelled` is NOT what's running on staging):

| Live status | Count on staging (141 runs) | Case |
|---|---|---|
| `completed` | 36 | A |
| `pending` | 17 | B |
| `running` | 3 | B |
| `failed` | 21 | C |
| `engine_silent_zero` | 8 | **E (new)** |
| `superseded` | 41 | filtered out (transient) |
| `stub_carry` | 15 | filtered out (prior-year placeholder) |

**Where `federal_credit` / `total_qre` come from** (corrected via probe — Iain's Q3 was wrong on both options): they live on `runs[i].federal_credit` + `runs[i].total_qre` in the prep-detail response, NOT on deliverables and NOT on the list-response row. Download URL comes from the separate `/deliverables` call in Case A only.

**Case A confirmed test target**: `Complete Automation, Inc.` TY 2025 → 1 completed run, federal_credit=$264,063.18, deliv_count=1 ✓ (probed Sunday night).

**Case E surfacing**: Bridge Organics TY 2025 = `engine_silent_zero` → joint smoke Monday will hit Case E for Bridge. Iain ACK on whether that counts as smoke-pass or needs reset first.

**Sanity-tested by**: `test_runner.py generate-workbook` against Complete Automation TY 2025 (Case A), then a non-existent (client_name, tax_year) pair (Case D), then Bridge Organics TY 2025 (Case E surfacing).

### Change 2 — `p1-copy-from-inbox` pipeline: legacy ingest → convergence ingest

**File**: `workspaces/uhy-prod/pipelines/p1-copy-from-inbox/widdle.json`

**Today's relevant flow** (inside SANDBOX `eval_and_commit.py`):
1. `POST /api/v1/zip-jobs/upload-and-evaluate` (with `_upload_with_retry` for 409s)
2. `POST /api/v1/zip-jobs/{evaluation_id}/commit` (with `_commit_with_retry` + auto `force_replace=True` + pre-flight `GET /zip-jobs/{id}` status check)
3. Returns `total_qre`, `federal_credit`, `s174_sre` in `vmEvalResult`

**Proposed flow** (per Iain `UHY_WORKFLOWS_20260524.md` §5 + `ADRYANN_DISPATCH_20260525.md` §4):
1. `POST /api/v1/clients/upload-zip` (multipart: file, dir_name, tax_year, auto_ingest=true, force_replace=false)
2. If HTTP 200 + `idempotent: true` → treat as success, jump to step 3 with returned `ingest_job_id`. If HTTP 202 → fresh upload, take returned `ingest_job_id`. If HTTP 409 → operator decision (escalate or force_replace=true retry).
3. Poll `GET /api/v1/ingest/{ingest_job_id}/poll` until status=completed → returns `harness_job_id` + `harness_poll_url`
4. Poll `GET /api/v1/workbook/from-harness-async/{harness_job_id}` until status ∈ {succeeded, failed}
5. (Optional in this PR) `GET /api/v1/preparations/{prep_id}/deliverables` → pass to Pipeline B / downstream notify

**This change keeps Pipeline A's existing HITL / triage / canonicalize logic intact**. Only the VM-side ingest step (currently `sbx_evalcommit_*` + `vmEvalResult`) gets replaced. The S3 canonicalization SANDBOX up top (`canonicalize.py`) stays. The triage decision HITL stays. Same per-zip granularity, same operator UX, just a different VM contract.

**Critical questions before writing this**:
- Should the new `eval_and_commit.py` be replaced with a new SANDBOX script (e.g. `upload_and_poll.py`) using `urllib`/`httpx`, or should the entire chain be expressed natively in WDL steps (REST + CONDITION + JUMP)? Trade-off: SANDBOX is easier to write retry/backoff logic; native WDL is more declarative + visible in trace. Iain's preference?
- The current `vmEvalResult` JQ extracts QRE/credit numbers and feeds them into downstream steps. Does `/workbook/from-harness-async/{id}` terminal response surface the same fields, or do I need an extra `GET /deliverables/{id}` round-trip?

### Change 3 — Reset the three local `generate-workbook` copies

The grep turned up THREE local copies of `generate-workbook/widdle.json`:
- `workspaces/uhy-prod/actions/generate-workbook/widdle.json` → swifty-panda
- `workspaces/uhy-prod/agents/uhy-uber-agent/actions/generate-workbook/widdle.json` → echo-summit (!)
- `workspaces/uhy-prod/agents/uhy-workpaper-agent/actions/generate-workbook/widdle.json` → swifty-panda

These look like checkouts that have diverged from each other. **Open Q #1**: are these the same platform action_id (`96a83403-...`) checked out three times with stale local content, or are they three distinct platform actions? If the same, only one needs editing + republishing. If distinct, all three need the same change.

---

## §3 — What's explicitly OUT of scope

- **No new pipeline.** No `uhy-workbook-ready-notify` receiver pipeline. Architecture D doesn't need one — idempotency lookup serves status from the existing VM endpoints.
- **No worker.py touch.** Zero LoC in `clients_uhy/demo/api/`. No `_defer_webhook_signal` call site added. The emit-side helper (`main.py:1164`) stays unconsumed — its first production wiring is deferred to v1.1 (see §11).
- **No signal_kind emit / consume.** No `publication_complete` / `engine_silent_zero` / `failure` emit from `generate_workbook_task`. No new signal subscriber. The chat agent renders status inline; we don't need an out-of-band channel.
- **No threading of `originating_workstream_id`.** Not needed — Arch D returns status in the same chat turn the user asked.
- **No HITL-in-originating-chat for "workbook ready" event.** This is the UX gap of Arch D. User asks "is it ready?" and gets either the URL or a "still working" message. The "proactive HITL when ready" UX is deferred to v1.1.
- **No prod / `swifty-panda` reconciliation.** Staging-only (`echo-summit`). Iain: *"Don't reconcile the action with prod/swifty-panda in this PR. Staging-first; prod cutover is a separate ticket after we burn down the soak."*
- **No API endpoint additions.** If the `/preparations?client_name=X&tax_year=Y` shape doesn't exist, fall back to the two-call `/clients?name=X` → `/clients/{id}/preparations` chain. Don't add a new endpoint to the API (Iain Q1).
- **No HITL surface changes.** `uhy-prep-review` + `uhy-hitl-task-review` already speak the new `/preparations/*` shape — verified in §1 inventory.
- **No `uhy-suralink-hitl-agent` changes.** It's at v34 on the platform vs v31 referenced in the original §5 doc — orthogonal to this handshake.
- **No child-pipeline `unifyHitlOverrides` cleanup PR.** Already synced locally; needs a separate `save_pipeline_draft` cycle later (logged in §6b).

---

## §4 — Test plan

Following Iain's dispatch §4 suggestion: pick small clients, drive end-to-end, observe.

### Per-change unit smoke (before integration)

| Change | Smoke command | Pass criteria |
|---|---|---|
| `generate-workbook` async | `python cli/test_runner.py generate-workbook --test test_1.json` with input `{client_name: "Bridge Organics", tax_year: 2025}` | 200/202 response chain, terminal `succeeded`, valid signed download URL, ≤ 15 min wall-clock |
| `p1-copy-from-inbox` ingest | `python cli/test_pipeline.py p1-copy-from-inbox` on Bridge Organics zip | `/upload-zip` 200/202, `/ingest/poll` reaches `completed`, `/from-harness-async` reaches `succeeded`, no HITL fired |

### End-to-end integration smoke (Iain's §4 acceptance)

Per dispatch §4: pick Bridge Organics (~4min) first, then Reinhart (~14min) for real-credit validation.

**Test 1 — Bridge Organics baseline (silent-zero, fast)**:
- Trigger: Iain runs his upload-zip → Adryann's adopt.ai-side handshake takes over → end-to-end succeeds
- Expected: `outcome=skipped_silent_zero`, no deliverable, but pipeline completes
- Validates: `/upload-zip` → `/ingest/poll` → `/from-harness-async` happy path

**Test 2 — Reinhart real-credit (deliverable validation)**:
- Trigger: same handshake, Reinhart 2025.zip (Iain has this; folder ≠ legal → exercises B-040 path)
- Expected: `federal_credit ≈ $217,592.62` (±0.1%), deliverable downloadable via signed URL
- Validates: full chain incl. `/deliverables/*` fetch + URL signing

**Test 3 — `generate-workbook` agent on already-prepared client**:
- Trigger: chat `"Generate workbook for Complete Automation TY 2025"` in agent
- Expected: 5-15 min agent response with new download URL pointing at `/deliverables/{id}/download/{filename}?token=...`, link works
- Validates: new agent-side async-poll + new deliverable shape rendering

### Failure-mode probes (Tier-1 per dispatch §4)

- Test on a client where `/from-harness-async` returns `failed` → agent renders error message gracefully, no crash
- Test idempotent 200 on `/upload-zip` (re-trigger same zip within 48h) → pipeline treats as success, doesn't retry upload
- Test 409 on different zip + same `dir_name` → operator-visible error (escalate to existing triage agent? new HITL?)

---

## §5 — Risk + rollback

| Risk | Mitigation | Rollback |
|---|---|---|
| New WDL doesn't poll long enough → agent times out | Use exponential backoff capped at 60s, max 45min total (Iain §12: Complete Automation = 35.5min) | Re-publish prior version via `python cli/publish_wdl_action.py generate-workbook --version 11` |
| `/upload-zip` rejects some current zip shape | Test on Bridge Organics first (small + known to Iain) | Re-publish Pipeline A prior version (`p1-copy-from-inbox` v50 hard-write baseline preserved at `versions/v50_widdle.json`) |
| Three local `generate-workbook` copies diverge after edit | Resolve Open Q #1 first; if distinct, edit + republish all three; if same, edit one + remove the other two local copies | Same as above |
| `/from-harness-async` polling shape doesn't carry `preparation_id` | Add `GET /preparations?client_name=...&tax_year=...` lookup as fallback | N/A — just an extra REST step |
| Surface-2 chat agent timeout shorter than 45min | Confirm with platform team; if so, agent emits "still generating, link will appear in this thread when ready" placeholder + uses HITL receiver pipeline pattern (revives the dropped plan ONLY in this case) | N/A |

**Rollback time budget**: ≤ 10 min per affected surface (republish prior version is one command).

---

## §6 — Open questions (all CLOSED — see §0 for resolutions)

> All 8 original gating Qs were resolved through three rounds with Iain. Kept here as audit trail. Decisions live in §0.

| # | Question | Resolution |
|---|---|---|
| 1 | Are the three local `generate-workbook` copies the same platform action_id? | ✅ Resolved by local probe (Iain's cheap-probe suggestion). Standalone + uber-agent share `96a83403-...` v11; workpaper-agent's local has GHOST `63860b1f-...` (returns "Action not found"). ONE platform record to edit, two stale local copies to delete. |
| 2 | SANDBOX script vs native WDL for ingest step? | ✅ Resolved: SANDBOX script (`upload_and_poll.py`). Consistency with existing `eval_and_commit.py`; better observability + retry/backoff. |
| 3 | Does `/from-harness-async/{id}` terminal carry preparation_id + summary fields? | ⚠️ Resolved by Iain's code-read, but **partially superseded** by Sunday-night probe of `/preparations/{id}`: `federal_credit` + `total_qre` actually live on `runs[i]` not `preparations[0]`. Probe finding details in `PROBE_FINDINGS_ARCH_D_REALITY_CHECK_FOR_IAIN.md` §5. Action body in §2 uses the probed shape. |
| 4 | Adopt chat-agent max response time? | ✅ Resolved: **3 minutes**. → Forced Q4 sub-track on architecture choice (Arch A vs A' vs B vs D). After probe #2 went RED on Arch A, Iain picked **Arch D** (`REPLY_TO_ADRYANN_ARCHITECTURE_D_GREEN_LIGHT_20260524.md`). Sub-tracks below archived. |
| 5 | First joint-smoke client + Monday timing? | ✅ Resolved: BioPro canary at Monday 10:00 ET (15:00 UTC). Iain runs `adryann_smoke.sh --client biopro --target staging`. Extend to Bridge then Reinhart if BioPro passes. Skip Complete Automation. |
| 6 | Triage HITL operator card content? | ✅ Resolved implicitly: the new flow defers QRE/credit numbers until post-`/from-harness-async`; triage card shows pre-ingest metadata only. (Iain didn't push back; status quo preserved.) |
| 7 | Convergence VM version target? | ✅ Resolved: v0.4.3 (staging today). Iain will redeploy staging if v0.4.4+ ships before joint smoke. |
| 8 | Branch + PR naming? | ✅ Resolved: `feat/adopt-surface1-surface2-architecture-d` off `dev`, PR back to `dev`, in `abcd/`. (Final name reflects Arch D choice.) |

### §6 (sub-track) — Arch A scope expansion → fallback (ARCHIVED 2026-05-24 EOD)

After Q4=3min came back, three rounds with Iain to figure out architecture:
1. `SCOPE_EXPANSION_REQUEST_Q4_FORCES_WEBHOOK_FOR_IAIN.md` — proposed Arch A
2. `REPLY_TO_ADRYANN_ARCHITECTURE_A_ACK_20260524.md` — Iain ACK'd Arch A conditional on 2 probes
3. `PROBE_FINDINGS_ARCHITECTURE_RISK_FOR_IAIN.md` — probe #2 went RED (no receiver-pipeline pattern in platform)
4. `REPLY_TO_ADRYANN_ARCHITECTURE_D_GREEN_LIGHT_20260524.md` — Iain picked Arch D over A'/B

Outcome: Arch D shipped in v1.0; Arch A deferred to v1.1 (§11 below). All sub-Qs archived.

---

## §7 — Execution sequence (approved by Iain — branch tomorrow, joint smoke Monday)

Per Iain's "5 asks for tomorrow" in `REPLY_TO_ADRYANN_ARCHITECTURE_D_GREEN_LIGHT_20260524.md`:

1. **Pre-branch probe** (5 min): confirm `/api/v1/preparations?client_name=X&tax_year=Y` endpoint shape on staging. If absent, plan for the two-call fallback `/clients?name=X` → `/clients/{id}/preparations`. Don't add a new endpoint.
2. **Create branch** `feat/adopt-surface1-surface2-architecture-d` off latest `dev` in `abcd/`.
3. **Build Change 1 first** (small, fast — Arch D action body):
   - Edit `workspaces/uhy-prod/actions/generate-workbook/widdle.json` to the 3-branch shape in §2
   - `python cli/test_runner.py generate-workbook --compile`
   - `python cli/test_runner.py generate-workbook` against an already-`succeeded` client (Complete Automation TY 2025 if available on staging) → confirms Case A
   - Add `test_2.json` for Case D (no-existing-prep) using a fresh client_name
   - Iterate. Save draft. Do NOT publish.
4. **Delete the two stale local copies** of `generate-workbook` (under `agents/uhy-uber-agent/actions/` and `agents/uhy-workpaper-agent/actions/`). Note in PR description.
5. **Build Change 2** (larger — pipeline-side ingest swap):
   - Edit `workspaces/uhy-prod/pipelines/p1-copy-from-inbox/widdle.json` SANDBOX body to call `/api/v1/clients/upload-zip` → poll `/api/v1/ingest/{id}/poll` → poll `/api/v1/workbook/from-harness-async/{id}` → fetch `/api/v1/preparations/{prep_id}/deliverables`
   - Polling cadence per Iain Q4: 5s for first 30s, then 15s, max 45 min total
   - `python cli/test_pipeline.py p1-copy-from-inbox` against Bridge Organics zip
   - Save draft. Do NOT publish.
6. **Joint smoke Monday 10:00 ET**: Iain runs `adryann_smoke.sh --client biopro --target staging`. Adryann observes the new action fire (instant "starting" message), waits ~6min, re-invokes the action as the "user" → returns deliverable URL + credits, sha256-verifies the workbook. Repeat for Bridge Organics (Case C silent-zero UX check). Skip Complete Automation (offline async).
7. **Publish** post-smoke: `publish_wdl_action.py generate-workbook --yes` + `publish_pipeline.py p1-copy-from-inbox --yes`. Snapshot new versions into `TEST_FLOWS_CHILD_AND_PIPELINE_A.md` §6c.
8. **PR opens by EOD Monday** with this doc, full diff, before/after WDL snippets, link to §6b baseline + §6c post-PR snapshot, joint-smoke evidence, and a reference to the §11 v1.1 follow-up ticket. Iain ACKs without further iteration unless something surprising surfaces.

---

## §8 — What this PR is NOT

So we don't drift mid-build:
- Not adding webhooks
- Not adding new pipelines
- Not touching the worker, the API service, the DB, or migrations
- Not changing HITL surfaces (prep-review, hitl-task-review, uhy-suralink-hitl-agent)
- Not retiring the old `swifty-panda` swifty-panda swifty-panda VM (Iain owns that decision)
- Not creating tunnels, ngrok forwards, or new env vars in adopt.ai
- Not addressing the v34 uhy-suralink-hitl-agent drift, the v42 uhy-uber-agent un-snapshot, or the uhy-prep-review missing-versions (logged in §6b, separate cleanup PR)

---

## §9 — Reference material

- Snapshot of all uhy-prod surfaces (pre-this-PR baseline): `uhy solution/TEST_FLOWS_CHILD_AND_PIPELINE_A.md` §6b
- Iain conceptual map: `~/Downloads/UHY_WORKFLOWS_20260524.md`
- Iain Monday playbook: `~/Downloads/ADRYANN_DISPATCH_20260525.md`
- Existing Pipeline A history: TEST_FLOWS doc §6 + §6 Pipeline A v22-v31 history
- WDL operation docs (roaming RAG): `https://adoptai.github.io/widdle_docs/operations/index.md`

---

## §10 — Decision status

> All decisions locked. No further Iain input needed before branching. Doc now serves as the canonical implementation spec.

| | State |
|---|---|
| Iain ACK on Architecture D | ✅ `REPLY_TO_ADRYANN_ARCHITECTURE_D_GREEN_LIGHT_20260524.md` |
| All 8 original Open Qs | ✅ Resolved (see §6) |
| Sub-track on Arch A→A'→B→D | ✅ Resolved → Arch D ships, Arch A deferred to v1.1 |
| Branch name | ✅ `feat/adopt-surface1-surface2-architecture-d` |
| Joint smoke slot | ✅ Monday 10:00 ET (07:00 PT / 15:00 UTC) |
| Pre-branch endpoint probe | ⏳ Running (Iain's tomorrow ask #3) |

---

## §11 — v1.1 follow-up ticket (capture before we forget)

Per Iain's `REPLY_TO_ADRYANN_ARCHITECTURE_D_GREEN_LIGHT_20260524.md`, draft to file post-PR against `adoptai-workflows`:

> **Title:** Event-triggered actions infrastructure for HITL-in-originating-chat
>
> Builds on Adryann's March 2026 draft spec `docs/scheduled-event-triggered-actions-spec.md` (status: Draft, not built).
>
> **Required platform capabilities (currently missing):**
> 1. Subscribe a pipeline (or new artifact type) to a webhook `signal_kind` vocabulary (current webhook intake at `adoptwebui/backend/app/routes/webhook.py` triggers actions, not pipelines, and creates new conversations).
> 2. API to post a HITL/message into an *existing* workstream from outside (no such API exists today).
>
> **Already shipped (waiting for consumers):**
> - `WEBHOOK_SIGNAL_KINDS` allowlist with `publication_complete` / `engine_silent_zero` / `failure` (`clients_uhy/demo/api/main.py:1086`)
> - `_defer_webhook_signal(...)` helper (`clients_uhy/demo/api/main.py:1164`) — currently has zero call sites in-tree; will get its first wiring once the receiver side exists
> - `emit_workflow_signal` task body with HMAC sign, retry curve, JWT mint (`clients_uhy/demo/api/worker.py:1777`)
>
> **Once landed:** UHY's `generate-workbook` action upgrades from Architecture D → Architecture A: instead of "ask me again," the user gets a HITL DM in the originating chat the moment the workbook is ready. The 3-branch idempotency lookup body shipped in this PR is the building block — it stays as the synchronous lookup path; the async-notify path layers on top.
>
> **Out of scope for v1.0 UHY-convergence handshake**; ticket exists to remind us the emit-side infra is already half-built.

Reference this ticket in the v1.0 PR description so future reviewers understand D was intentional and A is a documented v1.1 plan — not a forgotten dead-end.
