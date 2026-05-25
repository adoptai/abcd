# Slack ping — Wave-2 drafts saved, ready to publish on your watch

**Target:** Iain (DM, #adopt-uhy)
**Subject:** v13 + v53 drafts on platform, prod POL 6+7 GREEN, awaiting your "fire" so you can watch

---

🟢 **Wave-2 drafts saved on platform. Ready for publish on your green-light.**

Per your "ping me before you fire so I can watch" — here's the state.

**v13 — `generate-workbook` action draft (`96a83403-3b0f-4205-9e2b-676510c87766` version 13):**
- Copied from v12 widdle (28 steps, Arch D, 4/4 green vs staging)
- 5 URL swaps + 4 inline bearer swaps: `echo-summit.westus3.cloudapp.azure.com` → `swifty-panda.westus3.cloudapp.azure.com`, staging bearer `7YukKqbE…` → prod bearer `qK2u5ZkH…`
- Compile: PASS (1.2s)
- Action-level `adopt_profile.json` swapped in-place (defensive — workspace default also points at prod, but the override keeps action decoupled)

**v53 — `p1-copy-from-inbox` pipeline draft (`2622de32e0f64bae` version_id `9e221b37e1b14f63`):**
- Copied from v52 widdle (19 steps, upload_and_poll.py SANDBOX, timeout=50)
- v52 smoke evidence: Bridge Organics Run 1 `019e5fe7-1cb9-78ac` SUCCESS 1510s, triage_status=GO, federal_credit=$42,305.15
- 1 URL swap + 1 bearer swap (`VM_API_URL` + `VM_BEARER` env on SANDBOX init)
- Dry-run validation: 19 steps accepted
- Save: GREEN

**Pre-publish prod sanity (`exec_pol.py` against swifty-panda + prod bearer):**

| stage | endpoint | result |
|---|---|---|
| 6 | `GET /api/v1/clients` | 200 (741ms) |
| 6 | `GET /api/v1/clients/2/employees` | 200 (1138ms) |
| 6 | `POST /api/v1/query` | 200 (9371ms) |
| 6 | `POST /api/v1/search` | 200 (1521ms) |
| 7 | `GET /api/v1/clients/2/hitl-summary` | 200 (498ms) |
| 7 | `GET /api/v1/clients/2/duplicate-suspects` | 200 (486ms) |
| 7 | `GET /api/v1/clients/2/publish-status` | 200 (516ms) |

**Overall:** Stages 6+7 GREEN against prod. Confirms (a) your Wave-1 deploy is healthy, (b) my URL+bearer swap is correct, (c) prod auth works end-to-end.

Stage 5 byte-fidelity deferred per your spec — 0 deliverables on prod yet; first real chat-driven run validates it (Complete Automation exists on prod as `client_id=2` ready as a target).

---

**🚦 Asking for two things:**

1. **Go on publish.** Standing by to fire `publish_wdl_action.py` + `publish_pipeline.py`. Just say the word + watch your screen.
2. **Publish order preference.** Action-then-pipeline? Pipeline-then-action? Or both in parallel? I'd default to **action-first** (chat-facing surface; pipeline runs are scheduled/manual so the gap is invisible to users) — but you call it.

---

**Bonus done:** Logged the `/preparations/{id}/deliverables` dict-envelope gotcha in `WAVE2_PR_BODY_DRAFT.md` "Callouts for v53" — plus 5 more endpoint-surface gotchas surfaced during POL (POST not GET, per-client not global, integer client_id, signed-URL shape, no hitl_state field). Future devs get the heads-up.

---

**Branch state (`abcd → feat/adopt-surface1-surface2-architecture-d`):** all docs + POL probe + swap scripts pushed. Workspaces are gitignored (direct-publish per repo norm), so v13/v53 widdle.json is on the platform — not in git.
