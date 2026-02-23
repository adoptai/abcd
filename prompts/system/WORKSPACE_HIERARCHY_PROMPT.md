# Hierarchical Workspace Management System

**PURPOSE**: This prompt explains the three-level workspace hierarchy in ABCD and how to use it effectively.

**LOAD THIS WHEN**: Creating workspaces, managing environments, or working with Uber Agents.

---

## ⚠️ IMPORTANT: Environment Required

**All operations require an environment.** The repo ships with a `default` environment that is automatically created and activated.

- **Default Environment**: `workspaces/default/` - use for development
- **Additional Environments**: Create for staging, production, or different clients
- **No standalone actions outside environments** - all actions must be within an environment

---

## Workspace Hierarchy

ABCD uses a **three-level workspace hierarchy**:

```
workspaces/
├── .active_env                       # Tracks active environment
│
├── default/                          # Default environment (auto-created)
│   ├── .env                          # API credentials
│   ├── adopt_profile.json            # Base URL, workflow params
│   ├── env.json                      # Environment metadata
│   │
│   ├── agents/                       # LEVEL 2: Uber Agents
│   │   └── {agent}/
│   │       ├── agent.json            # Agent metadata + sub-actions
│   │       ├── widdle.json           # PROMPT_AND_TOOLS_AGENT WDL
│   │       ├── metadata.json
│   │       ├── adopt_profile.json    # Optional override
│   │       ├── test_cases/
│   │       │   └── subaction_tests/  # Via-agent tests
│   │       └── actions/              # Sub-actions
│   │           └── {action}/         # LEVEL 3: Sub-action
│   │
│   └── actions/                      # Actions in environment
│       └── {action}/                 # LEVEL 3: Action
│
└── {other_environment}/              # Additional environments
    └── ...                           # Same structure as default
```

---

## Configuration Inheritance

Config files resolve in priority order (first found wins):

```
1. Action adopt_profile.json       ← Most specific
2. Agent adopt_profile.json
3. Environment adopt_profile.json  ← Required base
```

**Example**: An action in `workspaces/prod/agents/my-agent/actions/get-data/` will check:
1. `workspaces/prod/agents/my-agent/actions/get-data/adopt_profile.json`
2. `workspaces/prod/agents/my-agent/adopt_profile.json`
3. `workspaces/prod/adopt_profile.json` ← Provides defaults

---

## adopt_profile.json Structure

### Basic Structure

```json
{
  "base_url": "https://default-api.example.com",
  "application_base_url": "",
  "workflow_params": {},
  "security_params": {
    "Authorization": "Bearer token123"
  }
}
```

### Advanced: profiles_map for Multi-API Actions

When your action calls multiple APIs with different base URLs or authentication, use `profiles_map`:

```json
{
  "base_url": "https://default-api.example.com",
  "application_base_url": "",
  "workflow_params": {},
  "security_params": {},
  "profiles_map": {
    "ShippingAPI": {
      "base_url": "https://api.shipping-provider.com",
      "security_params": {
        "API-Key": "your-shipping-api-key"
      }
    },
    "CRMAPI": {
      "base_url": "https://yourorg.crm-provider.com",
      "security_params": {
        "Authorization": "Bearer crm-token"
      }
    }
  }
}
```

### How profiles_map Works

1. **In your WDL REST block**, set the `application` property:
   ```json
   {
     "id": "fetch_shipment_data",
     "operation": "REST",
     "application": "ShippingAPI",
     "method": "GET",
     "url": "/v2/shipments/types"
   }
   ```

2. **At runtime**, the executor looks up `"ShippingAPI"` in `profiles_map`:
   - Uses `profiles_map.ShippingAPI.base_url` as the API base URL
   - Adds `profiles_map.ShippingAPI.security_params` as headers to the request

3. **Fallback**: If no matching profile is found, falls back to the root `base_url` and `security_params`

### profiles_map vs Legacy Configuration

| Approach | When to Use |
|----------|-------------|
| Root `base_url` + `security_params` | Single API actions |
| `profiles_map` | Actions calling multiple APIs with different endpoints/auth |

> ⚠️ **IMPORTANT**: The root `base_url` is **still required** even when using `profiles_map`. The backend validates that a base_url exists before execution. The `profiles_map` entries override the root values at runtime, but the root serves as a required fallback. Set the root `base_url` to your primary/default API endpoint.

### MCP Integration: mcp_profiles_map

For MCP (Model Context Protocol) integrations:

```json
{
  "mcp_profiles_map": {
    "slack-integration": {
      "tool_id": "slack-send-message",
      "security_params": {
        "Authorization": "Bearer xoxb-slack-token"
      }
    }
  }
}
```

---

## Environment Validation (Before Creating Actions)

**IMPORTANT**: Before creating any action, validate that the user's request matches the active environment's purpose.

### env.json Schema

```json
{
  "env_id": "clientx-marketing",
  "name": "Client X Marketing",
  "description": "Marketing automation actions for Client X. Email campaigns, social media, analytics.",
  "target": "production",
  "client": "clientx",
  "domain": "marketing",
  "allowed_domains": ["marketing", "analytics", "email"],
  "agents": [],
  "actions": []
}
```

### Validation Process

1. **Read the active environment's description**
2. **Compare against user's request**
3. **If mismatch**, ask user to confirm:

```
I notice you want to create an [inventory control] action, but the active 
environment "clientx-marketing" is configured for: "Marketing automation 
actions for Client X. Email campaigns, social media, analytics."

Would you like me to:
1. Proceed anyway in the current environment
2. Switch to a different environment
3. Create a new environment for inventory

To switch: python cli/workspace.py env use <env-id>
To list available: python cli/workspace.py env list
```

---

## CLI Commands Reference

### Global Flags

All workspace CLI scripts support the following universal flags:

| Flag | Short | Description |
|------|-------|-------------|
| `--verbose` | `-v` | Show detailed debug output: workspace resolution paths, API calls made, files read/written, action ID resolution. Useful when troubleshooting. |
| `--dry-run` | — | Simulate the operation without making changes (no file writes, no API calls). Prints what *would* happen. |

```bash
# Example: debug why an action can't be found
python cli/status.py my-action --verbose

# Example: check what save would do before committing
python cli/save_wdl_draft.py my-action --dry-run

# Example: combine both
python cli/save_wdl_draft.py my-action --dry-run --verbose
```

> **Note**: Read-only scripts (`status.py`, `validate.py`, `list_wdl_versions.py`, `discover.py`) accept both flags for CLI consistency — `--dry-run` is a no-op on them.

---

### Environment Commands

```bash
# Create environment with description
python cli/workspace.py env create \
  --id clientx-inventory \
  --name "Client X Inventory" \
  --description "Inventory and warehouse management for Client X" \
  --target production \
  --use

# List environments (shows descriptions)
python cli/workspace.py env list

# Show details including description
python cli/workspace.py env show clientx-inventory

# SWITCH active environment
python cli/workspace.py env use clientx-inventory

# Delete
python cli/workspace.py env delete old-env --force
```

### Agent Commands

```bash
# Create agent (Uber Agent template)
python cli/workspace.py agent create --id my-agent --name "My Agent" --description "Description"

# List agents in active environment
python cli/workspace.py agent list

# Show agent details
python cli/workspace.py agent show my-agent

# Add sub-action to agent
python cli/workspace.py agent add-subaction \
  --agent my-agent \
  --action get-orderpoints \
  --remote-id 111-222-333 \
  --title get-orderpoints

# Remove sub-action
python cli/workspace.py agent remove-subaction --agent my-agent --action get-orderpoints

# Checkout agent from remote (with all sub-actions)
python cli/workspace.py agent checkout \
  --remote-id abc-123-def \
  --env staging \
  --include-subactions

# Sync local with remote
python cli/workspace.py agent sync my-agent --pull
```

### Action Commands

```bash
# Create standalone action in environment
python cli/workspace.py action create --id my-action --title "My Action"

# Create sub-action within agent
python cli/workspace.py action create --id get-data --title "get-data" --agent my-agent

# List actions
python cli/workspace.py action list                    # All in active env
python cli/workspace.py action list --agent my-agent   # Only in agent
```

### Move Actions/Agents Between Workspaces

Use `move_action.py` to move actions or agents between LOCAL workspace environments.
This is useful for:
- Promoting actions from staging to production
- Copying actions between different clients
- Moving agents with all their sub-actions

**⚠️ This script requires explicit user confirmation and cannot be auto-run by AI agents.**

```bash
# List available local workspace environments
python cli/move_action.py --list-envs

# List actions/agents in a workspace
python cli/move_action.py --list-actions --env clienta-prod
python cli/move_action.py --list-agents --env clientb-staging

# Move action within same client (staging → prod)
python cli/move_action.py my-action --from clienta-staging --to clienta-prod

# Move action between different clients
python cli/move_action.py my-action --from clienta-prod --to clientb-prod

# Copy action (keep original in source workspace)
python cli/move_action.py my-action --from clienta-staging --to clienta-prod --copy

# Move entire agent with all sub-actions
python cli/move_action.py my-agent --agent --from clienta-staging --to clienta-prod

# Move action into an agent in destination workspace
python cli/move_action.py my-action --from clienta-staging --to clientb-prod --dest-agent target-agent
```

**After moving an agent**, sub-actions need to be re-registered in the new environment:
1. Switch to dest env: `python cli/workspace.py env use <dest-workspace>`
2. For each sub-action: save draft → publish → enable tool mode
3. Update agent WDL with new action_ids
4. Save and publish the agent

### Profile Commands

```bash
# Show resolved profile (with inheritance)
python cli/workspace.py profile show --action my-action

# Update profile at environment level
python cli/workspace.py profile update --env staging --base-url "https://api.example.com"

# Update profile at agent level
python cli/workspace.py profile update --agent my-agent --cookie "session=xyz"
```

---

## Metadata Files

### env.json (Environment)

```json
{
  "env_id": "production-client-a",
  "name": "Production - Client A",
  "description": "Production environment",
  "target": "production",
  "client": "client-a",
  "agents": ["inventory-agent"],
  "standalone_actions": ["quick-lookup"],
  "created_at": "2026-01-29T10:00:00Z"
}
```

### metadata.json (Unified — All Workspace Types)

`metadata.json` is the **single source of truth** for all workspace types (agents, sub-actions, standalone actions). The `type` field distinguishes them.

**Agent (`type: "agent"`):**
```json
{
  "workflow_id": "inventory-agent",
  "title": "Inventory Agent",
  "description": "Manages inventory operations",
  "type": "agent",
  "action_id": "abc-123-def",
  "env_name": "production-client-a",
  "is_tool_mode": false,
  "is_visible_in_list": true,
  "sub_actions": [
    {
      "action_id": "get-orderpoints",
      "remote_action_id": "111-222-333",
      "title": "get-orderpoints"
    }
  ],
  "created_at": "2026-01-29T10:00:00Z",
  "updated_at": "2026-01-29T10:00:00Z"
}
```

**Sub-action (`type: "sub_action"`):**
```json
{
  "workflow_id": "get-orderpoints",
  "title": "get-orderpoints",
  "type": "sub_action",
  "action_id": "111-222-333",
  "agent_name": "inventory-agent",
  "env_name": "production-client-a",
  "is_tool_mode": true,
  "is_visible_in_list": false,
  "created_at": "2026-01-29T10:00:00Z"
}
```

**Standalone action (`type: "action"`):**
```json
{
  "workflow_id": "quick-lookup",
  "title": "Quick Lookup",
  "type": "action",
  "action_id": "xyz-987",
  "env_name": "production-client-a",
  "is_tool_mode": true,
  "is_visible_in_list": true,
  "created_at": "2026-01-29T10:00:00Z"
}
```

> **Legacy `agent.json`**: Older workspaces may still have `agent.json`. The workspace manager auto-migrates these to `metadata.json` on first access — no manual action needed.

---

## Best Practices

1. **Use environments** to separate staging, production, and client-specific configs
2. **Set active environment** with `env use` to avoid specifying `--env` on every command
3. **Keep credentials at env level** unless agent needs different ones
4. **Enable tool mode** on all sub-actions before adding to agent
5. **Use agent checkout** to get existing agents with all sub-actions

---

## Related Prompts

- **UBER_AGENT_PROMPT.md** - Creating and managing Uber Agents
- **TESTING_PROMPT.md** - Testing strategies including via-agent tests
- **CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md** - Creating WDL workflows


