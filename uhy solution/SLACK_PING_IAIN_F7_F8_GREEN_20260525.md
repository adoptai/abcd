# Slack ping to Iain — Change 1 4/4 green, Change 2 v51 draft, PR open

**To paste into `#adopt-uhy` (or wherever you DM Iain).**

---

```
Iain — picking up your DELTAS_ACK_CHANGE2_GO reply. Status:

🟢 CHANGE 1 (generate-workbook v12 draft on action 96a83403)
   • All 3 deltas ACKed by you — keeping as canonical, shipping
   • test_runner --all: 4/4 green in 50s
       Case A      (Complete Automation TY 2025)  ✅ 8.4s  $264K credit + working download URL
       Case A-fold (Bridge Organics TY 2025)      ✅ 8.5s  $0/$0/empty (engine_silent_zero)
       Case C      (Akervall TY 2025, failed run) ✅ 10.0s retry prompt
       Case D-Err  (ZZZ Nonexistent TY 2099)      ✅ 23.2s graceful "no corpus dir"
   • Per your §2: "4/4 IS the smoke." DO NOT publishing v12 — v11 stays live on prod
     swifty-panda per Wave-2 plan.

🟢 CHANGE 2 (p1-copy-from-inbox v51 draft on pipeline 2622de32e0f64bae)
   • Built per your §3 spec NOW, not waiting:
       1. POST /api/v1/clients/upload-zip                  → ingest_job_id (200+idempotent
          / 202 / 409+FORCE_REPLACE handled)
       2. GET  /api/v1/ingest/{id}/poll                    → harness_job_id when completed
       3. GET  /api/v1/workbook/from-harness-async/{id}    → preparations[0]{prep_id,
          federal_credit, total_qre} when succeeded
   • Poll cadence: 5s × first 30s, then 15s, max 45 min wall-clock
   • Surgical scope: 3 of 19 widdle steps mutated, 16 byte-identical to v50
   • Output-shape preserved → existing vmEvalResult JQ / triageHitl ESCALATE /
     Pipeline B trigger / final OUTPUT_TEXT all unchanged
   • Tests: ast.parse green, save_pipeline_draft --dry-run green, save_pipeline_draft
     real green (v51 on platform). Real exec test deferred to Tuesday post-Wave-1
     per your §3 — I don't have Bridge zip locally. Want me to spin one up or do you
     have BioPro/Reinhart bundles you can share?

🟢 DRAFT PR OPEN (per your §4)
   <PR URL goes here — paste after gh pr create>
   Description has: Change 1 4/4 evidence + 3 deltas + Change 2 spec + 5 platform
   bugs (added is_last_step→JUMP per your §1 Δ2) + 5 v1.1 tickets + Wave-2 note +
   "job_id 32 during diagnostics" footnote.

OPEN QUESTIONS / DECISIONS NEEDED FROM YOU:
   1. Do you want to run adryann_smoke.sh staging Bridge|BioPro|Reinhart from your
      side, or should I spin up a local zip for Change 2 exec test? (Iain §3 said
      Tuesday — I'm flexible.)
   2. PR description has the test_1..test_4 evidence inline; ping me if you want
      raw trace dumps from workspaces/uhy-prod/actions/generate-workbook/traces/.

Nothing blocking on my side. Carry on with adryann_smoke.sh whenever — completely
independent of this PR per your §2.
```

---

**Notes for the sender (Adryann):**

1. Replace `<PR URL goes here>` with the actual PR URL once `gh pr create` succeeds.
2. The "5 v1.1 tickets" count includes the original 2 (`T-Case-E`, `T-Event-Triggered`) + 3 new ones surfaced by Change 2 (`T-S174`, `T-FR-AutoRetry`, `T-Deliverables-Step`). PR description has all 5.
3. The "5 platform bugs" count matches Iain's §5 table exactly (his addition of `is_last_step` brought it to 5).
4. If Iain has already pinged about the BioPro/Reinhart zips, drop question 1.
