# Playground Profiles & Token Manager

This document covers managing playground profiles, token configs, the `adopt_profile.json` lifecycle, HAR file analysis, and the local-to-platform testing progression.

---

## 1. Overview and Concepts

### Playground Profiles

Playground profiles are **remote configurations** stored on the AdoptAI platform. They define:
- API and app base URLs for target applications
- Security headers (authentication cookies, tokens, etc.)
- User properties (workflow params, metadata)

The Chrome extension and platform use these profiles at runtime to authenticate API requests against the target application.

### Token Configs (Token Manager)

Token configs define **how to dynamically extract** authentication values at runtime. Instead of storing static cookies or tokens that expire, a token config tells the Chrome extension how to obtain the current value by:
- Reading from **localStorage** or **sessionStorage**
- Extracting **cookies** from specific domains
- Querying **DOM elements** via XPath selectors
- Running **custom JavaScript** in a sandboxed iframe

When a playground profile's security header value matches a token config's `name`, the Chrome extension resolves it dynamically at runtime instead of using the static string.

### adopt_profile.json

The local `adopt_profile.json` file is the **development-time equivalent** of playground profiles. It provides base URLs, security params, and workflow params for local WDL testing via `test_runner.py`. It uses static values extracted from browser sessions or HAR files.

### Lifecycle

```
Local Development          Platform/Extension
┌──────────────────┐      ┌─────────────────────┐
│ adopt_profile.json│─push→│ Playground Profiles  │
│ (static tokens)  │      │ (security_headers)   │
└──────────────────┘      └─────────┬───────────┘
                                    │
                          ┌─────────▼───────────┐
                          │ Token Configs        │
                          │ (dynamic extraction) │
                          └─────────────────────┘
```

1. **Local dev**: Edit `adopt_profile.json` with static values for testing
2. **Push to platform**: Sync to remote playground profiles
3. **Configure tokens**: Replace static values with token config names for production
4. **Chrome extension**: Resolves token names dynamically at runtime

---

## 2. adopt_profile.json Deep Dive

### Schema

```json
{
  "base_url": "https://example.com",
  "application_base_url": "https://example.com",
  "workflow_params": {
    "api_version": "v57.0",
    "rest_base": "https://api.example.com"
  },
  "security_params": {
    "Cookie": "sid=...",
    "Authorization": "Bearer ..."
  },
  "profiles_map": {
    "ApplicationName": {
      "base_url": "https://api.example.com",
      "security_params": {
        "access_token": "..."
      }
    }
  },
  "mcp_profiles_map": {
    "integration-name": {
      "tool_id": "...",
      "security_params": {}
    }
  }
}
```

### Field Mapping (Local to Remote)

| Local (`adopt_profile.json`)  | Remote (Playground Profile)  |
|-------------------------------|------------------------------|
| `base_url`                    | `api_base_url`               |
| `application_base_url`        | `app_base_url`               |
| `security_params`             | `security_headers`           |
| `workflow_params`             | `user_properties`            |
| `profiles_map` keys           | `application` field          |

### Config Inheritance

Profiles merge in order (later overrides earlier):

1. **Environment** `adopt_profile.json` (base)
2. **Agent** `adopt_profile.json` (if action is a sub-action)
3. **Action** `adopt_profile.json` (most specific)

### profiles_map

When an action calls multiple APIs across different applications, each application gets its own entry in `profiles_map`. The key (e.g., `SalesforceCPQ`) maps to the `application` field on the remote playground profile.

WDL REST blocks reference the application:
```json
{"id": "apiCall", "operation": "REST", "application": "SalesforceCPQ", ...}
```

The executor uses `profiles_map.SalesforceCPQ` for `base_url` and `security_params`.

---

## 3. HAR File Analysis Workflow

### When to Use

HAR (HTTP Archive) files capture all network requests made by the browser. They are useful when you need to extract authentication tokens, session cookies, CSRF tokens, or other security parameters that the target application uses. Analyzing a HAR file lets you identify the exact headers, cookies, and request parameters needed to populate `adopt_profile.json` and playground profiles.

### Step-by-Step

1. **Capture HAR**: In Chrome DevTools → Network tab → perform the actions you want to automate in the target application → right-click → "Save all as HAR with content"
2. **Analyze the HAR file**: Open the HAR (it's JSON) and look for:
   - **Authentication headers**: `Authorization`, `Cookie`, custom auth headers on API requests
   - **Session cookies**: Look at `Set-Cookie` responses and `Cookie` request headers
   - **CSRF / anti-forgery tokens**: Often embedded in page HTML or sent as request headers
   - **API base URLs**: Identify the domains and base paths used for API calls
   - **Application-specific tokens**: Framework tokens (e.g., Aura tokens, XSRF tokens, bearer tokens)
3. **Identify domain-to-profile mapping**: If the application uses multiple API domains, each one may need its own `profiles_map` entry with separate `base_url` and `security_params`
4. **Populate `adopt_profile.json`**: Fill in `base_url`, `security_params`, and `profiles_map` entries based on what you found
5. **Handle HttpOnly cookies**: Chrome strips HttpOnly cookies from HAR exports. Get these from DevTools → Application → Cookies and add them manually to `security_params`
6. **Test**: Run `python cli/test_runner.py <action-id>` to validate the configuration
7. **Push**: Sync to remote with `python cli/playground_profiles.py push`

### What to Look For in a HAR File

| What to Extract          | Where to Find It                                               |
|--------------------------|----------------------------------------------------------------|
| Session cookies          | `Cookie` request headers, `Set-Cookie` response headers        |
| Bearer / access tokens   | `Authorization` headers, token exchange response bodies        |
| CSRF tokens              | Hidden form fields, meta tags, custom request headers          |
| API base URLs            | Request URLs — identify base domain and path patterns          |
| Framework tokens         | Request bodies (e.g., POST payloads), embedded in page HTML    |
| Application identifiers  | URL patterns, request headers that distinguish sub-applications |

### Security Rules

- **NEVER** commit HAR files (`.gitignore` already excludes `*.har`)
- **NEVER** write client names, URLs, auth tokens, or sensitive data into open source repos
- HAR files and sensitive tokens are for local development only

---

## 4. Playground Profiles CLI Reference

All commands use: `python cli/playground_profiles.py [global-flags] <command> [options]`

**Global flags**: `-v, --verbose`, `-e, --env <env-id>`

### list

List all remote playground profiles.

```bash
python cli/playground_profiles.py list
python cli/playground_profiles.py list --integration-id <id>
python cli/playground_profiles.py list --json
```

### show

Show a single profile by ID or the default profile.

```bash
python cli/playground_profiles.py show <profile-id>
python cli/playground_profiles.py show --default
python cli/playground_profiles.py show <profile-id> --json
```

### create

Create a new playground profile.

```bash
python cli/playground_profiles.py create \
  --name "SalesforceCPQ" \
  --application SalesforceCPQ \
  --api-base-url "https://example.vf.force.com" \
  --app-base-url "https://example.vf.force.com" \
  --security-header "Cookie=sid=..." \
  --security-header "Referer=https://example.vf.force.com/apex/sb" \
  --user-property "api_version=v57.0"
```

### edit

Update fields on an existing profile.

```bash
python cli/playground_profiles.py edit <profile-id> \
  --api-base-url "https://new-url.com" \
  --set-header "Cookie=sid=new_value" \
  --remove-header "OldHeader" \
  --set-property "api_version=v58.0" \
  --remove-property "deprecated_param"
```

### delete

Delete a profile (soft-delete).

```bash
python cli/playground_profiles.py delete <profile-id>
python cli/playground_profiles.py delete <profile-id> --force
```

### push

Push local `adopt_profile.json` values to matching remote profiles.

```bash
python cli/playground_profiles.py push
python cli/playground_profiles.py push --dry-run
python cli/playground_profiles.py push --profile path/to/adopt_profile.json
python cli/playground_profiles.py push --only SalesforceCPQ --only SalesforceREST
python cli/playground_profiles.py push --exclude SalesforceREST
```

**Matching logic**: Local `profiles_map` keys are matched to remote profiles by the `application` field. Root `security_params` are pushed to the remote profile whose `api_base_url` matches the local `base_url`.

### pull

Pull remote profiles into local `adopt_profile.json`.

```bash
python cli/playground_profiles.py pull
python cli/playground_profiles.py pull --dry-run
python cli/playground_profiles.py pull --merge
python cli/playground_profiles.py pull --profile path/to/adopt_profile.json
```

- Without `--merge`: overwrites the local profile completely
- With `--merge`: merges remote data into the existing local profile

### set-token

Set a security header to reference a token config by name.

```bash
python cli/playground_profiles.py set-token <profile-id> Cookie sid_cookie_token
```

This sets `security_headers["Cookie"] = "sid_cookie_token"`. At runtime, the Chrome extension resolves `sid_cookie_token` by running the matching token config's extraction logic.

### show-tokens

Show which headers reference token configs vs static values.

```bash
python cli/playground_profiles.py show-tokens <profile-id>
```

Output distinguishes between:
- `🔗 Cookie: sid_cookie_token  (-> token config)` — dynamically resolved
- `📝 Referer: https://...  (static)` — literal value

---

## 5. Token Manager CLI Reference

All commands use: `python cli/playground_profiles.py token <command> [options]`

### token list

```bash
python cli/playground_profiles.py token list
python cli/playground_profiles.py token list --search "sid"
python cli/playground_profiles.py token list --published
python cli/playground_profiles.py token list --unpublished
python cli/playground_profiles.py token list --integration-id <id>
python cli/playground_profiles.py token list --json
```

### token show

```bash
python cli/playground_profiles.py token show <token-id>
python cli/playground_profiles.py token show <token-id> --json
```

### token create

```bash
# Cookie extraction
python cli/playground_profiles.py token create \
  --name sid_cookie \
  --domain-suffix .force.com \
  --storage-type cookie \
  --cookie-key sid \
  --cookie-domain .lightning.force.com

# All cookies from a domain
python cli/playground_profiles.py token create \
  --name all_sf_cookies \
  --domain-suffix .force.com \
  --storage-type cookie \
  --all-cookies \
  --cookie-domain .lightning.force.com

# localStorage
python cli/playground_profiles.py token create \
  --name auth_token \
  --domain-suffix .example.com \
  --storage-type localStorage \
  --storage-key "auth_token"

# DOM element (XPath)
python cli/playground_profiles.py token create \
  --name csrf_token \
  --domain-suffix .example.com \
  --storage-type domElement \
  --dom-selector "//meta[@name='csrf-token']" \
  --dom-attribute content

# Custom JavaScript
python cli/playground_profiles.py token create \
  --name bearer_token \
  --domain-suffix .example.com \
  --storage-type customScript \
  --custom-script-file scripts/extract_bearer.js

# With parser logic (post-processing)
python cli/playground_profiles.py token create \
  --name formatted_cookie \
  --domain-suffix .force.com \
  --storage-type cookie \
  --cookie-key sid \
  --parser-logic "return 'sid=' + value;"
```

### token edit

```bash
python cli/playground_profiles.py token edit <token-id> \
  --cookie-key new_cookie_name \
  --parser-logic "return value.trim();"
```

### token delete

```bash
python cli/playground_profiles.py token delete <token-id>
python cli/playground_profiles.py token delete <token-id> --force
```

### token publish / unpublish

```bash
python cli/playground_profiles.py token publish <id1> <id2> <id3>
python cli/playground_profiles.py token unpublish <id1>
```

### Storage Types Reference

| Type             | Required Fields                       | Description                                     |
|------------------|---------------------------------------|-------------------------------------------------|
| `localStorage`   | `--storage-key`                       | Reads `localStorage.getItem(key)`               |
| `sessionStorage`  | `--storage-key`                       | Reads `sessionStorage.getItem(key)`             |
| `cookie`         | `--cookie-key` or `--all-cookies`     | Uses `chrome.cookies.getAll()`                  |
| `domElement`     | `--dom-selector`                      | XPath query, reads attribute or text content    |
| `customScript`   | `--custom-script` or `--custom-script-file` | Arbitrary JS in sandbox, supports `@{tokens.X}` |

### Token Dependencies

Custom scripts can reference other tokens via `@{tokens.token_name}`. Dependencies are resolved recursively before execution. Circular dependencies are detected and rejected.

### Parser Logic

Optional post-processing JS that runs after extraction. Receives the raw extracted value and returns the transformed value. Runs in a sandboxed iframe.

---

## 6. The Testing Lifecycle (Local to Platform)

### Phase A: Local Development

Use `adopt_profile.json` with static security params for local WDL testing.

1. Extract tokens from HAR files or browser DevTools
2. Populate `adopt_profile.json` with base URLs and security params
3. Test actions locally: `python cli/test_runner.py <action-id>`
4. Iterate until tests pass

```bash
# Test locally
python cli/test_runner.py my-action --compile
python cli/test_runner.py my-action --all
```

### Phase B: Push to Playground

Sync local config to remote playground profiles so the platform can use the same configuration.

```bash
# Preview
python cli/playground_profiles.py push --dry-run

# Apply
python cli/playground_profiles.py push
```

This validates that the same configuration works on the platform side, not just locally.

### Phase C: Configure Token Manager

Replace static security params with token config names for production use. Static tokens expire; token configs extract fresh values at runtime.

```bash
# Create token configs for each credential type
python cli/playground_profiles.py token create \
  --name sf_sid --domain-suffix .force.com \
  --storage-type cookie --cookie-key sid

# Link token configs to profile headers
python cli/playground_profiles.py set-token <profile-id> Cookie sf_sid

# Verify the setup
python cli/playground_profiles.py show-tokens <profile-id>
```

### Phase D: Verify in Chrome Extension

The Chrome extension:
1. Fetches playground profiles and token configs from the platform
2. For each security header value, checks if it matches a token config name
3. If matched, runs the token config's extraction logic (cookie, localStorage, DOM, etc.)
4. Replaces the header value with the dynamically extracted value
5. Uses the resolved headers for API requests

This is the final validation step — confirm the Chrome extension correctly resolves all dynamic tokens and API calls succeed.

---

## 7. Profile Workflow for New Actions

When creating a new action, follow this workflow to set up the correct `adopt_profile.json` and playground profiles. The goal is to reuse existing remote profiles when available and only create from scratch when necessary.

### Step 1: Check Remote for Existing Profiles

Before building anything locally, check if playground profiles already exist for the APIs the new action will call.

```bash
# List all remote profiles for this environment
python cli/playground_profiles.py list

# Check for profiles matching a specific application name
python cli/playground_profiles.py list --json | python -m json.tool
```

Look at `application` and `api_base_url` fields to find profiles that match the target APIs. For example, if the new action calls a CRM API at `https://crm.example.com/api`, look for profiles with that base URL or a related application name.

### Step 2a: Remote Profiles Exist — Pull and Adapt

If matching profiles exist remotely, pull them to seed the local `adopt_profile.json`:

```bash
# Pull remote profiles into local adopt_profile.json
python cli/playground_profiles.py pull --merge

# Or preview first
python cli/playground_profiles.py pull --dry-run
```

Then review and adjust the local file:
- Add or update `workflow_params` needed by the new action's WDL
- Adjust `profiles_map` entries if the action calls additional APIs not covered by existing profiles
- Update `security_params` if the action needs different or additional auth headers

### Step 2b: No Remote Profiles — Create from HAR / DevTools

If no matching profiles exist, analyze available HAR files and/or browser DevTools to build the profile from scratch:

1. **Analyze the HAR file** to identify:
   - API base URLs (→ `base_url`, `profiles_map.*.base_url`)
   - Authentication headers and cookies (→ `security_params`)
   - Application-specific tokens (→ `security_params` or `profiles_map.*.security_params`)
   - Multiple API domains (→ separate `profiles_map` entries, each with its own `base_url` and `security_params`)
2. **Populate `adopt_profile.json`** with the extracted values
3. **Identify token sources** for later token config creation — note where each credential comes from:
   - Is it a cookie? Which domain and key?
   - Is it in localStorage/sessionStorage? Which key?
   - Is it embedded in the page DOM?
   - Does it require custom JS to extract?

### Step 3: Build and Test Iteratively

As you develop the action's WDL, test continuously against the local profile:

```bash
# Compile check
python cli/test_runner.py <action-id> --compile

# Run tests
python cli/test_runner.py <action-id> --all
```

Each time a test fails due to auth or configuration issues, update `adopt_profile.json` accordingly. Common iterations:
- Adding missing security headers discovered from API error responses
- Adjusting base URLs when API paths differ from what the HAR showed
- Adding `profiles_map` entries when the WDL introduces REST blocks with new `application` values
- Adding `workflow_params` that the WDL references via `$wf{...}` expressions

### Step 4: Push to Remote

Once all tests pass locally, push the finalized profiles to the platform:

```bash
# Preview what will be synced
python cli/playground_profiles.py push --dry-run

# Push all profiles
python cli/playground_profiles.py push

# Or push specific applications only
python cli/playground_profiles.py push --only ApplicationName
```

If remote profiles already existed (Step 2a), push updates them. If they didn't exist (Step 2b), this creates new playground profiles on the platform.

### Step 5: Create Token Configs (When Possible)

Based on the HAR analysis from Step 2b (or knowledge of the target application's auth mechanism), create token configs so the Chrome extension can resolve credentials dynamically instead of relying on static values that expire:

```bash
# Cookie-based credential
python cli/playground_profiles.py token create \
  --name app_session_cookie \
  --domain-suffix .example.com \
  --storage-type cookie \
  --cookie-key session_id \
  --cookie-domain .example.com

# localStorage-based token
python cli/playground_profiles.py token create \
  --name app_auth_token \
  --domain-suffix .example.com \
  --storage-type localStorage \
  --storage-key "auth_token"

# Link token configs to profile headers
python cli/playground_profiles.py set-token <profile-id> Cookie app_session_cookie
python cli/playground_profiles.py set-token <profile-id> Authorization app_auth_token

# Publish so the Chrome extension can use them
python cli/playground_profiles.py token publish <token-id>

# Verify the setup
python cli/playground_profiles.py show-tokens <profile-id>
```

Not all credentials can be tokenized — some may require custom JavaScript or may not be extractable at runtime. Prioritize tokenizing credentials that expire frequently (session cookies, short-lived tokens).

### Summary Flow

```
New action needed
│
├─ Check remote: playground_profiles.py list
│
├─ Matching profiles exist?
│   ├─ YES → pull --merge → adapt locally → test → push updates
│   └─ NO  → analyze HAR → build adopt_profile.json → test → push new profiles
│
├─ All tests pass?
│   ├─ YES → push to remote
│   └─ NO  → iterate on adopt_profile.json, fix WDL, re-test
│
└─ Create token configs?
    ├─ Credentials are extractable → token create + set-token + publish
    └─ Not extractable at runtime → keep static values in playground profile
```

---

## 8. Decision Tree

```
What do you need to do?
│
├─ Creating a new action?
│   → Check remote first: playground_profiles.py list
│   → Profiles exist? → pull --merge → adapt → test → push
│   → No profiles? → analyze HAR → build adopt_profile.json → test → push
│   → Then create token configs if credentials are extractable
│   → See: Section 7 (Profile Workflow for New Actions)
│
├─ Configure auth for local testing?
│   → Analyze HAR file to identify tokens, cookies, and base URLs
│   → Edit adopt_profile.json with extracted values
│   → Then test: python cli/test_runner.py <action-id>
│
├─ Push local config to platform?
│   → python cli/playground_profiles.py push --dry-run
│   → python cli/playground_profiles.py push
│
├─ Pull remote config to local?
│   → python cli/playground_profiles.py pull --dry-run
│   → python cli/playground_profiles.py pull [--merge]
│
├─ Create/manage remote profiles?
│   → python cli/playground_profiles.py create --name "..." --application "..."
│   → python cli/playground_profiles.py edit <id> --set-header KEY=VALUE
│   → python cli/playground_profiles.py delete <id>
│
├─ Set up dynamic token extraction?
│   → python cli/playground_profiles.py token create --name ... --storage-type ...
│   → python cli/playground_profiles.py set-token <profile-id> <header> <token-name>
│
├─ See what's configured remotely?
│   → python cli/playground_profiles.py list
│   → python cli/playground_profiles.py show --default
│   → python cli/playground_profiles.py token list
│   → python cli/playground_profiles.py show-tokens <profile-id>
│
└─ Manage token publish status?
    → python cli/playground_profiles.py token publish <id>
    → python cli/playground_profiles.py token unpublish <id>
```
