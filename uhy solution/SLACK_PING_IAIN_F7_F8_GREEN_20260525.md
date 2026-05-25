# Slack ping to Iain — Surface 1 + Surface 2 done, handoff doc in clients_uhy

**To paste into `#adopt-uhy` (or wherever you DM Iain).**

---

```
Iain — picking up your DELTAS_ACK_CHANGE2_GO reply. Status:

🟢 SURFACE 1 — generate-workbook action (v12 DRAFT on action 96a83403)
   • All 3 deltas ACKed — keeping as canonical, shipping
   • test_runner --all: 4/4 green in 50s against echo-summit staging
       Case A      (Complete Automation TY 2025)  ✅ 8.4s  $264K credit + working download URL
       Case A-fold (Bridge Organics TY 2025)      ✅ 8.5s  $0/$0/empty (engine_silent_zero)
       Case C      (Akervall TY 2025, failed run) ✅ 10.0s retry prompt
       Case D-Err  (ZZZ Nonexistent TY 2099)      ✅ 23.2s graceful "no corpus dir"
   • Per your §2: "4/4 IS the smoke." DO NOT publishing v12 — v11 stays live on prod
     swifty-panda per Wave-2 plan.

🟢 SURFACE 2 — p1-copy-from-inbox pipeline (v51 DRAFT on pipeline 2622de32e0f64bae)
   • Built per your §3 spec NOW, not waiting:
       1. POST /api/v1/clients/upload-zip                  → ingest_job_id (200+idempotent
          / 202 / 409+FORCE_REPLACE handled via existing triage HITL)
       2. GET  /api/v1/ingest/{id}/poll                    → harness_job_id when completed
       3. GET  /api/v1/workbook/from-harness-async/{id}    → preparations[0]{prep_id,
          federal_credit, total_qre} when succeeded
   • Poll cadence: 5s × first 30s, then 15s, max 45 min wall-clock
   • Surgical scope: 3 of 19 widdle steps mutated, 16 byte-identical to v50
   • Output-shape preserved → existing vmEvalResult JQ / triageHitl ESCALATE /
     Pipeline B trigger / final OUTPUT_TEXT all unchanged
   • Tests: ast.parse green, save_pipeline_draft --dry-run green,
     save_pipeline_draft real green (v51 on platform)

📄 HANDOFF DOC for your review (matches your HANDOFF_PHASE_4_PR_* convention):
   adoptai/clients_uhy → branch docs/adopt-surface1-surface2-handoff-iain-20260525
   docs/HANDOFF_ADOPT_SURFACE1_SURFACE2_FOR_IAIN_20260525.md
   Covers: TL;DR table, both surfaces, 3 deltas ACKed, test evidence, F7+F8
   findings, output-shape contract preservation, 5 bugs (added is_last_step
   per your §1 Δ2), 5 v1.1 tickets, Wave-2 note, diagnostics footnote on
   staging harness_jobs 31+32, 3 open questions for you.

🔍 To inspect the actual WDL drafts (abcd is direct-publish, no PR in that repo):
   • Action v12  : GET /v1/actions/96a83403-3b0f-4205-9e2b-676510c87766?version=12&allow_draft=true
   • Pipeline v51: GET /v1/pipelines/2622de32e0f64bae?version=51&allow_draft=true
   • Script mirror (for git-side review of upload_and_poll.py):
     abcd → branch feat/adopt-surface1-surface2-architecture-d
     uhy solution/change-2-upload-and-poll/upload_and_poll.py + README.md

OPEN QUESTIONS (full context in §"For you to weigh in" of the handoff doc):
   1. Bridge zip for Tuesday's exec test — you run adryann_smoke.sh staging
      Bridge from your side, or DM me the zip / S3 URL and I run it via the
      pipeline path?
   2. s174_sre in /workbook/from-harness-async response — file T-S174 against
      clients_uhy or already on your convergence v0.5+ roadmap?
   3. GET /api/v1/preparations filter params silently ignored — server-side
      fix on roadmap or plan around no-fix? (SANDBOX does full-list fetch
      every action invocation as workaround.)

Nothing blocking on my side. Carry on with adryann_smoke.sh whenever —
completely independent of any Adopt-side work per your §2.
```

---

**Notes for the sender (Adryann):**

1. The HANDOFF doc is on the `docs/adopt-surface1-surface2-handoff-iain-20260525` branch of `adoptai/clients_uhy`. If Iain wants it on `dev`, he can FF-merge or you can rebase/merge from your side.
2. The "5 v1.1 tickets" count includes the original 2 (`T-Case-E`, `T-Event-Triggered`) + 3 new ones surfaced by Change 2 (`T-S174`, `T-FR-AutoRetry`, `T-Deliverables-Step`). HANDOFF doc has all 5.
3. The "5 platform bugs" count matches Iain's §5 table exactly (his addition of `is_last_step` brought it to 5).
4. If Iain has already pinged about the Bridge zip access, drop question 1.
5. abcd is `.gitignore`d for `workspaces/` so there's no PR to open there — direct-publish via `save_*_draft.py`/`publish_*.py` is the deployment surface. The abcd branch `feat/adopt-surface1-surface2-architecture-d` exists as a paper trail for the spec docs + script mirror only.
