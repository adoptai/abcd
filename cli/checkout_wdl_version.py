#!/usr/bin/env python3
"""
Checkout a specific version of a WDL action.

Downloads the version's WDL to local workspace for testing/modification.

TRANSPARENT BEHAVIOR (default):
- Action ID: Auto-detected from workspace metadata
- Agent/Standalone: Auto-detected from workspace location

USE FLAGS ONLY when automatic behavior doesn't work.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.api_client import get_api_client_for_env
from cli.wdl_common.metadata_manager import MetadataManager
from cli.wdl_common.version_tracker import (
    set_checked_out_version,
    update_current_version,
    update_metadata_version,
)
from cli.wdl_common.workspace_manager import get_workspace_manager

_verbose = False


def _vprint(*args: object) -> None:
    if _verbose:
        print("[VERBOSE]", *args)


def checkout_wdl_version(
    action_id: str | None = None,
    version_id: str | None = None,
    workflow_id: str | None = None,
    standalone: bool = False,
    dry_run: bool = False,
) -> bool:
    """
    Checkout a specific version to local workspace.

    TRANSPARENT: Can be called with just workflow_id - action_id auto-detected.

    Args:
        action_id: The action ID (optional - auto-detected from metadata)
        version_id: Version to checkout
        workflow_id: Workflow ID to find workspace (recommended)
        standalone: Use standalone mode (override auto-detection)

    Returns:
        True if successful
    """
    print("\n" + "=" * 80)
    if dry_run:
        print("🔍 [DRY-RUN] CHECKOUT WDL VERSION (simulating — no files will be written)")
    else:
        print("📥 CHECKOUT WDL VERSION")
    print("=" * 80)

    # Try to find workspace by workflow_id first
    workspace = None
    meta_manager = None

    if workflow_id:
        workspace_manager = get_workspace_manager()
        _vprint(f"Searching for workspace: {workflow_id} (env={workspace_manager.active_env})")
        action_info = workspace_manager.find_action(workflow_id)
        if action_info:
            workspace = Path(action_info["path"])
            _vprint(f"Found workspace at: {workspace}")

    # If workspace found, use MetadataManager for action_id
    if workspace and workspace.exists():
        _vprint(f"Loading metadata from: {workspace / 'metadata.json'}")
        meta_manager = MetadataManager(workspace)

        # Get action_id from metadata if not provided
        if not action_id:
            action_id = meta_manager.get_action_id()
            _vprint(f"Action ID from metadata: {action_id}")

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
                            if action_id:
                                meta_manager.set_action_id(str(action_id))
                            break

    # Fallback to active environment's actions folder if no workspace
    if not workspace and action_id:
        from cli.wdl_common.workspace_manager import WORKSPACES_DIR

        env = get_workspace_manager().active_env or "default"
        # Try standalone actions first, then search agent sub-action directories
        candidate = WORKSPACES_DIR / env / "actions" / action_id
        if candidate.exists():
            workspace = candidate
        else:
            # Search inside all agents
            agents_dir = WORKSPACES_DIR / env / "agents"
            if agents_dir.exists():
                for agent_dir in agents_dir.iterdir():
                    sub_candidate = agent_dir / "actions" / action_id
                    if sub_candidate.exists():
                        workspace = sub_candidate
                        break
            if not workspace:
                workspace = candidate  # Default fallback even if it doesn't exist

    if not action_id:
        print(
            "❌ No action_id found. Provide --action-id or use --workflow-id with linked workspace."
        )
        return False

    if not version_id:
        print("❌ No version specified. Use --version to specify version to checkout.")
        return False

    if workspace is None:
        print(f"❌ Could not find workspace for action: {action_id}")
        return False

    print(f"Action ID: {action_id}")
    print(f"Version: {version_id}")
    _vprint(f"Workspace path: {workspace}")

    if not workspace.exists():
        if dry_run:
            print(f"⚠️  [DRY-RUN] Workspace does not exist yet — would create: {workspace}")
        else:
            print(f"⚠️  Creating workspace: {workspace}")
            workspace.mkdir(parents=True)
            (workspace / "test_cases").mkdir(exist_ok=True)
            (workspace / "traces").mkdir(exist_ok=True)
            (workspace / "versions").mkdir(exist_ok=True)

    # Load API client with environment credentials
    _vprint("Initialising API client for active environment")
    client = get_api_client_for_env()

    # Get versions list to check if this version is current
    print("\n📋 Checking version status...")
    _vprint(f"API call: list_versions({action_id})")
    success, versions, msg = client.list_versions(action_id)

    if not success:
        print(f"❌ Failed to list versions: {msg}")
        return False

    # Find the version we want to checkout
    version_number = int(version_id)
    target_version = None
    for v in versions or []:
        if v.get("version_number") == version_number:
            target_version = v
            break

    if not target_version:
        print(f"❌ Version {version_id} not found")
        return False

    is_current = target_version.get("is_current_version", False)

    # Check if we have a local copy of this version's WDL
    versions_dir = workspace / "versions"
    local_wdl_path = versions_dir / f"v{version_id}_widdle.json"

    version_data = None
    wdl = None

    if local_wdl_path.exists():
        # Use local copy if available
        print(f"📋 Found local copy of version {version_id}...")
        try:
            wdl = json.loads(local_wdl_path.read_text())
            # Create version_data from target_version info
            version_data = {
                "version_number": version_number,
                "status": target_version.get("status", "pending_approval"),
                "change_reason": target_version.get("change_reason", ""),
                "created_at": target_version.get("created_at", ""),
                "updated_at": target_version.get("updated_at", ""),
                "wdl": wdl,
            }
            print("   ✅ Using local WDL copy")
        except Exception as e:
            print(f"   ⚠️  Error reading local copy: {e}")
            print("   📋 Fetching from API instead...")

    # If no local copy or error, fetch from API (only works for current version)
    if not version_data:
        if not is_current:
            print(f"⚠️  Version {version_id} is not the current version")
            print(
                "   Cannot fetch WDL from API (only current version available via /current/ endpoint)"
            )
            print(
                f"   Current version: {next((v.get('version_number') for v in (versions or []) if v.get('is_current_version')), 'unknown')}"
            )
            print(
                f"   💡 Tip: Checkout the current version first, or ensure version {version_id} WDL exists locally"
            )
            return False

        # Get current version details (includes WDL)
        print(f"📋 Fetching current version (version {version_id}) from API...")
        success, version_data, msg = client.get_current_version(action_id)

        if not success or not version_data:
            print(f"❌ {msg}")
            return False

        # Verify version number matches
        if version_data.get("version_number") != version_number:
            print(
                f"⚠️  Warning: Current version is {version_data.get('version_number')}, not {version_id}"
            )
            print(f"   Proceeding with version {version_data.get('version_number')}")
            version_number = int(version_data.get("version_number") or version_number)

        wdl = version_data.get("wdl", version_data.get("widdle", []))

    # Extract version metadata
    # Status is always either "pending_approval" (draft) or "approved" (published)
    version_status = version_data.get("status", "pending_approval")
    is_published = version_status == "approved"
    version_description = version_data.get("change_reason", "")
    created_at = version_data.get("created_at", "")
    updated_at = version_data.get("updated_at", "")

    if not wdl:
        print("❌ No WDL found in version")
        return False

    # Check if this is an Uber Agent (contains PROMPT_AND_TOOLS_AGENT operation)
    is_uber_agent = False
    sub_action_ids = []
    for step in wdl:
        if isinstance(step, dict) and step.get("operation") == "PROMPT_AND_TOOLS_AGENT":
            is_uber_agent = True
            sub_action_ids = step.get("action_ids", [])
            break

    if is_uber_agent and not standalone:
        print("\n🤖 UBER AGENT DETECTED!")
        print(
            f"   This action contains PROMPT_AND_TOOLS_AGENT with {len(sub_action_ids)} sub-actions."
        )
        print("   💡 Consider using agent checkout instead:")
        print(
            f"      python cli/workspace.py agent checkout --remote-id {action_id} --env <env> --include-subactions"
        )
        print("\n   Continuing with standard checkout (sub-actions will NOT be downloaded)...")

    if dry_run:
        wdl_path = workspace / "widdle.json"
        versions_dir = workspace / "versions"
        print("\n🔍 [DRY-RUN] Would perform the following file operations:")
        if wdl_path.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            print(f"   - Back up existing widdle.json → widdle_backup_{timestamp}.json")
        print(f"   - Write WDL ({len(wdl)} operations) → widdle.json")
        print(f"   - Write version archive → versions/v{version_id}_widdle.json")
        print(
            f"   - Update current_version.txt (version={version_number}, status={version_status})"
        )
        print(f"   - Update metadata.json (checked_out_version={version_number})")
        if not (workspace / "adopt_profile.json").exists():
            print("   - Create adopt_profile.json (empty template)")
        if not (workspace / "test_cases" / "test_1.json").exists():
            print("   - Create test_cases/test_1.json (default template)")
        print("\n   ✅ Validation passed — run without --dry-run to apply changes")
        print("=" * 80)
        return True

    # Backup current WDL if exists
    wdl_path = workspace / "widdle.json"
    if wdl_path.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = workspace / f"widdle_backup_{timestamp}.json"
        wdl_path.rename(backup_path)
        print(f"   Backed up current WDL to: {backup_path.name}")

    # Save checked-out WDL
    wdl_path.write_text(json.dumps(wdl, indent=2))
    print(f"   ✅ Saved WDL to: {wdl_path}")

    # Update current_version.txt with full version info
    update_current_version(
        workspace=workspace,
        version_number=version_number,
        status=version_status,
        is_published=is_published,
        description=version_description,
    )

    # Update metadata.json with checked_out_version and version info
    set_checked_out_version(workspace, version_number)
    update_metadata_version(
        workspace=workspace,
        version_number=version_number,
        status=version_status,
        is_published=is_published,
        description=version_description,
        created_at=created_at,
        updated_at=updated_at,
        checked_out_at=datetime.now().isoformat(),
    )

    # Save to versions folder
    versions_dir = workspace / "versions"
    versions_dir.mkdir(exist_ok=True)
    (versions_dir / f"v{version_id}_widdle.json").write_text(json.dumps(wdl, indent=2))

    # Create adopt_profile.json if doesn't exist
    profile_path = workspace / "adopt_profile.json"
    if not profile_path.exists():
        profile = {
            "base_url": "",
            "application_base_url": "",
            "workflow_params": {},
            "security_params": {},
        }
        profile_path.write_text(json.dumps(profile, indent=2))

    # Create default test case if doesn't exist
    test_case_path = workspace / "test_cases" / "test_1.json"
    if not test_case_path.exists():
        (workspace / "test_cases").mkdir(exist_ok=True)
        test_case = {"prompt": "Test input for the action", "workflow_params": {}}
        test_case_path.write_text(json.dumps(test_case, indent=2))

    # Display checkout summary
    print("\n" + "=" * 80)
    print("✅ VERSION CHECKED OUT")
    print("=" * 80)
    print(f"   Version: {version_id}")
    print(f"   Status: {version_status} {'✅' if is_published else ''}")
    if version_description:
        print(f"   Description: {version_description}")
    print(f"   WDL: {len(wdl)} operations")
    print("\n   You can now:")
    print("   - Edit widdle.json")
    if workflow_id:
        print(f"   - Test: python cli/test_runner.py {workflow_id}")
        print(f"   - Save draft: python cli/save_wdl_draft.py --workflow-id {workflow_id}")
    else:
        print(f"   - Test: python cli/test_runner.py {action_id}")
        print(f"   - Save draft: python cli/save_wdl_draft.py --action-id {action_id}")
    print("=" * 80)

    return True


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Checkout a specific version",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
TRANSPARENT BEHAVIOR (default):
  - Action ID: Auto-detected from workspace metadata
  - Agent: Auto-detected from workspace location

Examples:
  # Checkout using workflow_id (RECOMMENDED)
  python checkout_wdl_version.py --workflow-id my-workflow --version 3

  # Checkout with explicit action_id (legacy)
  python checkout_wdl_version.py abc123-action-id --version 3

USE FLAGS ONLY when automatic behavior doesn't work.
""",
    )
    parser.add_argument(
        "action_id", nargs="?", default=None, help="Action ID (optional if --workflow-id provided)"
    )
    parser.add_argument("--version", "-v", required=True, help="Version to checkout")
    parser.add_argument(
        "--workflow-id", "-w", help="Workflow ID (RECOMMENDED - auto-detects action_id)"
    )
    parser.add_argument(
        "--standalone",
        "-s",
        action="store_true",
        help="Override agent detection with standalone mode",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate the checkout without writing any local files or metadata",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed debug information (workspace resolution, API calls, file operations)",
    )
    args = parser.parse_args()

    global _verbose
    _verbose = args.verbose
    if _verbose:
        print("[VERBOSE] Verbose mode enabled", file=sys.stderr)

    # Require either action_id or workflow_id
    if not args.action_id and not args.workflow_id:
        parser.error("Either action_id or --workflow-id is required")

    success = checkout_wdl_version(
        action_id=args.action_id,
        version_id=args.version,
        workflow_id=args.workflow_id,
        standalone=args.standalone,
        dry_run=args.dry_run,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
