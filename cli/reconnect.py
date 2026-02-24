#!/usr/bin/env python3
"""
Reconnect Command - Link a workspace to an existing remote action.

Use this when:
- Workspace lost its action_id
- You want to link a local workspace to an existing action
- You need to recover from metadata corruption

Usage:
    python cli/reconnect.py my-workflow abc-123-action-id
    python cli/reconnect.py my-workflow --search  # Search by title
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.api_client import get_api_client_for_env
from cli.wdl_common.metadata_manager import MetadataManager
from cli.wdl_common.workspace_manager import get_workspace_manager

_verbose = False


def _vprint(*args: object) -> None:
    if _verbose:
        print("[VERBOSE]", *args)


def find_workspace(workflow_id: str) -> Path:
    """Find workspace by workflow_id, auto-detecting agent vs standalone."""
    manager = get_workspace_manager()
    _vprint(f"Searching for workspace: {workflow_id} (env={manager.active_env})")
    action_info = manager.find_action(workflow_id)

    if action_info:
        _vprint(f"Found workspace at: {action_info['path']}")
        return Path(action_info["path"])

    raise ValueError(f"Workspace not found: {workflow_id}")


def search_action_by_title(title: str) -> dict | None:
    """
    Search for action by title on remote.

    Args:
        title: Title to search for

    Returns:
        Action dict if found, None otherwise
    """
    client = get_api_client_for_env()  # Uses active environment
    success, tools, msg = client.list_tools()

    if not success or not tools:
        return None

    # Exact match first
    for tool in tools:
        if tool.get("title") == title:
            return tool

    # Partial match
    title_lower = title.lower()
    for tool in tools:
        tool_title = tool.get("title", "").lower()
        if title_lower in tool_title or tool_title in title_lower:
            return tool

    return None


def recover_action_by_title(workspace: Path) -> str | None:
    """
    Attempt to recover action_id by searching remote by title.

    Args:
        workspace: Workspace path

    Returns:
        Recovered action_id or None
    """
    meta_manager = MetadataManager(workspace)
    metadata = meta_manager.load()
    title = metadata.title

    if not title:
        print("❌ No title in metadata, cannot search by title")
        return None

    print(f"🔍 Searching for action with title: {title}")
    action = search_action_by_title(title)

    if action:
        action_id = action.get("action_id") or action.get("id")
        print(f"✅ Found matching action: {action_id}")
        print(f"   Title: {action.get('title', 'N/A')}")
        return action_id

    print("❌ No matching action found")
    return None


def reconnect_workspace(
    workflow_id: str,
    action_id: str | None = None,
    search_by_title: bool = False,
    dry_run: bool = False,
) -> bool:
    """
    Reconnect a workspace to an existing remote action.

    Args:
        workflow_id: Workflow ID
        action_id: Action ID to link to (optional if search_by_title)
        search_by_title: Search for action by title

    Returns:
        True if successful
    """
    print("\n" + "=" * 80)
    if dry_run:
        print("🔍 [DRY-RUN] RECONNECT WORKSPACE (simulating — no changes will be made)")
    else:
        print("🔗 RECONNECT WORKSPACE")
    print("=" * 80)

    # Find workspace
    try:
        workspace = find_workspace(workflow_id)
    except ValueError as e:
        print(f"\n❌ {e}")
        return False

    print(f"📁 Workspace: {workspace}")
    _vprint(f"Loading metadata from: {workspace / 'metadata.json'}")
    meta_manager = MetadataManager(workspace)
    meta_manager.load()

    # Check current state
    current_action_id = meta_manager.get_action_id()
    if current_action_id:
        print(f"⚠️  Workspace already linked to: {current_action_id}")
        if not dry_run:
            confirm = input("   Replace with new action? (yes/no): ").strip().lower()
            if confirm != "yes":
                print("❌ Cancelled")
                return False
        else:
            print("   [DRY-RUN] Would prompt for confirmation to replace — skipping")

    # If search by title, try to find action
    if search_by_title or not action_id:
        recovered_id = recover_action_by_title(workspace)
        if recovered_id:
            action_id = recovered_id
        elif not action_id:
            print("❌ No action_id provided and could not find by title")
            return False

    # Verify action exists on remote (read-only, always safe)
    print(f"\n🔍 Verifying action: {action_id}")
    _vprint("Initialising API client for active environment")
    client = get_api_client_for_env()  # Uses active environment
    _vprint(f"API call: get_action({action_id})")
    success, action_data, msg = client.get_action(action_id)

    if not success:
        print(f"❌ Action not found on remote: {msg}")
        return False

    assert action_data is not None  # guaranteed by success=True
    print(f"   ✅ Action exists: {action_data.get('title', 'N/A')}")

    if dry_run:
        # Check versions (read-only)
        v_success, versions, _v_msg = client.list_versions(action_id)
        version_count = len(versions) if v_success and versions else 0
        print("\n🔍 [DRY-RUN] Would link workspace to action:")
        print(f"   Workflow ID  : {workflow_id}")
        print(f"   Action ID    : {action_id}")
        print(f"   Title        : {action_data.get('title', 'N/A')}")
        print(f"   Versions     : {version_count} would be synced")
        print("\n   ✅ Validation passed — run without --dry-run to apply changes")
        print("=" * 80)
        return True

    # Link workspace to action
    meta_manager.set_action_id(action_id)

    # Sync versions from remote
    print("\n📥 Syncing versions from remote...")
    success, versions, msg = client.list_versions(action_id)
    if success and versions:
        meta_manager.sync_versions_from_api(versions)
        print(f"   ✅ Synced {len(versions)} versions")
    else:
        print(f"   ⚠️  Could not sync versions: {msg}")

    print("\n" + "=" * 80)
    print("✅ WORKSPACE RECONNECTED")
    print("=" * 80)
    print(f"   Workflow ID: {workflow_id}")
    print(f"   Action ID: {action_id}")
    print(f"   Title: {action_data.get('title', 'N/A')}")
    print("\n   Next steps:")
    print(f"   - Check status: python cli/status.py {workflow_id}")
    print(f"   - Test: python cli/test.py {workflow_id}")
    print("=" * 80)

    return True


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Reconnect workspace to existing remote action",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Reconnect with known action ID
  python cli/reconnect.py my-workflow abc-123-action-id

  # Search for action by title
  python cli/reconnect.py my-workflow --search
        """,
    )

    parser.add_argument("workflow_id", help="Workflow ID")
    parser.add_argument(
        "action_id",
        nargs="?",
        help="Action ID to connect to (optional if using --search)",
    )
    parser.add_argument(
        "--search",
        "-s",
        action="store_true",
        help="Search for action by title",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate the reconnect without writing any metadata or syncing versions",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed debug information (workspace resolution, API calls, metadata updates)",
    )

    args = parser.parse_args()

    global _verbose
    _verbose = args.verbose
    if _verbose:
        print("[VERBOSE] Verbose mode enabled", file=sys.stderr)

    if not args.action_id and not args.search:
        parser.error("Either action_id or --search is required")

    success = reconnect_workspace(
        workflow_id=args.workflow_id,
        action_id=args.action_id,
        search_by_title=args.search,
        dry_run=args.dry_run,
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
