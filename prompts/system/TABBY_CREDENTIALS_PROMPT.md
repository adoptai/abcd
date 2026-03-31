# Tabby Credential Integration for WDL Testing

**PURPOSE**: Set up and use live Tabby browser-session credentials for `abcd test`, replacing manual copy-paste of cookies/headers from DevTools.

**LOAD THIS WHEN**: Setting up Tabby credential integration, debugging auth failures in tests, or configuring `tabby_profile_id` in `adopt_profile.json`.

---

## Overview

Tabby maintains persistent authenticated browser sessions and serves live credentials (cookies, headers, CSRF tokens) on demand. The `abcd` CLI enriches `security_params` at runtime by fetching fresh credentials from Tabby before each test run. This eliminates manual credential copying and prevents stale-session failures.

**Architecture:**

```
test_runner.py
    |
    +-- resolve_adopt_profile()        <-- reads adopt_profile.json (as before)
    |
    +-- enrich_profile_with_tabby()    <-- NEW: overlays Tabby credentials
    |       |
    |       +-- if tabby_profile_id present:
    |              POST /auth/agent-token   --> JWT (cached, thread-safe)
    |              POST /credentials/request --> CredentialResponseEnvelope
    |              merge auth fields into existing security_params
    |
    +-- client.run_wdl_directly()      <-- no change here
```

---

## Quick Start (Full Zero-Friction Setup)

```bash
# 1. Start Tabby infrastructure + API
python cli/tabby_setup.py start

# 2. Full provisioning (interactive: asks for profile, app login config)
python cli/tabby_setup.py setup

# 3. Start a browser session and wait for it to become healthy
python cli/tabby_setup.py session ensure

# 4. Source the env vars and run tests
source .env
python cli/test_runner.py <action-id>
```

All four steps are idempotent. Re-running them is safe and skips already-completed work.

---

## Setup Commands Reference

| Command | What It Does |
|---------|-------------|
| `python cli/tabby_setup.py health` | Check Docker Compose services + API liveness |
| `python cli/tabby_setup.py start` | Start infra (`docker compose up -d`) + NestJS API in background |
| `python cli/tabby_setup.py stop [--infra]` | Stop API process (and optionally Docker Compose) |
| `python cli/tabby_setup.py setup [--profiles p1 p2] [--force]` | Full provisioning: agent client + ServiceProfile + write `.env` |
| `python cli/tabby_setup.py session status` | Show browser session state for all configured profiles |
| `python cli/tabby_setup.py session ensure [--profile ID]` | Ensure a HEALTHY session exists, starting the worker if needed |
| `python cli/tabby_setup.py session stop` | Stop the locally-running worker process |

---

## What `setup` Does (Step by Step)

1. **Starts Tabby** if the API is not already running
2. **Logs in** as admin using credentials from `tabby/.env.local`
3. **Extracts `tenant_id`** from the admin JWT
4. **Determines profiles**: `--profiles` flag > auto-discover from `adopt_profile.json` > cached default > interactive prompt
5. **Registers an agent client** (`abcd-test-runner`) or reuses the cached one
6. **For each profile**: creates Application + ServiceProfile interactively (prompts for login URL, credentials, CSS selectors), then promotes STAGING -> CANARY -> ACTIVE
7. **Writes** `TABBY_API_URL`, `TABBY_CLIENT_ID`, `TABBY_CLIENT_SECRET` to `abcd/.env`

Credentials and provisioning state are cached in `tabby/.tabby-abcd-client.json`. Re-running `setup` skips already-configured items.

---

## What `session ensure` Does

1. Checks for an existing HEALTHY session for the profile's `app_id`
2. If none, **seeds a session record** directly in PostgreSQL via `scripts/batch-a-seed-session.js` (the K8s controller is not running locally)
3. **Writes credential files** to `/tmp/tabby-local-secrets/{secret-name}/{username,password}` for the worker to read (the env-var fallback is unreliable on Ubuntu due to `dash` dropping hyphenated keys)
4. **Starts the worker** locally via `pnpm --filter @browser-hitl/worker start` in headless CDP mode
5. **Polls** until `health_result_type = PASS`, then promotes `state` to `HEALTHY` via direct SQL (normally the K8s controller does this)

---

## Configuring `adopt_profile.json`

Add `tabby_profile_id` at the top level and/or per `profiles_map` entry:

```json
{
  "tabby_profile_id": "salesforce-standard",
  "base_url": "https://your-app.example.com",
  "security_params": {
    "user_org_id": "acme-corp",
    "user_email": "user@acme.com"
  },
  "profiles_map": {
    "calendar": {
      "base_url": "https://calendar.google.com",
      "tabby_profile_id": "google-workspace-standard"
    }
  }
}
```

**Key rules:**

- `tabby_profile_id` is **optional** at every level. When absent, behavior is unchanged.
- Tabby credentials are **overlaid** onto `security_params`, not replaced. Existing non-auth keys (e.g. `user_org_id`, `user_email`) are preserved.
- The profile ID must match what was registered during `tabby_setup.py setup`.

---

## Credential Mapping

The `CredentialResponseEnvelope` from Tabby is mapped to flat `security_params` headers:

| Envelope field | Mapped to |
|---------------|-----------|
| `cookies[]` | `Cookie: name=val; name2=val2` (joined) |
| `headers[]` | Each `header.name: header.value` |
| `csrf.header_name` + `csrf.token` | `<header_name>: <token>` |

These are then injected as request headers by the WDL executor and available as WDL substitution variables.

---

## Graceful Degradation

| Scenario | Behavior |
|----------|----------|
| No `tabby_profile_id` in profile | No-op, profile returned unchanged |
| Tabby env vars not set + real static `security_params` exist | Warning logged, uses static fallback |
| Tabby env vars not set + no real static fallback | **RuntimeError** (fail hard) |
| Tabby reachable, credentials fetched | Overlaid onto `security_params` |
| Tabby unreachable + real static fallback | Warning logged, uses static fallback |
| Tabby unreachable + no real fallback | **RuntimeError** (fail hard) |

A "real static fallback" means `security_params` contains at least one non-empty string value (not just placeholder dicts).

---

## No-Login Profiles

For apps that don't require authentication (e.g. public APIs, unauthenticated pages), leave the email/password blank during `setup`. The system will:

- Use `k8s:secret/no-auth` as a dummy `credential_ref`
- Skip login DSL steps (only `goto` + optional success selector)
- Write dummy credential files for the worker to satisfy its resolution check

---

## Troubleshooting

### "Tabby credential fetch failed: 404"

The credentials service returns 404 in two cases:
1. **No ACTIVE ServiceProfile** for the `tabby_profile_id`. Re-run `python cli/tabby_setup.py setup`.
2. **No HEALTHY session** for the profile's app. Run `python cli/tabby_setup.py session ensure`.

### "Falling back to static security_params" (warning)

Tabby is configured (`tabby_profile_id` is set) but credentials couldn't be fetched. Static values in `security_params` are being used instead. Check that:
- `TABBY_API_URL`, `TABBY_CLIENT_ID`, `TABBY_CLIENT_SECRET` are set (run `source .env`)
- The Tabby API is running (`python cli/tabby_setup.py health`)
- A HEALTHY session exists (`python cli/tabby_setup.py session status`)

### Worker fails with EADDRINUSE on port 8091

A previous worker is still running. Run `python cli/tabby_setup.py session stop` before `session ensure`.

### Worker fails with "Credentials not found"

Credential files were not written or the `CREDENTIALS_MOUNT_PATH` is wrong. Re-run `session ensure` which creates files under `/tmp/tabby-local-secrets/`.

---

## Key Files

| File | Role |
|------|------|
| `cli/tabby_client.py` | HTTP client: JWT cache, credential fetch, overlay, profile enrichment |
| `cli/tabby_setup.py` | CLI: start/stop/setup/session lifecycle management |
| `cli/test_runner.py:598,845,1078` | Three enrichment hook points (after `resolve_adopt_profile()`) |
| `tabby/.env.local` | Admin credentials + DB/Redis/NATS URLs for local Tabby |
| `tabby/.tabby-abcd-client.json` | Cached agent client credentials + app provisioning state |
| `tabby/.tabby-api.pid` | PID file for background API process |
| `tabby/.tabby-worker.pid` | PID file for background worker process |
| `abcd/.env` | TABBY_API_URL, TABBY_CLIENT_ID, TABBY_CLIENT_SECRET |

---

## Provisioning Prerequisites

For credentials to flow end-to-end, all of the following must be true:

1. An **agent client** (`TABBY_CLIENT_ID` / `TABBY_CLIENT_SECRET`) is registered in Tabby with `allowed_profiles` covering the target `tabby_profile_id` -- handled by `setup`
2. A **ServiceProfile** exists for that `profile_id` with `version_state = ACTIVE` -- handled by `setup`
3. Tabby has a **HEALTHY session** for the profile's `app_id` -- handled by `session ensure`
4. The three `TABBY_*` env vars are set in the shell -- handled by `source .env`
