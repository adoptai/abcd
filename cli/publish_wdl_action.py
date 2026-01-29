#!/usr/bin/env python3
"""
Approve and publish a WDL action (makes it live).

⚠️  EXPLICIT USER ACTION REQUIRED

This script:
1. AUTO-DETECTS action_id from metadata (or recovers by title)
2. AUTO-FINDS latest draft version to publish
3. Approves the version
4. Makes the action live

TRANSPARENT BEHAVIOR (default):
- Action ID: Auto-detected from metadata, recovered by title if lost
- Version: Auto-finds latest draft
- Agent/Standalone: Auto-detected from workspace location

Only run this when you've tested thoroughly and are ready to deploy.
USE FLAGS ONLY when automatic behavior doesn't work.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.api_client import AdoptAPIClient
from cli.wdl_common.metadata_manager import MetadataManager
from cli.wdl_common.version_tracker import (
    update_current_version,
    update_metadata_version,
    set_current_version,
    read_metadata,
)
from cli.wdl_common.workspace_manager import WorkspaceManager


def publish_wdl_action(
    action_id: Optional[str] = None,
    version_id: Optional[str] = None,
    skip_confirm: bool = False,
    description: Optional[str] = None,
    workflow_id: Optional[str] = None,
    standalone: bool = False,
) -> bool:
    """
    Approve and publish the action.

    TRANSPARENT: Can be called with just workflow_id - action_id auto-detected.

    Args:
        action_id: The action ID (optional - auto-detected from metadata)
        version_id: Specific version to publish (default: latest draft)
        skip_confirm: Skip confirmation prompt
        description: Optional description for this version
        workflow_id: Workflow ID to find workspace (recommended)
        standalone: Use standalone mode (override auto-detection)

    Returns:
        True if successful
    """
    print("\n" + "=" * 80)
    print("🚀 PUBLISH WDL ACTION")
    print("=" * 80)
    print("⚠️  This will make the action LIVE")

    # Try to find workspace by workflow_id first (preferred)
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
                client = AdoptAPIClient()
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
        print("❌ No action_id found. Provide --action-id or use --workflow-id with linked workspace.")
        return False

    print(f"Action ID: {action_id}")

    client = AdoptAPIClient()

    # Get existing version description if available (for reference)
    existing_description = None
    if version_id:
        # Get version info from versions list
        success, versions, msg = client.list_versions(action_id)
        if success and versions:
            version_number = int(version_id) if isinstance(version_id, str) else version_id
            for v in versions:
                if v.get("version_number") == version_number:
                    existing_description = v.get("change_reason") or v.get("status_notes", "")
                    break
    elif workspace and workspace.exists():
        # Try to get from metadata
        metadata = read_metadata(workspace)
        current_version = metadata.get("current_version")
        if current_version:
            versions = metadata.get("versions", {})
            version_info = versions.get(str(current_version), {})
            existing_description = version_info.get("description")

    # Use default description if not provided (no interactive prompts)
    # Only show existing description, but don't prompt for new one
    if not description:
        if existing_description and not skip_confirm:
            print(f"   Current description: {existing_description}")
        description = existing_description or "WDL action update"

    # Get current action to find latest version
    if not version_id:
        print("\n📋 Finding latest draft version...")
        success, versions, msg = client.list_versions(action_id)

        if not success or not versions:
            print(f"❌ {msg}")
            return False

        # Find latest draft/pending_approval version
        # Status is always either "pending_approval" (draft) or "approved" (published)
        for v in versions:
            if v.get("status") == "pending_approval":
                version_id = v.get("version_number", v.get("id"))
                break

        if not version_id:
            print("❌ No draft version found to publish")
            return False

        print(f"   Found version: {version_id}")

    # Confirm with user
    if not skip_confirm:
        print(f"\n⚠️  About to publish version {version_id}")
        if description:
            print(f"   Description: {description}")
        confirm = input("   Type 'yes' to confirm: ").strip().lower()

        if confirm != "yes":
            print("❌ Cancelled")
            return False

    # Approve version
    print(f"\n✅ Approving version {version_id}...")
    success, msg = client.approve_version(action_id, version_id, change_reason=description)

    if not success:
        print(f"❌ {msg}")
        return False

    version_number = int(version_id) if version_id else None

    # Update local version files if workspace exists
    if workspace and workspace.exists() and version_number:
        # Save WDL locally to versions directory for future checkout
        versions_dir = workspace / "versions"
        versions_dir.mkdir(exist_ok=True)
        version_wdl_path = versions_dir / f"v{version_number}_widdle.json"
        
        # Try to get WDL from local sources (avoid API call)
        wdl_to_save = None
        
        # First, check if we already have a local copy (from draft save)
        if version_wdl_path.exists():
            try:
                wdl_to_save = json.loads(version_wdl_path.read_text())
                print("   💾 Using existing local WDL copy")
            except Exception:
                pass
        
        # If not found, try to use current workspace WDL (should be the version being published)
        if not wdl_to_save:
            wdl_path = workspace / "widdle.json"
            if wdl_path.exists():
                try:
                    wdl_to_save = json.loads(wdl_path.read_text())
                    print("   💾 Using workspace WDL")
                except Exception:
                    pass
        
        # Save WDL locally if we have it
        if wdl_to_save:
            version_wdl_path.write_text(json.dumps(wdl_to_save, indent=2))
            print(f"   💾 Saved WDL locally: {version_wdl_path.name}")
        else:
            print("   ⚠️  Could not save WDL locally (will need API call for checkout)")
        
        # Update current_version.txt
        update_current_version(
            workspace=workspace,
            version_number=version_number,
            status="published",
            is_published=True,
            description=description,
        )
        
        # Update metadata.json
        set_current_version(workspace, version_number)
        
        # Get version data from versions list to get timestamps
        success, versions, msg = client.list_versions(action_id)
        created_at = ""
        updated_at = datetime.now().isoformat()
        if success and versions:
            for v in versions:
                if v.get("version_number") == version_number:
                    created_at = v.get("created_at", "")
                    updated_at = v.get("updated_at", updated_at)
                    break
        
        # Update version data in versions map
        update_metadata_version(
            workspace=workspace,
            version_number=version_number,
            status="published",
            is_published=True,
            description=description,
            created_at=created_at,
            updated_at=updated_at,
        )
        
        # If checked-out version matches published version, update its checked_out_at
        metadata = read_metadata(workspace)
        if metadata.get("checked_out_version") == version_number:
            update_metadata_version(
                workspace=workspace,
                version_number=version_number,
                status="published",
                is_published=True,
                description=description,
                created_at=created_at,
                updated_at=updated_at,
                checked_out_at=datetime.now().isoformat(),
            )

    print("\n" + "=" * 80)
    print("🎉 ACTION PUBLISHED!")
    print("=" * 80)
    print(f"   Action ID: {action_id}")
    print(f"   Version: {version_id}")
    print("   Status: LIVE")
    if description:
        print(f"   Description: {description}")
    print("=" * 80)

    return True


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Approve and publish action (makes it live)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
TRANSPARENT BEHAVIOR (default):
  - Action ID: Auto-detected from workspace metadata
  - Version: Auto-finds latest draft to publish
  - Agent: Auto-detected from workspace location

Examples:
  # Publish using workflow_id (RECOMMENDED - auto-detects action_id)
  python publish_wdl_action.py --workflow-id my-workflow

  # Publish with explicit action_id (legacy)
  python publish_wdl_action.py abc123-action-id

USE FLAGS ONLY when automatic behavior doesn't work.
""",
    )
    # Make action_id optional - can be derived from workflow_id
    parser.add_argument(
        "action_id", nargs="?", default=None,
        help="Action ID (optional if --workflow-id provided)"
    )
    parser.add_argument("--version", "-v", help="Specific version to publish (default: latest draft)")
    parser.add_argument(
        "--yes", "-y", action="store_true", help="Skip confirmation prompt"
    )
    parser.add_argument(
        "--description", "-d", help="Description of changes for this version"
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

    success = publish_wdl_action(
        action_id=args.action_id,
        version_id=args.version,
        skip_confirm=args.yes,
        description=args.description,
        workflow_id=args.workflow_id,
        standalone=args.standalone,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
