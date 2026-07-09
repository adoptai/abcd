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

    playground-profile list   - List remote playground profiles
    playground-profile show   - Show playground profile details
    playground-profile create - Create playground profile
    playground-profile update - Update playground profile
    playground-profile delete - Delete playground profile

    token-config list      - List remote token configurations
    token-config show      - Show token configuration details
    token-config create    - Create token configuration
    token-config update    - Update token configuration
    token-config delete    - Delete token configuration
    token-config publish   - Publish token configuration(s)
    token-config unpublish - Unpublish token configuration(s)

    pipeline create       - Create a new pipeline workspace
    pipeline list         - List pipeline workspaces in active environment
    pipeline show         - Show pipeline workspace details
    pipeline checkout-all - Download all remote pipelines into local workspaces
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Add parent to path for imports
CLI_DIR = Path(__file__).parent
PROJECT_ROOT = CLI_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from cli.wdl_common.api_client import AdoptAPIClient
from cli.wdl_common.workspace_manager import (
    WORKSPACES_DIR,
    get_workspace_manager,
)

_verbose = False


def _vprint(*args: object) -> None:
    if _verbose:
        print("[VERBOSE]", *args)


def cmd_env_create(args: argparse.Namespace) -> int:
    """Create a new environment."""
    dry_run = getattr(args, "dry_run", False)
    if dry_run:
        print("--- DRY RUN MODE ---")
        print(
            f"DRY RUN: Would create environment '{args.id}' (name={args.name}, target={args.target or 'staging'})"
        )
        if args.use:
            print(f"DRY RUN: Would set '{args.id}' as active environment")
        return 0

    manager = get_workspace_manager()
    success, path, message = manager.create_env(
        env_id=args.id,
        name=args.name,
        description=args.description or "",
        target=args.target or "staging",
        client=args.client or "",
        domain=getattr(args, "domain", None) or "",
    )

    if success:
        print(f"✅ {message}")
        print(f"   Path: {path}")
        if args.description:
            print(f"   Description: {args.description}")
        if args.use:
            manager.active_env = args.id
            print("   Set as active environment")
        return 0
    else:
        print(f"❌ {message}")
        return 1


def cmd_env_list(args: argparse.Namespace) -> int:
    """List all environments with descriptions."""
    manager = get_workspace_manager()
    envs = manager.list_envs()

    if not envs:
        print("No environments found. Create one with: workspace.py env create")
        return 0

    print("\n" + "=" * 90)
    print("📁 ENVIRONMENTS")
    print("=" * 90)

    for env in envs:
        env_id = env.get("env_id", "?")
        name = env.get("name", "Untitled")
        target = env.get("target", "?")
        description = env.get("description", "")[:60]
        domain = env.get("domain", "")
        active = " ✓ ACTIVE" if env.get("is_active") else ""

        print(f"\n  {env_id}{active}")
        print(f"    Name: {name} | Target: {target}" + (f" | Domain: {domain}" if domain else ""))
        if description:
            print(
                f"    Description: {description}{'...' if len(env.get('description', '')) > 60 else ''}"
            )

    print("\n" + "=" * 90)
    print("💡 To switch: python cli/workspace.py env use <env-id>")
    print("=" * 90)
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
    dry_run = getattr(args, "dry_run", False)
    if dry_run:
        print("--- DRY RUN MODE ---")
        print(f"DRY RUN: Would delete environment '{args.id}'" + (" (force)" if args.force else ""))
        return 0

    manager = get_workspace_manager()

    if not args.force:
        confirm = input(f"Are you sure you want to delete '{args.id}'? [y/N]: ")
        if confirm.lower() != "y":
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
    dry_run = getattr(args, "dry_run", False)
    if dry_run:
        print("--- DRY RUN MODE ---")
        print(f"DRY RUN: Would create agent '{args.id}' (name={args.name}, env={args.env})")
        return 0

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
    dry_run = getattr(args, "dry_run", False)
    if dry_run:
        print("--- DRY RUN MODE ---")
        print(
            f"DRY RUN: Would add sub-action '{args.action}' to agent '{args.agent}' (remote_id={args.remote_id})"
        )
        return 0

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
    dry_run = getattr(args, "dry_run", False)
    if dry_run:
        print("--- DRY RUN MODE ---")
        print(f"DRY RUN: Would remove sub-action '{args.action}' from agent '{args.agent}'")
        return 0

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


def cmd_agent_move_action(args: argparse.Namespace) -> int:
    """Move a standalone action to become part of an agent."""
    dry_run = getattr(args, "dry_run", False)
    manager = get_workspace_manager()

    env: str | None = args.env or manager.active_env
    if not env:
        print("❌ No environment specified. Use --env or set active environment first.")
        return 1

    if dry_run:
        print("--- DRY RUN MODE ---")
        print(
            f"DRY RUN: Would move standalone action '{args.action}' into agent '{args.agent}' (env={env})"
        )
        if not args.no_wdl_update:
            print("DRY RUN: Would update agent WDL with action reference")
        return 0

    print(f"\n{'=' * 70}")
    print("📦 MOVE ACTION TO AGENT")
    print(f"{'=' * 70}")
    print(f"   Action: {args.action}")
    print(f"   Target Agent: {args.agent}")
    print(f"   Environment: {env}")

    # Check if the action exists
    standalone_path = WORKSPACES_DIR / env / "actions" / args.action

    if not standalone_path.exists():
        print(f"\n❌ Standalone action not found: {args.action}")
        print(f"   Expected path: {standalone_path}")
        return 1

    # Check if agent exists
    if not manager.agent_exists(args.agent, env):
        print(f"\n❌ Agent not found: {args.agent}")
        return 1

    # Perform the move
    success, message = manager.move_action_to_agent(
        action_id=args.action,
        agent_name=args.agent,
        env_name=env,
        update_wdl=not args.no_wdl_update,
    )

    if success:
        print(f"\n✅ {message}")
        agent_path = WORKSPACES_DIR / env / "agents" / args.agent / "actions" / args.action
        print(f"   New location: {agent_path}")

        if not args.no_wdl_update:
            print("   Agent WDL updated with action reference")
        else:
            print("   ⚠️  Agent WDL NOT updated (--no-wdl-update flag)")

        print("\n💡 Next steps:")
        print(
            f"   1. Ensure the action is published: python cli/publish_wdl_action.py --workflow-id {args.action}"
        )
        print(
            f"   2. Enable tool mode: python cli/deployment_rules.py {args.action} --enable-tool-mode"
        )
        print(
            f"   3. Push the updated agent: python cli/save_wdl_draft.py --workflow-id {args.agent}"
        )
        return 0
    else:
        print(f"\n❌ {message}")
        return 1


def cmd_agent_checkout(args: argparse.Namespace) -> int:
    """Checkout agent from remote with all sub-actions."""

    dry_run = getattr(args, "dry_run", False)
    manager = get_workspace_manager()

    env: str | None = args.env or manager.active_env
    _vprint(f"Target environment: {env}")
    if not env:
        print("❌ No environment specified. Use --env or set active environment first.")
        return 1

    if not manager.env_exists(env):
        print(f"❌ Environment not found: {env}")
        return 1

    # Use environment-specific credentials
    from cli.wdl_common.context import get_client

    # Temporarily set active env if different
    original_env = manager.active_env
    if env != manager.active_env:
        manager.active_env = env

    try:
        _vprint("Initialising API client")
        client = get_client()
    finally:
        # Restore original active env if changed
        if original_env != env:
            manager.active_env = original_env

    _vprint(f"API call: get_action({args.remote_id})")
    if dry_run:
        print("--- DRY RUN MODE ---")
        print(f"DRY RUN: Would fetch agent from remote: {args.remote_id}")
        print(f"DRY RUN: Would create agent workspace in env '{env}'")
        if args.include_subactions:
            print("DRY RUN: Would download all sub-actions")
        return 0

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

    agent_title = action_data.get("title") or args.remote_id
    agent_id = args.id or agent_title.lower().replace(" ", "-")[:50]

    print(f"   Title: {agent_title}")
    print(f"   Found {len(sub_action_ids)} sub-actions")
    _vprint(f"Agent local ID: {agent_id}")
    _vprint(f"Sub-action IDs: {sub_action_ids}")

    # Create agent workspace
    if env is None:
        return 1
    _vprint(f"Creating agent workspace: {WORKSPACES_DIR / env / 'agents' / agent_id}")
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
        print("   ⚠️  Agent already exists locally, updating...")
        agent_path = WORKSPACES_DIR / env / "agents" / agent_id

    # Save agent WDL
    (agent_path / "widdle.json").write_text(json.dumps(wdl, indent=2))

    # Update unified metadata.json with remote ID and agent details
    meta_path = agent_path / "metadata.json"
    if meta_path.exists():
        metadata = json.loads(meta_path.read_text())
    else:
        metadata = {
            "created_at": datetime.now().isoformat(),
        }
    metadata["workflow_id"] = agent_id
    metadata["title"] = agent_title
    metadata["description"] = action_data.get("action_description", metadata.get("description", ""))
    metadata["type"] = "agent"
    metadata["env_name"] = env
    metadata["agent_name"] = None
    metadata["action_id"] = args.remote_id
    if not isinstance(metadata.get("sub_actions"), list):
        metadata["sub_actions"] = []
    metadata.setdefault("is_tool_mode", False)
    metadata.setdefault("is_visible_in_list", True)
    metadata["updated_at"] = datetime.now().isoformat()
    _vprint(f"Writing agent metadata to: {meta_path}")
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

            # Save sub-action metadata (unified schema with type indicator)
            sub_meta = {
                "workflow_id": sub_action_id,
                "title": sub_title,
                "description": sub_data.get("action_description", ""),
                "type": "sub_action",
                "env_name": env,
                "agent_name": agent_id,
                "action_id": sub_id,
                "sub_actions": None,
                "is_tool_mode": True,
                "is_visible_in_list": False,
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

    # -------------------------------------------------------------------------
    # Auto-download lambdas referenced by this agent and its sub-actions
    # -------------------------------------------------------------------------
    if env:
        _download_referenced_lambdas(client, agent_path, env, manager)

    print("\n✅ Agent checkout complete!")
    print(f"   Path: {agent_path}")
    return 0


def _collect_lambda_names_from_wdl(wdl: list[Any]) -> set[str]:
    """Return all unique lambda_name values from EXECUTE_LAMBDA / SANDBOX steps."""
    names: set[str] = set()
    for step in wdl:
        if not isinstance(step, dict):
            continue
        op = step.get("operation", "")
        if op in ("EXECUTE_LAMBDA", "SANDBOX"):
            name = step.get("lambda_name")
            if name:
                names.add(name)
    return names


def _download_referenced_lambdas(
    client: AdoptAPIClient,
    agent_path: Path,
    env: str,
    manager: Any,
) -> None:
    """
    Scan all WDL files under agent_path for lambda_name references and
    download any that are not already present locally.

    Lambdas are stored in the shared workspaces/{env}/lambdas/{name}/
    directory (not under the agent), so they can be reused across agents.
    """
    # Collect lambda names from agent WDL + all sub-action WDLs
    lambda_names: set[str] = set()
    for wdl_file in agent_path.rglob("widdle.json"):
        try:
            wdl = json.loads(wdl_file.read_text())
            lambda_names.update(_collect_lambda_names_from_wdl(wdl))
        except Exception:
            pass

    if not lambda_names:
        return

    print(f"\n⏳ Checking {len(lambda_names)} lambda reference(s)...")

    lambdas_base = WORKSPACES_DIR / env / "lambdas"
    downloaded = 0

    for name in sorted(lambda_names):
        lambda_local_path = lambdas_base / name
        if lambda_local_path.exists() and (
            (lambda_local_path / "metadata.json").exists()
            or (lambda_local_path / "lambda.json").exists()
        ):
            _vprint(f"   Lambda '{name}' already exists locally — skipping")
            continue

        print(f"   ⏳ Downloading lambda: {name}")

        # Find lambda on remote by name
        success, lambdas_list, msg = client.list_lambdas(search=name, page_size=100)
        if not success or not lambdas_list:
            print(f"      ⚠️  Could not find lambda '{name}' on remote: {msg}")
            continue

        matches = [lam for lam in lambdas_list if lam.get("name") == name]
        if not matches:
            print(f"      ⚠️  Lambda '{name}' not found on remote")
            continue

        remote = matches[0]
        lambda_id = remote.get("id") or remote.get("lambda_id")
        if not lambda_id:
            print(f"      ⚠️  Lambda '{name}' has no ID on remote")
            continue

        # Fetch full lambda details
        success, lambda_data, msg = client.get_lambda(lambda_id)
        if not success or not lambda_data:
            print(f"      ⚠️  Failed to fetch lambda details for '{name}': {msg}")
            continue

        # Create local workspace directory
        lambda_local_path.mkdir(parents=True, exist_ok=True)
        (lambda_local_path / "test_cases").mkdir(exist_ok=True)

        # Write unified metadata.json
        metadata: dict[str, Any] = {
            "name": name,
            "type": "lambda",
            "lambda_id": lambda_id,
            "language": lambda_data.get("language", "python"),
            "entry_point": lambda_data.get("entry_point", "script.py"),
            "timeout_seconds": lambda_data.get("timeout_seconds", 300),
            "runtime_image": lambda_data.get("runtime_image", "adopt-lambda-runtime:latest"),
            "cpu_limit": lambda_data.get("cpu_limit", "500m"),
            "memory_limit": lambda_data.get("memory_limit", "512Mi"),
            "resource_permissions": lambda_data.get("resource_permissions", []),
        }
        (lambda_local_path / "metadata.json").write_text(json.dumps(metadata, indent=2))

        # Download source files
        success, files_list, msg = client.list_lambda_files(lambda_id)
        if success and files_list:
            for file_info in files_list:
                file_path_str = file_info.get("path") or file_info.get("name", "")
                if not file_path_str:
                    continue
                success_f, content, msg_f = client.get_lambda_file(lambda_id, file_path_str)
                if success_f and content is not None:
                    dest = lambda_local_path / file_path_str
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(content if isinstance(content, str) else str(content))
        else:
            _vprint(f"      No files listed for lambda '{name}': {msg}")

        print(f"      ✅ {name} (id: {lambda_id})")
        downloaded += 1

    if downloaded:
        print(f"\n   Downloaded {downloaded} lambda(s) referenced by this agent")


def cmd_agent_sync(args: argparse.Namespace) -> int:
    """Sync agent with remote."""

    dry_run = getattr(args, "dry_run", False)
    manager = get_workspace_manager()

    env: str | None = args.env or manager.active_env

    # Use environment-specific credentials
    from cli.wdl_common.context import get_client

    # Temporarily set active env if different
    original_env = manager.active_env
    if env and env != manager.active_env:
        manager.active_env = env

    try:
        client = get_client()
    finally:
        # Restore original active env if changed
        if env and original_env != env:
            manager.active_env = original_env
    agent = manager.get_agent(args.id, env)

    if not agent:
        print(f"❌ Agent not found: {args.id}")
        return 1

    remote_id = agent.get("action_id") or agent.get("remote_action_id")
    if not remote_id:
        print("❌ Agent not linked to remote. Use agent checkout first.")
        return 1

    print(f"\n⏳ Syncing agent: {args.id}")
    print(f"   Remote ID: {remote_id}")
    _vprint(f"API call: get_action({remote_id})")

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
        if dry_run:
            print("\n--- DRY RUN MODE ---")
            for sub_id in new_subs:
                print(f"   DRY RUN: Would download sub-action: {sub_id}")
            for sub_id in removed_subs:
                print(f"   DRY RUN: Would note removal of sub-action: {sub_id}")
            print("DRY RUN: No files written.")
            return 0

        # Download new sub-actions
        from cli.wdl_common.workspace_manager import WORKSPACES_DIR

        agent_path = WORKSPACES_DIR / (env or "") / "agents" / (args.id or "")

        for sub_id in new_subs:
            print(f"\n   ⏳ Downloading: {sub_id}")
            success, sub_data, msg = client.get_action(sub_id)
            if not success:
                print(f"      ⚠️  Failed: {msg}")
                continue

            sub_title = sub_data.get("title") or sub_id
            sub_action_id = sub_title.lower().replace(" ", "-")[:50] if sub_title else sub_id[:50]

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
                "description": sub_data.get("action_description", ""),
                "type": "sub_action",
                "env_name": env,
                "agent_name": args.id,
                "action_id": sub_id,
                "sub_actions": None,
                "is_tool_mode": True,
                "is_visible_in_list": False,
                "created_at": sub_data.get("created_at", datetime.now().isoformat()),
                "updated_at": sub_data.get("updated_at", datetime.now().isoformat()),
            }
            (sub_path / "metadata.json").write_text(json.dumps(sub_meta, indent=2))

            manager.add_subaction(
                agent_name=args.id,
                action_id=sub_action_id,
                remote_action_id=sub_id,
                title=sub_title,
                description=sub_data.get("action_description", ""),
                env_name=env,
            )
            print("      ✅ Downloaded")

        print("\n✅ Sync complete!")
    else:
        print("\n   Run with --pull to download new sub-actions")

    return 0


def cmd_action_create(args: argparse.Namespace) -> int:
    """Create a new action."""
    dry_run = getattr(args, "dry_run", False)
    if dry_run:
        print("--- DRY RUN MODE ---")
        loc = f"agent '{args.agent}'" if args.agent else f"env '{args.env}'"
        print(f"DRY RUN: Would create action '{args.id}' (title={args.title or args.id}) in {loc}")
        return 0

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


def cmd_action_checkout_all(args: argparse.Namespace) -> int:
    """
    Checkout all actions from remote to local workspace.

    Downloads actions from the remote API and creates local workspaces for each.
    Uber Agents are automatically detected and placed in the agents/ directory.
    """
    import json
    import shutil

    from dotenv import load_dotenv

    from cli.wdl_common.api_client import get_api_client_for_env

    dry_run = getattr(args, "dry_run", False)
    manager = get_workspace_manager()

    env: str | None = args.env or manager.active_env
    if not env:
        print("❌ No environment specified. Use --env or set active environment first.")
        return 1

    if not manager.env_exists(env):
        print(f"❌ Environment not found: {env}")
        return 1

    # Load environment credentials
    env_path = WORKSPACES_DIR / env
    env_dotenv = env_path / ".env"
    if env_dotenv.exists():
        load_dotenv(env_dotenv, override=True)
    else:
        print(f"⚠️  Warning: No .env file found in environment: {env}")

    print(f"\n{'=' * 80}")
    print("📥 BULK ACTION CHECKOUT")
    print(f"{'=' * 80}")
    print(f"Environment: {env}")

    # Use discovery module for fetching actions (uses correct API)
    from cli.wdl_common.discovery import get_discovery

    discovery = get_discovery()

    # Fetch all actions
    _vprint(
        f"Bulk checkout: env={env}, uber_agents_only={args.uber_agents_only}, include_subactions={args.include_subactions}"
    )
    print("\n⏳ Fetching actions from remote...")
    if args.uber_agents_only:
        success, actions, msg = discovery.fetch_uber_agents(force_refresh=True)
        action_type = "Uber Agents"
    elif args.tools_only:
        success, actions, msg = discovery.fetch_actions(execution_type="TOOL", force_refresh=True)
        action_type = "tools"
    elif args.workflows_only:
        success, actions, msg = discovery.fetch_actions(
            execution_type="WORKFLOW", force_refresh=True
        )
        action_type = "workflows"
    else:
        success, actions, msg = discovery.fetch_actions(execution_type=None, force_refresh=True)
        action_type = "all actions"

    # Get API client for action fetching (after discovery loaded credentials)
    client = get_api_client_for_env()

    if not success:
        print(f"❌ Failed to fetch actions: {msg}")
        return 1

    print(f"   Found {len(actions)} {action_type}")

    # Apply limit
    if args.limit and args.limit < len(actions):
        actions = actions[: args.limit]
        print(f"   Limited to first {args.limit} actions")

    if not actions:
        print("⚠️  No actions to checkout")
        return 0

    # Checkout each action
    success_count = 0
    skip_count = 0
    error_count = 0
    uber_agent_count = 0

    for i, action in enumerate(actions, 1):
        action_id = action.get("id") or action.get("action_id")
        title = action.get("title", action_id)

        print(f"\n[{i}/{len(actions)}] {title}")
        print(f"   ID: {action_id}")

        if not action_id:
            print("   ⚠️  Skipped: No action ID")
            skip_count += 1
            continue

        try:
            # Fetch full action details
            fetch_success, action_data, fetch_msg = client.get_action(action_id)
            if not fetch_success or not action_data:
                print(f"   ⚠️  Failed to fetch: {fetch_msg}")
                error_count += 1
                continue

            # Check if uber agent
            wdl = action_data.get("wdl", [])
            if isinstance(wdl, str):
                import json

                wdl = json.loads(wdl)

            is_uber_agent = False
            sub_action_ids = []
            for step in wdl:
                if isinstance(step, dict) and step.get("operation") == "PROMPT_AND_TOOLS_AGENT":
                    is_uber_agent = True
                    sub_action_ids = step.get("action_ids", [])
                    break

            if is_uber_agent:
                uber_agent_count += 1
                print(f"   🤖 Uber Agent with {len(sub_action_ids)} sub-actions")

                # Create agent workspace
                agent_id = title.lower().replace(" ", "-")[:50] if title else action_id[:50]
                agent_path = WORKSPACES_DIR / env / "agents" / agent_id

                if dry_run:
                    print(f"   DRY RUN: Would create agent workspace '{agent_id}' in env '{env}'")
                    if args.include_subactions and sub_action_ids:
                        for sub_id in sub_action_ids:
                            print(f"   DRY RUN:   Would download sub-action: {sub_id}")
                    success_count += 1
                    continue

                if agent_path.exists() and not args.force:
                    print("   ⏭️  Skipped: Already exists (use --force to overwrite)")
                    skip_count += 1
                    continue

                if agent_path.exists() and args.force:
                    import shutil

                    shutil.rmtree(agent_path)

                # Create agent structure
                agent_success, agent_path, agent_msg = manager.create_agent(
                    agent_id=agent_id,
                    name=title or agent_id,
                    description=action_data.get("action_description", ""),
                    env_name=env,
                )

                if not agent_success and "already exists" not in agent_msg:
                    print(f"   ⚠️  Failed: {agent_msg}")
                    error_count += 1
                    continue

                # Save WDL
                (agent_path / "widdle.json").write_text(json.dumps(wdl, indent=2))

                # Update unified metadata.json with remote action_id
                meta_path = agent_path / "metadata.json"
                if meta_path.exists():
                    agent_meta = json.loads(meta_path.read_text())
                else:
                    agent_meta = {
                        "workflow_id": agent_id,
                        "title": title,
                        "description": action_data.get("action_description", ""),
                        "type": "agent",
                        "env_name": env,
                        "agent_name": None,
                        "sub_actions": [],
                        "is_tool_mode": False,
                        "is_visible_in_list": True,
                        "created_at": datetime.now().isoformat(),
                    }
                agent_meta["action_id"] = action_id
                agent_meta["type"] = "agent"
                agent_meta["updated_at"] = datetime.now().isoformat()
                meta_path.write_text(json.dumps(agent_meta, indent=2))

                # Download sub-actions if requested
                if args.include_subactions and sub_action_ids:
                    print(f"   ⏳ Downloading {len(sub_action_ids)} sub-actions...")
                    for sub_id in sub_action_ids:
                        sub_success, sub_data, sub_msg = client.get_action(sub_id)
                        if not sub_success or not sub_data:
                            print(f"      ⚠️  {sub_id}: {sub_msg}")
                            continue

                        sub_title = sub_data.get("title", sub_id)
                        sub_action_id = (
                            sub_title.lower().replace(" ", "-")[:50] if sub_title else sub_id[:50]
                        )

                        sub_path = agent_path / "actions" / sub_action_id
                        sub_path.mkdir(parents=True, exist_ok=True)
                        (sub_path / "test_cases").mkdir(exist_ok=True)
                        (sub_path / "traces").mkdir(exist_ok=True)
                        (sub_path / "versions").mkdir(exist_ok=True)

                        sub_wdl = sub_data.get("wdl", [])
                        if isinstance(sub_wdl, str):
                            sub_wdl = json.loads(sub_wdl)
                        (sub_path / "widdle.json").write_text(json.dumps(sub_wdl, indent=2))

                        sub_meta = {
                            "workflow_id": sub_action_id,
                            "title": sub_title,
                            "description": sub_data.get("action_description", ""),
                            "type": "sub_action",
                            "env_name": env,
                            "agent_name": agent_id,
                            "action_id": sub_id,
                            "sub_actions": None,
                            "is_tool_mode": True,
                            "is_visible_in_list": False,
                            "created_at": sub_data.get("created_at", datetime.now().isoformat()),
                            "updated_at": sub_data.get("updated_at", datetime.now().isoformat()),
                        }
                        (sub_path / "metadata.json").write_text(json.dumps(sub_meta, indent=2))

                        # Register sub-action in agent's metadata.json sub_actions list
                        manager.add_subaction(
                            agent_name=agent_id,
                            action_id=sub_action_id,
                            remote_action_id=sub_id,
                            title=str(sub_title) if sub_title else sub_action_id,
                            description=sub_data.get("action_description", ""),
                            env_name=env,
                        )

                        print(f"      ✅ {sub_title}")

                print(f"   ✅ Created agent: {agent_path}")
                success_count += 1

            else:
                # Regular action - create in actions/ directory
                action_local_id = title.lower().replace(" ", "-")[:50] if title else action_id[:50]
                action_path = WORKSPACES_DIR / env / "actions" / action_local_id

                if dry_run:
                    print(
                        f"   DRY RUN: Would create action workspace '{action_local_id}' in env '{env}'"
                    )
                    success_count += 1
                    continue

                if action_path.exists() and not args.force:
                    print("   ⏭️  Skipped: Already exists (use --force to overwrite)")
                    skip_count += 1
                    continue

                if action_path.exists() and args.force:
                    import shutil

                    shutil.rmtree(action_path)

                # Create action workspace
                action_success, action_path, action_msg = manager.create_action(
                    action_id=action_local_id,
                    title=title or action_local_id,
                    description=action_data.get("action_description", ""),
                    env_name=env,
                )

                if not action_success and "already exists" not in action_msg:
                    print(f"   ⚠️  Failed: {action_msg}")
                    error_count += 1
                    continue

                # Save WDL
                (action_path / "widdle.json").write_text(json.dumps(wdl, indent=2))

                # Update metadata
                meta_path = action_path / "metadata.json"
                if meta_path.exists():
                    meta = json.loads(meta_path.read_text())
                else:
                    meta = {}
                meta["action_id"] = action_id
                meta["remote_action_id"] = action_id
                meta_path.write_text(json.dumps(meta, indent=2))

                print(f"   ✅ Created action: {action_path}")
                success_count += 1

        except Exception as e:
            print(f"   ❌ Error: {e}")
            error_count += 1
            continue

    # Summary
    print(f"\n{'=' * 80}")
    print("📊 CHECKOUT SUMMARY")
    print(f"{'=' * 80}")
    print(f"   ✅ Success: {success_count}")
    print(f"   ⏭️  Skipped: {skip_count}")
    print(f"   ❌ Errors: {error_count}")
    if uber_agent_count:
        print(f"   🤖 Uber Agents: {uber_agent_count}")
    print(f"{'=' * 80}")

    return 0 if error_count == 0 else 1


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
    dry_run = getattr(args, "dry_run", False)
    manager = get_workspace_manager()

    # Determine profile path

    if args.action:
        action_info = manager.find_action(args.action)
        if not action_info:
            print(f"❌ Action not found: {args.action}")
            return 1
        profile_path = action_info["path"] / "adopt_profile.json"
    elif args.agent:
        env: str | None = args.env or manager.active_env
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

    if dry_run:
        print("--- DRY RUN MODE ---")
        print(f"DRY RUN: Would update profile at: {profile_path}")
        print(f"DRY RUN: Updates: {updates}")
        return 0

    # Save
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps(profile, indent=2))
    print(f"✅ Profile updated: {profile_path}")
    return 0


# =========================================================================
# Playground Profile (remote) command handlers
# =========================================================================


def _get_client() -> AdoptAPIClient:
    """Get API client for active environment."""
    from cli.wdl_common.api_client import get_api_client_for_env

    return get_api_client_for_env(verbose=_verbose)


def _validate_security_headers_are_token_refs(
    security_headers: dict[str, Any],
) -> int | None:
    """Validate that all security header values reference published token configs.

    Security headers in remote playground profiles must reference token configs
    by name (not hardcoded secrets). This prevents accidental secret leakage and
    ensures tokens are managed via the token manager for automatic refresh.

    Returns:
        None if validation passes, or an error exit code (1) if it fails.
    """
    client = _get_client()
    success, data, message = client.list_token_configs(page=1, page_size=100, is_published=True)
    if not success or data is None:
        print(f"⚠️  Could not fetch token configs to validate: {message}")
        print("   Skipping security header validation.")
        return None

    items = data.get("items", []) if isinstance(data, dict) else []
    published_names = {t["name"] for t in items if t.get("name")}

    invalid = []
    for key, value in security_headers.items():
        if not isinstance(value, str):
            invalid.append((key, str(value), "value must be a string"))
        elif value not in published_names:
            invalid.append((key, value, "not a published token config"))

    if invalid:
        print("❌ Security header values must reference published token configs.")
        print("   Hardcoded secrets are not allowed in remote playground profiles.")
        print()
        for key, value, reason in invalid:
            preview = value[:40] + "..." if len(value) > 40 else value
            print(f'   • {key} = "{preview}" → {reason}')
        print()
        print(f"   Published token configs: {', '.join(sorted(published_names)) or '(none)'}")
        print()
        print("   To fix: create a token config first, then reference it by name:")
        print('     python cli/workspace.py token-config create --name "my_token" \\')
        print('       --domain-suffix "example.com" --storage-type customScript \\')
        print('       --custom-script "return document.cookie;"')
        return 1

    return None


def cmd_pg_profile_list(args: argparse.Namespace) -> int:
    """List remote playground profiles."""
    client = _get_client()
    integration_id = getattr(args, "integration_id", None)

    success, profiles, message = client.list_playground_profiles(
        integration_id=integration_id,
    )

    if not success:
        print(f"❌ {message}")
        return 1

    if not profiles:
        print("No playground profiles found.")
        return 0

    print("\n" + "=" * 100)
    print("🎮 PLAYGROUND PROFILES")
    print("=" * 100)
    print(f"{'ID':<38} {'Name':<22} {'API Base URL':<28} {'Default':<9} {'Available'}")
    print("-" * 100)

    for p in profiles:
        pid = (p.get("id") or "?")[:36]
        name = (p.get("profile_name") or "?")[:20]
        api_url = (p.get("api_base_url") or "-")[:26]
        default = "✓" if p.get("is_default") else ""
        available = "✓" if p.get("is_available") else "✗"
        print(f"{pid:<38} {name:<22} {api_url:<28} {default:<9} {available}")

    print("=" * 100)
    return 0


def cmd_pg_profile_show(args: argparse.Namespace) -> int:
    """Show a playground profile."""
    client = _get_client()

    if getattr(args, "default", False):
        success, profile, message = client.get_default_playground_profile()
    else:
        if not args.id:
            print("❌ Provide a profile ID or use --default")
            return 1
        success, profile, message = client.get_playground_profile(args.id)

    if not success or profile is None:
        print(f"❌ {message}")
        return 1

    if getattr(args, "json", False):
        print(json.dumps(profile, indent=2, default=str))
        return 0

    print("\n" + "=" * 60)
    print(f"🎮 PLAYGROUND PROFILE: {profile.get('profile_name', '?')}")
    print("=" * 60)
    print(f"  ID:              {profile.get('id')}")
    print(f"  Name:            {profile.get('profile_name')}")
    print(f"  App Base URL:    {profile.get('app_base_url', '-')}")
    print(f"  API Base URL:    {profile.get('api_base_url', '-')}")
    print(f"  User Email:      {profile.get('user_email', '-')}")
    print(f"  User Org ID:     {profile.get('user_org_id', '-')}")
    print(f"  Application:     {profile.get('application', '-')}")
    print(f"  Integration ID:  {profile.get('integration_id', '-')}")
    print(f"  Is Default:      {'Yes' if profile.get('is_default') else 'No'}")
    print(f"  Is Available:    {'Yes' if profile.get('is_available') else 'No'}")
    print(f"  Created:         {str(profile.get('created_at', '-'))[:19]}")
    print(f"  Updated:         {str(profile.get('updated_at', '-'))[:19]}")

    headers = profile.get("security_headers")
    if headers:
        print("\n  Security Headers:")
        for k, v in headers.items():
            print(f"    {k}: {v}")

    props = profile.get("user_properties")
    if props:
        print("\n  User Properties:")
        for k, v in props.items():
            print(f"    {k}: {v}")

    print("=" * 60)
    return 0


def cmd_pg_profile_create(args: argparse.Namespace) -> int:
    """Create a playground profile."""
    dry_run = getattr(args, "dry_run", False)
    payload: dict[str, Any] = {}

    # Load from file if provided
    from_file = getattr(args, "from_file", None)
    if from_file:
        file_path = Path(from_file)
        if not file_path.exists():
            print(f"❌ File not found: {from_file}")
            return 1
        payload = json.loads(file_path.read_text())
        _vprint(f"Loaded base payload from: {from_file}")

    # Load from local adopt_profile.json if requested
    from_profile = getattr(args, "from_profile", False)
    if from_profile:
        manager = get_workspace_manager()
        env = manager.active_env
        if not env:
            print("❌ No active environment. Set one with: workspace.py env use <env-id>")
            return 1
        local_profile = manager.resolve_adopt_profile(env_name=env)
        _vprint(f"Resolved local profile for env '{env}': {json.dumps(local_profile, indent=2)}")

        if local_profile.get("base_url") and "api_base_url" not in payload:
            payload["api_base_url"] = local_profile["base_url"]
        if local_profile.get("application_base_url") and "app_base_url" not in payload:
            payload["app_base_url"] = local_profile["application_base_url"]
        if local_profile.get("security_params") and "security_headers" not in payload:
            payload["security_headers"] = local_profile["security_params"]
        if local_profile.get("workflow_params") and "user_properties" not in payload:
            payload["user_properties"] = {
                k: str(v) for k, v in local_profile["workflow_params"].items()
            }

    # CLI flags override everything
    if args.name:
        payload["profile_name"] = args.name
    if getattr(args, "app_base_url", None):
        payload["app_base_url"] = args.app_base_url
    if getattr(args, "api_base_url", None):
        payload["api_base_url"] = args.api_base_url
    if getattr(args, "user_email", None):
        payload["user_email"] = args.user_email
    if getattr(args, "user_org_id", None):
        payload["user_org_id"] = args.user_org_id
    if getattr(args, "integration_id", None):
        payload["integration_id"] = args.integration_id
    if getattr(args, "is_default", False):
        payload["is_default"] = True
    if getattr(args, "application", None):
        payload["application"] = args.application
    if getattr(args, "documented_api_id", None):
        payload["documented_api_id"] = args.documented_api_id

    # JSON string fields
    sec_headers = getattr(args, "security_headers", None)
    if sec_headers:
        try:
            payload["security_headers"] = json.loads(sec_headers)
        except json.JSONDecodeError:
            print("❌ --security-headers must be a valid JSON string")
            return 1

    user_props = getattr(args, "user_properties", None)
    if user_props:
        try:
            payload["user_properties"] = json.loads(user_props)
        except json.JSONDecodeError:
            print("❌ --user-properties must be a valid JSON string")
            return 1

    # Validate required fields
    missing = []
    if "profile_name" not in payload:
        missing.append("--name")
    if "app_base_url" not in payload:
        missing.append("--app-base-url")
    if "api_base_url" not in payload:
        missing.append("--api-base-url")
    if missing:
        print(f"❌ Missing required fields: {', '.join(missing)}")
        print("   Use --from-profile or --from-file to populate, or provide flags directly")
        return 1

    # Validate security header values reference published token configs
    if payload.get("security_headers"):
        validation_err = _validate_security_headers_are_token_refs(payload["security_headers"])
        if validation_err:
            return validation_err

    if dry_run:
        print("--- DRY RUN MODE ---")
        print("DRY RUN: Would create playground profile:")
        print(json.dumps(payload, indent=2, default=str))
        return 0

    client = _get_client()
    success, profile, message = client.create_playground_profile(payload)

    if not success or profile is None:
        print(f"❌ {message}")
        return 1

    print(f"✅ {message}")
    print(f"   ID: {profile.get('id')}")
    print(f"   Name: {profile.get('profile_name')}")
    return 0


def cmd_pg_profile_update(args: argparse.Namespace) -> int:
    """Update a playground profile."""
    dry_run = getattr(args, "dry_run", False)
    payload: dict[str, Any] = {}

    if getattr(args, "name", None):
        payload["profile_name"] = args.name
    if getattr(args, "app_base_url", None):
        payload["app_base_url"] = args.app_base_url
    if getattr(args, "api_base_url", None):
        payload["api_base_url"] = args.api_base_url
    if getattr(args, "user_email", None):
        payload["user_email"] = args.user_email
    if getattr(args, "user_org_id", None):
        payload["user_org_id"] = args.user_org_id
    if getattr(args, "integration_id", None):
        payload["integration_id"] = args.integration_id
    if getattr(args, "application", None):
        payload["application"] = args.application
    if getattr(args, "documented_api_id", None):
        payload["documented_api_id"] = args.documented_api_id

    # Boolean flags
    if getattr(args, "is_default", None) is not None:
        payload["is_default"] = args.is_default
    if getattr(args, "is_available", None) is not None:
        payload["is_available"] = args.is_available

    sec_headers = getattr(args, "security_headers", None)
    if sec_headers:
        try:
            payload["security_headers"] = json.loads(sec_headers)
        except json.JSONDecodeError:
            print("❌ --security-headers must be a valid JSON string")
            return 1

    user_props = getattr(args, "user_properties", None)
    if user_props:
        try:
            payload["user_properties"] = json.loads(user_props)
        except json.JSONDecodeError:
            print("❌ --user-properties must be a valid JSON string")
            return 1

    if not payload:
        print("❌ No updates specified. Provide at least one flag to update.")
        return 1

    # Validate security header values reference published token configs
    if payload.get("security_headers"):
        validation_err = _validate_security_headers_are_token_refs(payload["security_headers"])
        if validation_err:
            return validation_err

    if dry_run:
        print("--- DRY RUN MODE ---")
        print(f"DRY RUN: Would update profile '{args.id}' with:")
        print(json.dumps(payload, indent=2, default=str))
        return 0

    client = _get_client()
    success, profile, message = client.update_playground_profile(args.id, payload)

    if not success:
        print(f"❌ {message}")
        return 1

    print(f"✅ {message}")
    return 0


def cmd_pg_profile_delete(args: argparse.Namespace) -> int:
    """Delete a playground profile."""
    dry_run = getattr(args, "dry_run", False)
    if dry_run:
        print("--- DRY RUN MODE ---")
        print(f"DRY RUN: Would delete playground profile '{args.id}'")
        return 0

    if not getattr(args, "force", False):
        confirm = input(f"Are you sure you want to delete profile '{args.id}'? [y/N]: ")
        if confirm.lower() != "y":
            print("Cancelled.")
            return 0

    client = _get_client()
    success, message = client.delete_playground_profile(args.id)

    if not success:
        print(f"❌ {message}")
        return 1

    print(f"✅ {message}")
    return 0


# =========================================================================
# Token Config (remote) command handlers
# =========================================================================

# Storage type validation map
_STORAGE_TYPE_REQUIRED = {
    "localStorage": ["storage_key"],
    "sessionStorage": ["storage_key"],
    "cookie": ["cookie_key"],
    "domElement": ["dom_selector", "dom_attribute"],
    "customScript": ["custom_script"],
}


def cmd_token_config_list(args: argparse.Namespace) -> int:
    """List remote token configurations."""
    client = _get_client()

    is_published = None
    if getattr(args, "published", False):
        is_published = True
    elif getattr(args, "unpublished", False):
        is_published = False

    success, data, message = client.list_token_configs(
        page=getattr(args, "page", 1) or 1,
        page_size=getattr(args, "page_size", 50) or 50,
        search=getattr(args, "search", None),
        is_published=is_published,
        integration_id=getattr(args, "integration_id", None),
    )

    if not success:
        print(f"❌ {message}")
        return 1

    items = data.get("items", []) if isinstance(data, dict) else []
    total = data.get("total", len(items)) if isinstance(data, dict) else len(items)

    if not items:
        print("No token configurations found.")
        return 0

    page = getattr(args, "page", 1) or 1
    page_size = getattr(args, "page_size", 50) or 50
    start = (page - 1) * page_size + 1
    end = start + len(items) - 1

    print("\n" + "=" * 110)
    print(f"🔑 TOKEN CONFIGURATIONS (showing {start}-{end} of {total})")
    print("=" * 110)
    print(f"{'ID':<38} {'Name':<22} {'Domain':<22} {'Storage Type':<16} {'Published'}")
    print("-" * 110)

    for t in items:
        tid = (t.get("id") or "?")[:36]
        name = (t.get("name") or "?")[:20]
        domain = (t.get("domain_suffix") or "-")[:20]
        stype = (t.get("storage_type") or "-")[:14]
        published = "✓" if t.get("is_published") else "✗"
        print(f"{tid:<38} {name:<22} {domain:<22} {stype:<16} {published}")

    print("=" * 110)
    return 0


def cmd_token_config_show(args: argparse.Namespace) -> int:
    """Show a token configuration."""
    client = _get_client()
    success, token, message = client.get_token_config(args.id)

    if not success or token is None:
        print(f"❌ {message}")
        return 1

    if getattr(args, "json", False):
        print(json.dumps(token, indent=2, default=str))
        return 0

    print("\n" + "=" * 60)
    print(f"🔑 TOKEN CONFIG: {token.get('name', '?')}")
    print("=" * 60)
    print(f"  ID:              {token.get('id')}")
    print(f"  Name:            {token.get('name')}")
    print(f"  Domain Suffix:   {token.get('domain_suffix')}")
    print(f"  Storage Type:    {token.get('storage_type')}")
    print(f"  Published:       {'Yes' if token.get('is_published') else 'No'}")
    print(f"  Integration ID:  {token.get('integration_id', '-')}")
    print(f"  Created:         {str(token.get('created_at', '-'))[:19]}")
    print(f"  Updated:         {str(token.get('updated_at', '-'))[:19]}")

    # Storage-type specific fields
    stype = token.get("storage_type", "")
    if stype in ("localStorage", "sessionStorage"):
        print(f"\n  Storage Key:     {token.get('storage_key', '-')}")
    elif stype == "cookie":
        print(f"\n  Cookie Key:      {token.get('cookie_key', '-')}")
        print(f"  Cookie Domains:  {', '.join(token.get('cookie_domains') or []) or '-'}")
        print(f"  Use All Cookies: {'Yes' if token.get('use_all_cookies') else 'No'}")
    elif stype == "domElement":
        print(f"\n  DOM Selector:    {token.get('dom_selector', '-')}")
        print(f"  DOM Attribute:   {token.get('dom_attribute', '-')}")
        print(f"  Use Content:     {'Yes' if token.get('use_content') else 'No'}")
    elif stype == "customScript":
        script = token.get("custom_script", "-")
        if script and len(script) > 80:
            script = script[:77] + "..."
        print(f"\n  Custom Script:   {script}")

    if token.get("parser_logic"):
        logic = token["parser_logic"]
        if len(logic) > 80:
            logic = logic[:77] + "..."
        print(f"  Parser Logic:    {logic}")

    print("=" * 60)
    return 0


def cmd_token_config_create(args: argparse.Namespace) -> int:
    """Create a token configuration."""
    dry_run = getattr(args, "dry_run", False)
    payload: dict[str, Any] = {}

    # Load from file if provided
    from_file = getattr(args, "from_file", None)
    if from_file:
        file_path = Path(from_file)
        if not file_path.exists():
            print(f"❌ File not found: {from_file}")
            return 1
        payload = json.loads(file_path.read_text())
        _vprint(f"Loaded base payload from: {from_file}")

    # CLI flags override
    if getattr(args, "name", None):
        payload["name"] = args.name
    if getattr(args, "domain_suffix", None):
        payload["domain_suffix"] = args.domain_suffix
    if getattr(args, "storage_type", None):
        payload["storage_type"] = args.storage_type
    if getattr(args, "storage_key", None):
        payload["storage_key"] = args.storage_key
    if getattr(args, "cookie_key", None):
        payload["cookie_key"] = args.cookie_key
    if getattr(args, "cookie_domains", None):
        payload["cookie_domains"] = [d.strip() for d in args.cookie_domains.split(",")]
    if getattr(args, "use_all_cookies", False):
        payload["use_all_cookies"] = True
        payload["cookie_key"] = "ALL_COOKIES"
    if getattr(args, "dom_selector", None):
        payload["dom_selector"] = args.dom_selector
    if getattr(args, "dom_attribute", None):
        payload["dom_attribute"] = args.dom_attribute
    if getattr(args, "use_content", False):
        payload["use_content"] = True
    if getattr(args, "integration_id", None):
        payload["integration_id"] = args.integration_id

    # Custom script: support @filepath syntax
    custom_script = getattr(args, "custom_script", None)
    if custom_script:
        if custom_script.startswith("@"):
            script_path = Path(custom_script[1:])
            if not script_path.exists():
                print(f"❌ Script file not found: {script_path}")
                return 1
            payload["custom_script"] = script_path.read_text()
        else:
            payload["custom_script"] = custom_script

    # Parser logic: support @filepath syntax
    parser_logic = getattr(args, "parser_logic", None)
    if parser_logic:
        if parser_logic.startswith("@"):
            logic_path = Path(parser_logic[1:])
            if not logic_path.exists():
                print(f"❌ Parser logic file not found: {logic_path}")
                return 1
            payload["parser_logic"] = logic_path.read_text()
        else:
            payload["parser_logic"] = parser_logic

    # Publish status
    if getattr(args, "no_publish", False):
        payload["is_published"] = False

    # Validate required fields
    missing = []
    if "name" not in payload:
        missing.append("--name")
    if "domain_suffix" not in payload:
        missing.append("--domain-suffix")
    if "storage_type" not in payload:
        missing.append("--storage-type")
    if missing:
        print(f"❌ Missing required fields: {', '.join(missing)}")
        return 1

    # Validate storage-type specific fields
    stype = payload.get("storage_type", "")
    required_for_type = _STORAGE_TYPE_REQUIRED.get(stype, [])
    type_missing = [f for f in required_for_type if not payload.get(f)]
    if type_missing:
        flag_names = [f"--{f.replace('_', '-')}" for f in type_missing]
        print(f"❌ Storage type '{stype}' requires: {', '.join(flag_names)}")
        return 1

    if dry_run:
        print("--- DRY RUN MODE ---")
        print("DRY RUN: Would create token config:")
        print(json.dumps(payload, indent=2, default=str))
        return 0

    client = _get_client()
    success, token, message = client.create_token_config(payload)

    if not success or token is None:
        print(f"❌ {message}")
        return 1

    print(f"✅ {message}")
    print(f"   ID: {token.get('id')}")
    print(f"   Name: {token.get('name')}")
    return 0


def cmd_token_config_update(args: argparse.Namespace) -> int:
    """Update a token configuration."""
    dry_run = getattr(args, "dry_run", False)
    payload: dict[str, Any] = {}

    if getattr(args, "name", None):
        payload["name"] = args.name
    if getattr(args, "domain_suffix", None):
        payload["domain_suffix"] = args.domain_suffix
    if getattr(args, "storage_type", None):
        payload["storage_type"] = args.storage_type
    if getattr(args, "storage_key", None):
        payload["storage_key"] = args.storage_key
    if getattr(args, "cookie_key", None):
        payload["cookie_key"] = args.cookie_key
    if getattr(args, "cookie_domains", None):
        payload["cookie_domains"] = [d.strip() for d in args.cookie_domains.split(",")]
    if getattr(args, "dom_selector", None):
        payload["dom_selector"] = args.dom_selector
    if getattr(args, "dom_attribute", None):
        payload["dom_attribute"] = args.dom_attribute
    if getattr(args, "integration_id", None):
        payload["integration_id"] = args.integration_id

    custom_script = getattr(args, "custom_script", None)
    if custom_script:
        if custom_script.startswith("@"):
            script_path = Path(custom_script[1:])
            if not script_path.exists():
                print(f"❌ Script file not found: {script_path}")
                return 1
            payload["custom_script"] = script_path.read_text()
        else:
            payload["custom_script"] = custom_script

    parser_logic = getattr(args, "parser_logic", None)
    if parser_logic:
        if parser_logic.startswith("@"):
            logic_path = Path(parser_logic[1:])
            if not logic_path.exists():
                print(f"❌ Parser logic file not found: {logic_path}")
                return 1
            payload["parser_logic"] = logic_path.read_text()
        else:
            payload["parser_logic"] = parser_logic

    # Boolean toggles
    if getattr(args, "publish", False):
        payload["is_published"] = True
    elif getattr(args, "unpublish", False):
        payload["is_published"] = False

    if not payload:
        print("❌ No updates specified. Provide at least one flag to update.")
        return 1

    if dry_run:
        print("--- DRY RUN MODE ---")
        print(f"DRY RUN: Would update token config '{args.id}' with:")
        print(json.dumps(payload, indent=2, default=str))
        return 0

    client = _get_client()
    success, token, message = client.update_token_config(args.id, payload)

    if not success:
        print(f"❌ {message}")
        return 1

    print(f"✅ {message}")
    return 0


def cmd_token_config_delete(args: argparse.Namespace) -> int:
    """Delete a token configuration."""
    dry_run = getattr(args, "dry_run", False)
    if dry_run:
        print("--- DRY RUN MODE ---")
        print(f"DRY RUN: Would delete token config '{args.id}'")
        return 0

    if not getattr(args, "force", False):
        confirm = input(f"Are you sure you want to delete token config '{args.id}'? [y/N]: ")
        if confirm.lower() != "y":
            print("Cancelled.")
            return 0

    client = _get_client()
    success, message = client.delete_token_config(args.id)

    if not success:
        print(f"❌ {message}")
        return 1

    print(f"✅ {message}")
    return 0


def cmd_token_config_publish(args: argparse.Namespace) -> int:
    """Publish token configurations."""
    dry_run = getattr(args, "dry_run", False)
    if dry_run:
        print("--- DRY RUN MODE ---")
        print(f"DRY RUN: Would publish {len(args.ids)} token config(s)")
        return 0

    client = _get_client()
    success, message = client.batch_token_config_status(args.ids, is_published=True)

    if not success:
        print(f"❌ {message}")
        return 1

    print(f"✅ {message}")
    return 0


def cmd_token_config_unpublish(args: argparse.Namespace) -> int:
    """Unpublish token configurations."""
    dry_run = getattr(args, "dry_run", False)
    if dry_run:
        print("--- DRY RUN MODE ---")
        print(f"DRY RUN: Would unpublish {len(args.ids)} token config(s)")
        return 0

    client = _get_client()
    success, message = client.batch_token_config_status(args.ids, is_published=False)

    if not success:
        print(f"❌ {message}")
        return 1

    print(f"✅ {message}")
    return 0


# =============================================================================
# Pipeline commands
# =============================================================================


def cmd_pipeline_create(args: argparse.Namespace) -> int:
    """Create a new pipeline workspace."""
    import re

    from cli.wdl_common.context import ensure_env
    from cli.wdl_common.workspace_manager import get_workspace_manager as _gwm

    def _slugify(text: str) -> str:
        s = text.lower().strip()
        s = re.sub(r"[^\w\s-]", "", s)
        s = re.sub(r"[\s_]+", "-", s)
        s = re.sub(r"-+", "-", s)
        return s.strip("-")[:64]

    env_name = ensure_env()
    manager = _gwm()

    pipeline_id = getattr(args, "id", None) or _slugify(args.title)

    if getattr(args, "source_type", "internal") == "connector":
        source_connector_id = getattr(args, "source_connector_id", None)
        if not source_connector_id:
            print("❌ --source-connector-id is required when --source-type=connector")
            return 1
        source = {
            "integration_id": source_connector_id,
            "integration_type": getattr(args, "source_connector_type", "connector") or "connector",
            "integration_name": getattr(args, "source_connector_name", None) or source_connector_id,
        }
    elif getattr(args, "source_type", "internal") == "salesforce":
        source = {
            "integration_id": getattr(args, "source_connector_id", None) or "salesforce",
            "integration_type": "salesforce",
            "integration_name": getattr(args, "source_connector_name", None) or "Salesforce",
        }
    else:
        source = {
            "integration_id": "internal_data_store",
            "integration_type": "internal",
            "integration_name": "Internal Data Store",
        }

    success, path, message = manager.create_pipeline_workspace(
        pipeline_id=pipeline_id,
        name=args.title,
        description=getattr(args, "description", "") or "",
        prompt=getattr(args, "prompt", "") or args.title,
        source=source,
        env_name=env_name,
    )

    if not success:
        print(f"❌ {message}")
        return 1

    print(f"✅ {message}")
    print(f"   Environment : {env_name}")
    print(f"   Path        : {path}")
    print("\n📝 Next steps:")
    print(f"   1. Edit widdle.json:  {path / 'widdle.json'}")
    print(f"   2. Push draft:        python cli/save_pipeline_draft.py {pipeline_id}")
    print(f"   3. Run test:          python cli/test_pipeline.py {pipeline_id}")
    print(f"   4. Publish:           python cli/publish_pipeline.py {pipeline_id} --yes")
    return 0


def cmd_pipeline_list(args: argparse.Namespace) -> int:
    """List pipeline workspaces."""
    from cli.wdl_common.context import ensure_env
    from cli.wdl_common.workspace_manager import get_workspace_manager as _gwm

    env_name = ensure_env()
    manager = _gwm()
    pipelines = manager.list_pipeline_workspaces(env_name)

    state_filter = getattr(args, "state", None)
    if state_filter:
        pipelines = [p for p in pipelines if p.get("state") == state_filter]

    if getattr(args, "json", False):
        out = [{k: str(v) if k == "path" else v for k, v in p.items()} for p in pipelines]
        print(json.dumps(out, indent=2, default=str))
        return 0

    if not pipelines:
        print(f"No pipeline workspaces found in environment '{env_name}'")
        print("Create one: python cli/workspace.py pipeline create --title 'My Pipeline'")
        return 0

    state_icons = {
        "local": "🏠",
        "draft": "📝",
        "published": "✅",
        "running": "🟢",
        "paused": "⏸",
        "error": "🔴",
    }
    print(f"\n📦 Pipelines in: {env_name}  ({len(pipelines)})\n")
    print(f"  {'ID':<30}  {'STATE':<12}  NAME")
    print(f"  {'-' * 30}  {'-' * 12}  {'-' * 30}")
    for p in pipelines:
        pid = p.get("pipeline_id", "?")[:30]
        state = p.get("state") or "local"
        icon = state_icons.get(state, "❓")
        name = p.get("name", pid)[:50]
        print(f"  {pid:<30}  {icon} {state:<10}  {name}")
    print()
    return 0


def cmd_pipeline_show(args: argparse.Namespace) -> int:
    """Show details of a pipeline workspace."""
    from cli.wdl_common.context import ensure_env
    from cli.wdl_common.workspace_manager import get_workspace_manager as _gwm

    env_name = ensure_env()
    manager = _gwm()
    meta = manager.get_pipeline_workspace(args.pipeline_id, env_name)

    if not meta:
        print(f"❌ Pipeline workspace not found: {args.pipeline_id}")
        return 1

    if getattr(args, "json", False):
        out = {k: str(v) if k == "path" else v for k, v in meta.items()}
        print(json.dumps(out, indent=2, default=str))
        return 0

    path = meta.get("path")
    wdl_steps = 0
    if path:
        wdl_path = Path(path) / "widdle.json"
        if wdl_path.exists():
            try:
                wdl_steps = len(json.loads(wdl_path.read_text()))
            except Exception:
                pass

    print(f"\n📋 Pipeline: {meta['pipeline_id']}")
    print(f"   Name          : {meta['name']}")
    print(f"   Description   : {meta.get('description', '')}")
    print(f"   State         : {meta.get('state', 'local')}")
    print(f"   Remote ID     : {meta.get('remote_pipeline_id') or '(not yet pushed)'}")
    print(f"   Version ID    : {meta.get('version_id') or '(none)'}")
    print(f"   Source        : {meta.get('source', {}).get('integration_name', '?')}")
    print(f"   Schedule      : {meta.get('schedule_type', 'manual')}")
    print(f"   WDL steps     : {wdl_steps}")
    print(f"   Path          : {path}")
    print()
    return 0


def cmd_pipeline_checkout_all(args: argparse.Namespace) -> int:
    """
    Download all remote pipelines into local workspaces.

    For each remote pipeline:
      1. Creates workspaces/{env}/pipelines/{slug}/pipeline.json
      2. Writes the pipeline's WDL to widdle.json
      3. Saves a versions/v1_widdle.json snapshot
      4. Stores remote_pipeline_id and version_id so push/publish work immediately
    """
    import re
    import shutil

    from dotenv import load_dotenv

    from cli.wdl_common.workspace_manager import WORKSPACES_DIR
    from cli.wdl_common.workspace_manager import get_workspace_manager as _gwm

    def _slugify(text: str) -> str:
        s = text.lower().strip()
        s = re.sub(r"[^\w\s-]", "", s)
        s = re.sub(r"[\s_]+", "-", s)
        s = re.sub(r"-+", "-", s)
        return s.strip("-")[:60]

    dry_run = getattr(args, "dry_run", False)
    manager = _gwm()

    env = getattr(args, "env", None) or manager.active_env
    if not env:
        print("❌ No environment specified. Use --env or set active environment first.")
        return 1

    if not manager.env_exists(env):
        print(f"❌ Environment not found: {env}")
        return 1

    env_path = WORKSPACES_DIR / env
    env_dotenv = env_path / ".env"
    if env_dotenv.exists():
        load_dotenv(env_dotenv, override=True)
    else:
        print(f"⚠️  Warning: No .env file found in environment: {env}")

    print(f"\n{'=' * 70}")
    print("📥 BULK PIPELINE CHECKOUT")
    print(f"{'=' * 70}")
    print(f"Environment : {env}")

    import os

    from cli.auth import get_bearer_token
    from cli.wdl_common.pipeline_client import PipelineClient

    try:
        token = get_bearer_token()
    except Exception as exc:
        print(f"❌ Could not get bearer token: {exc}")
        return 1

    base_url = os.getenv("ADOPT_ACTIONS_ENDPOINT", "https://api.adopt.ai").rstrip("/")
    client = PipelineClient(base_url=base_url, token=token)

    # Fetch all remote pipelines (paginated)
    print("\n⏳ Fetching pipelines from remote...")
    try:
        all_pipelines: list[dict] = []
        page = 1
        while True:
            resp = client.list_pipelines(
                state=getattr(args, "state", None) or None,
                search=getattr(args, "search", None) or None,
                page=page,
                page_size=50,
            )
            if isinstance(resp, list):
                page_items = resp
            elif isinstance(resp, dict):
                page_items = resp.get("items", [])
            else:
                page_items = []
            if not page_items:
                break
            all_pipelines.extend(page_items)
            # If we got fewer than 50 items the last page is done
            if len(page_items) < 50:
                break
            page += 1
    except Exception as exc:
        print(f"❌ Failed to fetch pipelines: {exc}")
        return 1

    print(f"   Found {len(all_pipelines)} pipeline(s)")

    limit = getattr(args, "limit", None)
    if limit and limit < len(all_pipelines):
        all_pipelines = all_pipelines[:limit]
        print(f"   Limited to first {limit} pipeline(s)")

    if not all_pipelines:
        print("⚠️  No pipelines to checkout")
        return 0

    success_count = 0
    skip_count = 0
    error_count = 0

    for i, pipeline in enumerate(all_pipelines, 1):
        remote_id = pipeline.get("id")
        name = pipeline.get("name") or remote_id or f"pipeline-{i}"
        state = pipeline.get("state", "unknown")
        slug = _slugify(name) or (f"pipeline-{remote_id[:8]}" if remote_id else f"pipeline-{i}")

        print(f"\n[{i}/{len(all_pipelines)}] {name}")
        print(f"   Remote ID : {remote_id}  |  State: {state}")

        if not remote_id:
            print("   ⚠️  Skipped: No remote ID")
            skip_count += 1
            continue

        # Check if workspace already exists
        pipeline_path = env_path / "pipelines" / slug
        if pipeline_path.exists() and not getattr(args, "force", False):
            print("   ⏭️  Skipped: Already exists (use --force to overwrite)")
            skip_count += 1
            continue

        if pipeline_path.exists() and getattr(args, "force", False):
            shutil.rmtree(pipeline_path)

        if dry_run:
            print(f"   DRY RUN: Would create workspace '{slug}'")
            success_count += 1
            continue

        try:
            # Fetch full pipeline details (includes WDL)
            detail = client.get_pipeline(remote_id)
            wdl = detail.get("wdl") or []
            if isinstance(wdl, str):
                wdl = json.loads(wdl)

            version_id = detail.get("draft_version_id") or detail.get("version_id")
            description = detail.get("description", "")
            source = detail.get("source") or {
                "integration_id": "internal_data_store",
                "integration_type": "internal",
                "integration_name": "Internal Data Store",
            }
            destinations = detail.get("destinations") or [
                {"type": "internal_data_store", "label": "results"}
            ]
            schedule_type = detail.get("schedule_type", "manual")

            # Create workspace
            ok, p, msg = manager.create_pipeline_workspace(
                pipeline_id=slug,
                name=name,
                description=description,
                prompt=detail.get("prompt", name),
                source=source,
                destinations=destinations,
                schedule_type=schedule_type,
                env_name=env,
            )

            if not ok and "already exists" not in msg:
                print(f"   ❌ Failed: {msg}")
                error_count += 1
                continue

            # Write WDL
            (p / "widdle.json").write_text(json.dumps(wdl, indent=2))

            # Version snapshot
            if wdl:
                manager.save_pipeline_version(slug, wdl, 1, env)

            # Update metadata with remote link and state
            manager.update_pipeline_workspace_meta(
                slug,
                {
                    "remote_pipeline_id": remote_id,
                    "version_id": version_id,
                    "state": state,
                },
                env,
            )

            wdl_steps = len(wdl) if wdl else 0
            print(f"   ✅ Created workspace: {p}  ({wdl_steps} WDL steps)")
            success_count += 1

        except Exception as exc:
            print(f"   ❌ Error: {exc}")
            error_count += 1

    # Summary
    print(f"\n{'=' * 70}")
    print("📊 CHECKOUT SUMMARY")
    print(f"{'=' * 70}")
    print(f"   ✅ Checked out : {success_count}")
    print(f"   ⏭️  Skipped     : {skip_count}")
    print(f"   ❌ Errors      : {error_count}")
    print(f"{'=' * 70}")
    if success_count:
        print(f"\nWorkspaces saved to: {env_path / 'pipelines'}")
        print("Edit WDL, then: python cli/save_pipeline_draft.py <pipeline-id>")
    print()

    return 0 if error_count == 0 else 1


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
    env_create.add_argument(
        "--id", required=True, help="Environment ID (e.g., 'clientx-marketing')"
    )
    env_create.add_argument("--name", required=True, help="Human-readable name")
    env_create.add_argument(
        "--description", help="Description of what this env is for (helps agents validate requests)"
    )
    env_create.add_argument(
        "--target", choices=["development", "staging", "production"], default="staging"
    )
    env_create.add_argument("--client", help="Client identifier")
    env_create.add_argument(
        "--domain", help="Primary business domain (e.g., 'marketing', 'inventory')"
    )
    env_create.add_argument("--use", action="store_true", help="Set as active")
    env_create.add_argument(
        "--dry-run", action="store_true", help="Simulate without making changes"
    )
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
    env_delete.add_argument(
        "--dry-run", action="store_true", help="Simulate without making changes"
    )
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
    agent_create.add_argument(
        "--dry-run", action="store_true", help="Simulate without making changes"
    )
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
    agent_add.add_argument("--dry-run", action="store_true", help="Simulate without making changes")
    agent_add.set_defaults(func=cmd_agent_add_subaction)

    # agent remove-subaction
    agent_remove = agent_subparsers.add_parser("remove-subaction", help="Remove sub-action")
    agent_remove.add_argument("--agent", required=True, help="Agent ID")
    agent_remove.add_argument("--action", required=True, help="Action ID")
    agent_remove.add_argument("--env", help="Environment")
    agent_remove.add_argument(
        "--dry-run", action="store_true", help="Simulate without making changes"
    )
    agent_remove.set_defaults(func=cmd_agent_remove_subaction)

    # agent move-action (move standalone action to agent)
    agent_move = agent_subparsers.add_parser(
        "move-action",
        help="Move standalone action to become an agent's sub-action",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Move action 'my-tool' to agent 'my-agent'
  python cli/workspace.py agent move-action --action my-tool --agent my-agent

  # Move without updating agent WDL (manual update later)
  python cli/workspace.py agent move-action --action my-tool --agent my-agent --no-wdl-update
        """,
    )
    agent_move.add_argument("--action", required=True, help="Standalone action ID to move")
    agent_move.add_argument("--agent", required=True, help="Target agent ID")
    agent_move.add_argument("--env", help="Environment")
    agent_move.add_argument("--no-wdl-update", action="store_true", help="Don't update agent's WDL")
    agent_move.add_argument(
        "--dry-run", action="store_true", help="Simulate without making changes"
    )
    agent_move.set_defaults(func=cmd_agent_move_action)

    # agent checkout
    agent_checkout = agent_subparsers.add_parser("checkout", help="Checkout agent from remote")
    agent_checkout.add_argument("--remote-id", required=True, help="Remote action ID")
    agent_checkout.add_argument("--env", required=True, help="Environment to checkout into")
    agent_checkout.add_argument("--id", help="Local agent ID (auto-generated if not provided)")
    agent_checkout.add_argument(
        "--include-subactions", action="store_true", help="Download all sub-actions"
    )
    agent_checkout.add_argument(
        "--dry-run", action="store_true", help="Simulate without making changes"
    )
    agent_checkout.set_defaults(func=cmd_agent_checkout)

    # agent sync
    agent_sync = agent_subparsers.add_parser("sync", help="Sync agent with remote")
    agent_sync.add_argument("id", help="Agent ID")
    agent_sync.add_argument("--env", help="Environment")
    agent_sync.add_argument("--pull", action="store_true", help="Download new sub-actions")
    agent_sync.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate without making changes (requires --pull to show effect)",
    )
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
    action_create.add_argument(
        "--dry-run", action="store_true", help="Simulate without making changes"
    )
    action_create.set_defaults(func=cmd_action_create)

    # action list
    action_list = action_subparsers.add_parser("list", help="List actions")
    action_list.add_argument("--env", help="Environment")
    action_list.add_argument("--agent", help="Agent")
    action_list.add_argument("--standalone-only", action="store_true", help="Exclude sub-actions")
    action_list.set_defaults(func=cmd_action_list)

    # action checkout-all
    action_checkout_all = action_subparsers.add_parser(
        "checkout-all",
        help="Bulk checkout actions from remote",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Checkout all actions
  python cli/workspace.py action checkout-all --env my-client-prod

  # Checkout first 10 actions
  python cli/workspace.py action checkout-all --env my-client-prod --limit 10

  # Checkout only Uber Agents with sub-actions
  python cli/workspace.py action checkout-all --env my-client-prod --uber-agents-only --include-subactions

  # Force overwrite existing
  python cli/workspace.py action checkout-all --env my-client-prod --force
        """,
    )
    action_checkout_all.add_argument("--env", help="Environment (uses active if not specified)")
    action_checkout_all.add_argument(
        "--limit", type=int, help="Limit number of actions to checkout"
    )
    action_checkout_all.add_argument(
        "--force", action="store_true", help="Overwrite existing local actions"
    )
    action_checkout_all.add_argument(
        "--tools-only", action="store_true", help="Only checkout tool-type actions"
    )
    action_checkout_all.add_argument(
        "--workflows-only", action="store_true", help="Only checkout workflow-type actions"
    )
    action_checkout_all.add_argument(
        "--uber-agents-only", action="store_true", help="Only checkout Uber Agents"
    )
    action_checkout_all.add_argument(
        "--include-subactions",
        action="store_true",
        help="Also download sub-actions for Uber Agents",
    )
    action_checkout_all.add_argument(
        "--dry-run", action="store_true", help="Simulate without making changes"
    )
    action_checkout_all.set_defaults(func=cmd_action_checkout_all)

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
    profile_update.add_argument(
        "--dry-run", action="store_true", help="Simulate without making changes"
    )
    profile_update.set_defaults(func=cmd_profile_update)

    # =========================================================================
    # PLAYGROUND-PROFILE commands (remote)
    # =========================================================================
    pg_parser = subparsers.add_parser(
        "playground-profile", help="Remote playground profile management"
    )
    pg_subparsers = pg_parser.add_subparsers(dest="pg_command")

    # playground-profile list
    pg_list = pg_subparsers.add_parser("list", help="List playground profiles")
    pg_list.add_argument("--integration-id", help="Filter by integration ID")
    pg_list.set_defaults(func=cmd_pg_profile_list)

    # playground-profile show
    pg_show = pg_subparsers.add_parser("show", help="Show playground profile details")
    pg_show.add_argument("id", nargs="?", help="Profile ID")
    pg_show.add_argument("--default", action="store_true", help="Show the default profile")
    pg_show.add_argument("--json", action="store_true", help="Output as JSON")
    pg_show.set_defaults(func=cmd_pg_profile_show)

    # playground-profile create
    pg_create = pg_subparsers.add_parser(
        "create",
        help="Create playground profile",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create from CLI flags
  python cli/workspace.py playground-profile create --name "My Profile" --app-base-url https://app.example.com --api-base-url https://api.example.com

  # Create from local adopt_profile.json (auto-maps base_url, security_params, etc.)
  python cli/workspace.py playground-profile create --name "My Profile" --from-profile

  # Create from JSON file
  python cli/workspace.py playground-profile create --from-file profile.json
        """,
    )
    pg_create.add_argument("--name", help="Profile name (required)")
    pg_create.add_argument("--app-base-url", help="Application base URL")
    pg_create.add_argument("--api-base-url", help="API base URL")
    pg_create.add_argument(
        "--from-profile",
        action="store_true",
        help="Pre-populate from local adopt_profile.json (maps base_url, security_params, etc.)",
    )
    pg_create.add_argument("--from-file", help="Path to JSON file with profile payload")
    pg_create.add_argument("--user-email", help="Login user email")
    pg_create.add_argument("--user-org-id", help="User organization ID")
    pg_create.add_argument("--integration-id", help="Integration ID")
    pg_create.add_argument("--is-default", action="store_true", help="Set as default profile")
    pg_create.add_argument("--security-headers", help="Security headers as JSON string")
    pg_create.add_argument("--user-properties", help="User properties as JSON string")
    pg_create.add_argument("--application", help="Third-party application name (e.g., Salesforce)")
    pg_create.add_argument("--documented-api-id", help="Documented API ID")
    pg_create.add_argument("--dry-run", action="store_true", help="Simulate without making changes")
    pg_create.set_defaults(func=cmd_pg_profile_create)

    # playground-profile update
    pg_update = pg_subparsers.add_parser("update", help="Update playground profile")
    pg_update.add_argument("id", help="Profile ID to update")
    pg_update.add_argument("--name", help="Profile name")
    pg_update.add_argument("--app-base-url", help="Application base URL")
    pg_update.add_argument("--api-base-url", help="API base URL")
    pg_update.add_argument("--user-email", help="Login user email")
    pg_update.add_argument("--user-org-id", help="User organization ID")
    pg_update.add_argument("--integration-id", help="Integration ID")
    pg_update.add_argument("--is-default", action="store_true", default=None, help="Set as default")
    pg_update.add_argument(
        "--is-available", action="store_true", default=None, help="Set as available"
    )
    pg_update.add_argument("--security-headers", help="Security headers as JSON string")
    pg_update.add_argument("--user-properties", help="User properties as JSON string")
    pg_update.add_argument("--application", help="Third-party application name")
    pg_update.add_argument("--documented-api-id", help="Documented API ID")
    pg_update.add_argument("--dry-run", action="store_true", help="Simulate without making changes")
    pg_update.set_defaults(func=cmd_pg_profile_update)

    # playground-profile delete
    pg_delete = pg_subparsers.add_parser("delete", help="Delete playground profile")
    pg_delete.add_argument("id", help="Profile ID to delete")
    pg_delete.add_argument("--force", action="store_true", help="Skip confirmation")
    pg_delete.add_argument("--dry-run", action="store_true", help="Simulate without making changes")
    pg_delete.set_defaults(func=cmd_pg_profile_delete)

    # =========================================================================
    # TOKEN-CONFIG commands (remote)
    # =========================================================================
    tc_parser = subparsers.add_parser("token-config", help="Remote token configuration management")
    tc_subparsers = tc_parser.add_subparsers(dest="tc_command")

    # token-config list
    tc_list = tc_subparsers.add_parser("list", help="List token configurations")
    tc_list.add_argument("--search", help="Search by name")
    tc_list.add_argument("--published", action="store_true", help="Show only published")
    tc_list.add_argument("--unpublished", action="store_true", help="Show only unpublished")
    tc_list.add_argument("--integration-id", help="Filter by integration ID")
    tc_list.add_argument("--page", type=int, default=1, help="Page number (default: 1)")
    tc_list.add_argument("--page-size", type=int, default=50, help="Page size (default: 50)")
    tc_list.set_defaults(func=cmd_token_config_list)

    # token-config show
    tc_show = tc_subparsers.add_parser("show", help="Show token configuration details")
    tc_show.add_argument("id", help="Token config ID")
    tc_show.add_argument("--json", action="store_true", help="Output as JSON")
    tc_show.set_defaults(func=cmd_token_config_show)

    # token-config create
    tc_create = tc_subparsers.add_parser(
        "create",
        help="Create token configuration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # localStorage token
  python cli/workspace.py token-config create --name "my-token" --domain-suffix "example.com" --storage-type localStorage --storage-key "auth_token"

  # Cookie token
  python cli/workspace.py token-config create --name "session" --domain-suffix "app.com" --storage-type cookie --cookie-key "SESSIONID" --cookie-domains "app.com,api.app.com"

  # Custom script from file
  python cli/workspace.py token-config create --name "custom" --domain-suffix "app.com" --storage-type customScript --custom-script @extract.js

  # From JSON file
  python cli/workspace.py token-config create --from-file token.json
        """,
    )
    tc_create.add_argument("--name", help="Token name (no spaces, required)")
    tc_create.add_argument("--domain-suffix", help="Domain suffix (required)")
    tc_create.add_argument(
        "--storage-type",
        choices=["localStorage", "sessionStorage", "cookie", "domElement", "customScript"],
        help="Storage type (required)",
    )
    tc_create.add_argument("--storage-key", help="Storage key (for localStorage/sessionStorage)")
    tc_create.add_argument("--cookie-key", help="Cookie key (for cookie type)")
    tc_create.add_argument("--cookie-domains", help="Comma-separated cookie domains")
    tc_create.add_argument("--use-all-cookies", action="store_true", help="Extract all cookies")
    tc_create.add_argument("--dom-selector", help="XPath selector (for domElement)")
    tc_create.add_argument("--dom-attribute", help="DOM attribute (for domElement)")
    tc_create.add_argument("--use-content", action="store_true", help="Use element content")
    tc_create.add_argument("--custom-script", help="Custom JS script or @filepath")
    tc_create.add_argument("--parser-logic", help="Parser JS logic or @filepath")
    tc_create.add_argument("--from-file", help="Path to JSON file with full payload")
    tc_create.add_argument("--no-publish", action="store_true", help="Create as unpublished")
    tc_create.add_argument("--integration-id", help="Integration ID")
    tc_create.add_argument("--dry-run", action="store_true", help="Simulate without making changes")
    tc_create.set_defaults(func=cmd_token_config_create)

    # token-config update
    tc_update = tc_subparsers.add_parser("update", help="Update token configuration")
    tc_update.add_argument("id", help="Token config ID to update")
    tc_update.add_argument("--name", help="Token name")
    tc_update.add_argument("--domain-suffix", help="Domain suffix")
    tc_update.add_argument(
        "--storage-type",
        choices=["localStorage", "sessionStorage", "cookie", "domElement", "customScript"],
        help="Storage type",
    )
    tc_update.add_argument("--storage-key", help="Storage key")
    tc_update.add_argument("--cookie-key", help="Cookie key")
    tc_update.add_argument("--cookie-domains", help="Comma-separated cookie domains")
    tc_update.add_argument("--dom-selector", help="XPath selector")
    tc_update.add_argument("--dom-attribute", help="DOM attribute")
    tc_update.add_argument("--custom-script", help="Custom JS script or @filepath")
    tc_update.add_argument("--parser-logic", help="Parser JS logic or @filepath")
    tc_update.add_argument("--integration-id", help="Integration ID")
    tc_update.add_argument("--publish", action="store_true", help="Publish the token config")
    tc_update.add_argument("--unpublish", action="store_true", help="Unpublish the token config")
    tc_update.add_argument("--dry-run", action="store_true", help="Simulate without making changes")
    tc_update.set_defaults(func=cmd_token_config_update)

    # token-config delete
    tc_delete = tc_subparsers.add_parser("delete", help="Delete token configuration")
    tc_delete.add_argument("id", help="Token config ID to delete")
    tc_delete.add_argument("--force", action="store_true", help="Skip confirmation")
    tc_delete.add_argument("--dry-run", action="store_true", help="Simulate without making changes")
    tc_delete.set_defaults(func=cmd_token_config_delete)

    # token-config publish
    tc_publish = tc_subparsers.add_parser("publish", help="Publish token configuration(s)")
    tc_publish.add_argument("ids", nargs="+", help="Token config ID(s) to publish")
    tc_publish.add_argument(
        "--dry-run", action="store_true", help="Simulate without making changes"
    )
    tc_publish.set_defaults(func=cmd_token_config_publish)

    # token-config unpublish
    tc_unpublish = tc_subparsers.add_parser("unpublish", help="Unpublish token configuration(s)")
    tc_unpublish.add_argument("ids", nargs="+", help="Token config ID(s) to unpublish")
    tc_unpublish.add_argument(
        "--dry-run", action="store_true", help="Simulate without making changes"
    )
    tc_unpublish.set_defaults(func=cmd_token_config_unpublish)

    # =========================================================================
    # PIPELINE commands
    # =========================================================================
    pipeline_parser = subparsers.add_parser("pipeline", help="Pipeline workspace management")
    pipeline_subparsers = pipeline_parser.add_subparsers(dest="pipeline_command")

    # pipeline create
    pl_create = pipeline_subparsers.add_parser("create", help="Create a new pipeline workspace")
    pl_create.add_argument("--id", help="Pipeline ID (slug). Derived from title if omitted.")
    pl_create.add_argument("--title", "-t", required=True, help="Pipeline title")
    pl_create.add_argument("--description", "-d", help="Pipeline description")
    pl_create.add_argument(
        "--prompt",
        help="One-line prompt describing what the pipeline does",
    )
    pl_create.add_argument(
        "--source-type",
        choices=["internal", "salesforce", "connector"],
        default="internal",
        help="Source type (default: internal)",
    )
    pl_create.add_argument("--source-connector-id", help="Connector instance ID")
    pl_create.add_argument(
        "--source-connector-type", help="Connector provider type (e.g. amazon_s3)"
    )
    pl_create.add_argument("--source-connector-name", help="Human-readable source name")
    pl_create.set_defaults(func=cmd_pipeline_create)

    # pipeline list
    pl_list = pipeline_subparsers.add_parser("list", help="List pipeline workspaces")
    pl_list.add_argument(
        "--state",
        choices=["local", "draft", "published", "running", "paused", "error"],
        help="Filter by state",
    )
    pl_list.add_argument("--json", action="store_true", help="Output as JSON")
    pl_list.set_defaults(func=cmd_pipeline_list)

    # pipeline show
    pl_show = pipeline_subparsers.add_parser("show", help="Show pipeline workspace details")
    pl_show.add_argument("pipeline_id", help="Pipeline ID")
    pl_show.add_argument("--json", action="store_true", help="Output as JSON")
    pl_show.set_defaults(func=cmd_pipeline_show)

    # pipeline checkout-all
    pl_checkout = pipeline_subparsers.add_parser(
        "checkout-all",
        help="Download all remote pipelines into local workspaces",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Checkout all pipelines from active environment
  python cli/workspace.py pipeline checkout-all

  # Checkout only running pipelines
  python cli/workspace.py pipeline checkout-all --state running

  # Limit to first 10 pipelines
  python cli/workspace.py pipeline checkout-all --limit 10

  # Force overwrite existing workspaces
  python cli/workspace.py pipeline checkout-all --force

  # Dry run (show what would be created)
  python cli/workspace.py pipeline checkout-all --dry-run

  # Use a specific environment
  python cli/workspace.py pipeline checkout-all --env my-client-prod
        """,
    )
    pl_checkout.add_argument("--env", help="Environment to checkout into (uses active if not set)")
    pl_checkout.add_argument(
        "--limit", type=int, metavar="N", help="Max number of pipelines to checkout"
    )
    pl_checkout.add_argument(
        "--state",
        choices=["draft", "running", "paused", "error"],
        help="Only checkout pipelines with this state",
    )
    pl_checkout.add_argument("--search", help="Filter remote pipelines by name")
    pl_checkout.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing local workspaces",
    )
    pl_checkout.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be created without making changes",
    )
    pl_checkout.set_defaults(func=cmd_pipeline_checkout_all)

    # Global verbose flag
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed debug information (workspace resolution, API calls, file operations)",
    )

    # Parse and execute
    args = parser.parse_args()

    global _verbose
    _verbose = getattr(args, "verbose", False)
    if _verbose:
        print("[VERBOSE] Verbose mode enabled", file=sys.stderr)

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
        elif args.command == "playground-profile":
            pg_parser.print_help()
        elif args.command == "token-config":
            tc_parser.print_help()
        elif args.command == "pipeline":
            pipeline_parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
