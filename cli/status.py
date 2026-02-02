#!/usr/bin/env python3
"""
Unified Status Command - Shows comprehensive workspace state.

Displays:
- Working version and local changes
- Remote state (latest draft, published, current)
- Sync status
- Suggested next actions

Usage:
    python cli/status.py my-workflow
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.metadata_manager import MetadataManager
from cli.wdl_common.workspace_manager import WorkspaceManager
from cli.wdl_common.api_client import AdoptAPIClient, get_api_client_for_env


def find_workspace(workflow_id: str) -> tuple[Path, dict]:
    """
    Find workspace by workflow_id, auto-detecting agent vs standalone.
    
    Args:
        workflow_id: Workflow ID to find
        
    Returns:
        Tuple of (workspace_path, metadata_dict)
        
    Raises:
        ValueError: If workspace not found
    """
    # Try with agents first
    manager = WorkspaceManager(use_agents=True)
    success, data, msg = manager.load_workspace(workflow_id)
    
    if success:
        return data["workspace_path"], data.get("metadata", {})
    
    # Try standalone
    manager = WorkspaceManager(use_agents=False)
    success, data, msg = manager.load_workspace(workflow_id)
    
    if success:
        return data["workspace_path"], data.get("metadata", {})
    
    raise ValueError(f"Workspace not found: {workflow_id}")


def show_status(workflow_id: str, sync_remote: bool = True) -> bool:
    """
    Show comprehensive status for a workflow.
    
    Args:
        workflow_id: Workflow ID
        sync_remote: Whether to sync with remote (fetch latest versions)
        
    Returns:
        True if successful
    """
    print("\n" + "=" * 80)
    print("📊 WORKFLOW STATUS")
    print("=" * 80)
    
    # Find workspace
    try:
        workspace, _ = find_workspace(workflow_id)
    except ValueError as e:
        print(f"\n❌ {e}")
        return False
    
    # Load metadata with new manager
    meta_manager = MetadataManager(workspace)
    metadata = meta_manager.load()
    
    # Display workspace info
    print(f"\n📁 Workspace: {workspace}")
    if metadata.title:
        print(f"📝 Title: {metadata.title}")
    if metadata.agent_name:
        print(f"👤 Agent: {metadata.agent_name}")
    
    # Display remote action info
    action_id = meta_manager.get_action_id()
    if action_id:
        print(f"🔗 Remote Action: {action_id}")
    else:
        print("🔗 Remote Action: Not linked (local only)")
    
    # Sync with remote if action exists
    if action_id and sync_remote:
        print("\n⏳ Syncing with remote...")
        client = get_api_client_for_env()  # Uses active environment
        success, versions, msg = client.list_versions(action_id)
        if success and versions:
            meta_manager.sync_versions_from_api(versions)
            metadata = meta_manager.load()  # Reload after sync
            print("   ✅ Synced")
        elif not success:
            print(f"   ⚠️  Could not sync: {msg}")
    
    # Check for local changes
    has_local_changes = meta_manager.check_local_changes()
    
    # Working version section
    print("\n" + "┌" + "─" * 78 + "┐")
    print("│ WORKING VERSION" + " " * 62 + "│")
    print("├" + "─" * 78 + "┤")
    
    working = metadata.working_version
    if working.number:
        status_icon = "✅" if working.is_published else "📝"
        status_text = "published" if working.is_published else "draft"
        print(f"│ Version:    {working.number} ({status_text}) {status_icon}" + " " * (60 - len(str(working.number)) - len(status_text)) + "│")
        
        if has_local_changes:
            print("│ Status:     LOCAL CHANGES - needs save 🔸" + " " * 36 + "│")
        else:
            print("│ Status:     Synced ✅" + " " * 56 + "│")
        
        # Count operations in WDL
        wdl_path = workspace / "widdle.json"
        if wdl_path.exists():
            try:
                wdl = json.loads(wdl_path.read_text())
                op_count = len([op for op in wdl if isinstance(op, dict) and op.get("operation")])
                print(f"│ Operations: {op_count} operations in widdle.json" + " " * (50 - len(str(op_count))) + "│")
            except Exception:
                pass
        
        if working.description:
            desc = working.description[:50] + "..." if len(working.description) > 50 else working.description
            print(f"│ Description: {desc}" + " " * (65 - len(desc)) + "│")
    else:
        print("│ No working version set" + " " * 55 + "│")
    
    print("└" + "─" * 78 + "┘")
    
    # Deployment rules section (if linked to remote)
    if action_id and sync_remote:
        print("\n" + "┌" + "─" * 78 + "┐")
        print("│ DEPLOYMENT RULES" + " " * 61 + "│")
        print("├" + "─" * 78 + "┤")

        success, rules, _ = client.get_deployment_rules(action_id)
        if success and rules:
            is_tool_mode = rules.get("is_tool_mode", False)
            is_visible = rules.get("is_visible_in_list", True)

            tool_mode_text = "ENABLED ✓" if is_tool_mode else "Disabled"
            visible_text = "Yes" if is_visible else "Hidden"

            print(f"│ Tool Mode:        {tool_mode_text}" + " " * (59 - len(tool_mode_text)) + "│")
            print(f"│ Visible in List:  {visible_text}" + " " * (59 - len(visible_text)) + "│")

            targeting_rules = rules.get("rules", [])
            if targeting_rules:
                print(f"│ Targeting Rules:  {len(targeting_rules)} rules configured" + " " * 43 + "│")
            else:
                print("│ Targeting Rules:  None (available to all)" + " " * 35 + "│")
        else:
            print("│ Could not fetch deployment rules" + " " * 44 + "│")

        print("└" + "─" * 78 + "┘")

    # Remote state section
    print("\n" + "┌" + "─" * 78 + "┐")
    print("│ REMOTE STATE" + " " * 65 + "│")
    print("├" + "─" * 78 + "┤")
    
    remote = metadata.remote_versions
    if remote.latest_draft or remote.latest_published or remote.current:
        if remote.latest_draft:
            match_text = " (matches working)" if remote.latest_draft == working.number else ""
            print(f"│ Latest Draft:     {remote.latest_draft}{match_text}" + " " * (59 - len(str(remote.latest_draft)) - len(match_text)) + "│")
        else:
            print("│ Latest Draft:     None" + " " * 55 + "│")
        
        if remote.latest_published:
            print(f"│ Latest Published: {remote.latest_published}" + " " * (59 - len(str(remote.latest_published))) + "│")
        else:
            print("│ Latest Published: None" + " " * 55 + "│")
        
        if remote.current:
            print(f"│ Current (Live):   {remote.current}" + " " * (59 - len(str(remote.current))) + "│")
    else:
        if action_id:
            print("│ No versions found on remote" + " " * 50 + "│")
        else:
            print("│ Not linked to remote action" + " " * 50 + "│")
    
    print("└" + "─" * 78 + "┘")
    
    # Versions history (if available)
    if metadata.versions:
        print("\n" + "┌" + "─" * 78 + "┐")
        print("│ VERSION HISTORY (recent)" + " " * 53 + "│")
        print("├" + "─" * 78 + "┤")
        
        # Sort versions by number descending
        sorted_versions = sorted(
            metadata.versions.items(),
            key=lambda x: int(x[0]),
            reverse=True
        )[:5]  # Show last 5
        
        for v_num, v_info in sorted_versions:
            status_icon = "✅" if v_info.is_published else "📝"
            local_icon = "💾" if v_info.has_local_copy else "  "
            desc = v_info.description[:35] + "..." if v_info.description and len(v_info.description) > 35 else (v_info.description or "")
            line = f"│ {status_icon} v{v_num} {local_icon} {desc}"
            print(line + " " * (79 - len(line)) + "│")
        
        print("└" + "─" * 78 + "┘")
    
    # Next actions section
    print("\n" + "┌" + "─" * 78 + "┐")
    print("│ NEXT ACTIONS" + " " * 65 + "│")
    print("├" + "─" * 78 + "┤")
    
    if not action_id:
        print("│ 📌 Create remote action and save:" + " " * 42 + "│")
        print(f"│    python cli/save.py {workflow_id}" + " " * (54 - len(workflow_id)) + "│")
    elif has_local_changes:
        print("│ ⚡ You have local changes. To test:" + " " * 41 + "│")
        print(f"│    python cli/test.py {workflow_id}" + " " * (54 - len(workflow_id)) + "│")
        print("│    (Will auto-save draft before testing)" + " " * 36 + "│")
        print("│" + " " * 78 + "│")
        print("│ 💾 Or save draft only:" + " " * 54 + "│")
        print(f"│    python cli/save.py {workflow_id}" + " " * (54 - len(workflow_id)) + "│")
    elif working.number and not working.is_published:
        print("│ 🧪 Test the current draft:" + " " * 51 + "│")
        print(f"│    python cli/test.py {workflow_id}" + " " * (54 - len(workflow_id)) + "│")
        print("│" + " " * 78 + "│")
        print(f"│ 📦 Publish version {working.number}:" + " " * (56 - len(str(working.number))) + "│")
        print(f"│    python cli/publish.py {workflow_id}" + " " * (51 - len(workflow_id)) + "│")
    elif working.number and working.is_published:
        print("│ ✅ Current version is published and live" + " " * 37 + "│")
        print("│" + " " * 78 + "│")
        print("│ 📝 To make changes, edit widdle.json and run:" + " " * 32 + "│")
        print(f"│    python cli/test.py {workflow_id}" + " " * (54 - len(workflow_id)) + "│")
    else:
        print("│ 🧪 Test the workflow:" + " " * 56 + "│")
        print(f"│    python cli/test.py {workflow_id}" + " " * (54 - len(workflow_id)) + "│")
    
    print("└" + "─" * 78 + "┘")
    
    print("\n" + "=" * 80)
    
    return True


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Show comprehensive workflow status",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cli/status.py my-workflow
  python cli/status.py my-workflow --no-sync
        """,
    )
    
    parser.add_argument("workflow_id", help="Workflow ID")
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Don't sync with remote (faster, uses cached data)",
    )
    
    args = parser.parse_args()
    
    success = show_status(
        workflow_id=args.workflow_id,
        sync_remote=not args.no_sync,
    )
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

