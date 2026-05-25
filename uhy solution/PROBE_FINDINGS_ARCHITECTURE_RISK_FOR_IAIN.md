# Probe findings — flagging an architecture risk before branching

> **To:** Iain
> **From:** Adryann (+ Claude)
> **Date:** 2026-05-24 night
> **Re:** Your "Ping me only if a probe surfaces something that breaks the architecture" — Probe #2 did.
> **Asks:** Decide between three fallback architectures (B / D / wait-for-A''). NO branch opened yet.

---

## TL;DR

Probe #1 (worker.py) is mostly green — minor sizing correction. **Probe #2 (receiver pipeline) is red**: the "pipeline subscribes to webhook signal" pattern you referenced **doesn't exist in the platform yet**. All 6 `uhy-prod` pipelines are `schedule_type: "manual"`. The webhook intake that exists (`adoptwebui/backend/app/routes/webhook.py`) triggers **actions**, not pipelines, and creates a **new conversation**, not opening a HITL in the originating chat. The event-triggered actions service is a draft spec I wrote in March 2026 that's still not built (and uses a different signal vocabulary anyway).

Per your reply: *"If no precedent exists, flag immediately — that changes the risk profile."* Flagging.

Three viable fallbacks below — I want your call before I touch anything.

---

## Probe #1 — worker.py emit pattern (mostly green)

What I confirmed by reading the code:

| Item | Expected | Found | Notes |
|---|---|---|---|
| `WEBHOOK_SIGNAL_KINDS` allowlist | `main.py:1086` | ✅ `main.py:1086` | Includes `publication_complete`, `engine_silent_zero`, `failure`, `hitl_ready`, etc. |
| `_defer_webhook_signal` helper | `worker.py:1780` | Actually `main.py:1164` | Same shape you described. Signature: `(db, *, kind, payload, target_url) -> (external_id, proc_id)`. Required payload keys: `preparation_id`, `client_name`, `tax_year`. Extras go in `payload_extra`. Docstring even mentions `final_credit`/`final_qre` for `publication_complete` specifically — you'd already designed the payload shape. |
| Task-tail signal emit precedent | `_record_artifact_dlq` at `worker.py:2110` | ⚠️ That's actually just a DB-row stamper (`UPDATE job_status_extra SET signal_kind=...`). The actual emit precedent is `_defer_dlq_alert_async` at `worker.py:2260`, called from the `WebhookRetryStrategy.get_retry_decision` callback — NOT a task-tail. | The "task-tail emits signal" pattern would be net-new code, not a copy of existing pattern. Not a problem, just clarifying. |
| `_defer_webhook_signal` call sites | Some | **Zero in-tree call sites.** | The helper, the task body, the retry strategy, the JWT mint, the DLQ alert — all shipped — but nobody calls it from anywhere yet. My addition would be the FIRST production wiring. Not a risk, but the "infra is in place" line is slightly more aspirational than your reply implied. |
| `generate_workbook_task` success-tail | `~line 1619` | `worker.py:1734` (function at `1331`) | Clean insertion point right after `db.commit()` on line 1725. All needed data is in scope (`run_preparation_id`, `deliverable_id`, `federal_credit`, `total_qre`, `workbook_path`). |

**Real worker.py touch size revised**:
- 1 `_defer_webhook_signal(...)` call for `publication_complete` at success-tail: **~20 LoC**
- 1 `_defer_webhook_signal(...)` call for `engine_silent_zero` in the silent-zero branch (line 1426-1453, currently `return skip_result` with no emit): **~15 LoC**
- 1 `_defer_webhook_signal(...)` call for `failure` — where? The task raises exceptions; there's no exception-handler block emitting a signal. We'd need either an outer try/except wrapper in the task body, or use `_record_artifact_dlq`'s strategy callback path which would emit `dlq.artifact_task_failed` instead of `failure`. Iain — preference? **~20–30 LoC depending on which path.**
- Threading `originating_workstream_id` from the action → API endpoint → `_defer_run_pipeline` (or wherever the upstream defer happens) → `run_pipeline_task` kwargs → `generate_workbook_task` kwargs → payload. **~10 LoC across 4 files, but crosses your "no worker logic changes beyond the one signal emit" line** (you said *"keep your hands off the actual pipeline/workbook tasks"*).

**Total: ~60–80 LoC, not 15.** Still small/medium. Architecturally fine. Just bigger than your estimate.

---

## Probe #2 — receiver pipeline pattern (RED — flagging)

What I looked for: a pipeline in `workspaces/uhy-prod/pipelines/` that subscribes to a signal_kind like `hitl_ready`, per your hint *"the HITL escalation pipeline that subscribes to `hitl_ready` (lives in the suralink-hitl-agent area)."*

What I found:

| Surface checked | Result |
|---|---|
| All 6 `uhy-prod` pipelines (`p1-copy-from-inbox`, `uhy-hitl-task-review`, `uhy-index-docs`, `uhy-prep-review`, `uhy-suralink-ingest-per-zip`, `uhy-suralink-ingest-v2`) | All `schedule_type: "manual"`. Zero `signal_kind` / `event_type` / `subscriptions` / `trigger` keys in either `pipeline.json` metadata or `widdle.json`. None subscribe to anything. |
| `cli/wdl_common/pipeline_client.py` API surface | Supports `schedule_type=manual`, `schedule_type=cron` with `cron_expr`. **No event/signal subscription field.** No mention of `hitl_ready` or webhook intake. |
| Adopt platform webhook intake (`adoptwebui/backend/app/routes/webhook.py`) | Exists, but: triggers an **ACTION** (not a pipeline), creates a **NEW conversation** (not the originating one), and routes to `reasoning_workflow_url + /api/v1/workflows/execute-analysis` with a `WEBHOOK_USER_ID` user. Useful as a wedge for option A' below — but NOT a pipeline-trigger mechanism. |
| `adoptai-workflows` event-triggered actions code | **Doesn't exist.** Only thing I found is `docs/scheduled-event-triggered-actions-spec.md` — that's MY own draft spec from March 2026 (status: "Draft"), describing a service that would need to be built. Its event vocabulary (`document.uploaded`/`document.updated`/`document.deleted`) doesn't overlap with worker signal_kinds (`publication_complete`/`engine_silent_zero`/`failure`) anyway. |
| `hitl_ready` subscriber pipeline you referenced | **Cannot find one.** I checked uhy-prod fully. If it lives elsewhere (a different env / a different repo), I need a pointer. |
| Any "open HITL in existing workstream from outside" API | **Cannot find one.** HITL escalation is currently always initiated from within a pipeline run (`db_org_pipeline_run.metadata` carries `workstream_id` per the doc you linked, but that's a pipeline that's already running — not a way to inject a HITL into an arbitrary workstream from outside). |

**Conclusion**: Architecture A as I drew it can't be built today. The worker can emit the signal (infra exists), but nothing on the Adopt platform is listening for it, and even if I built a polling consumer in a pipeline I don't have an API to open a HITL in the originating chat.

---

## Three fallback architectures

| | **A' — webhook → notify action** | **B — polling pipeline** | **D — Iain-original-minimum** |
|---|---|---|---|
| Worker fires signal | YES (publication_complete via `_defer_webhook_signal`) | NO (no signal needed) | NO |
| Receiver | NEW action `notify-workbook-ready`, triggered via existing `/webhook/{org_id}/action/{action_id}` intake | NEW pipeline `uhy-workbook-poll-notify` with `schedule_type=cron` (every 2 min), reads `preparation_deliverables` for new completions | None |
| Worker.py touch | ~20 LoC (just the publication_complete emit) | Zero | Zero |
| Threading `originating_workstream_id` | YES (worker → signal payload → action invocation metadata) | YES (but stored in `preparation_deliverables` side table or in a new tiny `workbook_notify_queue` table; pipeline READ_FROM_DB) | NO |
| Opens HITL in originating chat | **BLOCKED** — webhook intake creates a NEW conversation; would need a new "post to existing workstream" API I can't find | **BLOCKED** — same issue: HITL ESCALATE inside a pipeline opens a HITL in the CURRENT pipeline's workstream, not an arbitrary one | NO HITL needed; user re-asks |
| User-visible UX | Notification arrives as a NEW chat thread ("Your BioPro workbook is ready: [link]"). Different thread than where they asked. | Same as A'. | Same chat thread. User has to re-ask. |
| Net-new platform infra required | Just one new action + a webhook secret per org | One new cron pipeline; possibly a tiny side table for "notified" flag | Nothing |
| Crosses your "no worker touch" line | YES (1 signal emit) | NO | NO |
| Crosses your "no new pipeline in v1" line | NO (action, not pipeline) | YES | NO |
| Crosses your "no API endpoint additions" line | Possibly — depends on whether "post to existing workstream" needs a new API | NO | NO |
| Feasible in v1 sprint | Probably (~3 days WDL + worker + receiver action) | Probably (~3 days but new pipeline + side state design) | YES (1 day, just edit the existing action) |

**My recommendation**: **D for v1.0**, plus an explicit v1.1 task to either build the event-triggered actions service properly (my March spec) or design the "post to existing workstream" API that A' needs. Reasoning:

1. The whole point of Q4=3min was that *any* delivery mechanism works as long as the agent returns fast. D delivers fast. The UX gap (user re-asks) is real but it ships today against a real architectural constraint.
2. A' and B both block on a piece of platform infra we don't have ("post HITL into existing workstream" API). Best case: 1 week to design + ship that API; worst case: weeks.
3. D doesn't preclude A' or B later — the action's idempotency lookup is a building block both would need anyway.

**If you want A' anyway**, please confirm where the "post HITL/message into existing workstream" API lives, or accept that we'd be building it in this PR.

**If you want B**, please OK adding `schedule_type=cron` to the pipeline pattern and a side table for "notified" tracking — that's a meaningful net-new piece.

---

## What changed vs. the previous ACK

You ACK'd Architecture A in `REPLY_TO_ADRYANN_ARCHITECTURE_A_ACK_20260524.md` on the assumption that:
> "the receiver pipeline is genuinely new, but the pattern's well-trodden"

The pattern isn't well-trodden — it doesn't exist. Either I'm searching the wrong place (please point me) or the assumption was wrong (please pick a fallback).

---

## Questions

| # | Question | Why |
|---|---|---|
| 1 | Where does the `hitl_ready` subscriber pipeline live? Different env? Different repo? | If it exists, I can template off it; my probe missed something. |
| 2 | Is there a "post message/HITL into existing workstream from outside" API? | A' and B both need it; without it, neither delivers to the originating chat. |
| 3 | Given the above: which fallback (A', B, D)? | The decision. |
| 4 | If A': worker touch grows to include threading workstream_id through the chain (~10 LoC across 4 files). OK with that? Bumps total to ~80 LoC. | Confirming scope. |
| 5 | If D: do you still want the worker `publication_complete` emit at all, as v1.1 prep? | Optional but cheap (~20 LoC). |
| 6 | Monday 10:00 ET joint smoke shape unchanged regardless of fallback? | The smoke is now mainly Surface 1 (Pipeline A migration); Surface 2 in D is just the action body change which is verifiable in <1 sec. |

---

## What's NOT in doubt (regardless of fallback)

- Surface 1 (`p1-copy-from-inbox` legacy → convergence ingest chain) is unchanged
- The `generate-workbook` action contract change (sync `/generate-sync` → async `/from-harness-async`, fire-and-immediate body, URL swifty-panda → echo-summit) is unchanged
- Deleting the two stale local `generate-workbook` checkouts is unchanged
- Pushbacks (5/15s polling cadence on the pipeline side, action-side idempotency lookup) are unchanged
- Monday 10:00 ET joint smoke target is unchanged
- Branch name `feat/adopt-surface1-surface2-handshake-architecture-a` is wrong (architecture-A may not survive); rename TBD based on your call

---

Ping back when you've picked. I'll either ship the updated PR plan or restart from a different architecture. No code touched yet. Standing by.

🙏 better to find this in a 30-min probe than in a 3-day PR review.
