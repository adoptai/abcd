# Hierarchical Workspace Management System

**PURPOSE**: This prompt explains the three-level workspace hierarchy in ABCD and how to use it effectively.

**LOAD THIS WHEN**: Creating workspaces, managing environments, or working with Uber Agents.

---

## Workspace Hierarchy

ABCD supports a **three-level workspace hierarchy**:

```
workspaces/
├── .env                              # Root fallback
├── adopt_profile.json                # Root fallback profile
├── .active_env                       # Tracks active environment
│
├── {environment}/                    # LEVEL 1: Environment
│   ├── .env                          # Env-specific config
│   ├── adopt_profile.json            # Shared credentials
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
│   └── actions/                      # Standalone actions in env
│       └── {action}/                 # LEVEL 3: Standalone action
│
└── standalone/                       # Global standalone (no env)
    └── {action}/
```

---

## Configuration Inheritance

Config files resolve in priority order (first found wins):

```
1. Action adopt_profile.json       ← Most specific
2. Agent adopt_profile.json
3. Environment adopt_profile.json
4. Root adopt_profile.json         ← Fallback
```

**Example**: An action in `workspaces/prod/agents/my-agent/actions/get-data/` will check:
1. `workspaces/prod/agents/my-agent/actions/get-data/adopt_profile.json`
2. `workspaces/prod/agents/my-agent/adopt_profile.json`
3. `workspaces/prod/adopt_profile.json`
4. `workspaces/adopt_profile.json`

---

## CLI Commands Reference

### Environment Commands

```bash
# Create environment
python cli/workspace.py env create --id staging --name "Staging" --target staging --use

# List environments
python cli/workspace.py env list

# Show details
python cli/workspace.py env show staging

# Set active (used as default for other commands)
python cli/workspace.py env use staging

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

### agent.json (Agent)

```json
{
  "agent_id": "inventory-agent",
  "name": "Inventory Agent",
  "description": "Manages inventory operations",
  "type": "uber_agent",
  "remote_action_id": "abc-123-def",
  "sub_actions": [
    {
      "action_id": "get-orderpoints",
      "remote_action_id": "111-222-333",
      "title": "get-orderpoints",
      "description": "Fetch orderpoints",
      "required": true
    }
  ],
  "created_at": "2026-01-29T10:00:00Z"
}
```

### metadata.json (Action)

```json
{
  "workflow_id": "get-orderpoints",
  "title": "get-orderpoints",
  "action_id": "111-222-333",
  "agent_name": "inventory-agent",
  "env_name": "production-client-a",
  "is_tool_mode": true,
  "is_visible_in_list": true,
  "created_at": "2026-01-29T10:00:00Z"
}
```

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


