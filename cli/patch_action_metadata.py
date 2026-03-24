#!/usr/bin/env python3
"""
Patch Action Metadata - Update action title, description, and statement on remote.

Reads metadata from local metadata.json and pushes to the cloud.

Commands:
    patch_action_metadata.py <action>                    Sync all metadata fields
    patch_action_metadata.py <action> --title "New Title" Patch only the title
    patch_action_metadata.py <action> --description "..." Patch only the description
    patch_action_metadata.py <action> --statement "..."   Patch only the statement
    patch_action_metadata.py --agent <agent>              Patch all sub-actions
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.api_client import get_api_client_for_env
from cli.wdl_common.workspace_manager import get_workspace_manager

_verbose = False


def _vprint(*args: object) -> None:
    if _verbose:
        print("[VERBOSE]", *args)


def _resolve_action(workflow_id: str) -> dict | None:
    """
    Resolve a workflow_id to its workspace info (path, metadata, action_id).

    Returns:
        action_info dict with 'path', 'metadata', etc., or None
    """
    manager = get_workspace_manager()
    _vprint(f"Resolving workspace for: {workflow_id} (env={manager.active_env})")
    action_info = manager.find_action(workflow_id)

    if not action_info:
        _vprint(f"Workspace not found for: {workflow_id}")
        return None

    _vprint(f"Action ID resolved: {action_info.get('metadata', {}).get('action_id')}")
    return action_info


def _load_local_metadata(workspace_path: Path) -> dict | None:
    """Load local metadata.json from a workspace path."""
    meta_path = workspace_path / "metadata.json"
    try:
        return json.loads(meta_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _save_local_metadata(
    workspace_path: Path, *, update_synced_at: bool = True, **fields: str
) -> None:
    """Update local metadata.json with patched fields."""
    meta_path = workspace_path / "metadata.json"
    try:
        metadata = json.loads(meta_path.read_text())
        for key, value in fields.items():
            if value is not None:
                metadata[key] = value

        if update_synced_at:
            metadata["metadata_synced_at"] = datetime.now().isoformat()

        _vprint(f"Writing metadata updates to: {meta_path}")
        meta_path.write_text(json.dumps(metadata, indent=2))
    except (json.JSONDecodeError, OSError) as e:
        _vprint(f"Failed to update local metadata: {e}")


def _patch_field(
    client: Any,
    field_name: str,
    value: str,
    action_id: str,
    dry_run: bool,
    draft_id: str | None = None,
) -> tuple[str, bool, str]:
    """
    Patch a single metadata field. Returns (field_name, success, message).
    """
    if dry_run:
        preview = value if len(value) <= 80 else f"{value[:80]}..."
        print(f"\n  [DRY-RUN] Would patch {field_name}: {preview}")
        return field_name, True, "[DRY-RUN] Would update"

    truncated = value[:50] + "..." if len(value) > 50 else value

    if field_name == "title":
        _vprint(f"API call: update_title({action_id}, {value!r})")
        success, msg = client.update_title(action_id, value)
    elif field_name == "statement":
        _vprint(f"API call: update_statement({action_id}, {truncated!r}, draft_id={draft_id!r})")
        success, msg = client.update_statement(action_id, value, draft_id=draft_id or "")
    elif field_name == "description":
        _vprint(f"API call: update_description({action_id}, {truncated!r})")
        success, msg = client.update_description(action_id, value)
    else:
        return field_name, False, f"Unknown field: {field_name}"

    icon = "ok" if success else "FAIL"
    preview = value if len(value) <= 80 else f"{value[:80]}..."
    print(f"\n  [{icon}] {field_name.capitalize()}: {preview}")
    if not success:
        print(f"      Error: {msg}")

    return field_name, success, msg


def _action_info_from_workspace(
    workspace_path: Path,
    *,
    env_name: str | None = None,
    agent_name: str | None = None,
    metadata: dict | None = None,
) -> dict | None:
    """Build action info for an already-resolved workspace path."""
    if not workspace_path.exists():
        return None

    action_info: dict = {
        "path": workspace_path,
        "env_name": env_name,
        "agent_name": agent_name,
        "action_id": workspace_path.name,
    }
    if metadata is None:
        metadata = _load_local_metadata(workspace_path)
    if metadata is not None:
        action_info["metadata"] = metadata
    return action_info


def _cmd_patch_action(
    args: argparse.Namespace,
    action_ref: str,
    action_info: dict | None = None,
) -> int:
    """Patch action metadata on remote for a resolved action workspace."""
    dry_run = args.dry_run

    if action_info is None:
        action_info = _resolve_action(action_ref)
    if not action_info:
        print(f"  No workspace found for: {action_ref}")
        return 1

    action_id = action_info.get("metadata", {}).get("action_id")
    if not action_id:
        print(f"  No remote action ID found for: {action_ref}")
        print("   The action may not be published yet.")
        return 1

    workspace_path = action_info["path"]
    local_meta = action_info.get("metadata")
    if local_meta is None:
        local_meta = _load_local_metadata(workspace_path)
    if local_meta is None:
        print(f"  Could not load metadata.json for: {action_ref}")
        return 1

    # Determine which fields to patch.
    # If explicit flags are provided, use those. Otherwise, sync all non-empty fields.
    has_explicit_flags = (
        args.title is not None or args.description is not None or args.statement is not None
    )

    title = (
        args.title
        if args.title is not None
        else (None if has_explicit_flags else local_meta.get("title"))
    )
    description = (
        args.description
        if args.description is not None
        else (None if has_explicit_flags else local_meta.get("description"))
    )
    statement = (
        args.statement
        if args.statement is not None
        else (None if has_explicit_flags else local_meta.get("statement"))
    )

    if not (title or description or statement):
        print(f"  No metadata fields to patch for: {action_ref}")
        print("   Set title, description, or statement in metadata.json")
        print("   Or use --title, --description, --statement flags")
        return 1

    _vprint("Initialising API client for active environment")
    client = get_api_client_for_env()

    print(f"\n{'=' * 60}")
    print(f"  PATCH METADATA: {action_ref}")
    print(f"{'=' * 60}")
    print(f"  Action ID: {action_id}")

    results = []

    # Patch title
    if title:
        results.append(_patch_field(client, "title", title, action_id, dry_run))

    # Patch statement BEFORE description.
    # update_description also overwrites statement on the draft, so statement
    # must be applied first to avoid the workflow service 500 on stale draft state.
    if statement:
        draft_id = ""
        if not dry_run:
            success_get, data, _ = client.get_action(action_id)
            if success_get and data:
                draft_id = data.get("id", data.get("draft_id", ""))
                _vprint(f"Draft ID for statement update: {draft_id!r}")
        results.append(
            _patch_field(client, "statement", statement, action_id, dry_run, draft_id=draft_id)
        )

    # Patch description
    if description:
        results.append(_patch_field(client, "description", description, action_id, dry_run))

    # Summary
    succeeded = sum(1 for _, s, _ in results if s)
    failed = sum(1 for _, s, _ in results if not s)

    # Update local metadata only for fields that were confirmed remotely.
    if not dry_run:
        requested_fields = {
            name: value
            for name, value in [
                ("title", title),
                ("description", description),
                ("statement", statement),
            ]
            if value
        }
        successful_fields = {
            name: requested_fields[name]
            for name, success, _ in results
            if success and name in requested_fields
        }
        if successful_fields:
            _save_local_metadata(
                workspace_path,
                update_synced_at=(failed == 0),
                **successful_fields,
            )

    print(f"\n{'=' * 60}")
    if failed == 0:
        print(f"  Patched {succeeded} field(s) successfully")
    else:
        print(f"  {succeeded} succeeded, {failed} failed")
    print(f"{'=' * 60}")

    return 1 if failed > 0 else 0


def cmd_patch(args: argparse.Namespace) -> int:
    """Patch action metadata on remote."""
    return _cmd_patch_action(args, args.action)


def cmd_patch_agent(args: argparse.Namespace) -> int:
    """Patch metadata for all sub-actions of an agent."""
    manager = get_workspace_manager()
    agent = manager.get_agent(args.agent, manager.active_env)

    if not agent:
        print(f"  Agent not found: {args.agent}")
        return 1

    print(f"\n  Patching metadata for agent: {args.agent}")
    print(f"  Path: {agent.get('path')}")

    sub_actions = agent.get("sub_actions", [])
    if not sub_actions:
        print("  No sub-actions found")
        return 0

    total_exit = 0
    for sa in sub_actions:
        sa_id = sa.get("action_id") or sa.get("id")
        if not sa_id:
            continue

        print(f"\n{'~' * 60}")
        print(f"  Sub-action: {sa_id}")

        sub_action_path = Path(agent["path"]) / "actions" / sa_id
        sub_action_info = _action_info_from_workspace(
            sub_action_path,
            env_name=agent.get("env_name"),
            agent_name=args.agent,
        )

        patch_args = argparse.Namespace(
            action=sa_id,
            title=args.title,
            description=args.description,
            statement=args.statement,
            dry_run=args.dry_run,
        )
        exit_code = _cmd_patch_action(patch_args, sa_id, action_info=sub_action_info)
        if exit_code != 0:
            total_exit = 1

    # Also patch the agent itself if it has metadata
    agent_path = Path(agent.get("path", ""))
    agent_meta = agent_path / "metadata.json"
    if agent_meta.exists():
        agent_id = agent.get("agent_name") or args.agent
        print(f"\n{'~' * 60}")
        print(f"  Agent: {agent_id}")
        patch_args = argparse.Namespace(
            action=agent_id,
            title=args.title,
            description=args.description,
            statement=args.statement,
            dry_run=args.dry_run,
        )
        agent_action_info = _action_info_from_workspace(
            agent_path,
            env_name=agent.get("env_name"),
            metadata=agent,
        )
        exit_code = _cmd_patch_action(patch_args, agent_id, action_info=agent_action_info)
        if exit_code != 0:
            total_exit = 1

    return total_exit


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Patch action metadata (title, description, statement) on remote",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Sync all metadata from local metadata.json to remote
  %(prog)s resolve-city

  # Patch only specific fields
  %(prog)s resolve-city --title "Resolve City"
  %(prog)s resolve-city --description "Resolves city names to airport codes"
  %(prog)s resolve-city --statement "Use when user mentions a city to resolve"

  # Patch all sub-actions of an agent
  %(prog)s --agent flight-price-agent

  # Dry run
  %(prog)s resolve-city --dry-run
        """,
    )

    # Action or agent mode
    parser.add_argument("action", nargs="?", help="Action/workflow ID to patch")
    parser.add_argument("--agent", "-a", help="Patch all sub-actions of an agent")

    # Optional field overrides
    parser.add_argument("--title", "-t", help="New title (overrides metadata.json)")
    parser.add_argument("--description", "-d", help="New description (overrides metadata.json)")
    parser.add_argument(
        "--statement", "-s", help="New statement/selection criteria (overrides metadata.json)"
    )

    # Common options
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate the operation without making any remote API calls",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed debug information",
    )

    args = parser.parse_args()

    global _verbose
    _verbose = args.verbose
    if _verbose:
        print("[VERBOSE] Verbose mode enabled", file=sys.stderr)

    if args.agent:
        return cmd_patch_agent(args)
    elif args.action:
        return cmd_patch(args)
    else:
        parser.print_help()
        print("\n  Error: Must provide an action or --agent", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
