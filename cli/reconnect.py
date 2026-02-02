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
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.metadata_manager import MetadataManager
from cli.wdl_common.workspace_manager import WorkspaceManager
from cli.wdl_common.api_client import AdoptAPIClient


def find_workspace(workflow_id: str) -> Path:
    """Find workspace by workflow_id, auto-detecting agent vs standalone."""
    manager = WorkspaceManager(use_agents=True)
    success, data, msg = manager.load_workspace(workflow_id)
    
    if success:
        return data["workspace_path"]
    
    manager = WorkspaceManager(use_agents=False)
    success, data, msg = manager.load_workspace(workflow_id)
    
    if success:
        return data["workspace_path"]
    
    raise ValueError(f"Workspace not found: {workflow_id}")


def search_action_by_title(title: str) -> Optional[dict]:
    """
    Search for action by title on remote.
    
    Args:
        title: Title to search for
        
    Returns:
        Action dict if found, None otherwise
    """
    client = AdoptAPIClient()
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


def recover_action_by_title(workspace: Path) -> Optional[str]:
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
    action_id: Optional[str] = None,
    search_by_title: bool = False,
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
    print("🔗 RECONNECT WORKSPACE")
    print("=" * 80)
    
    # Find workspace
    try:
        workspace = find_workspace(workflow_id)
    except ValueError as e:
        print(f"\n❌ {e}")
        return False
    
    print(f"📁 Workspace: {workspace}")
    
    meta_manager = MetadataManager(workspace)
    metadata = meta_manager.load()
    
    # Check current state
    current_action_id = meta_manager.get_action_id()
    if current_action_id:
        print(f"⚠️  Workspace already linked to: {current_action_id}")
        confirm = input("   Replace with new action? (yes/no): ").strip().lower()
        if confirm != "yes":
            print("❌ Cancelled")
            return False
    
    # If search by title, try to find action
    if search_by_title or not action_id:
        recovered_id = recover_action_by_title(workspace)
        if recovered_id:
            action_id = recovered_id
        elif not action_id:
            print("❌ No action_id provided and could not find by title")
            return False
    
    # Verify action exists on remote
    print(f"\n🔍 Verifying action: {action_id}")
    client = AdoptAPIClient()
    success, action_data, msg = client.get_action(action_id)
    
    if not success:
        print(f"❌ Action not found on remote: {msg}")
        return False
    
    print(f"   ✅ Action exists: {action_data.get('title', 'N/A')}")
    
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
        "--search", "-s",
        action="store_true",
        help="Search for action by title",
    )
    
    args = parser.parse_args()
    
    if not args.action_id and not args.search:
        parser.error("Either action_id or --search is required")
    
    success = reconnect_workspace(
        workflow_id=args.workflow_id,
        action_id=args.action_id,
        search_by_title=args.search,
    )
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()



