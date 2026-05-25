# Slack ping — Wave-2 LIVE on prod, ready for your joint chat-agent smoke

**Target:** Iain (DM, #adopt-uhy)
**Subject:** v13 + v53 published, both current on prod — fire your chat-agent smoke whenever ready

---

🟢 **Wave-2 LIVE on prod. Fire the joint chat-agent smoke whenever you're ready.**

| surface | id | live version | status |
|---|---|---|---|
| `generate-workbook` action | `96a83403-3b0f-4205-9e2b-676510c87766` | **v13** (current+approved) | ✅ pointing at `swifty-panda` + prod bearer |
| `p1-copy-from-inbox` pipeline | `2622de32e0f64bae` (v_id `9e221b37e1b14f63`) | **v53** (published + running) | ✅ pointing at `swifty-panda` + prod bearer |

Pre-publish prod POL was green (Stages 6+7: 7/7 endpoints 200, Complete Automation resolved as `client_id=2`). Your Bridge prod E2E green at 16:11 was the final confirmation I needed — published 14 min later.

---

**🟡 One transparency note — 46-second v9 regression on action publish:**

First `publish_wdl_action.py --workflow-id generate-workbook --yes` (no `--version` flag) auto-detected "latest draft" as **v9** (May 5 code) instead of v13. Action went `v11 → v9` for ~46 seconds before I caught it via `list_wdl_versions` and rolled forward with `publish ... --version 13` explicit.

- **Window:** `16:25:18 → 16:26:04` UTC-3 (`19:25:18 → 19:26:04` UTC)
- **Risk:** v9 = pre-Arch-D, pre-SANDBOX, pre-convergence-URL. Any chat invocation in those 46 sec would have HARD-FAILED (calling stale staging endpoints that no longer exist). Real-world chance: very low — chat traffic on this action is rare + not 16:25 weekend window.
- **Root cause:** `cli/publish_wdl_action.py` auto-detect of "latest draft" is broken — picked v9 (oldest approved that wasn't current?) instead of v13 (highest pending_approval). Workaround = always pass `--version <N>` explicitly.
- **Currently live:** v13. No follow-up action needed on your side.
- **Filing:** bug #8 against ABCD CLI for the `bugs_and_v11_tickets` doc. Will add when next I touch that file.

Sorry for the half-blip. Your screen-watch would have caught it instantly; I caught it within a minute via the version-list sanity check.

---

**🚦 Your move — joint chat-agent smoke. Suggested matrix:**

| case | client | tax_year | expected branch | expected output |
|---|---|---|---|---|
| A | Complete Automation | 2025 | existing prep, completed run → format deliverables + download URL | `client_id=2`, federal_credit + download link |
| D | Bridge Organics (your fresh prod run from 16:11) | 2025 | existing prep, completed run | same $42,305.15 + download URL |
| New-D | (anything fresh) | 2025 | no prep yet → POST `/from-harness-async` + "preparing, ask me again" | `prep_id` returned, async chain kicks |

Ping me when you fire — I'll watch traces from my side in parallel. If anything reds, I push a v14 hotfix.

---

**Bonus (low priority, post-smoke):**

- Logged 6 v53 callouts in `WAVE2_PR_BODY_DRAFT.md` (dict envelope, POST not GET, per-client HITL, integer client_id, signed-URL shape, no hitl_state) so future devs hit none of those walls.
- Your F10 (worker rebuild) + F11 (USE_HARNESS_INGEST env) — happy to mirror into a unified `bugs_and_v11_tickets` doc if you want one canonical post-mortem list. Otherwise they stay in your deploy SOP.

Standing by for your chat-agent smoke trigger.
