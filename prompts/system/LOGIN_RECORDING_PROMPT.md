# Login Recording — Full Workflow Guide

**PURPOSE**: Record a browser login flow, generate a Tabby ServiceProfile bundle, and provision it end-to-end (register → validate → promote) so the `test_runner` can pull live credentials.

**LOAD THIS WHEN**: Recording login flows, debugging `profile export/register/validate`, or provisioning a new `tabby_profile_id` for an app that requires browser-based auth.

---

## Prerequisite: Elicitation Backend + Chrome Extension

The login recorder depends on two components:

1. **Elicitation backend** — FastAPI server that stores sessions, click events, and bundles.
2. **Chrome extension** — Content script that captures field interactions. Must be loaded in Chrome from `abcd/elicitation/extension/`.

Both must be running before you start.

```bash
# Start the elicitation backend
python cli/elicitation_setup.py start
# → Prints: "Elicitation backend running on port 8000"

# Verify it's alive
python cli/elicitation_setup.py status
```

---

## Complete Workflow (Step by Step)

### Step 1 — Create a Recording Session

```bash
python cli/elicitation_setup.py profile record "<AppName>" "<https://login-url>"
```

This prints a **Session ID** and instructions. Keep the session ID — you need it for export.

### Step 2 — Record in Chrome (CRITICAL: use Login Recording Mode)

> **⚠️ Common mistake**: Opening a regular capture session instead of enabling Login Recording Mode. Regular capture sessions do NOT attach the login content script and will miss `field_role` data, causing the generator to report "No username/password field detected."

In Chrome with the Adopt.ai extension loaded:

1. Click the extension icon to open the popup.
2. **Click "Start Login Recording"** (not the normal "Start Recording" button).
3. Navigate to the login page URL you provided.
4. Fill in username and password normally and submit.
5. Wait for the post-login page to fully load.
6. Click **"Stop Recording"** in the extension popup.

> **If you accidentally used a regular capture session**: the `export` command will still work via a fallback — it searches for click events in the same process matching the login URL. The generated bundle may need manual review of selectors.

### Step 3 — Export and Analyze

```bash
python cli/elicitation_setup.py profile export <session_id>
# Output written to: workspaces/login-recordings/tabby-<app>-<date>.json
```

The command calls the backend `/login-sessions/{id}/analyze` endpoint which:
- Deduplicates consecutive keystrokes for the same field
- Infers `field_role` (username/password) from `input_type`, `autocomplete`, element IDs and field names
- Generates Playwright DSL steps (`fill`, `click`, `wait_for_url`)
- Builds keepalive config pointing to the post-login URL

Bundle files are saved to `workspaces/login-recordings/` by default. This folder is in `.gitignore` (via `workspaces/*`) so secrets never get committed.

### Step 4 — Review

```bash
python cli/elicitation_setup.py profile review workspaces/login-recordings/tabby-<app>-<date>.json
```

Prints a human-readable checklist of items that need verification:
- Are username/password steps present? Correct selectors?
- Is the `success_url_pattern` pointing to the right post-login page?
- Is the keepalive URL returning HTTP 200 when authenticated (not a redirect)?

> **Keepalive URL gotcha**: the health check URL must return `200` when the session is authenticated. A URL that returns `302` (e.g., the root `/` that redirects to `/dashboard`) will cause `health_result_type: TRANSIENT_FAIL` during validate. Set it to the actual post-login destination (e.g., `/dashboard`).

### Step 5 — Register (Provision in Tabby)

```bash
# Tabby must be running first
python cli/tabby_setup.py start

python cli/elicitation_setup.py profile register workspaces/login-recordings/tabby-<app>-<date>.json
```

This creates (or reuses) an **Application** and a **STAGING ServiceProfile** in Tabby, then writes provisioning state to `tabby/.tabby-abcd-client.json`.

> **`target_urls` must be HTTPS**: Tabby validates that target URLs use HTTPS. The register command automatically rewrites `http://localhost` → `https://localhost` for local dev. Remote URLs should already be HTTPS.

> **`--force` flag**: Re-creates the Application even if one exists in cache. Use this when you need to start fresh after a broken registration.

### Step 6 — Validate

```bash
python cli/elicitation_setup.py profile validate workspaces/login-recordings/tabby-<app>-<date>.json
```

Runs a full end-to-end check:
1. Seeds a browser session record directly in PostgreSQL (the K8s controller is not running locally).
2. Mounts credential files to `/tmp/tabby-local-secrets/<secret-name>/{username,password}` — prompts you for actual credentials if `has_login` is true.
3. Starts the Tabby worker (`pnpm --filter @browser-hitl/worker start`) in headless mode.
4. Polls until `health_result_type = PASS`.
5. Promotes the session to `HEALTHY` via direct SQL.

If validation succeeds, the profile is ready for use.

**Common validate failures:**

| Error | Cause | Fix |
|-------|-------|-----|
| `EADDRINUSE :8091` | Previous worker still running | `python cli/tabby_setup.py session stop` then retry |
| `wait_for_url` timeout | Wrong post-login URL pattern | Edit bundle `success_url_pattern` to use `/**` suffix, e.g. `https://app.example.com/**` |
| `health_result_type: TRANSIENT_FAIL` | Keepalive URL returns non-200 | Update `keepalive_url` in bundle to point to a page that returns 200 when authenticated |
| "Credentials not found" | Credential files missing | Re-run validate — it recreates the files |
| "No username/password field detected" | Generator couldn't infer field roles | Manually edit the bundle and add `field_role: "username"` / `"password"` to the relevant steps |

### Step 7 — Promote

```bash
python cli/elicitation_setup.py profile promote <profile_db_id>
```

The `profile_db_id` (UUID) is printed by the `register` command and stored in the bundle JSON. This promotes STAGING → CANARY → ACTIVE.

After promotion, the profile is available for `test_runner` to request credentials via `POST /credentials/request`.

---

## Shortcut: `profile import`

Combines export + review + register (and optionally validate) in one command:

```bash
python cli/elicitation_setup.py profile import <session_id>
python cli/elicitation_setup.py profile import <session_id> --validate
```

Useful when you're confident the recording was clean. If anything fails, fall back to running each step individually to diagnose.

---

## Command Reference

| Command | What It Does |
|---------|-------------|
| `profile record <app> <url>` | Create login recording session, print Chrome instructions |
| `profile list` | List all login recording sessions |
| `profile export <session_id>` | Analyze session → write bundle JSON to `workspaces/login-recordings/` |
| `profile review <bundle>` | Print checklist of items to verify before registering |
| `profile register <bundle>` | Create Application + STAGING ServiceProfile in Tabby |
| `profile validate <bundle>` | Seed session, start worker, verify HEALTHY |
| `profile promote <profile_db_id>` | Promote STAGING → CANARY → ACTIVE |
| `profile import <session_id>` | Shortcut: export + review + register |
| `profile import <session_id> --validate` | Shortcut: export + review + register + validate |

---

## Bundle File Structure

Bundle files (written to `workspaces/login-recordings/`) contain:

```json
{
  "app_name": "MyApp",
  "login_url": "https://app.example.com/login",
  "login_config": {
    "has_login": true,
    "credential_ref": "k8s:secret/myapp-credentials",
    "steps": [
      {"action": "goto", "url": "https://app.example.com/login"},
      {"action": "fill", "selector": "#username", "value": "${USERNAME}"},
      {"action": "fill", "selector": "#password", "value": "${PASSWORD}"},
      {"action": "click",  "selector": "#submit"},
      {"action": "wait_for_url", "pattern": "https://app.example.com/**"}
    ],
    "success_url_pattern": "https://app.example.com/**",
    "success_selector": null
  },
  "keepalive_config": {
    "interval_seconds": 120,
    "health_checks": [{"url": "https://app.example.com/dashboard", "expected_status": 200}],
    "actions": []
  },
  "export_policy": {
    "ttl_seconds": 300,
    "encryption": {"algo": "AES-256-GCM"},
    "target_urls": ["https://app.example.com"]
  }
}
```

**Key constraints (Tabby API validation):**
- `target_urls` must be HTTPS
- `keepalive_config.interval_seconds` must be ≥ 60
- `keepalive_config.health_checks` must be a non-empty array
- `export_policy.encryption.algo` must be `"AES-256-GCM"`
- `export_policy.ttl_seconds` must be ≥ 300

---

## After Registration: Using the Profile in Tests

Add `tabby_profile_id` to the action's `adopt_profile.json`:

```json
{
  "tabby_profile_id": "myapp-standard",
  "base_url": "https://app.example.com",
  "security_params": {}
}
```

Then ensure a healthy session exists before running tests:

```bash
python cli/tabby_setup.py session ensure --profile myapp-standard
source .env
python cli/test_runner.py <action-id>
```

**📖 Tabby infrastructure details: `prompts/system/TABBY_CREDENTIALS_PROMPT.md`**

---

## Key Files

| File | Role |
|------|------|
| `cli/elicitation_setup.py` | All `profile` subcommands (record/export/review/register/validate/promote/import) |
| `elicitation/backend/app/routers/login_sessions.py` | Backend routes: `/login-sessions`, `/analyze`, `/bundle` |
| `elicitation/backend/app/login_profile_generator.py` | Converts click/URL events into Tabby bundle JSON |
| `elicitation/extension/popup/api.js` | Extension API: `startLoginRecording`, `injectLoginRecorder` |
| `workspaces/login-recordings/` | Default output directory for bundle files (gitignored) |
| `tabby/.tabby-abcd-client.json` | Cached provisioning state: app_id, profile_db_id, credential_ref |
