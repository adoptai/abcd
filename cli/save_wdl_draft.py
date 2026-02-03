#!/usr/bin/env python3
"""
Save WDL to remote as draft (without publishing).

Features:
- Single action: save_wdl_draft.py my-action
- Multiple actions: save_wdl_draft.py action1 action2 action3
- Parallel execution: save_wdl_draft.py action1 action2 --parallel 3
- Agent + subactions: save_wdl_draft.py --agent my-agent

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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.api_client import AdoptAPIClient, get_api_client_for_env
from cli.wdl_common.workspace_manager import WorkspaceManager, get_workspace_manager, HierarchicalWorkspaceManager
from cli.wdl_common.metadata_manager import MetadataManager
from cli.wdl_common.validator import validate_wdl_file
from cli.wdl_common.version_tracker import (
    set_current_version,
    update_metadata_version,
)


@dataclass
class DraftResult:
    """Result of a single draft save operation."""
    workflow_id: str
    success: bool
    message: str
    version: Optional[int] = None
    action_id: Optional[str] = None
    error: Optional[str] = None


def save_single_draft(
    workflow_id: str,
    manager: HierarchicalWorkspaceManager,
    client: AdoptAPIClient,
    description: Optional[str] = None,
    verbose: bool = True,
) -> DraftResult:
    """
    Save WDL draft for a single action.
    
    Args:
        workflow_id: The workflow/action ID
        manager: Workspace manager instance
        client: API client instance
        description: Version description
        verbose: Print progress messages
        
    Returns:
        DraftResult with outcome
    """
    def log(msg: str) -> None:
        if verbose:
            print(msg)
    
    # Find workspace
    workspace_data = manager.find_action(workflow_id)
    if not workspace_data:
        return DraftResult(
            workflow_id=workflow_id,
            success=False,
            message="Workspace not found",
            error=f"Action '{workflow_id}' not found in active environment",
        )
    
    workspace = workspace_data["path"]
    log(f"📁 Workspace: {workspace}")
    
    # Use MetadataManager for enhanced action_id handling
    meta_manager = MetadataManager(workspace)
    meta_data = meta_manager.load()
    
    # Load WDL
    wdl_path = workspace / "widdle.json"
    if not wdl_path.exists():
        return DraftResult(
            workflow_id=workflow_id,
            success=False,
            message="No WDL file",
            error="widdle.json not found",
        )
    
    # Validate WDL before upload
    log("\n📋 Validating WDL...")
    validation = validate_wdl_file(wdl_path)
    if not validation.is_valid:
        return DraftResult(
            workflow_id=workflow_id,
            success=False,
            message="Validation failed",
            error=str(validation),
        )
    log("   ✅ WDL is valid")
    
    wdl = json.loads(wdl_path.read_text())
    log(f"📝 Loaded WDL: {len(wdl)} operations")
    
    # Step 1: Get action_id with recovery
    action_id = meta_manager.get_action_id()
    
    # If no action_id, try to recover by title search
    if not action_id:
        title = meta_data.title or workspace_data.get("metadata", {}).get("title")
        if title:
            log(f"\n🔍 No action_id found. Searching by title: {title}")
            success_list, tools, msg_list = client.list_tools()
            if success_list and tools:
                for tool in tools:
                    if tool.get("title") == title:
                        action_id = tool.get("action_id") or tool.get("id")
                        log(f"   ✅ Found existing action: {action_id}")
                        meta_manager.set_action_id(action_id)
                        break
    
    # If still no action_id, create new action
    if not action_id:
        log("\n🔧 Step 1: Creating remote action...")
        
        title = meta_data.title
        if not title:
            metadata_path = workspace / "metadata.json"
            if metadata_path.exists():
                metadata = json.loads(metadata_path.read_text())
                title = metadata.get("title", "New WDL Workflow")
            else:
                title = "New WDL Workflow"
        
        requirements_path = workspace / "requirements.md"
        if requirements_path.exists():
            requirements = requirements_path.read_text()[:200]
            action_description = f"Generated from requirements: {requirements}..."
        else:
            action_description = f"WDL workflow: {title}"
        
        api_ids = []
        apis_manifest = workspace / "apis" / "manifest.json"
        if apis_manifest.exists():
            manifest = json.loads(apis_manifest.read_text())
            api_ids = manifest.get("api_ids", [])
        
        log(f"   Title: {title}")
        log(f"   API IDs: {api_ids}")
        
        success, data, msg = client.create_action(
            title=title,
            description=action_description,
            api_ids=api_ids if api_ids else None,
        )
        
        if not success:
            return DraftResult(
                workflow_id=workflow_id,
                success=False,
                message="Failed to create action",
                error=msg,
            )
        
        action_id = data.get("action_id") if data else None
        if not action_id:
            return DraftResult(
                workflow_id=workflow_id,
                success=False,
                message="No action_id in response",
                error="Remote did not return action_id",
            )
        
        log(f"   ✅ Action created: {action_id}")
        client.set_deployment_rules(action_id)
        meta_manager.set_action_id(action_id)
        log("   ✅ Updated metadata with action_id")
    else:
        log(f"\n🔑 Step 1: Using existing action ID: {action_id}")
        success, current_data, msg = client.get_action(action_id)
        if not success:
            log(f"⚠️  Action not found on remote: {msg}")
            log("   Attempting to recover by title search...")
            title = meta_data.title
            if title:
                success_list, tools, msg_list = client.list_tools()
                if success_list and tools:
                    for tool in tools:
                        if tool.get("title") == title:
                            action_id = tool.get("action_id") or tool.get("id")
                            log(f"   ✅ Recovered action: {action_id}")
                            meta_manager.set_action_id(action_id)
                            break
                    else:
                        return DraftResult(
                            workflow_id=workflow_id,
                            success=False,
                            message="Could not recover action",
                            error="Action not found and title search failed",
                        )
            else:
                return DraftResult(
                    workflow_id=workflow_id,
                    success=False,
                    message="Cannot recover action",
                    error="No title in metadata for recovery",
                )
        else:
            log("   ✅ Action exists on remote")
    
    log(f"\n🔑 Action ID: {action_id}")
    
    # Step 2: Get draft ID
    log("\n📋 Step 2: Getting draft ID...")
    success, data, msg = client.get_action(action_id)
    if not success or not data:
        return DraftResult(
            workflow_id=workflow_id,
            success=False,
            message="Failed to get action",
            action_id=action_id,
            error=msg,
        )
    
    draft_id = data.get("id", data.get("draft_id", ""))
    
    # Step 3: Publish WDL
    log("\n📝 Step 3: Publishing WDL...")
    success, msg = client.publish_wdl(action_id, wdl, draft_id=draft_id if draft_id else None)
    if not success:
        return DraftResult(
            workflow_id=workflow_id,
            success=False,
            message="Failed to publish WDL",
            action_id=action_id,
            error=msg,
        )
    log(f"   ✅ {msg}")
    
    # Step 4: Wait for update
    log("\n⏳ Step 4: Waiting for update...")
    success, current_data, msg = client.get_action(action_id)
    if success and current_data:
        current_updated_at = current_data.get("updated_at", "")
        if current_updated_at:
            success, data, msg = client.wait_for_update(
                action_id, current_updated_at, max_retries=10, poll_interval=3
            )
            if success:
                log("   ✅ Update detected")
    
    # Step 5: Populate instructions
    log("\n📝 Step 5: Populating instructions...")
    client.populate_instructions(action_id)
    
    # Step 6: Verify draft ID
    log("\n📋 Step 6: Verifying draft ID...")
    success, data, msg = client.get_action(action_id)
    if not success or not data:
        return DraftResult(
            workflow_id=workflow_id,
            success=False,
            message="Failed to verify draft",
            action_id=action_id,
            error=msg,
        )
    
    if not draft_id:
        draft_id = data.get("id", data.get("draft_id", ""))
    
    if not description:
        description = "WDL workflow update"
    
    # Step 7: Save draft
    log("\n💾 Step 7: Saving draft...")
    success, version, msg = client.save_draft(action_id, draft_id, change_reason=description)
    if not success:
        return DraftResult(
            workflow_id=workflow_id,
            success=False,
            message="Failed to save draft",
            action_id=action_id,
            error=msg,
        )
    
    version_number = int(version) if version else None
    if not version_number:
        return DraftResult(
            workflow_id=workflow_id,
            success=False,
            message="No version returned",
            action_id=action_id,
            error="Server did not return version number",
        )
    
    # Save WDL locally to versions directory
    versions_dir = workspace / "versions"
    versions_dir.mkdir(exist_ok=True)
    version_wdl_path = versions_dir / f"v{version_number}_widdle.json"
    version_wdl_path.write_text(json.dumps(wdl, indent=2))
    log(f"   💾 Saved WDL locally: {version_wdl_path.name}")
    
    # Update tracking
    from cli.wdl_common.version_tracker import update_current_version
    update_current_version(
        workspace=workspace,
        version_number=version_number,
        status="draft",
        is_published=False,
        description=description,
    )
    
    set_current_version(workspace, version_number)
    update_metadata_version(
        workspace=workspace,
        version_number=version_number,
        status="draft",
        is_published=False,
        description=description,
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )
    
    return DraftResult(
        workflow_id=workflow_id,
        success=True,
        message=f"Draft saved (v{version_number})",
        version=version_number,
        action_id=action_id,
    )


def save_parallel_drafts(
    workflow_ids: List[str],
    manager: HierarchicalWorkspaceManager,
    max_workers: int = 5,
    description: Optional[str] = None,
) -> List[DraftResult]:
    """
    Save drafts for multiple actions in parallel.
    
    Args:
        workflow_ids: List of workflow/action IDs
        manager: Workspace manager instance
        max_workers: Maximum parallel workers
        description: Version description for all
        
    Returns:
        List of DraftResults
    """
    results: List[DraftResult] = []
    client = get_api_client_for_env()
    
    print(f"\n🚀 Saving {len(workflow_ids)} drafts in parallel (max {max_workers} workers)...")
    print("=" * 80)
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_workflow = {
            executor.submit(
                save_single_draft, 
                wid, 
                manager, 
                client, 
                description,
                False,  # verbose=False for parallel
            ): wid
            for wid in workflow_ids
        }
        
        for future in as_completed(future_to_workflow):
            workflow_id = future_to_workflow[future]
            try:
                result = future.result()
                results.append(result)
                status = "✅" if result.success else "❌"
                print(f"   {status} {workflow_id}: {result.message}")
            except Exception as e:
                results.append(DraftResult(
                    workflow_id=workflow_id,
                    success=False,
                    message="Exception",
                    error=str(e),
                ))
                print(f"   ❌ {workflow_id}: {e}")
    
    return results


def save_agent_drafts(
    agent_id: str,
    manager: HierarchicalWorkspaceManager,
    changed_only: bool = True,
    max_workers: int = 5,
    description: Optional[str] = None,
) -> List[DraftResult]:
    """
    Save drafts for an agent and all its subactions.
    
    Args:
        agent_id: Agent ID
        manager: Workspace manager instance
        changed_only: Only save subactions with local changes
        max_workers: Maximum parallel workers
        description: Version description
        
    Returns:
        List of DraftResults (subactions + agent)
    """
    env = manager.active_env
    agent = manager.get_agent(agent_id, env)
    
    if not agent:
        return [DraftResult(
            workflow_id=agent_id,
            success=False,
            message="Agent not found",
            error=f"Agent '{agent_id}' not found in environment '{env}'",
        )]
    
    print(f"\n🤖 Saving agent: {agent_id}")
    print(f"📁 Path: {agent.get('path')}")
    
    # Collect subactions to save
    subaction_ids: List[str] = []
    for sa in agent.get("sub_actions", []):
        sa_id = sa.get("action_id") or sa.get("id")
        if not sa_id:
            continue
        
        if changed_only:
            # Check for local changes
            sa_info = manager.find_action(sa_id)
            if sa_info:
                meta_manager = MetadataManager(sa_info["path"])
                if not meta_manager.check_local_changes():
                    print(f"   ⏭️  {sa_id}: No local changes, skipping")
                    continue
        
        subaction_ids.append(sa_id)
    
    print(f"\n📋 Subactions to save: {len(subaction_ids)}")
    
    results: List[DraftResult] = []
    
    # Save subactions in parallel
    if subaction_ids:
        subaction_results = save_parallel_drafts(
            subaction_ids, manager, max_workers, description
        )
        results.extend(subaction_results)
    
    # Save agent itself (if it has a widdle.json)
    agent_path = Path(agent.get("path", ""))
    if (agent_path / "widdle.json").exists():
        print(f"\n📦 Saving agent WDL...")
        client = get_api_client_for_env()
        agent_result = save_single_draft(agent_id, manager, client, description, True)
        results.append(agent_result)
    else:
        print(f"\n⏭️  Agent has no WDL, skipping agent save")
    
    return results


def print_results(results: List[DraftResult]) -> None:
    """Print summary of draft save results."""
    passed = [r for r in results if r.success]
    failed = [r for r in results if not r.success]
    
    print("\n" + "=" * 80)
    print("📊 SAVE DRAFT RESULTS SUMMARY")
    print("=" * 80)
    print(f"Total: {len(results)} | Saved: {len(passed)} ✅ | Failed: {len(failed)} ❌")
    print("-" * 80)
    
    if passed:
        print("\n✅ SAVED:")
        for r in passed:
            version_str = f"v{r.version}" if r.version else ""
            print(f"   {r.workflow_id} {version_str} - {r.message}")
    
    if failed:
        print("\n❌ FAILED:")
        for r in failed:
            print(f"   {r.workflow_id} - {r.message}")
            if r.error:
                print(f"      Error: {r.error[:100]}")
    
    print("=" * 80)


# Legacy function for backward compatibility
def save_wdl_draft(
    action_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
    standalone: bool = False,
    agent_name: Optional[str] = None,
    description: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Save WDL to remote as draft (legacy interface).
    
    For new code, use save_single_draft() instead.
    """
    print("\n" + "=" * 80)
    print("💾 SAVE WDL DRAFT")
    print("=" * 80)
    print("⚠️  This saves a draft - it will NOT be live until published")
    
    manager = get_workspace_manager()
    client = get_api_client_for_env()
    
    wid = workflow_id or action_id
    if not wid:
        print("❌ Must provide either action_id or workflow_id")
        return False, ""
    
    result = save_single_draft(wid, manager, client, description, verbose=True)
    
    if result.success:
        print("\n" + "=" * 80)
        print("✅ DRAFT SAVED SUCCESSFULLY")
        print("=" * 80)
        print(f"   Action ID: {result.action_id}")
        print(f"   Workflow ID: {wid}")
        print(f"   Version: {result.version}")
        print("   Status: DRAFT (not live)")
        print(f"\n   To publish: python cli/publish_wdl_action.py {result.action_id}")
        print("=" * 80)
    
    return result.success, str(result.version or "")


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Save WDL as draft (supports multiple actions and parallel execution)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single action (by workflow_id)
  python save_wdl_draft.py my-workflow
  
  # Multiple actions in parallel
  python save_wdl_draft.py action1 action2 action3 --parallel 3
  
  # Agent + all changed subactions
  python save_wdl_draft.py --agent my-agent
  
  # Agent + ALL subactions (even unchanged)
  python save_wdl_draft.py --agent my-agent --force
  
  # Legacy: using --workflow-id flag
  python save_wdl_draft.py --workflow-id my-workflow
        """
    )
    
    # Positional arguments (multiple actions)
    parser.add_argument(
        "actions", nargs="*",
        help="Action/workflow ID(s) to save (supports multiple)"
    )
    
    # Agent mode
    parser.add_argument(
        "--agent", "-a",
        help="Save agent and all its subactions"
    )
    parser.add_argument(
        "--force", "-f", action="store_true",
        help="Save all subactions (not just changed ones)"
    )
    
    # Parallel execution
    parser.add_argument(
        "--parallel", "-p", type=int, default=5,
        help="Max parallel workers (default: 5)"
    )
    
    # Legacy flags
    parser.add_argument(
        "--action-id",
        help="(legacy) Action ID"
    )
    parser.add_argument(
        "--workflow-id", "-w",
        help="(legacy) Workflow ID"
    )
    parser.add_argument(
        "--standalone", "-s", action="store_true",
        help="(legacy) Standalone mode"
    )
    
    # Common options
    parser.add_argument(
        "--description", "-d",
        help="Description of changes for this version"
    )
    parser.add_argument(
        "--env", "-e",
        help="Environment to use"
    )
    
    args = parser.parse_args()
    
    manager = get_workspace_manager()
    
    # Set environment if provided
    if args.env:
        if manager.env_exists(args.env):
            manager.active_env = args.env
        else:
            print(f"❌ Environment not found: {args.env}")
            sys.exit(1)
    
    # Determine what to save
    if args.agent:
        # Agent mode: save agent + subactions
        results = save_agent_drafts(
            agent_id=args.agent,
            manager=manager,
            changed_only=not args.force,
            max_workers=args.parallel,
            description=args.description,
        )
        print_results(results)
        success = all(r.success for r in results)
        sys.exit(0 if success else 1)
    
    elif args.actions:
        # Multiple actions from positional args
        if len(args.actions) == 1:
            # Single action - use verbose mode
            success, _ = save_wdl_draft(
                workflow_id=args.actions[0],
                description=args.description,
            )
            sys.exit(0 if success else 1)
        else:
            # Multiple actions - parallel mode
            results = save_parallel_drafts(
                workflow_ids=args.actions,
                manager=manager,
                max_workers=args.parallel,
                description=args.description,
            )
            print_results(results)
            success = all(r.success for r in results)
            sys.exit(0 if success else 1)
    
    elif args.workflow_id or args.action_id:
        # Legacy mode
        success, _ = save_wdl_draft(
            action_id=args.action_id,
            workflow_id=args.workflow_id,
            standalone=args.standalone,
            agent_name=args.agent,
            description=args.description,
        )
        sys.exit(0 if success else 1)
    
    else:
        parser.print_help()
        print("\n❌ Error: Must provide action(s), --agent, or --workflow-id", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
