#!/usr/bin/env python3
"""
Move Actions and Agents Between Local Workspace Environments

This script moves actions or agents (with all sub-actions) between LOCAL workspace
environments (e.g., clienta-staging → clienta-prod, or clienta-prod → clientb-prod).

Both source and destination environments MUST exist as local workspaces in the
workspaces/ directory before running this script.

It requires EXPLICIT user confirmation and cannot be run automatically by agents.

Usage:
    # Move a single action between workspace environments
    python cli/move_action.py <action-id> --from <source-workspace> --to <dest-workspace>

    # Move an entire agent with all sub-actions
    python cli/move_action.py <agent-id> --agent --from <source-workspace> --to <dest-workspace>

    # List available workspace environments
    python cli/move_action.py --list-envs

    # List actions/agents in a workspace
    python cli/move_action.py --list-actions --env clienta-staging
    python cli/move_action.py --list-agents --env clienta-prod

Features:
    - Move actions/agents between LOCAL workspace environments
    - Works across different clients (e.g., clienta → clientb)
    - Clears remote action_ids (sub-actions need re-registration in new environment)
    - Updates agent WDL action_ids placeholder for re-registration
    - Requires explicit user confirmation (cannot be auto-run)

IMPORTANT:
    - Environment names are LOCAL workspace directory names (e.g., clienta-prod, clientb-staging)
    - Both source and destination workspaces must already exist locally
    - Use 'python cli/workspace.py env create' to create new workspaces first
"""

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

_verbose = False


def _vprint(*args: object) -> None:
    if _verbose:
        print("[VERBOSE]", *args)


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


def list_agents_in_env(env_name: str) -> list[dict]:
    """List all agents in an environment."""
    workspaces_root = get_workspaces_root()
    env_path = workspaces_root / env_name

    if not env_path.exists():
        return []

    agents = []
    agents_dir = env_path / "agents"

    if agents_dir.exists():
        for agent_dir in agents_dir.iterdir():
            if agent_dir.is_dir():
                agent_json = agent_dir / "agent.json"
                widdle_file = agent_dir / "widdle.json"

                agent_info: dict[str, Any] = {
                    "id": agent_dir.name,
                    "name": agent_dir.name,
                    "path": str(agent_dir),
                    "sub_actions": [],
                    "has_widdle": widdle_file.exists(),
                }

                # Load from unified metadata.json first, fallback to agent.json
                meta_file = agent_dir / "metadata.json"
                if meta_file.exists():
                    try:
                        data = json.loads(meta_file.read_text())
                        agent_info["name"] = data.get("title") or data.get("name", agent_dir.name)
                        agent_info["remote_action_id"] = data.get("action_id") or data.get(
                            "remote_action_id"
                        )
                    except json.JSONDecodeError:
                        pass
                elif agent_json.exists():
                    try:
                        data = json.loads(agent_json.read_text())
                        agent_info["name"] = data.get("name", agent_dir.name)
                        agent_info["remote_action_id"] = data.get("remote_action_id")
                    except json.JSONDecodeError:
                        pass

                # List sub-actions
                actions_dir = agent_dir / "actions"
                if actions_dir.exists():
                    for action_dir in actions_dir.iterdir():
                        if action_dir.is_dir() and (action_dir / "widdle.json").exists():
                            metadata = _load_metadata(action_dir)
                            agent_info["sub_actions"].append(
                                {
                                    "id": action_dir.name,
                                    "title": metadata.get("title", action_dir.name),
                                    "action_id": metadata.get("action_id"),
                                    "path": str(action_dir),
                                }
                            )

                agents.append(agent_info)

    return agents


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
                actions.append(
                    {
                        "id": action_dir.name,
                        "title": metadata.get("title", action_dir.name),
                        "action_id": metadata.get("action_id"),
                        "path": str(action_dir),
                        "agent": None,
                        "type": "standalone",
                    }
                )

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
                                actions.append(
                                    {
                                        "id": action_dir.name,
                                        "title": metadata.get("title", action_dir.name),
                                        "action_id": metadata.get("action_id"),
                                        "path": str(action_dir),
                                        "agent": agent_dir.name,
                                        "type": "agent-action",
                                    }
                                )

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


def _load_json(file_path: Path) -> dict | list | None:
    """Load JSON file."""
    if file_path.exists():
        try:
            return json.loads(file_path.read_text())
        except json.JSONDecodeError:
            return None
    return None


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


def find_agent_path(env_name: str, agent_id: str) -> Path | None:
    """Find the path to an agent in an environment."""
    workspaces_root = get_workspaces_root()
    agent_path = workspaces_root / env_name / "agents" / agent_id

    if agent_path.exists() and agent_path.is_dir():
        return agent_path
    return None


def get_destination_path(dest_env: str, action_id: str, dest_agent: str | None = None) -> Path:
    """Get the destination path for an action."""
    workspaces_root = get_workspaces_root()
    env_path = workspaces_root / dest_env

    if dest_agent:
        return env_path / "agents" / dest_agent / "actions" / action_id
    else:
        return env_path / "actions" / action_id


def get_agent_destination_path(dest_env: str, agent_id: str) -> Path:
    """Get the destination path for an agent."""
    workspaces_root = get_workspaces_root()
    return workspaces_root / dest_env / "agents" / agent_id


def move_action(
    action_id: str,
    source_env: str,
    dest_env: str,
    source_agent: str | None = None,
    dest_agent: str | None = None,
    copy_mode: bool = False,
    force: bool = False,
    clear_remote_id: bool = False,
    dry_run: bool = False,
) -> tuple[bool, str, Path | None]:
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
        clear_remote_id: If True, clear the remote action_id (for re-registration)

    Returns:
        Tuple of (success, message, dest_path)
    """
    # Find source action
    _vprint(f"Resolving source path: {action_id} in {source_env} (agent={source_agent})")
    source_path = find_action_path(source_env, action_id, source_agent)
    if not source_path:
        return False, f"Action '{action_id}' not found in environment '{source_env}'", None
    _vprint(f"Source path: {source_path}")

    # Get destination path
    dest_path = get_destination_path(dest_env, action_id, dest_agent)
    _vprint(f"Destination path: {dest_path}")

    # Check if destination exists
    if dest_path.exists() and not force:
        return False, f"Action already exists at destination: {dest_path}", None

    if dry_run:
        operation = "copy" if copy_mode else "move"
        print(f"   DRY RUN: Would {operation} '{action_id}' from {source_env} to {dest_env}")
        print(f"   DRY RUN: Source: {source_path}")
        print(f"   DRY RUN: Dest:   {dest_path}")
        if clear_remote_id:
            print("   DRY RUN: Would clear remote action_id (re-registration required)")
        return (
            True,
            f"DRY RUN: Would {'copy' if copy_mode else 'move'} '{action_id}' from {source_env} to {dest_env}",
            dest_path,
        )

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

    # Update metadata (apply unified schema fields during move)
    metadata_file = dest_path / "metadata.json"
    old_action_id = None
    if metadata_file.exists():
        try:
            metadata = json.loads(metadata_file.read_text())
            old_action_id = metadata.get("action_id")
            metadata["env_name"] = dest_env
            if dest_agent:
                metadata["agent_name"] = dest_agent
                # Ensure unified schema fields reflect sub_action status
                metadata["type"] = "sub_action"
                metadata["is_tool_mode"] = True
                metadata["is_visible_in_list"] = False
                metadata.setdefault("sub_actions", None)
            else:
                if "agent_name" in metadata:
                    del metadata["agent_name"]
                # Standalone action — always overwrite so a former sub_action
                # is correctly promoted to a standalone action after move.
                metadata["type"] = "action"
                metadata["is_tool_mode"] = False
                metadata["is_visible_in_list"] = True
                metadata.setdefault("sub_actions", None)
            metadata["moved_at"] = datetime.now().isoformat()
            metadata["moved_from"] = {
                "env": source_env,
                "agent": source_agent,
                "original_action_id": old_action_id,
            }

            # Clear remote action_id if requested (for re-registration in new env)
            if clear_remote_id and "action_id" in metadata:
                metadata["previous_action_id"] = metadata["action_id"]
                del metadata["action_id"]
            if clear_remote_id and "remote_action_id" in metadata:
                del metadata["remote_action_id"]

            metadata_file.write_text(json.dumps(metadata, indent=2))
        except json.JSONDecodeError:
            pass

    # Also clear .action_id file if clearing remote ID
    if clear_remote_id:
        action_id_file = dest_path / ".action_id"
        if action_id_file.exists():
            action_id_file.unlink()

    return True, f"{operation} '{action_id}' from {source_env} to {dest_env}", dest_path


def _update_env_json(env_name: str, agent_id: str, action: str) -> None:
    """
    Add or remove an agent entry from env.json so workspace.py list commands stay correct.

    action: "add" | "remove"
    """
    workspaces_root = get_workspaces_root()
    env_json_path = workspaces_root / env_name / "env.json"
    if not env_json_path.exists():
        return

    try:
        data = json.loads(env_json_path.read_text())
    except (json.JSONDecodeError, OSError):
        return

    agents_list: list = data.get("agents", [])

    if action == "add":
        # Avoid duplicates
        if agent_id not in agents_list:
            agents_list.append(agent_id)
    elif action == "remove":
        agents_list = [a for a in agents_list if a != agent_id]

    data["agents"] = agents_list
    data["updated_at"] = datetime.now().isoformat()

    try:
        env_json_path.write_text(json.dumps(data, indent=2))
    except OSError:
        pass  # Best-effort; don't crash the move


def move_agent(
    agent_id: str,
    source_env: str,
    dest_env: str,
    copy_mode: bool = False,
    force: bool = False,
    dry_run: bool = False,
) -> tuple[bool, str, dict]:
    """
    Move or copy an agent with all sub-actions.

    This will:
    1. Copy/move all sub-actions
    2. Clear their remote action_ids (they need new ones in the new env)
    3. Copy/move the agent itself
    4. Update the agent's WDL with placeholder action_ids
    5. Update env.json in both source and destination environments

    Args:
        agent_id: The agent to move
        source_env: Source environment
        dest_env: Destination environment
        copy_mode: If True, copy instead of move
        force: If True, overwrite existing destination

    Returns:
        Tuple of (success, message, details_dict)
    """
    details: dict[str, Any] = {
        "sub_actions_moved": [],
        "sub_actions_failed": [],
        "agent_path": None,
        "action_id_mapping": {},
    }

    # Find source agent
    _vprint(f"Resolving source agent: {agent_id} in {source_env}")
    source_path = find_agent_path(source_env, agent_id)
    if not source_path:
        return False, f"Agent '{agent_id}' not found in environment '{source_env}'", details
    _vprint(f"Source agent path: {source_path}")

    # Get destination path
    dest_path = get_agent_destination_path(dest_env, agent_id)
    _vprint(f"Destination agent path: {dest_path}")

    # Check if destination exists
    if dest_path.exists() and not force:
        return False, f"Agent already exists at destination: {dest_path}", details

    # Get list of sub-actions
    sub_actions = []
    actions_dir = source_path / "actions"
    if actions_dir.exists():
        for action_dir in actions_dir.iterdir():
            if action_dir.is_dir() and (action_dir / "widdle.json").exists():
                metadata = _load_metadata(action_dir)
                sub_actions.append(
                    {
                        "id": action_dir.name,
                        "title": metadata.get("title", action_dir.name),
                        "old_action_id": metadata.get("action_id"),
                        "path": action_dir,
                    }
                )

    if dry_run:
        op = "copy" if copy_mode else "move"
        print(f"   DRY RUN: Would {op} agent '{agent_id}' from {source_env} to {dest_env}")
        print(f"   DRY RUN: Source: {source_path}")
        print(f"   DRY RUN: Dest:   {dest_path}")
        for sa in sub_actions:
            print(
                f"   DRY RUN: Would {op} sub-action: {sa['id']} (old_action_id={sa.get('old_action_id')})"
            )
        print("   DRY RUN: Would clear all sub-action remote IDs (re-registration required)")
        print("   DRY RUN: Would update env.json registrations")
        details["agent_path"] = dest_path
        details["sub_actions_moved"] = [sa["id"] for sa in sub_actions]
        return (
            True,
            f"DRY RUN: Would {'copy' if copy_mode else 'move'} agent '{agent_id}' with {len(sub_actions)} sub-actions from {source_env} to {dest_env}",
            details,
        )

    # Ensure destination parent directory exists
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # Copy/move the entire agent directory
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

    details["agent_path"] = dest_path

    # Update metadata.json for the agent (primary source of truth)
    agent_metadata_file = dest_path / "metadata.json"
    # Also handle legacy agent.json for workspaces that still have it
    agent_json_file = dest_path / "agent.json"

    # Determine which file to read as base (prefer metadata.json)
    if agent_metadata_file.exists():
        try:
            metadata = json.loads(agent_metadata_file.read_text())
        except json.JSONDecodeError:
            metadata = {}
    elif agent_json_file.exists():
        try:
            legacy = json.loads(agent_json_file.read_text())
            # Synthesize metadata from agent.json
            metadata = {
                "workflow_id": legacy.get("agent_id", dest_path.name),
                "title": legacy.get("name", dest_path.name),
                "description": legacy.get("description", ""),
                "type": "agent",
                "sub_actions": legacy.get("sub_actions", []),
                "action_id": legacy.get("remote_action_id"),
            }
        except json.JSONDecodeError:
            metadata = {}
    else:
        metadata = {}

    old_action_id = metadata.get("action_id")

    metadata["env_name"] = dest_env
    metadata["type"] = "agent"  # always overwrite — direct assignment
    metadata["moved_at"] = datetime.now().isoformat()
    metadata["moved_from"] = {"env": source_env, "original_action_id": old_action_id}

    # Clear remote action_id (needs re-registration in new env)
    if "action_id" in metadata:
        metadata["previous_action_id"] = metadata["action_id"]
        del metadata["action_id"]
    metadata["updated_at"] = datetime.now().isoformat()

    agent_metadata_file.write_text(json.dumps(metadata, indent=2))

    # Also update legacy agent.json if it exists (backward compat)
    if agent_json_file.exists():
        try:
            agent_data = json.loads(agent_json_file.read_text())
            old_remote_id = agent_data.get("remote_action_id")
            if "remote_action_id" in agent_data:
                agent_data["previous_remote_action_id"] = agent_data["remote_action_id"]
                del agent_data["remote_action_id"]
            agent_data["moved_at"] = metadata["moved_at"]
            agent_data["moved_from"] = {
                "env": source_env,
                "original_remote_action_id": old_remote_id,
            }
            agent_json_file.write_text(json.dumps(agent_data, indent=2))
        except json.JSONDecodeError:
            pass

    # Clear .action_id file for agent
    agent_action_id_file = dest_path / ".action_id"
    if agent_action_id_file.exists():
        agent_action_id_file.unlink()

    # Process each sub-action - clear their remote IDs
    dest_actions_dir = dest_path / "actions"
    if dest_actions_dir.exists():
        for action_dir in dest_actions_dir.iterdir():
            if action_dir.is_dir():
                action_id = action_dir.name

                # Update metadata (apply unified schema fields during move)
                metadata_file = action_dir / "metadata.json"
                if metadata_file.exists():
                    try:
                        metadata = json.loads(metadata_file.read_text())
                        old_action_id = metadata.get("action_id")

                        metadata["env_name"] = dest_env
                        metadata["agent_name"] = agent_id
                        # Always overwrite so the schema is authoritative after move
                        metadata["type"] = "sub_action"
                        metadata["is_tool_mode"] = True
                        metadata["is_visible_in_list"] = False
                        metadata.setdefault("sub_actions", None)
                        metadata["moved_at"] = datetime.now().isoformat()
                        metadata["moved_from"] = {
                            "env": source_env,
                            "agent": agent_id,
                            "original_action_id": old_action_id,
                        }

                        # Store mapping for later
                        if old_action_id:
                            details["action_id_mapping"][action_id] = {
                                "old_action_id": old_action_id,
                                "new_action_id": None,  # Will be set after registration
                            }

                        # Clear remote action_id (needs re-registration in new env)
                        if "action_id" in metadata:
                            metadata["previous_action_id"] = metadata["action_id"]
                            del metadata["action_id"]
                        # Also clear legacy remote_action_id field
                        if "remote_action_id" in metadata:
                            metadata.pop("remote_action_id", None)

                        metadata_file.write_text(json.dumps(metadata, indent=2))
                        details["sub_actions_moved"].append(action_id)
                    except json.JSONDecodeError:
                        details["sub_actions_failed"].append(action_id)

                # Clear .action_id file
                action_id_file = action_dir / ".action_id"
                if action_id_file.exists():
                    action_id_file.unlink()

    # Clear stale remote_action_ids from agent's sub_actions list in metadata.json
    # (they belonged to the source env and are now invalid)
    if agent_metadata_file.exists():
        try:
            agent_meta = json.loads(agent_metadata_file.read_text())
            updated_sub_actions = []
            for sa in agent_meta.get("sub_actions", []):
                sa = dict(sa)  # make a copy
                # Only track/clear the REMOTE registration ID.
                # The "action_id" field in sub_actions entries is the local
                # directory name (e.g. "get-vendor-pricing") and must NOT be
                # cleared — it is needed to locate the sub-action workspace.
                old_remote_id = sa.get("remote_action_id")
                if old_remote_id:
                    sa["previous_remote_action_id"] = old_remote_id
                sa.pop("remote_action_id", None)
                updated_sub_actions.append(sa)
            agent_meta["sub_actions"] = updated_sub_actions
            agent_meta["updated_at"] = datetime.now().isoformat()
            agent_metadata_file.write_text(json.dumps(agent_meta, indent=2))
        except (json.JSONDecodeError, OSError):
            pass

    # Update agent's WDL to mark action_ids as needing update
    widdle_file = dest_path / "widdle.json"
    if widdle_file.exists():
        try:
            widdle = json.loads(widdle_file.read_text())
            updated = False

            for block in widdle:
                if isinstance(block, dict):
                    # Look for PROMPT_AND_TOOLS_AGENT with action_ids
                    if block.get("operation") == "PROMPT_AND_TOOLS_AGENT":
                        if "action_ids" in block:
                            # Add comment/marker that these need updating
                            block["_needs_action_id_update"] = True
                            block["_original_action_ids"] = block["action_ids"]
                            # Clear the action_ids - they need to be re-registered
                            block["action_ids"] = []
                            updated = True

            if updated:
                widdle_file.write_text(json.dumps(widdle, indent=2))
        except json.JSONDecodeError:
            pass

    # ── Update env.json registrations ──────────────────────────────────────
    # Register agent in destination env.json
    _update_env_json(dest_env, agent_id, "add")
    # Remove from source env.json only when it's a real move (not a copy)
    if not copy_mode:
        _update_env_json(source_env, agent_id, "remove")

    return (
        True,
        f"{operation} agent '{agent_id}' with {len(details['sub_actions_moved'])} sub-actions from {source_env} to {dest_env}",
        details,
    )


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


def print_agent_summary(agent_path: Path) -> None:
    """Print a summary of an agent."""
    widdle_file = agent_path / "widdle.json"
    metadata_file = agent_path / "metadata.json"
    agent_json = agent_path / "agent.json"

    agent_name = agent_path.name
    remote_id = None

    # Primary: unified metadata.json
    if metadata_file.exists():
        try:
            data = json.loads(metadata_file.read_text())
            agent_name = data.get("title") or data.get("name", agent_path.name)
            remote_id = data.get("action_id") or data.get("remote_action_id")
        except json.JSONDecodeError:
            pass

    # Legacy fallback: agent.json
    if not remote_id and agent_json.exists():
        try:
            data = json.loads(agent_json.read_text())
            if not remote_id:
                agent_name = data.get("name", agent_name)
            remote_id = data.get("remote_action_id")
        except json.JSONDecodeError:
            pass

    print(f"   🤖 Name: {agent_name}")
    print(f"   🆔 Remote ID: {remote_id or 'Not linked'}")
    print(f"   📁 Path: {agent_path}")

    if widdle_file.exists():
        try:
            widdle = json.loads(widdle_file.read_text())
            # Find PROMPT_AND_TOOLS_AGENT block
            for block in widdle:
                if isinstance(block, dict) and block.get("operation") == "PROMPT_AND_TOOLS_AGENT":
                    action_ids = block.get("action_ids", [])
                    print(f"   🔗 Linked action_ids: {len(action_ids)}")
                    break
        except json.JSONDecodeError:
            pass

    # List sub-actions
    actions_dir = agent_path / "actions"
    if actions_dir.exists():
        sub_actions = [
            d for d in actions_dir.iterdir() if d.is_dir() and (d / "widdle.json").exists()
        ]
        print(f"   📦 Sub-actions: {len(sub_actions)}")
        for action_dir in sub_actions:
            metadata = _load_metadata(action_dir)
            action_id = metadata.get("action_id", "Not linked")
            print(f"      • {action_dir.name} ({action_id})")


def interactive_confirmation(
    item_id: str,
    source_env: str,
    dest_env: str,
    source_path: Path,
    dest_path: Path,
    copy_mode: bool,
    is_agent: bool = False,
    sub_actions: list[dict] | None = None,
) -> bool:
    """
    Request explicit user confirmation.

    This CANNOT be bypassed by agents - requires real user input.
    """
    operation = "COPY" if copy_mode else "MOVE"
    item_type = "AGENT" if is_agent else "ACTION"

    print()
    print("=" * 70)
    print(f"🚚 {item_type} {operation} CONFIRMATION")
    print("=" * 70)
    print()
    print("⚠️  This operation requires YOUR EXPLICIT CONFIRMATION.")
    print("   AI agents cannot automatically approve this operation.")
    print()
    print(f"📦 {item_type.title()}: {item_id}")
    print(f"📤 From:   {source_env}")
    print(f"📥 To:     {dest_env}")
    print()

    if is_agent:
        print("Source agent details:")
        print_agent_summary(source_path)
        print()

        if sub_actions:
            print(f"⚠️  This will also {operation.lower()} {len(sub_actions)} sub-actions:")
            for sa in sub_actions:
                print(f"      • {sa['id']} ({sa.get('old_action_id', 'Not linked')})")
            print()
            print("   Sub-action remote IDs will be CLEARED - they need re-registration")
            print("   in the new environment.")
    else:
        print("Source action details:")
        print_action_summary(source_path)

    print()

    if dest_path.exists():
        print("⚠️  WARNING: Destination already exists and will be OVERWRITTEN!")
        print()

    print("-" * 70)
    print()
    print(f"To confirm, type the FULL {item_type.lower()} ID exactly as shown above:")
    print(f"   → {item_id}")
    print()

    try:
        user_input = input(f"Confirm {item_type.lower()} ID: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n❌ Operation cancelled.")
        return False

    if user_input != item_id:
        print()
        print(f"❌ Confirmation failed. You typed: '{user_input}'")
        print(f"   Expected: '{item_id}'")
        print()
        print("Operation cancelled for safety.")
        return False

    print()
    print("✅ Confirmation received.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Move or copy actions/agents between environments",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
WORKSPACE ENVIRONMENTS:
    Environments are LOCAL workspace directories in workspaces/ folder.
    Examples: clienta-prod, clienta-staging, clientb-prod, clientc-staging

    Use --list-envs to see all available local workspaces.

Examples:
    # List available local workspace environments
    python cli/move_action.py --list-envs

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

    # List actions/agents in a workspace
    python cli/move_action.py --list-actions --env clienta-prod
    python cli/move_action.py --list-agents --env clientb-staging

⚠️  IMPORTANT:
    - This script requires explicit user confirmation (type the full ID to confirm)
    - AI agents cannot automatically approve the move operation
    - Both source and destination workspaces must exist locally

📝 AGENT MOVE WORKFLOW:
    When moving an agent, all remote action_ids are cleared because
    sub-actions need to be re-registered in the new environment.

    After moving an agent, follow these steps:
    1. Switch to dest env: python cli/workspace.py env use <dest-workspace>
    2. For each sub-action:
       a. Save draft: python cli/save_wdl_draft.py --workflow-id <action-id>
       b. Publish: python cli/publish_wdl_action.py --workflow-id <action-id>
       c. Enable tool mode: python cli/deployment_rules.py <action-id> --enable-tool-mode
    3. Update agent WDL with new action_ids
    4. Save agent draft: python cli/save_wdl_draft.py --workflow-id <agent-id>
    5. Publish agent: python cli/publish_wdl_action.py --workflow-id <agent-id>
""",
    )

    parser.add_argument("item_id", nargs="?", help="The action or agent ID to move")
    parser.add_argument(
        "--agent",
        "-a",
        action="store_true",
        help="Treat item_id as an agent (move agent with all sub-actions)",
    )
    parser.add_argument(
        "--from",
        "-f",
        dest="source_env",
        help="Source workspace environment (e.g., client-staging, client-prod)",
    )
    parser.add_argument(
        "--to",
        "-t",
        dest="dest_env",
        help="Destination workspace environment (e.g., client-prod, other-client-prod)",
    )
    parser.add_argument("--source-agent", help="Source agent (if action is within an agent)")
    parser.add_argument("--dest-agent", help="Destination agent (move action into an agent)")
    parser.add_argument(
        "--copy", "-c", action="store_true", help="Copy instead of move (keep original)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force overwrite if destination exists (still requires confirmation)",
    )
    parser.add_argument(
        "--keep-id",
        action="store_true",
        help="Keep the existing remote action_id instead of clearing it (useful for same-client moves)",
    )
    parser.add_argument(
        "--list-actions", action="store_true", help="List actions in an environment"
    )
    parser.add_argument("--list-agents", action="store_true", help="List agents in an environment")
    parser.add_argument("--list-envs", action="store_true", help="List available environments")
    parser.add_argument("--env", "-e", help="Environment for --list-actions/--list-agents")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate the move/copy without actually modifying any files",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed debug information (source/dest path resolution, metadata updates)",
    )

    args = parser.parse_args()

    global _verbose
    _verbose = args.verbose
    if _verbose:
        print("[VERBOSE] Verbose mode enabled", file=sys.stderr)

    # List environments
    if args.list_envs:
        print()
        print("📂 Available Environments:")
        print("-" * 40)
        for env in list_environments():
            print(f"   • {env}")
        print()
        return 0

    # List agents in environment
    if args.list_agents:
        if not args.env:
            print("❌ Error: --env required with --list-agents")
            print("   Usage: python cli/move_action.py --list-agents --env staging")
            sys.exit(1)

        print()
        print(f"🤖 Agents in '{args.env}':")
        print("-" * 60)

        agents = list_agents_in_env(args.env)
        if not agents:
            print("   No agents found.")
        else:
            for agent in agents:
                print(f"\n   • {agent['id']}")
                print(f"     Name: {agent['name']}")
                if agent.get("remote_action_id"):
                    print(f"     Remote ID: {agent['remote_action_id']}")
                print(f"     Sub-actions: {len(agent['sub_actions'])}")
                for sa in agent["sub_actions"]:
                    aid = sa.get("action_id", "Not linked")
                    print(f"       - {sa['id']} ({aid})")
        print()
        return 0

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
                    aid = action.get("action_id", "Not linked")
                    print(f"   • {action['id']} ({aid})")
                    print(f"     Title: {action['title']}")

            if agent_actions:
                # Group by agent
                agents_grouped: dict[str, list[Any]] = {}
                for action in agent_actions:
                    agent_key = str(action["agent"])
                    if agent_key not in agents_grouped:
                        agents_grouped[agent_key] = []
                    agents_grouped[agent_key].append(action)

                for agent_name, agent_acts in agents_grouped.items():
                    print(f"\n   Agent: {agent_name}")
                    for action in agent_acts:
                        aid = action.get("action_id", "Not linked")
                        print(f"   • {action['id']} ({aid})")
                        print(f"     Title: {action['title']}")
        print()
        return 0

    # Validate move/copy arguments
    if not args.item_id:
        parser.print_help()
        sys.exit(1)

    if not args.source_env or not args.dest_env:
        print("❌ Error: Both --from and --to are required")
        print("   Usage: python cli/move_action.py <id> --from <source> --to <dest>")
        sys.exit(1)

    if args.source_env == args.dest_env and not args.dest_agent and not args.source_agent:
        print("❌ Error: Source and destination are the same")
        print("   Use --dest-agent to move action into an agent within the same environment")
        sys.exit(1)

    # Check destination environment exists
    dest_env_path = get_workspaces_root() / args.dest_env
    if not dest_env_path.exists():
        print(f"❌ Destination environment '{args.dest_env}' not found")
        print()
        print("Available environments:")
        for env in list_environments():
            print(f"   • {env}")
        sys.exit(1)

    # Handle agent move
    if args.agent:
        # Find source agent
        source_path = find_agent_path(args.source_env, args.item_id)
        if not source_path:
            print(f"❌ Agent '{args.item_id}' not found in environment '{args.source_env}'")
            print()
            print("Available agents:")
            agents = list_agents_in_env(args.source_env)
            for agent in agents[:10]:
                print(f"   • {agent['id']} ({len(agent['sub_actions'])} sub-actions)")
            sys.exit(1)

        dest_path = get_agent_destination_path(args.dest_env, args.item_id)

        # Check if destination exists
        if dest_path.exists() and not args.force:
            print(f"❌ Agent already exists at destination: {dest_path}")
            print("   Use --force to overwrite (confirmation still required)")
            sys.exit(1)

        # Get sub-actions for confirmation display
        sub_actions = []
        actions_dir = source_path / "actions"
        if actions_dir.exists():
            for action_dir in actions_dir.iterdir():
                if action_dir.is_dir() and (action_dir / "widdle.json").exists():
                    metadata = _load_metadata(action_dir)
                    sub_actions.append(
                        {"id": action_dir.name, "old_action_id": metadata.get("action_id")}
                    )

        # In dry-run mode, skip interactive confirmation entirely
        if args.dry_run:
            print()
            print("--- DRY RUN MODE ---")
            print("No files will be moved or modified. Showing what would happen:")
            print()
        else:
            # Request explicit confirmation
            confirmed = interactive_confirmation(
                args.item_id,
                args.source_env,
                args.dest_env,
                source_path,
                dest_path,
                args.copy,
                is_agent=True,
                sub_actions=sub_actions,
            )
            if not confirmed:
                sys.exit(1)

        # Perform the move (or simulate it)
        success, message, details = move_agent(
            args.item_id,
            args.source_env,
            args.dest_env,
            args.copy,
            args.force,
            dry_run=args.dry_run,
        )

        if success:
            print()
            print("=" * 70)
            print("✅ " + message)
            print("=" * 70)
            print()
            print("📍 New location:", details["agent_path"])
            print()

            if details["sub_actions_moved"]:
                print("📦 Sub-actions moved:")
                for sa in details["sub_actions_moved"]:
                    print(f"   • {sa}")

            if details["action_id_mapping"]:
                print()
                print("🔗 Original action_id mapping (for reference):")
                for action_id, mapping in details["action_id_mapping"].items():
                    print(f"   • {action_id}: {mapping['old_action_id']}")

            print()
            print("=" * 70)
            print("📝 NEXT STEPS (Required to complete the move):")
            print("=" * 70)
            print()
            print("1. Switch to the destination environment:")
            print(f"   python cli/workspace.py env use {args.dest_env}")
            print()
            print("2. For EACH sub-action, register it in the new environment:")
            for sa in details["sub_actions_moved"]:
                print(f"   # {sa}")
                print(f"   python cli/save_wdl_draft.py --workflow-id {sa} --agent {args.item_id}")
                print(f"   python cli/publish_wdl_action.py --workflow-id {sa}")
                print(f"   python cli/deployment_rules.py {sa} --enable-tool-mode")
                print()
            print("3. Update the agent's WDL with the new action_ids:")
            print(
                "   # Edit widdle.json and add the new action_ids to the PROMPT_AND_TOOLS_AGENT block"
            )
            print()
            print("4. Register and publish the agent:")
            print(f"   python cli/save_wdl_draft.py --workflow-id {args.item_id}")
            print(f"   python cli/publish_wdl_action.py --workflow-id {args.item_id}")
            print()
        else:
            print(f"❌ {message}")
            sys.exit(1)

    else:
        # Handle action move
        # Find source action
        source_path = find_action_path(args.source_env, args.item_id, args.source_agent)
        if not source_path:
            print(f"❌ Action '{args.item_id}' not found in environment '{args.source_env}'")
            print()
            print("Available actions:")
            actions = list_actions_in_env(args.source_env)
            for action in actions[:10]:
                loc = f" (in {action['agent']})" if action["agent"] else ""
                print(f"   • {action['id']}{loc}")
            if len(actions) > 10:
                print(f"   ... and {len(actions) - 10} more")
            sys.exit(1)

        # Get destination path
        dest_path = get_destination_path(args.dest_env, args.item_id, args.dest_agent)

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

        # In dry-run mode, skip interactive confirmation entirely
        if args.dry_run:
            print()
            print("--- DRY RUN MODE ---")
            print("No files will be moved or modified. Showing what would happen:")
            print()
        else:
            # Request explicit confirmation
            confirmed = interactive_confirmation(
                args.item_id, args.source_env, args.dest_env, source_path, dest_path, args.copy
            )
            if not confirmed:
                sys.exit(1)

        # Clear remote ID by default when crossing environments; keep it when
        # the source and dest environments are the same (e.g. moving into an agent).
        clear_id = not args.keep_id and (args.source_env != args.dest_env)

        # Perform the move/copy (or simulate it)
        success, message, _ = move_action(
            args.item_id,
            args.source_env,
            args.dest_env,
            args.source_agent,
            args.dest_agent,
            args.copy,
            args.force,
            clear_remote_id=clear_id,
            dry_run=args.dry_run,
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
            print(f"   2. Test the action: python cli/test_runner.py {args.item_id}")
            print(
                f"   3. Save draft if needed: python cli/save_wdl_draft.py --workflow-id {args.item_id}"
            )
            print()
        else:
            print(f"❌ {message}")
            sys.exit(1)

    return 0


if __name__ == "__main__":
    main()
