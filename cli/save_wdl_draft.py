#!/usr/bin/env python3
"""
Save WDL to remote as draft (without publishing).

This script:
1. Automatically creates remote action if it doesn't exist
2. RECOVERS action_id by title search if lost
3. Automatically publishes WDL if not already published
4. Saves as draft (creates version)
5. Does NOT approve - action stays in draft state

TRANSPARENT BEHAVIOR (default):
- Action creation: Auto-creates if not linked
- Action recovery: Auto-searches by title if action_id lost
- Agent detection: Auto-detects from workspace location
- Validation: Validates WDL before upload

Use this after tests pass to persist changes.
Use publish_wdl_action.py when ready to make it live.

USE FLAGS ONLY when automatic behavior doesn't work.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.api_client import AdoptAPIClient
from cli.wdl_common.workspace_manager import WorkspaceManager
from cli.wdl_common.metadata_manager import MetadataManager
from cli.wdl_common.validator import validate_wdl_file
from cli.wdl_common.version_tracker import (
    set_current_version,
    update_metadata_version,
)


def save_wdl_draft(
    action_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
    standalone: bool = False,
    agent_name: Optional[str] = None,
    description: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Save WDL to remote as draft.
    
    Automatically creates remote action and publishes WDL if needed.

    Args:
        action_id: The remote action ID (if known)
        workflow_id: The workflow ID (to find workspace)
        standalone: Use standalone mode (no agents)
        agent_name: Specific agent to use

    Returns:
        Tuple of (success, version_number)
    """
    print("\n" + "=" * 80)
    print("💾 SAVE WDL DRAFT")
    print("=" * 80)
    print("⚠️  This saves a draft - it will NOT be live until published")

    # Find workspace
    workspace_manager = WorkspaceManager(use_agents=not standalone)
    workspace_data = None
    
    if workflow_id:
        # Load workspace by workflow_id
        success, workspace_data, msg = workspace_manager.load_workspace(workflow_id, agent_name)
        if not success:
            print(f"❌ {msg}")
            return False, ""
        workspace = workspace_data["workspace_path"]
        
        # Get action_id from metadata if not provided
        if not action_id:
            metadata = workspace_data.get("metadata", {})
            action_id = metadata.get("action_id")
    elif action_id:
        # Try to find workspace by action_id (legacy mode)
        workspace = Path(__file__).parent.parent / "actions" / action_id
        if not workspace.exists():
            print(f"❌ Workspace not found: {workspace}")
            print("💡 Try using --workflow-id instead")
            return False, ""
    else:
        print("❌ Must provide either --action-id or --workflow-id")
        return False, ""

    print(f"📁 Workspace: {workspace}")

    # Use MetadataManager for enhanced action_id handling
    meta_manager = MetadataManager(workspace)
    meta_data = meta_manager.load()

    # Load WDL
    wdl_path = workspace / "widdle.json"
    if not wdl_path.exists():
        print("❌ widdle.json not found")
        return False, ""

    # Validate WDL before upload
    print("\n📋 Validating WDL...")
    validation = validate_wdl_file(wdl_path)
    if not validation.is_valid:
        print(f"\n{validation}")
        print("💡 Fix validation errors before saving. Use --auto-fix if applicable.")
        return False, ""
    print("   ✅ WDL is valid")

    wdl = json.loads(wdl_path.read_text())
    print(f"📝 Loaded WDL: {len(wdl)} operations")

    client = AdoptAPIClient()

    # Step 1: Get action_id with recovery
    # Use MetadataManager's protected action_id mechanism
    if not action_id:
        action_id = meta_manager.get_action_id()

    # If still no action_id, try to recover by title search
    if not action_id:
        title = meta_data.title or workspace_data.get("metadata", {}).get("title") if workspace_data else None
        if title:
            print(f"\n🔍 No action_id found. Searching by title: {title}")
            success_list, tools, msg_list = client.list_tools()
            if success_list and tools:
                for tool in tools:
                    if tool.get("title") == title:
                        action_id = tool.get("action_id") or tool.get("id")
                        print(f"   ✅ Found existing action: {action_id}")
                        meta_manager.set_action_id(action_id)
                        break

    # If still no action_id, create new action
    if not action_id:
        print("\n🔧 Step 1: Creating remote action...")
        
        # Get title from metadata
        title = meta_data.title
        if not title:
        metadata_path = workspace / "metadata.json"
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text())
            title = metadata.get("title", "New WDL Workflow")
        else:
            title = "New WDL Workflow"
        
        # Load requirements for description
        requirements_path = workspace / "requirements.md"
        if requirements_path.exists():
            requirements = requirements_path.read_text()[:200]
            action_description = f"Generated from requirements: {requirements}..."
        else:
            action_description = f"WDL workflow: {title}"
        
        # Load API IDs from manifest
        api_ids = []
        apis_manifest = workspace / "apis" / "manifest.json"
        if apis_manifest.exists():
            manifest = json.loads(apis_manifest.read_text())
            api_ids = manifest.get("api_ids", [])
        
        print(f"   Title: {title}")
        print(f"   API IDs: {api_ids}")
        
        success, data, msg = client.create_action(
            title=title,
            description=action_description,
            api_ids=api_ids if api_ids else None,
        )
        
        if not success:
            print(f"❌ Failed to create action: {msg}")
            return False, ""
        
        action_id = data.get("action_id") if data else None
        if not action_id:
            print("❌ No action_id in response")
            return False, ""
        
        print(f"   ✅ Action created: {action_id}")
        
        # Set deployment rules
        client.set_deployment_rules(action_id)
        
        # Save action_id using MetadataManager (protected file + metadata)
        meta_manager.set_action_id(action_id)
        print("   ✅ Updated metadata with action_id")
    else:
        print(f"\n🔑 Step 1: Using existing action ID: {action_id}")
        # Verify action exists
        success, current_data, msg = client.get_action(action_id)
        if not success:
            print(f"⚠️  Action not found on remote: {msg}")
            print("   Attempting to recover by title search...")
            title = meta_data.title
            if title:
                success_list, tools, msg_list = client.list_tools()
                if success_list and tools:
                    for tool in tools:
                        if tool.get("title") == title:
                            action_id = tool.get("action_id") or tool.get("id")
                            print(f"   ✅ Recovered action: {action_id}")
                            meta_manager.set_action_id(action_id)
                            break
                    else:
                        print("❌ Could not recover action by title")
                        return False, ""
            else:
                print("❌ Cannot recover - no title in metadata")
            return False, ""
        else:
        print("   ✅ Action exists on remote")

    print(f"\n🔑 Action ID: {action_id}")

    # Step 2: Get draft ID first (needed for publishing WDL)
    print("\n📋 Step 2: Getting draft ID...")
    success, data, msg = client.get_action(action_id)
    if not success or not data:
        print(f"❌ {msg}")
        return False, ""
    
    draft_id = data.get("id", data.get("draft_id", ""))
    if not draft_id:
        print("   ⚠️  No draft_id found, action may not have a draft yet")
        # Try to get draft_id from latest version
        draft_id = data.get("draft_id", "")
    
    # Step 3: Publish WDL (always publish to ensure latest version is on remote)
    # Pass draft_id so WDL is properly stored in the draft table
    print("\n📝 Step 3: Publishing WDL...")
    success, msg = client.publish_wdl(action_id, wdl, draft_id=draft_id if draft_id else None)
    if not success:
        print(f"❌ {msg}")
        return False, ""
    print(f"   ✅ {msg}")

    # Step 4: Wait for update
    print("\n⏳ Step 4: Waiting for update...")
    success, current_data, msg = client.get_action(action_id)
    if success and current_data:
        current_updated_at = current_data.get("updated_at", "")
        if current_updated_at:
            success, data, msg = client.wait_for_update(
                action_id, current_updated_at, max_retries=10, poll_interval=3
            )
            if success:
                print("   ✅ Update detected")
    else:
        print("   ⚠️  Could not fetch action state, continuing...")

    # Step 5: Populate instructions
    print("\n📝 Step 5: Populating instructions...")
    client.populate_instructions(action_id)

    # Step 6: Verify draft ID is still available (may have changed after update)
    print("\n📋 Step 6: Verifying draft ID...")
    success, data, msg = client.get_action(action_id)
    if not success or not data:
        print(f"❌ {msg}")
        return False, ""
    
    # Use the draft_id we got earlier, or get it again if needed
    if not draft_id:
        draft_id = data.get("id", data.get("draft_id", ""))

    # Use default description if not provided (no interactive prompts)
    if not description:
        description = "WDL workflow update"  # Default

    # Step 7: Save draft (creates version but does NOT approve)
    print("\n💾 Step 7: Saving draft...")
    success, version, msg = client.save_draft(action_id, draft_id, change_reason=description)
    if not success:
        print(f"❌ {msg}")
        return False, ""

    version_number = int(version) if version else None
    if not version_number:
        print("❌ No version number returned")
        return False, ""

    # Save WDL locally to versions directory for future checkout
    versions_dir = workspace / "versions"
    versions_dir.mkdir(exist_ok=True)
    version_wdl_path = versions_dir / f"v{version_number}_widdle.json"
    version_wdl_path.write_text(json.dumps(wdl, indent=2))
    print(f"   💾 Saved WDL locally: {version_wdl_path.name}")

    # Update current_version.txt
    from cli.wdl_common.version_tracker import update_current_version
    update_current_version(
        workspace=workspace,
        version_number=version_number,
        status="draft",
        is_published=False,
        description=description,
    )
    
    # Update metadata.json
    metadata_path = workspace / "metadata.json"
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text())
    else:
        metadata = {}
    metadata["action_id"] = action_id
    
    # Set current_version (just the number)
    set_current_version(workspace, version_number)
    
    # Store full version data in versions map
    update_metadata_version(
        workspace=workspace,
        version_number=version_number,
        status="draft",
        is_published=False,
        description=description,
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )
    
    # If checked_out_version matches new version, update its checked_out_at
    metadata = json.loads(metadata_path.read_text())
    if metadata.get("checked_out_version") == version_number:
        update_metadata_version(
            workspace=workspace,
            version_number=version_number,
            status="draft",
            is_published=False,
            description=description,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
            checked_out_at=datetime.now().isoformat(),
        )

    print("\n" + "=" * 80)
    print("✅ DRAFT SAVED SUCCESSFULLY")
    print("=" * 80)
    print(f"   Action ID: {action_id}")
    if workflow_id:
        print(f"   Workflow ID: {workflow_id}")
    print(f"   Version: {version}")
    print("   Status: DRAFT (not live)")
    print(f"\n   To publish: python cli/publish_wdl_action.py {action_id}")
    print("=" * 80)

    return True, version or ""


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Save WDL as draft (no publish)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Using action_id (legacy)
  python cli/save_wdl_draft.py --action-id abc-123
  
  # Using workflow_id (recommended)
  python cli/save_wdl_draft.py --workflow-id 6f341fb2-local
  
  # Standalone workflow
  python cli/save_wdl_draft.py --workflow-id 6f341fb2-local --standalone
        """
    )
    parser.add_argument(
        "--action-id", help="Action ID (if known)"
    )
    parser.add_argument(
        "--workflow-id", "-w", help="Workflow ID (to find workspace)"
    )
    parser.add_argument(
        "--standalone", "-s", action="store_true",
        help="Standalone mode (no agent structure)"
    )
    parser.add_argument(
        "--agent", "-a", help="Specific agent to use"
    )
    parser.add_argument(
        "--description", "-d", help="Description of changes for this version"
    )
    
    args = parser.parse_args()
    
    if not args.action_id and not args.workflow_id:
        parser.print_help()
        print("\n❌ Error: Must provide either --action-id or --workflow-id", file=sys.stderr)
        sys.exit(1)

    success, _ = save_wdl_draft(
        action_id=args.action_id,
        workflow_id=args.workflow_id,
        standalone=args.standalone,
        agent_name=args.agent,
        description=args.description,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
