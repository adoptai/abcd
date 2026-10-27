# The harness debug loop

All commands take `--env <env>` and read `ADOPT_WEBUI_ENDPOINT` +
`ADOPT_HARNESS_PAT_CLIENT_ID/SECRET` from `workspaces/<env>/.env`. The PAT is exchanged at
`POST /v1/users/api-token`; the bearer is cached in `workspaces/<env>/harness/.token_cache.json`.

| Command | What it does |
|---|---|
| `harness_skill.py audit <dir>` | adopt-skill-review static audit (step budget, batching, file placement, frontmatter traps). Exit 2 = critical |
| `harness_skill.py push <dir> [--replace] [--aux A=P]` | lint → name-based exclusions → content secret scan → audit → upload → re-fetch |
| `harness_skill.py verify <name>` | re-fetch a live skill; flags a dropped `process:` block or degraded `output_type` |
| `harness_skill.py delete <name> --yes` | remove an org skill |
| `harness_skill.py push-plugin <dir>` | zip + upload a plugin (`.claude-plugin/plugin.json` + `skills/<slug>/`) |
| `harness_skill.py deploy-default <dir>` | platform default tier (`@adopt.ai` principals only) |
| `harness_workstream.py ensure <name>` / `seed <name> <files…>` | create/cache a workstream; upload docs into its store |
| `harness_run.py start --workstream W --message M` | start a conversation; streams ONE turn and stops |
| `harness_run.py continue <run_id> --message M` | post the next turn |
| `harness_trace.py fetch <turn_id>` / `review <run_id>` | parsed Temporal history; the review checklist (discovery burn, batching, data through the model) |
| `harness_process.py list/get/create/update/delete/…` | Agents-tab processes; components reference skills/plugins by name (admin PAT for writes) |
| `harness_conversations.py` | your recent Agents-tab chats |
| `harness_runs.py` | org-wide runs (admin PAT can read every user's) |

## Reading a turn

- `fetch_skill` in the stream means the skill was selected. If it never appears, the
  description did not match the request — fix the frontmatter `description` (trigger phrases).
- Every tool call costs a step; the harness budget is about 100 steps per turn. `harness_trace.py review`
  shows where they went.
- `call_web_api` results: `login_required` on a member's first use is expected (show the link,
  retry with `wait_for_login: true`); `forbidden` means the profile is not in the agent client's
  `allowed_profiles` or not ACTIVE (admin fix, not retryable).

## Parity, not first-draft debugging

Iterate locally until `audit` is clean and the local behaviour is right, then push and run.
Expect some harness degradation (different system prompt, sandbox, tool gating), but the run
should confirm what you already saw locally.
