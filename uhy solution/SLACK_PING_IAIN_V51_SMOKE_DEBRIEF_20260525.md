# Slack reply to Iain — v51 EXEC smoke debrief (post-UNBLOCK)

**To paste into `#adopt-uhy` (or DM).**

---

```
Iain — v51 exec smoke landed against staging per your UNBLOCK doc.
Headline: 🟢 GREEN on all 6 acceptance criteria, but 4 surprises you'll
want to know.

(Full writeup w/ trace, JSON, S3 keys, JQ output:
 abcd → branch feat/adopt-surface1-surface2-architecture-d
 uhy solution/PROBE_FINDINGS_ARCH_D_REALITY_CHECK_FOR_IAIN.md §16)

▰▰▰ RAW RESULTS ▰▰▰

Run 1  019e5fe7-1cb9-78ac  status=success         dur=25:10  path=GO (happy)
Run 2  019e5ff9-9b86-7b39  status=awaiting_human  dur= 6:44  path=VM_HTTP_502→triageHitl (graceful error)

Run 1 fired Pipeline B (uhy-index-docs) successfully via trigger_index.py,
matching the v50 contract byte-for-byte downstream of upload_and_poll.

▰▰▰ YOUR 6 CRITERIA ▰▰▰

1. terminal state             ✅  success
2. sbx_evalcommit_run exit 0  ✅
3. JSON shape correct         ✅  triage_status, engine_status, total_qre,
                                  federal_credit, prep_run_id, blockers
4. downstream JQ green        ✅  routeOnTriage→GO→sbx_trigger fired
5. poll cadence visible       ✅  5s first poll → 15s → 30s
6. force_replace=true honored ✅  VM accepted fresh upload

▰▰▰ SURPRISE 1: BRIDGE IS NOT SILENT_ZERO ▰▰▰

Your UNBLOCK §2 expected total_qre=0, federal_credit=0, engine_silent_zero.

Actual Run 1 result:
   total_qre        $892,513.66
   federal_credit   $ 42,305.15
   engine_status    "completed"
   prep_run_id      null   ← confirms your T-S174 still pending
   s174_sre         null   ← same

Bridge produced real R&D credit. Either your silent_zero expectation was
from an earlier convergence-engine version, or force_replace=true triggered
a fresh extract that differs from your prior smoke. Doesn't change Change 2
validation — pipeline correctly routed GO — but your mental model of Bridge
needs updating.

▰▰▰ SURPRISE 2: PROCRASTINATE QUEUE DELAYS sbx_evalcommit init→exec by 6-25 MIN ▰▰▰

This is the big one. Run 1's sbx_evalcommit_init returned exit 0 at 16:10:59.
sbx_evalcommit_run didn't dispatch until 16:35:49 — 24:50 silent gap with
the run sitting at status=running.

Compare: sbx_init→sbx_canonicalize fired back-to-back in 9 sec.
Only diff: sbx_init has timeout_minutes=5, sbx_evalcommit_init has =50.

Run 2 with timeout_minutes=10 instead of 50 still saw a 6:29 init→exec gap.
So even moderate timeouts trigger delay — appears proportional.

My read: this is the same root cause as your UNBLOCK §1 stuck-job 34 (had
to be poked manually). I think it's also why 5/22 run 019e5071-90f9-741a
sat at "failed" for 70 hours before the stale-run sweeper got it. v50's
reliability has been quietly degrading along this axis.

Wave-1 budget implication: a Bridge-size client needs ~50 min total budget
(25 Procrastinate + 25 harness). Reinhart-size (your §4 expects 8-15 min
harness) needs at least 25-30. v50 worked historically because its
timeout=10 + 5-10 min harness slipped under the radar.

This rises to bug-list severity H (was M). Should I file it against
adopt-platform tonight, or you wanna sit with it first?

▰▰▰ SURPRISE 3: upload_and_poll.py BUG — hardcoded "45 min" in detail string ▰▰▰

Run 2 returned: "Harness pipeline 41 did not reach a terminal status in 45
min (last status='')." — but actual wait was 92 seconds before VM returned
HTTP 502, forcing early exit from _poll_until_terminal.

Also conflates two failure modes:
   - VM_TIMEOUT     = elapsed >= POLL_MAX_S  (real timeout)
   - VM_HTTP_ERROR  = early exit on 5xx       (transient, retry-friendly)

Operators reading the HITL escalation will assume 45 min of polling that
didn't happen. ~20-LoC fix in _make_blocker_detail(). v1.1 candidate,
non-blocking for Wave-1.

▰▰▰ SURPRISE 4: VM 502 ON CONCURRENT BRIDGE LOAD ▰▰▰

Run 2 hit HTTP 502 from VM at /workbook/from-harness-async/41 poll because
Run 1's harness 39 was still cranking. VM doesn't appear to have
concurrency control or per-pipeline queueing. If a UHY scheduled sync drops
5 zips, they'll either serialize or compete for bandwidth and trigger 502s
that escalate to HITL.

v1.1 candidate: VM-side concurrency control OR upstream throttle in
uhy-suralink-ingest-per-zip (queue zips, fan out at controlled rate).

▰▰▰ PLATFORM-SIDE STATE ▰▰▰

p1-copy-from-inbox v50  PUBLISHED (no change — broken since VM swap, but unaffected)
p1-copy-from-inbox v51  DRAFT  c91403fce6394d51  timeout=10  (incorrect — too aggressive)
p1-copy-from-inbox v52  DRAFT  d667e105476a4671  timeout=50  ← smoke-validated, Wave-2 publishes THIS

▰▰▰ DECISIONS FOR YOU ▰▰▰

  Q1 v52 publish gating: happy with timeout=50, or want to push for
     Procrastinate-side fix first and keep timeout lower?
  Q2 Bridge real numbers: changes anything about your Test Suite 1
     / smoke baselines?
  Q3 Wave-1 ship readiness: v12 generate-workbook (4/4 green) + v52
     p1-copy-from-inbox (smoke-validated end-to-end). I'm at "ready
     to publish whenever you say go." Not publishing without your word.
  Q4 Bug #6 (Procrastinate delay) priority: file tonight, or sit?

Bug list now at 7 (added: #6 Procrastinate delay = H, #7 upload_and_poll
detail string = L). Full §16.8 table in PROBE_FINDINGS.

Off your back. Yours.
```

---

**Notes for the sender (Adryann):**

1. The full §16 writeup is the canonical evidence trail — Slack version is the executive summary. Include the path `uhy solution/PROBE_FINDINGS_ARCH_D_REALITY_CHECK_FOR_IAIN.md §16` so Iain can drill in.
2. v52 draft ID = `d667e105476a4671`. Iain can fetch it via:
   `GET /v1/pipelines/2622de32e0f64bae?version_id=d667e105476a4671`
3. Throwaway workstream `4f62ab39-c8a7-422b-a45b-a58c50c32136` ("Adryann v51 smoke - Bridge Organics - 20260525T155334Z") is on staging — Iain can also click through Adopt UI to see the runs (both terminal as of writing).
4. If Iain wants the orphan stuck workflows (`test_mode=true` runs from earlier) cleaned out of Procrastinate, that's a DB poke on his side.
5. If Iain pushes back on Surprise #1 (Bridge real numbers), the Run 1 stdout JSON is reproducible — same zip, same workstream-pattern, same SANDBOX log can be re-fetched from S3.
