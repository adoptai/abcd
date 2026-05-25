# URGENT — Adopt platform can't reach echo-summit. Need allowlist or alternative.
**Date:** 2026-05-24 23:45 ET (Sunday)
**From:** Adryann (post-branch build attempt)
**Re:** Architecture D green-light → Change 1 build → **infra blocker**

---

## TL;DR

**The Adopt platform's WDL REST executor cannot reach `echo-summit.westus3.cloudapp.azure.com`** (ConnectTimeout 60s, never reaches SSL). Same URL works fine from my laptop. The same `test_runner.py` call against your live v11 widdle (which targets swifty-panda) passes end-to-end in 55s.

**echo-summit's firewall doesn't allow the Adopt platform's outbound IPs.** swifty-panda's does.

I can't ship the action against staging until that's fixed. Need your call by Monday morning before joint smoke.

---

## What I built tonight before hitting the wall

1. Branched `feat/adopt-surface1-surface2-architecture-d` off `dev` in `abcd/`
2. Wrote the 5-branch Arch D widdle (21 steps) pointed at echo-summit per your spec
3. Compile: ✅ passes
4. End-to-end test against Complete Automation TY 2025: ❌ `ConnectTimeoutError`
5. Diagnostic: restored v11 (swifty-panda), reran same test → ✅ passed in 55s
6. Side effect of diagnostic: just regenerated Complete Automation's prod workbook. Sorry. Won't do that again.

Code is sitting on the branch, **not committed**. Will commit + push after you pick.

---

## Three platform paths

| Option | What it requires | Lead time | UX |
|---|---|---|---|
| **(α) Allowlist Adopt platform IPs on echo-summit** | You coordinate with UHY ops to add Adopt's outbound CIDR to echo-summit's NSG/firewall | Depends on UHY ops responsiveness | Cleanest. Honors your "staging-only" directive. |
| **(β) Repoint at swifty-panda (prod)** | Swap URL + bearer in widdle. swifty-panda already has `/api/v1/preparations` — same shape as echo-summit, 6 preps incl. Complete Automation TY 2025 (verified). | 5 min to swap, ready to test | Violates your "no prod reconciliation in this PR" directive but actually works end-to-end. |
| **(γ) Wrap action body in a SANDBOX step** | Convert REST steps to `urllib` inside SANDBOX (like `p1-copy-from-inbox` does). SANDBOX has different network routing than REST and CAN reach echo-summit (proven by the existing pipeline). | ~1 hour rebuild | Works against staging today. Doubles action complexity, loses WDL-level observability. |

## My recommendation

**α if you can get the allowlist updated by Monday morning** — cleanest, fits the original plan.

**γ if not** — SANDBOX wrap is the only path that keeps "staging-only" intact today.

**Avoid β** unless you're explicitly OK with prod cutover happening this PR.

---

## What I need from you

A single one-liner reply when you read this:
- "alpha — allowlist incoming, expect by 9am ET" → I keep the branch as-is, build Change 2 (pipeline, unaffected by this), wait on the allowlist
- "gamma — sandbox wrap" → I rebuild the action body as SANDBOX (delays Change 1 build by ~1h, all-in by 10am ET)
- "beta — prod is fine for v1" → I repoint + commit tonight (5 min) and the PR ships with prod URLs
- "delta — hold the PR" → I park and we resync Monday

If nothing arrives by 8am ET I'll default to α (no code change, keep building Change 2).

---

## Where to look

- This file: `uhy solution/URGENT_PING_IAIN_ECHO_SUMMIT_UNREACHABLE.md`
- Full diagnostic + 4-option matrix: `uhy solution/PROBE_FINDINGS_ARCH_D_REALITY_CHECK_FOR_IAIN.md` §12 (newly added)
- Branch with the Arch D widdle: `feat/adopt-surface1-surface2-architecture-d` (uncommitted, sitting in workspace)

---

## Probe metadata for credibility

| Test | From | To | Result |
|---|---|---|---|
| GET /api/v1/preparations | my laptop (curl) | echo-summit | HTTP 200, 32 preps |
| GET /api/v1/preparations | my laptop (curl) | swifty-panda | HTTP 200, 6 preps |
| Live v11 widdle (test_runner) | Adopt platform | swifty-panda /workbook/generate-sync | HTTP 200 in 54.77s, full Case A response |
| Arch D widdle (test_runner) | Adopt platform | echo-summit /preparations | ConnectTimeoutError after 60s |

The "platform IP allowlist missing on echo-summit" reading is the only thing that explains all four rows.

Sleep mode engaged. Pinging once you reply.
