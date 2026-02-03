#!/usr/bin/env python3
"""
Approve and publish WDL action(s) (makes them live).

Features:
- Single action: publish_wdl_action.py my-action
- Multiple actions: publish_wdl_action.py action1 action2 action3
- Parallel execution: publish_wdl_action.py action1 action2 --parallel 3
- Agent + subactions: publish_wdl_action.py --agent my-agent

⚠️  EXPLICIT USER ACTION REQUIRED

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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, List

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.api_client import AdoptAPIClient, get_api_client_for_env
from cli.wdl_common.metadata_manager import MetadataManager
from cli.wdl_common.version_tracker import (
    update_current_version,
    update_metadata_version,
    set_current_version,
    read_metadata,
)
from cli.wdl_common.workspace_manager import WorkspaceManager, get_workspace_manager, HierarchicalWorkspaceManager


@dataclass
class PublishResult:
    """Result of a single publish operation."""
    workflow_id: str
    success: bool
    message: str
    version: Optional[int] = None
    action_id: Optional[str] = None
    error: Optional[str] = None


def publish_single_action(
    workflow_id: str,
    manager: HierarchicalWorkspaceManager,
    client: AdoptAPIClient,
    version_id: Optional[str] = None,
    description: Optional[str] = None,
    verbose: bool = True,
) -> PublishResult:
    """
    Publish a single action.
    
    Args:
        workflow_id: The workflow/action ID
        manager: Workspace manager instance
        client: API client instance
        version_id: Specific version to publish (default: latest draft)
        description: Version description
        verbose: Print progress messages
        
    Returns:
        PublishResult with outcome
    """
    def log(msg: str) -> None:
        if verbose:
            print(msg)
    
    # Find workspace
    action_info = manager.find_action(workflow_id)
    workspace = None
    meta_manager = None
    action_id = None
    
    if action_info:
        workspace = Path(action_info["path"])
        meta_manager = MetadataManager(workspace)
        action_id = meta_manager.get_action_id()
        
        # Try to recover action_id by title if not found
        if not action_id:
            meta_data = meta_manager.load()
            title = meta_data.title
            if title:
                log(f"🔍 No action_id found. Searching by title: {title}")
                success_list, tools, msg_list = client.list_tools()
                if success_list and tools:
                    for tool in tools:
                        if tool.get("title") == title:
                            action_id = tool.get("action_id") or tool.get("id")
                            log(f"   ✅ Found action: {action_id}")
                            meta_manager.set_action_id(action_id)
                            break
    
    if not action_id:
        return PublishResult(
            workflow_id=workflow_id,
            success=False,
            message="No action_id found",
            error="Action not linked to remote. Save draft first.",
        )
    
    log(f"📁 Workspace: {workspace}")
    log(f"🔑 Action ID: {action_id}")
    
    # Find latest draft version if not specified
    if not version_id:
        log("\n📋 Finding latest draft version...")
        success, versions, msg = client.list_versions(action_id)
        
        if not success or not versions:
            return PublishResult(
                workflow_id=workflow_id,
                success=False,
                message="Failed to list versions",
                action_id=action_id,
                error=msg,
            )
        
        # Find latest draft/pending_approval version
        for v in versions:
            if v.get("status") == "pending_approval":
                version_id = v.get("version_number", v.get("id"))
                break
        
        if not version_id:
            return PublishResult(
                workflow_id=workflow_id,
                success=False,
                message="No draft version found",
                action_id=action_id,
                error="No pending_approval version to publish",
            )
        
        log(f"   Found version: {version_id}")
    
    # Use default description if not provided
    if not description:
        description = "WDL action update"
    
    # Approve version
    log(f"\n✅ Approving version {version_id}...")
    success, msg = client.approve_version(action_id, version_id, change_reason=description)
    
    if not success:
        return PublishResult(
            workflow_id=workflow_id,
            success=False,
            message="Failed to approve",
            action_id=action_id,
            version=int(version_id) if version_id else None,
            error=msg,
        )
    
    version_number = int(version_id) if version_id else None
    
    # Update local version files if workspace exists
    if workspace and workspace.exists() and version_number:
        versions_dir = workspace / "versions"
        versions_dir.mkdir(exist_ok=True)
        version_wdl_path = versions_dir / f"v{version_number}_widdle.json"
        
        # Try to get WDL from local sources
        wdl_to_save = None
        
        if version_wdl_path.exists():
            try:
                wdl_to_save = json.loads(version_wdl_path.read_text())
            except Exception:
                pass
        
        if not wdl_to_save:
            wdl_path = workspace / "widdle.json"
            if wdl_path.exists():
                try:
                    wdl_to_save = json.loads(wdl_path.read_text())
                except Exception:
                    pass
        
        if wdl_to_save:
            version_wdl_path.write_text(json.dumps(wdl_to_save, indent=2))
            log(f"   💾 Saved WDL locally: {version_wdl_path.name}")
        
        # Update tracking
        update_current_version(
            workspace=workspace,
            version_number=version_number,
            status="published",
            is_published=True,
            description=description,
        )
        
        set_current_version(workspace, version_number)
        
        update_metadata_version(
            workspace=workspace,
            version_number=version_number,
            status="published",
            is_published=True,
            description=description,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
    
    return PublishResult(
        workflow_id=workflow_id,
        success=True,
        message=f"Published v{version_number}",
        version=version_number,
        action_id=action_id,
    )


def publish_parallel_actions(
    workflow_ids: List[str],
    manager: HierarchicalWorkspaceManager,
    max_workers: int = 5,
    description: Optional[str] = None,
) -> List[PublishResult]:
    """
    Publish multiple actions in parallel.
    
    Args:
        workflow_ids: List of workflow/action IDs
        manager: Workspace manager instance
        max_workers: Maximum parallel workers
        description: Version description for all
        
    Returns:
        List of PublishResults
    """
    results: List[PublishResult] = []
    client = get_api_client_for_env()
    
    print(f"\n🚀 Publishing {len(workflow_ids)} actions in parallel (max {max_workers} workers)...")
    print("=" * 80)
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_workflow = {
            executor.submit(
                publish_single_action,
                wid,
                manager,
                client,
                None,  # version_id - auto-detect
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
                results.append(PublishResult(
                    workflow_id=workflow_id,
                    success=False,
                    message="Exception",
                    error=str(e),
                ))
                print(f"   ❌ {workflow_id}: {e}")
    
    return results


def publish_agent_with_subactions(
    agent_id: str,
    manager: HierarchicalWorkspaceManager,
    max_workers: int = 5,
    description: Optional[str] = None,
) -> List[PublishResult]:
    """
    Publish an agent and all its subactions.
    
    Subactions are published first (in parallel), then the agent.
    
    Args:
        agent_id: Agent ID
        manager: Workspace manager instance
        max_workers: Maximum parallel workers
        description: Version description
        
    Returns:
        List of PublishResults (subactions + agent)
    """
    env = manager.active_env
    agent = manager.get_agent(agent_id, env)
    
    if not agent:
        return [PublishResult(
            workflow_id=agent_id,
            success=False,
            message="Agent not found",
            error=f"Agent '{agent_id}' not found in environment '{env}'",
        )]
    
    print(f"\n🤖 Publishing agent: {agent_id}")
    print(f"📁 Path: {agent.get('path')}")
    
    client = get_api_client_for_env()
    
    # Collect subactions with draft versions
    subaction_ids: List[str] = []
    for sa in agent.get("sub_actions", []):
        sa_id = sa.get("action_id") or sa.get("id")
        if not sa_id:
            continue
        
        # Check if subaction has draft version
        sa_info = manager.find_action(sa_id)
        if sa_info:
            meta_manager = MetadataManager(sa_info["path"])
            remote_status = meta_manager.get_remote_status()
            if remote_status in ["draft", "draft_with_published"]:
                subaction_ids.append(sa_id)
            else:
                print(f"   ⏭️  {sa_id}: No draft to publish (status: {remote_status})")
    
    print(f"\n📋 Subactions to publish: {len(subaction_ids)}")
    
    results: List[PublishResult] = []
    
    # Publish subactions in parallel FIRST
    if subaction_ids:
        print("\n📦 Publishing subactions first...")
        subaction_results = publish_parallel_actions(
            subaction_ids, manager, max_workers, description
        )
        results.extend(subaction_results)
        
        # Check if all subactions succeeded
        failed = [r for r in subaction_results if not r.success]
        if failed:
            print(f"\n⚠️  {len(failed)} subaction(s) failed to publish")
    
    # Publish agent itself (if it has draft version)
    agent_path = Path(agent.get("path", ""))
    if (agent_path / "widdle.json").exists():
        meta_manager = MetadataManager(agent_path)
        remote_status = meta_manager.get_remote_status()
        
        if remote_status in ["draft", "draft_with_published"]:
            print(f"\n📦 Publishing agent...")
            agent_result = publish_single_action(agent_id, manager, client, None, description, True)
            results.append(agent_result)
        else:
            print(f"\n⏭️  Agent has no draft to publish (status: {remote_status})")
    else:
        print(f"\n⏭️  Agent has no WDL, skipping")
    
    return results


def print_results(results: List[PublishResult]) -> None:
    """Print summary of publish results."""
    passed = [r for r in results if r.success]
    failed = [r for r in results if not r.success]
    
    print("\n" + "=" * 80)
    print("📊 PUBLISH RESULTS SUMMARY")
    print("=" * 80)
    print(f"Total: {len(results)} | Published: {len(passed)} ✅ | Failed: {len(failed)} ❌")
    print("-" * 80)
    
    if passed:
        print("\n✅ PUBLISHED:")
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
def publish_wdl_action(
    action_id: Optional[str] = None,
    version_id: Optional[str] = None,
    skip_confirm: bool = False,
    description: Optional[str] = None,
    workflow_id: Optional[str] = None,
    standalone: bool = False,
) -> bool:
    """
    Approve and publish the action (legacy interface).
    
    For new code, use publish_single_action() instead.
    """
    print("\n" + "=" * 80)
    print("🚀 PUBLISH WDL ACTION")
    print("=" * 80)
    print("⚠️  This will make the action LIVE")
    
    manager = get_workspace_manager()
    client = get_api_client_for_env()
    
    wid = workflow_id or action_id
    if not wid:
        print("❌ Must provide either action_id or workflow_id")
        return False
    
    # Confirm with user if not skipped
    if not skip_confirm:
        print(f"\n⚠️  About to publish: {wid}")
        confirm = input("   Type 'yes' to confirm: ").strip().lower()
        if confirm != "yes":
            print("❌ Cancelled")
            return False
    
    result = publish_single_action(wid, manager, client, version_id, description, verbose=True)
    
    if result.success:
        print("\n" + "=" * 80)
        print("🎉 ACTION PUBLISHED!")
        print("=" * 80)
        print(f"   Action ID: {result.action_id}")
        print(f"   Workflow ID: {wid}")
        print(f"   Version: {result.version}")
        print("   Status: LIVE")
        if description:
            print(f"   Description: {description}")
        print("=" * 80)
    
    return result.success


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Approve and publish action(s) - makes them live",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single action (by workflow_id)
  python publish_wdl_action.py my-workflow
  
  # Multiple actions in parallel
  python publish_wdl_action.py action1 action2 action3 --parallel 3
  
  # Agent + all draft subactions
  python publish_wdl_action.py --agent my-agent
  
  # Legacy: using --workflow-id flag
  python publish_wdl_action.py --workflow-id my-workflow
  
  # Skip confirmation
  python publish_wdl_action.py my-workflow --yes

TRANSPARENT BEHAVIOR (default):
  - Action ID: Auto-detected from workspace metadata
  - Version: Auto-finds latest draft to publish
  - Agent: Auto-detected from workspace location

USE FLAGS ONLY when automatic behavior doesn't work.
        """
    )
    
    # Positional arguments (multiple actions)
    parser.add_argument(
        "actions", nargs="*",
        help="Action/workflow ID(s) to publish (supports multiple)"
    )
    
    # Agent mode
    parser.add_argument(
        "--agent", "-a",
        help="Publish agent and all its draft subactions"
    )
    
    # Parallel execution
    parser.add_argument(
        "--parallel", "-p", type=int, default=5,
        help="Max parallel workers (default: 5)"
    )
    
    # Confirmation
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip confirmation prompt"
    )
    
    # Legacy flags
    parser.add_argument(
        "--workflow-id", "-w",
        help="(legacy) Workflow ID"
    )
    parser.add_argument(
        "--version", "-v",
        help="Specific version to publish (default: latest draft)"
    )
    parser.add_argument(
        "--standalone", "-s", action="store_true",
        help="(legacy) Standalone mode"
    )
    
    # Common options
    parser.add_argument(
        "--description", "-d",
        help="Description for this version"
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
    
    # Determine what to publish
    if args.agent:
        # Confirm before agent publish
        if not args.yes:
            print(f"\n⚠️  About to publish agent '{args.agent}' and all its draft subactions")
            confirm = input("   Type 'yes' to confirm: ").strip().lower()
            if confirm != "yes":
                print("❌ Cancelled")
                sys.exit(0)
        
        results = publish_agent_with_subactions(
            agent_id=args.agent,
            manager=manager,
            max_workers=args.parallel,
            description=args.description,
        )
        print_results(results)
        success = all(r.success for r in results)
        sys.exit(0 if success else 1)
    
    elif args.actions:
        # Multiple actions from positional args
        if len(args.actions) == 1:
            # Single action - use verbose mode with confirmation
            success = publish_wdl_action(
                workflow_id=args.actions[0],
                version_id=args.version,
                skip_confirm=args.yes,
                description=args.description,
            )
            sys.exit(0 if success else 1)
        else:
            # Multiple actions - confirm first
            if not args.yes:
                print(f"\n⚠️  About to publish {len(args.actions)} actions:")
                for a in args.actions:
                    print(f"     - {a}")
                confirm = input("   Type 'yes' to confirm: ").strip().lower()
                if confirm != "yes":
                    print("❌ Cancelled")
                    sys.exit(0)
            
            results = publish_parallel_actions(
                workflow_ids=args.actions,
                manager=manager,
                max_workers=args.parallel,
                description=args.description,
            )
            print_results(results)
            success = all(r.success for r in results)
            sys.exit(0 if success else 1)
    
    elif args.workflow_id:
        # Legacy mode
        success = publish_wdl_action(
            workflow_id=args.workflow_id,
            version_id=args.version,
            skip_confirm=args.yes,
            description=args.description,
            standalone=args.standalone,
        )
        sys.exit(0 if success else 1)
    
    else:
        parser.print_help()
        print("\n❌ Error: Must provide action(s), --agent, or --workflow-id", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
