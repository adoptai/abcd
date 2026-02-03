#!/usr/bin/env python3
"""
Move Action Between Environments

This script moves actions between environments (e.g., staging → production).
It requires EXPLICIT user confirmation and cannot be run automatically by agents.

Usage:
    python cli/move_action.py <action-id> --from <source-env> --to <dest-env>
    python cli/move_action.py get-orderpoints --from staging --to production
    python cli/move_action.py --list-actions --env staging  # List actions in env

Features:
    - Copies action files to destination environment
    - Optionally copies to an agent within the destination
    - Updates metadata with new environment info
    - Preserves version history
    - Requires explicit user confirmation (cannot be auto-run)
"""

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path


# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


def get_workspaces_root() -> Path:
    """Get the workspaces root directory."""
    return Path(__file__).parent.parent / "workspaces"


def list_environments() -> list[str]:
    """List all available environments."""
    workspaces_root = get_workspaces_root()
    envs = []
    for item in workspaces_root.iterdir():
        if item.is_dir() and not item.name.startswith("."):
            envs.append(item.name)
    return sorted(envs)


def list_actions_in_env(env_name: str, include_agent_actions: bool = True) -> list[dict]:
    """List all actions in an environment."""
    workspaces_root = get_workspaces_root()
    env_path = workspaces_root / env_name
    
    if not env_path.exists():
        print(f"❌ Environment not found: {env_name}")
        return []
    
    actions = []
    
    # Check standalone actions
    actions_dir = env_path / "actions"
    if actions_dir.exists():
        for action_dir in actions_dir.iterdir():
            if action_dir.is_dir() and (action_dir / "widdle.json").exists():
                metadata = _load_metadata(action_dir)
                actions.append({
                    "id": action_dir.name,
                    "title": metadata.get("title", action_dir.name),
                    "path": str(action_dir),
                    "agent": None,
                    "type": "standalone"
                })
    
    # Check agent actions
    if include_agent_actions:
        agents_dir = env_path / "agents"
        if agents_dir.exists():
            for agent_dir in agents_dir.iterdir():
                if agent_dir.is_dir():
                    agent_actions_dir = agent_dir / "actions"
                    if agent_actions_dir.exists():
                        for action_dir in agent_actions_dir.iterdir():
                            if action_dir.is_dir() and (action_dir / "widdle.json").exists():
                                metadata = _load_metadata(action_dir)
                                actions.append({
                                    "id": action_dir.name,
                                    "title": metadata.get("title", action_dir.name),
                                    "path": str(action_dir),
                                    "agent": agent_dir.name,
                                    "type": "agent-action"
                                })
    
    return actions


def _load_metadata(action_path: Path) -> dict:
    """Load action metadata."""
    metadata_file = action_path / "metadata.json"
    if metadata_file.exists():
        try:
            return json.loads(metadata_file.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def find_action_path(env_name: str, action_id: str, agent_name: str | None = None) -> Path | None:
    """Find the path to an action in an environment."""
    workspaces_root = get_workspaces_root()
    env_path = workspaces_root / env_name
    
    if not env_path.exists():
        return None
    
    # If agent specified, look there first
    if agent_name:
        agent_action_path = env_path / "agents" / agent_name / "actions" / action_id
        if agent_action_path.exists():
            return agent_action_path
    
    # Check standalone actions
    standalone_path = env_path / "actions" / action_id
    if standalone_path.exists():
        return standalone_path
    
    # Search in all agents
    agents_dir = env_path / "agents"
    if agents_dir.exists():
        for agent_dir in agents_dir.iterdir():
            if agent_dir.is_dir():
                agent_action_path = agent_dir / "actions" / action_id
                if agent_action_path.exists():
                    return agent_action_path
    
    return None


def get_destination_path(
    dest_env: str, 
    action_id: str, 
    dest_agent: str | None = None
) -> Path:
    """Get the destination path for an action."""
    workspaces_root = get_workspaces_root()
    env_path = workspaces_root / dest_env
    
    if dest_agent:
        return env_path / "agents" / dest_agent / "actions" / action_id
    else:
        return env_path / "actions" / action_id


def move_action(
    action_id: str,
    source_env: str,
    dest_env: str,
    source_agent: str | None = None,
    dest_agent: str | None = None,
    copy_mode: bool = False,
    force: bool = False
) -> tuple[bool, str]:
    """
    Move or copy an action between environments.
    
    Args:
        action_id: The action to move
        source_env: Source environment
        dest_env: Destination environment
        source_agent: Source agent (if action is within an agent)
        dest_agent: Destination agent (if moving to an agent)
        copy_mode: If True, copy instead of move
        force: If True, overwrite existing destination
    
    Returns:
        Tuple of (success, message)
    """
    # Find source action
    source_path = find_action_path(source_env, action_id, source_agent)
    if not source_path:
        return False, f"Action '{action_id}' not found in environment '{source_env}'"
    
    # Get destination path
    dest_path = get_destination_path(dest_env, action_id, dest_agent)
    
    # Check if destination exists
    if dest_path.exists() and not force:
        return False, f"Action already exists at destination: {dest_path}"
    
    # Ensure destination parent directory exists
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Copy or move
    if copy_mode:
        if dest_path.exists():
            shutil.rmtree(dest_path)
        shutil.copytree(source_path, dest_path)
        operation = "Copied"
    else:
        if dest_path.exists():
            shutil.rmtree(dest_path)
        shutil.move(str(source_path), str(dest_path))
        operation = "Moved"
    
    # Update metadata
    metadata_file = dest_path / "metadata.json"
    if metadata_file.exists():
        try:
            metadata = json.loads(metadata_file.read_text())
            metadata["env_name"] = dest_env
            if dest_agent:
                metadata["agent_name"] = dest_agent
            elif "agent_name" in metadata and not dest_agent:
                del metadata["agent_name"]
            metadata["moved_at"] = datetime.now().isoformat()
            metadata["moved_from"] = {
                "env": source_env,
                "agent": source_agent
            }
            metadata_file.write_text(json.dumps(metadata, indent=2))
        except json.JSONDecodeError:
            pass
    
    return True, f"{operation} '{action_id}' from {source_env} to {dest_env}"


def print_action_summary(action_path: Path) -> None:
    """Print a summary of an action."""
    metadata = _load_metadata(action_path)
    widdle_file = action_path / "widdle.json"
    
    print(f"   📋 Title: {metadata.get('title', 'Unknown')}")
    print(f"   🆔 Action ID: {metadata.get('action_id', 'Not linked')}")
    print(f"   📁 Path: {action_path}")
    
    if widdle_file.exists():
        try:
            widdle = json.loads(widdle_file.read_text())
            op_count = len([b for b in widdle if isinstance(b, dict) and b.get("operation")])
            print(f"   ⚙️  Operations: {op_count}")
        except json.JSONDecodeError:
            pass
    
    # Check for versions
    versions_dir = action_path / "versions"
    if versions_dir.exists():
        versions = list(versions_dir.glob("v*_widdle.json"))
        print(f"   📚 Local versions: {len(versions)}")
    
    # Check for test cases
    test_cases_dir = action_path / "test_cases"
    if test_cases_dir.exists():
        tests = list(test_cases_dir.glob("test_*.json"))
        print(f"   🧪 Test cases: {len(tests)}")


def interactive_confirmation(
    action_id: str,
    source_env: str,
    dest_env: str,
    source_path: Path,
    dest_path: Path,
    copy_mode: bool
) -> bool:
    """
    Request explicit user confirmation.
    
    This CANNOT be bypassed by agents - requires real user input.
    """
    operation = "COPY" if copy_mode else "MOVE"
    
    print()
    print("=" * 70)
    print(f"🚚 ACTION {operation} CONFIRMATION")
    print("=" * 70)
    print()
    print(f"⚠️  This operation requires YOUR EXPLICIT CONFIRMATION.")
    print(f"   AI agents cannot automatically approve this operation.")
    print()
    print(f"📦 Action: {action_id}")
    print(f"📤 From:   {source_env} ({source_path})")
    print(f"📥 To:     {dest_env} ({dest_path})")
    print()
    print("Source action details:")
    print_action_summary(source_path)
    print()
    
    if dest_path.exists():
        print("⚠️  WARNING: Destination already exists and will be OVERWRITTEN!")
        print()
    
    print("-" * 70)
    print()
    print("To confirm, type the FULL action ID exactly as shown above:")
    print(f"   → {action_id}")
    print()
    
    try:
        user_input = input("Confirm action ID: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n❌ Operation cancelled.")
        return False
    
    if user_input != action_id:
        print()
        print(f"❌ Confirmation failed. You typed: '{user_input}'")
        print(f"   Expected: '{action_id}'")
        print()
        print("Operation cancelled for safety.")
        return False
    
    print()
    print("✅ Confirmation received.")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Move or copy actions between environments",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Move action from staging to production
    python cli/move_action.py get-orderpoints --from staging --to production
    
    # Copy action (keep original)
    python cli/move_action.py get-orderpoints --from staging --to production --copy
    
    # Move to a specific agent in destination
    python cli/move_action.py get-orderpoints --from staging --to production --dest-agent inventory-agent
    
    # List actions in an environment
    python cli/move_action.py --list-actions --env staging
    
    # List available environments
    python cli/move_action.py --list-envs

⚠️  IMPORTANT: This script requires explicit user confirmation.
    AI agents cannot automatically approve the move operation.
"""
    )
    
    parser.add_argument(
        "action_id",
        nargs="?",
        help="The action ID to move"
    )
    parser.add_argument(
        "--from", "-f",
        dest="source_env",
        help="Source environment"
    )
    parser.add_argument(
        "--to", "-t",
        dest="dest_env",
        help="Destination environment"
    )
    parser.add_argument(
        "--source-agent",
        help="Source agent (if action is within an agent)"
    )
    parser.add_argument(
        "--dest-agent",
        help="Destination agent (move action into an agent)"
    )
    parser.add_argument(
        "--copy", "-c",
        action="store_true",
        help="Copy instead of move (keep original)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force overwrite if destination exists (still requires confirmation)"
    )
    parser.add_argument(
        "--list-actions",
        action="store_true",
        help="List actions in an environment"
    )
    parser.add_argument(
        "--list-envs",
        action="store_true",
        help="List available environments"
    )
    parser.add_argument(
        "--env", "-e",
        help="Environment for --list-actions"
    )
    
    args = parser.parse_args()
    
    # List environments
    if args.list_envs:
        print()
        print("📂 Available Environments:")
        print("-" * 40)
        for env in list_environments():
            print(f"   • {env}")
        print()
        return
    
    # List actions in environment
    if args.list_actions:
        if not args.env:
            print("❌ Error: --env required with --list-actions")
            print("   Usage: python cli/move_action.py --list-actions --env staging")
            sys.exit(1)
        
        print()
        print(f"📋 Actions in '{args.env}':")
        print("-" * 60)
        
        actions = list_actions_in_env(args.env)
        if not actions:
            print("   No actions found.")
        else:
            standalone = [a for a in actions if a["type"] == "standalone"]
            agent_actions = [a for a in actions if a["type"] == "agent-action"]
            
            if standalone:
                print("\n   Standalone Actions:")
                for action in standalone:
                    print(f"   • {action['id']}")
                    print(f"     Title: {action['title']}")
            
            if agent_actions:
                # Group by agent
                agents = {}
                for action in agent_actions:
                    agent = action["agent"]
                    if agent not in agents:
                        agents[agent] = []
                    agents[agent].append(action)
                
                for agent_name, agent_acts in agents.items():
                    print(f"\n   Agent: {agent_name}")
                    for action in agent_acts:
                        print(f"   • {action['id']}")
                        print(f"     Title: {action['title']}")
        print()
        return
    
    # Validate move/copy arguments
    if not args.action_id:
        parser.print_help()
        sys.exit(1)
    
    if not args.source_env or not args.dest_env:
        print("❌ Error: Both --from and --to are required")
        print("   Usage: python cli/move_action.py <action-id> --from <source> --to <dest>")
        sys.exit(1)
    
    if args.source_env == args.dest_env and not args.dest_agent and not args.source_agent:
        print("❌ Error: Source and destination are the same")
        print("   Use --dest-agent to move action into an agent within the same environment")
        sys.exit(1)
    
    # Find source action
    source_path = find_action_path(args.source_env, args.action_id, args.source_agent)
    if not source_path:
        print(f"❌ Action '{args.action_id}' not found in environment '{args.source_env}'")
        print()
        print("Available actions:")
        actions = list_actions_in_env(args.source_env)
        for action in actions[:10]:
            loc = f" (in {action['agent']})" if action['agent'] else ""
            print(f"   • {action['id']}{loc}")
        if len(actions) > 10:
            print(f"   ... and {len(actions) - 10} more")
        sys.exit(1)
    
    # Get destination path
    dest_path = get_destination_path(args.dest_env, args.action_id, args.dest_agent)
    
    # Check destination environment exists
    dest_env_path = get_workspaces_root() / args.dest_env
    if not dest_env_path.exists():
        print(f"❌ Destination environment '{args.dest_env}' not found")
        print()
        print("Available environments:")
        for env in list_environments():
            print(f"   • {env}")
        sys.exit(1)
    
    # Check destination agent exists if specified
    if args.dest_agent:
        dest_agent_path = dest_env_path / "agents" / args.dest_agent
        if not dest_agent_path.exists():
            print(f"❌ Destination agent '{args.dest_agent}' not found in '{args.dest_env}'")
            sys.exit(1)
    
    # Check if destination exists
    if dest_path.exists() and not args.force:
        print(f"❌ Action already exists at destination: {dest_path}")
        print("   Use --force to overwrite (confirmation still required)")
        sys.exit(1)
    
    # Request explicit confirmation
    confirmed = interactive_confirmation(
        args.action_id,
        args.source_env,
        args.dest_env,
        source_path,
        dest_path,
        args.copy
    )
    
    if not confirmed:
        sys.exit(1)
    
    # Perform the move/copy
    success, message = move_action(
        args.action_id,
        args.source_env,
        args.dest_env,
        args.source_agent,
        args.dest_agent,
        args.copy,
        args.force
    )
    
    if success:
        print()
        print("=" * 70)
        print("✅ " + message)
        print("=" * 70)
        print()
        print("📍 New location:", dest_path)
        print()
        print("Next steps:")
        print(f"   1. Switch to environment: python cli/workspace.py env use {args.dest_env}")
        print(f"   2. Test the action: python cli/test_wdl_action.py {args.action_id}")
        print(f"   3. Save draft if needed: python cli/save_wdl_draft.py --workflow-id {args.action_id}")
        print()
    else:
        print(f"❌ {message}")
        sys.exit(1)


if __name__ == "__main__":
    main()

