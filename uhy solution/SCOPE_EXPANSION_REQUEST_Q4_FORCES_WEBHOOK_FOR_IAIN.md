# Scope expansion request — Q4 forced our hand. Need re-ACK before branching.

> **To:** Iain
> **From:** Adryann (+ Claude)
> **Date:** 2026-05-24 night
> **Re:** Iain's `REPLY_TO_ADRYANN_PR_PLAN_20260524.md` § Q4 → "only add the webhook if Q4 forces our hand (it might)"
> **Asks:** ACK or push back on adding **Architecture A** (worker signal + receiver pipeline) to the same v1 PR.

---

## TL;DR

**Q4 answer came back: chat-agent timeout is 3 minutes.** Bridge / BioPro take 3–8 min, Reinhart ~14 min, Complete Automation ~35 min — **no client run completes inside the timeout.**

**Important nuance:** Rather than have the action try to outrun the 3-min timeout, **the action returns immediately** (sub-second) after firing the trigger — just a "your workbook is being prepared" message. The 3-min timeout never bites because we never wait on it. The actual workbook delivery is the receiver-pipeline's job (DM-when-ready hook), opening a new HITL in the originating chat with the download URL.

So the ask is *not* "Q4 forces the hand on timing." It's "**Q4 forces the hand on UX shape**" — we can't ever have the action return the URL inline (under any timeout), so the choice is:

- **Architecture A (this ask):** action returns immediate "preparing…" + receiver pipeline opens new HITL with URL when ready. Proactive UX. Crosses your "no worker touch / no v1 receiver pipeline" lines.
- **Architecture D (your ACK'd minimum):** action returns immediate "preparing… ask me again in N min." User manually re-invokes. Action's idempotency lookup detects the in-flight job and either returns "still preparing" or the URL if done. Crude UX (user pings). Stays inside your lines.

Asking for re-ACK on A before I branch.

---

## §1 — What Q4 actually is

| Datum | Value | Source |
|---|---|---|
| Adopt chat-agent max sync response time | **3 minutes** | Adryann confirmed with the platform team / docs |
| Bridge Organics pipeline | ~3–4 min e2e | dispatch §12 |
| BioPro pipeline | ~5–8 min e2e | weekend soak ledger |
| Reinhart pipeline | ~10–14 min e2e | weekend soak ledger |
| Complete Automation pipeline | ~35 min e2e | dispatch §12 |

→ **No client completes inside the 3-min timeout.** Chat-side polling is mathematically off the table for any real call.

---

## §2 — Implication mapped onto your Q4 scenarios

You wrote three scenarios in your reply:

| Scenario | Your stance | Now-relevant given 3-min |
|---|---|---|
| ≥ 45 min timeout | "ship as-is, action does full poll-to-done" | N/A — we're not chasing this anymore (decided not to fight timeout at all) |
| 5–45 min timeout | "ship a hybrid… DM-when-ready hook — not v1" | The DM-when-ready hook is what we want; the "agent polls up to N min then deferred-responds" half is unnecessary because we just return immediately. |
| < 5 min timeout | "split the action into two… **avoid this if you can**" | We avoid this by going fire-and-immediate on ONE action + receiver-pipeline-handles-delivery (= Architecture A below). NOT the start/status split. |

So we land effectively in your "5–45 min hybrid" branch but stripped of the agent-side polling — which simplifies the action body and means **timeout-bracket math is irrelevant**: the action always returns in <1 sec regardless of how long the worker takes.

---

## §3 — Proposed Architecture A (what we want to add to this PR)

```
[ user types "generate workbook for BioPro 2025" in chat ]
      │
      ▼
[ generate-workbook action ]  ← always returns in <1 sec
  1. Idempotency lookup: GET /preparations/{client}/{year}
     → if a run is queued/doing → return immediately with the EXISTING harness_job_id:
       OUTPUT_TEXT: "Your RTC workbook for **BioPro TY 2025** is already being prepared
                     (started ~3 min ago). I'll surface the download link in this chat
                     when it's ready."
     → if a completed deliverable exists → return its download URL directly
     → else: continue
  2. POST /workbook/from-harness-async {client_name, tax_year, originating_workstream_id}
     → On 200/202 with {harness_job_id, poll_url}: continue to step 3
     → On 4xx (validation): OUTPUT_TEXT the server's error message (no preparation
       was started, no receiver-pipeline HITL will fire — synchronous failure)
     → On 5xx: OUTPUT_TEXT a retry-prompt message (action errors out)
  3. OUTPUT_TEXT immediately:
     "Your RTC workbook for **BioPro TY 2025** is being prepared.
      I'll surface the download link in this chat when it's ready."
     Resolve the chat turn.

  (NO polling. NO waiting. Action lifetime ≈ idempotency-GET + async-POST ≈ <1 sec.)

  ──── time passes ────

[ worker finishes generate_workbook_task ]
   → calls existing _defer_webhook_signal with kind="workbook.ready"
     payload includes {originating_workstream_id, preparation_id, download_url,
                       client_name, tax_year, federal_credit, total_qre}
   → also kind="workbook.failed" on the DLQ stamp path

[ new receiver pipeline: uhy-workbook-ready-notify ]
   → subscribed to workbook.ready / workbook.failed signal_kinds
   → opens HITL ESCALATE in the originating workstream:
     "Your RTC workbook for **BioPro TY 2025** is ready.
      Federal credit: $28,292.31. [Download workbook]({signed_url})  (expires 7d)"
   → resolve-cancel terminal (no further action expected)
```

### What this requires that's NOT in the ACK'd plan

| Surface | Change | Size |
|---|---|---|
| `clients_uhy/demo/api/worker.py` | Add `workbook.ready` + `workbook.failed` to `WEBHOOK_SIGNAL_KINDS`. In `generate_workbook_task` post-success/failure, call existing `_defer_webhook_signal(kind, payload)`. Payload threads originating_workstream_id (already in scope per dispatch finding #2 work). | ~30 LoC, single function. Pattern is identical to existing `dlq.artifact_task_failed` emission. No new schema, no migrations. |
| `generate-workbook` action | Add idempotency lookup step + immediate-return branch (steps 1, 3 above). NO polling step. Action body becomes simpler than current sync version. | +2 WDL steps (idempotency lookup + branching CONDITION); replaces the current /generate-sync POST |
| `generate-workbook` action | Thread originating_workstream_id from chat context into `/from-harness-async` body so worker can echo it back in the signal payload | +1 input mapping |
| New pipeline `workspaces/uhy-prod/pipelines/uhy-workbook-ready-notify/widdle.json` | Listens to `workbook.ready` / `workbook.failed` signal_kinds, opens HITL ESCALATE in originating_workstream_id from payload | ~50-line WDL |

### What stays exactly as you ACK'd

- `p1-copy-from-inbox` migration to `/upload-zip` + polling chain (Change 2) — unchanged
- The `generate-workbook` URL switch swifty-panda → echo-summit — unchanged
- SANDBOX-script preference for the new ingest step — unchanged
- Test plan (BioPro → Bridge → Reinhart, no Complete Automation in joint smoke) — unchanged
- Rollback paragraph for PR description — unchanged plus: "remove the receiver pipeline if needed; signal emission is no-op if no pipeline subscribes"
- The two non-blocking pushbacks (5/15s polling cadence; action-side idempotency) — folded in as designed
- Branch off `dev`, PR back to `dev`, name `feat/adopt-surface1-handshake` — unchanged

### What gets DROPPED from the plan

- Nothing.

### What stays explicitly out of scope (still)

- No DB schema changes
- No new migrations
- No API endpoint additions (uses existing `_defer_webhook_signal` infra)
- No HITL surface changes (`uhy-prep-review`, `uhy-hitl-task-review`, `uhy-suralink-hitl-agent`) untouched
- No prod / swifty-panda reconciliation (staging-only)

---

## §4 — Why we think this is still v1 not v1.1

Three reasons:

1. **3 min < any client's pipeline time.** It's not a Complete Automation edge case — *every single call* needs async delivery. Shipping D first means every interaction has the crude "ask me again" UX; we'd be re-PRing the UX fix immediately. Cost of "ship D then upgrade in v1.1" ≈ cost of "ship A in v1," with bad UX in between.
2. **Action body is actually SIMPLER than the current sync version.** No /generate-sync wait, no inline file generation, no 35-min timeout risk. Just two REST steps (idempotency GET + async POST) and an OUTPUT_TEXT. The complexity moves to the receiver pipeline — but that's where it belongs (it owns the lifecycle of "workbook is ready").
3. **The worker piece is tiny.** Single function in `generate_workbook_task`, existing pattern (`_defer_webhook_signal`), no schema, no new infra. Risk surface ≈ a couple of unit tests. We already use this pattern for `dlq.artifact_task_failed`.

If you disagree on any of these, happy to ship D first and follow up. But D means every user interaction is crude, not just the rare ones — that's why we'd rather ship A.

---

## §5 — Open Qs for you (this round)

| # | Question | Why I'm asking |
|---|---|---|
| A | ACK Architecture A in v1, OR ship D first? | The headline decision. |
| B | If A: is it OK that I add the new `workbook.ready` / `workbook.failed` to your `WEBHOOK_SIGNAL_KINDS` vocab in `main.py`? Or does the vocab live somewhere else now in convergence? | Don't want to dead-end on a stale vocab list. |
| C | If A: should the receiver pipeline `display_label` template include the federal_credit number, OR keep it generic for failure-mode parity? (Failure mode = HITL with "Workbook generation failed for {client}…" — no credit number to show.) | UX consistency. |
| D | If A: idempotency lookup endpoint — is `GET /preparations?client_name=X&tax_year=Y` the right shape, or should we use `GET /clients/{client_id}/preparations`? Or just enumerate `/preparations/{prep_id}` and filter? | Picking the right read endpoint for the dedupe path. |
| E | Monday 10:00 ET joint smoke shape unchanged? (BioPro → Bridge → Reinhart, all async-pattern now since none fit the 3-min timeout) | Just confirming. |

---

## §6 — Ready to branch and start once you bless

If ACK on §5 Q-A: I branch `feat/adopt-surface1-handshake` off dev, ship draft PR by Monday morning, joint smoke 10:00 ET as planned.

If push-back: tell me what to drop and I'll re-cut.

If radio-silent: I'll start on the agreed Iain-ACK'd minimum (action contract change + Pipeline A ingest migration) and stub the receiver-pipeline path so we can drop it in cleanly once you bless.

🙏 thanks. Last decision gate before code touches.
