# Change 2 — `upload_and_poll.py` (review copy)

**Spec source:** Iain `REPLY_TO_ADRYANN_DELTAS_ACK_CHANGE2_GO_20260525.md` §3
**Pipeline:** `p1-copy-from-inbox` (remote `2622de32e0f64bae`)
**Draft version saved:** v51 (`c91403fce6394d51`)
**Status:** Built, structurally validated (dry-run save accepted 19 steps). Real execution test scheduled for Tuesday post-Wave-1 per Iain §3.

---

## Why this folder exists

`workspaces/` is `.gitignore`d, so the live WDL with the embedded script cannot be
diffed in PRs. This folder mirrors the SANDBOX script so reviewers can read it
inline alongside the PR description. The **source of truth** is the platform
draft (v51) — fetch it via
`GET /v1/pipelines/2622de32e0f64bae` to see the exact `upload_files[0].content`.

---

## What it replaces

The legacy `eval_and_commit.py` (still on platform via `git log` v50 + earlier
versions) used the deprecated `/upload-and-evaluate` + `/commit` pair. Per the
convergence VM v0.4 contract, these endpoints are gone. The replacement
`upload_and_poll.py` calls the new three-step chain:

| # | Endpoint | Purpose |
|---|----------|---------|
| 1 | `POST /api/v1/clients/upload-zip` | Upload + queue ingest in one call (returns `ingest_job_id`) |
| 2 | `GET  /api/v1/ingest/{id}/poll`   | Poll until `status=completed` → returns `harness_job_id` |
| 3 | `GET  /api/v1/workbook/from-harness-async/{harness_job_id}` | Poll until `status=succeeded` → `preparations[0]` has `id` + `federal_credit` + `total_qre` |

Poll cadence: **5s for first 30s, then 15s thereafter, max 45 min** (Iain §3).

---

## Surgical scope

Only the `sbx_evalcommit_init` upload-files entry, the `sbx_evalcommit_run`
exec command (`python /workspace/upload_and_poll.py`), and one new env var
(`FORCE_REPLACE`) changed. All other 16 steps in the widdle are untouched:

- Step 0–3: `sbx_canonicalize_*` + `copyResult` JQ (S3 zip → docstore unchanged)
- **Step 4–6: `sbx_evalcommit_*` ← THE ONLY MUTATION** (script body + filename + 1 env var + timeout 10→50min)
- Step 7: `vmEvalResult` JQ (extracts the same fields off the new stdout)
- Step 8–13: `triageFormatted` JQ + `routeOnTriage` CONDITION + `triageHitl` ESCALATE + `resolveTriageDecision` JQ + `routeAfterTriage` CONDITION + `triageAbortOutput` (HITL chain unchanged)
- Step 14–17: `sbx_trigger_*` + `triggerResult` JQ (Pipeline B trigger unchanged)
- Step 18: `output` OUTPUT_TEXT (final summary unchanged)

---

## Output-shape contract (downstream stability)

The new script emits **the same JSON field-set** the old script emitted, so
`vmEvalResult`, `triageFormatted`, `routeOnTriage`, the `triageHitl` ESCALATE
question, the final `output` OUTPUT_TEXT, and the `sbx_trigger_init` env var
substitution (`{vmEvalResult.folder_name}`, `{vmEvalResult.client_id}`) all
continue to work without modification. Fields:

| Field | Source in new chain |
|-------|---------------------|
| `triage_status` | derived: `GO` / `HITL_REQUIRED` / `VM_INGEST_FAILED` / `VM_PIPELINE_FAILED` / `VM_TIMEOUT` / `error` |
| `go_no_go` | `go` if step 3 succeeded, else `NO_GO` |
| `recommendation_code` | `PROCEED` / `FORCE_REPLACE_DECISION` / `VM_RETRY_REQUIRED` |
| `folder_name` | `ingest_body.canonical_dir_name` (fallback: resolved zip stem) |
| `client_id` | `ingest_body.client_id` |
| `evaluation_id` | `ingest_job_id` (analogous to old `evaluation_id`) |
| `harness_job_id` | new — surfaced for debugging / re-poll convenience |
| `engine_status` | `completed` / `engine_silent_zero` / `failed` / `skipped` |
| `total_qre`, `federal_credit` | `preparations[0].{total_qre, federal_credit}` |
| `s174_sre` | `null` (not in new VM shape; harmless — old downstream OUTPUT_TEXT prints "None") |
| `prep_run_id` | `preparations[0].id` |
| `elapsed_seconds` | derived from `events[].at` (started → terminal) |
| `blockers` | populated for any `NO_GO` outcome, surfaced to existing `triageFormatted` JQ + HITL |

---

## 409 force_replace handling

Per Iain §3:
> **409 → escalate via existing triage HITL pattern (force_replace decision)**

Implementation:

1. New `FORCE_REPLACE` workflow_argument (default `"false"`)
2. On upload-zip 409: script emits `triage_status=HITL_REQUIRED` with a `blockers`
   entry telling the operator: *"Directory already exists on VM. To overwrite,
   re-trigger this pipeline with `FORCE_REPLACE=true`."*
3. Existing `routeOnTriage` CONDITION sees `go_no_go=NO_GO` → routes to existing
   `triageHitl` ESCALATE
4. Operator picks `abort` → pipeline exits cleanly; the operator manually re-runs
   with `FORCE_REPLACE=true` in `workflow_arguments`

**v1.1 followup candidate:** auto-retry inside the same pipeline run when
operator says "force_replace". For v1.0, the manual re-trigger is a 1-line
workflow_arg flip and matches Iain's "existing triage HITL pattern" minimum.

---

## Test status

| Tier | Status | Notes |
|------|--------|-------|
| JSON syntax / `ast.parse` | ✅ | 434 lines, parses clean |
| `save_pipeline_draft --dry-run` | ✅ | 19 steps accepted |
| `save_pipeline_draft` (real) | ✅ | v51 saved to platform draft |
| Real execution against Bridge Organics zip | ⏳ | Tuesday post-Wave-1 per Iain §3 — needs zip locally OR Iain runs from his side |

---

## Known deviations from Iain's §3 sketch (none material)

1. **`workstream_id_hint` is forwarded as a multipart field** when the env var is
   substituted (matches the old `eval_and_commit.py` pattern; preserves Pipeline B
   workstream context). Iain's sketch only listed `file/dir_name/tax_year/
   auto_ingest/force_replace`. Forwarding hint is non-breaking and consistent
   with the legacy contract.
2. **`engine_silent_zero` engine_status is inferred** when `preparations` is empty
   OR `total_qre` and `federal_credit` are both `null` after `status=succeeded`.
   This matches Bridge Organics' expected "silent_zero" shape from
   `adryann_smoke.sh` line 60 (`EXPECTED_SHAPE="silent_zero"`).
3. **`s174_sre` is `null`** in all cases. The new VM shape does not surface s174;
   the legacy `eval_and_commit.py` got it from `engine.s174_sre`. Downstream
   `output` OUTPUT_TEXT will print "None" for that slot — graceful degradation.
4. **Timeout bumped 10min → 50min** on the SANDBOX init step to give the 45 min
   internal poll cap headroom for tear-up/tear-down.
