#!/usr/bin/env python3
"""
Workspace CLI - Hierarchical workspace management.

Commands:
    env create    - Create a new environment
    env list      - List all environments
    env show      - Show environment details
    env use       - Set active environment
    env delete    - Delete an environment

    agent create  - Create a new agent (Uber Agent)
    agent list    - List agents in environment
    agent show    - Show agent details
    agent add-subaction    - Add sub-action to agent
    agent remove-subaction - Remove sub-action from agent
    agent checkout - Download agent from remote
    agent sync    - Sync agent with remote

    action create - Create a new action
    action list   - List actions
    action move   - Move action to different agent
    action promote - Promote standalone to sub-action

    profile show  - Show resolved profile
    profile update - Update profile
    profile copy  - Copy profile between levels
"""

import argparse
import json
import sys
from pathlib import Path

# Add parent to path for imports
CLI_DIR = Path(__file__).parent
PROJECT_ROOT = CLI_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from cli.wdl_common.workspace_manager import (
    HierarchicalWorkspaceManager,
    get_workspace_manager,
    WORKSPACES_DIR,
)


def cmd_env_create(args: argparse.Namespace) -> int:
    """Create a new environment."""
    manager = get_workspace_manager()
    success, path, message = manager.create_env(
        env_id=args.id,
        name=args.name,
        description=args.description or "",
        target=args.target or "staging",
        client=args.client or "",
    )

    if success:
        print(f"✅ {message}")
        print(f"   Path: {path}")
        if args.use:
            manager.active_env = args.id
            print(f"   Set as active environment")
        return 0
    else:
        print(f"❌ {message}")
        return 1


def cmd_env_list(args: argparse.Namespace) -> int:
    """List all environments."""
    manager = get_workspace_manager()
    envs = manager.list_envs()

    if not envs:
        print("No environments found. Create one with: workspace.py env create")
        return 0

    print("\n" + "=" * 70)
    print("📁 ENVIRONMENTS")
    print("=" * 70)
    print(f"{'ID':<25} {'Name':<25} {'Target':<12} {'Active'}")
    print("-" * 70)

    for env in envs:
        env_id = env.get("env_id", "?")[:23]
        name = env.get("name", "Untitled")[:23]
        target = env.get("target", "?")[:10]
        active = "✓" if env.get("is_active") else ""
        print(f"{env_id:<25} {name:<25} {target:<12} {active}")

    print("=" * 70)
    return 0


def cmd_env_show(args: argparse.Namespace) -> int:
    """Show environment details."""
    manager = get_workspace_manager()
    env = manager.get_env(args.id)

    if not env:
        print(f"❌ Environment not found: {args.id}")
        return 1

    print("\n" + "=" * 60)
    print(f"📁 ENVIRONMENT: {env.get('name', args.id)}")
    print("=" * 60)
    print(f"  ID:          {env.get('env_id')}")
    print(f"  Name:        {env.get('name')}")
    print(f"  Description: {env.get('description', '-')}")
    print(f"  Target:      {env.get('target')}")
    print(f"  Client:      {env.get('client', '-')}")
    print(f"  Active:      {'Yes' if env.get('is_active') else 'No'}")
    print(f"  Path:        {env.get('path')}")
    print(f"  Created:     {env.get('created_at', '-')[:19]}")

    # List agents
    agents = manager.list_agents(args.id)
    if agents:
        print(f"\n  Agents ({len(agents)}):")
        for agent in agents:
            print(f"    - {agent.get('agent_id')}: {agent.get('name', 'Untitled')}")

    # List standalone actions
    actions = manager.list_actions(env_name=args.id, include_subactions=False)
    if actions:
        print(f"\n  Standalone Actions ({len(actions)}):")
        for action in actions:
            print(f"    - {action.get('action_id')}")

    print("=" * 60)
    return 0


def cmd_env_use(args: argparse.Namespace) -> int:
    """Set active environment."""
    manager = get_workspace_manager()

    try:
        manager.active_env = args.id
        print(f"✅ Active environment set to: {args.id}")
        return 0
    except ValueError as e:
        print(f"❌ {e}")
        return 1


def cmd_env_delete(args: argparse.Namespace) -> int:
    """Delete an environment."""
    manager = get_workspace_manager()

    if not args.force:
        confirm = input(f"Are you sure you want to delete '{args.id}'? [y/N]: ")
        if confirm.lower() != 'y':
            print("Cancelled.")
            return 0

    success, message = manager.delete_env(args.id, force=args.force)
    if success:
        print(f"✅ {message}")
        return 0
    else:
        print(f"❌ {message}")
        return 1


def cmd_agent_create(args: argparse.Namespace) -> int:
    """Create a new agent."""
    manager = get_workspace_manager()

    success, path, message = manager.create_agent(
        agent_id=args.id,
        name=args.name,
        description=args.description or "",
        env_name=args.env,
        template=args.template or "uber_agent",
    )

    if success:
        print(f"✅ {message}")
        print(f"   Path: {path}")
        return 0
    else:
        print(f"❌ {message}")
        return 1


def cmd_agent_list(args: argparse.Namespace) -> int:
    """List agents in environment."""
    manager = get_workspace_manager()
    agents = manager.list_agents(args.env)

    if not agents:
        env_name = args.env or manager.active_env or "(none)"
        print(f"No agents found in environment: {env_name}")
        return 0

    print("\n" + "=" * 70)
    print(f"📁 AGENTS in {args.env or manager.active_env}")
    print("=" * 70)
    print(f"{'ID':<25} {'Name':<30} {'Sub-actions'}")
    print("-" * 70)

    for agent in agents:
        agent_id = agent.get("agent_id", "?")[:23]
        name = agent.get("name", "Untitled")[:28]
        sub_count = len(agent.get("sub_actions", []))
        print(f"{agent_id:<25} {name:<30} {sub_count}")

    print("=" * 70)
    return 0


def cmd_agent_show(args: argparse.Namespace) -> int:
    """Show agent details."""
    manager = get_workspace_manager()
    agent = manager.get_agent(args.id, args.env)

    if not agent:
        print(f"❌ Agent not found: {args.id}")
        return 1

    print("\n" + "=" * 60)
    print(f"🤖 AGENT: {agent.get('name', args.id)}")
    print("=" * 60)
    print(f"  ID:            {agent.get('agent_id')}")
    print(f"  Name:          {agent.get('name')}")
    print(f"  Description:   {agent.get('description', '-')}")
    print(f"  Type:          {agent.get('type')}")
    print(f"  Remote ID:     {agent.get('remote_action_id', 'Not linked')}")
    print(f"  Environment:   {agent.get('env_name')}")
    print(f"  Path:          {agent.get('path')}")
    print(f"  Created:       {agent.get('created_at', '-')[:19]}")

    sub_actions = agent.get("sub_actions", [])
    if sub_actions:
        print(f"\n  Sub-Actions ({len(sub_actions)}):")
        for action in sub_actions:
            print(f"    - {action.get('action_id')}: {action.get('title')}")
            print(f"      Remote: {action.get('remote_action_id', 'Not linked')}")

    print("=" * 60)
    return 0


def cmd_agent_add_subaction(args: argparse.Namespace) -> int:
    """Add sub-action to agent."""
    manager = get_workspace_manager()

    success, message = manager.add_subaction(
        agent_name=args.agent,
        action_id=args.action,
        remote_action_id=args.remote_id,
        title=args.title or args.action,
        description=args.description or "",
        env_name=args.env,
    )

    if success:
        print(f"✅ {message}")
        return 0
    else:
        print(f"❌ {message}")
        return 1


def cmd_agent_remove_subaction(args: argparse.Namespace) -> int:
    """Remove sub-action from agent."""
    manager = get_workspace_manager()

    success, message = manager.remove_subaction(
        agent_name=args.agent,
        action_id=args.action,
        env_name=args.env,
    )

    if success:
        print(f"✅ {message}")
        return 0
    else:
        print(f"❌ {message}")
        return 1


def cmd_agent_checkout(args: argparse.Namespace) -> int:
    """Checkout agent from remote with all sub-actions."""
    from cli.wdl_common.api_client import AdoptAPIClient
    from cli.wdl_common.workspace_manager import WORKSPACES_DIR

    manager = get_workspace_manager()
    client = AdoptAPIClient()

    env = args.env or manager.active_env
    if not env:
        print("❌ No environment specified. Use --env or set active environment first.")
        return 1

    if not manager.env_exists(env):
        print(f"❌ Environment not found: {env}")
        return 1

    print(f"\n⏳ Fetching agent from remote: {args.remote_id}")

    # Fetch agent action from remote
    success, action_data, message = client.get_action(args.remote_id)
    if not success:
        print(f"❌ Failed to fetch agent: {message}")
        return 1

    # Parse WDL to find sub-actions
    wdl_str = action_data.get("wdl", "[]")
    try:
        wdl = json.loads(wdl_str) if isinstance(wdl_str, str) else wdl_str
    except json.JSONDecodeError:
        wdl = []

    # Find PROMPT_AND_TOOLS_AGENT operation
    sub_action_ids = []
    for step in wdl:
        if isinstance(step, dict) and step.get("operation") == "PROMPT_AND_TOOLS_AGENT":
            sub_action_ids = step.get("action_ids", [])
            break

    agent_title = action_data.get("title", args.remote_id)
    agent_id = args.id or agent_title.lower().replace(" ", "-")[:50]

    print(f"   Title: {agent_title}")
    print(f"   Found {len(sub_action_ids)} sub-actions")

    # Create agent workspace
    success, agent_path, msg = manager.create_agent(
        agent_id=agent_id,
        name=agent_title,
        description=action_data.get("action_description", ""),
        env_name=env,
    )

    if not success and "already exists" not in msg:
        print(f"❌ {msg}")
        return 1
    elif "already exists" in msg:
        print(f"   ⚠️  Agent already exists locally, updating...")
        agent_path = WORKSPACES_DIR / env / "agents" / agent_id

    # Save agent WDL
    (agent_path / "widdle.json").write_text(json.dumps(wdl, indent=2))

    # Update agent metadata with remote ID
    agent_json = agent_path / "agent.json"
    agent_data = json.loads(agent_json.read_text())
    agent_data["remote_action_id"] = args.remote_id
    agent_json.write_text(json.dumps(agent_data, indent=2))

    # Update metadata.json
    meta_path = agent_path / "metadata.json"
    if meta_path.exists():
        metadata = json.loads(meta_path.read_text())
    else:
        metadata = {}
    metadata["action_id"] = args.remote_id
    metadata["remote_action_id"] = args.remote_id
    meta_path.write_text(json.dumps(metadata, indent=2))

    print(f"   ✅ Agent workspace created: {agent_path}")

    # Checkout sub-actions if requested
    if args.include_subactions and sub_action_ids:
        print(f"\n⏳ Downloading {len(sub_action_ids)} sub-actions...")

        for sub_id in sub_action_ids:
            print(f"   ⏳ Fetching: {sub_id}")
            success, sub_data, msg = client.get_action(sub_id)
            if not success:
                print(f"      ⚠️  Failed: {msg}")
                continue

            sub_title = sub_data.get("title", sub_id)
            sub_action_id = sub_title.lower().replace(" ", "-")[:50] if sub_title else sub_id[:50]

            # Create sub-action workspace under agent
            sub_path = agent_path / "actions" / sub_action_id
            sub_path.mkdir(parents=True, exist_ok=True)
            (sub_path / "test_cases").mkdir(exist_ok=True)
            (sub_path / "traces").mkdir(exist_ok=True)
            (sub_path / "versions").mkdir(exist_ok=True)

            # Save WDL
            sub_wdl_str = sub_data.get("wdl", "[]")
            try:
                sub_wdl = json.loads(sub_wdl_str) if isinstance(sub_wdl_str, str) else sub_wdl_str
            except json.JSONDecodeError:
                sub_wdl = []
            (sub_path / "widdle.json").write_text(json.dumps(sub_wdl, indent=2))

            # Save metadata
            sub_meta = {
                "workflow_id": sub_action_id,
                "title": sub_title,
                "description": sub_data.get("action_description", ""),
                "action_id": sub_id,
                "remote_action_id": sub_id,
                "agent_name": agent_id,
                "env_name": env,
                "created_at": sub_data.get("created_at"),
                "updated_at": sub_data.get("updated_at"),
            }
            (sub_path / "metadata.json").write_text(json.dumps(sub_meta, indent=2))

            # Update agent's sub_actions list
            manager.add_subaction(
                agent_name=agent_id,
                action_id=sub_action_id,
                remote_action_id=sub_id,
                title=sub_title,
                description=sub_data.get("action_description", ""),
                env_name=env,
            )

            print(f"      ✅ {sub_title}")

    print(f"\n✅ Agent checkout complete!")
    print(f"   Path: {agent_path}")
    return 0


def cmd_agent_sync(args: argparse.Namespace) -> int:
    """Sync agent with remote."""
    from cli.wdl_common.api_client import AdoptAPIClient

    manager = get_workspace_manager()
    client = AdoptAPIClient()

    env = args.env or manager.active_env
    agent = manager.get_agent(args.id, env)

    if not agent:
        print(f"❌ Agent not found: {args.id}")
        return 1

    remote_id = agent.get("remote_action_id")
    if not remote_id:
        print(f"❌ Agent not linked to remote. Use agent checkout first.")
        return 1

    print(f"\n⏳ Syncing agent: {args.id}")
    print(f"   Remote ID: {remote_id}")

    # Fetch latest from remote
    success, action_data, message = client.get_action(remote_id)
    if not success:
        print(f"❌ Failed to fetch from remote: {message}")
        return 1

    # Parse WDL for current sub-actions
    wdl_str = action_data.get("wdl", "[]")
    try:
        wdl = json.loads(wdl_str) if isinstance(wdl_str, str) else wdl_str
    except json.JSONDecodeError:
        wdl = []

    remote_sub_ids = set()
    for step in wdl:
        if isinstance(step, dict) and step.get("operation") == "PROMPT_AND_TOOLS_AGENT":
            remote_sub_ids = set(step.get("action_ids", []))
            break

    # Compare with local
    local_sub_ids = set()
    for sub in agent.get("sub_actions", []):
        if sub.get("remote_action_id"):
            local_sub_ids.add(sub["remote_action_id"])

    new_subs = remote_sub_ids - local_sub_ids
    removed_subs = local_sub_ids - remote_sub_ids

    if not new_subs and not removed_subs:
        print("   ✅ Already in sync!")
        return 0

    if new_subs:
        print(f"\n   📥 {len(new_subs)} new sub-actions on remote:")
        for sub_id in new_subs:
            print(f"      - {sub_id}")

    if removed_subs:
        print(f"\n   📤 {len(removed_subs)} sub-actions removed on remote:")
        for sub_id in removed_subs:
            print(f"      - {sub_id}")

    if args.pull:
        # Download new sub-actions
        from cli.wdl_common.workspace_manager import WORKSPACES_DIR
        agent_path = WORKSPACES_DIR / env / "agents" / args.id

        for sub_id in new_subs:
            print(f"\n   ⏳ Downloading: {sub_id}")
            success, sub_data, msg = client.get_action(sub_id)
            if not success:
                print(f"      ⚠️  Failed: {msg}")
                continue

            sub_title = sub_data.get("title", sub_id)
            sub_action_id = sub_title.lower().replace(" ", "-")[:50]

            sub_path = agent_path / "actions" / sub_action_id
            sub_path.mkdir(parents=True, exist_ok=True)
            (sub_path / "test_cases").mkdir(exist_ok=True)
            (sub_path / "traces").mkdir(exist_ok=True)
            (sub_path / "versions").mkdir(exist_ok=True)

            sub_wdl_str = sub_data.get("wdl", "[]")
            try:
                sub_wdl = json.loads(sub_wdl_str) if isinstance(sub_wdl_str, str) else sub_wdl_str
            except json.JSONDecodeError:
                sub_wdl = []
            (sub_path / "widdle.json").write_text(json.dumps(sub_wdl, indent=2))

            sub_meta = {
                "workflow_id": sub_action_id,
                "title": sub_title,
                "action_id": sub_id,
                "remote_action_id": sub_id,
                "agent_name": args.id,
                "env_name": env,
            }
            (sub_path / "metadata.json").write_text(json.dumps(sub_meta, indent=2))

            manager.add_subaction(
                agent_name=args.id,
                action_id=sub_action_id,
                remote_action_id=sub_id,
                title=sub_title,
                env_name=env,
            )
            print(f"      ✅ Downloaded")

        print(f"\n✅ Sync complete!")
    else:
        print(f"\n   Run with --pull to download new sub-actions")

    return 0


def cmd_action_create(args: argparse.Namespace) -> int:
    """Create a new action."""
    manager = get_workspace_manager()

    # Load requirements if file provided
    requirements = ""
    if args.requirements:
        req_path = Path(args.requirements)
        if req_path.exists():
            requirements = req_path.read_text()
        else:
            print(f"❌ Requirements file not found: {args.requirements}")
            return 1

    success, path, message = manager.create_action(
        action_id=args.id,
        title=args.title or args.id,
        description=args.description or "",
        requirements=requirements,
        env_name=args.env,
        agent_name=args.agent,
        template=args.template or "simple",
    )

    if success:
        print(f"✅ {message}")
        print(f"   Path: {path}")
        return 0
    else:
        print(f"❌ {message}")
        return 1


def cmd_action_list(args: argparse.Namespace) -> int:
    """List actions."""
    manager = get_workspace_manager()
    actions = manager.list_actions(
        env_name=args.env,
        agent_name=args.agent,
        include_subactions=not args.standalone_only,
    )

    if not actions:
        print("No actions found.")
        return 0

    print("\n" + "=" * 80)
    scope = args.agent or args.env or manager.active_env or "standalone"
    print(f"📁 ACTIONS in {scope}")
    print("=" * 80)
    print(f"{'ID':<30} {'Title':<25} {'Agent':<15} {'Env'}")
    print("-" * 80)

    for action in actions:
        action_id = action.get("action_id", "?")[:28]
        meta = action.get("metadata", {})
        title = meta.get("title", action_id)[:23]
        agent = action.get("agent_name", "-")[:13] if action.get("agent_name") else "-"
        env = action.get("env_name", "-")[:10] if action.get("env_name") else "-"
        print(f"{action_id:<30} {title:<25} {agent:<15} {env}")

    print("=" * 80)
    return 0


def cmd_profile_show(args: argparse.Namespace) -> int:
    """Show resolved profile."""
    manager = get_workspace_manager()

    if args.action:
        action_info = manager.find_action(args.action)
        if not action_info:
            print(f"❌ Action not found: {args.action}")
            return 1

        profile = manager.resolve_adopt_profile(
            action_path=action_info["path"],
            agent_name=action_info.get("agent_name"),
            env_name=action_info.get("env_name"),
        )
        print(f"\n📋 Resolved Profile for: {args.action}")
    else:
        profile = manager.resolve_adopt_profile(
            agent_name=args.agent,
            env_name=args.env,
        )
        scope = args.agent or args.env or manager.active_env or "root"
        print(f"\n📋 Resolved Profile for: {scope}")

    print("=" * 50)
    print(json.dumps(profile, indent=2))
    return 0


def cmd_profile_update(args: argparse.Namespace) -> int:
    """Update profile at specified level."""
    manager = get_workspace_manager()

    # Determine profile path
    from cli.wdl_common.workspace_manager import WORKSPACES_DIR

    if args.action:
        action_info = manager.find_action(args.action)
        if not action_info:
            print(f"❌ Action not found: {args.action}")
            return 1
        profile_path = action_info["path"] / "adopt_profile.json"
    elif args.agent:
        env = args.env or manager.active_env
        if not env:
            print("❌ No environment specified")
            return 1
        profile_path = WORKSPACES_DIR / env / "agents" / args.agent / "adopt_profile.json"
    elif args.env:
        profile_path = WORKSPACES_DIR / args.env / "adopt_profile.json"
    else:
        profile_path = WORKSPACES_DIR / "adopt_profile.json"

    # Load existing profile
    if profile_path.exists():
        profile = json.loads(profile_path.read_text())
    else:
        profile = {}

    # Update with provided values
    updates = {}
    if args.base_url:
        updates["base_url"] = args.base_url
    if args.app_url:
        updates["application_base_url"] = args.app_url
    if args.cookie:
        updates.setdefault("security_params", {})["cookie"] = args.cookie

    if not updates:
        print("❌ No updates specified. Use --base-url, --app-url, or --cookie")
        return 1

    # Deep merge updates
    def deep_update(base: dict, update: dict) -> None:
        for k, v in update.items():
            if isinstance(v, dict) and k in base and isinstance(base[k], dict):
                deep_update(base[k], v)
            else:
                base[k] = v

    deep_update(profile, updates)

    # Save
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps(profile, indent=2))
    print(f"✅ Profile updated: {profile_path}")
    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Hierarchical workspace management for ABCD",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Command category")

    # =========================================================================
    # ENV commands
    # =========================================================================
    env_parser = subparsers.add_parser("env", help="Environment management")
    env_subparsers = env_parser.add_subparsers(dest="env_command")

    # env create
    env_create = env_subparsers.add_parser("create", help="Create environment")
    env_create.add_argument("--id", required=True, help="Environment ID")
    env_create.add_argument("--name", required=True, help="Environment name")
    env_create.add_argument("--description", help="Description")
    env_create.add_argument("--target", choices=["staging", "production"], default="staging")
    env_create.add_argument("--client", help="Client identifier")
    env_create.add_argument("--use", action="store_true", help="Set as active")
    env_create.set_defaults(func=cmd_env_create)

    # env list
    env_list = env_subparsers.add_parser("list", help="List environments")
    env_list.set_defaults(func=cmd_env_list)

    # env show
    env_show = env_subparsers.add_parser("show", help="Show environment details")
    env_show.add_argument("id", help="Environment ID")
    env_show.set_defaults(func=cmd_env_show)

    # env use
    env_use = env_subparsers.add_parser("use", help="Set active environment")
    env_use.add_argument("id", help="Environment ID")
    env_use.set_defaults(func=cmd_env_use)

    # env delete
    env_delete = env_subparsers.add_parser("delete", help="Delete environment")
    env_delete.add_argument("id", help="Environment ID")
    env_delete.add_argument("--force", action="store_true", help="Force delete")
    env_delete.set_defaults(func=cmd_env_delete)

    # =========================================================================
    # AGENT commands
    # =========================================================================
    agent_parser = subparsers.add_parser("agent", help="Agent management")
    agent_subparsers = agent_parser.add_subparsers(dest="agent_command")

    # agent create
    agent_create = agent_subparsers.add_parser("create", help="Create agent")
    agent_create.add_argument("--id", required=True, help="Agent ID")
    agent_create.add_argument("--name", required=True, help="Agent name")
    agent_create.add_argument("--description", help="Description")
    agent_create.add_argument("--env", help="Environment (uses active if not specified)")
    agent_create.add_argument("--template", choices=["uber_agent"], default="uber_agent")
    agent_create.set_defaults(func=cmd_agent_create)

    # agent list
    agent_list = agent_subparsers.add_parser("list", help="List agents")
    agent_list.add_argument("--env", help="Environment")
    agent_list.set_defaults(func=cmd_agent_list)

    # agent show
    agent_show = agent_subparsers.add_parser("show", help="Show agent details")
    agent_show.add_argument("id", help="Agent ID")
    agent_show.add_argument("--env", help="Environment")
    agent_show.set_defaults(func=cmd_agent_show)

    # agent add-subaction
    agent_add = agent_subparsers.add_parser("add-subaction", help="Add sub-action")
    agent_add.add_argument("--agent", required=True, help="Agent ID")
    agent_add.add_argument("--action", required=True, help="Action ID")
    agent_add.add_argument("--remote-id", required=True, help="Remote action ID")
    agent_add.add_argument("--title", help="Action title")
    agent_add.add_argument("--description", help="Description")
    agent_add.add_argument("--env", help="Environment")
    agent_add.set_defaults(func=cmd_agent_add_subaction)

    # agent remove-subaction
    agent_remove = agent_subparsers.add_parser("remove-subaction", help="Remove sub-action")
    agent_remove.add_argument("--agent", required=True, help="Agent ID")
    agent_remove.add_argument("--action", required=True, help="Action ID")
    agent_remove.add_argument("--env", help="Environment")
    agent_remove.set_defaults(func=cmd_agent_remove_subaction)

    # agent checkout
    agent_checkout = agent_subparsers.add_parser("checkout", help="Checkout agent from remote")
    agent_checkout.add_argument("--remote-id", required=True, help="Remote action ID")
    agent_checkout.add_argument("--env", required=True, help="Environment to checkout into")
    agent_checkout.add_argument("--id", help="Local agent ID (auto-generated if not provided)")
    agent_checkout.add_argument("--include-subactions", action="store_true", help="Download all sub-actions")
    agent_checkout.set_defaults(func=cmd_agent_checkout)

    # agent sync
    agent_sync = agent_subparsers.add_parser("sync", help="Sync agent with remote")
    agent_sync.add_argument("id", help="Agent ID")
    agent_sync.add_argument("--env", help="Environment")
    agent_sync.add_argument("--pull", action="store_true", help="Download new sub-actions")
    agent_sync.set_defaults(func=cmd_agent_sync)

    # =========================================================================
    # ACTION commands
    # =========================================================================
    action_parser = subparsers.add_parser("action", help="Action management")
    action_subparsers = action_parser.add_subparsers(dest="action_command")

    # action create
    action_create = action_subparsers.add_parser("create", help="Create action")
    action_create.add_argument("--id", required=True, help="Action ID")
    action_create.add_argument("--title", "-t", help="Action title")
    action_create.add_argument("--description", "-d", help="Description")
    action_create.add_argument("--requirements", "-r", help="Requirements file")
    action_create.add_argument("--env", help="Environment")
    action_create.add_argument("--agent", help="Agent (creates as sub-action)")
    action_create.add_argument("--template", choices=["simple", "complex"], default="simple")
    action_create.set_defaults(func=cmd_action_create)

    # action list
    action_list = action_subparsers.add_parser("list", help="List actions")
    action_list.add_argument("--env", help="Environment")
    action_list.add_argument("--agent", help="Agent")
    action_list.add_argument("--standalone-only", action="store_true", help="Exclude sub-actions")
    action_list.set_defaults(func=cmd_action_list)

    # =========================================================================
    # PROFILE commands
    # =========================================================================
    profile_parser = subparsers.add_parser("profile", help="Profile management")
    profile_subparsers = profile_parser.add_subparsers(dest="profile_command")

    # profile show
    profile_show = profile_subparsers.add_parser("show", help="Show resolved profile")
    profile_show.add_argument("--action", help="Action ID")
    profile_show.add_argument("--agent", help="Agent ID")
    profile_show.add_argument("--env", help="Environment")
    profile_show.set_defaults(func=cmd_profile_show)

    # profile update
    profile_update = profile_subparsers.add_parser("update", help="Update profile")
    profile_update.add_argument("--action", help="Action ID")
    profile_update.add_argument("--agent", help="Agent ID")
    profile_update.add_argument("--env", help="Environment")
    profile_update.add_argument("--base-url", help="Base URL")
    profile_update.add_argument("--app-url", help="Application base URL")
    profile_update.add_argument("--cookie", help="Session cookie")
    profile_update.set_defaults(func=cmd_profile_update)

    # Parse and execute
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    if hasattr(args, "func"):
        return args.func(args)
    else:
        # Print subcommand help
        if args.command == "env":
            env_parser.print_help()
        elif args.command == "agent":
            agent_parser.print_help()
        elif args.command == "action":
            action_parser.print_help()
        elif args.command == "profile":
            profile_parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())

