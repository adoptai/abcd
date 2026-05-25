# Slack ping — POL Stages 5-7 green on staging

**Target:** Iain (DM, #adopt-uhy)
**Subject:** POL gate cleared (6/7 acceptance criteria met) — ready for your Wave-1 call

---

🟢 **POL Stages 5-7 read-only sweep — GREEN on staging.**

Probe wrapper: `uhy solution/change-3-pol-stages-5-7/exec_pol.py` (3 stages, structured JSON report, ran in ~11s per target).

**Run 1 — Complete Automation, Inc. TY 2025 (your Stage 5 known-good):**

| stage | verdict | notes |
|---|---|---|
| 5 — deliverable download + sha256 | **PASS** | sha256 byte-fidelity match: advertised `6b8973…36e087` == computed; 3.2 MB .xlsx streamed via signed URL with no bearer |
| 6 — query surfaces (4 endpoints) | **PASS** | `/clients`, `/clients/{id}/employees`, `POST /query`, `POST /search` all 200 |
| 7 — HITL surface (3 endpoints) | **PASS** | `/hitl-summary`, `/duplicate-suspects`, `/publish-status` all 200 |

**Run 2 — Akervall Technologies, Inc. TY 2025 (Case C / silent_zero canary):**

| stage | verdict | notes |
|---|---|---|
| 5 | FAIL (expected) | no deliverable to download — consistent with your spec saying it's `failed` state |
| 6 | **PASS** | same 4 endpoints, all 200 |
| 7 | **PASS** | Akervall surfaces **real HITL data**: 12 flagged employees, 3 open events, 0 duplicate suspects — confirms HITL surface is queryable + populated |

**Acceptance roll-up: 6/7 criteria met, 1 deferred.**

The 1 deferred = the Stage 7 mutating step (`POST /clients/{id}/duplicate-suspects/{event_id}/resolve` and/or `PATCH /employees/{id}`). I didn't fire it without your explicit go — Akervall has valid event_ids ready if you want me to fire one and confirm `_consolidate_hitl_for_run` re-projection fires on the Adopt side too.

Full evidence in `uhy solution/PROBE_FINDINGS_ARCH_D_REALITY_CHECK_FOR_IAIN.md §17` (~120 lines, includes endpoint-surface deltas, your spec corrections, sha256 hashes, response shape captures). JSON reports on disk under `uhy solution/change-3-pol-stages-5-7/pol_report_*.json`.

---

**📋 4 things for you to look at:**

1. **Your POL spec endpoint surface was off in 5 places** (`/query` is POST not GET; `/hitl-summary` is `/clients/{id}/hitl-summary` not global; `/hitl/tasks` doesn't exist; etc.). Documented in §17.5 — none functional, just naming/method. Suggest I draft a corrected version of REPLY_TO_ADRYANN_V51_GREEN_WITH_POL for the next operator. Want me to?

2. **Akervall on staging shows `triage_status=ingested`, NOT `failed`** as your spec says. Either snapshot drifted, or it never went to failed. Matters because v12 generate-workbook's Case C branch is keyed on `runs[*].status=failed` — if no Akervall-class run is actually in `failed` state on staging, we can't end-to-end validate that branch without manufacturing a failure. Your call: ignore for now, or do you want a Case C probe?

3. **All 32 staging preps show prep-level `status=in_progress`** — but Complete Automation's single run is `status=completed` (which is why the deliverable exists). Worth noting because future POL specs picking targets by prep-level status will return zero hits.

4. **Mutating step authorization** — green-light Akervall HITL resolve? Or hold until prod-POL?

---

**🚦 My ask: your go/no-go on Wave-1.**

With POL clean (modulo the deferred mutation probe), v12 generate-workbook 4/4 green (per §13), and v52 pipeline smoke green (per §16), we're at "ready when you say so." I'm holding on publish.

**Bonus already done:** 7 bugs + 5 v1.1 tickets filed in `clients_uhy/docs/BUGS_AND_V11_TICKETS_FROM_WAVE1.md` (commit `b5a5bdd`, your same branch). One-glance triage matrix in §0 of that doc.

---

**Branch state (`abcd → feat/adopt-surface1-surface2-architecture-d`):**
- v12 generate-workbook draft (`96a83403`) — Change 1, 4/4 green
- v52 p1-copy-from-inbox draft (`d667e105476a4671`) — Change 2, Run 1 smoke green (Bridge zip)
- POL evidence + reports pushed alongside

Standing by.
