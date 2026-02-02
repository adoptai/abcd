#!/usr/bin/env python3
"""
List all versions of a WDL action.

Shows version history with status, timestamps, and which version is live.

TRANSPARENT BEHAVIOR (default):
- Action ID: Auto-detected from workspace metadata
- Agent/Standalone: Auto-detected from workspace location

USE FLAGS ONLY when automatic behavior doesn't work.
"""

import argparse
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.api_client import AdoptAPIClient, get_api_client_for_env
from cli.wdl_common.metadata_manager import MetadataManager
from cli.wdl_common.version_tracker import (
    sync_versions_from_api,
    read_metadata,
)
from cli.wdl_common.workspace_manager import WorkspaceManager


def list_wdl_versions(
    action_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
    standalone: bool = False,
) -> bool:
    """
    List all versions of an action and sync to metadata.json.

    TRANSPARENT: Can be called with just workflow_id - action_id auto-detected.

    Args:
        action_id: The action ID (optional - auto-detected from metadata)
        workflow_id: Workflow ID to find workspace (recommended)
        standalone: Use standalone mode (override auto-detection)

    Returns:
        True if successful
    """
    print("\n" + "=" * 80)
    print("📋 WDL ACTION VERSIONS")
    print("=" * 80)

    # Try to find workspace by workflow_id first
    workspace = None
    meta_manager = None

    if workflow_id:
        # Try agent mode first
        workspace_manager = WorkspaceManager(use_agents=True)
        success, data, msg = workspace_manager.load_workspace(workflow_id)
        if success:
            workspace = data.get("workspace_path")
        else:
            # Try standalone mode
            workspace_manager = WorkspaceManager(use_agents=False)
            success, data, msg = workspace_manager.load_workspace(workflow_id)
            if success:
                workspace = data.get("workspace_path")

    # If workspace found, use MetadataManager for action_id
    if workspace and workspace.exists():
        meta_manager = MetadataManager(workspace)

        # Get action_id from metadata if not provided
        if not action_id:
            action_id = meta_manager.get_action_id()

        # Try to recover action_id by title if still not found
        if not action_id:
            meta_data = meta_manager.load()
            title = meta_data.title
            if title:
                print(f"🔍 No action_id found. Searching by title: {title}")
                client = get_api_client_for_env()  # Uses active environment
                success_list, tools, msg_list = client.list_tools()
                if success_list and tools:
                    for tool in tools:
                        if tool.get("title") == title:
                            action_id = tool.get("action_id") or tool.get("id")
                            print(f"   ✅ Found action: {action_id}")
                            meta_manager.set_action_id(action_id)
                            break

    # Fallback to legacy location if no workspace
    if not workspace and action_id:
        workspace = Path(__file__).parent.parent / "actions" / action_id

    if not action_id:
        print("❌ No action_id found. Provide action_id or use --workflow-id with linked workspace.")
        return False

    print(f"Action ID: {action_id}")

    # Load API client with environment credentials
    client = get_api_client_for_env()

    success, versions, msg = client.list_versions(action_id)

    if not success:
        print(f"❌ {msg}")
        return False

    if not versions:
        print("   No versions found")
        return True

    # Sync all versions from API to metadata.json
    if workspace and workspace.exists():
        print("\n💾 Syncing versions to metadata.json...")
        sync_versions_from_api(workspace, versions)
        print("   ✅ Versions synced")
        
        # Get current and checked-out versions from metadata
        metadata = read_metadata(workspace)
        current_version = metadata.get("current_version")
        checked_out_version = metadata.get("checked_out_version")
    else:
        current_version = None
        checked_out_version = None

    print(f"\n   Found {len(versions)} versions:\n")
    print("   " + "-" * 70)
    print(f"   {'Version':<10} {'Status':<12} {'Created':<20} {'Description'}")
    print("   " + "-" * 70)

    for v in versions:
        version_id = v.get("version_number", v.get("id", "N/A"))
        status = v.get("status", "unknown")
        created = v.get("created_at", v.get("updated_at", "N/A"))
        if isinstance(created, str) and len(created) > 19:
            created = created[:19]
        description = v.get("change_reason", "")[:40]

        # Highlight live version and current/checked-out
        markers = []
        if status == "approved":
            markers.append("✅")
        if current_version and str(version_id) == str(current_version):
            markers.append("(current)")
        if checked_out_version and str(version_id) == str(checked_out_version):
            markers.append("(checked out)")
        marker = " ".join(markers) if markers else "  "

        print(f"   {marker:<15} {version_id:<8} {status:<12} {created:<20} {description}")

    print("   " + "-" * 70)
    
    if current_version or checked_out_version:
        version_info = []
        if current_version:
            version_info.append(f"Current: {current_version}")
        if checked_out_version:
            version_info.append(f"Checked out: {checked_out_version}")
        print(f"\n   {' | '.join(version_info)}")
    
    print("\n   To checkout a version:")
    if workflow_id:
        print(f"   python cli/checkout_wdl_version.py {action_id} --version <VERSION> --workflow-id {workflow_id}")
    else:
        print(f"   python cli/checkout_wdl_version.py {action_id} --version <VERSION>")
    print("=" * 80)

    return True


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="List action versions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
TRANSPARENT BEHAVIOR (default):
  - Action ID: Auto-detected from workspace metadata
  - Agent: Auto-detected from workspace location

Examples:
  # List versions using workflow_id (RECOMMENDED)
  python list_wdl_versions.py --workflow-id my-workflow

  # List versions with explicit action_id (legacy)
  python list_wdl_versions.py abc123-action-id

USE FLAGS ONLY when automatic behavior doesn't work.
""",
    )
    parser.add_argument(
        "action_id", nargs="?", default=None,
        help="Action ID (optional if --workflow-id provided)"
    )
    parser.add_argument(
        "--workflow-id", "-w",
        help="Workflow ID (RECOMMENDED - auto-detects action_id)"
    )
    parser.add_argument(
        "--standalone", "-s", action="store_true",
        help="Override agent detection with standalone mode"
    )
    args = parser.parse_args()

    # Require either action_id or workflow_id
    if not args.action_id and not args.workflow_id:
        parser.error("Either action_id or --workflow-id is required")

    success = list_wdl_versions(
        action_id=args.action_id,
        workflow_id=args.workflow_id,
        standalone=args.standalone,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
