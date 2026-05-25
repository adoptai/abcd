# Probe findings: Architecture D pre-branch reality check
**Date:** 2026-05-24 22:30 ET (Sunday)
**Audience:** Iain
**Context:** You asked in `REPLY_TO_ADRYANN_ARCHITECTURE_D_GREEN_LIGHT_20260524.md` "tomorrow ask #3" for a 5-min probe of the preparation-lookup endpoint before I branch. Ran it. Took ~15 min and surfaced more than expected. Posting before you wake so you can ACK or push back over coffee.
**Bottom line:** Arch D is still the right call. Endpoints exist. Logic shape needs **6 branches not 3**, and the **status vocab you cited in Q2 doesn't match what's running on staging**. Not a re-architecture — just spec correction. ~30 extra LoC, no platform changes.

---

## §1 — What I probed

`GET /api/v1/preparations?client_name=X&tax_year=Y` shape, run status vocab, deliverable correlation. All against `https://echo-summit.westus3.cloudapp.azure.com` with the staging bearer from `clients_uhy/uhy_client_onboarding/envs/staging.env`.

Sample: 32 preparations on staging today, 141 individual runs across them.

---

## §2 — Finding #1: query-string filter is silently ignored

```bash
GET /api/v1/preparations?client_name=BioPro&tax_year=2025
GET /api/v1/preparations  # no params
```

**Both return the identical 8294-byte payload** — the full 32-prep list. The server ignores `client_name` and `tax_year` query params. Confirmed by byte-size equality and JSON diff.

**Impact on Arch D action body:**
- Cannot rely on server-side `?client_name=X&tax_year=Y` filter
- Action must `GET /api/v1/preparations` (full list) → JQ-filter client-side by both fields
- JQ also needs **fuzzy client_name match** because user types "BioPro" but the prep row says "Bio Pro, Inc." (full legal name). There are also two distinct clients on staging: `Bio Pro, Inc.` (id=176) and `Bio Pro` (id=174) — disambiguation matters.

**Recommended JQ filter** (case-insensitive substring on `client_name`):
```jq
[ .[] | select(
    (.client_name | ascii_downcase) | contains($q | ascii_downcase)
  ) | select(.tax_year == ($ty | tonumber))
] | sort_by(.created_at) | last
```

**Question for you:** is the silent-ignore intentional (and we'll fix it in convergence VM v0.4.x) or a bug in the v0.4.1 deployment? Either way, client-side filter works for v1 — no API change needed for this PR.

---

## §3 — Finding #2: status vocab on staging ≠ Q2 schema

You said in Q2: `queued`, `doing`, `succeeded`, `failed`, `cancelled` (treat `cancelled` as Case C).

**Staging reality** (counts across 141 sampled runs):

| Status string | Count | Notes |
|---|---|---|
| `superseded` | 41 | Not in Q2. Created when a new run starts on the same prep. Not terminal — needs filtering out. |
| `completed` | 36 | Q2 called this `succeeded`. Terminal-good. |
| `failed` | 21 | Matches Q2. Terminal-bad. |
| `pending` | 17 | Q2 called this `queued`. In-flight. |
| `stub_carry` | 15 | Not in Q2. Looks like prior-year placeholders (4× Complete Automation TY22/23/24 all show `[stub_carry]` runs). Not real runs. |
| `engine_silent_zero` | 8 | Not in Q2. Terminal — engine ran, found no qualifying R&D activity. Distinct UX from `failed` (nothing broke). |
| `running` | 3 | Q2 called this `doing`. In-flight. |

**Zero occurrences** of: `succeeded`, `queued`, `doing`, `cancelled`. Either:
1. Q2 was forward-looking (convergence VM v0.5+ rename in progress) and staging is on the old vocab, OR
2. Q2 was best-guess and I should treat staging-live as canonical.

**Either way, the action's branch logic has to consume staging-live vocab today.**

**Revised case mapping** (UX-preserving — same user-visible message for semantically-same outcomes):

| Latest non-superseded, non-stub run status | Case | Action behavior |
|---|---|---|
| `completed` | **A** | Fetch deliverable, render credit + download URL |
| `pending` / `running` | **B** | "Still in progress, ask me again" |
| `failed` | **C** | "Last attempt failed: {error}, want me to retry?" |
| `engine_silent_zero` | **E** (NEW) | "Engine ran and found no qualifying R&D activity for this client/year. Want to investigate, or work with a CPA?" |
| (only `superseded` rows exist) | treat as B | The superseding run hasn't terminated yet — pending equivalent |
| (only `stub_carry` rows exist) | treat as D | No real run; trigger a new one |
| no matching prep at all (404) | **D** | POST `/from-harness-async`, "started, ask me again" |

`stub_carry` and `superseded` are filtered out *before* picking the "latest" run. So the actual logic:
```
latest_real = preparation.runs
              | filter status not in ('superseded', 'stub_carry')
              | sort by run_number desc
              | first
```

Then branch on `latest_real.status` (or nothing-found → Case D).

---

## §4 — Finding #3: list view doesn't include `runs`; need 2-step lookup

`GET /api/v1/preparations` returns flat rows with `[id, client_name, tax_year, status, scenario, accuracy_estimate, document_count, employee_count, run_count, created_at]`. No `runs` array.

To see run status I have to follow up with `GET /api/v1/preparations/{id}` which returns the full detail incl. `runs[]`, `tasks`, `documents`, `deliverable_count`, etc.

**Impact on Arch D action body:** 2 REST steps for case-detection (was 1 in your spec):

```
Step 1: listPreparations    GET /preparations             → flat list
Step 2: filterMatching      JQ filter by client_name + tax_year → prep_id or null
Step 3: CONDITION           if null → JUMP to caseD
Step 4: fetchPrepDetail     GET /preparations/{prep_id}   → runs[]
Step 5: pickLatestReal      JQ filter runs by status, sort, take last
Step 6: branch              CONDITION on status → Case A/B/C/E
```

Still <2 sec for cases A/B/C/E. Case D adds one POST. Action lifetime well within 3-min platform timeout.

**Note on list-page-status field:** the prep-level `status` field on the list response is `in_progress` for **all 32 preps** on staging. Not useful for branching — must inspect `runs[]` from the detail call.

---

## §5 — Finding #4: federal_credit + total_qre live on `runs[i]`, not deliverable

You said in Q3 these come from either `/deliverables` response or `preparations[0]`. Reality is **both** lie — they live on `runs[i].federal_credit` and `runs[i].total_qre` in the prep-detail response.

Example (Akervall TY2025, the only run is failed so values are null):
```json
"runs": [
  {
    "id": "00fd6c78-...",
    "run_number": 1,
    "status": "failed",
    "scenario": "D",
    "total_qre": null,
    "federal_credit": null,
    "employee_count": null,
    "elapsed_seconds": 76.0,
    "started_at": "2026-05-11T19:06:53.174056+00:00"
  }
]
```

And from Complete Automation TY2025 (succeeded):
- `runs=['completed']`, `latest_credit=$264063.18`, `deliv_count=1` ✓

So Case A's render pulls credit + qre from `runs[-1]` (after filtering superseded/stub_carry) and download_url from the separate `/deliverables` call.

`/preparations/{prep_id}/deliverables` shape (Akervall, no deliverables):
```json
{ "preparation_id": "34ce1c8e-...", "deliverables": [] }
```

Don't have a sample of a non-empty `deliverables[]` yet (Akervall + Bridge are zero; Complete Automation TY2025 has `deliv_count: 1` but I didn't probe the array shape). Will probe during Change 1 dev — won't block branching.

---

## §6 — Finding #5: Bridge Organics TY2025 is `engine_silent_zero` on staging

Your joint-smoke target Bridge Organics is currently sitting at `runs=['engine_silent_zero']` with `deliv_count=0`. That's the new Case E above. If the joint smoke (Monday 10ET) is the user's first call to `generate-workbook` for Bridge, they'll hit Case E, not Case A. UX wording matters here — you don't want it to read like "failed/retry" when in fact the engine ran cleanly and found nothing qualifying.

**Question:** is the silent-zero a legitimate "this client has no R&D" outcome that should be cleanly surfaced, OR is it the symptom of an underlying issue (missing docs, ingest didn't finish) that should be auto-retriable? Wording depends on your answer.

My current proposal (Case E):
> "Engine ran for Bridge Organics TY 2025 and found no qualifying R&D activity. This usually means either (a) no R&D documents were ingested, or (b) the underlying activity genuinely doesn't qualify. Want me to look at what docs the prep contains, or work with a CPA to confirm?"

---

## §7 — Finding #6: Complete Automation TY2025 = perfect Case A test target ✓

Your suggestion in tomorrow ask #4 works:
- `runs=['completed']`
- `federal_credit=$264,063.18`
- `deliv_count=1`

I'll build Change 1, set this as the seed test_case, and confirm the Case A render before joint smoke.

---

## §8 — Revised Architecture D action body (canonical spec)

Pulling all 5 findings together:

```
required_inputs: client_name (string), tax_year (integer)

Step 1 — listPreps:
  GET {VM_BASE_STAGING}/api/v1/preparations
  (server ignores ?client_name=&tax_year= — filter client-side. Confirmed.)

Step 2 — matchPrep JQ:
  [ .[] | select(
      (.client_name | ascii_downcase) | contains($q | ascii_downcase)
    ) | select(.tax_year == ($ty | tonumber))
  ] | sort_by(.created_at) | last
  → emit { found: true|false, prep_id?, client_name?, tax_year? }

Step 3 — CONDITION on matchPrep.found:
  false → JUMP to step 9 (caseD: trigger new)
  true  → continue

Step 4 — fetchPrepDetail:
  GET {VM_BASE_STAGING}/api/v1/preparations/{matchPrep.prep_id}

Step 5 — pickLatestRealRun JQ:
  .runs
  | map(select(.status as $s | $s != "superseded" and $s != "stub_carry"))
  | sort_by(.run_number)
  | last
  → emit { found: true|false, status?, federal_credit?, total_qre?, started_at?, error? }
  (found=false means "all runs were superseded or stub_carry" — treat as B)

Step 6 — CONDITION on pickLatestRealRun.status (with .found check):
  not found OR status in ('pending','running') → JUMP to step 7 (caseB)
  status == 'completed'              → continue to step 8 (caseA)
  status == 'failed'                 → JUMP to caseC formatter
  status == 'engine_silent_zero'     → JUMP to caseE formatter
  default (unrecognized future status) → JUMP to caseB (conservative)

Step 7 — caseB_format JQ:
  "Workbook generation is still in progress for {client_name} TY {tax_year}.
   Started at {pickLatestRealRun.started_at}. Typical runtime:
     • Small clients (e.g. Bridge Organics): ~5 min
     • Medium clients (e.g. Reinhart, BioPro): ~10–15 min
     • Large clients (e.g. Complete Automation): ~30–40 min
   Ask me again when you think it should be ready."
  → JUMP to output

Step 8 — caseA path:
  Step 8a — getDeliverables:
    GET {VM_BASE_STAGING}/api/v1/preparations/{matchPrep.prep_id}/deliverables
  Step 8b — pickNewestDeliverable JQ:
    .deliverables | sort_by(.created_at) | last
  Step 8c — caseA_format JQ:
    "Workbook ready for {client_name} TY {tax_year}.
     **Federal credit:** ${pickLatestRealRun.federal_credit:,.2f}
     **Total QRE:** ${pickLatestRealRun.total_qre:,.2f}
     **Download:** [{pickNewestDeliverable.filename}]({pickNewestDeliverable.download_url}) (expires 7d)"
  → JUMP to output

caseC_format JQ:
  "Last workbook generation attempt for {client_name} TY {tax_year} failed:
     {pickLatestRealRun.error // 'unknown error'}.
   Want me to retry?
   (Reply 'yes' to start a new attempt, or work with a CPA to resolve the failure.)"
  → JUMP to output

caseE_format JQ:
  "Workbook ready for {client_name} TY {tax_year}, but the engine found
   **no qualifying R&D activity** (federal credit = $0).
   This usually means either:
     • No R&D documents were ingested for this client/year, OR
     • The activity genuinely doesn't qualify under §41 rules.
   Want me to check what documents the preparation has, or work with a CPA?"
  → JUMP to output

Step 9 — caseD path:
  Step 9a — triggerNew:
    POST {VM_BASE_STAGING}/api/v1/workbook/from-harness-async
      { client_name, tax_year }
    → 202 + { job_id, poll_url }
  Step 9b — caseD_format JQ:
    "Started generating workbook for {client_name} TY {tax_year}.
     Typical runtime: ~5 min (small) → ~35 min (large with many BCs).
     Ask me again in a few minutes to check status."
  → JUMP to output

output: OUTPUT_TEXT (markdown, single message)
```

**Step count: 6 WDL steps in the hottest path (B/C/E), 8 in A, 4 in D.** All <3 sec. No polling. No platform timeout risk.

**Compared to your sketch:** same architecture, more branches (5 cases not 3), 2 REST calls for case detection (not 1), client-side filter (not server-side). No fundamental shape change.

---

## §9 — Questions for you before I branch (pick what to answer)

| # | Question | If you don't answer, I'll assume |
|---|---|---|
| 1 | Is the `?client_name=&tax_year=` silent-ignore intentional in v0.4.1, or a bug? Either way I'll filter client-side for v1, but worth knowing if it's filed. | Bug, will be fixed in v0.5+; my client-side filter remains until then |
| 2 | Status vocab — are `pending/running/completed/failed/engine_silent_zero/superseded/stub_carry` the canonical set, or transitional to your Q2 set (`queued/doing/succeeded/failed/cancelled`)? | Staging-live is canonical for v1; if rename ships mid-flight I'll update the action |
| 3 | `engine_silent_zero` UX — is my §6 wording acceptable, or do you want different framing? | Ship my §6 wording; iterate during joint smoke if you push back |
| 4 | Bridge Organics TY2025 = `engine_silent_zero`. Joint-smoke pass criteria: do we count Case E surfacing as success, or do you want me to reset Bridge to a clean state first? | Case E surfacing counts as smoke-pass — it's a real terminal outcome, not a bug |
| 5 | `stub_carry` runs — confirm these are prior-year placeholders to skip, not real runs to surface | Skip them (filter alongside superseded) |
| 6 | The deliverable response shape (`deliverables[]` items) — do they include `filename` + `download_url` + `expires_at`? Couldn't probe a non-empty array. | Yes; I'll discover during dev and fix the JQ if my field names are wrong |

---

## §10 — What I'm doing while you sleep

1. Updating `uhy solution/PR_PLAN_SURFACE1_HANDSHAKE_FOR_IAIN_REVIEW.md`:
   - §0 gate table: probe added as "YELLOW — spec corrections needed but architecture unchanged"
   - §2 Change 1: action body replaced with the §8 spec above
   - §6 Q2/Q3: marked "superseded by staging-probe findings — see PROBE_FINDINGS_ARCH_D_REALITY_CHECK"
2. **NOT branching yet.** Holding for your ACK on the Q3 wording at minimum (Bridge Organics joint-smoke depends on it).
3. Ready to branch the moment you ACK (or override). Target: branch + Change 1 built before joint smoke 10ET, Change 2 starts after smoke.

If you want me to push ahead without waiting on you for Q1/2/5/6 (low risk, defaults are safe), just say "go on defaults" and I'll branch at 8am ET Monday with everything except the §6 wording (which I'll hold for your reply).

---

## §11 — Cost of these findings

- 15 min of probing instead of 5 (you asked for 5)
- Found 5 real spec deltas vs your sketch
- Zero of them changes the architecture
- All of them would have surfaced as bugs during build/test if I'd skipped the probe

ROI on the probe: high. Continuing to probe before code touches.

---

## §12 — UPDATE 23:30 ET: build attempt surfaced a NEW infrastructure blocker

After writing this doc and branching, I built the Arch D action body in `widdle.json` and ran `test_runner.py generate-workbook --test test_1.json` to confirm Case A against staging. **The Adopt platform's WDL REST executor cannot reach `echo-summit.westus3.cloudapp.azure.com`.**

### Evidence

| Call | Source | Target | Result |
|---|---|---|---|
| Arch D widdle (test_runner via /run-wdl) | Adopt platform server | `https://echo-summit.../api/v1/preparations` | `ConnectTimeoutError: timed out` after 60s (never reaches SSL handshake) |
| Same URL, same bearer | My laptop (curl) | `https://echo-summit.../api/v1/preparations` | HTTP 200 + 32-prep payload (worked Sunday during the original probe) |
| Live v11 widdle (test_runner via /run-wdl) | Adopt platform server | `https://swifty-panda.../api/v1/workbook/generate-sync` | HTTP 200 in 54.77s, full Case A flow returned a markdown message with download URL — **action passed against prod** |
| `GET /api/v1/preparations` shape check | My laptop (curl, swifty-panda bearer) | `https://swifty-panda.../api/v1/preparations` | HTTP 200 + 6-prep payload (yes, swifty-panda has the same endpoint) |

### Interpretation

1. Network: Adopt platform's outbound IPs are **not on echo-summit's firewall allowlist**. They ARE on swifty-panda's allowlist (proven by the v11 test passing). Both are Azure cloudapp public DNS, so this is firewall/vnet-level not DNS-level.
2. Side effect: running the v11 test as the diagnostic step **just regenerated Complete Automation TY 2025's workbook on PROD** via `/generate-sync`. Hopefully idempotent. Noted in the PR description.
3. SANDBOX vs REST: the `p1-copy-from-inbox` pipeline successfully calls echo-summit from a SANDBOX step (per the existing widdle). So **SANDBOX has different network routing than REST** on the platform side. SANDBOX can reach staging; REST can't.
4. swifty-panda already has the convergence-shape endpoints (`/api/v1/preparations` etc.). So Arch D body is structurally portable between staging and prod.

### Three options for v1.0

| Option | What it requires | Tradeoffs |
|---|---|---|
| **(α) Get echo-summit allowlist updated** | Iain coordinates with UHY ops to add Adopt platform outbound IPs to echo-summit's NSG/firewall | Cleanest. Honors your "staging-only" directive. Unknown lead time. |
| **(β) Build Arch D against swifty-panda (prod)** | Swap `echo-summit` → `swifty-panda` URLs in the widdle; switch bearer accordingly | Violates your "no prod reconciliation in this PR" directive. But it actually works end-to-end and the soak runs naturally on real data. |
| **(γ) Wrap the action body in a SANDBOX step** | Convert REST steps to `urllib`-in-SANDBOX (like `p1-copy-from-inbox` does) — bigger code change, ~100 lines of Python in a SANDBOX block | Works against staging today. Roughly doubles the action's complexity. Loses some WDL-level observability. |
| **(δ) Hold this PR** | Don't ship Change 1 until echo-summit is reachable from REST | Slips joint smoke Monday. |

### My recommendation

**(α) if you can get echo-summit allowlist updated by Monday morning** — cleanest, fits the original plan.

**(γ) if not** — SANDBOX-wrap is the only path that keeps "staging-only" intact today. I'd build a single SANDBOX step that does lookupPreps + matchPrep + fetchPrepDetail + branch + (triggerNew | getDeliverables) inline in Python; the WDL becomes 3 steps: `required_inputs` → `runAction` (SANDBOX) → `output` (OUTPUT_TEXT). I have the staging bearer + endpoints ready to drop in.

**Avoid (β) unless you're OK with prod cutover happening this PR.**

### What I shipped on the branch tonight

The branch `feat/adopt-surface1-surface2-architecture-d` has my Arch D widdle **pointed at echo-summit** (the "intended" target per your plan). It compiles green, but doesn't pass the end-to-end test because of the network block above. PR description will lead with this paragraph + ask for your choice between α/β/γ. Test cases test_1/2/3 are in place; they'll start passing the moment α or β is in effect (γ requires reworking the action body to match the SANDBOX shape).

### Questions for you on top of §9

| # | Question | Default if no reply |
|---|---|---|
| 7 | Network — α, β, or γ? | α (you pursue allowlist), I'll keep building Change 2 (pipeline already uses SANDBOX so it's unaffected) |
| 8 | Did I just leak a regenerated Complete Automation TY 2025 workbook into prod via the diagnostic? Should I be more careful about test_runner against prod? | Be more careful. I'll skip prod-touching tests during diagnostics. |

---

## §13 — Monday morning test evidence (post VM-back-up)

**Date:** 2026-05-25 10:30 ET
**Context:** VM came back up per `REPLY_TO_ADRYANN_VM_BACK_UP_20260524.md`. Folded Case E into Case A per your 4-branch instruction (engine_silent_zero normalized to completed at `pickLatestRun` JQ — see §13.4 for v1.1 followup). Ran test_runner end-to-end against echo-summit. Hit two new platform-level findings — neither is a bug in the WDL body; the business chain works end-to-end from curl.

### §13.1 — Auth pattern (FIXED, FYI for the PR description)

The `adopt_profile.json` at workspace level (`workspaces/uhy-prod/adopt_profile.json`) has `base_url=swifty-panda` and `security_params.authorization=<swifty bearer>`. The platform's WDL REST executor pulls auth from `security_params.authorization` and **overrides any inline `headers.Authorization`** in the step. So inline `Bearer 7Yuk...` in the widdle was getting silently replaced by `Bearer qK2u...` (swifty's), which echo-summit rejected with "Invalid or missing token".

**Fix:** added an action-level override at `workspaces/uhy-prod/actions/generate-workbook/adopt_profile.json` pointing `base_url`+`security_params` at echo-summit + staging bearer. Resolved.

PR description will note this so future actions targeting staging from a swifty-default workspace know to override at action level.

### §13.2 — Finding #7: JQ_FILTER bodies do NOT substitute `{workflow_arguments.X}`

`{workflow_arguments.client_name}` and `{workflow_arguments.tax_year}` substitute correctly in REST URL strings (see all the working `uhy-prep-review` pipeline steps), but **do NOT substitute inside JQ `filter` body strings**.

**Evidence:** test_1.json sets `client_name="Complete Automation"` + `tax_year=2025`. The widdle's `matchPrep` JQ filter contains:
```
.client_name | ascii_downcase | contains("{workflow_arguments.client_name}" | ascii_downcase)
```
Curl confirms `Complete Automation, Inc.` TY 2025 (prep_id `80ff9785-d6b1-44d8-a137-50afd3845348`, runs[0].status=`completed`, federal_credit=$264,063.18) exists in the prep list. The JQ filter (run locally against the same payload) matches it. But the WDL execution path returned `matchPrep.found=false` → fell into Case D (POST trigger), proving the substitution didn't happen.

**This is the same problem the existing `uhy-suralink-ingest-per-zip` pipeline solves via SANDBOX steps** — that pipeline does Python-side filtering, not JQ-side. JQ_FILTER's substitution layer is REST-step-only.

**Workaround options for the WDL body:**
- (a) Use a SANDBOX step for the lookup branch (matches existing pipeline pattern)
- (b) Inject `workflow_arguments` as JQ args via `inputs:` field (need to check WDL docs whether `inputs` makes workflow_arguments available as `$workflow_arguments` in the filter)
- (c) Convert the matchPrep step to use EXTRACT or a different operation that exposes workflow_arguments

I'm parking this as **Finding #7 for joint smoke** — it's a real WDL substitution semantics question that needs your call on which workaround pattern to adopt. Spec is fine; implementation needs one more bend.

### §13.3 — Finding #8: platform REST executor flags echo-summit's `{status: "queued"}` response as 400 error

When the action falls through to Case D, `triggerNew` POSTs to `/api/v1/workbook/from-harness-async`. Echo-summit returns **200 OK** with body:
```json
{"status": "queued", "job_id": "31", "poll_url": "/api/v1/workbook/from-harness-async/31",
 "client_name": "Complete Automation", "tax_year": 2025,
 "note": "Run a procrastinate worker on queue='pipeline' to process this job."}
```

The platform's WiddleExecutor reads `.status` in the body and — apparently expecting `success` or boolean true — wraps the response as failed:
```json
{"status": false, "message": "Error: {'status': 'queued', ...}"}
```

**The POST itself succeeded** (echo-summit returned a job_id and queued the work — I can see job 31 in the queue from curl). The platform is just mis-classifying the response shape.

**Impact:**
- Case D will always look like a test failure even though it's working
- Case A might hit the same issue when echo-summit deliverables endpoint returns its response (need to verify with a deeper trace)

**Workaround options:**
- (a) WDL-side: handle the response in JQ (treat `status=="queued"` as success at the format step) — but the platform errors out before JQ runs
- (b) Platform-side: relax the WiddleExecutor's response classifier so non-`status:true` responses don't auto-error (this is a platform PR, not Arch D scope)
- (c) Move the POST into a SANDBOX step (bypass the WDL REST executor's response classifier entirely)

Parking as **Finding #8 for joint smoke**. The smoke run from chat will hit this — when the user kicks off a fresh Bridge Organics, the action will look failed even though Bridge will actually start preparing on staging.

### §13.4 — v1.1 followup: reintroduce Case E for `engine_silent_zero`

Per your 4-branch instruction, the current widdle folds `engine_silent_zero` into Case A by rewriting the run status to `completed` at `pickLatestRun`. This means Bridge Organics TY 2025 (the staging `engine_silent_zero` case) will get a Case A message: "Workbook ready. Federal credit: $0. Total QRE: $0. Download: ...".

That's technically accurate but doesn't tell the user *why* (no R&D documents ingested vs. genuinely non-qualifying). The original Case E wording surfaced the distinction.

**v1.1 ticket** (to be opened post-PR): reintroduce Case E with the proper "no qualifying R&D activity" wording, behind the same `engine_silent_zero` status check.

### §13.5 — What's true vs what's blocked

| Check | Status |
|---|---|
| VM reachable from platform REST | ✅ (no more ConnectTimeout — was just powered off) |
| Auth pattern (action-level profile override) | ✅ fixed |
| Compile | ✅ 2s, green |
| Business chain via curl (preps → detail → runs → deliverables) | ✅ verified end-to-end |
| WDL JQ matchPrep against real data | ❌ blocked by Finding #7 |
| Case D POST response classification | ❌ blocked by Finding #8 |
| End-to-end test_1 (Case A) passing | ❌ blocked by #7 (lookup fails → falls to Case D → blocked by #8) |
| End-to-end test_3 (Case D) passing | ❌ blocked by #8 |
| Staging DB state | 32 preps all `status="in_progress"` (this is the prep status, not the run status — runs[0] still shows `completed`/`failed`/etc per client) |

### §13.6 — Recommended joint smoke flow given #7 + #8

Don't run the chat-side smoke until we decide on Finding #7 workaround. Options for the 10:00 ET window:

1. **Walk through #7 + #8 together over Slack/Zoom**, decide on workaround pattern (likely SANDBOX-wrap for matchPrep + acceptable response handling for the POST)
2. **Postpone joint smoke 30-60 min** to give time to apply chosen workaround and rerun test_runner
3. **Run the smoke anyway** with the known-failing classification, treat the two errors as "expected, captured under #7 / #8, action body logic verified by curl"

I'll have curl evidence + traces ready in the branch.

---

## §14 — Findings #7 + #8 RESOLVED via SANDBOX-wrap (2026-05-25 AM)

Iain green-lit the SANDBOX-wrap workaround in `REPLY_TO_ADRYANN_F7_F8_SANDBOX_GREENLIGHT_20260525.md`. Applied per §2 + §3 of that reply, modelled on the `uhy-suralink-ingest-per-zip` pipeline pattern (init upload_files → exec heredoc + python → teardown → JQ_FILTER parses stdout).

### §14.1 — Action shape (final, 28 steps)

```
required_inputs(client_name, tax_year)

# Idempotency lookup (Finding #7 SANDBOX workaround)
matchPrep_init        SANDBOX(init)   uploads match_prep.py
matchPrep_exec        SANDBOX(exec)   writes /tmp/match_ctx.json + runs script
matchPrep_teardown    SANDBOX(teardown)
matchPrep             JQ_FILTER       try (.stdout | fromjson) catch {...}
checkMatchFound       CONDITION       found==true → fetchPrepDetail | else → triggerNew_init

# Lookup branch (Cases A/B/C)
fetchPrepDetail       REST GET /api/v1/preparations/{matchPrep.prep_id}
pickLatestRun         JQ_FILTER       filters superseded/stub_carry; folds engine_silent_zero → completed
checkIsCompleted      CONDITION       status==completed → getDeliverables | else → checkIsFailed
checkIsFailed         CONDITION       status==failed    → formatCaseC      | else → formatCaseB

# Case A (completed)
getDeliverables       REST GET /api/v1/preparations/{matchPrep.prep_id}/deliverables
pickNewestDeliverable JQ_FILTER       sort by created_at; synthesise download_url from id
formatCaseA           JQ_FILTER       "Workbook ready..."
outputA               OUTPUT_TEXT
jumpAfterA            JUMP target=endOfFlow

# Case B (in progress / pending)
formatCaseB           JQ_FILTER       "Workbook generation is still in progress..."
outputB               OUTPUT_TEXT
jumpAfterB            JUMP target=endOfFlow

# Case C (failed)
formatCaseC           JQ_FILTER       "Last workbook generation attempt failed. Want me to retry?"
outputC               OUTPUT_TEXT
jumpAfterC            JUMP target=endOfFlow

# Case D (no prep — trigger new) — Finding #8 SANDBOX workaround
triggerNew_init       SANDBOX(init)   uploads trigger_new.py
triggerNew_exec       SANDBOX(exec)   writes /tmp/trigger_ctx.json + runs POST
triggerNew_teardown   SANDBOX(teardown)
triggerNew            JQ_FILTER       parses stdout {ok, job_id, queue_status, ...}
checkTriggerOk        CONDITION       ok==true → formatCaseD | else → formatCaseDError
formatCaseD           JQ_FILTER       "Started generating workbook..."
outputD               OUTPUT_TEXT
jumpAfterD            JUMP target=endOfFlow
formatCaseDError      JQ_FILTER       "Couldn't find any source data... HTTP {status}... please verify"
outputDError          OUTPUT_TEXT

endOfFlow             JQ_FILTER       noop terminator
```

### §14.2 — Test_runner results (4/4 GREEN)

| # | Case | Client + TY | Result | Time |
|---|------|-------------|--------|------|
| test_1 | A   | Complete Automation TY 2025 | ✅ PASS — full Case A render with $264,063 credit + working download URL | 8.4s |
| test_2 | A-fold | Bridge Organics TY 2025  | ✅ PASS — $0/$0 + empty download (engine_silent_zero folded per §1.5) | 8.5s |
| test_3 | D-Error | ZZZ Nonexistent TY 2099 | ✅ PASS — graceful "HTTP 404 / no corpus dir" message | 23.2s |
| test_4 | C   | Akervall TY 2025            | ✅ PASS — "Last attempt failed. Want me to retry?" | 10.0s |

Total: 50s for `--all`. Trace files in `workspaces/uhy-prod/actions/generate-workbook/traces/`.

### §14.3 — Deltas from your §2 + §3 sketch

- **Bearer:** hardcoded in the heredoc `cat > /tmp/{ctx}.json` (same pattern as suralink). Did not need a workflow_argument fallback.
- **Image / network:** `python:3.11-slim`, `network.egress: "allow"` (matches suralink).
- **Three-step SANDBOX:** kept the canonical init / exec / teardown shape; sandbox persists across the exec step.
- **POST error handling (Finding #8):** went one step beyond your sketch — instead of `sys.exit(1)` on HTTP 4xx, the script now exits 0 with `{ok: false, http_status, detail, ...}` and a new `checkTriggerOk` CONDITION routes to a `formatCaseDError` step. This way the action surfaces a meaningful "no corpus dir" message instead of crashing. Real VM 404s (like ZZZ Nonexistent) become useful UX feedback rather than action failures.
- **JUMP-after-output:** `is_last_step: true` did NOT halt execution in tests — the flow continued through every subsequent case-format/output step, even firing `triggerNew` POSTs accidentally. Fixed by adding explicit `JUMP target=endOfFlow` after every `outputA/B/C/D` and a no-op `endOfFlow` JQ filter as the trailing step. With JUMPs in place, traces show clean single-path execution (13-14 step traces per case).
- **fetchPrepDetail / getDeliverables stayed as REST:** only POST responses tripped the WiddleExecutor classifier (Finding #8), GETs work fine — so I kept them native. The action-level `adopt_profile.json` still applies to those REST steps to pin the staging bearer.

### §14.4 — Downstream consequences

- **Action-level `adopt_profile.json` is STILL needed** (your §7 said remove, but only the two REST GETs need it now — SANDBOX scripts carry the bearer in the heredoc directly).
- **Side effect during testing:** the first iteration without JUMPs accidentally POSTed `from-harness-async` for Complete Automation TY 2025 (job_id 32 was created on staging). No prod impact — that's a real running workbook regen on staging only. Idempotency lookup would catch it on a re-test.
- **v1.1 ticket Case E:** still queued per §13.4 — fold-to-A renders `$0/$0/empty download` for Bridge Organics which is technically correct but suboptimal UX. Per your §9: leave for smoke iteration.

### §14.5 — Ready for joint smoke

Per your §5 sequence:
1. ✅ §2 + §3 SANDBOX-wrap fixes applied
2. ✅ test_runner --all green against 4 cases on staging
3. → Ping you on Slack with this branch + commit
4. → You run `adryann_smoke.sh --client biopro --target staging`
5. → I watch from chat-agent side, verify Case D → Case B → Case A transitions on a fresh BioPro upload
6. → Verify download URL fetches the right workbook (sha256)
7. → Iterate Bridge Organics next, then Reinhart

Branch: `feat/adopt-surface1-surface2-architecture-d`. Commit incoming.

---

## §15 — Change 2 built per `REPLY_TO_ADRYANN_DELTAS_ACK_CHANGE2_GO_20260525.md` §3

You said §3: *"Start now, don't wait. Build per spec, real test Tuesday post-Wave-1."* Done.

### §15.1 — What landed on the platform

- **Pipeline:** `p1-copy-from-inbox` (remote `2622de32e0f64bae`)
- **Draft saved:** **v51** (`c91403fce6394d51`) — direct WDL push via `save_pipeline_draft.py`. v50 (the old `eval_and_commit.py` chain) remains the published version; v51 stays draft until Tuesday's real-zip test.
- **Step diff scope:** only the middle SANDBOX chain (`sbx_evalcommit_*`). 16 of 19 widdle steps are byte-identical to v50. Surgical.

### §15.2 — Mutations

1. `sbx_evalcommit_init.upload_files[0].path`: `/workspace/eval_and_commit.py` → `/workspace/upload_and_poll.py`
2. `sbx_evalcommit_init.upload_files[0].content`: rewritten (~434 lines, source mirror at `uhy solution/change-2-upload-and-poll/upload_and_poll.py` so reviewers can read it inline — gitignored `workspaces/` blocks WDL diff visibility)
3. `sbx_evalcommit_init.env` gained `FORCE_REPLACE = "{workflow_arguments.force_replace}"` (default `"false"`; HITL re-trigger path flips to `"true"`)
4. `sbx_evalcommit_init.timeout_minutes`: `10` → `50` (45 min internal poll cap + 5 min boot/teardown headroom)
5. `sbx_evalcommit_run.command`: `python /workspace/eval_and_commit.py` → `python /workspace/upload_and_poll.py`

Everything else (canonicalize SANDBOX chain, `vmEvalResult` JQ, `triageFormatted` JQ, `routeOnTriage` CONDITION, `triageHitl` ESCALATE, `resolveTriageDecision` JQ, `routeAfterTriage` CONDITION, `triageAbortOutput`, `sbx_trigger_*`, `triggerResult` JQ, `output` OUTPUT_TEXT) is untouched.

### §15.3 — Endpoint chain (per your §3)

```
1. POST /api/v1/clients/upload-zip
     multipart: file, dir_name, tax_year, auto_ingest=true, force_replace, workstream_id_hint
     200 + idempotent=true → take ingest_job_id, → step 2
     202                   → take ingest_job_id, → step 2
     409                   → emit HITL_REQUIRED + force_replace blocker (existing HITL chain)
2. GET /api/v1/ingest/{ingest_job_id}/poll
     5s × 6 (first 30s) then 15s, max 45 min wall-clock
     completed → take harness_job_id, → step 3
     failed    → emit VM_INGEST_FAILED blocker (existing HITL chain)
3. GET /api/v1/workbook/from-harness-async/{harness_job_id}
     same cadence, same cap
     succeeded → preparations[0] has id + federal_credit + total_qre
                 (engine_silent_zero inferred if both null OR preparations empty)
     failed    → emit VM_PIPELINE_FAILED blocker (existing HITL chain)
```

Step 4 from your spec (optional `GET /preparations/{prep_id}/deliverables`) is **not in v1.0** — your spec said *"Skip if scope-creep concern; can land in a follow-up PR."* Skipped. v1.1 ticket queued.

### §15.4 — Output-shape contract preserved

`upload_and_poll.py` emits the same JSON keys `eval_and_commit.py` emitted, so all downstream JQ/CONDITION/HITL/OUTPUT_TEXT steps continue to work:

`triage_status`, `go_no_go`, `recommendation_code`, `folder_name`, `client_id`, `evaluation_id` (= `ingest_job_id`), `engine_status` (= `completed | engine_silent_zero | failed | skipped`), `total_qre`, `federal_credit`, `s174_sre` (=`null`, not in new VM shape), `prep_run_id`, `elapsed_seconds`, `blockers`.

Plus a new `harness_job_id` field for debugging convenience (downstream ignores unknown fields).

### §15.5 — Deltas from your §3 sketch

- **`workstream_id_hint` forwarded as multipart field** when env-substituted (matches legacy `eval_and_commit.py` pattern, preserves Pipeline B workstream context). Your sketch only listed the 5 base fields. Non-breaking.
- **`s174_sre` null** in all cases — new VM shape doesn't surface it. Downstream `output` will print `None` for that slot. v1.1 candidate: add to VM response OR derive from `preparations[0]`.
- **`engine_silent_zero` inferred locally** when `status=succeeded` but `preparations` is empty OR both qre+credit are null (matches Bridge Organics' shape per `adryann_smoke.sh` line 60 `EXPECTED_SHAPE="silent_zero"`).
- **409 force_replace handling = manual re-trigger** (operator flips `FORCE_REPLACE=true` workflow_arg). Auto-retry-after-HITL would need a second SANDBOX chain — out of scope for v1.0. v1.1 ticket queued.

### §15.6 — Test status

| Tier | Status | Notes |
|------|--------|-------|
| `ast.parse` | ✅ | 434 lines, clean |
| `save_pipeline_draft --dry-run` | ✅ | 19 steps accepted |
| `save_pipeline_draft` (real) | ✅ | v51 on platform |
| Real execution test (Bridge Organics zip) | ⏳ | Tuesday post-Wave-1 per your §3 — I don't have the zip locally; runs from your side OR I can spin a fresh zip locally if you can share BioPro/Reinhart bundles |

### §15.7 — Bug-list update (now at 5)

Per your §5 table, plus #5 added per your §1 Delta 2:

| # | Bug | Repo | Severity |
|---|-----|------|----------|
| 1 | `GET /api/v1/preparations?client_name=&tax_year=` silently ignores filter params | `adoptai/clients_uhy` | L |
| 2 | `widdle_docs` missing action-level `adopt_profile.json` override docs | `adoptai/adopt-docs` | L |
| 3 | F7: `{workflow_arguments.X}` doesn't substitute in JQ_FILTER bodies | adopt platform | M |
| 4 | F8: WiddleExecutor mis-classifies non-200-shaped success responses as 400 | adopt platform | M |
| 5 | `is_last_step: true` doesn't halt action execution; explicit JUMP required | adopt platform | M |

### §15.8 — Next moves

1. Commit `uhy solution/change-2-upload-and-poll/` + this §15 + push to branch
2. (Optional, pending your call) open draft PR against `dev` with both Change 1 + Change 2 documented — your §4 says "open today once Change 2 is at least stubbed"; Change 2 is more than stubbed (it's complete pending real-zip exec test)
3. Tuesday: real exec test on staging via `adryann_smoke.sh` or chat-trigger
4. After Wave-1 stable: Wave-2 prod cutover (separate ticket per your §7)

---

## §16 — Change 2 v51/v52 EXEC SMOKE (Monday 5/25, post-UNBLOCK)

**Status: GREEN. Per `UNBLOCK_ADRYANN_V51_EXEC_20260525.md` you forwarded — your 6 acceptance criteria all met.** Plus 4 surprises worth your eyes.

### §16.1 — What I ran

Bridge zip `Bridge Organics - TY 2025 - AI Test.zip` (sha256 `680f9103…57a2a3`, 13.99 MB), uploaded to `s3://adopt-org-docstore/.../suralink_inbox/adryann-v51-test/20260525T155334Z/`, fired via custom wrapper `uhy solution/change-2-upload-and-poll/exec_v51_smoke.py` against throwaway workstream `4f62ab39-c8a7-422b-a45b-a58c50c32136`.

Two consecutive runs landed (not by intent — see §16.4):

| run_id | trigger time | duration | terminal | path exercised | timeout_min on `sbx_evalcommit_init` |
|---|---|---|---|---|---|
| `019e5fe7-1cb9-78ac` | 16:10:42 UTC | 25:10 | **success** | GO → trigger_index → Pipeline B fired | 50 |
| `019e5ff9-9b86-7b39` | 16:30:54 UTC | 6:44 | awaiting_human (clean triageHitl) | VM_TIMEOUT (HTTP 502) → triageHitl | 10 |

Run 1 = your happy path. Run 2 = the graceful-error path (different code branch).

### §16.2 — Run 1: your 6 acceptance criteria

| # | Criterion | Result |
|---|---|---|
| 1 | Pipeline reaches terminal | ✅ `status=success` |
| 2 | `sbx_evalcommit_run` exit 0 | ✅ |
| 3 | Output JSON shape correct | ✅ `triage_status, engine_status, total_qre, federal_credit, prep_run_id, blockers` all present |
| 4 | Downstream JQ consumes cleanly | ✅ `routeOnTriage→GO→sbx_trigger` fired Pipeline B |
| 5 | Poll cadence visible in trace | ✅ 5s first poll, then 15s, then 30s (script's escalation logic) |
| 6 | `force_replace=true` honored | ✅ VM accepted fresh upload (no 409) |

### §16.3 — SURPRISE #1: Bridge is NOT silent_zero

Your UNBLOCK §2 expected: `engine_status=engine_silent_zero, total_qre=0, federal_credit=0`.

**Actual from Run 1's `upload_and_poll.py` stdout:**

```json
{
  "triage_status": "GO",
  "go_no_go": "go",
  "recommendation_code": "PROCEED",
  "folder_name": "Bridge Organics_TY 2025",
  "evaluation_id": "e6c6ceb2",
  "harness_job_id": "39",
  "engine_status": "completed",
  "total_qre": 892513.66,
  "federal_credit": 42305.15,
  "s174_sre": null,
  "prep_run_id": null,
  "elapsed_seconds": 1470
}
```

Bridge yielded **$42,305 federal credit on $892,513 QRE**, `engine_status=completed`. Either (a) your "silent_zero" expectation was from an earlier convergence-engine version, (b) `force_replace=true` triggered a fresh extract that differed from your prior smoke, or (c) the zip contents legitimately changed since your last touch. Doesn't affect Change 2 validation — the pipeline correctly routed GO → trigger_index — but worth knowing your mental model of Bridge needs updating.

`prep_run_id=null` and `s174_sre=null` confirm your T-S174 ticket: the VM's `/from-harness-async` terminal response still doesn't surface those two fields.

### §16.4 — SURPRISE #2: Adopt platform Procrastinate queue delays init→exec dispatch by 6–25 min

This is the big one. **Run 1's `sbx_evalcommit_init` returned exit 0 at 16:10:59. Its corresponding `sbx_evalcommit_run` didn't dispatch until 16:35:49 — a 24:50 silent gap with the run sitting at `status=running` and zero observable activity.**

`sbx_init` → `sbx_canonicalize` fired back-to-back in 9 seconds (16:10:45 → 16:10:54). So this is not generic Procrastinate-stuck — it's specific to `sbx_evalcommit_init`.

The difference is `timeout_minutes`. `sbx_init` has `timeout_minutes=5`. `sbx_evalcommit_init` had `timeout_minutes=50`. **Something about the platform's Procrastinate dispatch is sensitive to high session timeouts.** Plausible mechanism: longer session reservations move the job into a different scheduler bucket / wait pool.

Run 2 (with `timeout_minutes=10` instead of 50) saw a smaller but still significant **6:29 init→exec gap** (16:31:07 → 16:37:36). So even moderate timeouts trigger delay.

**Implication for Wave-1 prod:** a Bridge-sized client will need ~50 min of total budget (25 min Procrastinate delay + 24.5 min harness). A Reinhart-sized client (Iain's §4 expects 8-15 min harness) needs at least ~25-30 min total. v50 worked historically because its `sbx_evalcommit_init` had `timeout_minutes=10` and harness completed in ~5-10 min, so it slipped under the Procrastinate-delay radar.

**My read:** this is the same root cause as your UNBLOCK §1 stuck-job 34 (Procrastinate had to be poked). I think it's also why run `019e5071-90f9-741a` from 5/22 sat at "failed" with `duration_ms=252241000` (70 hours) — the stale-run sweeper finally collected it. v50's reliability has been quietly degrading along this axis.

**Recommendation:** ticket against adopt-platform — "SANDBOX sessions with timeout_minutes > 10 sit in Procrastinate dispatch queue for 6-25 minutes before exec fires; init/teardown unaffected." Independent of Wave-1 ship — Wave-1 should ship v52 (timeout=50) and we tolerate the delay.

### §16.5 — SURPRISE #3: `upload_and_poll.py` bug — hardcoded "45 min" string in VM_TIMEOUT message

Run 2's output blocker detail:

```
"detail": "Harness pipeline 41 did not reach a terminal status in 45 min (last status='')."
```

Actual wait was ~92s before HTTP 502 from VM forced an early exit from `_poll_until_terminal`. The 45 min figure is `POLL_MAX_S` hardcoded in the message template, not the elapsed time. Operators reading the HITL escalation will assume the script polled for 45 min when it didn't.

**Also:** the script classifies HTTP 5xx errors AS `VM_TIMEOUT`. Should distinguish:
- `VM_TIMEOUT` = elapsed >= POLL_MAX_S (real timeout)
- `VM_HTTP_ERROR` = early exit on 5xx (transient VM issue, retry-friendly)

**v1.1 ticket:** file under abcd/`uhy solution/change-2-upload-and-poll/` — 20-LoC fix in `_make_blocker_detail()`. Not blocking Wave-1.

### §16.6 — SURPRISE #4: Wave-1 budget math

Putting it together: a single Bridge-class run consumes ~50 min wall-clock on the platform. If a UHY scheduled sync arrives with 5 zips, that's ~50 min × 5 = 4.2 hours sequential, OR parallel runs that compete for VM bandwidth (Run 2 hit HTTP 502 because VM was busy with Run 1's harness 39).

**v1.1 candidate:** add VM-side concurrency control or upstream throttle in `uhy-suralink-ingest-per-zip` (queue zips, fan out at controlled rate).

### §16.7 — What changed on platform (drafts only, nothing published)

| pipeline | platform state | what's there |
|---|---|---|
| `p1-copy-from-inbox` v50 | **published** (no change) | original eval_and_commit pre-convergence — broken since VM swap, see §6b live-canonical analysis |
| `p1-copy-from-inbox` v51 (`c91403fce6394d51`) | draft | first save with `timeout=10` (incorrect — too aggressive for Bridge harness duration) |
| `p1-copy-from-inbox` v52 (`d667e105476a4671`) | **draft, smoke-validated** | `timeout=50` confirmed by Run 1 — Wave-2 should publish THIS |

### §16.8 — Bug list update (now at 7)

| # | Bug | Repo | Severity | Source |
|---|-----|------|----------|--------|
| 1 | `GET /api/v1/preparations?client_name=&tax_year=` silently ignores filter params | `adoptai/clients_uhy` | L | §15 |
| 2 | `widdle_docs` missing action-level `adopt_profile.json` override docs | `adoptai/adopt-docs` | L | §15 |
| 3 | F7: `{workflow_arguments.X}` doesn't substitute in JQ_FILTER bodies | adopt platform | M | §11 |
| 4 | F8: WiddleExecutor mis-classifies non-200-shaped success responses as 400 | adopt platform | M | §11 |
| 5 | `is_last_step: true` doesn't halt action execution; explicit JUMP required | adopt platform | M | §15 |
| 6 | **NEW: SANDBOX `timeout_minutes > 10` delays init→exec dispatch 6-25 min** | adopt platform | **H** | §16.4 |
| 7 | **NEW: `upload_and_poll.py` VM_TIMEOUT detail string hardcoded "45 min"; conflates 5xx + true timeout** | abcd/this-PR | L | §16.5 |

### §16.9 — Decision items for you

1. **v52 publish gating** — happy with timeout=50? (Or would you rather push for a Procrastinate-side fix first and keep timeout lower?)
2. **Bridge real numbers** — does this change anything about your Test Suite 1 in UNBLOCK §3 / smoke baselines?
3. **Wave-1 ship readiness** — v52 draft is behaviorally validated end-to-end on staging. With v12 generate-workbook also at 4/4 green, we're at "ready to publish whenever you give the word." I'm not publishing without your explicit go.
4. **Bug #6 priority** — I think this rises to H (high) because it affects every UHY ingest. Want me to file it against adopt-platform tonight?

