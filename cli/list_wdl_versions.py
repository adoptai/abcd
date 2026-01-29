#!/usr/bin/env python3
"""
List all versions of a WDL action.

Shows version history with status, timestamps, and which version is live.
"""

import argparse
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.api_client import AdoptAPIClient
from cli.wdl_common.version_tracker import (
    sync_versions_from_api,
    read_metadata,
)
from cli.wdl_common.workspace_manager import WorkspaceManager


def list_wdl_versions(
    action_id: str,
    workflow_id: Optional[str] = None,
    standalone: bool = False,
) -> bool:
    """
    List all versions of an action and sync to metadata.json.

    Args:
        action_id: The action ID
        workflow_id: Optional workflow ID to find workspace
        standalone: Use standalone mode (no agents)

    Returns:
        True if successful
    """
    print("\n" + "=" * 80)
    print("📋 WDL ACTION VERSIONS")
    print("=" * 80)
    print(f"Action ID: {action_id}")

    # Try to find workspace
    workspace = None
    if workflow_id:
        workspace_manager = WorkspaceManager(use_agents=not standalone)
        success, data, msg = workspace_manager.load_workspace(workflow_id)
        if success:
            workspace = data.get("workspace_path")
    
    # Fallback to legacy location
    if not workspace:
        workspace = Path(__file__).parent.parent / "actions" / action_id

    client = AdoptAPIClient()

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
    parser = argparse.ArgumentParser(description="List action versions")
    parser.add_argument("action_id", help="Action ID")
    parser.add_argument("--workflow-id", "-w", help="Workflow ID (to find workspace)")
    parser.add_argument("--standalone", "-s", action="store_true", help="Standalone mode")
    args = parser.parse_args()

    success = list_wdl_versions(
        args.action_id,
        workflow_id=args.workflow_id,
        standalone=args.standalone,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
